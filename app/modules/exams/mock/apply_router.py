import secrets
from datetime import date
from typing import Optional

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.captcha import require_captcha
from app.core.config import settings
from app.core.database import get_db
from app.core.rate_limit import _get_ip, limiter
from app.modules.auth.models import Gender, User, UserProfile, UserRole
from app.modules.exams.mock.applicant_media import is_own_photo_url, save_applicant_photo
from app.modules.exams.mock.applicant_rules import normalize_phone, normalize_region
from app.modules.exams.mock.apply_schemas import (
    ApplicantRegisterInput, ApplicantResponse, ApplySessionPublic,
)
from app.modules.exams.mock.mk_id import generate_unique_mk_id
from app.modules.exams.mock.models import ExamSession, MockExam, MockRegistration, RegistrationStatus
from app.modules.exams.mock.seats import reserve_seat

router = APIRouter(tags=['Public Registration'])

_MK_ID_INSERT_ATTEMPTS = 5


async def _load_open_session(db: AsyncSession, registration_slug: str):
    """Public ro'yxatdan o'tish uchun ochiq sessiya + imtihonni qaytaradi (yoki HTTPException).

    Sig'im qulfi emas, atomik joy band qilish (seats.reserve_seat) bilan himoyalangan.
    """
    stmt = select(ExamSession).where(ExamSession.registration_slug == registration_slug)
    session = (await db.execute(stmt)).scalar_one_or_none()
    if not session:
        raise HTTPException(404, 'Sessiya topilmadi')
    if not session.is_active:
        raise HTTPException(403, "Ro'yxatdan o'tish hozirda yopiq")
    exam = await db.get(MockExam, session.mock_exam_id)
    if exam is None:
        raise HTTPException(404, 'Sessiya topilmadi')
    return session, exam


# ─── Public Registration (login talab qilinmaydi) ────────────────────────────

async def _payment_details(db: AsyncSession, session, exam):
    """To'lov rekvizitlari manbai (tashkilot hisobi) olib tashlangan: hozircha ko'rsatilmaydi."""
    return None


@router.get('/apply/{registration_slug}', response_model=ApplySessionPublic)
async def get_session_for_registration(
    registration_slug: str,
    db: AsyncSession = Depends(get_db),
):
    """Public endpoint — foydalanuvchi login qilmasdan sessiya ma'lumotini oladi."""
    session, exam = await _load_open_session(db, registration_slug)
    is_online = (session.location_address or '').strip().lower() == 'online'
    return {
        'delivery_mode': session.delivery_mode,
        'id': session.id,
        'name': exam.title or "Mock Imtihon",
        'exam_type': 'online' if is_online else 'offline',
        'scheduled_at': session.exam_date,
        'location': session.location_address,
        'duration_minutes': exam.duration_minutes or 180,
        'payment_details': await _payment_details(db, session, exam),
    }


@router.post('/apply/{registration_slug}/photo', status_code=201)
@limiter.limit("10/minute")
async def upload_applicant_photo(
    request: Request,
    registration_slug: str,
    file: UploadFile = File(...),
    captcha_token: Optional[str] = Form(None),
    db: AsyncSession = Depends(get_db),
):
    """Candidate rasmini yuklaydi (login talab qilinmaydi). Javob: {"photo_url": ...}.

    Ro'yxatdan o'tishda shu URL'ni `photo_url` sifatida yuboring.
    """
    await _load_open_session(db, registration_slug)
    await require_captcha(captcha_token, _get_ip(request))
    return {"photo_url": await save_applicant_photo(file)}


@router.post('/apply/{registration_slug}', response_model=ApplicantResponse, status_code=201)
@limiter.limit("10/minute")
async def register_applicant(
    request: Request,
    registration_slug: str,
    data: ApplicantRegisterInput,
    db: AsyncSession = Depends(get_db),
):
    """Public endpoint — applicant ro'yxatdan o'tadi, login talab qilinmaydi.

    User, profil, kontakt va registration BIR tranzaksiyada yaratiladi: xato bo'lsa
    hech narsa qolmaydi. Joy band qilish atomik (seats.reserve_seat).
    """
    await require_captcha(data.captcha_token, _get_ip(request))
    session, exam = await _load_open_session(db, registration_slug)
    session_id = session.id
    mock_exam_id = session.mock_exam_id

    # Onlayn to'lov majburiy bo'lsa, anonim (to'lovsiz) ro'yxatdan o'tish mumkin emas.
    if session.requires_online_payment and exam.price and exam.price > 0:
        raise HTTPException(402, "Bu sessiya uchun onlayn to'lov talab qilinadi; akkaunt orqali ro'yxatdan o'ting")

    # MK ID formati tug'ilgan sanaga tayanadi va Exams login shu formatni kutadi.
    if not data.birth_date:
        raise HTTPException(400, "Tug'ilgan sana majburiy")
    try:
        birth_date_obj = date.fromisoformat(data.birth_date)
    except ValueError:
        raise HTTPException(400, "Tug'ilgan sana YYYY-MM-DD formatida bo'lishi kerak")
    today = date.today()
    if birth_date_obj > today:
        raise HTTPException(400, "Tug'ilgan sana kelajakda bo'la olmaydi")
    if not is_own_photo_url(data.photo_url):
        raise HTTPException(400, "Rasmni avval /apply/{slug}/photo orqali yuklang")
    age = today.year - birth_date_obj.year - ((today.month, today.day) < (birth_date_obj.month, birth_date_obj.day))

    gender = Gender.MALE if data.gender.upper() == 'M' else Gender.FEMALE
    full_name = f"{data.last_name} {data.first_name}"
    if data.patronymic:
        full_name += f" {data.patronymic}"

    # Telefon: exams login ikkinchi omili (oxirgi 4 raqam) shu raqamga tayanadi.
    require_phone = settings.EXAMS_MK_ID_REQUIRE_PHONE_LAST4
    if require_phone and not data.phone:
        raise HTTPException(400, "Telefon raqami majburiy")
    phone = normalize_phone(data.phone) if data.phone else None
    # Telefon hech qayerga (UserContact) biriktirilmaydi: applicant akkaunti login qila olmaydi,
    # va telefon bilan mavjud akkauntni aniqlab bo'lmasin. Faqat oxirgi 4 raqam saqlanadi.
    phone_last4 = phone[-4:] if phone else None

    region = normalize_region(data.address)

    last_exc: IntegrityError | None = None
    for _ in range(_MK_ID_INSERT_ATTEMPTS):
        await reserve_seat(db, session_id)  # atomik: sig'im to'lgan bo'lsa 400 (rollback bilan birga qaytariladi)
        try:
            student_id = await generate_unique_mk_id(db, birth_date_obj)
        except RuntimeError as exc:
            raise HTTPException(409, "MK ID yaratib bo'lmadi; qayta urinib ko'ring") from exc

        dummy_user = User(is_active=True, global_role=UserRole.STUDENT, hashed_password=None)  # login qila olmaydi
        db.add(dummy_user)
        await db.flush()
        db.add(UserProfile(user_id=dummy_user.id, full_name=full_name, birth_date=birth_date_obj, gender=gender))
        registration = MockRegistration(
            user_id=dummy_user.id,
            session_id=session_id,
            mock_exam_id=mock_exam_id,
            full_name=full_name,
            student_id=student_id,
            exam_access_id=secrets.token_urlsafe(27),
            qr_token=secrets.token_urlsafe(24),
            age=age,
            date_of_birth=birth_date_obj,
            gender=gender.value,
            region=region,
            phone_last4=phone_last4,
            photo_url=data.photo_url,
            status=RegistrationStatus.REGISTERED,
        )
        db.add(registration)
        try:
            await db.commit()
        except IntegrityError as exc:
            await db.rollback()  # user/profil/kontakt va joy band qilish ham qaytariladi
            last_exc = exc
            continue
        await db.refresh(registration)
        return {
            "id": str(registration.id),
            "session_id": registration.session_id,
            "exam_id": registration.student_id,
            "first_name": data.first_name,
            "last_name": data.last_name,
            "patronymic": data.patronymic,
            "address": data.address,
            "gender": data.gender,
            "birth_date": data.birth_date,
            "passport_series": None,  # saqlanmaydi: ContactType'da passport turi yo'q
            "phone": data.phone,
            "photo_url": data.photo_url,
            "status": "registered",
            "created_at": registration.created_at,
        }
    raise HTTPException(409, "Ro'yxatdan o'tishda ID ziddiyati yuz berdi, qayta urinib ko'ring") from last_exc
