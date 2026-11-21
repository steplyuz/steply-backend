"""Kanal adapterlari: SMS (Eskiz), Telegram (Bot API), email (Gmail OAuth yoki Resend API).

Xato turlari:
* PermanentChannelError — qayta urinish befoyda (noto'g'ri manzil, bot bloklangan, sozlama yo'q).
* TransientChannelError — vaqtinchalik (tarmoq, 5xx, 429). Worker qayta urinadi.

Diqqat: httpx xato matnida URL bo'lishi mumkin (Telegram URL ichida bot token bor),
shuning uchun tarmoq xatolarida asl exception matni log/DB ga yozilmaydi.
"""
from __future__ import annotations

import httpx

from app.core.config import settings

HTTP_TIMEOUT = 10


class ChannelError(Exception):
    pass


class PermanentChannelError(ChannelError):
    pass


class TransientChannelError(ChannelError):
    pass


def _safe_error_text(response: httpx.Response) -> str:
    try:
        data = response.json()
        if isinstance(data, dict):
            return str(data.get("description") or data.get("message") or data.get("error") or "")[:300]
    except ValueError:
        pass
    return response.text[:200]


async def send(
    channel: str,
    recipient: str,
    subject: str | None,
    body: str,
    idempotency_key: str,
    *,
    email_account=None,
    list_unsubscribe: str | None = None,
) -> str | None:
    """Xabarni kanal orqali yuboradi. Provayder message id ni qaytaradi (bo'lsa)."""
    if channel == "sms":
        await send_sms(recipient, body)
        return None
    if channel == "telegram":
        return await send_telegram(recipient, body)
    if channel == "email":
        if email_account is not None:
            from app.modules.notifications import gmail

            return await gmail.send_mail(email_account, recipient, subject or "", body, list_unsubscribe)
        return await send_email(recipient, subject or "", body, idempotency_key, list_unsubscribe)
    raise PermanentChannelError(f"Noma'lum kanal: {channel}")


async def send_sms(phone: str, text: str) -> None:
    from app.modules.auth.sms import SmsService

    if not settings.ESKIZ_EMAIL or not settings.ESKIZ_PASSWORD:
        raise PermanentChannelError("Eskiz SMS sozlanmagan (ESKIZ_EMAIL/ESKIZ_PASSWORD)")
    try:
        await SmsService.send_text(phone, text)
    except ValueError as exc:  # telefon formati noto'g'ri
        raise PermanentChannelError(str(exc)) from exc
    except (httpx.HTTPError, RuntimeError) as exc:
        raise TransientChannelError("SMS yuborishda vaqtinchalik xato") from exc


async def send_telegram(chat_id: str, text: str) -> str | None:
    if not settings.TELEGRAM_BOT_TOKEN:
        raise PermanentChannelError("TELEGRAM_BOT_TOKEN sozlanmagan")
    try:
        chat = int(chat_id)
    except ValueError as exc:
        raise PermanentChannelError("Telegram chat_id noto'g'ri") from exc

    url = f"https://api.telegram.org/bot{settings.TELEGRAM_BOT_TOKEN}/sendMessage"
    try:
        async with httpx.AsyncClient(timeout=HTTP_TIMEOUT) as client:
            response = await client.post(url, json={"chat_id": chat, "text": text[:4096]})
    except httpx.HTTPError as exc:
        raise TransientChannelError("Telegram tarmoq xatosi") from exc

    if response.status_code == 200:
        try:
            result = response.json().get("result") or {}
            message_id = result.get("message_id")
            return str(message_id) if message_id is not None else None
        except ValueError:
            return None
    detail = f"Telegram {response.status_code}: {_safe_error_text(response)}"
    # 429 va 5xx — vaqtinchalik; 400/403 (bot bloklangan, /start bosilmagan) — doimiy
    if response.status_code == 429 or response.status_code >= 500:
        raise TransientChannelError(detail)
    raise PermanentChannelError(detail)


async def send_email(
    to: str, subject: str, text: str, idempotency_key: str, list_unsubscribe: str | None = None
) -> str | None:
    """Resend API orqali (fallback: Gmail hisob ulanmagan bo'lsa)."""
    if not settings.RESEND_API_KEY or not settings.RESEND_FROM_EMAIL:
        raise PermanentChannelError("Email yuborish sozlanmagan: Gmail hisob ulang yoki RESEND_API_KEY kiriting")
    url = f"{settings.RESEND_API_BASE.rstrip('/')}/emails"
    headers = {
        "Authorization": f"Bearer {settings.RESEND_API_KEY}",
        "Content-Type": "application/json",
        # Qayta urinishda bir xil xat ikki marta ketmasligi uchun
        "Idempotency-Key": idempotency_key[:256],
    }
    payload: dict = {"from": settings.RESEND_FROM_EMAIL, "to": [to], "subject": subject, "text": text}
    if list_unsubscribe:
        payload["headers"] = {
            "List-Unsubscribe": f"<{list_unsubscribe}>",
            "List-Unsubscribe-Post": "List-Unsubscribe=One-Click",
        }
    try:
        async with httpx.AsyncClient(timeout=HTTP_TIMEOUT) as client:
            response = await client.post(url, json=payload, headers=headers)
    except httpx.HTTPError as exc:
        raise TransientChannelError("Resend tarmoq xatosi") from exc

    if response.status_code in (200, 201):
        try:
            return response.json().get("id")
        except ValueError:
            return None
    detail = f"Resend {response.status_code}: {_safe_error_text(response)}"
    if response.status_code == 429 or response.status_code >= 500:
        raise TransientChannelError(detail)
    raise PermanentChannelError(detail)
