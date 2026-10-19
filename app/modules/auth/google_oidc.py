"""Google Sign-In (Google Identity Services): id_token JS'dan keladi, redirect yo'q.

Oqim (Telegram popup bilan bir xil): POST /auth/google/nonce -> GIS `nonce`
parametri -> POST /auth/google/verify {id_token}. Nonce bir martalik va
brauzer cookie'siga bog'langan.
"""
from __future__ import annotations

import logging
import secrets
from datetime import datetime, timedelta, timezone
from typing import Any

import httpx
import jwt
from fastapi import HTTPException, Request
from sqlalchemy import delete, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.modules.auth.models import (
    ContactType, Identity, TelegramOIDCTransaction, User, UserContact, UserProfile, UserRole,
)
from app.modules.auth.schemas import Token
from app.modules.auth.service import AuthService
from app.modules.auth.telegram_oidc import _clean_text, _decrypt, _encrypt, _now, _sha256

logger = logging.getLogger(__name__)

GOOGLE_ISSUERS = ["https://accounts.google.com", "accounts.google.com"]
STATE_COOKIE = "google_oidc_browser"
STATE_COOKIE_PATH = "/api/v1/auth/google"
_FAIL = "Google authentication failed"


class GoogleJWKS:
    _keys: dict[str, dict[str, Any]] = {}
    _expires_at: datetime | None = None

    async def get(self, *, force: bool = False) -> dict[str, dict[str, Any]]:
        now = _now()
        if not force and self._keys and self._expires_at and now < self._expires_at:
            return self._keys
        try:
            async with httpx.AsyncClient(timeout=settings.GOOGLE_HTTP_TIMEOUT_SECONDS) as c:
                r = await c.get(settings.GOOGLE_JWKS_URL)
                r.raise_for_status()
                keys = r.json().get("keys")
        except (httpx.HTTPError, ValueError) as exc:
            logger.warning("Google JWKS fetch failed")
            raise HTTPException(503, "Google authentication is temporarily unavailable") from exc
        parsed = {k["kid"]: k for k in keys or [] if isinstance(k, dict) and isinstance(k.get("kid"), str)}
        if not parsed:
            raise HTTPException(503, "Google signing keys unavailable")
        self._keys, self._expires_at = parsed, now + timedelta(hours=1)
        return parsed

    async def validate(self, id_token: str, *, expected_nonce: str) -> dict[str, Any]:
        try:
            header = jwt.get_unverified_header(id_token)
        except jwt.PyJWTError as exc:
            raise HTTPException(401, _FAIL) from exc
        kid = header.get("kid")
        if header.get("alg") != "RS256" or not isinstance(kid, str):
            raise HTTPException(401, _FAIL)
        keys = await self.get()
        jwk = keys.get(kid) or (await self.get(force=True)).get(kid)
        if jwk is None:
            raise HTTPException(401, _FAIL)
        try:
            claims = jwt.decode(
                id_token,
                jwt.PyJWK.from_dict(jwk, algorithm="RS256").key,
                algorithms=["RS256"],
                audience=settings.GOOGLE_CLIENT_ID,
                issuer=GOOGLE_ISSUERS,
                options={"require": ["iss", "sub", "aud", "iat", "exp", "nonce"]},
                leeway=60,
            )
        except (jwt.PyJWTError, TypeError, ValueError) as exc:
            raise HTTPException(401, _FAIL) from exc
        nonce = claims.get("nonce")
        if not isinstance(nonce, str) or not secrets.compare_digest(nonce, expected_nonce):
            raise HTTPException(401, _FAIL)
        if not isinstance(claims.get("sub"), str) or not claims["sub"]:
            raise HTTPException(401, _FAIL)
        return claims


jwks = GoogleJWKS()


class GoogleOIDCService:
    def __init__(self, db: AsyncSession):
        self.db = db
        self.auth = AuthService(db)

    @staticmethod
    def _clear_cookie(response) -> None:
        response.delete_cookie(key=STATE_COOKIE, path=STATE_COOKIE_PATH, domain=settings.COOKIE_DOMAIN)

    async def start(self, response, request: Request) -> dict[str, str]:
        if not settings.GOOGLE_CLIENT_ID:
            raise HTTPException(503, "Google authentication is not configured")

        secret = request.cookies.get(STATE_COOKIE)
        if secret:  # idempotent: amaldagi ishlatilmagan nonce qayta qaytariladi
            tx = (await self.db.execute(select(TelegramOIDCTransaction).where(
                TelegramOIDCTransaction.browser_secret_hash == _sha256(secret)))).scalar_one_or_none()
            if (tx is not None and tx.used_at is None
                    and tx.expires_at.replace(tzinfo=timezone.utc) > _now() + timedelta(seconds=60)):
                return {"nonce": _decrypt(tx.nonce_encrypted), "client_id": settings.GOOGLE_CLIENT_ID}

        await self.db.execute(delete(TelegramOIDCTransaction).where(
            TelegramOIDCTransaction.expires_at < _now() - timedelta(hours=1)).execution_options(synchronize_session=False))
        nonce, browser_secret = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
        self.db.add(TelegramOIDCTransaction(
            state_hash=_sha256("google:" + secrets.token_urlsafe(32)),
            nonce_hash=_sha256(nonce),
            nonce_encrypted=_encrypt(nonce),
            browser_secret_hash=_sha256(browser_secret),
            code_verifier_encrypted=_encrypt("google-popup-flow"),
            expires_at=_now() + timedelta(seconds=settings.TELEGRAM_OIDC_STATE_TTL_SECONDS),
        ))
        await self.db.flush()
        response.set_cookie(
            key=STATE_COOKIE, value=browser_secret, httponly=True, secure=settings.COOKIE_SECURE,
            samesite=settings.COOKIE_SAMESITE, path=STATE_COOKIE_PATH,
            max_age=settings.TELEGRAM_OIDC_STATE_TTL_SECONDS,
        )
        return {"nonce": nonce, "client_id": settings.GOOGLE_CLIENT_ID}

    async def verify(self, *, request: Request, response, id_token: str) -> Token:
        if not settings.GOOGLE_CLIENT_ID:
            raise HTTPException(503, "Google authentication is not configured")
        secret = request.cookies.get(STATE_COOKIE)
        if not secret or not id_token:
            self._clear_cookie(response)
            raise HTTPException(400, _FAIL)
        tx = (await self.db.execute(select(TelegramOIDCTransaction).where(
            TelegramOIDCTransaction.browser_secret_hash == _sha256(secret)))).scalar_one_or_none()
        if tx is None or tx.used_at is not None or tx.expires_at.replace(tzinfo=timezone.utc) <= _now():
            self._clear_cookie(response)
            raise HTTPException(400, _FAIL)
        nonce = _decrypt(tx.nonce_encrypted)

        # Bir urinish — bir nonce: validatsiyadan OLDIN sarflab, darhol commit.
        consumed = await self.db.execute(
            update(TelegramOIDCTransaction)
            .where(TelegramOIDCTransaction.id == tx.id, TelegramOIDCTransaction.used_at.is_(None))
            .values(used_at=_now()).execution_options(synchronize_session=False))
        if consumed.rowcount != 1:
            self._clear_cookie(response)
            raise HTTPException(400, _FAIL)
        await self.db.commit()

        claims = await jwks.validate(id_token, expected_nonce=nonce)
        user = await self._find_or_create_user(claims)
        if not user.is_active:
            self._clear_cookie(response)
            raise HTTPException(403, "Hisob faol emas")
        user.last_login_at = _now()
        token = await self.auth._create_session_and_tokens(user, request)
        self._clear_cookie(response)
        return token

    async def _identity_user(self, sub: str) -> User | None:
        ident = (await self.db.execute(select(Identity).where(
            Identity.provider == "google", Identity.provider_user_id == sub))).scalar_one_or_none()
        return await self.auth._get_user_full(ident.user_id) if ident else None

    async def _find_or_create_user(self, claims: dict[str, Any]) -> User:
        sub = str(claims["sub"])
        user = await self._identity_user(sub)
        if user is not None:
            return user

        email = (_clean_text(claims.get("email"), 255) or "").lower() or None
        email_verified = claims.get("email_verified") in (True, "true")
        if not email_verified:
            email = None  # tasdiqlanmagan email'ga ishonilmaydi (account takeover xavfi)

        # Never merge/link an existing account solely because the email matches.
        # Account linking must be an explicit authenticated flow proving control
        # of both identities. If this email is already claimed, keep it off the
        # new Google-only account below rather than attaching it to another user.

        full_name = _clean_text(claims.get("name"), 255) or _clean_text(
            " ".join(x for x in (claims.get("given_name"), claims.get("family_name")) if x), 255) or "Google User"
        try:
            async with self.db.begin_nested():
                user = User(global_role=UserRole.STUDENT, is_active=True, hashed_password=None)
                self.db.add(user)
                await self.db.flush()
                self.db.add(UserProfile(user_id=user.id, full_name=full_name,
                                        avatar_url=_clean_text(claims.get("picture"), 500)))
                self.db.add(Identity(user_id=user.id, provider="google", provider_user_id=sub))
                if email:
                    taken = (await self.db.execute(select(UserContact.id).where(
                        UserContact.contact_type == ContactType.EMAIL,
                        UserContact.value == email))).scalar_one_or_none()
                    if taken is None:
                        self.db.add(UserContact(user_id=user.id, contact_type=ContactType.EMAIL,
                                                value=email, is_verified=True, is_primary=True))
                await self.db.flush()
        except IntegrityError:
            existing = await self._identity_user(sub)
            if existing is None:
                raise HTTPException(409, "Google account could not be linked")
            return existing
        return user

    async def _link(self, user_id, sub: str) -> User:
        try:
            async with self.db.begin_nested():
                self.db.add(Identity(user_id=user_id, provider="google", provider_user_id=sub))
                await self.db.flush()
        except IntegrityError:
            existing = await self._identity_user(sub)
            if existing is None:
                raise HTTPException(409, "Google account could not be linked")
            return existing
        return await self.auth._get_user_full(user_id)
