import re
import asyncio
import httpx
from datetime import datetime, timedelta, timezone
from app.core.config import settings


class SmsService:
    LOGIN_EMAIL = settings.ESKIZ_EMAIL
    LOGIN_PASSWORD = settings.ESKIZ_PASSWORD

    LOGIN_URL = "https://notify.eskiz.uz/api/auth/login"
    SMS_URL = "https://notify.eskiz.uz/api/message/sms/send"

    _token: str | None = None
    _token_expires_at: datetime | None = None
    _lock = asyncio.Lock()

    @classmethod
    async def _get_token(cls) -> str:
        """Eskiz tokenini async tarzda oladi. Hali muddati o'tmagan bo'lsa qaytaradi."""
        async with cls._lock:
            now = datetime.now(timezone.utc)
            if cls._token and cls._token_expires_at and cls._token_expires_at > now:
                return cls._token

            async with httpx.AsyncClient(timeout=10) as client:
                response = await client.post(
                    cls.LOGIN_URL,
                    data={"email": cls.LOGIN_EMAIL, "password": cls.LOGIN_PASSWORD},
                    headers={"Content-Type": "application/x-www-form-urlencoded"},
                )
                response.raise_for_status()

            data = response.json()
            token = data.get("data", {}).get("token")

            if not token:
                raise RuntimeError("Eskiz tokenni qaytarmadi")

            cls._token = token
            # Token 24 soat amal qiladi, 23 soatdan keyin yangilaymiz
            cls._token_expires_at = datetime.now(timezone.utc) + timedelta(hours=23)
            return cls._token

    @classmethod
    def _normalize_phone(cls, phone: str) -> str:
        phone = re.sub(r"\D", "", phone)
        if not phone.startswith("998") or len(phone) != 12:
            raise ValueError("Telefon formati noto'g'ri. Masalan: +998901234567")
        return phone

    @classmethod
    def _site_url(cls) -> str:
        return settings.PUBLIC_BASE_URL or f"https://{settings.AUTH_DOMAIN}"

    @classmethod
    async def send_otp(cls, phone: str, otp: str) -> None:
        """OTP kodni async SMS orqali yuboradi."""
        phone = cls._normalize_phone(phone)
        token = await cls._get_token()
        message = f"{cls._site_url()} ro'yxatdan o'tish uchun tasdiqlash kodingiz: {otp}"
        async with httpx.AsyncClient(timeout=10) as client:
            response = await client.post(
                cls.SMS_URL,
                json={"mobile_phone": phone, "message": message, "from": settings.RESULT_SMS_SENDER},
                headers={"Authorization": f"Bearer {token}"},
            )
        if response.status_code != 200:
            raise RuntimeError(f"SMS xato ({response.status_code})")

    @classmethod
    async def send_result_ready(cls, phone: str) -> None:
        """Natija tayyor SMS ni async tarzda yuboradi."""
        phone = cls._normalize_phone(phone)
        token = await cls._get_token()
        site = cls._site_url()
        async with httpx.AsyncClient(timeout=10) as client:
            response = await client.post(
                cls.SMS_URL,
                json={
                    "mobile_phone": phone,
                    "message": f"Mock imtihon natijangiz tayyor. {site} saytida natijangizni ko'rishingiz mumkin.",
                    "from": settings.RESULT_SMS_SENDER,
                },
                headers={"Authorization": f"Bearer {token}"},
            )
        if response.status_code != 200:
            raise RuntimeError(f"SMS xato ({response.status_code})")
