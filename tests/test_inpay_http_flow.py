"""inPAY HTTP oqimi: buyurtma yaratish (inPAY API mock) va imzolangan webhook (haqiqiy endpoint)."""
import hashlib
import hmac
import json
import os
import tempfile
import uuid
from datetime import datetime, timedelta, timezone

_tmp = tempfile.mkdtemp()
os.environ["DATABASE_URL"] = f"sqlite+aiosqlite:///{_tmp}/inpay_http.db"
os.environ["ENV"] = "local"

import pytest
import pytest_asyncio
from fastapi import Request
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from app.core import database as dbm
from app.core.config import settings
from app.core.database import init_db
from app.modules.auth.dependencies import get_current_user
from app.modules.auth.models import User, UserRole
from app.modules.billing import inpay
from app.modules.billing.models import BillingOrder, BillingOrderStatus, SubscriptionPlan, SubscriptionTier, UserSubscription

pytestmark = pytest.mark.asyncio
API = "/api/v1"
SECRET = "whsec_http_test"


@pytest_asyncio.fixture(scope="module")
async def env():
    await init_db()
    from app.main import app
    async with dbm.AsyncSessionLocal() as s:
        student = User(global_role=UserRole.STUDENT, is_active=True); s.add(student); await s.flush()
        plan = SubscriptionPlan(name="inpay-http", price=100000, duration_days=30, tier=SubscriptionTier.PRO,
                                ai_limit=0, exam_limit=0, included_skills=["READING", "WRITING"])
        s.add(plan); await s.commit()
        ids = {"student": str(student.id), "plan": plan.id}

    async def _user(request: Request):
        async with dbm.AsyncSessionLocal() as s:
            return await s.get(User, uuid.UUID(request.headers["X-Test-User"]))

    app.dependency_overrides[get_current_user] = _user
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://localhost") as c:
        yield c, ids
    app.dependency_overrides.clear()


def _sign(body: bytes) -> str:
    t = int(datetime.now(timezone.utc).timestamp())
    sig = hmac.new(SECRET.encode(), f"{t}.".encode() + body, hashlib.sha256).hexdigest()
    return f"t={t},v1={sig}"


async def test_order_creation_calls_inpay_and_returns_checkout(env, monkeypatch):
    client, ids = env
    seen = {}

    class _Resp:
        status_code = 200
        def json(self):
            return {"success": True, "data": {"id": "pay_http_1", "checkout_url": "https://inpay.uz/checkout/pay_http_1"}}

    import httpx
    original_post = httpx.AsyncClient.post

    async def fake_post(self, url, *args, **kw):
        if "inpay.uz" not in str(url):
            return await original_post(self, url, *args, **kw)
        seen.update(url=str(url), json=kw.get("json"), headers=kw.get("headers"))
        return _Resp()

    monkeypatch.setattr(settings, "INPAY_ENABLED", True)
    monkeypatch.setattr(settings, "INPAY_SECRET_KEY", "sk_test_http")
    monkeypatch.setattr(settings, "INPAY_RETURN_URL", "https://steply.uz/dashboard/billing")
    monkeypatch.setattr(settings, "INPAY_WEBHOOK_URL", "https://api.steply.uz/api/v1/billing/inpay/webhook")
    monkeypatch.setattr(httpx.AsyncClient, "post", fake_post)

    r = await client.post(f"{API}/billing/orders", json={"product_type": "subscription", "product_id": str(ids["plan"])},
                          headers={"X-Test-User": ids["student"]})
    assert r.status_code == 201, r.text
    assert r.json()["checkout_url"] == "https://inpay.uz/checkout/pay_http_1"
    assert seen["url"].endswith("/payments/")
    assert seen["headers"]["Authorization"] == "Bearer sk_test_http"
    assert seen["json"]["amount"] == 100000 and seen["json"]["currency"] == "UZS"
    assert seen["json"]["return_url"].startswith("https://steply.uz/dashboard/billing?order=")
    assert seen["json"]["webhook_url"].endswith("/billing/inpay/webhook")


async def test_signed_webhook_over_http_marks_order_paid(env, monkeypatch):
    client, ids = env
    monkeypatch.setattr(settings, "INPAY_WEBHOOK_SECRET", SECRET)
    pay_id = f"pay_hook_{uuid.uuid4().hex[:8]}"
    async with dbm.AsyncSessionLocal() as s:
        plan = await s.get(SubscriptionPlan, ids["plan"])
        order = BillingOrder(user_id=uuid.UUID(ids["student"]), product_type="subscription", product_id=str(plan.id),
                             product_title=plan.name, amount=plan.price, original_amount=plan.price, discount_amount=0,
                             status=BillingOrderStatus.PENDING_PAYMENT, payment_provider="inpay", provider_payment_id=pay_id)
        s.add(order); await s.commit(); oid = order.id

    body = json.dumps({"id": f"evt_{uuid.uuid4().hex}", "type": "payment.succeeded", "livemode": False,
                       "data": {"object": {"id": pay_id, "amount": 100000, "status": "succeeded"}}}).encode()
    r = await client.post(f"{API}/billing/inpay/webhook", content=body,
                          headers={"Content-Type": "application/json", "X-Inpay-Signature": _sign(body)})
    assert r.status_code == 200, r.text
    assert r.json()["result"] == "paid"
    async with dbm.AsyncSessionLocal() as s:
        assert (await s.get(BillingOrder, oid)).status == BillingOrderStatus.PAID


async def test_webhook_rejects_bad_signature_over_http(env, monkeypatch):
    client, _ = env
    monkeypatch.setattr(settings, "INPAY_WEBHOOK_SECRET", SECRET)
    body = b'{"type":"payment.succeeded","data":{"object":{"id":"x","amount":1}}}'
    r = await client.post(f"{API}/billing/inpay/webhook", content=body,
                          headers={"Content-Type": "application/json", "X-Inpay-Signature": "t=1,v1=bad"})
    assert r.status_code == 400


async def test_order_detail_syncs_paid_status_from_inpay_when_webhook_missing(env, monkeypatch):
    client, ids = env
    pay_id = f"pay_sync_{uuid.uuid4().hex[:8]}"
    async with dbm.AsyncSessionLocal() as s:
        plan = await s.get(SubscriptionPlan, ids["plan"])
        order = BillingOrder(user_id=uuid.UUID(ids["student"]), product_type="subscription", product_id=str(plan.id),
                             product_title=plan.name, amount=plan.price, original_amount=plan.price, discount_amount=0,
                             status=BillingOrderStatus.PENDING_PAYMENT, payment_provider="inpay", provider_payment_id=pay_id)
        s.add(order); await s.commit(); oid = order.id

    async def fake_fetch(payment_id):
        return {"id": payment_id, "status": "succeeded", "amount": 100000}

    monkeypatch.setattr(inpay, "fetch_payment", fake_fetch)
    r = await client.get(f"{API}/billing/orders/{oid}", headers={"X-Test-User": ids["student"]})
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "paid"
