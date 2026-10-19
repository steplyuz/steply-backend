from datetime import datetime, timezone
import secrets
from typing import List, Optional

from fastapi import HTTPException
from sqlalchemy import select, func as sqlfunc
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.billing.models import BillingOrder, BillingOrderStatus, BillingProductType

from .models import ExamSession, MockRegistration, RegistrationStatus, MockExam
import uuid
from .schemas import (
    ExamSessionCreate,
    ExamSessionUpdate,
    MockRegistrationCreate,
)
from .mk_id import generate_unique_mk_id
from .seats import reserve_seat
from .services import get_or_create_attempt_for_user

_MK_ID_INSERT_ATTEMPTS = 3


# =====================================================
# EXAM SESSIONS (ochiq test sanalari) — ADMIN
# =====================================================


def is_online_location(location: Optional[str]) -> bool:
    """Masofaviy (uydan topshiriladigan) sessiya belgisi: location_address == 'online'."""
    return (location or "").strip().lower() == "online"


def validate_delivery(delivery_mode: str, location: Optional[str]) -> None:
    """Online sessiya faqat kompyuterda topshiriladi; PAPER + online ziddiyat."""
    if is_online_location(location) and delivery_mode != "COMPUTER":
        raise HTTPException(422, "Online sessiya uchun delivery_mode COMPUTER bo'lishi shart")


async def create_session(db: AsyncSession, data: ExamSessionCreate) -> ExamSession:
    stmt = select(MockExam).where(MockExam.id == data.mock_exam_id)
    exam = (await db.execute(stmt)).scalar_one_or_none()
    if not exam:
        raise HTTPException(404, "Mock imtihon topilmadi")
    missing = [name for name, value in (("Reading", exam.reading_id), ("Listening", exam.listening_id), ("Writing", exam.writing_id), ("Speaking", exam.speaking_id)) if not value]
    if missing:
        raise HTTPException(400, "Mock session yaratib bo'lmaydi: " + ", ".join(missing) + " ID mavjud emas")

    delivery_mode = data.delivery_mode.value
    validate_delivery(delivery_mode, data.location_address)

    import secrets
    session = ExamSession(
        mock_exam_id=data.mock_exam_id,
        exam_date=data.exam_date,
        center_name=data.center_name,
        room_name=data.room_name,
        location_address=data.location_address,
        region=data.region.value if data.region else None,
        delivery_mode=delivery_mode,
        registration_slug=secrets.token_urlsafe(20),
        capacity=data.capacity,
        required_documents=data.required_documents
        or "Pasport yoki ID-karta;To'lov kvitansiyasi;1 dona 3x4 rasm",
        is_active=data.is_active,
        requires_online_payment=data.requires_online_payment,
    )
    db.add(session)
    await db.commit()
    await db.refresh(session)
    return session


async def update_session(
    db: AsyncSession, session_id: int, data: ExamSessionUpdate
) -> ExamSession:
    stmt = select(ExamSession).join(MockExam, MockExam.id == ExamSession.mock_exam_id).where(ExamSession.id == session_id)
    session = (await db.execute(stmt)).scalar_one_or_none()
    if not session:
        raise HTTPException(404, "Sessiya topilmadi")

    update_data = data.model_dump(exclude_unset=True)
    for field, value in update_data.items():
        if field in ("region", "delivery_mode") and value is not None:
            value = value.value if hasattr(value, "value") else value
        setattr(session, field, value)
    # Yakuniy holat (PATCH'dan keyingi) ziddiyatsiz bo'lishi kerak.
    validate_delivery(session.delivery_mode, session.location_address)

    await db.commit()
    await db.refresh(session)
    return session


async def list_open_sessions(
    db: AsyncSession, mock_exam_id: Optional[str] = None
) -> List[dict]:
    """Foydalanuvchiga ko'rinadigan, hali o'tmagan ochiq test sanalari."""
    now = datetime.now(timezone.utc)
    stmt = select(ExamSession).where(
        ExamSession.is_active.is_(True),
        ExamSession.exam_date >= now,
    )
    if mock_exam_id:
        stmt = stmt.where(ExamSession.mock_exam_id == mock_exam_id)
    stmt = stmt.order_by(ExamSession.exam_date.asc())

    result = await db.execute(stmt)
    sessions = result.scalars().all()

    out = []
    for s in sessions:
        count_stmt = select(sqlfunc.count()).select_from(MockRegistration).where(
            MockRegistration.session_id == s.id,
            MockRegistration.status != RegistrationStatus.CANCELLED,
        )
        registered_count = (await db.execute(count_stmt)).scalar_one()
        out.append({**s.__dict__, "registered_count": registered_count})
    return out


# =====================================================
# REGISTRATION (foydalanuvchi ro'yxatdan o'tishi)
# =====================================================


async def register_for_session(
    db: AsyncSession, user_id: uuid.UUID, data: MockRegistrationCreate
) -> MockRegistration:
    # RACE FIX: sessiya qatorini qulflab olamiz (SELECT ... FOR UPDATE).
    # Ilgari capacity va "allaqachon ro'yxatdan o'tganmi" tekshiruvlari
    # oddiy SELECT bilan qilinib, keyin INSERT qilinardi — ikkita parallel
    # so'rov (masalan bitta bo'sh joy qolganda ikkita talaba deyarli bir
    # vaqtda ro'yxatdan o'tsa) ikkalasi ham "joy bor" deb ko'rib, sig'imdan
    # ortiq ro'yxatga olinishi mumkin edi. Qator ushbu tranzaksiya
    # commit/rollback bo'lgunga qadar qulflanganda, shu SESSIYAGA parallel
    # ro'yxatdan o'tishlar ketma-ket bajariladi va tekshiruvlar endi
    # ishonchli bo'ladi. (SQLite'da bu qulf amalda e'tiborga olinmaydi —
    # zarar yo'q, chunki SQLite baribir yozuvlarni serializatsiya qiladi;
    # Postgres production'da esa haqiqiy himoya beradi.)
    session = (
        await db.execute(
            select(ExamSession).where(ExamSession.id == data.session_id)
        )
    ).scalar_one_or_none()
    if not session or not session.is_active:
        raise HTTPException(404, "Ochiq test sanasi topilmadi yoki faol emas")

    existing = (
        await db.execute(
            select(MockRegistration).where(
                MockRegistration.user_id == user_id,
                MockRegistration.session_id == data.session_id,
            )
        )
    ).scalar_one_or_none()
    if existing:
        raise HTTPException(409, "Siz bu sanaga allaqachon ro'yxatdan o'tgansiz")

    session_id = session.id
    mock_exam_id = session.mock_exam_id

    exam = await db.get(MockExam, session.mock_exam_id)
    if not exam:
        raise HTTPException(404, "Mock imtihon topilmadi")

    # TO'LOV GATE'I: admin shu sessiya uchun onlayn to'lovni majburiy qilgan
    # bo'lsa (va imtihon pullik bo'lsa), tasdiqlangan (PAID) buyurtma talab
    # qilinadi. Aks holda (standart) — to'lov imtihon markazida naqd
    # olinadi, mavjud xatti-harakat o'zgarmaydi.
    billing_order_id: Optional[str] = None
    if session.requires_online_payment and exam.price and exam.price > 0:
        paid_orders = (
            await db.execute(
                select(BillingOrder).where(
                    BillingOrder.user_id == user_id,
                    BillingOrder.product_type.in_([BillingProductType.FULL_EXAM, BillingProductType.MOCK_EXAM]),
                    BillingOrder.product_id == exam.id,
                    BillingOrder.status == BillingOrderStatus.PAID,
                )
            )
        ).scalars().all()
        # Bitta to'lov faqat bitta ro'yxatdan o'tishni tasdiqlaydi — allaqachon
        # boshqa MockRegistration'ga bog'langan order'lar hisobga olinmaydi.
        consumed_ids = set(
            (
                await db.execute(
                    select(MockRegistration.billing_order_id).where(
                        MockRegistration.billing_order_id.isnot(None),
                        MockRegistration.billing_order_id.in_([o.id for o in paid_orders]),
                    )
                )
            ).scalars().all()
        ) if paid_orders else set()
        unused_order = next((o for o in paid_orders if o.id not in consumed_ids), None)
        if not unused_order:
            raise HTTPException(
                402,
                "Bu sessiyaga ro'yxatdan o'tish uchun avval to'lovni amalga oshirib, "
                "admin tomonidan tasdiqlanishini kutish kerak "
                "(POST /billing/orders, keyin chek yuklang).",
            )
        billing_order_id = unused_order.id

    # MK ID AYNAN SHU YERDA yaratiladi — foydalanuvchi profilida emas.
    # Ochiq seansga yozilish = imtihonda ishtirok etish huquqi; MK ID esa
    # shu ishtirokning identifikatori va Exams tizimiga kirish kaliti.
    # Registration ma'lumotlari profile'dan olinmaydi: candidate bu ma'lumotlarni
    # registration vaqtida to'liq qo'lda beradi.
    birth_date = data.date_of_birth
    if not birth_date:
        raise HTTPException(400, "Tug'ilgan sana registrationda majburiy")
    if not data.photo_url:
        raise HTTPException(400, "Candidate rasmi registrationda majburiy")

    # RACE FIX: generate_unique_mk_id() faqat SELECT bilan bo'shligini
    # tekshiradi — INSERT paytigacha bo'lgan oraliqda boshqa parallel
    # ro'yxatdan o'tish xuddi shu MK ID'ni "band qilishi" mumkin (bir xil
    # tug'ilgan sana + jins juftligida). Bunday holatda commit
    # IntegrityError bilan yiqiladi; buni xato sifatida foydalanuvchiga
    # ko'rsatish o'rniga, yangi nomzod bilan bir necha marta qayta
    # urinamiz.
    last_exc: Optional[IntegrityError] = None
    for _ in range(_MK_ID_INSERT_ATTEMPTS):
        await reserve_seat(db, session_id)  # atomik: sig'im to'lgan bo'lsa 400
        try:
            student_id = await generate_unique_mk_id(db, birth_date)
        except RuntimeError as exc:
            raise HTTPException(409, "MK ID yaratib bo'lmadi; qayta urinib ko'ring") from exc

        registration = MockRegistration(
            user_id=user_id,
            session_id=data.session_id,
            mock_exam_id=mock_exam_id,
            full_name=data.full_name,
            student_id=student_id,
            exam_access_id=secrets.token_urlsafe(27),
            qr_token=secrets.token_urlsafe(24),
            age=data.age,
            date_of_birth=data.date_of_birth,
            gender=data.gender.value,
            region=data.region.value,
            photo_url=data.photo_url,
            status=RegistrationStatus.REGISTERED,
            billing_order_id=billing_order_id,
        )
        db.add(registration)
        try:
            await db.commit()
        except IntegrityError as exc:
            await db.rollback()
            last_exc = exc
            continue
        await db.refresh(registration)
        return registration

    raise HTTPException(
        409, "Ro'yxatdan o'tishda ID ziddiyati yuz berdi, qayta urinib ko'ring"
    ) from last_exc


async def get_my_registrations(db: AsyncSession, user_id: uuid.UUID) -> List[MockRegistration]:
    result = await db.execute(
        select(MockRegistration)
        .where(MockRegistration.user_id == user_id)
        .order_by(MockRegistration.created_at.desc())
    )
    return list(result.scalars().all())




# =====================================================
# ATTENDANCE — qog'ozdagi imzo admin tomonidan tasdiqlanadi
# =====================================================


async def _attach_paper_versions(db: AsyncSession, attempt, exam: Optional[MockExam], *, strict: bool) -> None:
    """Reading/Listening skill attemptlarga lock qilingan qog'oz versiyasini biriktiradi.

    strict=True bo'lsa, material biriktirilmagan yoki lock qilinmagan holatda 409 beradi.
    """
    from sqlalchemy import select as _select
    from app.modules.exams.paper.models import ExamVersion, PaperModule
    from .models import MockSkillAttempt, SkillType

    for skill, module, source_id in (
        (SkillType.LISTENING, PaperModule.LISTENING, exam.listening_id if exam else None),
        (SkillType.READING, PaperModule.READING, exam.reading_id if exam else None),
    ):
        if not source_id:
            if strict:
                raise HTTPException(409, f"{skill.value} materiali biriktirilmagan")
            continue
        version = (await db.execute(
            _select(ExamVersion)
            .where(ExamVersion.module == module, ExamVersion.exam_id == source_id, ExamVersion.is_locked.is_(True))
            .order_by(ExamVersion.version_number.desc())
            .limit(1)
        )).scalar_one_or_none()
        if not version:
            if strict:
                raise HTTPException(409, f"{skill.value} materiali hali lock qilinmagan")
            continue
        skill_row = (await db.execute(
            _select(MockSkillAttempt).where(MockSkillAttempt.attempt_id == attempt.id, MockSkillAttempt.skill == skill)
        )).scalar_one_or_none()
        if skill_row:
            skill_row.paper_version_id = version.id


async def check_in_registration(db: AsyncSession, registration: MockRegistration, *, strict: bool = False):
    """YAGONA check-in yo'li (admin "keldi" belgilashi ham, Mock Center check-in ham shu orqali).

    1. ro'yxat holati CHECKED_IN + kelgan vaqti;
    2. MockExamAttempt va 4 ta MockSkillAttempt (bor bo'lsa qayta yaratilmaydi);
    3. Reading/Listening uchun lock qilingan qog'oz versiyasi biriktiriladi.

    strict=True (Mock Center): material/speaking tayyor bo'lmasa 409 va hech narsa o'zgarmaydi.
    Qaytaradi: MockExamAttempt. Commit shu yerda qilinadi.
    """
    from sqlalchemy import select as _select
    from app.modules.tests.speaking.models import SpeakingTest

    exam = await db.get(MockExam, registration.mock_exam_id)
    if strict:
        has_speaking = (await db.execute(
            _select(SpeakingTest.id).where(SpeakingTest.is_active.is_(True), SpeakingTest.is_practice.is_(False)).limit(1)
        )).scalar_one_or_none()
        if not has_speaking:
            raise HTTPException(409, "Speaking testlar bazada tayyorlanmagan")

    registration.status = RegistrationStatus.CHECKED_IN
    registration.attended_confirmed_at = datetime.now(timezone.utc)
    attempt = await get_or_create_attempt_for_user(
        db,
        registration.user_id,
        registration.mock_exam_id,
        registration_id=registration.id,
        session_id=registration.session_id,
    )
    try:
        await _attach_paper_versions(db, attempt, exam, strict=strict)
    except HTTPException:
        await db.rollback()
        raise
    await db.commit()
    await db.refresh(registration)
    return attempt


async def confirm_attendance(db: AsyncSession, registration_id: int) -> MockRegistration:
    """Qog'ozdagi imzo tekshirilgach admin "keldi" deb belgilaydi (check_in_registration ga delegatsiya)."""
    registration = await db.get(MockRegistration, registration_id)
    if not registration:
        raise HTTPException(404, "Ro'yxat topilmadi")
    if registration.status == RegistrationStatus.CHECKED_IN:
        raise HTTPException(400, "Bu ro'yxat allaqachon 'keldi' deb belgilangan")
    await check_in_registration(db, registration)
    return registration


# =====================================================
# ADMIN — JADVAL (ro'yxatdan o'tganlar ro'yxati)
# =====================================================


async def list_session_registrations(
    db: AsyncSession, session_id: int
) -> List[MockRegistration]:
    stmt = select(MockRegistration).join(
        ExamSession, ExamSession.id == MockRegistration.session_id
    ).join(
        MockExam, MockExam.id == ExamSession.mock_exam_id
    ).where(MockRegistration.session_id == session_id).order_by(MockRegistration.full_name.asc())
    result = await db.execute(stmt)
    return list(result.scalars().all())
