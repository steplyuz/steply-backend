import secrets
import hashlib
import hmac
import json
import uuid
from datetime import date, datetime, timedelta, timezone
from typing import Optional

from fastapi import HTTPException, Request
from sqlalchemy import select, and_, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload
from sqlalchemy.exc import IntegrityError

from app.core.security import create_access_token, hash_password, verify_password
from app.core.config import settings
from app.core.errors import AppError
from app.modules.admin.system_settings import SystemSettingsService

from app.modules.auth.schemas import (
    Token,
    PhoneAuthResponse,
    RegisterRequest,
    RegisterInitResponse,
    RegisterResponse,
    StatusResponse,
)

from app.modules.auth.models import (
    User,
    UserContact,
    UserProfile,
    UserSession,
    VerificationCode,
    ContactType,
    UserRole,
    OtpPurpose,
    OtpChannel,
    Gender,
)
from app.modules.auth.sms import SmsService
from app.modules.auth.telegram import TelegramService
from app.modules.auth.models import Identity, OtpSendLimit
from app.core.keys import derive_key, fernet_key
from app.core.rate_limit import _get_ip
from app.core.throttle import FailureThrottle
from app.core.refresh_rotation import (
    ReuseOutcome, claim_refresh_token, on_lost_claim, on_reused_token,
)
import functools
from cryptography.fernet import Fernet, InvalidToken


_INVALID_LOGIN = "Telefon yoki parol noto'g'ri"
_LOGIN_LOCKED = "Juda ko'p noto'g'ri urinish. Keyinroq qayta urinib ko'ring"
# (telefon, IP) juftligi: qat'iy himoya — begona IP egasini qulflay olmaydi.
_LOGIN_PAIR = FailureThrottle("login-pair", max_failures=5, window_seconds=15 * 60)
# Telefon bo'yicha umumiy chegara: taqsimlangan brute-force'ga qarshi. Bu egasini
# vaqtincha qulflashi mumkin (DoS xavfi); CAPTCHA (5-bosqich) buni kamaytiradi.
_LOGIN_PHONE = FailureThrottle("login-phone", max_failures=20, window_seconds=15 * 60)
_REGISTER_SENT_MESSAGE = "SMS kodi yuborildi. Telegram bot orqali kodni olish mumkin."


@functools.lru_cache(maxsize=1)
def _dummy_hash() -> str:
    """Mavjud bo'lmagan akkaunt uchun ham bir xil vaqt sarflash uchun (enumeratsiyaga qarshi)."""
    return hash_password(secrets.token_urlsafe(16))


def _dummy_verify(password: str) -> None:
    verify_password(password, _dummy_hash())


class AuthService:
    def __init__(self, db: AsyncSession):
        self.db = db

    # =====================================================
    # UTILS
    # =====================================================
    OTP_EXPIRE_MINUTES = 5
    # Qayta yuborish faqat shu vaqt o'tgach mumkin
    OTP_RESEND_COOLDOWN_SECONDS = 5 * 60
    # Shu miqdorda yuborilgach, bloklanadi
    OTP_MAX_SENDS = 3
    # Bloklangandan keyin kutish vaqti
    OTP_SEND_BLOCK_MINUTES = 60
    # Kodni tasdiqlashda noto'g'ri urinishlar limiti
    OTP_MAX_VERIFY_ATTEMPTS = 5
    # Telegram orqali /start bosilishini kutish vaqti (link amal qilish muddati)
    BOT_LINK_EXPIRE_MINUTES = 10
    # Refresh tokenni qayta ishlatish (parallel tab) uchun imtiyozli oyna: shu vaqt ichida
    # eski token rad etiladi, lekin butun sessiya oilasi o'chirilmaydi.

    def _hash(self, value: str) -> str:
        return hashlib.sha256(value.encode()).hexdigest()

    def _otp_cipher(self) -> Fernet:
        return Fernet(fernet_key("otp-encrypt"))

    def _code_hash(self, code: str) -> str:
        """OTP kodining kalitli hash'i. Kalitsiz sha256 6 xonali kodni bir zumda ochib beradi."""
        return hmac.new(derive_key("otp-hmac"), code.encode(), hashlib.sha256).hexdigest()

    def _encrypt_otp(self, code: str) -> str:
        return self._otp_cipher().encrypt(code.encode()).decode()

    def _decrypt_otp(self, value: str) -> str:
        try:
            return self._otp_cipher().decrypt(value.encode()).decode()
        except (InvalidToken, ValueError, TypeError) as exc:
            raise HTTPException(400, "Kod yaroqsiz yoki eskirgan") from exc

    @staticmethod
    def _aware(dt: datetime) -> datetime:
        """SQLite kabi ba'zi bazalar timezone ma'lumotini saqlamaydi — UTC deb hisoblaymiz."""
        if dt.tzinfo is None:
            return dt.replace(tzinfo=timezone.utc)
        return dt

    async def _get_user_full(self, user_id: uuid.UUID) -> User:
        stmt = (
            select(User)
            .options(
                selectinload(User.contacts),
                selectinload(User.profile),
            )
            .where(User.id == user_id)
        )
        result = await self.db.execute(stmt)
        user = result.scalar_one_or_none()
        if not user:
            raise HTTPException(status_code=404, detail="User not found")
        return user

    # =====================================================
    # SMS RO'YXATDAN O'TISH SOZLAMASI (admin boshqaradi, DB'da saqlanadi)
    # =====================================================

    async def _require_sms_registration_enabled(self) -> None:
        """SMS orqali RO'YXATDAN O'TISH o'chiq bo'lsa 403 qaytaradi.

        Faqat SMS-registratsiyaga tegishli: parol bilan login, Telegram
        login/registratsiya, parolni tiklash (SMS) va natija-SMS bunga
        bog'liq EMAS. Qiymat har safar DB'dan o'qiladi (kesh yo'q).
        """
        if not await SystemSettingsService.sms_registration_enabled(self.db):
            raise AppError(
                status_code=403,
                code="SMS_REGISTRATION_DISABLED",
                message="SMS registration is currently disabled.",
            )

    # =====================================================
    # YAGONA AKKAUNT YARATISH SERVISI (SMS va Telegram ikkalasi shuni ishlatadi)
    # =====================================================

    async def _create_user_account(
        self,
        *,
        full_name: Optional[str],
        hashed_password: Optional[str] = None,
        birth_date: Optional[date] = None,
        gender: Optional[Gender] = None,
        phone: Optional[str] = None,
        telegram_user_id: Optional[int] = None,
    ) -> User:
        """Oddiy STUDENT akkaunt yaratadi (User + UserProfile [+ telefon kontakti]).

        IntegrityError'ni (masalan telefon yoki telegram_user_id UNIQUE)
        chaqiruvchi hal qiladi.
        """
        # MK ID BU YERDA YARATILMAYDI. U imtihon seansiga yozilganda
        # (mock_test/registration_service.register_for_session) beriladi —
        # qarang: mock_test/mk_id.py. Signup paytidagi "umrbod MK ID"
        # modeli olib tashlandi.
        user = User(
            # id qo'lda berilmaydi — User.id UUID4 bo'lib, model darajasida
            # (mapped_column default=uuid.uuid4) avtomatik generatsiya qilinadi.
            # Oldingi versiya 8 xonali tasodifiy int ID ishlatgan edi (taxmin
            # qilish/enumerate xavfi — IDOR), shuning uchun UUID4ga o'tkazildi.
            global_role=UserRole.STUDENT,
            is_active=True,
            hashed_password=hashed_password,
            telegram_user_id=telegram_user_id,
        )
        self.db.add(user)
        await self.db.flush()

        self.db.add(
            UserProfile(
                user_id=user.id,
                full_name=full_name,
                birth_date=birth_date,
                gender=gender,
            )
        )

        if phone:
            self.db.add(
                UserContact(
                    user_id=user.id,
                    contact_type=ContactType.PHONE,
                    value=phone,
                    is_verified=True,
                    is_primary=True,
                )
            )

        await self.db.flush()
        return user

    # =====================================================
    # REGISTER — 1-QADAM: ma'lumotlarni qabul qilish + kod yuborish
    # =====================================================

    async def register(self, data: RegisterRequest, request: Request) -> RegisterInitResponse:
        from datetime import date as date_cls

        await self._require_sms_registration_enabled()

        # Validatsiya mavjudlikka bog'liq bo'lmasligi kerak: aks holda xato javobi
        # ro'yxatdan o'tgan raqamni oshkor qiladi.
        try:
            date_cls.fromisoformat(data.birth_date)
        except ValueError as exc:
            raise HTTPException(400, "birth_date formati noto'g'ri (YYYY-MM-DD)") from exc

        existing = (
            await self.db.execute(
                select(UserContact).where(
                    UserContact.contact_type == ContactType.PHONE,
                    UserContact.value == data.phone,
                )
            )
        ).scalar_one_or_none()
        if existing:
            # Mavjud raqam: SMS yuborilmaydi, lekin javob yangi raqamdagidek ko'rinadi
            # (xuddi shu tuzilma, xuddi shu xabar, o'xshash vaqt). Havola esa hech
            # qanday ro'yxatdan o'tish jarayoniga bog'lanmaydi.
            hash_password(data.password)
            return RegisterInitResponse(
                status="pending_verification",
                message=_REGISTER_SENT_MESSAGE,
                expires_in_seconds=self.OTP_EXPIRE_MINUTES * 60,
                bot_link=TelegramService.build_start_link(secrets.token_urlsafe(32)),
            )

        payload = {
            "hashed_password": hash_password(data.password),
            "full_name": data.full_name,
            "birth_date": data.birth_date,
            "gender": data.gender,
        }
        result = await self.send_otp(
            phone=data.phone,
            purpose=OtpPurpose.REGISTER,
            channel=OtpChannel.SMS,
            payload=payload,
            source="web",
            client_ip=_get_ip(request),
        )
        return RegisterInitResponse(
            status="pending_verification",
            message=_REGISTER_SENT_MESSAGE,
            expires_in_seconds=self.OTP_EXPIRE_MINUTES * 60,
            bot_link=result.get("bot_link"),
        )

    # =====================================================
    # REGISTER — 2-QADAM: kodni tasdiqlab, userni yaratish
    # =====================================================

    async def register_verify(self, phone: str, code: str, request: Request) -> RegisterResponse:
        await self._require_sms_registration_enabled()
        pending = (await self.db.execute(
            select(VerificationCode)
            .where(
                VerificationCode.target == phone,
                VerificationCode.purpose == OtpPurpose.REGISTER,
                VerificationCode.is_used.is_(False),
                VerificationCode.expires_at > datetime.now(timezone.utc),
            )
            .order_by(VerificationCode.id.desc())
        )).scalars().first()
        if not pending or not pending.payload:
            raise HTTPException(400, "Kod noto'g'ri yoki eskirgan")

        payload = json.loads(pending.payload)
        tg_id = pending.telegram_user_id
        if not tg_id or not payload.get("telegram_phone_verified"):
            raise HTTPException(400, "Telegram raqamini tasdiqlashni yakunlang")

        record = await self._consume_otp(phone, code, purpose=OtpPurpose.REGISTER)
        if not record or not record.payload:
            raise HTTPException(400, "Kod noto'g'ri yoki eskirgan")
        payload = json.loads(record.payload)

        existing = (
            await self.db.execute(
                select(UserContact).where(
                    UserContact.contact_type == ContactType.PHONE,
                    UserContact.value == phone,
                )
            )
        ).scalar_one_or_none()
        if existing:
            raise HTTPException(400, "Tasdiqlash jarayoni yaroqsiz yoki eskirgan")

        existing_identity = (
            await self.db.execute(
                select(Identity).where(
                    Identity.provider == "telegram",
                    Identity.provider_user_id == str(tg_id),
                )
            )
        ).scalar_one_or_none()
        if existing_identity:
            raise HTTPException(409, "Bu Telegram akkaunti allaqachon ro'yxatdan o'tgan")

        birth_date = date.fromisoformat(payload["birth_date"])
        gender = Gender(payload["gender"])
        try:
            user = await self._create_user_account(
                full_name=payload["full_name"],
                hashed_password=payload["hashed_password"],
                birth_date=birth_date,
                gender=gender,
                phone=phone,
                telegram_user_id=tg_id,
            )
            profile = (await self.db.execute(
                select(UserProfile).where(UserProfile.user_id == user.id)
            )).scalar_one()
            tg_username = (payload.get("telegram_username") or "").lstrip("@")[:50] or None
            if tg_username:
                owner = (await self.db.execute(
                    select(UserProfile.user_id).where(UserProfile.username == tg_username)
                )).scalar_one_or_none()
                if owner is None:
                    profile.username = tg_username
            profile.avatar_url = payload.get("telegram_photo_url") or None
            user.last_login_at = datetime.now(timezone.utc)
            self.db.add(Identity(
                user_id=user.id,
                provider="telegram",
                provider_user_id=str(tg_id),
            ))
            await self.db.flush()
        except IntegrityError as exc:
            raise HTTPException(409, "Ro'yxatdan o'tishda ziddiyat yuz berdi, qayta urinib ko'ring") from exc

        full_user = await self._get_user_full(user.id)
        tokens = await self._create_session_and_tokens(full_user, request)
        return RegisterResponse(status="success", token=tokens)

    # =====================================================
    # PHONE & OTP
    # =====================================================

    def _verify_hash(self, plain: str, hashed: str) -> bool:
        return hmac.compare_digest(self._code_hash(plain), hashed)

    async def _consume_send_quota(
        self, phone: str, purpose: OtpPurpose, client_ip: str, now: datetime
    ) -> None:
        ip_hash = self._hash(client_ip or "unknown")
        limit = (await self.db.execute(
            select(OtpSendLimit).where(
                OtpSendLimit.target == phone,
                OtpSendLimit.purpose == purpose.value,
                OtpSendLimit.ip_hash == ip_hash,
            )
        )).scalar_one_or_none()
        if limit is None:
            limit = OtpSendLimit(
                target=phone, purpose=purpose.value, ip_hash=ip_hash,
                send_count=0, window_started_at=now,
            )
            self.db.add(limit)
            await self.db.flush()
        elif now - self._aware(limit.window_started_at) >= timedelta(minutes=self.OTP_SEND_BLOCK_MINUTES):
            limit.send_count = 0
            limit.window_started_at = now
        if limit.send_count >= self.OTP_MAX_SENDS:
            raise HTTPException(429, "Juda ko'p urinish qilindi. Keyinroq qayta urinib ko'ring")
        limit.send_count += 1
        await self.db.flush()

    async def send_otp(
        self,
        phone: str,
        purpose: OtpPurpose = OtpPurpose.RESET_PASSWORD,
        channel: OtpChannel = OtpChannel.SMS,
        payload: Optional[dict] = None,
        source: str = "web",
        client_ip: str = "unknown",
    ) -> dict:
        if purpose == OtpPurpose.REGISTER:
            await self._require_sms_registration_enabled()

        now = datetime.now(timezone.utc)
        stmt = (
            select(VerificationCode)
            .where(VerificationCode.target == phone, VerificationCode.purpose == purpose)
            .order_by(VerificationCode.id.desc())
            .with_for_update()
        )
        record = (await self.db.execute(stmt)).scalars().first()
        active = record is not None and not record.is_used
        if active:
            elapsed = (now - self._aware(record.last_sent_at)).total_seconds()
            if elapsed < self.OTP_RESEND_COOLDOWN_SECONDS:
                raise HTTPException(429, "Iltimos, qayta yuborishdan oldin kuting")
        # Limit telefon+IP juftligi bo'yicha: begona IP egasini bloklay olmaydi.
        await self._consume_send_quota(phone, purpose, client_ip, now)

        code = str(secrets.randbelow(900000) + 100000)
        raw_bot_token = secrets.token_urlsafe(32) if purpose == OtpPurpose.REGISTER else None
        bot_token_hash = self._hash(raw_bot_token) if raw_bot_token else None

        if active:
            record.code_hash = self._code_hash(code)
            record.code_encrypted = self._encrypt_otp(code) if purpose == OtpPurpose.REGISTER else None
            record.expires_at = now + timedelta(minutes=self.OTP_EXPIRE_MINUTES)
            record.failed_attempts = 0
            record.send_count += 1
            record.last_sent_at = now
            record.channel = OtpChannel.SMS
            record.bot_token = bot_token_hash
            record.telegram_user_id = None
            record.telegram_chat_id = None
            record.payload = json.dumps(payload) if payload is not None else record.payload
        else:
            record = VerificationCode(
                target=phone,
                purpose=purpose,
                channel=OtpChannel.SMS,
                code_hash=self._code_hash(code),
                code_encrypted=self._encrypt_otp(code) if purpose == OtpPurpose.REGISTER else None,
                expires_at=now + timedelta(minutes=self.OTP_EXPIRE_MINUTES),
                failed_attempts=0,
                send_count=1,
                last_sent_at=now,
                bot_token=bot_token_hash,
                payload=json.dumps(payload) if payload is not None else None,
                is_used=False,
            )
            self.db.add(record)
        await self.db.flush()

        if settings.is_production:
            try:
                await SmsService.send_otp(phone, code)
            except Exception as exc:
                raise HTTPException(502, "SMS yuborishda xatolik yuz berdi. Birozdan keyin qayta urinib ko'ring") from exc

        result = {"status": "sent"}
        if raw_bot_token:
            result["bot_link"] = TelegramService.build_start_link(raw_bot_token)
        return result

    async def telegram_register_start(
        self, *, start_token: str, telegram_user_id: int, chat_id: str,
        username: str | None, first_name: str | None, last_name: str | None, photo_url: str | None
    ) -> dict:
        record = (await self.db.execute(
            select(VerificationCode).where(
                VerificationCode.bot_token == self._hash(start_token),
                VerificationCode.purpose == OtpPurpose.REGISTER,
                VerificationCode.is_used.is_(False),
            )
        )).scalar_one_or_none()
        if not record or self._aware(record.expires_at) <= datetime.now(timezone.utc):
            raise HTTPException(404, "Havola yaroqsiz yoki eskirgan")

        payload = json.loads(record.payload or "{}")
        existing_tg_id = payload.get("telegram_user_id")
        if payload.get("telegram_phone_verified") and existing_tg_id != telegram_user_id:
            raise HTTPException(409, "Bu ro'yxatdan o'tish jarayoni boshqa Telegram akkauntiga bog'langan")
        payload.update({
            "telegram_user_id": telegram_user_id,
            "telegram_chat_id": chat_id,
            "telegram_username": (username or "").lstrip("@")[:50] or None,
            "telegram_first_name": (first_name or "")[:120] or None,
            "telegram_last_name": (last_name or "")[:120] or None,
            "telegram_photo_url": (photo_url or "")[:500] or None,
            "telegram_phone_verified": bool(payload.get("telegram_phone_verified")) and existing_tg_id == telegram_user_id,
        })
        record.telegram_user_id = telegram_user_id
        record.telegram_chat_id = chat_id
        record.payload = json.dumps(payload)
        await self.db.flush()
        return {"status": "ok", "requires_contact": True}

    async def _active_register_record(
        self, telegram_user_id: int, start_token: Optional[str]
    ) -> Optional[VerificationCode]:
        """Botga tegishli faol REGISTER yozuvini topadi.

        Bot holatsiz ishlaydi: `start_token` berilsa u tekshiriladi, berilmasa
        /start paytida shu Telegram user_id'ga bog'langan eng so'nggi faol yozuv olinadi.
        Har holatda yozuv shu telegram_user_id'ga tegishli bo'lishi shart.
        """
        stmt = select(VerificationCode).where(
            VerificationCode.purpose == OtpPurpose.REGISTER,
            VerificationCode.is_used.is_(False),
            VerificationCode.telegram_user_id == telegram_user_id,
        )
        if start_token:
            stmt = stmt.where(VerificationCode.bot_token == self._hash(start_token))
        record = (await self.db.execute(stmt.order_by(VerificationCode.id.desc()).limit(1))).scalars().first()
        if not record or self._aware(record.expires_at) <= datetime.now(timezone.utc):
            return None
        return record

    async def telegram_register_verify_contact(
        self, *, start_token: Optional[str], telegram_user_id: int, chat_id: str, phone: str
    ) -> dict:
        record = await self._active_register_record(telegram_user_id, start_token)
        if not record:
            raise HTTPException(404, "Havola yaroqsiz yoki eskirgan")
        if record.telegram_chat_id != chat_id:
            raise HTTPException(403, "Telegram tasdiqlash mos kelmadi")
        if phone != record.target:
            raise HTTPException(403, "Telegram raqami ro'yxatdan o'tish raqamiga mos emas")

        payload = json.loads(record.payload or "{}")
        payload["telegram_phone_verified"] = True
        record.payload = json.dumps(payload)
        await self.db.flush()
        return {"status": "verified", "verified": True}

    async def telegram_register_get_code(self, *, start_token: Optional[str], telegram_user_id: int) -> str:
        record = await self._active_register_record(telegram_user_id, start_token)
        if not record:
            raise HTTPException(404, "Kod yaroqsiz yoki eskirgan")
        payload = json.loads(record.payload or "{}")
        if not payload.get("telegram_phone_verified"):
            raise HTTPException(403, "Avval telefon raqamingizni tasdiqlang")
        if not record.code_encrypted:
            raise HTTPException(410, "Kod mavjud emas")
        return self._decrypt_otp(record.code_encrypted)

    # =====================================================
    # VERIFY OTP
    # =====================================================

    async def _consume_otp(
        self, phone: str, code: str, purpose: OtpPurpose
    ) -> Optional[VerificationCode]:
        now = datetime.now(timezone.utc)

        stmt = (
            select(VerificationCode)
            .where(
                VerificationCode.target == phone,
                VerificationCode.purpose == purpose,
                VerificationCode.is_used == False,  # noqa: E712
                VerificationCode.expires_at > now,
            )
            .order_by(VerificationCode.id.desc())
            .limit(1)
        )
        record = (await self.db.execute(stmt)).scalars().first()
        if not record or not record.code_hash:
            return None
        if record.failed_attempts >= self.OTP_MAX_VERIFY_ATTEMPTS:
            return None

        if not self._verify_hash(code, record.code_hash):
            # SQLite does not provide row-level SELECT ... FOR UPDATE locking.
            # Increment attempts with an atomic conditional UPDATE instead.
            await self.db.execute(
                update(VerificationCode)
                .where(
                    VerificationCode.id == record.id,
                    VerificationCode.is_used == False,  # noqa: E712
                    VerificationCode.expires_at > now,
                    VerificationCode.failed_attempts < self.OTP_MAX_VERIFY_ATTEMPTS,
                )
                .values(failed_attempts=VerificationCode.failed_attempts + 1)
                .execution_options(synchronize_session=False)
            )
            await self.db.commit()
            return None

        # Verify the hash first, then consume atomically. Two concurrent
        # correct-code requests cannot both observe a successful consume.
        consumed = await self.db.execute(
            update(VerificationCode)
            .where(
                VerificationCode.id == record.id,
                VerificationCode.is_used == False,  # noqa: E712
                VerificationCode.expires_at > now,
                VerificationCode.failed_attempts < self.OTP_MAX_VERIFY_ATTEMPTS,
            )
            .values(is_used=True)
            .execution_options(synchronize_session=False)
        )
        if consumed.rowcount != 1:
            return None
        await self.db.flush()
        return record

    async def _verify_otp(self, phone: str, code: str) -> bool:
        """Eski (parolni tiklash uchun ishlatiladigan) tasdiqlash — payload kerak emas."""
        record = await self._consume_otp(phone, code, purpose=OtpPurpose.RESET_PASSWORD)
        return record is not None

    # =====================================================
    # AUTHENTICATE
    # =====================================================

    async def authenticate_by_phone(
        self, phone: str, password: str, request: Request
    ) -> PhoneAuthResponse:
        pair_key = f"{self._hash(phone)}|{_get_ip(request)}"
        phone_key = self._hash(phone)
        if _LOGIN_PAIR.is_blocked(pair_key) or _LOGIN_PHONE.is_blocked(phone_key):
            # Mavjudlikdan qat'i nazar bir xil: hisob bor/yo'qligi oshkor bo'lmaydi.
            raise HTTPException(429, _LOGIN_LOCKED)
        try:
            result = await self._authenticate_by_phone(phone, password, request)
        except HTTPException as exc:
            if exc.status_code == 400 and exc.detail == _INVALID_LOGIN:
                _LOGIN_PAIR.record_failure(pair_key)
                _LOGIN_PHONE.record_failure(phone_key)
            raise
        _LOGIN_PAIR.reset(pair_key)
        return result

    async def _authenticate_by_phone(
        self, phone: str, password: str, request: Request
    ) -> PhoneAuthResponse:

        stmt = select(UserContact).where(
            and_(
                UserContact.contact_type == ContactType.PHONE,
                UserContact.value == phone,
            )
        )

        contact = (await self.db.execute(stmt)).scalar_one_or_none()

        # Enumeratsiya yo'q: raqam yo'q, parol yo'q va parol noto'g'ri holatlari
        # bir xil xabar va bir xil status kodi bilan javob beradi.
        if not contact:
            _dummy_verify(password)
            raise HTTPException(status_code=400, detail=_INVALID_LOGIN)

        user = await self._get_user_full(contact.user_id)

        if not user.hashed_password:
            _dummy_verify(password)
            raise HTTPException(status_code=400, detail=_INVALID_LOGIN)

        if not verify_password(password, user.hashed_password):
            raise HTTPException(status_code=400, detail=_INVALID_LOGIN)

        if not user.is_active:
            raise HTTPException(status_code=403, detail="Hisob faol emas")

        tokens = await self._create_session_and_tokens(user, request)

        return PhoneAuthResponse(
            status="success",
            token=tokens,
            force_password_change=user.force_password_change,
            message=("Parolni almashtirish talab etiladi" if user.force_password_change else None),
        )

    # =====================================================
    # FIRST-LOGIN / TEMPORARY PASSWORD CHANGE
    # =====================================================

    async def change_password(self, user_id: uuid.UUID, current_password: str, new_password: str) -> None:
        user = await self._get_user_full(user_id)
        if not user.hashed_password or not verify_password(current_password, user.hashed_password):
            raise HTTPException(400, "Joriy parol noto'g'ri")
        if current_password == new_password:
            raise HTTPException(400, "Yangi parol joriy paroldan farq qilishi kerak")
        user.hashed_password = hash_password(new_password)
        user.force_password_change = False
        await self.db.execute(
            update(UserSession)
            .where(UserSession.user_id == user.id, UserSession.is_revoked.is_(False))
            .values(is_revoked=True)
        )
        await self.db.flush()

    # =====================================================
    # PASSWORD RESET
    # =====================================================

    async def forgot_password(self, phone: str, request: Optional[Request] = None) -> dict:
        # XAVFSIZLIK: ro'yxatdan o'tmagan raqamga 404 (mavjudiga 200) qaytarish
        # — bu orqali istalgan tashqi kishi qaysi telefon raqamlari
        # ro'yxatdan o'tganini so'rov yuborib aniqlab olishi mumkin edi
        # (account enumeration). Endi ikkala holatda ham javob bir xil
        # ko'rinadi; kod faqat raqam haqiqatan mavjud bo'lsagina yuboriladi.
        stmt = select(UserContact).where(
            and_(
                UserContact.contact_type == ContactType.PHONE,
                UserContact.value == phone,
            )
        )
        contact = (await self.db.execute(stmt)).scalar_one_or_none()
        client_ip = _get_ip(request) if request is not None else "unknown"
        if not contact:
            # Javob va IP limiti ro'yxatdan o'tgan raqamdagidek: sxemani tenglashtiramiz.
            await self._consume_send_quota(phone, OtpPurpose.RESET_PASSWORD, client_ip, datetime.now(timezone.utc))
            await self.db.commit()
            return {"status": "sent"}

        return await self.send_otp(
            phone, purpose=OtpPurpose.RESET_PASSWORD, client_ip=client_ip,
        )

    async def reset_password(self, phone: str, code: str, new_password: str) -> StatusResponse:
        if not await self._verify_otp(phone, code):
            raise HTTPException(400, "Kod noto'g'ri yoki eskirgan")

        stmt = select(UserContact).where(
            and_(
                UserContact.contact_type == ContactType.PHONE,
                UserContact.value == phone,
            )
        )
        contact = (await self.db.execute(stmt)).scalar_one_or_none()
        if not contact:
            raise HTTPException(404, "Bu telefon raqam ro'yxatdan o'tmagan")

        user = await self._get_user_full(contact.user_id)
        user.hashed_password = hash_password(new_password)
        user.force_password_change = False
        # Parol almashgach eski refresh tokenlar (o'g'irlangan bo'lishi mumkin) ishlamasligi kerak.
        await self.db.execute(
            update(UserSession)
            .where(UserSession.user_id == user.id, UserSession.is_revoked.is_(False))
            .values(is_revoked=True)
        )
        await self.db.flush()

        return StatusResponse(status="success", message="Parol muvaffaqiyatli o'zgartirildi")

    # =====================================================
    # LOGOUT
    # =====================================================

    async def logout(self, refresh_token: str) -> None:
        """Revoke the complete refresh-token family represented by this token."""
        refresh_hash = self._hash(refresh_token)
        session = (await self.db.execute(
            select(UserSession).where(UserSession.refresh_token_hash == refresh_hash)
        )).scalar_one_or_none()
        if session:
            await self.db.execute(
                update(UserSession)
                .where(UserSession.session_family_id == session.session_family_id)
                .values(is_revoked=True)
            )
        await self.db.flush()

    # =====================================================
    # REFRESH
    # =====================================================

    async def refresh(self, refresh_token: str, request: Request) -> Token:
        now = datetime.now(timezone.utc)
        refresh_hash = self._hash(refresh_token)

        session = (await self.db.execute(
            select(UserSession).where(UserSession.refresh_token_hash == refresh_hash)
        )).scalar_one_or_none()
        if not session:
            raise HTTPException(401, "Invalid refresh token")

        if self._aware(session.expires_at) < now:
            session.is_revoked = True
            await self.db.commit()
            raise HTTPException(401, "Token muddati tugagan")

        if session.is_revoked:
            outcome = await on_reused_token(self.db, UserSession, session, now)
            if outcome == ReuseOutcome.GRACE:
                raise HTTPException(401, "Token allaqachon yangilangan")
            raise HTTPException(401, "Token reuse detected")

        user = await self._get_user_full(session.user_id)
        if not user.is_active:
            session.is_revoked = True
            await self.db.commit()
            raise HTTPException(403, "Hisob faol emas")

        if not await claim_refresh_token(self.db, UserSession, session.id, refresh_hash, now):
            outcome = await on_lost_claim(self.db, UserSession, session, now)
            if outcome == ReuseOutcome.GRACE:
                raise HTTPException(401, "Token allaqachon yangilangan")
            raise HTTPException(401, "Token reuse detected")

        new_token = await self._create_session_and_tokens(
            user=user,
            request=request,
            session_family_id=session.session_family_id,
        )
        # Record the rotation lineage for audit/debugging.
        new_session = (await self.db.execute(
            select(UserSession).where(UserSession.refresh_token_hash == self._hash(new_token.refresh_token))
        )).scalar_one()
        new_session.rotated_from_id = session.id
        await self.db.commit()
        return new_token

    async def _revoke_family(self, family_id: uuid.UUID):
        await self.db.execute(
            update(UserSession)
            .where(UserSession.session_family_id == family_id)
            .values(is_revoked=True)
        )
        await self.db.flush()

    # =====================================================
    # INTERNAL: Session + Token yaratish
    # =====================================================

    async def _create_session_and_tokens(
        self,
        user: User,
        request: Request,
        session_family_id: Optional[uuid.UUID] = None,
    ) -> Token:
        refresh_token = secrets.token_urlsafe(64)
        refresh_hash = self._hash(refresh_token)

        session = UserSession(
            user_id=user.id,
            refresh_token_hash=refresh_hash,
            session_family_id=session_family_id or uuid.uuid4(),
            user_agent=request.headers.get("user-agent"),
            ip_address=request.client.host if request.client else None,
            expires_at=datetime.now(timezone.utc)
            + timedelta(days=settings.REFRESH_TOKEN_DAYS),
        )

        self.db.add(session)
        await self.db.flush()

        # Access JWT is bound to the DB session. Logout/password reset/rotation
        # can therefore invalidate an access token before JWT expiry.
        access_token = create_access_token(
            user_id=user.id,
            extra_data={"role": user.global_role.value, "sid": str(session.id)},
        )

        return Token(access_token=access_token, refresh_token=refresh_token)
