"""
exams_auth/service.py
=======================
Exams uchun mustaqil autentifikatsiya biznes-logikasi.

Ataylab `app.modules.auth.service.AuthService` dan meros OLMAYDI va uning
metodlarini chaqirmaydi — faqat pastki daraja stateless yordamchilarni
(`SmsService`) qayta ishlatadi. Bu bilan asosiy login/parol/OTP oqimi
umuman tegilmagan holda qoladi (band 2: "boshqa Steply... buzma").
"""

import hashlib
import hmac
import secrets
import uuid
from app.core.throttle import FailureThrottle
from app.core.refresh_rotation import (
    ReuseOutcome, claim_refresh_token, on_lost_claim, on_reused_token,
)
from datetime import datetime, timedelta, timezone
from typing import List, Optional

from fastapi import HTTPException, Request
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.config import settings
from app.modules.auth.models import ContactType, User
from app.modules.exams.mock.models import (
    ExamSession,
    MockExam,
    MockExamAttempt,
    MockRegistration,
    RegistrationStatus,
)

from .models import ExamsAuthSession
from .schemas import (
    EnrolledExamResponse,
    EnrolledSessionInfo,
    ExamsStudentResponse,
    ExamsTokenResponse,
    ExamIdLoginResponse,
)
from .security import create_exams_access_token

# Har qanday mavjud/mavjud emas MK ID uchun BIR XIL javob — mavjudlik yoki
# yozilganlik holatini oshkor qilmaslik uchun (band 6: "Student mavjudligi
# va enrollment holatini ortiqcha oshkor qilma").
_GENERIC_LOGIN_ERROR = "MK ID topilmadi yoki Exams tizimiga kirish huquqi yo'q"


# MK ID + ikkinchi omil: noto'g'ri urinishlarni registratsiya bo'yicha cheklash.
# Xotirada saqlanadi (worker boshiga), shuning uchun bu qo'shimcha to'siq —
# asosiy himoya emas; asosiy himoya `exam_id` ga o'tish va IP rate-limit.
_FACTOR_MAX_FAILURES = 5
_FACTOR_WINDOW_SECONDS = 15 * 60
# Umumiy storage (RATE_LIMIT_STORAGE_URI): barcha worker'lar bitta hisobni ko'radi.
_factor_throttle = FailureThrottle("exams-factor", _FACTOR_MAX_FAILURES, _FACTOR_WINDOW_SECONDS)


class ExamsAuthService:
    OTP_EXPIRE_MINUTES = 5
    OTP_RESEND_COOLDOWN_SECONDS = 5 * 60
    OTP_MAX_SENDS = 3
    OTP_SEND_BLOCK_MINUTES = 60
    OTP_MAX_VERIFY_ATTEMPTS = 5

    def __init__(self, db: AsyncSession):
        self.db = db

    # ---------------------------------------------------------------
    # UTIL
    # ---------------------------------------------------------------

    @staticmethod
    def _hash(value: str) -> str:
        return hashlib.sha256(value.encode()).hexdigest()

    @staticmethod
    def _aware(dt: datetime) -> datetime:
        if dt.tzinfo is None:
            return dt.replace(tzinfo=timezone.utc)
        return dt

    def _verify_hash(self, plain: str, hashed: str) -> bool:
        return hmac.compare_digest(self._hash(plain), hashed)

    # ---------------------------------------------------------------
    # STUDENT LOOKUP
    # ---------------------------------------------------------------

    async def _find_registration_by_mk_id(self, mk_id: str) -> Optional[MockRegistration]:
        """MK ID -> ochiq seansdagi ro'yxat (registration).

        MK ID endi `users_profile.mk_id` da emas, `mock_registrations.student_id`
        da yashaydi: ya'ni MK ID "kim" emas, "qaysi seansdagi kim" degani.
        Bekor qilingan ro'yxat kirish huquqi bermaydi.
        """
        stmt = (
            select(MockRegistration)
            .options(selectinload(MockRegistration.session))
            .where(
                MockRegistration.student_id == mk_id,
                MockRegistration.status != RegistrationStatus.CANCELLED,
            )
            .order_by(MockRegistration.created_at.desc())
            .limit(1)
        )
        return (await self.db.execute(stmt)).scalar_one_or_none()

    async def _resolve_login_target(self, mk_id: str) -> tuple[User, MockRegistration]:
        """MK ID bo'yicha (user, registration) juftligini qaytaradi.

        Har qanday muvaffaqiyatsiz holat BIR XIL 404 beradi — MK ID mavjudmi,
        student borми, ro'yxat bekor qilinganmi degan savollarga javob
        oshkor qilinmaydi.
        """
        registration = await self._find_registration_by_mk_id(mk_id)
        if not registration:
            raise HTTPException(404, _GENERIC_LOGIN_ERROR)

        user = await self._find_student_by_id(registration.user_id)
        if not user or not user.is_active:
            raise HTTPException(404, _GENERIC_LOGIN_ERROR)
        return user, registration

    @staticmethod
    def _primary_phone(user: User) -> Optional[str]:
        verified = [c for c in user.contacts if c.contact_type == ContactType.PHONE and c.is_verified]
        if not verified:
            return None
        primary = [c for c in verified if c.is_primary]
        return (primary[0] if primary else verified[0]).value

    def _student_response(
        self, user: User, registration: MockRegistration, with_phone: bool = False
    ) -> ExamsStudentResponse:
        """Nomzod ma'lumotlari + rasmi — registration snapshot'idan olinadi."""
        return ExamsStudentResponse(
            id=user.id,
            full_name=registration.full_name,
            mk_id=registration.student_id,
            exam_id=registration.exam_access_id,
            registration_id=registration.id,
            phone_last4=((self._primary_phone(user) or "")[-4:] or None) if with_phone else None,
            photo_url=registration.photo_url,
            age=registration.age,
            date_of_birth=registration.date_of_birth,
            gender=registration.gender,
            region=registration.region,
        )

    # ---------------------------------------------------------------
    # LOGIN — FAQAT EXAM ACCESS ID
    # ---------------------------------------------------------------

    async def login_by_exam_id(self, exam_id: str, request: Request) -> ExamIdLoginResponse:
        stmt = (
            select(MockRegistration)
            .options(selectinload(MockRegistration.session))
            .where(
                MockRegistration.exam_access_id == exam_id.strip(),
                MockRegistration.status != RegistrationStatus.CANCELLED,
            )
            .limit(1)
        )
        registration = (await self.db.execute(stmt)).scalar_one_or_none()
        return await self._login_for_registration(
            registration, request, "Exam ID topilmadi yoki imtihonga kirish huquqi yo'q"
        )

    async def login_by_student_id(
        self, student_id: str, request: Request, phone_last4: Optional[str] = None
    ) -> ExamIdLoginResponse:
        """Student ID (MK ID, masalan MK-290706-785) bilan kirish.

        MK ID tahmin qilinishi oson (sana + 3 raqam), shuning uchun:
          * EXAMS_MK_ID_LOGIN_ENABLED=False bo'lsa bu yo'l butunlay yopiladi;
          * EXAMS_MK_ID_REQUIRE_PHONE_LAST4=True bo'lsa ikkinchi omil talab qilinadi.
        """
        if not settings.EXAMS_MK_ID_LOGIN_ENABLED:
            raise HTTPException(404, _GENERIC_LOGIN_ERROR)

        registration = await self._find_registration_by_mk_id(student_id)
        if registration is not None and settings.EXAMS_MK_ID_REQUIRE_PHONE_LAST4:
            await self._check_second_factor(registration, phone_last4)
        return await self._login_for_registration(registration, request, _GENERIC_LOGIN_ERROR)

    async def _check_second_factor(
        self, registration: MockRegistration, phone_last4: Optional[str]
    ) -> None:
        """Telefonning oxirgi 4 raqamini tekshiradi.

        Muvaffaqiyatsizlik har doim `_GENERIC_LOGIN_ERROR` (404) — MK ID
        mavjudligi ikkinchi omil to'g'ri bo'lmaguncha oshkor bo'lmaydi.
        """
        if _factor_throttle.is_blocked(str(registration.id)):
            raise HTTPException(429, "Juda ko'p noto'g'ri urinish. Biroz kutib qayta urinib ko'ring")

        if registration.phone_last4:
            # Ochiq ro'yxatdan o'tish: telefon oxirgi 4 raqami registratsiyada saqlangan.
            endings = {registration.phone_last4}
        else:
            user = await self._find_student_by_id(registration.user_id)
            endings = {
                "".join(ch for ch in c.value if ch.isdigit())[-4:]
                for c in (user.contacts if user else [])
                if c.contact_type == ContactType.PHONE and c.value
            }
        ok = bool(phone_last4) and any(
            secrets.compare_digest(phone_last4, e) for e in endings if len(e) == 4
        )
        if not ok:
            _factor_throttle.record_failure(str(registration.id))
            raise HTTPException(404, _GENERIC_LOGIN_ERROR)
        _factor_throttle.reset(str(registration.id))

    @staticmethod
    def _check_login_window(registration: MockRegistration) -> None:
        """Seans vaqt oynasidan tashqarida kirishni rad etadi (sozlangan bo'lsa)."""
        before = settings.EXAMS_LOGIN_OPENS_MINUTES_BEFORE
        after = settings.EXAMS_LOGIN_CLOSES_MINUTES_AFTER
        session = registration.session
        if (before is None and after is None) or session is None or session.exam_date is None:
            return
        start = session.exam_date
        if start.tzinfo is None:
            start = start.replace(tzinfo=timezone.utc)
        now = datetime.now(timezone.utc)
        if before is not None and now < start - timedelta(minutes=before):
            raise HTTPException(403, "Imtihon kirishi hali ochilmagan")
        if after is not None and now > start + timedelta(minutes=after):
            raise HTTPException(403, "Imtihonga kirish vaqti tugagan")

    async def _login_for_registration(
        self,
        registration: Optional[MockRegistration],
        request: Request,
        not_found_msg: str,
    ) -> ExamIdLoginResponse:
        # Har qanday "topilmadi" holati BIR XIL 404 — ID mavjudligi oshkor bo'lmaydi.
        if not registration:
            raise HTTPException(404, not_found_msg)
        user = await self._find_student_by_id(registration.user_id)
        if not user or not user.is_active:
            raise HTTPException(404, not_found_msg)
        if registration.status == RegistrationStatus.CANCELLED:
            raise HTTPException(403, "Bu registration bekor qilingan")
        if registration.status == RegistrationStatus.COMPLETED:
            raise HTTPException(403, "Imtihon yakunlangan. Qayta kirib bo'lmaydi.")

        self._check_login_window(registration)

        # Kirish = student keldi deb hisoblanadi: REGISTERED -> CHECKED_IN.
        if registration.status == RegistrationStatus.REGISTERED:
            registration.status = RegistrationStatus.CHECKED_IN
            from datetime import datetime, timezone as _tz
            registration.attended_confirmed_at = datetime.now(_tz.utc)
            await self.db.commit()
            await self.db.refresh(registration)

        tokens = await self._create_session_and_tokens(user, registration.id, request)
        student = self._student_response(user, registration)
        return ExamIdLoginResponse(status="success", token=tokens, student=student)

    # ---------------------------------------------------------------
    # SESSION / TOKENS
    # ---------------------------------------------------------------

    async def _create_session_and_tokens(
        self,
        user: User,
        registration_id: Optional[int],
        request: Request,
        session_family_id: Optional[uuid.UUID] = None,
    ) -> ExamsTokenResponse:
        from .security import EXAMS_REFRESH_TOKEN_DAYS

        refresh_token = secrets.token_urlsafe(64)

        session = ExamsAuthSession(
            user_id=user.id,
            registration_id=registration_id,
            refresh_token_hash=self._hash(refresh_token),
            session_family_id=session_family_id or uuid.uuid4(),
            user_agent=request.headers.get("user-agent"),
            ip_address=request.client.host if request.client else None,
            expires_at=datetime.now(timezone.utc) + timedelta(days=EXAMS_REFRESH_TOKEN_DAYS),
        )
        self.db.add(session)
        await self.db.flush()

        # Bind the access token to this exact Exams DB session.
        access_token = create_exams_access_token(
            user_id=user.id, extra_data={"reg": registration_id, "sid": str(session.id)}
        )
        await self.db.commit()

        return ExamsTokenResponse(access_token=access_token, refresh_token=refresh_token)

    async def refresh(self, refresh_token: str, request: Request) -> ExamsTokenResponse:
        now = datetime.now(timezone.utc)
        refresh_hash = self._hash(refresh_token)

        session = (await self.db.execute(
            select(ExamsAuthSession).where(ExamsAuthSession.refresh_token_hash == refresh_hash)
        )).scalar_one_or_none()
        if not session:
            raise HTTPException(401, "Yaroqsiz refresh token")
        if self._aware(session.expires_at) < now:
            session.is_revoked = True
            await self.db.commit()
            raise HTTPException(401, "Sessiya muddati tugagan, qaytadan kiring")
        if session.is_revoked:
            outcome = await on_reused_token(self.db, ExamsAuthSession, session, now)
            if outcome == ReuseOutcome.GRACE:
                raise HTTPException(401, "Token allaqachon yangilangan")
            raise HTTPException(401, "Token qayta ishlatildi — barcha Exams seanslari bekor qilindi")

        user = await self._find_student_by_id(session.user_id)
        if not user or not user.is_active:
            session.is_revoked = True
            await self.db.commit()
            raise HTTPException(401, "Foydalanuvchi topilmadi yoki hisob faol emas")

        if not await claim_refresh_token(self.db, ExamsAuthSession, session.id, refresh_hash, now):
            outcome = await on_lost_claim(self.db, ExamsAuthSession, session, now)
            if outcome == ReuseOutcome.GRACE:
                raise HTTPException(401, "Token allaqachon yangilangan")
            raise HTTPException(401, "Token qayta ishlatildi — barcha Exams seanslari bekor qilindi")

        new_tokens = await self._create_session_and_tokens(
            user, session.registration_id, request, session_family_id=session.session_family_id
        )
        new_session = (await self.db.execute(
            select(ExamsAuthSession).where(ExamsAuthSession.refresh_token_hash == self._hash(new_tokens.refresh_token))
        )).scalar_one()
        new_session.rotated_from_id = session.id
        await self.db.commit()
        return new_tokens

    async def end_session_if_all_submitted(self, attempt_id: int) -> bool:
        """4 ta skill ham topshirilgach imtihon sessiyasini avtomatik yopadi:
        registration COMPLETED bo'ladi va shu registration'ning barcha Exams
        refresh-sessiyalari bekor qilinadi (Exam ID bilan qayta kirib bo'lmaydi).
        Idempotent. True — shu chaqiruvda yopildi.
        """
        from app.modules.exams.mock.models import (
            MockExamAttempt, MockRegistration as _Reg, MockSkillAttempt,
        )

        rows = (await self.db.execute(
            select(MockSkillAttempt).where(MockSkillAttempt.attempt_id == attempt_id)
        )).scalars().all()
        if len({r.skill for r in rows}) < 4 or any(r.submitted_at is None for r in rows):
            return False
        attempt = await self.db.get(MockExamAttempt, attempt_id)
        registration_id = getattr(attempt, "registration_id", None) if attempt else None
        if not registration_id:
            return False
        reg = await self.db.get(_Reg, registration_id)
        if not reg or reg.status == RegistrationStatus.COMPLETED:
            return False
        reg.status = RegistrationStatus.COMPLETED
        await self.db.execute(
            update(ExamsAuthSession)
            .where(ExamsAuthSession.registration_id == registration_id)
            .values(is_revoked=True)
        )
        await self.db.commit()
        return True

    async def _find_student_by_id(self, user_id: uuid.UUID) -> Optional[User]:
        stmt = (
            select(User)
            .options(selectinload(User.profile), selectinload(User.contacts))
            .where(User.id == user_id)
        )
        return (await self.db.execute(stmt)).scalar_one_or_none()

    async def logout(self, refresh_token: str) -> None:
        refresh_hash = self._hash(refresh_token)
        session = (await self.db.execute(
            select(ExamsAuthSession).where(ExamsAuthSession.refresh_token_hash == refresh_hash)
        )).scalar_one_or_none()
        if session:
            await self.db.execute(
                update(ExamsAuthSession)
                .where(ExamsAuthSession.session_family_id == session.session_family_id)
                .values(is_revoked=True)
            )
        await self.db.commit()

    async def get_me(
        self, user_id: uuid.UUID, registration_id: Optional[int] = None
    ) -> ExamsStudentResponse:
        user = await self._find_student_by_id(user_id)
        if not user:
            raise HTTPException(404, "Foydalanuvchi topilmadi")

        registration = (
            await self.db.get(MockRegistration, registration_id)
            if registration_id is not None
            else None
        )
        if registration:
            return self._student_response(user, registration, with_phone=True)
        return ExamsStudentResponse(
            id=user.id,
            full_name=user.profile.full_name if user.profile else None,
            phone_last4=(self._primary_phone(user) or "")[-4:] or None,
        )

    # ---------------------------------------------------------------
    # ENROLLMENT-SCOPED EXAM LIST
    # ---------------------------------------------------------------

    async def _load_registration(self, registration_id: Optional[int]) -> MockRegistration:
        registration = (
            await self.db.get(MockRegistration, registration_id)
            if registration_id is not None
            else None
        )
        if not registration or registration.status == RegistrationStatus.CANCELLED:
            raise HTTPException(403, "Exams seansi yaroqsiz — qaytadan kiring")
        return registration

    async def require_enrollment(
        self, user_id: uuid.UUID, registration_id: Optional[int], exam_id: str
    ) -> MockRegistration:
        """Token bog'langan ro'yxat AYNAN shu imtihonga tegishlimi?

        Ilgari bu yer faqat "shu foydalanuvchida shu imtihonga yozilish
        yoki xarid bormi?" deb tekshirardi. Endi MK ID seansga tegishli
        bo'lgani uchun tekshiruv qattiqroq: bitta MK ID bilan kirgan
        talaba faqat O'SHA seansdagi imtihonni topshira oladi.
        """
        registration = await self._load_registration(registration_id)
        if registration.user_id != user_id:
            raise HTTPException(403, "Exams seansi yaroqsiz — qaytadan kiring")
        if registration.mock_exam_id != exam_id:
            raise HTTPException(403, "Bu MK ID boshqa imtihon seansiga tegishli")
        if registration.status == RegistrationStatus.CANCELLED:
            raise HTTPException(403, "Bu registration bekor qilingan")
        if registration.status not in {
            RegistrationStatus.REGISTERED,   # exam_access_id login avtomatik CHECKED_IN qiladi,
            RegistrationStatus.CHECKED_IN,   # lekin fallback sifatida REGISTERED ham qabul
            RegistrationStatus.IN_PROGRESS,
            RegistrationStatus.COMPLETED,
        }:
            raise HTTPException(403, "Imtihonni boshlashdan oldin ro'yxatdan o'tish tasdiqlanishi kerak")
        return registration

    async def list_enrolled_exams(
        self, user_id: uuid.UUID, registration_id: Optional[int] = None
    ) -> List[EnrolledExamResponse]:
        """Token bog'langan seansdagi YAGONA imtihonni qaytaradi.

        Ilgari bu metod foydalanuvchining barcha registration/purchase
        yozuvlarini bo'ylab yurib, imtihonlar ro'yxatini yig'ardi. MK ID
        endi seansga tegishli bo'lgani uchun Exams tizimidagi seans ham
        aynan bitta imtihonni anglatadi — ro'yxat shundan iborat.
        """
        registration = await self._load_registration(registration_id)
        if registration.user_id != user_id:
            raise HTTPException(403, "Exams seansi yaroqsiz — qaytadan kiring")

        exam = await self.db.get(MockExam, registration.mock_exam_id)
        if not exam or not exam.is_active:
            return []

        attempt = (
            await self.db.execute(
                select(MockExamAttempt)
                .where(
                    MockExamAttempt.user_id == user_id,
                    MockExamAttempt.mock_exam_id == exam.id,
                    MockExamAttempt.registration_id == registration.id,
                )
                .order_by(MockExamAttempt.started_at.desc())
                .limit(1)
            )
        ).scalar_one_or_none()

        session_info = None
        session = await self.db.get(ExamSession, registration.session_id)
        if session:
            session_info = EnrolledSessionInfo(
                id=session.id,
                exam_date=session.exam_date,
                center_name=session.center_name,
                room_name=session.room_name,
                location_address=session.location_address,
            )

        return [
            EnrolledExamResponse(
                id=exam.id,
                title=exam.title,
                cefr_level=exam.cefr_level,
                duration_minutes=exam.duration_minutes,
                enrollment_source="registration",
                mk_id=registration.student_id,
                session=session_info,
                attempt_id=attempt.id if attempt else None,
                attempt_status=attempt.status.value if attempt else None,
                is_finished=attempt.is_finished if attempt else None,
                reading_id=exam.reading_id,
                listening_id=exam.listening_id,
                writing_id=exam.writing_id,
                speaking_id=exam.speaking_id,
            )
        ]