"""inPAY avtomatik to'lov: imzo, webhook natijalari va buyurtma yaratish.

- imzo: to'g'ri, noto'g'ri sir, eskirgan vaqt, o'zgartirilgan tana;
- payment.succeeded -> buyurtma PAID, tarif avtomatik beriladi (admin tekshiruvisiz);
- takroriy hodisa ikki marta tarif bermaydi;
- summa mos kelmasa tarif berilmaydi;
- bekor qilingan buyurtma uchun to'lov kelsa avtomatik berilmaydi (needs_review);
- onlayn yoqilganda buyurtma checkout_url va provider ID oladi.
"""
import hashlib
import hmac
import json
import os
import tempfile
import uuid
from datetime import datetime, timedelta, timezone

_tmp = tempfile.mkdtemp()
os.environ["DATABASE_URL"] = f"sqlite+aiosqlite:///{_tmp}/test_inpay.db"

import pytest
import pytest_asyncio
from fastapi import HTTPException
from sqlalchemy import select

from app.core import database as dbm
from app.core.config import settings
from app.core.database import init_db
from app.modules.auth.models import User, UserRole
from app.modules.billing import inpay
from app.modules.billing import router as billing_router
from app.modules.billing.models import (
    BillingOrder, BillingOrderStatus, BillingProductType, SubscriptionPlan, SubscriptionTier, UserSubscription,
)

SECRET = "whsec_test_secret"


@pytest_asyncio.fixture(scope="module", autouse=True)
async def _db():
    await init_db()
    yield
    await dbm.engine.dispose()


def _sign(body: bytes, secret: str = SECRET, t: int | None = None) -> str:
    t = int(datetime.now(timezone.utc).timestamp()) if t is None else t
    sig = hmac.new(secret.encode(), f"{t}.".encode() + body, hashlib.sha256).hexdigest()
    return f"t={t},v1={sig}"


def test_signature_valid_and_rejections():
    body = json.dumps({"type": "payment.succeeded"}).encode()
    now = int(datetime.now(timezone.utc).timestamp())
    inpay.verify_signature(body, _sign(body), SECRET, now, 300)  # ok

    with pytest.raises(ValueError):
        inpay.verify_signature(body, _sign(body, secret="wrong"), SECRET, now, 300)
    with pytest.raises(ValueError):
        inpay.verify_signature(body + b" ", _sign(body), SECRET, now, 300)  # tana o'zgargan
    with pytest.raises(ValueError):
        inpay.verify_signature(body, _sign(body, t=now - 3600), SECRET, now, 300)  # eskirgan
    with pytest.raises(ValueError):
        inpay.verify_signature(body, "", SECRET, now, 300)


async def _plan(price: int = 100000) -> int:
    async with dbm.AsyncSessionLocal() as s:
        plan = SubscriptionPlan(name=f"ip-{uuid.uuid4().hex[:6]}", price=price, duration_days=30,
                                tier=SubscriptionTier.PRO, ai_limit=0, exam_limit=0,
                                included_skills=["READING", "LISTENING", "WRITING", "SPEAKING"])
        s.add(plan)
        await s.commit()
        return plan.id


async def _user() -> uuid.UUID:
    async with dbm.AsyncSessionLocal() as s:
        u = User(global_role=UserRole.STUDENT, is_active=True)
        s.add(u)
        await s.commit()
        return u.id


async def _order(user_id, plan_id, payment_id: str, status=BillingOrderStatus.PENDING_PAYMENT, amount=None) -> str:
    async with dbm.AsyncSessionLocal() as s:
        plan = await s.get(SubscriptionPlan, plan_id)
        order = BillingOrder(
            user_id=user_id, product_type=BillingProductType.SUBSCRIPTION, product_id=str(plan_id),
            product_title=plan.name, amount=int(amount if amount is not None else plan.price),
            original_amount=int(plan.price), discount_amount=0, status=status,
            payment_provider="inpay", provider_payment_id=payment_id,
        )
        s.add(order)
        await s.commit()
        return order.id


def _event(etype: str, payment_id: str, amount: int) -> dict:
    return {"id": f"evt_{uuid.uuid4().hex}", "type": etype,
            "data": {"object": {"id": payment_id, "status": "succeeded", "amount": amount}}}


async def _subs_count(user_id) -> int:
    async with dbm.AsyncSessionLocal() as s:
        rows = (await s.execute(select(UserSubscription).where(UserSubscription.user_id == user_id))).scalars().all()
        return len(rows)


@pytest.mark.asyncio
async def test_succeeded_payment_grants_automatically_once():
    plan_id = await _plan()
    uid = await _user()
    pay_id = f"pay_{uuid.uuid4().hex[:10]}"
    oid = await _order(uid, plan_id, pay_id)
    evt = _event("payment.succeeded", pay_id, 100000)

    async with dbm.AsyncSessionLocal() as s:
        assert await inpay.handle_event(s, evt, datetime.now(timezone.utc)) == "paid"
    async with dbm.AsyncSessionLocal() as s:
        order = await s.get(BillingOrder, oid)
        assert order.status == BillingOrderStatus.PAID
    assert await _subs_count(uid) == 1

    # Takroriy webhook: tarif ikki marta berilmaydi
    async with dbm.AsyncSessionLocal() as s:
        assert await inpay.handle_event(s, evt, datetime.now(timezone.utc)) == "already_processed"
    assert await _subs_count(uid) == 1


@pytest.mark.asyncio
async def test_amount_mismatch_does_not_grant():
    plan_id = await _plan()
    uid = await _user()
    pay_id = f"pay_{uuid.uuid4().hex[:10]}"
    oid = await _order(uid, plan_id, pay_id)
    async with dbm.AsyncSessionLocal() as s:
        assert await inpay.handle_event(s, _event("payment.succeeded", pay_id, 50000), datetime.now(timezone.utc)) == "amount_mismatch"
    async with dbm.AsyncSessionLocal() as s:
        order = await s.get(BillingOrder, oid)
        assert order.status == BillingOrderStatus.PENDING_PAYMENT
        assert "mos emas" in (order.admin_note or "")
    assert await _subs_count(uid) == 0


@pytest.mark.asyncio
async def test_paid_after_cancel_is_flagged_not_granted():
    plan_id = await _plan()
    uid = await _user()
    pay_id = f"pay_{uuid.uuid4().hex[:10]}"
    oid = await _order(uid, plan_id, pay_id, status=BillingOrderStatus.CANCELLED)
    async with dbm.AsyncSessionLocal() as s:
        assert await inpay.handle_event(s, _event("payment.succeeded", pay_id, 100000), datetime.now(timezone.utc)) == "needs_review"
    async with dbm.AsyncSessionLocal() as s:
        order = await s.get(BillingOrder, oid)
        assert order.status == BillingOrderStatus.CANCELLED
        assert "qo'lda tekshiring" in (order.admin_note or "")
    assert await _subs_count(uid) == 0


@pytest.mark.asyncio
async def test_canceled_event_cancels_pending_order():
    plan_id = await _plan()
    uid = await _user()
    pay_id = f"pay_{uuid.uuid4().hex[:10]}"
    oid = await _order(uid, plan_id, pay_id)
    async with dbm.AsyncSessionLocal() as s:
        assert await inpay.handle_event(s, _event("payment.canceled", pay_id, 100000), datetime.now(timezone.utc)) == "canceled"
    async with dbm.AsyncSessionLocal() as s:
        assert (await s.get(BillingOrder, oid)).status == BillingOrderStatus.CANCELLED


@pytest.mark.asyncio
async def test_unknown_payment_is_ignored():
    async with dbm.AsyncSessionLocal() as s:
        assert await inpay.handle_event(s, _event("payment.succeeded", "pay_unknown_1", 1000), datetime.now(timezone.utc)) == "unknown_payment"


@pytest.mark.asyncio
async def test_create_order_returns_checkout_url_when_inpay_enabled(monkeypatch):
    plan_id = await _plan()
    uid = await _user()
    calls = {}

    async def fake_create(**kwargs):
        calls.update(kwargs)
        return {"id": "pay_fake_123", "checkout_url": "https://inpay.uz/checkout/pay_fake_123"}

    monkeypatch.setattr(settings, "INPAY_ENABLED", True)
    monkeypatch.setattr(settings, "INPAY_SECRET_KEY", "sk_test_x")
    monkeypatch.setattr(inpay, "create_payment", fake_create)

    async with dbm.AsyncSessionLocal() as s:
        db_user = await s.get(User, uid)
        data = billing_router.OrderCreate(product_type="subscription", product_id=str(plan_id))
        order = await billing_router.create_order(data, user=db_user, db=s)
        out = billing_router.OrderPublic.model_validate(order).model_dump()

    assert calls["amount"] == 100000
    assert calls["idempotency_key"].startswith("order-")
    assert out["checkout_url"] == "https://inpay.uz/checkout/pay_fake_123"
    async with dbm.AsyncSessionLocal() as s:
        stored = (await s.execute(select(BillingOrder).where(BillingOrder.id == out["id"]))).scalar_one()
        assert stored.provider_payment_id == "pay_fake_123"
        assert stored.status == BillingOrderStatus.PENDING_PAYMENT


@pytest.mark.asyncio
async def test_create_order_without_inpay_keeps_manual_flow(monkeypatch):
    plan_id = await _plan()
    uid = await _user()
    monkeypatch.setattr(settings, "INPAY_ENABLED", False)
    async with dbm.AsyncSessionLocal() as s:
        db_user = await s.get(User, uid)
        order = await billing_router.create_order(
            billing_router.OrderCreate(product_type="subscription", product_id=str(plan_id)), user=db_user, db=s)
        assert order.provider_payment_id is None
        assert order.provider_checkout_url is None
