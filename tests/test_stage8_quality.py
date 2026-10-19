"""8-bosqich: sifat — passlib'dan argon2-cffi'ga o'tish, refresh rotatsiyasi (ikkala servis),
xato javobida request_id, konfiguratsiya tozaligi va amaliyot kvotasining idempotentligi.
"""
import asyncio
import os
import tempfile
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

_tmp = tempfile.mkdtemp()
os.environ["DATABASE_URL"] = f"sqlite+aiosqlite:///{_tmp}/test_stage8.db"

import pytest
import pytest_asyncio
from fastapi import FastAPI, HTTPException
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select, text

from app.core import database as dbm
from app.core.config import Settings, settings
from app.core.database import init_db
from app.core.rate_limit import limiter
from app.core.security import hash_password, verify_password
from app.modules.exams.auth.dependencies import EXAMS_REFRESH_COOKIE
from app.modules.exams.auth.router import router as exams_auth_router
from app.modules.exams.mock.apply_router import router as b2b_router
from app.modules.exams.mock.models import ExamSession, MockExam

ROOT = Path(__file__).resolve().parents[1]
API = "/api/v1"

# passlib (argon2, time=2, memory=102400, parallelism=4) bilan yaratilgan eski hash
LEGACY_HASH = ("$argon2id$v=19$m=102400,t=2,p=4$ByCk9J5TyrmXcm6NEaJ0jg$"
               "+aDlh/jzUAaFHcHUVcbIsGKxK15b1NX9cd0lqVyE9HM")


# --------------------------------------------------------------------------
# 1. Parol hashlari: argon2-cffi, mavjud hashlar bilan moslik
# --------------------------------------------------------------------------

def test_legacy_passlib_hash_still_verifies():
    assert verify_password("Legacy-Parol-123", LEGACY_HASH) is True
    assert verify_password("boshqa-parol", LEGACY_HASH) is False


def test_new_hashes_are_argon2id_and_verify():
    h = hash_password("Yangi-Parol-9")
    assert h.startswith("$argon2id$")
    assert verify_password("Yangi-Parol-9", h) is True


@pytest.mark.parametrize("garbage", ["", "not-a-hash", "$2b$12$bcryptlike"])
def test_malformed_hash_is_rejected_not_raised(garbage):
    assert verify_password("x", garbage) is False


def test_passlib_is_no_longer_a_dependency():
    req = (ROOT / "requirements.txt").read_text(encoding="utf-8")
    assert "passlib" not in req.lower()


# --------------------------------------------------------------------------
# 2. Konfiguratsiya va model sozlamalari
# --------------------------------------------------------------------------

def test_default_trusted_hosts_have_no_tunnel_domain():
    defaults = Settings.model_fields["TRUSTED_HOSTS"].default
    assert "localhost" in defaults
    assert not any("ngrok" in h for h in defaults)


def test_error_payload_carries_request_id():
    from app.main import _error_payload

    body = _error_payload(status_code=400, code="X", message="m", request_id="req-123")
    assert body["error"]["request_id"] == "req-123"
    assert "request_id" not in _error_payload(status_code=400, code="X", message="m")["error"]


# --------------------------------------------------------------------------
# 3. Exams refresh: parallel so'rov sessiyani o'chirmasin (grace), o'g'irlash — oila bekor
# --------------------------------------------------------------------------

async def _mk_session(**kw) -> tuple[str, int]:
    async with dbm.AsyncSessionLocal() as s:
        await s.execute(text("PRAGMA foreign_keys=OFF"))
        exam = MockExam(id=f"mx-{uuid.uuid4().hex[:8]}", title="Mock", 
                        price=0, speaking_id="sp-test")
        s.add(exam)
        await s.flush()
        sess = ExamSession(mock_exam_id=exam.id, exam_date=datetime.now(timezone.utc) + timedelta(days=3),
                           location_address="Tashkent", registration_slug=f"slug{uuid.uuid4().hex[:10]}", **kw)
        s.add(sess)
        await s.commit()
        return sess.registration_slug, sess.id


@pytest_asyncio.fixture(scope="module")
async def env():
    await init_db()
    app = FastAPI()
    app.state.limiter = limiter
    app.include_router(b2b_router, prefix=API)
    app.include_router(exams_auth_router, prefix=API)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        yield c
    await dbm.engine.dispose()


@pytest.fixture(autouse=True)
def _reset(monkeypatch):
    limiter.reset()
    monkeypatch.setattr(settings, "TRUST_PROXY_HEADERS", True)
    monkeypatch.setattr(settings, "EXAMS_MK_ID_REQUIRE_PHONE_LAST4", True)
    yield
    limiter.reset()


async def _registered_student(env) -> tuple[str, str]:
    """Ochiq ariza orqali ro'yxatdan o'tgan talaba: (MK ID, telefon oxirgi 4 raqami)."""
    import io
    from PIL import Image

    slug, _ = await _mk_session()
    buf = io.BytesIO()
    Image.new("RGB", (20, 20), (1, 2, 3)).save(buf, format="PNG")
    up = await env.post(f"{API}/apply/{slug}/photo", files={"file": ("p.png", buf.getvalue(), "image/png")})
    assert up.status_code == 201, up.text
    reg = await env.post(f"{API}/apply/{slug}", json={
        "first_name": "Ali", "last_name": "Valiyev", "gender": "M", "birth_date": "2001-03-04",
        "phone": "+998901230007", "photo_url": up.json()["photo_url"], "address": "Toshkent",
    })
    assert reg.status_code == 201, reg.text
    return reg.json()["exam_id"], "0007"


async def test_exams_refresh_grace_keeps_session_for_parallel_tab(env):
    mk_id, last4 = await _registered_student(env)
    login = await env.post(f"{API}/exams-auth/login", json={"student_id": mk_id, "phone_last4": last4})
    assert login.status_code == 200, login.text
    old = login.cookies[EXAMS_REFRESH_COOKIE]

    first = await env.post(f"{API}/exams-auth/refresh", headers={"Cookie": f"{EXAMS_REFRESH_COOKIE}={old}"})
    assert first.status_code == 200, first.text
    new = first.cookies[EXAMS_REFRESH_COOKIE]

    # Ikkinchi tab eski tokenni yuboradi: rad etiladi, lekin sessiya oilasi saqlanadi.
    second = await env.post(f"{API}/exams-auth/refresh", headers={"Cookie": f"{EXAMS_REFRESH_COOKIE}={old}"})
    assert second.status_code == 401
    assert "allaqachon" in second.text

    third = await env.post(f"{API}/exams-auth/refresh", headers={"Cookie": f"{EXAMS_REFRESH_COOKIE}={new}"})
    assert third.status_code == 200, "grace oynasida yangi token ishlashda davom etishi kerak"


async def test_exams_refresh_reuse_after_grace_revokes_family(env, monkeypatch):
    mk_id, last4 = await _registered_student(env)
    login = await env.post(f"{API}/exams-auth/login", json={"student_id": mk_id, "phone_last4": last4})
    old = login.cookies[EXAMS_REFRESH_COOKIE]
    first = await env.post(f"{API}/exams-auth/refresh", headers={"Cookie": f"{EXAMS_REFRESH_COOKIE}={old}"})
    new = first.cookies[EXAMS_REFRESH_COOKIE]

    # Grace oynasi tugadi deb faraz qilamiz (updated_at ni orqaga suramiz).
    async with dbm.AsyncSessionLocal() as s:
        await s.execute(text(
            "UPDATE exams_auth_sessions SET updated_at = :old WHERE refresh_token_hash IS NOT NULL AND is_revoked = 1"
        ), {"old": datetime.now(timezone.utc) - timedelta(minutes=5)})
        await s.commit()

    replay = await env.post(f"{API}/exams-auth/refresh", headers={"Cookie": f"{EXAMS_REFRESH_COOKIE}={old}"})
    assert replay.status_code == 401
    assert "barcha Exams seanslari bekor" in replay.text
    # Oila bekor qilindi: yangi token ham ishlamaydi.
    dead = await env.post(f"{API}/exams-auth/refresh", headers={"Cookie": f"{EXAMS_REFRESH_COOKIE}={new}"})
    assert dead.status_code == 401


# --------------------------------------------------------------------------
# 4. Amaliyot kvotasi: idempotent rezervatsiya, limitdan keyin rad
# --------------------------------------------------------------------------

async def test_practice_quota_is_idempotent_per_test_and_capped():
    from app.modules.billing.entitlements import enforce_practice_limit
    from app.modules.auth.models import User, UserRole
    from app.modules.billing.models import TierPracticeLimit, SubscriptionTier

    async with dbm.AsyncSessionLocal() as s:
        # This test's contract is a cap of five; the migration default is 20/week.
        limits = await s.scalar(select(TierPracticeLimit).where(TierPracticeLimit.tier == SubscriptionTier.FREE))
        if limits is None:
            limits = TierPracticeLimit(tier=SubscriptionTier.FREE, weekly_limit=5, monthly_limit=None)
            s.add(limits)
        else:
            limits.weekly_limit = 5
            limits.monthly_limit = None
        user = User(global_role=UserRole.STUDENT, is_active=True)
        s.add(user)
        await s.commit()
        uid = user.id

    # FREE tarif: haftasiga 5 (migratsiya seed). Bitta testni qayta boshlash kvotani yemasin.
    async with dbm.AsyncSessionLocal() as s:
        for _ in range(3):
            await enforce_practice_limit(s, uid, "READING", "same-test")
            await s.commit()
    for i in range(4):  # jami 1 + 4 = 5 ta (limit)
        async with dbm.AsyncSessionLocal() as s:
            await enforce_practice_limit(s, uid, "READING", f"test-{i}")
            await s.commit()

    async with dbm.AsyncSessionLocal() as s:
        with pytest.raises(HTTPException) as exc:
            await enforce_practice_limit(s, uid, "READING", "one-too-many")
    assert exc.value.status_code == 429


def test_env_example_lists_exactly_the_settings_fields():
    from app.core.config import Settings as _Settings

    lines = (ROOT / ".env.example").read_text(encoding="utf-8").splitlines()
    keys = [l.split("=", 1)[0] for l in lines if l and not l.startswith("#") and "=" in l]
    assert len(keys) == len(set(keys)), "takroriy kalit"
    assert set(keys) == set(_Settings.model_fields), (
        set(keys) ^ set(_Settings.model_fields))


def test_readme_references_existing_files_only():
    import re

    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    for ref in set(re.findall(r"`((?:docs|tests|scripts|app|migrations)/[^`\s]+)`", readme)):
        assert (ROOT / ref).exists() or "*" in ref, f"README mavjud bo'lmagan faylga ishora qiladi: {ref}"


async def test_billing_public_lists_keep_their_fields(env):
    """response_model qo'shilganda javob kalitlari o'zgarmasin: ORM ustunlari bilan solishtiriladi."""
    from app.modules.billing.models import (
        BillingProduct, BillingProductType, SubscriptionPlan, SubscriptionTier,
    )
    from app.modules.billing.router import router as billing_router

    async with dbm.AsyncSessionLocal() as s:
        plan = SubscriptionPlan(name=f"p-{uuid.uuid4().hex[:6]}", price=1000, duration_days=30,
                                tier=SubscriptionTier.PRO, ai_limit=1, exam_limit=2, is_active=True)
        product = BillingProduct(product_type=BillingProductType.MOCK_EXAM, title="Mock", price=500,
                                 is_active=True, code=f"c-{uuid.uuid4().hex[:6]}")
        s.add_all([plan, product])
        await s.commit()

    app = FastAPI()
    app.include_router(billing_router, prefix=API)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        plans = await c.get(f"{API}/billing/plans")
        products = await c.get(f"{API}/billing/products")
    assert plans.status_code == 200 and products.status_code == 200
    plan_row = next(p for p in plans.json() if p["name"] == plan.name)
    assert {"id", "name", "description", "price", "duration_days", "tier", "ai_limit", "exam_limit",
            "is_active", "created_at"} <= set(plan_row)
    assert plan_row["tier"] == "pro"
    product_row = next(p for p in products.json() if p["title"] == "Mock")
    assert {"id", "product_type", "code", "title", "price", "is_active", "created_at"} <= set(product_row)


async def test_billing_user_endpoints_shape_and_hide_receipt_paths(env):
    from app.modules.auth.dependencies import get_current_user
    from app.modules.auth.models import User, UserRole
    from app.modules.billing.models import BillingOrder, BillingOrderStatus, BillingProductType
    from app.modules.billing.router import router as billing_router

    async with dbm.AsyncSessionLocal() as s:
        user = User(global_role=UserRole.STUDENT, is_active=True)
        s.add(user)
        await s.flush()
        s.add(BillingOrder(user_id=user.id, product_type=BillingProductType.MOCK_EXAM, product_id="mx-x",
                           product_title="Mock", amount=1000, discount_amount=0,
                           status=BillingOrderStatus.UNDER_REVIEW,
                           receipt_path="storage/private/billing_receipts/secret.pdf",
                           receipt_storage_key="secret-key"))
        await s.commit()
        uid = user.id

    app = FastAPI()
    app.include_router(billing_router, prefix=API)

    async def _user():
        async with dbm.AsyncSessionLocal() as s:
            return await s.get(User, uid)

    app.dependency_overrides[get_current_user] = _user
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        orders = await c.get(f"{API}/billing/orders")
        limits = await c.get(f"{API}/billing/me/limits")
        sub = await c.get(f"{API}/billing/me/subscription")
    assert orders.status_code == 200, orders.text
    assert len(orders.json()) == 1
    item = orders.json()[0]
    assert isinstance(item["status"], str) and item["status"]
    assert "receipt_path" not in item and "receipt_storage_key" not in item
    assert "secret" not in orders.text
    assert limits.status_code == 200
    body = limits.json()
    assert body["tier"] and "practice" in body
    assert {"weekly_limit", "used_this_week", "remaining"} <= set(body["practice"])
    assert sub.status_code == 200 and sub.json() is None


def test_admin_dashboard_schema_matches_endpoint_keys():
    import re
    from app.modules.admin.schemas import AdminDashboardResponse

    src = (ROOT / "app/modules/admin/router.py").read_text(encoding="utf-8")
    body = src.split("async def admin_dashboard(")[1].split("@router.get(\"/export/users\")")[0]
    endpoint_keys = set(re.findall(r'"(\w+)":\s*await', body))
    assert endpoint_keys == set(AdminDashboardResponse.model_fields)


async def test_public_apply_session_shape(env):
    """GET /apply/{slug} javobi frontend uchun o'zgarmasin."""
    slug, _ = await _mk_session()
    r = await env.get(f"{API}/apply/{slug}")
    assert r.status_code == 200, r.text
    body = r.json()
    assert set(body) == {"delivery_mode", "id", "name", "exam_type", "scheduled_at", "location",
                         "duration_minutes", "payment_details"}
    assert body["exam_type"] in {"online", "offline"}


async def test_public_catalog_shapes_match_schemas(env):
    """/public/* javob shakllari sxemalar bilan mos (response_model runtime validatsiyasi + kalitlar)."""
    from fastapi import FastAPI as _FA
    from httpx import ASGITransport as _AT, AsyncClient as _AC
    from app.modules.exams.mock.public_router import router as public_router
    from app.modules.exams.mock.public_schemas import PublicSessionOut

    slug, sid = await _mk_session()
    app = _FA()
    app.include_router(public_router, prefix=API)
    async with _AC(transport=_AT(app=app), base_url="http://t") as c:
        sessions = await c.get(f"{API}/public/sessions")
        one = await c.get(f"{API}/public/sessions/{sid}")
        catalog = await c.get(f"{API}/public/mock-exams")
    assert sessions.status_code == 200, sessions.text
    assert one.status_code == 200, one.text
    assert catalog.status_code == 200, catalog.text
    row = next(r for r in sessions.json() if r["id"] == sid)
    assert set(row) == set(PublicSessionOut.model_fields)
    assert set(one.json()) == {"exam", "session"}
    assert "answer" not in catalog.text and "correct_answer" not in catalog.text
