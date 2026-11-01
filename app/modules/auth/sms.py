
import asyncio
import logging
import re
from datetime import datetime, timedelta, timezone

import httpx

from app.core.config import settings

logger = logging.getLogger(__name__)


class SmsService:
    LOGIN_URL = "https://notify.eskiz.uz/api/auth/login"
    SMS_URL = "https://notify.eskiz.uz/api/message/sms/send"

    _token: str | None = None
    _token_expires_at: datetime | None = None
    _lock = asyncio.Lock()

    @classmethod
    async def _get_token(cls) -> str:
        """Eskiz API tokenini oladi va vaqtincha keshlaydi."""
        async with cls._lock:
            now = datetime.now(timezone.utc)

            if (
                cls._token
                and cls._token_expires_at
                and cls._token_expires_at > now
            ):
                return cls._token

            email = settings.ESKIZ_EMAIL
            password = settings.ESKIZ_PASSWORD

            if not email or not password:
                raise RuntimeError(
                    "ESKIZ_EMAIL yoki ESKIZ_PASSWORD .env faylida sozlanmagan"
                )

            try:
                async with httpx.AsyncClient(timeout=15) as client:
                    response = await client.post(
                        cls.LOGIN_URL,
                        data={
                            "email": email,
                            "password": password,
                        },
                        headers={
                            "Content-Type": "application/x-www-form-urlencoded"
                        },
                    )
            except httpx.TimeoutException as exc:
                logger.exception("Eskiz login so'rovi vaqti tugadi")
                raise RuntimeError(
                    "Eskiz login so'rovida timeout yuz berdi"
                ) from exc
            except httpx.RequestError as exc:
                logger.exception("Eskiz login serveriga ulanishda xatolik")
                raise RuntimeError(
                    "Eskiz serveriga ulanish amalga oshmadi"
                ) from exc

            if response.status_code != 200:
                # Javobda maxfiy ma'lumot bo'lishi mumkin; parol/tokenni log qilmang.
                logger.error(
                    "Eskiz login muvaffaqiyatsiz. HTTP status: %s",
                    response.status_code,
                )
                raise RuntimeError(
                    f"Eskiz login xatosi: HTTP {response.status_code}"
                )

            try:
                data = response.json()
            except ValueError as exc:
                logger.error("Eskiz login JSON formatida javob bermadi")
                raise RuntimeError(
                    "Eskiz login javobi JSON formatida emas"
                ) from exc

            token = (data.get("data") or {}).get("token")

            if not token:
                logger.error(
                    "Eskiz login javobida token yo'q. Response keys: %s",
                    list(data.keys()) if isinstance(data, dict) else "not a dict",
                )
                raise RuntimeError(
                    "Eskiz javobida token topilmadi; hisob va API javobini tekshiring"
                )

            cls._token = token
            cls._token_expires_at = datetime.now(timezone.utc) + timedelta(
                hours=23
            )

            logger.info("Eskiz API tokeni muvaffaqiyatli olindi")
            return cls._token

    @classmethod
    def _normalize_phone(cls, phone: str) -> str:
        """Telefon raqamini Eskiz uchun 998XXXXXXXXX formatiga keltiradi."""
        normalized = re.sub(r"\D", "", phone or "")

        if len(normalized) == 9:
            normalized = "998" + normalized

        if not normalized.startswith("998") or len(normalized) != 12:
            raise ValueError(
                "Telefon formati noto'g'ri. Masalan: +998901234567"
            )

        return normalized

    @classmethod
    async def _post_sms(
        cls,
        phone: str,
        message: str,
    ) -> None:
        """Eskiz API orqali SMS yuboradi."""
        token = await cls._get_token()

        sender = settings.RESULT_SMS_SENDER
        if not sender:
            raise RuntimeError(
                "RESULT_SMS_SENDER .env faylida sozlanmagan"
            )

        try:
            async with httpx.AsyncClient(timeout=20) as client:
                response = await client.post(
                    cls.SMS_URL,
                    data={
                        "mobile_phone": phone,
                        "message": message,
                        "from": sender,
                    },
                    headers={
                        "Authorization": f"Bearer {token}",
                    },
                )
        except httpx.TimeoutException as exc:
            logger.exception("Eskiz SMS so'rovi vaqti tugadi")
            raise RuntimeError(
                "Eskiz SMS so'rovida timeout yuz berdi"
            ) from exc
        except httpx.RequestError as exc:
            logger.exception("Eskiz SMS serveriga ulanishda xatolik")
            raise RuntimeError(
                "Eskiz SMS serveriga ulanish amalga oshmadi"
            ) from exc

        if response.status_code not in (200, 201):
            # Token, OTP yoki boshqa maxfiy ma'lumotlarni logga chiqarmaymiz.
            logger.error(
                "Eskiz SMS yuborishni rad etdi. HTTP status: %s",
                response.status_code,
            )
            raise RuntimeError(
                f"Eskiz SMS xatosi: HTTP {response.status_code}"
            )

        logger.info("Eskiz SMS so'rovi muvaffaqiyatli qabul qilindi")

    @classmethod
    async def send_otp(cls, phone: str, otp: str) -> None:
        """Ro'yxatdan o'tish yoki tasdiqlash OTP kodini yuboradi."""
        normalized_phone = cls._normalize_phone(phone)

        message = f"""NarxNav sayti orqali ro\'yxatdan o\'tish uchun tasdiqlash kodingiz: {otp}"""

        await cls._post_sms(
            phone=normalized_phone,
            message=message,
        )

    @classmethod
    async def send_result_ready(cls, phone: str) -> None:
        """Imtihon natijasi tayyor bo'lganda SMS yuboradi."""
        normalized_phone = cls._normalize_phone(phone)

        site = (
            settings.PUBLIC_BASE_URL
            or f"https://{settings.AUTH_DOMAIN}"
        )

        message = (
            "Steply mock imtihon natijangiz tayyor. "
            f"Natijangizni ko'rish: {site}"
        )

        await cls._post_sms(
            phone=normalized_phone,
            message=message,
        )
