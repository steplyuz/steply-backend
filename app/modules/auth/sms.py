"""Eskiz.uz SMS API klienti (notify.eskiz.uz).

* Token 24 soat amal qiladi; Eskiz uni muddatidan oldin ham bekor qilishi mumkin.
  Shuning uchun 401 javobida token qayta olinadi va so'rov bir marta qayta yuboriladi.
* Xato javoblari SmsApiError sifatida qaytadi (status kodi va Eskiz xabari bilan).
  SmsApiError RuntimeError'dan meros oladi — eski chaqiruvchilar buzilmaydi.
* Test uchun `SmsService._transport` ga httpx.MockTransport berish mumkin.
"""
from __future__ import annotations

import asyncio
import re
from datetime import datetime, timedelta, timezone
from typing import Any

import httpx

from app.core.config import settings

BASE_URL = "https://notify.eskiz.uz/api"
REQUEST_TIMEOUT = 10.0
TOKEN_TTL = timedelta(hours=23)  # 24 soatlik tokenni 1 soat oldin yangilaymiz

# OTP matni Eskiz'da tasdiqlangan shablonga mos bo'lishi kerak (production).
OTP_TEMPLATE = "Steply sayti orqali ro'yxatdan o'tish uchun tasdiqlash kodingiz: {otp}"
RESULT_READY_TEMPLATE = "Mock imtihon natijangiz tayyor. {site} saytida natijangizni ko'rishingiz mumkin."


class SmsNotConfigured(RuntimeError):
    """ESKIZ_EMAIL / ESKIZ_PASSWORD sozlanmagan."""


class SmsApiError(RuntimeError):
    """Eskiz javobi muvaffaqiyatsiz (HTTP xato yoki noto'g'ri javob)."""

    def __init__(self, status_code: int, message: str):
        super().__init__(f"SMS xato ({status_code}): {message}")
        self.status_code = status_code
        self.message = message

    @property
    def is_permanent(self) -> bool:
        """4xx (401 va 429 dan tashqari) qayta urinish befoyda."""
        return 400 <= self.status_code < 500 and self.status_code not in (401, 429)


def _error_text(response: httpx.Response) -> str:
    try:
        body = response.json()
    except ValueError:
        return response.text[:300]
    if isinstance(body, dict):
        message = body.get("message") or body.get("error") or body
        return str(message)[:300]
    return str(body)[:300]


class SmsService:
    _token: str | None = None
    _token_expires_at: datetime | None = None
    _lock: asyncio.Lock | None = None
    _transport: httpx.AsyncBaseTransport | None = None  # faqat test/integratsiya uchun

    # ---------- Auth ----------

    @classmethod
    def _get_lock(cls) -> asyncio.Lock:
        # Lock'ni birinchi ishlatilganda yaratamiz (turli event loop'larda xato bermasligi uchun)
        if cls._lock is None:
            cls._lock = asyncio.Lock()
        return cls._lock

    @classmethod
    def _client(cls) -> httpx.AsyncClient:
        return httpx.AsyncClient(timeout=REQUEST_TIMEOUT, transport=cls._transport)

    @classmethod
    def invalidate_token(cls) -> None:
        cls._token = None
        cls._token_expires_at = None

    @classmethod
    async def _get_token(cls) -> str:
        """Keshdagi tokenni qaytaradi, muddati tugagan bo'lsa Eskiz'dan yangisini oladi."""
        async with cls._get_lock():
            now = datetime.now(timezone.utc)
            if cls._token and cls._token_expires_at and cls._token_expires_at > now:
                return cls._token
            if not settings.ESKIZ_EMAIL or not settings.ESKIZ_PASSWORD:
                raise SmsNotConfigured("Eskiz SMS sozlanmagan (ESKIZ_EMAIL/ESKIZ_PASSWORD)")

            async with cls._client() as client:
                response = await client.post(
                    f"{BASE_URL}/auth/login",
                    data={"email": settings.ESKIZ_EMAIL, "password": settings.ESKIZ_PASSWORD},
                )
            if response.status_code != 200:
                raise SmsApiError(response.status_code, _error_text(response))

            token = ((response.json() or {}).get("data") or {}).get("token")
            if not token:
                raise SmsApiError(response.status_code, "Eskiz tokenni qaytarmadi")
            cls._token = token
            cls._token_expires_at = datetime.now(timezone.utc) + TOKEN_TTL
            return token

    # ---------- So'rov qatlami ----------

    @classmethod
    async def _request(
        cls,
        method: str,
        path: str,
        *,
        json_body: dict[str, Any] | None = None,
        data: dict[str, Any] | None = None,
        params: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        url = f"{BASE_URL}{path}"
        for attempt in (1, 2):
            token = await cls._get_token()
            async with cls._client() as client:
                response = await client.request(
                    method,
                    url,
                    json=json_body,
                    data=data,
                    params=params,
                    headers={"Authorization": f"Bearer {token}", "Accept": "application/json"},
                )
            if response.status_code == 401 and attempt == 1:
                # Token Eskiz tomonidan bekor qilingan: yangilab, bir marta qayta urinamiz
                cls.invalidate_token()
                continue
            if response.status_code >= 400:
                raise SmsApiError(response.status_code, _error_text(response))
            try:
                body = response.json()
            except ValueError:
                return {}
            return body if isinstance(body, dict) else {}
        raise SmsApiError(401, "Eskiz autentifikatsiya xatosi")

    # ---------- Yordamchilar ----------

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
    def _default_sender(cls) -> str:
        return settings.RESULT_SMS_SENDER

    # ---------- Yuborish ----------

    @classmethod
    async def send_text(cls, phone: str, message: str, sender: str | None = None) -> str | None:
        """Matnli SMS. Eskiz'ning xabar identifikatorini qaytaradi (bo'lsa)."""
        body = await cls._request(
            "POST",
            "/message/sms/send",
            json_body={"mobile_phone": cls._normalize_phone(phone), "message": message, "from": sender or cls._default_sender()},
        )
        provider_id = body.get("id")
        return str(provider_id) if provider_id is not None else None

    @classmethod
    async def send_otp(cls, phone: str, otp: str) -> None:
        await cls.send_text(phone, OTP_TEMPLATE.format(otp=otp))

    @classmethod
    async def send_result_ready(cls, phone: str) -> None:
        await cls.send_text(phone, RESULT_READY_TEMPLATE.format(site=cls._site_url()))

    # ---------- Hisobot va ma'lumot ----------

    @classmethod
    async def status_by_id(cls, sms_id: int | str) -> dict[str, Any]:
        """SMS holati. sms_id: Eskiz ID (INT) yoki so'nggi 30 kun ichidagi request_id (UUID)."""
        return await cls._request("GET", f"/message/sms/status_by_id/{sms_id}")

    @classmethod
    async def prices(cls) -> dict[str, Any]:
        """Lokal va global SMS narxlari."""
        return await cls._request("GET", "/user/prices")

    @classmethod
    async def dispatch_report(cls, dispatch_id: int | str, *, is_ad: bool | None = None, status: str | None = None) -> dict[str, Any]:
        """Rassylka bo'yicha xarajat hisoboti.

        status: None (hammasi) | "delivered" (yetkazilgan) | "rejected" (yetkazilmagan)
        is_ad: None (hammasi) | True (reklama) | False (servis)
        """
        if status not in (None, "delivered", "rejected"):
            raise ValueError("status faqat None, 'delivered' yoki 'rejected' bo'lishi mumkin")
        form = {"dispatch_id": str(dispatch_id), "is_ad": "" if is_ad is None else ("1" if is_ad else "0")}
        return await cls._request(
            "POST", "/report/total-by-dispatch", data=form, params={"status": status} if status else None,
        )
