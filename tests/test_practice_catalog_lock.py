"""GET /practice/tests: premium testlar yashirilmaydi, foydalanuvchiga qarab `is_locked` bilan qaytadi."""
import os
import tempfile
import uuid
from datetime import datetime, timedelta, timezone

_tmp = tempfile.mkdtemp()
os.environ["DATABASE_URL"] = f"sqlite+aiosqlite:///{_tmp}/test_practice_catalog_lock.db"

import pytest
import pytest_asyncio
from fastapi import FastAPI, Request
from httpx import ASGITransport, AsyncClient

from app.core import database as dbm
from app.core.database import init_db
from app.modules.auth.dependencies import get_current_user_optional
from app.modules.auth.models import User, UserProfile, UserRole
from app.modules.billing.models import SubscriptionPlan, SubscriptionTier, UserSubscription
from app.modules.practice.router import router as practice_router
from app.modules.tests.reading.models import ReadingTest

pytestmark = pytest.mark.asyncio
API = "/api/v1"


@pytest_asyncio.fixture(scope="module")
async def env():
    await init_db()
    app = FastAPI()
    app.include_router(practice_router, prefix=API)

    async def _user(request: Request):
        uid = request.headers.get("X-Test-User")
        if not uid:
            return None
        async with dbm.AsyncSessionLocal() as s:
            return await s.get(User, uuid.UUID(uid))

    app.dependency_overrides[get_current_user_optional] = _user
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        yield c
    await dbm.engine.dispose()


async def _mk_user(role=UserRole.STUDENT, tier=None):
    async with dbm.AsyncSessionLocal() as s:
        u = User(global_role=role, is_active=True)
        s.add(u); await s.flush()
        s.add(UserProfile(user_id=u.id, full_name="T"))
        if tier is not None:
            plan = SubscriptionPlan(name=f"p-{uuid.uuid4().hex[:6]}", price=1000, duration_days=30, tier=tier,
                                    included_skills=["READING", "LISTENING", "WRITING"])
            s.add(plan); await s.flush()
            now = datetime.now(timezone.utc)
            s.add(UserSubscription(user_id=u.id, plan_id=plan.id, is_active=True,
                                   start_date=now - timedelta(days=1), end_date=now + timedelta(days=29)))
        await s.commit()
        return u.id


async def _mk_reading(is_free: bool) -> str:
    tid = f"r-{uuid.uuid4().hex[:8]}"
    async with dbm.AsyncSessionLocal() as s:
        s.add(ReadingTest(id=tid, title="t", cefr_level="B2", total_questions=35,
                          is_practice=True, is_active=True, is_free=is_free))
        await s.commit()
    return tid


async def _catalog(env, user=None):
    headers = {"X-Test-User": str(user)} if user else {}
    r = await env.get(f"{API}/practice/tests?skill=reading", headers=headers)
    assert r.status_code == 200
    return {t["id"]: t for t in r.json()["items"]["reading"]}


async def test_free_user_sees_premium_locked_but_listed(env):
    free_id, premium_id = await _mk_reading(True), await _mk_reading(False)
    got = await _catalog(env, await _mk_user())
    assert got[free_id]["access"] == "free" and got[free_id]["is_locked"] is False
    assert got[premium_id]["access"] == "premium" and got[premium_id]["is_locked"] is True


async def test_anonymous_sees_premium_locked(env):
    free_id, premium_id = await _mk_reading(True), await _mk_reading(False)
    got = await _catalog(env)
    assert got[free_id]["is_locked"] is False
    assert got[premium_id]["is_locked"] is True


async def test_plus_and_admin_unlock_premium(env):
    premium_id = await _mk_reading(False)
    assert (await _catalog(env, await _mk_user(tier=SubscriptionTier.PLUS)))[premium_id]["is_locked"] is False
    assert (await _catalog(env, await _mk_user(role=UserRole.ADMIN)))[premium_id]["is_locked"] is False


async def test_free_speaking_stays_open_but_premium_speaking_locked_for_plus_without_speaking(env):
    from app.modules.tests.speaking.models import SpeakingTest

    async def mk(is_free):
        tid = f"s-{uuid.uuid4().hex[:8]}"
        async with dbm.AsyncSessionLocal() as s:
            s.add(SpeakingTest(id=tid, title="sp", is_practice=True, is_active=True, is_free=is_free))
            await s.commit()
        return tid

    free_sp, prem_sp = await mk(True), await mk(False)
    plus = await _mk_user(tier=SubscriptionTier.PLUS)  # included_skills: READING/LISTENING/WRITING
    r = await env.get(f"{API}/practice/tests?skill=speaking", headers={"X-Test-User": str(plus)})
    got = {t["id"]: t for t in r.json()["items"]["speaking"]}
    assert got[free_sp]["is_locked"] is False
    assert got[prem_sp]["is_locked"] is True
