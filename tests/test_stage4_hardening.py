"""4-bosqich: bot alohida jarayon va holatsiz, umumiy (Redis) rate-limit/throttle,
login hisob bo'yicha cheklov, /admin IP allowlist va login cheklovi, sezgir
admin jadvallarini yashirish.

Redis testi RATE_LIMIT_TEST_REDIS_URL o'rnatilgan bo'lsagina ishlaydi.
"""
import os
import subprocess
import sys
import tempfile
import uuid
from pathlib import Path
from urllib.parse import parse_qs, urlparse

_tmp = tempfile.mkdtemp()
os.environ["DATABASE_URL"] = f"sqlite+aiosqlite:///{_tmp}/test_stage4.db"

import pytest
import pytest_asyncio
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from app.core import database as dbm
from app.core.config import settings
from app.core.database import init_db
from app.core.rate_limit import limiter
from app.core.throttle import FailureThrottle, get_storage
from app.modules.auth import service as auth_service
from app.modules.auth.router import router as auth_router

API = "/api/v1"
ROOT = Path(__file__).resolve().parents[1]
PASSWORD = "Parol12345"
INTERNAL = {"X-Internal-Token": "stage4-internal-token"}


def _phone() -> str:
    return f"+99890{uuid.uuid4().int % 10_000_000:07d}"


async def _make_user(phone: str, password: str = PASSWORD) -> None:
    from app.core.security import hash_password

    async with dbm.AsyncSessionLocal() as s:
        await auth_service.AuthService(s)._create_user_account(
            full_name="Test User", hashed_password=hash_password(password), phone=phone
        )
        await s.commit()


@pytest.fixture(autouse=True)
def _isolate(monkeypatch):
    monkeypatch.setattr(settings, "INTERNAL_API_TOKEN", INTERNAL["X-Internal-Token"])
    monkeypatch.setattr(settings, "TRUST_PROXY_HEADERS", True)
    limiter.reset()
    get_storage().reset()
    yield
    get_storage().reset()


@pytest_asyncio.fixture(scope="module")
async def client():
    await init_db()
    app = FastAPI()
    app.state.limiter = limiter
    app.include_router(auth_router, prefix=API)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        yield c
    await dbm.engine.dispose()


# --------------------------------------------------------------------------
# 1. Bot alohida jarayon: API import vaqtida botni yuklamaydi va token talab qilmaydi
# --------------------------------------------------------------------------

def test_api_import_does_not_load_bot_or_require_bot_token():
    env = {k: v for k, v in os.environ.items()
           if not k.startswith(("TELEGRAM_", "INTERNAL_API"))}
    env.update({"ENV": "local", "AUTO_CREATE_SCHEMA": "false",
                "DATABASE_URL": "sqlite+aiosqlite:///:memory:"})
    code = "import sys, app.main; print('bot' in sys.modules, 'aiogram' in sys.modules)"
    r = subprocess.run([sys.executable, "-c", code], cwd=ROOT, env=env,
                       capture_output=True, text=True, timeout=180)
    assert r.returncode == 0, r.stderr[-1500:]
    assert r.stdout.strip().splitlines()[-1] == "False False"


def test_bot_in_api_is_disabled_by_default():
    assert settings.RUN_TELEGRAM_BOT_IN_API is False


# --------------------------------------------------------------------------
# 2. Bot holatsiz: butun ro'yxatdan o'tish FSM'siz (faqat telegram_user_id bilan)
# --------------------------------------------------------------------------

async def test_telegram_registration_flow_is_stateless(client, monkeypatch):
    async def _enabled(self):
        return None

    monkeypatch.setattr(auth_service.AuthService, "_require_sms_registration_enabled", _enabled)
    phone = _phone()
    tg_id = 987654321
    body = {"phone": phone, "password": PASSWORD, "full_name": "Ali Valiyev",
            "birth_date": "2000-01-01", "gender": "male"}

    r = await client.post(f"{API}/auth/register", json=body)
    assert r.status_code == 200, r.text
    start_token = parse_qs(urlparse(r.json()["bot_link"]).query)["start"][0]

    # Bot: /start (token bilan) → backendda tg_id bog'lanadi
    r = await client.post(f"{API}/auth/telegram/register/start", headers=INTERNAL, json={
        "start_token": start_token, "telegram_user_id": tg_id, "chat_id": "111"})
    assert r.status_code == 200, r.text

    # Bot: kontakt — start_token YO'Q, faqat telegram_user_id
    r = await client.post(f"{API}/auth/telegram/register/contact", headers=INTERNAL, json={
        "telegram_user_id": tg_id, "chat_id": "111", "phone": phone})
    assert r.status_code == 200 and r.json()["verified"] is True, r.text

    # Bot: kodni olish — yana faqat telegram_user_id
    r = await client.post(f"{API}/auth/telegram/register/code", headers=INTERNAL,
                          json={"telegram_user_id": tg_id})
    assert r.status_code == 200, r.text
    code = r.json()["code"]
    assert len(code) == 6 and code.isdigit()

    # Foydalanuvchi kodni saytga kiritadi
    r = await client.post(f"{API}/auth/register/verify", json={"phone": phone, "code": code})
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "success"


async def test_stateless_lookup_rejects_unbound_telegram_user(client):
    r = await client.post(f"{API}/auth/telegram/register/code", headers=INTERNAL,
                          json={"telegram_user_id": 1})
    assert r.status_code == 404


# --------------------------------------------------------------------------
# 3. Login: hisob bo'yicha cheklov (telefon+IP) va mavjudlikdan mustaqil javob
# --------------------------------------------------------------------------

async def test_login_pair_lock_does_not_block_owner_from_other_ip(client):
    phone = _phone()
    await _make_user(phone)
    attacker = {"X-Forwarded-For": "203.0.113.50"}
    owner = {"X-Forwarded-For": "198.51.100.10"}

    for _ in range(5):
        r = await client.post(f"{API}/auth/phone/login", json={"phone": phone, "password": "xato-parol"}, headers=attacker)
        assert r.status_code == 400

    locked = await client.post(f"{API}/auth/phone/login", json={"phone": phone, "password": PASSWORD}, headers=attacker)
    assert locked.status_code == 429

    ok = await client.post(f"{API}/auth/phone/login", json={"phone": phone, "password": PASSWORD}, headers=owner)
    assert ok.status_code == 200 and ok.json()["status"] == "success"


async def test_login_lock_is_identical_for_unknown_phone(client):
    phone = _phone()  # ro'yxatdan o'tmagan
    attacker = {"X-Forwarded-For": "203.0.113.77"}
    for _ in range(5):
        await client.post(f"{API}/auth/phone/login", json={"phone": phone, "password": "x"}, headers=attacker)
    r = await client.post(f"{API}/auth/phone/login", json={"phone": phone, "password": "x"}, headers=attacker)
    assert r.status_code == 429  # registered raqamdagidek: ko'rinish bir xil


async def test_successful_login_resets_pair_counter(client):
    phone = _phone()
    await _make_user(phone)
    ip = {"X-Forwarded-For": "198.51.100.44"}
    for _ in range(4):
        await client.post(f"{API}/auth/phone/login", json={"phone": phone, "password": "xato"}, headers=ip)
    ok = await client.post(f"{API}/auth/phone/login", json={"phone": phone, "password": PASSWORD}, headers=ip)
    assert ok.status_code == 200
    # Route'dagi slowapi limiti (5/min) alohida: u bu testni buzmasligi uchun tozalaymiz.
    limiter.reset()
    for _ in range(4):
        await client.post(f"{API}/auth/phone/login", json={"phone": phone, "password": "xato"}, headers=ip)
    again = await client.post(f"{API}/auth/phone/login", json={"phone": phone, "password": PASSWORD}, headers=ip)
    assert again.status_code == 200, "muvaffaqiyatdan keyin hisoblagich nolga qaytishi kerak"


# --------------------------------------------------------------------------
# 4. Umumiy storage: ikki "worker" bitta hisobni ko'radi (Redis bo'lsa)
# --------------------------------------------------------------------------

def test_throttle_shared_between_instances_memory():
    a = FailureThrottle("shared-test", 2, 60)
    b = FailureThrottle("shared-test", 2, 60)
    a.record_failure("k")
    b.record_failure("k")
    assert a.is_blocked("k") and b.is_blocked("k")


@pytest.mark.skipif(not os.environ.get("RATE_LIMIT_TEST_REDIS_URL"), reason="Redis URL berilmagan")
def test_throttle_shared_between_instances_redis():
    from limits.storage import storage_from_string

    uri = os.environ["RATE_LIMIT_TEST_REDIS_URL"]
    s1, s2 = storage_from_string(uri), storage_from_string(uri)
    a = FailureThrottle("redis-test", 3, 60, storage=s1)
    b = FailureThrottle("redis-test", 3, 60, storage=s2)
    key = f"k-{uuid.uuid4().hex}"
    a.reset(key)
    a.record_failure(key)
    b.record_failure(key)
    assert not a.is_blocked(key)
    a.record_failure(key)
    assert b.is_blocked(key), "ikkinchi worker birinchi worker'ning xatolarini ko'rishi kerak"
    a.reset(key)
    assert not b.is_blocked(key)


# --------------------------------------------------------------------------
# 5. /admin: IP allowlist, login cheklovi, sezgir jadvallar yashirilgan
# --------------------------------------------------------------------------

async def _admin_client():
    from app.main import app as main_app

    return AsyncClient(
        transport=ASGITransport(app=main_app, raise_app_exceptions=False), base_url="http://t"
    )


async def test_admin_allowlist_hides_panel_from_other_ips(monkeypatch):
    monkeypatch.setattr(settings, "ADMIN_ALLOWED_IPS", ["10.0.0.1"])
    async with await _admin_client() as c:
        denied = await c.get("/admin/", headers={"X-Forwarded-For": "203.0.113.9"})
        allowed = await c.get("/admin/", headers={"X-Forwarded-For": "10.0.0.1"})
    assert denied.status_code == 404
    assert allowed.status_code != 404


async def test_admin_login_attempts_are_limited(monkeypatch):
    import app.main as main_mod

    monkeypatch.setattr(main_mod, "admin_login_attempts", FailureThrottle("admin-login-test", 3, 900))
    ip = {"X-Forwarded-For": "198.51.100.200"}
    async with await _admin_client() as c:
        for _ in range(3):
            r = await c.post("/admin/api/sign-in", json={"username": "x", "password": "y"}, headers=ip)
            assert r.status_code != 429
        blocked = await c.post("/admin/api/sign-in", json={"username": "x", "password": "y"}, headers=ip)
    assert blocked.status_code == 429


async def test_sensitive_models_are_not_in_admin_panel():
    import app.modules.fastadmin_panel.admin as adm

    exposed = set(adm._HIDDEN_MODELS) & set(adm._auto_registered_models)
    assert exposed == set(), f"admin panelda ko'rinmasligi kerak: {exposed}"
    assert {"UserSession", "VerificationCode", "OtpSendLimit"} <= set(adm._HIDDEN_MODELS)
