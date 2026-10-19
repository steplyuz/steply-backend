"""Cookie-auth uchun CSRF himoyasi (Origin / Sec-Fetch-Site tekshiruvi).

Access/refresh cookie'lari brauzer tomonidan avtomatik yuboriladi, shuning uchun
begona saytdan yuborilgan unsafe so'rov foydalanuvchi nomidan bajarilishi mumkin.
Qoidalar:
- faqat unsafe metodlar (POST/PUT/PATCH/DELETE) va cookie bilan kelgan so'rovlar;
- Authorization header bo'lsa — cookie avtomatik yuborilmagan, tekshirilmaydi;
- Origin bor bo'lsa, u ALLOWED_ORIGINS yoki FRONTEND_URL ichida bo'lishi kerak;
- Origin yo'q bo'lsa, Sec-Fetch-Site: cross-site bo'lsa rad etiladi;
- ikkalasi ham yo'q bo'lsa (eski/non-browser mijoz) o'tkaziladi.
"""
from typing import Iterable

from fastapi import Request
from fastapi.responses import JSONResponse

from app.core.config import settings

UNSAFE_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})
COOKIE_NAMES = ("access_token", "refresh_token")


def _normalize(origin: str) -> str:
    return origin.strip().rstrip("/").lower()


def _trusted_origins() -> set[str]:
    origins: Iterable[str] = list(settings.ALLOWED_ORIGINS or [])
    if settings.FRONTEND_URL:
        origins = [*origins, settings.FRONTEND_URL]
    return {_normalize(o) for o in origins if o}


def is_cross_site_cookie_request(request: Request) -> bool:
    if request.method not in UNSAFE_METHODS:
        return False
    if request.headers.get("authorization"):
        return False
    if not any(request.cookies.get(name) for name in COOKIE_NAMES):
        return False

    origin = request.headers.get("origin")
    if origin is not None:
        return _normalize(origin) not in _trusted_origins()
    return request.headers.get("sec-fetch-site", "").lower() == "cross-site"


async def csrf_middleware(request: Request, call_next):
    if is_cross_site_cookie_request(request):
        return JSONResponse(
            status_code=403,
            content={"detail": "CSRF tekshiruvi o'tmadi: so'rov ishonchli manbadan kelmagan"},
        )
    return await call_next(request)
