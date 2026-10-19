"""
Oddiy in-memory rate limiter (IP asosida).
Storage RATE_LIMIT_STORAGE_URI orqali tanlanadi: memory:// (dev) yoki redis:// (production).

Ishlatish:
    from app.core.rate_limit import limiter, otp_limit

    @router.post("/register")
    @limiter.limit("5/minute")
    async def register(request: Request, ...):
        ...
"""
from slowapi import Limiter
from slowapi.errors import RateLimitExceeded
from fastapi import Request
from fastapi.responses import JSONResponse

from app.core.config import settings


def _get_ip(request: Request) -> str:
    """Rate-limit kaliti uchun mijoz IP manzilini topadi.

    X-Forwarded-For — mijoz o'zi yubora oladigan oddiy HTTP header, shuning
    uchun faqat TRUST_PROXY_HEADERS=true bo'lganda (ya'ni server ishonchli
    reverse-proxy ortida bo'lib, proxy bu headerni har doim qayta yozganda)
    ishlatiladi. Aks holda uni hisobga olish — har so'rovda soxta IP yuborib
    barcha rate-limitlarni (login brute-force, OTP spam) chetlab o'tish
    imkonini beradi.
    """
    if settings.TRUST_PROXY_HEADERS:
        forwarded = request.headers.get("X-Forwarded-For")
        if forwarded:
            return forwarded.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


limiter = Limiter(key_func=_get_ip, storage_uri=settings.RATE_LIMIT_STORAGE_URI)


async def rate_limit_error_handler(request: Request, exc: RateLimitExceeded) -> JSONResponse:
    return JSONResponse(
        status_code=429,
        content={
            "error": {
                "status_code": 429,
                "code": "RATE_LIMITED__429",
                "message": "Juda ko'p so'rov. Biroz kutib turing.",
                "fields": None,
            }
        },
    )
