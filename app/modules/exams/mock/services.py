from sqlalchemy.ext.asyncio import AsyncSession
from app.modules.tests.scoring_policy import cefr_for_score, scale_listening_reading
from app.modules.tests.attempts.timer import transition_mock_attempt
from sqlalchemy import select, and_
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import selectinload
from fastapi import HTTPException
from typing import List, Optional

# Tashqi Result modellarni import qilish (MUHIM!)
from app.modules.tests.listening.models import ListeningTest, ListeningResult
from app.modules.tests.reading.models import ReadingTest, ReadingResult
from app.modules.tests.writing.models import WritingExam, WritingResult

from app.modules.tests.speaking.models import SpeakingTest

import uuid
from .models import (
    MockExam, MockExamAttempt, MockPurchase, MockSkillAttempt,
    MockExamResult, SkillType, MockRegistration, RegistrationStatus,
)
from .schemas import MockExamCreate, MockExamUpdate
from app.core.id_generator import generate_mock_exam_id
from .models import AttemptStatus
from app.modules.tests.attempts.timer import as_utc, calculate_deadline, is_expired, utc_now
from app.core.clock import utcnow



# --- 0. ENROLLMENT / OWNERSHIP GUARDS -------------------------------------
# Qo'shildi: avval start_exam/submit_skill/finish_exam_service/
# get_attempt_status_service/get_mock_result_service HECH QANDAY tekshiruv
# qilmasdan istalgan attempt_id bilan chaqirilishi mumkin edi (IDOR) va
# start_exam esa MockPurchase/MockRegistration'ni umuman tekshirmasdan
# attempt yaratardi. Bu yerdagi ikkita helper shu bo'shliqni yopadi va
# quyidagi barcha funksiyalarda ishlatiladi.

async def has_active_enrollment(db: AsyncSession, user_id: uuid.UUID, exam_id: str) -> bool:
    """Foydalanuvchi shu MockExam uchun 'yozilgan' hisoblanadimi?

    Ikkalasidan BIRI yetarli (spec: "yoki"):
      - Bekor qilinmagan MockRegistration (ochiq test sanasiga yozilgan)
      - Faol MockPurchase (sotib olingan/admin tomonidan yoqilgan)
    """
    reg_stmt = select(MockRegistration.id).where(
        MockRegistration.user_id == user_id,
        MockRegistration.mock_exam_id == exam_id,
        MockRegistration.status != RegistrationStatus.CANCELLED,
    ).limit(1)
    if (await db.execute(reg_stmt)).scalar_one_or_none() is not None:
        return True

    purchase_stmt = select(MockPurchase.id).where(
        MockPurchase.user_id == user_id,
        MockPurchase.mock_exam_id == exam_id,
        MockPurchase.is_active.is_(True),
    ).limit(1)
    return (await db.execute(purchase_stmt)).scalar_one_or_none() is not None


async def _get_owned_attempt(
    db: AsyncSession, attempt_id: int, user_id: uuid.UUID
) -> MockExamAttempt:
    """Attemptni yuklaydi va egasini tekshiradi.

    Boshqa studentning attempt_id'sini taxmin qilib chaqirsa ham, mavjud
    404 xabari bilan bir xil javob qaytadi — attempt mavjudligini
    oshkor qilmaslik uchun ataylab "topilmadi" deyiladi (403 emas).
    """
    attempt = await db.get(MockExamAttempt, attempt_id)
    if not attempt or attempt.user_id != user_id:
        raise HTTPException(status_code=404, detail="Imtihon urinish topilmadi")
    return attempt

# --- 1. DTM STANDARTLASHTIRISH VA BAHOLASH LOGIKASI ---
def calculate_scaled_score(raw_score: float, skill: SkillType) -> float:
    if skill in [SkillType.READING, SkillType.LISTENING]:
        return scale_listening_reading(int(raw_score))[0]
    return min(raw_score, 75.0)


get_cefr_level = cefr_for_score


# --- 2. CORE CRUD SERVICES (Admin uchun) ---
async def _resolve_skill_id(
    db: AsyncSession,
    model,
    label: str,
    explicit_id: Optional[str],
    cefr_level: Optional[str] = None,
    require_active: bool = False,
) -> str:
    """Mock uchun skill test ID sini aniqlaydi.

    explicit_id berilsa: mavjudligi, practice emasligi (va kerak bo'lsa faol
    ekanligi) tekshiriladi. Xato matni aynan nima noto'g'riligini aytadi.
    Berilmasa: bazadan eng so'nggi mos test avtomatik tanlanadi.
    """
    if explicit_id:
        obj = await db.get(model, explicit_id)
        if obj is None:
            raise HTTPException(400, f"{label} testi topilmadi: '{explicit_id}'")
        if obj.is_practice:
            raise HTTPException(
                400,
                f"{label} testi '{explicit_id}' practice (is_practice=true). "
                f"Mock uchun is_practice=false bo'lishi kerak.",
            )
        if require_active and not obj.is_active:
            raise HTTPException(
                400, f"{label} testi '{explicit_id}' faol emas (is_active=false)."
            )
        return explicit_id

    conds = [model.is_practice == False]  # noqa: E712
    if require_active:
        conds.append(model.is_active == True)  # noqa: E712
    if cefr_level is not None:
        conds.append(model.cefr_level == cefr_level)
    stmt = select(model.id).where(*conds).order_by(model.created_at.desc()).limit(1)
    found = (await db.execute(stmt)).scalar_one_or_none()
    if not found:
        level = f" ({cefr_level})" if cefr_level else ""
        raise HTTPException(
            400,
            f"Bazada mock uchun mos {label} testi yo'q{level}. "
            f"is_practice=false bo'lgan {label} test yarating.",
        )
    return found


async def create_exam(db: AsyncSession, data: MockExamCreate) -> MockExam:
    """Yangi imtihon yaratish va testlarni avtomatik tanlash."""
    r_id = await _resolve_skill_id(db, ReadingTest, "Reading", data.reading_id, data.cefr_level)
    l_id = await _resolve_skill_id(db, ListeningTest, "Listening", data.listening_id, data.cefr_level)
    w_id = await _resolve_skill_id(db, WritingExam, "Writing", data.writing_id, data.cefr_level)
    # Speaking testlarda cefr_level yo'q (Multilevel), lekin faol bo'lishi shart.
    s_id = await _resolve_skill_id(db, SpeakingTest, "Speaking", data.speaking_id, None, require_active=True)

    # Explicit ID validatsiyasi yuqorida (get() orqali) allaqachon bajarildi.

    # --- 3. Exam yaratish ---
    new_exam = MockExam(
        id=generate_mock_exam_id(),
        **data.model_dump(exclude={"reading_id", "listening_id", "writing_id", "speaking_id"}),
        reading_id=r_id,
        listening_id=l_id,
        writing_id=w_id,
        speaking_id=s_id,
    )
    db.add(new_exam)
    await db.commit()
    await db.refresh(new_exam)
    return new_exam



async def update_exam(db: AsyncSession, exam_id: str, data: MockExamUpdate) -> MockExam:
    stmt = select(MockExam).where(MockExam.id == exam_id)
    exam = (await db.execute(stmt)).scalar_one_or_none()
    if not exam:
        raise HTTPException(404, "Imtihon topilmadi")
    for key, value in data.model_dump(exclude_unset=True).items():
        setattr(exam, key, value)
    await db.commit()
    await db.refresh(exam)
    return exam

async def delete_exam_service(db: AsyncSession, exam_id: str):
    stmt = select(MockExam).where(MockExam.id == exam_id)
    exam = (await db.execute(stmt)).scalar_one_or_none()
    if not exam:
        raise HTTPException(404, "Imtihon topilmadi")
    await db.delete(exam)
    await db.commit()

# --- 3. PURCHASE & ACCESS SERVICES ---
async def list_user_exams(db: AsyncSession, user_id: uuid.UUID) -> List[dict]:
    exams = (await db.execute(select(MockExam).where(MockExam.is_active == True))).scalars().all()
    purchase_stmt = select(MockPurchase.mock_exam_id).where(
        and_(MockPurchase.user_id == user_id, MockPurchase.is_active == True)
    )
    purchased_ids = set((await db.execute(purchase_stmt)).scalars().all())

    return [{
        "id": e.id,
        "title": e.title,
        "description": e.description,
        "image_file": e.image_file,
        "cefr_level": e.cefr_level,
        "duration_minutes": e.duration_minutes,
        "price": e.price,
        "is_active": e.is_active,
        "is_purchased": e.id in purchased_ids,
        "reading_id": e.reading_id,
        "listening_id": e.listening_id,
        "writing_id": e.writing_id,
        "speaking_id": e.speaking_id,
        "created_at": e.created_at
    } for e in exams]

async def buy_exam_request(db: AsyncSession, user_id: uuid.UUID, exam_id: str):
    """ILGARI TOPILGAN XATO: bu funksiya har doim `MockPurchase(is_active=False)`
    yaratardi va butun kod bazasida `is_active`ni `True`ga o'tkazadigan
    BIRORTA HAM joy yo'q edi (na admin panelda, na billing tasdiqlashda) —
    demak pullik imtihonni "sotib olish" hech qachon haqiqiy kirish
    huquqi bermasdi.

    Endi:
      - Bepul (`exam.price <= 0`) imtihon — darhol faollashtiriladi
        (spec: "Mock exam bepul bo'lsa, ro'yxatdan o'tish tasdiqlanadi").
      - Pullik imtihon — `MockPurchase(is_active=False)` bilan bir qatorda
        standart billing oqimi uchun `BillingOrder` ham yaratiladi;
        foydalanuvchi `/billing/orders/{id}/receipt` orqali chek yuklaydi,
        admin uni tasdiqlagach (`/admin/billing/orders/{id}/approve`) —
        `MockPurchase.is_active` avtomatik `True`ga o'tadi (qarang:
        billing/router.py::approve_order).
    """
    from app.modules.billing.models import BillingOrder, BillingOrderStatus, BillingProductType

    exam = await db.get(MockExam, exam_id)
    if not exam or not exam.is_active:
        raise HTTPException(404, "Imtihon topilmadi")

    existing = (
        await db.execute(
            select(MockPurchase).where(
                and_(MockPurchase.user_id == user_id, MockPurchase.mock_exam_id == exam_id)
            )
        )
    ).scalar_one_or_none()
    if existing and existing.is_active:
        raise HTTPException(400, "Siz bu imtihonni allaqachon sotib olgansiz")

    if not exam.price or exam.price <= 0:
        if existing:
            existing.is_active = True
            purchase = existing
        else:
            purchase = MockPurchase(user_id=user_id, mock_exam_id=exam_id, is_active=True)
            db.add(purchase)
        await db.commit()
        await db.refresh(purchase)
        return purchase

    pending_order = (
        await db.execute(
            select(BillingOrder).where(
                BillingOrder.user_id == user_id,
                BillingOrder.product_type == BillingProductType.MOCK_EXAM,
                BillingOrder.product_id == exam_id,
                BillingOrder.status.in_(
                    [BillingOrderStatus.PENDING_PAYMENT, BillingOrderStatus.UNDER_REVIEW]
                ),
            )
        )
    ).scalar_one_or_none()
    if pending_order:
        raise HTTPException(
            400,
            "Sizda ushbu imtihon uchun to'lov kutilayotgan buyurtma allaqachon mavjud "
            f"(order_id={pending_order.id}) — chek yuklang yoki admin tasdiqlashini kuting.",
        )

    db.add(
        BillingOrder(
            user_id=user_id,
            product_type=BillingProductType.MOCK_EXAM,
            product_id=exam_id,
            product_title=exam.title,
            amount=exam.price,
        )
    )
    if existing:
        existing.is_active = False
        purchase = existing
    else:
        purchase = MockPurchase(user_id=user_id, mock_exam_id=exam_id, is_active=False)
        db.add(purchase)
    await db.commit()
    await db.refresh(purchase)
    return purchase

# --- 4. EXAM PROCESS SERVICES ---
async def _create_attempt_with_skills(
    db: AsyncSession,
    user_id: uuid.UUID,
    exam_id: str,
    registration_id: int | None = None,
    session_id: int | None = None,
) -> MockExamAttempt:
    """
    Bitta foydalanuvchi uchun MockExamAttempt va uning 4 ta bo'sh
    MockSkillAttempt qatorlarini yaratadi.

    Bu funksiya ikkala oqim uchun ham ishlatiladi:
      - Onlayn: foydalanuvchi o'zi /start ni chaqirganda (start_exam)
      - Qog'oz: admin "keldi" deb belgilaganda (confirm_attendance)
    Shu tufayli qog'ozda topshirgan talaba ham /save orqali natijasi
    yozilishi uchun kerakli MockExamAttempt/MockSkillAttempt qatorlariga ega bo'ladi.

    Bu funksiya db.commit() QILMAYDI — chaqiruvchi tomon commit qiladi,
    shunda chaqiruvchi o'z tranzaksiyasi bilan birga saqlay oladi.
    """
    attempt = MockExamAttempt(
        user_id=user_id,
        mock_exam_id=exam_id,
        registration_id=registration_id,
        session_id=session_id,
        status=AttemptStatus.CREATED,
    )
    db.add(attempt)
    await db.flush()  # ID ni olish uchun

    # Speaking aynan shu MockExam ichida biriktirilgan testdan olinadi.
    mock_exam = await db.get(MockExam, exam_id)
    if not mock_exam:
        raise HTTPException(404, "Mock imtihon topilmadi")
    chosen_speaking_id = mock_exam.speaking_id

    for skill_type in SkillType:
        new_skill = MockSkillAttempt(
            attempt_id=attempt.id,
            user_id=user_id,
            skill=skill_type,
            is_checked=False,
            submitted_at=None,
            raw_score=0,
            scaled_score=0.0,
            speaking_test_id=chosen_speaking_id if skill_type == SkillType.SPEAKING else None,
        )
        db.add(new_skill)

    await db.flush()
    return attempt


async def get_or_create_attempt_for_user(
    db: AsyncSession,
    user_id: uuid.UUID,
    exam_id: str,
    registration_id: int | None = None,
    session_id: int | None = None,
) -> MockExamAttempt:
    """Return an existing unfinished attempt or create a non-started attempt.

    This helper deliberately does NOT start/reset the timer. Only ``start_exam``
    may establish the authoritative deadline.
    """
    conditions = [
        MockExamAttempt.user_id == user_id,
        MockExamAttempt.mock_exam_id == exam_id,
        MockExamAttempt.is_finished.is_(False),
    ]
    if registration_id is not None:
        conditions.append(MockExamAttempt.registration_id == registration_id)
    stmt = select(MockExamAttempt).where(*conditions).order_by(MockExamAttempt.id.desc()).limit(1)
    existing = (await db.execute(stmt)).scalar_one_or_none()
    if existing:
        return existing

    attempt = await _create_attempt_with_skills(
        db, user_id, exam_id, registration_id=registration_id, session_id=session_id
    )
    await db.commit()
    await db.refresh(attempt)
    return attempt


def _active_scope_key(user_id: uuid.UUID, exam_id: str, registration_id: int | None) -> str:
    return f"{user_id}:{exam_id}:{registration_id if registration_id is not None else '-'}"


async def start_exam(
    db: AsyncSession,
    user_id: uuid.UUID,
    exam_id: str,
    registration_id: int | None = None,
    session_id: int | None = None,
) -> MockExamAttempt:
    exam = await db.get(MockExam, exam_id)
    if not exam or not exam.is_active:
        raise HTTPException(404, "Mock imtihon topilmadi yoki faol emas")

    if not await has_active_enrollment(db, user_id, exam_id):
        raise HTTPException(403, "Bu imtihonni boshlash uchun avval unga ro'yxatdan o'ting yoki sotib oling")

    if registration_id is not None:
        registration = await db.get(MockRegistration, registration_id)
        if registration is None or registration.user_id != user_id or registration.mock_exam_id != exam_id:
            raise HTTPException(403, "Imtihon registrationi yaroqsiz")
        if registration.status in {RegistrationStatus.CANCELLED, RegistrationStatus.COMPLETED}:
            raise HTTPException(409, "Bu exam registrationi allaqachon yopilgan")
        if session_id is None:
            session_id = registration.session_id

    scope_key = _active_scope_key(user_id, exam_id, registration_id)
    conditions = [
        MockExamAttempt.user_id == user_id,
        MockExamAttempt.mock_exam_id == exam_id,
        MockExamAttempt.is_finished.is_(False),
    ]
    if registration_id is not None:
        conditions.append(MockExamAttempt.registration_id == registration_id)
    attempt = (await db.execute(select(MockExamAttempt).where(*conditions).order_by(MockExamAttempt.id.desc()).limit(1))).scalar_one_or_none()

    if attempt is None:
        try:
            async with db.begin_nested():
                attempt = await _create_attempt_with_skills(
                    db, user_id, exam_id, registration_id=registration_id, session_id=session_id
                )
                attempt.active_scope_key = scope_key
                now = utc_now()
                duration_seconds = int(exam.duration_minutes) * 60
                attempt.started_at = now
                attempt.allowed_duration_seconds = duration_seconds
                attempt.expires_at = calculate_deadline(now, duration_seconds)
                transition_mock_attempt(attempt, AttemptStatus.IN_PROGRESS)
                await db.flush()
        except IntegrityError:
            attempt = (await db.execute(
                select(MockExamAttempt).where(MockExamAttempt.active_scope_key == scope_key).limit(1)
            )).scalar_one_or_none()
            if attempt is None:
                raise
    else:
        if attempt.status == AttemptStatus.EXPIRED:
            raise HTTPException(409, "Bu imtihon uchun ajratilgan vaqt tugagan")
        if attempt.status == AttemptStatus.FINALIZED or attempt.is_finished:
            raise HTTPException(409, "Bu imtihon allaqachon yakunlangan")
        # Only CREATED may start and only IN_PROGRESS may resume. Intermediate
        # completion states must never restart or extend the authoritative timer.
        if attempt.status not in {AttemptStatus.CREATED, AttemptStatus.IN_PROGRESS}:
            raise HTTPException(409, "Bu imtihon sessiyasi qayta boshlash uchun yaroqsiz")
        if attempt.status == AttemptStatus.CREATED:
            now = utc_now()
            duration_seconds = int(exam.duration_minutes) * 60
            attempt.active_scope_key = attempt.active_scope_key or scope_key
            attempt.started_at = now
            attempt.allowed_duration_seconds = duration_seconds
            attempt.expires_at = calculate_deadline(now, duration_seconds)
            transition_mock_attempt(attempt, AttemptStatus.IN_PROGRESS)
        elif attempt.expires_at and is_expired(attempt.expires_at):
            await expire_mock_attempt(db, attempt.id)
            raise HTTPException(409, "Bu imtihon uchun ajratilgan vaqt tugagan")
        elif attempt.expires_at is None:
            # Backward-compatible legacy attempt: preserve its original start time
            # and snapshot the exam duration once; future config edits cannot reset it.
            now = as_utc(attempt.started_at) or utc_now()
            duration_seconds = int(exam.duration_minutes) * 60
            attempt.allowed_duration_seconds = duration_seconds
            attempt.expires_at = calculate_deadline(now, duration_seconds)

    if registration_id is not None:
        registration = await db.get(MockRegistration, registration_id)
        if registration and registration.status == RegistrationStatus.CHECKED_IN:
            registration.status = RegistrationStatus.IN_PROGRESS
    await db.commit()
    await db.refresh(attempt)
    return attempt


async def expire_mock_attempt(db: AsyncSession, attempt_id: int) -> MockExamAttempt:
    """Idempotently mark an expired official attempt and finalize objective skills.

    Reading/Listening are scored from already persisted answers. Writing/Speaking
    remain pending/manual if they were submitted but not yet checked.
    """
    from app.modules.tests.attempts import common as at
    from app.modules.exams import attempts as _exa
    attempt = await db.get(MockExamAttempt, attempt_id)
    if attempt is None:
        raise HTTPException(404, "Imtihon urinish topilmadi")
    if attempt.status == AttemptStatus.FINALIZED:
        return attempt
    if attempt.status != AttemptStatus.EXPIRED:
        if not attempt.expires_at or not is_expired(attempt.expires_at):
            return attempt
        transition_mock_attempt(attempt, AttemptStatus.EXPIRED)
    # Objective sections are finalized from persisted answers exactly once.
    for skill in (SkillType.READING, SkillType.LISTENING):
        row = (await db.execute(select(MockSkillAttempt).where(
            MockSkillAttempt.attempt_id == attempt.id, MockSkillAttempt.skill == skill
        ))).scalar_one_or_none()
        if row and row.submitted_at is None:
            try:
                await _exa.finalize_mock_skill(db, at.spec_for(skill), attempt.user_id, attempt.id)
            except HTTPException as exc:
                if exc.status_code != 409:
                    raise
    return await db.get(MockExamAttempt, attempt.id)


async def ensure_mock_attempt_active(db: AsyncSession, attempt_id: int, user_id: uuid.UUID) -> MockExamAttempt:
    attempt = await _get_owned_attempt(db, attempt_id, user_id)
    if attempt.status == AttemptStatus.EXPIRED:
        raise HTTPException(409, "Imtihon vaqti tugagan")
    if attempt.status in {AttemptStatus.FINALIZED} or attempt.is_finished:
        raise HTTPException(409, "Yakunlangan imtihonda bu amalni bajarib bo'lmaydi")
    if attempt.status != AttemptStatus.IN_PROGRESS:
        raise HTTPException(409, "Imtihon hali boshlanmagan")
    if attempt.expires_at and is_expired(attempt.expires_at):
        await expire_mock_attempt(db, attempt.id)
        await db.commit()
        raise HTTPException(409, "Imtihon vaqti tugagan")
    return attempt


async def get_attempt_status_service(db: AsyncSession, attempt_id: int, user_id: uuid.UUID):
    ALL_SKILLS = ["LISTENING", "READING", "WRITING", "SPEAKING"]

    await _get_owned_attempt(db, attempt_id, user_id)

    stmt = select(MockSkillAttempt).where(MockSkillAttempt.attempt_id == attempt_id)
    res = await db.execute(stmt)
    db_skills = res.scalars().all()
    skill_map = {s.skill.upper(): s for s in db_skills}

    result = []
    for skill in ALL_SKILLS:
        s = skill_map.get(skill)
        is_submitted = bool(s and s.submitted_at is not None)
        if s and s.is_checked:
            state = "COMPLETED"
        elif is_submitted:
            state = "CHECKING"
        elif s and (s.last_saved_at is not None or s.current_part is not None or s.current_question is not None):
            state = "IN_PROGRESS"
        else:
            state = "NOT_STARTED"
        result.append({
            "skill": skill,
            "status": state,
            "is_checked": bool(s and s.is_checked),
            "is_submitted": is_submitted,
            "submitted_at": s.submitted_at if s else None,
            "current_part": s.current_part if s else None,
            "current_question": s.current_question if s else None,
            "draft": s.draft if s else None,
            "last_saved_at": s.last_saved_at if s else None,
        })
    return result



async def autosave_skill(
    db: AsyncSession,
    attempt_id: int,
    skill: SkillType,
    user_id: uuid.UUID,
    current_part: Optional[str] = None,
    current_question: Optional[str] = None,
    draft: Optional[dict] = None,
    user_answers: Optional[dict] = None,
) -> MockSkillAttempt:
    """Candidate progress/draftini idempotent tarzda serverga saqlaydi."""
    await ensure_mock_attempt_active(db, attempt_id, user_id)
    stmt = select(MockSkillAttempt).where(
        MockSkillAttempt.attempt_id == attempt_id,
        MockSkillAttempt.skill == skill,
    )
    skill_attempt = (await db.execute(stmt)).scalar_one_or_none()
    if not skill_attempt:
        raise HTTPException(status_code=404, detail="Bo'lim urinishi topilmadi")
    if skill_attempt.submitted_at is not None:
        raise HTTPException(status_code=400, detail="Ushbu bo'lim allaqachon topshirilgan")

    if current_part is not None:
        skill_attempt.current_part = current_part
    if current_question is not None:
        skill_attempt.current_question = current_question
    if draft is not None:
        skill_attempt.draft = draft
    if user_answers is not None:
        skill_attempt.user_answers = user_answers
        if skill in (SkillType.READING, SkillType.LISTENING):
            # Haqiqiy manba — attempt_answers (savol bo'yicha upsert, merge).
            from app.modules.tests.attempts import common as at
            from app.modules.exams import attempts as _exa
            await _exa.save_mock_answers_bulk(db, user_id, attempt_id, skill, user_answers)
    skill_attempt.last_saved_at = utcnow()
    await db.commit()
    await db.refresh(skill_attempt)
    return skill_attempt

async def submit_skill(db: AsyncSession, attempt_id: int, skill: SkillType, user_id: uuid.UUID):
    """
    Foydalanuvchi biror bo'limni (masalan, Reading) tugatganini bildiradi.
    Funksiya tegishli modul jadvalidan natijani oladi va Mock statusini yangilaydi.
    """
    # 0. Ownership + authoritative deadline.
    await ensure_mock_attempt_active(db, attempt_id, user_id)

    # 1. Bo'lim urinishini qidirish
    stmt = select(MockSkillAttempt).where(
        and_(
            MockSkillAttempt.attempt_id == attempt_id,
            MockSkillAttempt.skill == skill
        )
    )
    result = await db.execute(stmt)
    skill_attempt = result.scalar_one_or_none()

    if not skill_attempt:
        raise HTTPException(status_code=404, detail="Bo'lim urinishi topilmadi")

    if skill in (SkillType.READING, SkillType.LISTENING):
        # Ilgari: mavjud ReadingResult/ListeningResult qidirilar, topilmasa
        # JIMGINA 0 ball bilan "baholangan" deb belgilanardi. Endi: saqlangan
        # javoblardan baholanadi; takroriy chaqiruv mavjud natijani qoldiradi.
        from app.modules.tests.attempts import common as at
        from app.modules.exams import attempts as _exa
        await _exa.finalize_mock_skill(db, at.spec_for(skill), user_id, attempt_id)
        await db.refresh(skill_attempt)
        return skill_attempt

    if skill_attempt.submitted_at is not None:
        raise HTTPException(status_code=400, detail="Ushbu bo'lim allaqachon topshirilgan")

    # 2. MockAttempt orqali tegishli test ID sini aniqlash
    # (Sessiyaga bog'langan MockExam dan ID larni olamiz)
    attempt_stmt = select(MockExamAttempt).options(selectinload(MockExamAttempt.exam)).where(MockExamAttempt.id == attempt_id)
    attempt_data = (await db.execute(attempt_stmt)).scalar_one_or_none()
    exam = attempt_data.exam

    # 3. Tegishli modul natijalar jadvalidan ballni tortib olish
    raw_score = 0.0
    scaled_score = 0.0
    if skill == SkillType.READING:
        r_res = await db.execute(select(ReadingResult.raw_score).where(
            and_(ReadingResult.test_id == exam.reading_id, ReadingResult.user_id == user_id, ReadingResult.exam_attempt_id == attempt_id)
        ).order_by(ReadingResult.created_at.desc()).limit(1))
        raw_score = r_res.scalars().first() or 0.0
        
    elif skill == SkillType.LISTENING:
        l_res = await db.execute(select(ListeningResult.raw_score).where(
            and_(ListeningResult.exam_id == exam.listening_id, ListeningResult.user_id == user_id, ListeningResult.exam_attempt_id == attempt_id)
        ).order_by(ListeningResult.created_at.desc()).limit(1))
        raw_score = l_res.scalars().first() or 0.0

    elif skill == SkillType.WRITING:
        w_res = await db.execute(select(WritingResult.scaled_score, WritingResult.is_finalized).where(
            and_(WritingResult.exam_id == exam.writing_id, WritingResult.user_id == user_id, WritingResult.exam_attempt_id == attempt_id)
        ).order_by(WritingResult.created_at.desc()).limit(1))
        w_row = w_res.first()
        scaled_score = (w_row[0] or 0.0) if w_row else 0.0
        writing_finalized = bool(w_row and w_row[1])

    # 4. MockSkillAttempt jadvalini yangilash
    skill_attempt.submitted_at = utcnow()
    skill_attempt.raw_score = raw_score

    if skill in [SkillType.READING, SkillType.LISTENING]:
        # Avtomatik hisoblash
        skill_attempt.scaled_score = calculate_scaled_score(raw_score, skill)
        skill_attempt.cefr_level = get_cefr_level(skill_attempt.scaled_score)
        skill_attempt.is_checked = True
    elif skill == SkillType.WRITING:
        # Writing AI tomonidan avtomatik baholangan — scaled_score WritingResult dan olindi
        skill_attempt.scaled_score = scaled_score
        # Writing endi odam tomonidan baholanadi: baholanmaguncha is_checked=False qoladi,
        # aks holda umumiy natija 0 ball bilan erta yakunlanib ketardi.
        skill_attempt.cefr_level = get_cefr_level(scaled_score) if writing_finalized else None
        skill_attempt.is_checked = writing_finalized
    else:
        # Speaking — qo'lda baholanadi
        skill_attempt.scaled_score = 0.0
        skill_attempt.is_checked = False

    try:
        await db.commit()
        await db.refresh(skill_attempt)
    except Exception:
        await db.rollback()
        raise HTTPException(status_code=500, detail="Xatolik yuz berdi")
    
    return skill_attempt

async def finish_exam_service(
    db: AsyncSession,
    attempt_id: int,
    user_id: Optional[uuid.UUID] = None,
    require_all_checked: bool = True,
    allow_pending: bool = False,
) -> MockExamResult:
    """Return the persisted overall snapshot and finalize only when all skills are complete.

    ``allow_pending=True`` is used by the student-facing endpoint so a partial
    exam returns a durable PENDING result instead of being mistaken for a
    completed exam. Admin finalization keeps the historical 409/400-style
    blocking behavior by leaving ``allow_pending`` false.
    """
    from .finalize import _get_or_create_overall, _refresh_overall_snapshot, _skill_state, try_finalize_mock_attempt

    if user_id is not None:
        await _get_owned_attempt(db, attempt_id, user_id)

    attempt = await db.get(MockExamAttempt, attempt_id)
    if attempt is None:
        raise HTTPException(404, "Sessiya topilmadi")

    if attempt.status == AttemptStatus.IN_PROGRESS and attempt.expires_at and is_expired(attempt.expires_at):
        await expire_mock_attempt(db, attempt.id)
        attempt = await db.get(MockExamAttempt, attempt.id)

    # Historical FINALIZED attempts are immutable snapshots.
    if attempt.is_finished:
        existing = (await db.execute(
            select(MockExamResult).where(MockExamResult.attempt_id == attempt_id).limit(1)
        )).scalar_one_or_none()
        if existing is None:
            raise HTTPException(409, "Yakunlangan imtihonning natijasi topilmadi")
        return existing

    # Lock + evaluate through the single finalizer.
    await try_finalize_mock_attempt(db, attempt_id)
    attempt = await db.get(MockExamAttempt, attempt_id)
    if attempt is None:
        raise HTTPException(404, "Sessiya topilmadi")
    result = await _get_or_create_overall(db, attempt)

    if attempt.is_finished:
        await db.commit()
        await db.refresh(result)
        return result

    _rows, _by_skill, states, scores = await _skill_state(db, attempt_id)
    if require_all_checked and not allow_pending:
        missing = [skill for skill, state in states.items() if state != "COMPLETED"]
        await db.rollback()
        raise HTTPException(409, f"Imtihon hali yakunlanmagan: {', '.join(missing)}")

    await _refresh_overall_snapshot(db, attempt, result, states, scores, finalized=False)
    await db.commit()
    await db.refresh(result)
    return result


async def manual_grade_skill(
    db: AsyncSession,
    attempt_id: int,
    skill: SkillType,
    scaled_score: float,
    raw_score: Optional[float] = None,
) -> MockSkillAttempt:
    """
    Admin tomonidan qo'lda ball qo'yish (masalan qog'ozda yozilgan Writing
    inshosi yoki Speaking audiosi tinglab chiqilgach). To'g'ridan-to'g'ri
    MockSkillAttemptga yozadi — shu bilan finish_exam_service buni ko'ra oladi.

    Bu WRITING (qog'ozdan qo'lda o'qib baholash) va zarur bo'lsa boshqa
    ko'nikmalar uchun ham universal yo'l — grading_router faqat
    Listening/Reading uchun AI-OCR bilan avtomatik ishlaydi, Writing esa
    hozircha qo'lyozmani AI o'qishga mo'ljallanmagani uchun admin
    o'zi o'qib shu endpoint orqali ball qo'yadi.
    """
    stmt = select(MockSkillAttempt).where(
        and_(MockSkillAttempt.attempt_id == attempt_id, MockSkillAttempt.skill == skill)
    )
    skill_attempt = (await db.execute(stmt)).scalar_one_or_none()
    if not skill_attempt:
        raise HTTPException(404, "Bu urinish uchun tegishli bo'lim (skill attempt) topilmadi")
    attempt = await db.get(MockExamAttempt, attempt_id)
    if attempt is None:
        raise HTTPException(404, "Imtihon urinish topilmadi")
    if attempt.is_finished:
        raise HTTPException(409, "Yakunlangan imtihon natijasini o'zgartirib bo'lmaydi")

    if scaled_score < 0 or scaled_score > 75:
        raise HTTPException(400, "scaled_score 0 dan 75 gacha bo'lishi kerak")
    if raw_score is not None and (raw_score < 0 or raw_score > 75):
        raise HTTPException(400, "raw_score 0 dan 75 gacha bo'lishi kerak")

    skill_attempt.scaled_score = scaled_score
    skill_attempt.raw_score = raw_score if raw_score is not None else skill_attempt.raw_score
    skill_attempt.cefr_level = get_cefr_level(scaled_score)
    skill_attempt.is_checked = True
    skill_attempt.submitted_at = skill_attempt.submitted_at or utcnow()

    await db.flush()
    # Manual grading can be the last missing skill; finalize automatically.
    all_skills = (await db.execute(select(MockSkillAttempt).where(MockSkillAttempt.attempt_id == attempt_id))).scalars().all()
    if len(all_skills) == 4 and all(s.is_checked for s in all_skills):
        await finish_exam_service(db, attempt_id, require_all_checked=True)
    else:
        await db.commit()
    await db.refresh(skill_attempt)
    return skill_attempt


async def grade_skill_as_staff(
    db: AsyncSession,
    current_user,
    attempt_id: int,
    skill: SkillType,
    scaled_score: float,
    raw_score: Optional[float] = None,
) -> MockSkillAttempt:
    """YAGONA qo'lda baholash yo'li: manual_grade_skill.

    Ikkala endpoint (admin `/attempts/{id}/manual-grade` va Mock Center
    `/attempts/{id}/skills/{skill}/score`) shu funksiyadan foydalanadi.
    """
    stmt = (
        select(MockExamAttempt)
        .join(MockExam, MockExam.id == MockExamAttempt.mock_exam_id)
        .where(MockExamAttempt.id == attempt_id)
    )
    attempt = (await db.execute(stmt)).scalar_one_or_none()
    if attempt is None:
        raise HTTPException(404, "Attempt topilmadi")

    return await manual_grade_skill(db, attempt_id, skill, scaled_score, raw_score)


async def get_mock_result_service(
    db: AsyncSession, attempt_id: int, user_id: uuid.UUID, allow_pending: bool = False
) -> MockExamResult:
    attempt = await _get_owned_attempt(db, attempt_id, user_id)
    if attempt.status == AttemptStatus.IN_PROGRESS and attempt.expires_at and is_expired(attempt.expires_at):
        await expire_mock_attempt(db, attempt.id)
        await db.commit()
        attempt = await db.get(MockExamAttempt, attempt.id)
    result = (await db.execute(
        select(MockExamResult).where(MockExamResult.attempt_id == attempt_id).limit(1)
    )).scalar_one_or_none()
    if result is None and allow_pending and not attempt.is_finished:
        from .finalize import _get_or_create_overall
        result = await _get_or_create_overall(db, attempt)
        await db.commit()
        await db.refresh(result)
    if result is None:
        raise HTTPException(404, "Ushbu imtihon uchun natija hali mavjud emas.")
    if result.status != "FINALIZED" and not allow_pending:
        raise HTTPException(404, "Ushbu imtihon uchun yakuniy natija hali mavjud emas.")
    return result
