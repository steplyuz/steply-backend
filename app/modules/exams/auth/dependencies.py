"""
exams_auth/dependencies.py
============================
FastAPI Depends — Exams platformasi uchun MUSTAQIL autentifikatsiya
tekshiruvi. `app.modules.auth.dependencies.get_current_user` bilan
HECH QANDAY aloqasi yo'q: boshqa cookie nomlari, boshqa token
decode funksiyasi (boshqa issuer/audience/type).
"""

import uuid

from fastapi import Depends, HTTPException, Request, status
from datetime import datetime, timezone
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.modules.auth.models import User
from .models import ExamsAuthSession

from .security import decode_exams_token

EXAMS_ACCESS_COOKIE = "exams_access_token"
EXAMS_REFRESH_COOKIE = "exams_refresh_token"


async def _resolve_exams_token(request: Request) -> str | None:
    token = request.cookies.get(EXAMS_ACCESS_COOKIE)
    if not token:
        auth = request.headers.get("Authorization", "")
        if auth.startswith("Bearer "):
            token = auth[7:].strip()
    return token or None


class ExamsSession:
    """Exams tokenidan olingan kontekst: KIM + QAYSI SEANS (registration).

    MK ID endi foydalanuvchida emas, `mock_registrations.student_id` da
    yaratilgani uchun Exams tokeni ham faqat "kim" emas, "qaysi seansdagi
    kim" degan ma'noni tashiydi.
    """

    __slots__ = ("user", "registration_id")

    def __init__(self, user: User, registration_id: int | None):
        self.user = user
        self.registration_id = registration_id


async def get_current_exams_session(
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> ExamsSession:
    token = await _resolve_exams_token(request)
    if not token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Exams tizimiga kirish talab etiladi",
            headers={"WWW-Authenticate": "Bearer"},
        )

    payload = decode_exams_token(token, token_type="exams_access")
    user_id = payload.get("sub")
    if not user_id:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, detail="Token ichida foydalanuvchi ma'lumoti yo'q")

    try:
        user_uuid = uuid.UUID(str(user_id))
    except (ValueError, TypeError, AttributeError):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, detail="Token ichidagi identifikator noto'g'ri")

    sid_raw = payload.get("sid")
    try:
        session_id = uuid.UUID(str(sid_raw))
    except (ValueError, TypeError, AttributeError):
        raise HTTPException(status_code=401, detail="Exams sessiyasi yaroqsiz")

    session = (await db.execute(
        select(ExamsAuthSession).where(
            ExamsAuthSession.id == session_id,
            ExamsAuthSession.user_id == user_uuid,
        )
    )).scalar_one_or_none()
    if (
        not session
        or session.is_revoked
        or (session.expires_at.replace(tzinfo=timezone.utc) if session.expires_at.tzinfo is None else session.expires_at) <= datetime.now(timezone.utc)
    ):
        raise HTTPException(status_code=401, detail="Exams sessiyasi bekor qilingan yoki muddati tugagan")

    # Token's registration claim must match the server-side session binding.
    raw_reg = payload.get("reg")
    token_registration_id = int(raw_reg) if isinstance(raw_reg, (int, str)) and str(raw_reg).isdigit() else None
    if token_registration_id != session.registration_id:
        raise HTTPException(status_code=401, detail="Exams sessiyasi yaroqsiz")

    from .service import ExamsAuthService

    service = ExamsAuthService(db)
    user = await service._find_student_by_id(user_uuid)
    if not user:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, detail="Foydalanuvchi topilmadi yoki hisob o'chirilgan")
    if not user.is_active:
        raise HTTPException(status.HTTP_403_FORBIDDEN, detail="Hisob faol emas")

    registration_id = token_registration_id
    if registration_id is not None and request.method not in ("GET", "HEAD", "OPTIONS"):
        from app.modules.exams.mock.models import MockRegistration, RegistrationStatus

        reg = await db.get(MockRegistration, registration_id)
        if reg is not None and reg.status == RegistrationStatus.COMPLETED:
            # Imtihon tugagan: access token muddati tugaguncha ham yozuvlar yopiq (natijani o'qish mumkin).
            raise HTTPException(status.HTTP_403_FORBIDDEN, detail="Imtihon yakunlangan")

    return ExamsSession(user=user, registration_id=registration_id)


