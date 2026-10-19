"""Auth xavfsizligi (3-bosqich): hisob enumeratsiyasi, telefon normalizatsiyasi,
sxema validatsiyasi, OTP yuborish cheklovi (telefon+IP), refresh grace oynasi,
cookie-auth uchun CSRF himoyasi va kriptografik kalitlarni ajratish.

Haqiqiy SQLite bazasida ishlaydi (test_hardening.py bilan bir xil uslub)."""
import hashlib
import os
import tempfile
import uuid

_tmp = tempfile.mkdtemp()
os.environ["DATABASE_URL"] = f"sqlite+aiosqlite:///{_tmp}/test_auth.db"

import pytest
import pytest_asyncio
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from app.core import database as dbm
from app.core.config import settings
from app.core.database import init_db
from app.core.rate_limit import limiter
from app.modules.auth import service as auth_service
from app.modules.auth.models import VerificationCode
from app.modules.auth.router import router as auth_router

API = "/api/v1"
PASSWORD = "Parol12345"


def _phone() -> str:
    return f"+99890{uuid.uuid4().int % 10_000_000:07d}"


async def _make_user(phone: str, password: str | None = PASSWORD) -> uuid.UUID:
    from app.core.security import hash_password

    async with dbm.AsyncSessionLocal() as s:
        svc = auth_service.AuthService(s)
        user = await svc._create_user_account(
            full_name="Test User",
            hashed_password=hash_password(password) if password else None,
            phone=phone,
        )
        await s.commit()
        return user.id


@pytest_asyncio.fixture(scope="module")
async def client():
    await init_db()
    app = FastAPI()
    app.state.limiter = limiter
    app.include_router(auth_router, prefix=API)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        yield c
    await dbm.engine.dispose()


@pytest.fixture(autouse=True)
def _reset_limiter():
    limiter.reset()


# --------------------------------------------------------------------------
# 1. Hisob enumeratsiyasi: login javoblari bir xil bo'lishi kerak
# --------------------------------------------------------------------------

async def test_login_failures_are_indistinguishable(client):
    registered = _phone()
    no_password = _phone()
    unknown = _phone()
    await _make_user(registered)
    await _make_user(no_password, password=None)

    wrong_pw = await client.post(f"{API}/auth/phone/login", json={"phone": registered, "password": "xato-parol"})
    no_pw = await client.post(f"{API}/auth/phone/login", json={"phone": no_password, "password": PASSWORD})
    missing = await client.post(f"{API}/auth/phone/login", json={"phone": unknown, "password": PASSWORD})

    assert wrong_pw.status_code == no_pw.status_code == missing.status_code
    assert wrong_pw.json() == no_pw.json() == missing.json()


async def test_login_success_returns_tokens(client):
    phone = _phone()
    await _make_user(phone)
    r = await client.post(f"{API}/auth/phone/login", json={"phone": phone, "password": PASSWORD})
    assert r.status_code == 200
    assert r.json()["status"] == "success"


# --------------------------------------------------------------------------
# 2. Telefon normalizatsiyasi: +998, 998 va bo'shliqli formatlar bir xil
# --------------------------------------------------------------------------

def test_normalize_uz_phone_accepts_common_formats():
    from app.modules.auth.phone import normalize_uz_phone

    assert normalize_uz_phone("+998901234567") == "+998901234567"
    assert normalize_uz_phone("998901234567") == "+998901234567"
    assert normalize_uz_phone("+998 90 123 45 67") == "+998901234567"
    assert normalize_uz_phone("90 123 45 67") == "+998901234567"


@pytest.mark.parametrize("bad", ["", "12345", "+7 912 345 67 89", "+99890123456789", "abc"])
def test_normalize_uz_phone_rejects_invalid(bad):
    from app.modules.auth.phone import normalize_uz_phone

    with pytest.raises(ValueError):
        normalize_uz_phone(bad)


async def test_login_accepts_phone_in_any_format(client):
    phone = _phone()  # +998XXXXXXXXX
    await _make_user(phone)
    digits_only = phone.lstrip("+")
    spaced = f"+{phone[1:4]} {phone[4:6]} {phone[6:9]} {phone[9:11]} {phone[11:]}"
    for variant in (digits_only, spaced):
        r = await client.post(f"{API}/auth/phone/login", json={"phone": variant, "password": PASSWORD})
        assert r.status_code == 200, (variant, r.text)
        assert r.json()["status"] == "success", (variant, r.text)


# --------------------------------------------------------------------------
# 3. Sxema validatsiyasi: noto'g'ri kirish 500 emas, 422 bo'lishi kerak
# --------------------------------------------------------------------------

@pytest.mark.parametrize(
    "overrides",
    [
        {"password": "123"},             # parol minimal uzunligidan qisqa
        {"gender": "other"},             # Gender enum'da yo'q
        {"phone": "not-a-phone"},        # telefon formati noto'g'ri
        {"birth_date": "2000-01-01", "full_name": "A"},  # ism juda qisqa
    ],
)
async def test_register_rejects_invalid_input_with_422(client, overrides):
    body = {
        "phone": _phone(),
        "password": PASSWORD,
        "full_name": "Ali Valiyev",
        "birth_date": "2000-01-01",
        "gender": "male",
    }
    body.update(overrides)
    r = await client.post(f"{API}/auth/register", json=body)
    assert r.status_code == 422, r.text


# --------------------------------------------------------------------------
# 4. Register enumeratsiyasi: mavjud va yangi raqam javobi bir xil tuzilishda
# --------------------------------------------------------------------------

async def test_register_response_shape_does_not_reveal_existing_phone(client, monkeypatch):
    async def _enabled(self):
        return None

    monkeypatch.setattr(auth_service.AuthService, "_require_sms_registration_enabled", _enabled)
    existing = _phone()
    await _make_user(existing)
    body_base = {"password": PASSWORD, "full_name": "Ali Valiyev", "birth_date": "2000-01-01", "gender": "male"}

    r_existing = await client.post(f"{API}/auth/register", json={**body_base, "phone": existing})
    r_new = await client.post(f"{API}/auth/register", json={**body_base, "phone": _phone()})

    assert r_existing.status_code == r_new.status_code == 200
    assert set(r_existing.json()) == set(r_new.json())
    assert r_existing.json()["bot_link"] and r_new.json()["bot_link"]
    assert r_existing.json()["message"] == r_new.json()["message"]


# --------------------------------------------------------------------------
# 5. OTP yuborish blokirovkasi: egasini bloklash emas, telefon+IP bo'yicha
# --------------------------------------------------------------------------

async def test_attacker_cannot_lock_owner_out_of_password_reset(client, monkeypatch):
    monkeypatch.setattr(settings, "TRUST_PROXY_HEADERS", True)
    monkeypatch.setattr(auth_service.AuthService, "OTP_RESEND_COOLDOWN_SECONDS", 0)
    owner_phone = _phone()
    await _make_user(owner_phone)

    attacker = {"X-Forwarded-For": "203.0.113.7"}
    owner_ip = {"X-Forwarded-For": "198.51.100.9"}
    for _ in range(3):
        r = await client.post(f"{API}/auth/password/forgot", json={"phone": owner_phone}, headers=attacker)
        assert r.status_code == 200
    blocked = await client.post(f"{API}/auth/password/forgot", json={"phone": owner_phone}, headers=attacker)
    assert blocked.status_code == 429

    # Egasi o'z IP'idan kodni olishda davom eta olishi kerak.
    owner_try = await client.post(f"{API}/auth/password/forgot", json={"phone": owner_phone}, headers=owner_ip)
    assert owner_try.status_code == 200, owner_try.text


# --------------------------------------------------------------------------
# 6. Refresh: parallel so'rov (ikki tab) hamma sessiyani o'chirmasligi kerak
# --------------------------------------------------------------------------

async def test_concurrent_refresh_within_grace_keeps_session(client):
    phone = _phone()
    await _make_user(phone)
    login = await client.post(f"{API}/auth/phone/login", json={"phone": phone, "password": PASSWORD})
    old_refresh = login.cookies["refresh_token"]

    first = await client.post(f"{API}/auth/refresh", headers={"Cookie": f"refresh_token={old_refresh}"})
    assert first.status_code == 200
    new_refresh = first.cookies["refresh_token"]

    # Ikkinchi tab eski tokenni yuboradi: rad etiladi, lekin sessiya o'chmaydi.
    second = await client.post(f"{API}/auth/refresh", headers={"Cookie": f"refresh_token={old_refresh}"})
    assert second.status_code == 401

    third = await client.post(f"{API}/auth/refresh", headers={"Cookie": f"refresh_token={new_refresh}"})
    assert third.status_code == 200, "grace oynasidan keyin ham yangi sessiya ishlashi kerak"


# --------------------------------------------------------------------------
# 7. Kriptografik kalitlarni ajratish va OTP hash'i
# --------------------------------------------------------------------------

def test_derived_keys_are_separated_from_jwt_secret():
    from app.core.keys import derive_key

    jwt_like = hashlib.sha256(settings.SECRET_KEY.encode()).digest()
    otp_enc = derive_key("otp-encrypt")
    otp_mac = derive_key("otp-hmac")
    pkce = derive_key("telegram-pkce")
    assert len({otp_enc, otp_mac, pkce}) == 3
    assert jwt_like not in {otp_enc, otp_mac, pkce}


async def test_otp_code_is_not_stored_as_plain_sha256(client, monkeypatch):
    monkeypatch.setattr(auth_service.secrets, "randbelow", lambda n: 23456)  # -> "123456"
    phone = _phone()
    await _make_user(phone)
    r = await client.post(f"{API}/auth/password/forgot", json={"phone": phone})
    assert r.status_code == 200

    async with dbm.AsyncSessionLocal() as s:
        record = (
            await s.execute(select(VerificationCode).where(VerificationCode.target == phone))
        ).scalars().first()
    assert record is not None
    assert record.code_hash != hashlib.sha256(b"123456").hexdigest(), \
        "OTP kalitsiz sha256 bilan saqlanmasligi kerak (6 xonali kodni brute-force qilish oson)"


# --------------------------------------------------------------------------
# 8. CSRF: cookie bilan kelgan cross-origin unsafe so'rov rad etilsin
# --------------------------------------------------------------------------

@pytest_asyncio.fixture
async def csrf_client():
    from app.core.csrf import csrf_middleware

    app = FastAPI()
    app.state.limiter = limiter
    app.include_router(auth_router, prefix=API)
    app.middleware("http")(csrf_middleware)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        yield c


async def test_csrf_rejects_cross_origin_cookie_post(csrf_client):
    r = await csrf_client.post(
        f"{API}/auth/logout",
        headers={"Cookie": "refresh_token=abc", "Origin": "https://evil.example"},
    )
    assert r.status_code == 403


async def test_csrf_allows_trusted_origin_and_non_browser_clients(csrf_client):
    trusted = settings.ALLOWED_ORIGINS[0]
    r1 = await csrf_client.post(
        f"{API}/auth/logout", headers={"Cookie": "refresh_token=abc", "Origin": trusted}
    )
    r2 = await csrf_client.post(f"{API}/auth/logout", headers={"Cookie": "refresh_token=abc"})
    assert r1.status_code == 200
    assert r2.status_code == 200
