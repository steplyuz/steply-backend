"""Ochiq (anonim) formalar uchun CAPTCHA tekshiruvi — Cloudflare Turnstile.

APPLY_CAPTCHA_ENABLED=false bo'lsa tekshiruv o'chiq (dev/test). Productionda yoqilgan
bo'lishi shart. Yoqilgan holatda token yo'q, sekret yo'q yoki provayder javob bermasa —
so'rov RAD ETILADI (fail closed).
"""
import httpx
from fastapi import HTTPException

from app.core.config import settings

TURNSTILE_VERIFY_URL = "https://challenges.cloudflare.com/turnstile/v0/siteverify"


async def verify_turnstile(token: str, remote_ip: str | None) -> bool:
    if not token or not settings.TURNSTILE_SECRET_KEY:
        return False
    data = {"secret": settings.TURNSTILE_SECRET_KEY, "response": token}
    if remote_ip:
        data["remoteip"] = remote_ip
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            response = await client.post(TURNSTILE_VERIFY_URL, data=data)
        return response.status_code == 200 and response.json().get("success") is True
    except (httpx.HTTPError, ValueError):
        return False


async def require_captcha(token: str | None, remote_ip: str | None) -> None:
    if not settings.APPLY_CAPTCHA_ENABLED:
        return
    if not await verify_turnstile(token or "", remote_ip):
        raise HTTPException(400, "CAPTCHA tekshiruvi o'tmadi. Sahifani yangilab qayta urinib ko'ring")
