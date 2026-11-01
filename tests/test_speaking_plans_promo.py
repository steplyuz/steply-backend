"""Speaking AI — umumiy AI kredit pool'idan; promo bilan bepul obuna.

- Speaking kvotasi = oylik obuna AI limiti + sotib olingan kreditlar (umumiy pool, alohida limit yo'q);
- Speaking birligi pool'dan yechiladi; xato bo'lsa birlik qaytariladi;
- 100% promo admin tasdig'isiz obunani darhol faollashtiradi; bir foydalanuvchiga bir marta.
"""
import os
import tempfile
import uuid
from datetime import datetime, timedelta, timezone

_tmp = tempfile.mkdtemp()
os.environ["DATABASE_URL"] = f"sqlite+aiosqlite:///{_tmp}/test_speaking_plans.db"

import pytest
import pytest_asyncio
from fastapi import HTTPException
from sqlalchemy import select

from app.core import database as dbm
from app.core.database import init_db
from app.modules.auth.models import User, UserRole
from app.modules.billing import router as billing_router
from app.modules.billing.entitlements import (
    enforce_ai_quota, get_ai_quota, release_speaking_usage,
)
from app.modules.billing.models import (
    BillingOrder, BillingOrderStatus, BillingProductType, PromoCode, AIUsage, SubscriptionPlan,
    SubscriptionTier, UserSubscription, AICreditGrant,
)
from app.modules.billing.review import grant_subscription


@pytest_asyncio.fixture(scope="module", autouse=True)
async def _db():
    await init_db()
    yield
    await dbm.engine.dispose()


async def _user() -> User:
    async with dbm.AsyncSessionLocal() as s:
        user = User(global_role=UserRole.STUDENT, is_active=True)
        s.add(user)
        await s.commit()
        await s.refresh(user)
        return user


async def _plan(ai_limit: int = 0, days: int = 30, price: int = 100000) -> int:
    async with dbm.AsyncSessionLocal() as s:
        plan = SubscriptionPlan(
            name=f"sp-{uuid.uuid4().hex[:8]}", price=price, duration_days=days,
            tier=SubscriptionTier.PRO, ai_limit=ai_limit, ai_enabled=ai_limit > 0, exam_limit=0,
            included_skills=["READING", "LISTENING", "WRITING", "SPEAKING"],
        )
        s.add(plan)
        await s.commit()
        return plan.id


async def _subscribe(user_id, plan_id: int) -> None:
    now = datetime.now(timezone.utc)
    async with dbm.AsyncSessionLocal() as s:
        plan = await s.get(SubscriptionPlan, plan_id)
        await grant_subscription(s, user_id, plan, now - timedelta(minutes=1))
        await s.commit()


async def _purchased_credits(user_id, credits: int) -> None:
    async with dbm.AsyncSessionLocal() as s:
        order = BillingOrder(
            user_id=user_id, product_type=BillingProductType.AI_CREDITS, product_title="AI kredit",
            amount=10000, original_amount=10000, discount_amount=0, status=BillingOrderStatus.PAID,
        )
        s.add(order)
        await s.flush()
        s.add(AICreditGrant(user_id=user_id, order_id=order.id, credits_total=credits, credits_remaining=credits))
        await s.commit()


async def _promo(plan_id: int | None, percent: int | None = 100) -> str:
    code = f"P{uuid.uuid4().hex[:10].upper()}"
    async with dbm.AsyncSessionLocal() as s:
        s.add(PromoCode(code=code, discount_percent=percent, activates_plan_id=plan_id, is_active=True))
        await s.commit()
    return code


@pytest.mark.asyncio
async def test_speaking_quota_is_the_shared_subscription_pool():
    user = await _user()
    plan_a = await _plan(ai_limit=5)
    await _subscribe(user.id, plan_a)
    async with dbm.AsyncSessionLocal() as s:
        quota = await get_ai_quota(s, user.id)
    assert quota["monthly_limit"] == 5 and quota["used_this_month"] == 0 and quota["remaining"] == 5


@pytest.mark.asyncio
async def test_no_subscription_no_speaking_quota():
    user = await _user()
    async with dbm.AsyncSessionLocal() as s:
        quota = await get_ai_quota(s, user.id)
    assert quota["remaining"] == 0


@pytest.mark.asyncio
async def test_speaking_usage_is_counted_in_the_shared_pool():
    user = await _user()
    await _subscribe(user.id, await _plan(ai_limit=3))
    async with dbm.AsyncSessionLocal() as s:
        s.add(AIUsage(feature="speaking", user_id=user.id, job_id=910001, billing_source="subscription", purchased_units=0))
        await s.commit()
    async with dbm.AsyncSessionLocal() as s:
        quota = await get_ai_quota(s, user.id)
    assert quota["used_this_month"] == 1 and quota["remaining"] == 2


@pytest.mark.asyncio
async def test_purchased_credit_is_used_after_subscription_is_exhausted():
    user = await _user()
    await _subscribe(user.id, await _plan(ai_limit=0))
    await _purchased_credits(user.id, 2)
    async with dbm.AsyncSessionLocal() as s:
        purchased = await enforce_ai_quota(s, user.id, 1, feature="Speaking")
        await s.commit()
    assert purchased == 1
    async with dbm.AsyncSessionLocal() as s:
        grant = (await s.execute(select(AICreditGrant).where(AICreditGrant.user_id == user.id))).scalar_one()
        assert grant.credits_remaining == 1


@pytest.mark.asyncio
async def test_speaking_error_refunds_purchased_credit():
    user = await _user()
    await _subscribe(user.id, await _plan(ai_limit=0))
    await _purchased_credits(user.id, 1)
    job_id = 920001
    async with dbm.AsyncSessionLocal() as s:
        purchased = await enforce_ai_quota(s, user.id, 1, feature="Speaking")
        s.add(AIUsage(feature="speaking", user_id=user.id, job_id=job_id, billing_source="purchased", purchased_units=purchased))
        await s.commit()
    async with dbm.AsyncSessionLocal() as s:
        await release_speaking_usage(s, job_id)
        await s.commit()
    async with dbm.AsyncSessionLocal() as s:
        grant = (await s.execute(select(AICreditGrant).where(AICreditGrant.user_id == user.id))).scalar_one()
        assert grant.credits_remaining == 1
        assert await s.scalar(select(AIUsage.id).where(AIUsage.job_id == job_id)) is None


@pytest.mark.asyncio
async def test_failed_speaking_job_does_not_consume_subscription_units():
    user = await _user()
    await _subscribe(user.id, await _plan(ai_limit=2))
    job_id = 930001
    async with dbm.AsyncSessionLocal() as s:
        s.add(AIUsage(feature="speaking", user_id=user.id, job_id=job_id, billing_source="subscription", purchased_units=0))
        await s.commit()
    async with dbm.AsyncSessionLocal() as s:
        assert (await get_ai_quota(s, user.id))["used_this_month"] == 1
    async with dbm.AsyncSessionLocal() as s:
        await release_speaking_usage(s, job_id)
        await s.commit()
    async with dbm.AsyncSessionLocal() as s:
        assert (await get_ai_quota(s, user.id))["remaining"] == 2


@pytest.mark.asyncio
async def test_free_promo_activates_subscription_without_review():
    user = await _user()
    plan_id = await _plan(ai_limit=3, days=30, price=50000)
    code = await _promo(plan_id, percent=100)
    async with dbm.AsyncSessionLocal() as s:
        db_user = await s.get(User, user.id)
        order = await billing_router.activate_promo_subscription(
            billing_router.PromoActivateRequest(code=code), user=db_user, db=s,
        )
    assert order.status == BillingOrderStatus.PAID
    assert order.amount == 0
    assert order.discount_amount == 50000
    async with dbm.AsyncSessionLocal() as s:
        subs = (await s.execute(select(UserSubscription).where(UserSubscription.user_id == user.id))).scalars().all()
        assert len(subs) == 1 and subs[0].plan_id == plan_id
        assert (await get_ai_quota(s, user.id))["monthly_limit"] == 3


@pytest.mark.asyncio
async def test_free_promo_is_single_use_per_user():
    user = await _user()
    plan_id = await _plan()
    code = await _promo(plan_id, percent=100)
    async with dbm.AsyncSessionLocal() as s:
        db_user = await s.get(User, user.id)
        await billing_router.activate_promo_subscription(
            billing_router.PromoActivateRequest(code=code), user=db_user, db=s,
        )
    async with dbm.AsyncSessionLocal() as s:
        db_user = await s.get(User, user.id)
        with pytest.raises(HTTPException) as exc:
            await billing_router.activate_promo_subscription(
                billing_router.PromoActivateRequest(code=code), user=db_user, db=s,
            )
        assert exc.value.status_code == 409


@pytest.mark.asyncio
async def test_partial_promo_cannot_be_used_as_free_activation():
    user = await _user()
    plan_id = await _plan()
    code = await _promo(plan_id, percent=50)
    async with dbm.AsyncSessionLocal() as s:
        db_user = await s.get(User, user.id)
        with pytest.raises(HTTPException) as exc:
            await billing_router.activate_promo_subscription(
                billing_router.PromoActivateRequest(code=code), user=db_user, db=s,
            )
        assert exc.value.status_code == 400


@pytest.mark.asyncio
async def test_validate_promo_reports_free_activation_binding():
    user = await _user()
    plan_id = await _plan()
    code = await _promo(plan_id, percent=100)
    async with dbm.AsyncSessionLocal() as s:
        db_user = await s.get(User, user.id)
        res = await billing_router.validate_promo_code(
            billing_router.PromoValidateRequest(code=code, amount=100000), user=db_user, db=s,
        )
    assert res["activates_plan_id"] == plan_id
    assert res["is_free_activation"] is True


@pytest.mark.asyncio
async def test_validate_promo_partial_is_not_free_activation():
    user = await _user()
    plan_id = await _plan()
    code = await _promo(plan_id, percent=30)
    async with dbm.AsyncSessionLocal() as s:
        db_user = await s.get(User, user.id)
        res = await billing_router.validate_promo_code(
            billing_router.PromoValidateRequest(code=code, amount=100000), user=db_user, db=s,
        )
    assert res["is_free_activation"] is False


@pytest.mark.asyncio
async def test_grant_subscription_same_plan_extends_queue():
    user = await _user()
    plan_id = await _plan(days=30)
    now = datetime.now(timezone.utc)
    async with dbm.AsyncSessionLocal() as s:
        plan = await s.get(SubscriptionPlan, plan_id)
        first = await grant_subscription(s, user.id, plan, now)
        await s.commit()
        first_end = first.end_date
    async with dbm.AsyncSessionLocal() as s:
        plan = await s.get(SubscriptionPlan, plan_id)
        second = await grant_subscription(s, user.id, plan, now)
        await s.commit()
    assert abs((second.start_date.replace(tzinfo=timezone.utc) - first_end.replace(tzinfo=timezone.utc)).total_seconds()) < 2
