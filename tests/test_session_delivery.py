"""Seanslar: delivery_mode (PAPER/COMPUTER) va admin-only boshqaruv (haqiqiy SQLite)."""
import os
import tempfile
import uuid
from datetime import datetime, timedelta, timezone

_tmp = tempfile.mkdtemp()
os.environ["DATABASE_URL"] = f"sqlite+aiosqlite:///{_tmp}/test_delivery.db"

import pytest
import pytest_asyncio
from fastapi import FastAPI, Request
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text

from app.core import database as dbm
from app.core.database import init_db
from app.modules.auth.dependencies import get_current_user
from app.modules.auth.models import User, UserProfile, UserRole
from app.modules.exams.mock.admin_router import router as mock_admin_router
from app.modules.exams.mock.models import MockExam

pytestmark = pytest.mark.asyncio
BASE = "/api/v1/admin/mock-exams/sessions"


@pytest_asyncio.fixture(scope="module")
async def env():
    await init_db()
    app = FastAPI()
    app.state.limiter = __import__("app.core.rate_limit", fromlist=["limiter"]).limiter
    app.include_router(mock_admin_router, prefix="/api/v1")

    async def _user(request: Request):
        async with dbm.AsyncSessionLocal() as s:
            return await s.get(User, uuid.UUID(request.headers["X-Test-User"]))

    app.dependency_overrides[get_current_user] = _user
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        yield c
    await dbm.engine.dispose()


async def mk_user(role=UserRole.STUDENT):
    async with dbm.AsyncSessionLocal() as s:
        u = User(global_role=role, is_active=True)
        s.add(u); await s.flush()
        s.add(UserProfile(user_id=u.id, full_name="T"))
        await s.commit()
        return u.id


async def mk_exam():
    async with dbm.AsyncSessionLocal() as s:
        await s.execute(text("PRAGMA foreign_keys=OFF"))  # skill jadvallarisiz test
        e = MockExam(id=f"mx-{uuid.uuid4().hex[:8]}", title="Mock",
                     reading_id="r", listening_id="l", writing_id="w", speaking_id="s")
        s.add(e); await s.commit()
        return e.id


def H(uid):
    return {"X-Test-User": str(uid)}


def body(exam_id, **kw):
    return {"mock_exam_id": exam_id, "exam_date": (datetime.now(timezone.utc) + timedelta(days=5)).isoformat(),
            "location_address": "Tashkent, Amir Temur 1", **kw}


async def test_default_is_paper_and_computer_is_stored(env):
    admin = await mk_user(UserRole.ADMIN)
    eid = await mk_exam()
    h = H(admin)
    r = await env.post(BASE, json=body(eid), headers=h)
    assert r.status_code == 201 and r.json()["delivery_mode"] == "PAPER"
    r = await env.post(BASE, json=body(eid, delivery_mode="COMPUTER"), headers=h)
    assert r.status_code == 201 and r.json()["delivery_mode"] == "COMPUTER"
    r = await env.post(BASE, json=body(eid, delivery_mode="CD"), headers=h)
    assert r.status_code == 422


async def test_online_session_allowed_for_admin_and_patch_works(env):
    admin = await mk_user(UserRole.ADMIN)
    eid = await mk_exam()
    h = H(admin)
    r = await env.post(BASE, json=body(eid, location_address="Online", delivery_mode="COMPUTER"), headers=h)
    assert r.status_code == 201, r.text
    sid = r.json()["id"]
    r = await env.patch(f"{BASE}/{sid}", json={"capacity": 5}, headers=h)
    assert r.status_code == 200


async def test_students_cannot_manage_sessions(env):
    admin = await mk_user(UserRole.ADMIN)
    student = await mk_user(UserRole.STUDENT)
    eid = await mk_exam()
    sid = (await env.post(BASE, json=body(eid), headers=H(admin))).json()["id"]
    t = H(student)
    assert (await env.get(BASE, headers=t)).status_code == 403
    assert (await env.post(BASE, json=body(eid), headers=t)).status_code == 403
    assert (await env.patch(f"{BASE}/{sid}", json={"capacity": 5}, headers=t)).status_code == 403
    assert (await env.delete(f"{BASE}/{sid}", headers=t)).status_code == 403
