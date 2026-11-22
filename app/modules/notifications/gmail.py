"""Gmail akkauntini OAuth orqali ulash va Gmail API orqali xat yuborish.

Oqim: admin /connect -> Google consent -> /callback (code) -> refresh token shifrlanib saqlanadi.
Yuborish: access token yangilanadi, so'ng users.messages.send (raw RFC 2822, base64url).

Eslatmalar:
* gmail.send — Google "sensitive" scope. Ko'p foydalanuvchi/production uchun Google OAuth verification kerak.
* Gmail kunlik limiti: oddiy akkaunt ~500 xat/kun, Google Workspace ~2000. Ommaviy reklama uchun Resend yaxshiroq.
"""
from __future__ import annotations

import base64
import secrets
import time
import uuid
from datetime import datetime, timedelta, timezone
from email.message import EmailMessage
from email.utils import formataddr
from urllib.parse import urlencode

import httpx
import jwt
from cryptography.fernet import Fernet, InvalidToken

from app.core.config import settings
from app.core.keys import derive_key, fernet_key
from app.modules.notifications.channels import HTTP_TIMEOUT, PermanentChannelError, TransientChannelError

AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_URL = "https://oauth2.googleapis.com/token"
USERINFO_URL = "https://openidconnect.googleapis.com/v1/userinfo"
SEND_URL = "https://gmail.googleapis.com/gmail/v1/users/me/messages/send"
SCOPES = "openid email https://www.googleapis.com/auth/gmail.send"

_STATE_PURPOSE = "gmail-oauth-state"
_STATE_TTL = timedelta(minutes=10)
_TOKEN_REFRESH_MARGIN = 60
# account_id -> (access_token, monotonic_expiry). Har worker o'z keshiga ega.
_access_cache: dict[int, tuple[str, float]] = {}


class GmailAuthError(PermanentChannelError):
    """Ulanish bekor qilingan yoki ruxsat yo'q — admin qayta ulashi kerak."""

    def __init__(self, account_id: int, message: str):
        super().__init__(message)
        self.account_id = account_id


def require_oauth_config() -> None:
    if not settings.GOOGLE_CLIENT_ID or not settings.GOOGLE_CLIENT_SECRET or not settings.GMAIL_REDIRECT_URI:
        raise ValueError("Gmail OAuth sozlanmagan (GOOGLE_CLIENT_ID, GOOGLE_CLIENT_SECRET, GMAIL_REDIRECT_URI)")


def _fernet() -> Fernet:
    return Fernet(fernet_key("gmail-refresh-token"))


def encrypt_token(token: str) -> str:
    return _fernet().encrypt(token.encode("utf-8")).decode("ascii")


def decrypt_token(value: str) -> str:
    return _fernet().decrypt(value.encode("ascii")).decode("utf-8")


# ---------- OAuth ----------

def build_connect_url(admin_id: uuid.UUID) -> str:
    require_oauth_config()
    now = datetime.now(timezone.utc)
    # State: admin id + nonce, imzolangan va 10 daqiqada tugaydi (CSRF himoyasi)
    state = jwt.encode(
        {"sub": str(admin_id), "purpose": _STATE_PURPOSE, "nonce": secrets.token_urlsafe(12),
         "iat": now, "exp": now + _STATE_TTL},
        derive_key(_STATE_PURPOSE), algorithm="HS256",
    )
    params = {
        "client_id": settings.GOOGLE_CLIENT_ID,
        "redirect_uri": settings.GMAIL_REDIRECT_URI,
        "response_type": "code",
        "scope": SCOPES,
        "access_type": "offline",
        "prompt": "consent",  # refresh token olish uchun majburiy
        "include_granted_scopes": "true",
        "state": state,
    }
    return f"{AUTH_URL}?{urlencode(params)}"


def verify_state(state: str) -> uuid.UUID:
    try:
        payload = jwt.decode(state, derive_key(_STATE_PURPOSE), algorithms=["HS256"])
    except jwt.PyJWTError as exc:
        raise ValueError("OAuth state noto'g'ri yoki muddati o'tgan") from exc
    if payload.get("purpose") != _STATE_PURPOSE:
        raise ValueError("OAuth state turi noto'g'ri")
    return uuid.UUID(payload["sub"])


async def exchange_code(code: str) -> dict:
    async with httpx.AsyncClient(timeout=HTTP_TIMEOUT) as client:
        response = await client.post(TOKEN_URL, data={
            "code": code,
            "client_id": settings.GOOGLE_CLIENT_ID,
            "client_secret": settings.GOOGLE_CLIENT_SECRET,
            "redirect_uri": settings.GMAIL_REDIRECT_URI,
            "grant_type": "authorization_code",
        })
    if response.status_code != 200:
        raise ValueError(f"Google token almashtirishda xato ({response.status_code})")
    return response.json()


async def fetch_profile(access_token: str) -> tuple[str, str | None]:
    async with httpx.AsyncClient(timeout=HTTP_TIMEOUT) as client:
        response = await client.get(USERINFO_URL, headers={"Authorization": f"Bearer {access_token}"})
    if response.status_code != 200:
        raise ValueError("Google profil ma'lumotini olib bo'lmadi")
    data = response.json()
    email = (data.get("email") or "").strip().lower()
    if not email or data.get("email_verified") is not True:
        raise ValueError("Google email tasdiqlanmagan")
    return email, data.get("name")


async def _access_token(account) -> str:
    cached = _access_cache.get(account.id)
    if cached and cached[1] - time.monotonic() > _TOKEN_REFRESH_MARGIN:
        return cached[0]
    if not account.refresh_token_encrypted:
        raise GmailAuthError(account.id, "Refresh token yo'q, hisobni qayta ulang")
    try:
        refresh = decrypt_token(account.refresh_token_encrypted)
    except InvalidToken as exc:
        raise GmailAuthError(account.id, "Refresh token o'qilmadi, hisobni qayta ulang") from exc

    try:
        async with httpx.AsyncClient(timeout=HTTP_TIMEOUT) as client:
            response = await client.post(TOKEN_URL, data={
                "client_id": settings.GOOGLE_CLIENT_ID,
                "client_secret": settings.GOOGLE_CLIENT_SECRET,
                "refresh_token": refresh,
                "grant_type": "refresh_token",
            })
    except httpx.HTTPError as exc:
        raise TransientChannelError("Google token tarmoq xatosi") from exc
    if response.status_code == 400 and "invalid_grant" in response.text:
        raise GmailAuthError(account.id, "Gmail ulanishi bekor qilingan, hisobni qayta ulang")
    if response.status_code != 200:
        raise TransientChannelError(f"Google token xatosi ({response.status_code})")

    data = response.json()
    token = data["access_token"]
    _access_cache[account.id] = (token, time.monotonic() + int(data.get("expires_in", 3600)))
    return token


# ---------- Yuborish ----------

def build_raw_message(
    from_email: str, from_name: str | None, to: str, subject: str, text: str, list_unsubscribe: str | None,
    html: str | None = None,
) -> str:
    msg = EmailMessage()
    msg["From"] = formataddr((from_name or "", from_email))
    msg["To"] = to
    msg["Subject"] = subject
    if list_unsubscribe:
        msg["List-Unsubscribe"] = f"<{list_unsubscribe}>"
        msg["List-Unsubscribe-Post"] = "List-Unsubscribe=One-Click"
    msg.set_content(text)
    if html:
        msg.add_alternative(html, subtype="html")
    return base64.urlsafe_b64encode(msg.as_bytes()).decode("ascii")


async def send_mail(account, to: str, subject: str, text: str, list_unsubscribe: str | None = None, html: str | None = None) -> str | None:
    if not account.is_active:
        raise PermanentChannelError("Email hisob faol emas")
    token = await _access_token(account)
    raw = build_raw_message(account.email, account.display_name, to, subject, text, list_unsubscribe, html)
    try:
        async with httpx.AsyncClient(timeout=HTTP_TIMEOUT) as client:
            response = await client.post(SEND_URL, json={"raw": raw}, headers={"Authorization": f"Bearer {token}"})
    except httpx.HTTPError as exc:
        raise TransientChannelError("Gmail tarmoq xatosi") from exc

    if response.status_code == 200:
        try:
            return response.json().get("id")
        except ValueError:
            return None
    body = response.text[:300]
    lowered = body.lower()
    if response.status_code == 401 or (response.status_code == 403 and "insufficient" in lowered):
        _access_cache.pop(account.id, None)
        raise GmailAuthError(account.id, f"Gmail ruxsati yo'q, hisobni qayta ulang ({response.status_code})")
    if response.status_code == 429 or response.status_code >= 500 or (
        response.status_code == 403 and "rate" in lowered
    ):
        raise TransientChannelError(f"Gmail vaqtinchalik xato ({response.status_code})")
    raise PermanentChannelError(f"Gmail {response.status_code}: {body}")
