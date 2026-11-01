"""Admin istalgan foydalanuvchiga istalgan faol tarifni darhol beradi (audit bilan)."""
import os
import tempfile
import uuid

_tmp = tempfile.mkdtemp()
os.environ["DATABASE_URL"] = f"sqlite+aiosqlite:///{_tmp}/admin_grant.db"
os.environ["ENV"] = "local"

import pytest
import pytest_asyncio
from fastapi import HTTPException
from sqlalchemy import select

from app.core import database as dbm
from app.core.database import init_db
from app.modules.auth.models import User, UserRole
from app.modules.billing import router as billing_router
from app.modules.billing.models import BillingOrder, BillingOrderStatus, SubscriptionPlan, SubscriptionTier, UserSubscription

pytestmark = pytest.mark.asyncio


@pytest_asyncio.fixture(scope="module", autouse=True)
async def _db():
    await init_db()
    yield
    await dbm.engine.dispose()


async def _user(role=UserRole.STUDENT) -> User:
    async with dbm.AsyncSessionLocal() as s:
        u = User(global_role=role, is_active=True); s.add(u); await s.commit(); await s.refresh(u)
        return u


async def _plan(active=True) -> int:
    async with dbm.AsyncSessionLocal() as s:
        p = SubscriptionPlan(name=f"grant-{uuid.uuid4().hex[:6]}", price=99000, duration_days=90,
                             tier=SubscriptionTier.PRO, ai_limit=0, exam_limit=0, is_active=active,
                             included_skills=["READING", "WRITING"])
        s.add(p); await s.commit(); return p.id


async def test_admin_grants_plan_immediately_with_audit():
    admin = await _user(UserRole.ADMIN)
    student = await _user()
    plan_id = await _plan()
    async with dbm.AsyncSessionLocal() as s:
        db_admin = await s.get(User, admin.id)
        order = await billing_router.admin_grant_subscription(
            user_id=student.id,
            data=billing_router.AdminGrantSubscriptionRequest(plan_id=plan_id, note="kelishuv bo'yicha"),
            admin=db_admin, db=s,
        )
    assert order.status == BillingOrderStatus.PAID
    assert order.reviewed_by == admin.id
    assert "kelishuv" in (order.admin_note or "")
    async with dbm.AsyncSessionLocal() as s:
        subs = (await s.execute(select(UserSubscription).where(UserSubscription.user_id == student.id))).scalars().all()
        assert len(subs) == 1 and subs[0].plan_id == plan_id and subs[0].is_active


async def test_admin_grant_rejects_inactive_plan_and_unknown_user():
    admin = await _user(UserRole.ADMIN)
    student = await _user()
    inactive = await _plan(active=False)
    async with dbm.AsyncSessionLocal() as s:
        db_admin = await s.get(User, admin.id)
        with pytest.raises(HTTPException) as exc:
            await billing_router.admin_grant_subscription(
                user_id=student.id, data=billing_router.AdminGrantSubscriptionRequest(plan_id=inactive),
                admin=db_admin, db=s)
        assert exc.value.status_code == 404
        with pytest.raises(HTTPException) as exc2:
            await billing_router.admin_grant_subscription(
                user_id=uuid.uuid4(), data=billing_router.AdminGrantSubscriptionRequest(plan_id=await _plan()),
                admin=db_admin, db=s)
        assert exc2.value.status_code == 404
