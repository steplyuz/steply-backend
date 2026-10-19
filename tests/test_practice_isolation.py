"""Practice faqat is_practice=True (mock emas) testlar bilan ishlaydi:
katalog filtri va mock-only testda practice urinish ochilmasligi."""
import itertools
import os
import tempfile
import uuid

_tmp = tempfile.mkdtemp()
os.environ["DATABASE_URL"] = f"sqlite+aiosqlite:///{_tmp}/test_practice_isolation.db"

import pytest
import pytest_asyncio
from fastapi import FastAPI, Request
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text

from app.core import database as dbm
from app.core.database import init_db
from app.core.rate_limit import limiter
from app.modules.auth.dependencies import get_current_user
from app.modules.auth.models import User, UserProfile, UserRole
from app.modules.practice.router import router as practice_router
from app.modules.tests.listening.models import ListeningTest
from app.modules.tests.listening.router import router as listening_router
from app.modules.tests.reading.models import ReadingTest
from app.modules.tests.reading.router import router as reading_router

pytestmark = pytest.mark.asyncio
API = "/api/v1"


@pytest_asyncio.fixture(scope="module")
async def env():
    await init_db()
    app = FastAPI()
    app.state.limiter = limiter
    for r in (practice_router, reading_router, listening_router):
        app.include_router(r, prefix=API)

    async def _user(request: Request):
        async with dbm.AsyncSessionLocal() as s:
            return await s.get(User, uuid.UUID(request.headers["X-Test-User"]))

    app.dependency_overrides[get_current_user] = _user
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        yield c
    await dbm.engine.dispose()


@pytest.fixture(autouse=True)
def _reset_limiter():
    limiter.reset()


async def _mk_user(role=UserRole.STUDENT):
    async with dbm.AsyncSessionLocal() as s:
        u = User(global_role=role, is_active=True)
        s.add(u); await s.flush()
        s.add(UserProfile(user_id=u.id, full_name="T"))
        await s.commit()
        return u.id


async def _mk_tests(**flags):
    ids = {}
    async with dbm.AsyncSessionLocal() as s:
        await s.execute(text("PRAGMA foreign_keys=OFF"))
        for key, model in (("reading", ReadingTest), ("listening", ListeningTest)):
            tid = f"{key[0]}-{uuid.uuid4().hex[:8]}"
            kw = dict(id=tid, title=f"{key} t", cefr_level="B2", **flags)
            if key == "reading":
                kw["total_questions"] = 35
            else:
                kw["sections"] = "[]"
            s.add(model(**kw)); ids[key] = tid
        await s.commit()
    return ids


async def test_catalog_returns_only_active_practice_tests(env):
    practice = await _mk_tests(is_practice=True, is_free=True, is_active=True)
    mock_only = await _mk_tests(is_practice=False, is_free=True, is_active=True)
    inactive = await _mk_tests(is_practice=True, is_free=True, is_active=False)

    data = (await env.get(f"{API}/practice/tests")).json()["items"]
    for skill in ("reading", "listening"):
        listed = {t["id"] for t in data[skill]}
        assert practice[skill] in listed
        assert mock_only[skill] not in listed
        assert inactive[skill] not in listed


@pytest.mark.parametrize("skill,prefix", [("reading", "reading-tests"), ("listening", "listening-tests")])
async def test_practice_attempt_not_allowed_on_mock_only_test(env, skill, prefix):
    user = await _mk_user()
    mock_only = await _mk_tests(is_practice=False, is_free=True, is_active=True)
    r = await env.post(f"{API}/{prefix}/{mock_only[skill]}/attempts", headers={"X-Test-User": str(user)})
    assert r.status_code == 404

    ok = await _mk_tests(is_practice=True, is_free=True, is_active=True)
    r = await env.post(f"{API}/{prefix}/{ok[skill]}/attempts", headers={"X-Test-User": str(user)})
    assert r.status_code == 200, r.text
