"""PUT /admin/writing-tests/{id} — tuzatilgan 2025 JSON bilan yangilash (500 regressiyasi)."""
import json
import os
import tempfile
import uuid

_tmp = tempfile.mkdtemp()
os.environ["DATABASE_URL"] = f"sqlite+aiosqlite:///{_tmp}/writing_update.db"
os.environ["ENV"] = "local"

import pytest
import pytest_asyncio
from fastapi import Request
from httpx import ASGITransport, AsyncClient

from app.core import database as dbm
from app.core.database import init_db
from app.core.rate_limit import limiter
from app.modules.auth.dependencies import get_current_user
from app.modules.auth.models import User, UserProfile, UserRole

pytestmark = pytest.mark.asyncio
API = "/api/v1"
FIXED = "/mnt/user-data/outputs/writing-2025-tuzatilgan.json"


@pytest_asyncio.fixture(scope="module")
async def admin():
    await init_db()
    from app.main import app
    async with dbm.AsyncSessionLocal() as s:
        u = User(global_role=UserRole.ADMIN, is_active=True)
        s.add(u)
        await s.flush()
        s.add(UserProfile(user_id=u.id, full_name="Admin"))
        await s.commit()
        admin_id = u.id

    async def _user(request: Request):
        async with dbm.AsyncSessionLocal() as s:
            return await s.get(User, uuid.UUID(request.headers["X-Test-User"]))

    app.dependency_overrides[get_current_user] = _user
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://localhost",
                           headers={"X-Test-User": str(admin_id)}) as c:
        yield c
    app.dependency_overrides.pop(get_current_user, None)
    await dbm.engine.dispose()


@pytest.fixture(autouse=True)
def _reset_limiter():
    limiter.reset()


async def test_put_fixed_2025_json_updates_exam(admin):
    body = {"title": "Seed", "cefr_level": "B2", "duration_minutes": 60, "tasks": [
        {"part_number": 1, "sub_part": 1, "topic": "Informal email", "instruction": "Write an email.", "format_id": None},
        {"part_number": 1, "sub_part": 2, "topic": "Formal email", "instruction": "Write a letter."},
        {"part_number": 2, "topic": "Essay", "instruction": "Write an essay."},
    ]}
    r = await admin.post(f"{API}/admin/writing-tests", json=body)
    assert r.status_code == 201, r.text
    eid = r.json()["id"]

    fixed = json.load(open(FIXED, encoding="utf-8"))
    r2 = await admin.put(f"{API}/admin/writing-tests/{eid}", json=fixed)
    assert r2.status_code == 200, r2.text[:1500]
    out = r2.json()
    tasks = sorted(out["tasks"], key=lambda t: (t["part_number"], t["sub_part"] or 0))
    assert [(t["part_number"], t["sub_part"]) for t in tasks] == [(1, 1), (1, 2), (2, None)]
    fmts = [(t["format"]["cefr_level"], t["format"]["style"]) for t in tasks]
    assert fmts == [("B1", "INFORMAL"), ("B2", "FORMAL"), ("C1", "POST")], fmts


async def test_put_with_mismatched_format_id_is_422_not_500(admin):
    body = {"title": "Seed2", "cefr_level": "B2", "duration_minutes": 60, "tasks": [
        {"part_number": 1, "sub_part": 1, "topic": "Informal email", "instruction": "Write an email."},
        {"part_number": 1, "sub_part": 2, "topic": "Formal email", "instruction": "Write a letter."},
        {"part_number": 2, "topic": "Essay", "instruction": "Write an essay."},
    ]}
    r = await admin.post(f"{API}/admin/writing-tests", json=body)
    eid = r.json()["id"]
    # 1-bosqichda yaratilgan avto-format (B1/INFORMAL) ni 1.2 ga noto'g'ri bog'laymiz
    tasks = [dict(t) for t in r.json()["tasks"]]
    bad_format_id = tasks[0]["format"]["id"]
    payload = {"tasks": [
        {"part_number": 1, "sub_part": 1, "topic": "Informal email", "instruction": "Write an email."},
        {"part_number": 1, "sub_part": 2, "topic": "Formal email", "instruction": "Write a letter.", "format_id": bad_format_id},
        {"part_number": 2, "topic": "Essay", "instruction": "Write an essay."},
    ]}
    r2 = await admin.put(f"{API}/admin/writing-tests/{eid}", json=payload)
    assert r2.status_code == 422, r2.text[:500]
