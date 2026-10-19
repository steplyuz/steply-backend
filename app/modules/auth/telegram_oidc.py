"""Telegram Login OIDC service: Authorization Code + PKCE."""
from __future__ import annotations

import base64
import hashlib
import logging
import secrets
from datetime import datetime, timedelta, timezone
from typing import Any
from urllib.parse import urlencode

import httpx
import jwt
from cryptography.fernet import Fernet, InvalidToken
from app.core.keys import fernet_key
from fastapi import HTTPException, Request
from sqlalchemy import delete, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.modules.auth.models import (
    ContactType,
    Identity,
    TelegramOIDCTransaction,
    User,
    UserContact,
    UserProfile,
    UserRole,
)
from app.modules.auth.phone import normalize_uz_phone
from app.modules.auth.schemas import Token
from app.modules.auth.service import AuthService

logger = logging.getLogger(__name__)

OIDC_ISSUER = "https://oauth.telegram.org"
STATE_COOKIE = "telegram_oidc_browser"
STATE_COOKIE_PATH = "/api/v1/auth/telegram"
# Popup (telegram-login.js) flow has no authorization code, so no PKCE verifier.
# The NOT NULL column stores this marker instead (no schema migration needed).
POPUP_FLOW_MARKER = "popup-flow-no-code-verifier"


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _sha256(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _pkce_challenge(verifier: str) -> str:
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    return base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")


def _fernet() -> Fernet:
    # PKCE/state shifrlash JWT imzolash kalitidan ajratilgan (core/keys.py).
    return Fernet(fernet_key("telegram-pkce"))


def _encrypt(value: str) -> str:
    return _fernet().encrypt(value.encode("utf-8")).decode("ascii")


def _decrypt(value: str) -> str:
    try:
        return _fernet().decrypt(value.encode("ascii")).decode("utf-8")
    except (InvalidToken, ValueError, TypeError) as exc:
        raise HTTPException(400, "Telegram authentication failed") from exc


class TelegramOIDCProvider:
    """Telegram discovery/JWKS client with bounded in-process caches."""

    _metadata: dict[str, Any] | None = None
    _metadata_expires_at: datetime | None = None
    _jwks: dict[str, dict[str, Any]] = {}
    _jwks_expires_at: datetime | None = None

    async def discovery(self, *, force: bool = False) -> dict[str, Any]:
        now = _now()
        if (
            not force
            and self._metadata is not None
            and self._metadata_expires_at is not None
            and now < self._metadata_expires_at
        ):
            return self._metadata

        try:
            async with httpx.AsyncClient(
                timeout=settings.TELEGRAM_OIDC_HTTP_TIMEOUT_SECONDS
            ) as client:
                response = await client.get(settings.TELEGRAM_OIDC_DISCOVERY_URL)
                response.raise_for_status()
                metadata = response.json()
        except (httpx.HTTPError, ValueError) as exc:
            logger.warning("Telegram OIDC discovery failed")
            raise HTTPException(503, "Telegram authentication is temporarily unavailable") from exc

        if metadata.get("issuer") != OIDC_ISSUER:
            logger.error("Telegram OIDC discovery returned unexpected issuer")
            raise HTTPException(503, "Telegram authentication is temporarily unavailable")

        for key in ("authorization_endpoint", "token_endpoint", "jwks_uri"):
            if not isinstance(metadata.get(key), str):
                raise HTTPException(503, "Telegram authentication is temporarily unavailable")

        self._metadata = metadata
        self._metadata_expires_at = now + timedelta(hours=1)
        return metadata

    async def jwks(self, *, force: bool = False) -> dict[str, dict[str, Any]]:
        now = _now()
        if (
            not force
            and self._jwks
            and self._jwks_expires_at is not None
            and now < self._jwks_expires_at
        ):
            return self._jwks

        metadata = await self.discovery()
        try:
            async with httpx.AsyncClient(
                timeout=settings.TELEGRAM_OIDC_HTTP_TIMEOUT_SECONDS
            ) as client:
                response = await client.get(metadata["jwks_uri"])
                response.raise_for_status()
                payload = response.json()
        except (httpx.HTTPError, ValueError) as exc:
            logger.warning("Telegram OIDC JWKS fetch failed")
            raise HTTPException(503, "Telegram authentication is temporarily unavailable") from exc

        keys = payload.get("keys")
        if not isinstance(keys, list):
            raise HTTPException(503, "Telegram signing keys unavailable")

        parsed = {
            item["kid"]: item
            for item in keys
            if isinstance(item, dict) and isinstance(item.get("kid"), str)
        }
        if not parsed:
            raise HTTPException(503, "Telegram signing keys unavailable")

        self._jwks = parsed
        self._jwks_expires_at = now + timedelta(hours=1)
        return parsed

    async def authorization_url(self, *, state: str, nonce: str, code_challenge: str) -> str:
        metadata = await self.discovery()
        return (
            f"{metadata['authorization_endpoint']}?"
            + urlencode(
                {
                    "client_id": settings.TELEGRAM_CLIENT_ID,
                    "redirect_uri": settings.TELEGRAM_REDIRECT_URI,
                    "response_type": "code",
                    "scope": settings.TELEGRAM_OIDC_SCOPE,
                    "state": state,
                    "nonce": nonce,
                    "code_challenge": code_challenge,
                    "code_challenge_method": "S256",
                }
            )
        )

    async def exchange_code(self, *, code: str, code_verifier: str) -> dict[str, Any]:
        metadata = await self.discovery()
        try:
            async with httpx.AsyncClient(
                timeout=settings.TELEGRAM_OIDC_HTTP_TIMEOUT_SECONDS
            ) as client:
                response = await client.post(
                    metadata["token_endpoint"],
                    data={
                        "grant_type": "authorization_code",
                        "code": code,
                        "redirect_uri": settings.TELEGRAM_REDIRECT_URI,
                        "client_id": settings.TELEGRAM_CLIENT_ID,
                        "code_verifier": code_verifier,
                    },
                    auth=(settings.TELEGRAM_CLIENT_ID, settings.TELEGRAM_CLIENT_SECRET),
                    headers={"Accept": "application/json"},
                )
        except httpx.HTTPError as exc:
            logger.warning("Telegram OIDC token exchange transport failure")
            raise HTTPException(502, "Telegram authentication failed") from exc

        if response.status_code != 200:
            logger.info(
                "Telegram OIDC token exchange rejected status=%s",
                response.status_code,
            )
            raise HTTPException(401, "Telegram authentication failed")

        try:
            payload = response.json()
        except ValueError as exc:
            raise HTTPException(502, "Telegram authentication failed") from exc

        if not isinstance(payload.get("id_token"), str):
            raise HTTPException(401, "Telegram authentication failed")

        return payload

    async def validate_id_token(
        self, id_token: str, *, expected_nonce: str
    ) -> dict[str, Any]:
        metadata = await self.discovery()

        try:
            header = jwt.get_unverified_header(id_token)
        except jwt.PyJWTError as exc:
            raise HTTPException(401, "Telegram authentication failed") from exc

        alg = header.get("alg")
        kid = header.get("kid")
        supported_algs = metadata.get("id_token_signing_alg_values_supported") or ["RS256"]

        if not isinstance(alg, str) or alg not in supported_algs or alg == "none":
            raise HTTPException(401, "Telegram authentication failed")
        if not isinstance(kid, str):
            raise HTTPException(401, "Telegram authentication failed")

        keys = await self.jwks()
        jwk = keys.get(kid)
        if jwk is None:
            keys = await self.jwks(force=True)
            jwk = keys.get(kid)
        if jwk is None:
            raise HTTPException(401, "Telegram authentication failed")

        try:
            key = jwt.PyJWK.from_dict(jwk, algorithm=alg).key
            claims = jwt.decode(
                id_token,
                key,
                algorithms=[alg],
                audience=settings.TELEGRAM_CLIENT_ID,
                issuer=OIDC_ISSUER,
                options={
                    "require": ["iss", "sub", "aud", "iat", "exp", "nonce"],
                    "verify_signature": True,
                    "verify_exp": True,
                    "verify_aud": True,
                    "verify_iss": True,
                },
                leeway=settings.TELEGRAM_OIDC_CLOCK_SKEW_SECONDS,
            )
        except (jwt.PyJWTError, TypeError, ValueError) as exc:
            raise HTTPException(401, "Telegram authentication failed") from exc

        nonce = claims.get("nonce")
        if not isinstance(nonce, str) or not secrets.compare_digest(nonce, expected_nonce):
            raise HTTPException(401, "Telegram authentication failed")

        sub = claims.get("sub")
        if not isinstance(sub, str) or not sub:
            raise HTTPException(401, "Telegram authentication failed")

        iat = claims.get("iat")
        if not isinstance(iat, (int, float)):
            raise HTTPException(401, "Telegram authentication failed")
        if iat > _now().timestamp() + settings.TELEGRAM_OIDC_CLOCK_SKEW_SECONDS:
            raise HTTPException(401, "Telegram authentication failed")

        return claims


provider = TelegramOIDCProvider()


class TelegramOIDCService:
    def __init__(self, db: AsyncSession):
        self.db = db
        self.auth = AuthService(db)

    async def start(self, response) -> str:
        if not all(
            (
                settings.TELEGRAM_CLIENT_ID,
                settings.TELEGRAM_CLIENT_SECRET,
                settings.TELEGRAM_REDIRECT_URI,
            )
        ):
            raise HTTPException(503, "Telegram authentication is not configured")

        await self.db.execute(
            delete(TelegramOIDCTransaction).where(
                TelegramOIDCTransaction.expires_at < _now() - timedelta(hours=1)
            ).execution_options(synchronize_session=False)
        )

        state = secrets.token_urlsafe(32)
        nonce = secrets.token_urlsafe(32)
        browser_secret = secrets.token_urlsafe(32)
        code_verifier = secrets.token_urlsafe(64)

        tx = TelegramOIDCTransaction(
            state_hash=_sha256(state),
            nonce_hash=_sha256(nonce),
            nonce_encrypted=_encrypt(nonce),
            browser_secret_hash=_sha256(browser_secret),
            code_verifier_encrypted=_encrypt(code_verifier),
            expires_at=_now()
            + timedelta(seconds=settings.TELEGRAM_OIDC_STATE_TTL_SECONDS),
        )
        self.db.add(tx)
        await self.db.flush()

        response.set_cookie(
            key=STATE_COOKIE,
            value=browser_secret,
            httponly=True,
            secure=settings.COOKIE_SECURE,
            samesite=settings.COOKIE_SAMESITE,
            path=STATE_COOKIE_PATH,
            max_age=settings.TELEGRAM_OIDC_STATE_TTL_SECONDS,
        )

        return await provider.authorization_url(
            state=state,
            nonce=nonce,
            code_challenge=_pkce_challenge(code_verifier),
        )

    async def callback(
        self,
        *,
        request: Request,
        response,
        code: str,
        state: str,
    ) -> Token:
        if not code or not state:
            self._clear_state_cookie(response)
            raise HTTPException(400, "Telegram authentication failed")

        tx = (
            await self.db.execute(
                select(TelegramOIDCTransaction).where(
                    TelegramOIDCTransaction.state_hash == _sha256(state)
                )
            )
        ).scalar_one_or_none()

        browser_secret = request.cookies.get(STATE_COOKIE)
        if (
            tx is None
            or tx.used_at is not None
            or tx.expires_at.replace(tzinfo=timezone.utc) <= _now()
            or not browser_secret
            or not secrets.compare_digest(
                tx.browser_secret_hash, _sha256(browser_secret)
            )
        ):
            self._clear_state_cookie(response)
            raise HTTPException(400, "Telegram authentication failed")

        code_verifier = _decrypt(tx.code_verifier_encrypted)
        nonce = _decrypt(tx.nonce_encrypted)
        token_payload = await provider.exchange_code(
            code=code, code_verifier=code_verifier
        )
        claims = await provider.validate_id_token(
            token_payload["id_token"], expected_nonce=nonce
        )

        # Mark the authorization transaction consumed only after Telegram has
        # accepted the code and the ID token has passed all validation checks.
        consumed = await self.db.execute(
            update(TelegramOIDCTransaction)
            .where(
                TelegramOIDCTransaction.id == tx.id,
                TelegramOIDCTransaction.used_at.is_(None),
            )
            .values(used_at=_now())
        )
        if consumed.rowcount != 1:
            self._clear_state_cookie(response)
            raise HTTPException(400, "Telegram authentication failed")

        user = await self._find_or_create_user(claims)
        if not user.is_active:
            self._clear_state_cookie(response)
            raise HTTPException(403, "Hisob faol emas")
        user.last_login_at = _now()
        token = await self.auth._create_session_and_tokens(user, request)

        self._clear_state_cookie(response)
        return token

    # ------------------------------------------------------------------
    # Popup flow (telegram-login.js): no redirect URI, no callback page.
    # Telegram returns the id_token to the page's JS; the backend only has to
    # hand out a one-time nonce first and validate the token afterwards.
    # ------------------------------------------------------------------

    async def start_popup(self, response, request: Request | None = None) -> dict[str, str]:
        if not settings.TELEGRAM_CLIENT_ID:
            raise HTTPException(503, "Telegram authentication is not configured")

        # Idempotent: bitta brauzer cookie'si faqat BITTA tranzaksiyaga ishora
        # qiladi. Frontend /nonce ni ikki marta chaqirsa (React StrictMode,
        # qayta bosish), yangi nonce eskisini "yo'qotib" qo'ymasligi uchun
        # amaldagi, ishlatilmagan nonce qayta qaytariladi.
        existing_secret = request.cookies.get(STATE_COOKIE) if request is not None else None
        if existing_secret:
            tx = (
                await self.db.execute(
                    select(TelegramOIDCTransaction).where(
                        TelegramOIDCTransaction.browser_secret_hash == _sha256(existing_secret)
                    )
                )
            ).scalar_one_or_none()
            if (
                tx is not None
                and tx.used_at is None
                and tx.expires_at.replace(tzinfo=timezone.utc) > _now() + timedelta(seconds=60)
            ):
                return {
                    "nonce": _decrypt(tx.nonce_encrypted),
                    "client_id": str(settings.TELEGRAM_CLIENT_ID),
                }

        await self.db.execute(
            delete(TelegramOIDCTransaction).where(
                TelegramOIDCTransaction.expires_at < _now() - timedelta(hours=1)
            ).execution_options(synchronize_session=False)
        )

        nonce = secrets.token_urlsafe(32)
        browser_secret = secrets.token_urlsafe(32)
        self.db.add(
            TelegramOIDCTransaction(
                # state_hash is UNIQUE NOT NULL; the popup flow has no `state`.
                state_hash=_sha256(secrets.token_urlsafe(32)),
                nonce_hash=_sha256(nonce),
                nonce_encrypted=_encrypt(nonce),
                browser_secret_hash=_sha256(browser_secret),
                code_verifier_encrypted=_encrypt(POPUP_FLOW_MARKER),
                expires_at=_now()
                + timedelta(seconds=settings.TELEGRAM_OIDC_STATE_TTL_SECONDS),
            )
        )
        await self.db.flush()

        response.set_cookie(
            key=STATE_COOKIE,
            value=browser_secret,
            httponly=True,
            secure=settings.COOKIE_SECURE,
            samesite=settings.COOKIE_SAMESITE,
            path=STATE_COOKIE_PATH,
            max_age=settings.TELEGRAM_OIDC_STATE_TTL_SECONDS,
        )
        # client_id is public (it is the `aud` of every id_token).
        return {"nonce": nonce, "client_id": str(settings.TELEGRAM_CLIENT_ID)}

    async def verify_popup(self, *, request: Request, response, id_token: str) -> Token:
        browser_secret = request.cookies.get(STATE_COOKIE)
        if not browser_secret or not id_token:
            self._clear_state_cookie(response)
            raise HTTPException(400, "Telegram authentication failed")

        tx = (
            await self.db.execute(
                select(TelegramOIDCTransaction).where(
                    TelegramOIDCTransaction.browser_secret_hash == _sha256(browser_secret)
                )
            )
        ).scalar_one_or_none()
        if (
            tx is None
            or tx.used_at is not None
            or tx.expires_at.replace(tzinfo=timezone.utc) <= _now()
        ):
            self._clear_state_cookie(response)
            raise HTTPException(400, "Telegram authentication failed")

        nonce = _decrypt(tx.nonce_encrypted)

        # One attempt per nonce: consume BEFORE validating and commit right away,
        # because get_db rolls back on HTTPException. A failed/forged token cannot
        # be retried against the same nonce; the page just asks for a new one.
        consumed = await self.db.execute(
            update(TelegramOIDCTransaction)
            .where(
                TelegramOIDCTransaction.id == tx.id,
                TelegramOIDCTransaction.used_at.is_(None),
            )
            .values(used_at=_now())
            .execution_options(synchronize_session=False)
        )
        if consumed.rowcount != 1:
            self._clear_state_cookie(response)
            raise HTTPException(400, "Telegram authentication failed")
        await self.db.commit()

        claims = await provider.validate_id_token(id_token, expected_nonce=nonce)

        user = await self._find_or_create_user(claims)
        if not user.is_active:
            self._clear_state_cookie(response)
            raise HTTPException(403, "Hisob faol emas")
        user.last_login_at = _now()
        token = await self.auth._create_session_and_tokens(user, request)

        self._clear_state_cookie(response)
        return token

    async def _find_or_create_user(self, claims: dict[str, Any]) -> User:
        provider_user_id = str(claims["sub"])

        identity = (
            await self.db.execute(
                select(Identity).where(
                    Identity.provider == "telegram",
                    Identity.provider_user_id == provider_user_id,
                )
            )
        ).scalar_one_or_none()

        if identity is not None:
            user = await self.auth._get_user_full(identity.user_id)
            numeric_id = _telegram_numeric_id(claims)
            if numeric_id is not None and user.telegram_user_id != numeric_id:
                # Eski versiya `sub` ni telegram_user_id ga yozgan bo'lishi mumkin.
                taken = (await self.db.execute(
                    select(User.id).where(
                        User.telegram_user_id == numeric_id, User.id != user.id
                    )
                )).scalar_one_or_none()
                if taken is None:
                    user.telegram_user_id = numeric_id
            if user.profile is not None:
                username = _clean_text(claims.get("preferred_username"), 50)
                if username:
                    username = username.lstrip("@")
                    if user.profile.username != username:
                        owner = (await self.db.execute(
                            select(UserProfile.user_id).where(
                                UserProfile.username == username,
                                UserProfile.user_id != user.id,
                            )
                        )).scalar_one_or_none()
                        if owner is None:
                            user.profile.username = username
                picture = _clean_text(claims.get("picture"), 500)
                if picture:
                    user.profile.avatar_url = picture
                first_name = _clean_text(claims.get("given_name") or "", 120)
                last_name = _clean_text(claims.get("family_name") or "", 120)
                if first_name or last_name:
                    user.profile.full_name = _clean_text(
                        " ".join(x for x in (first_name, last_name) if x), 255
                    )
            return user

        # Backward compatibility: existing Telegram deep-link accounts can be
        # upgraded to the generic Identity model without account merging.
        telegram_id = _telegram_numeric_id(claims)

        if telegram_id is not None:
            legacy_user = (
                await self.db.execute(
                    select(User).where(User.telegram_user_id == telegram_id)
                )
            ).scalar_one_or_none()
        else:
            legacy_user = None

        if legacy_user is not None:
            try:
                async with self.db.begin_nested():
                    self.db.add(
                        Identity(
                            user_id=legacy_user.id,
                            provider="telegram",
                            provider_user_id=provider_user_id,
                        )
                    )
                    await self.db.flush()
            except IntegrityError:
                identity = (
                    await self.db.execute(
                        select(Identity).where(
                            Identity.provider == "telegram",
                            Identity.provider_user_id == provider_user_id,
                        )
                    )
                ).scalar_one()
                return await self.auth._get_user_full(identity.user_id)
            return legacy_user

        first_name = _clean_text(claims.get("given_name") or "", 120)
        last_name = _clean_text(claims.get("family_name") or "", 120)
        name_claim = _clean_text(claims.get("name") or "", 255)
        full_name = _clean_text(
            " ".join(x for x in (first_name, last_name) if x).strip()
            or name_claim
            or "Telegram User",
            255,
        )

        # New User + Identity creation is one nested transaction. A concurrent
        # first login for the same Telegram account is resolved by the unique
        # provider/provider_user_id constraint.
        try:
            async with self.db.begin_nested():
                user = User(
                    global_role=UserRole.STUDENT,
                    is_active=True,
                    hashed_password=None,
                    telegram_user_id=telegram_id,
                )
                self.db.add(user)
                await self.db.flush()

                username = _clean_text(claims.get("preferred_username"), 50)
                if username:
                    username = username.lstrip("@")
                    username_owner = (
                        await self.db.execute(
                            select(UserProfile.user_id).where(
                                UserProfile.username == username
                            )
                        )
                    ).scalar_one_or_none()
                    if username_owner is not None:
                        username = None

                self.db.add(
                    UserProfile(
                        user_id=user.id,
                        full_name=full_name,
                        username=username,
                        avatar_url=_clean_text(claims.get("picture"), 500),
                    )
                )

                self.db.add(
                    Identity(
                        user_id=user.id,
                        provider="telegram",
                        provider_user_id=provider_user_id,
                    )
                )

                phone = _normalize_phone(claims.get("phone_number"))
                if phone:
                    # Do not steal/reassign an existing phone identity.
                    phone_owner = (
                        await self.db.execute(
                            select(UserContact.user_id).where(
                                UserContact.contact_type == ContactType.PHONE,
                                UserContact.value == phone,
                            )
                        )
                    ).scalar_one_or_none()
                    if phone_owner is None:
                        self.db.add(
                            UserContact(
                                user_id=user.id,
                                contact_type=ContactType.PHONE,
                                value=phone,
                                is_verified=bool(claims.get("phone_number_verified")),
                                is_primary=True,
                            )
                        )

                await self.db.flush()
        except IntegrityError:
            identity = (
                await self.db.execute(
                    select(Identity).where(
                        Identity.provider == "telegram",
                        Identity.provider_user_id == provider_user_id,
                    )
                )
            ).scalar_one_or_none()
            if identity is None:
                raise HTTPException(409, "Telegram account could not be linked")
            return await self.auth._get_user_full(identity.user_id)

        return user

    @staticmethod
    def _clear_state_cookie(response) -> None:
        response.delete_cookie(
            key=STATE_COOKIE,
            path=STATE_COOKIE_PATH,
            domain=settings.COOKIE_DOMAIN,
        )


_INT64_MAX = 2**63 - 1


def _telegram_numeric_id(claims: dict[str, Any]) -> int | None:
    """Haqiqiy Telegram user ID — `id` claim (scope `profile`).

    `sub` — Telegram'ning 19 xonali OPAQUE identifikatori, Telegram user ID
    EMAS (u int64 dan katta bo'lishi ham mumkin). Shuning uchun u
    User.telegram_user_id ga yozilmaydi.
    """
    raw = claims.get("id")
    if isinstance(raw, bool):
        return None
    if isinstance(raw, str) and raw.isdigit():
        raw = int(raw)
    if isinstance(raw, int) and 0 < raw <= _INT64_MAX:
        return raw
    return None


def _normalize_phone(raw: Any) -> str | None:
    """Telegram `phone_number` ("998901234567", "+" yo'q) -> kanonik +998... ."""
    digits = "".join(ch for ch in str(raw or "") if ch.isdigit())
    if not digits:
        return None
    try:
        return normalize_uz_phone(digits)
    except ValueError:
        return "+" + digits if 7 <= len(digits) <= 15 else None


def _clean_text(value: Any, max_length: int) -> str | None:
    if value is None:
        return None
    value = str(value).replace("\x00", "").strip()
    return value[:max_length] or None
