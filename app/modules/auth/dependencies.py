"""
auth/dependencies.py
====================
FastAPI Depends uchun autentifikatsiya va rol tekshiruvi.

Muammolar tuzatildi:
  1. Cookie-first + Bearer header fallback (export/download uchun)
  2. require_role() case-insensitive (DB da 'ADMIN', enum da 'admin' bo'lsa ham ishlaydi)
  3. get_current_user_optional() — anonim sahifalar uchun
"""

import uuid

from fastapi import Depends, HTTPException, Request, status
from sqlalchemy import select
from datetime import datetime, timezone
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.security import decode_token
from app.modules.auth.models import User, UserRole, UserSession


async def _resolve_token(request: Request) -> str | None:
    """
    Token olish tartibi:
      1. httpOnly cookie  → 'access_token'
      2. Authorization header → 'Bearer <token>'
    """
    token = request.cookies.get("access_token")
    if not token:
        auth = request.headers.get("Authorization", "")
        if auth.startswith("Bearer "):
            token = auth[7:].strip()
    return token or None


async def get_current_user(
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> User:
    """Majburiy autentifikatsiya. Token topilmasa 401 qaytaradi."""
    token = await _resolve_token(request)
    if not token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Autentifikatsiya talab etiladi",
            headers={"WWW-Authenticate": "Bearer"},
        )

    payload = decode_token(token, token_type="access")
    user_id = payload.get("sub")
    if not user_id:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token ichida foydalanuvchi ma'lumoti yo'q",
        )

    try:
        user_uuid = uuid.UUID(str(user_id))
    except (ValueError, TypeError, AttributeError):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token ichidagi foydalanuvchi identifikatori noto'g'ri",
        )

    sid_raw = payload.get("sid")
    try:
        session_id = uuid.UUID(str(sid_raw))
    except (ValueError, TypeError, AttributeError):
        raise HTTPException(status_code=401, detail="Token sessiyasi yaroqsiz")

    session = (await db.execute(
        select(UserSession).where(UserSession.id == session_id, UserSession.user_id == user_uuid)
    )).scalar_one_or_none()
    if not session or session.is_revoked or (session.expires_at.replace(tzinfo=timezone.utc) if session.expires_at.tzinfo is None else session.expires_at) <= datetime.now(timezone.utc):
        raise HTTPException(status_code=401, detail="Sessiya bekor qilingan yoki muddati tugagan")

    from app.modules.auth.service import AuthService
    service = AuthService(db)
    try:
        user = await service._get_user_full(user_uuid)
    except HTTPException:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Foydalanuvchi topilmadi yoki hisob o'chirilgan",
        )

    if not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Hisob faol emas",
        )
    if user.force_password_change and request.url.path != "/api/v1/auth/password/change":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Avval vaqtinchalik parolni almashtiring",
        )
    return user


async def get_current_user_allow_forced_change(
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> User:
    """Authenticate a user while allowing the dedicated password-change route."""
    token = await _resolve_token(request)
    if not token:
        raise HTTPException(status_code=401, detail="Autentifikatsiya talab etiladi")
    payload = decode_token(token, token_type="access")
    user_id = payload.get("sub")
    try:
        user_uuid = uuid.UUID(str(user_id))
    except (ValueError, TypeError, AttributeError):
        raise HTTPException(status_code=401, detail="Token ichidagi foydalanuvchi identifikatori noto'g'ri")
    sid_raw = payload.get("sid")
    try:
        session_id = uuid.UUID(str(sid_raw))
    except (ValueError, TypeError, AttributeError):
        raise HTTPException(status_code=401, detail="Token sessiyasi yaroqsiz")
    session = (await db.execute(
        select(UserSession).where(UserSession.id == session_id, UserSession.user_id == user_uuid)
    )).scalar_one_or_none()
    if not session or session.is_revoked or (session.expires_at.replace(tzinfo=timezone.utc) if session.expires_at.tzinfo is None else session.expires_at) <= datetime.now(timezone.utc):
        raise HTTPException(status_code=401, detail="Sessiya bekor qilingan yoki muddati tugagan")
    from app.modules.auth.service import AuthService
    user = await AuthService(db)._get_user_full(user_uuid)
    if not user.is_active:
        raise HTTPException(status_code=403, detail="Hisob faol emas")
    return user


async def get_current_user_optional(
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> User | None:
    """Ixtiyoriy autentifikatsiya — token bo'lmasa None qaytaradi."""
    try:
        return await get_current_user(request, db)
    except HTTPException:
        return None


def require_role(*allowed_roles: UserRole):
    """
    Rol tekshiruvi Depends factory.

    Case-insensitive: DB da 'ADMIN' saqlangan bo'lsa ham,
    UserRole.ADMIN = 'admin' bilan to'g'ri solishtiradi.
    """
    allowed_values = {
        (r.value.lower() if isinstance(r, UserRole) else str(r).lower())
        for r in allowed_roles
    }

    async def _checker(current_user: User = Depends(get_current_user)) -> User:
        role_raw = current_user.global_role
        role_value = (
            role_raw.value.lower() if isinstance(role_raw, UserRole)
            else str(role_raw).lower()
        )
        if role_value not in allowed_values:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Bu amalni bajarishga ruxsatingiz yo'q",
            )
        return current_user

    return _checker


def ensure_force_allowed(force: bool, user: User) -> None:
    """force=true (natija/urinishlar bilan birga majburiy o'chirish) faqat SUPER_ADMIN uchun."""
    if force and user.global_role != UserRole.SUPER_ADMIN:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="force=true faqat super admin uchun",
        )
