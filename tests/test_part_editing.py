"""Partlarni alohida tahrirlash: speaking (1.1/1.2/2/3) va writing (topshiriq bo'yicha)."""
import os
import tempfile
import uuid
from datetime import datetime, timedelta, timezone

_tmp = tempfile.mkdtemp()
os.environ["DATABASE_URL"] = f"sqlite+aiosqlite:///{_tmp}/part_editing.db"
os.environ["ENV"] = "local"

import pytest
import pytest_asyncio
from fastapi import Request
from httpx import ASGITransport, AsyncClient

from app.core import database as dbm
from app.core.database import init_db
from app.modules.auth.dependencies import get_current_user
from app.modules.auth.models import User, UserProfile, UserRole

pytestmark = pytest.mark.asyncio
API = "/api/v1"

SPEAKING_JSON = {
    "title": "Part edit", "is_practice": True, "is_free": False,
    "part_1_1": {"question_1": "q1", "question_2": "q2", "question_3": "q3"},
    "part_1_2": {"image_1_url": "/static/a.png", "image_2_url": "/static/b.png",
                 "question_describe": "d", "question_2": "q2", "question_3": "q3"},
    "part_2": {"image_url": "/static/c.png", "question_1": "q1", "question_2": "q2", "question_3": "q3"},
    "part_3": {"topic": "t", "for_points": ["a", "b", "c"], "against_points": ["a", "b", "c"]},
}
T1 = {"part_number": 1, "sub_part": 1, "topic": "Informal letter", "instruction": "Write a letter."}
T12 = {"part_number": 1, "sub_part": 2, "topic": "Formal letter", "instruction": "Write a formal letter."}
T2 = {"part_number": 2, "topic": "Essay", "instruction": "Write an essay."}


@pytest_asyncio.fixture(scope="module")
async def env():
    await init_db()
    from app.main import app
    async with dbm.AsyncSessionLocal() as s:
        admin = User(global_role=UserRole.ADMIN, is_active=True); s.add(admin); await s.flush()
        s.add(UserProfile(user_id=admin.id, full_name="Admin"))
        await s.commit()
        admin_id = str(admin.id)

    async def _user(request: Request):
        async with dbm.AsyncSessionLocal() as s:
            return await s.get(User, uuid.UUID(request.headers["X-Test-User"]))

    app.dependency_overrides[get_current_user] = _user
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://localhost") as c:
        yield c, {"X-Test-User": admin_id}
    app.dependency_overrides.clear()


async def test_speaking_single_part_update_keeps_other_parts(env):
    client, h = env
    created = (await client.post(f"{API}/admin/speaking-tests", json=SPEAKING_JSON, headers=h)).json()
    tid = created["id"]
    before_p11 = created["part_1_1"]

    new_p2 = dict(SPEAKING_JSON["part_2"], question_1="Yangi savol 1")
    r = await client.put(f"{API}/admin/speaking-tests/{tid}/parts/2", json=new_p2, headers=h)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["part_2"]["question_1"] == "Yangi savol 1"
    assert body["part_1_1"]["question_1_id"] == before_p11["question_1_id"]

    bad = await client.put(f"{API}/admin/speaking-tests/{tid}/parts/9", json=new_p2, headers=h)
    assert bad.status_code == 404


async def test_writing_single_task_update_and_add(env):
    client, h = env
    created = (await client.post(f"{API}/admin/writing-tests", headers=h, json={
        "title": "Part writing", "cefr_level": "B2", "duration_minutes": 60, "tasks": [T1, T12, T2]})).json()
    eid = created["id"]
    refs = [t["id"] for t in created["tasks"]]
    assert len(refs) == 3
    t12_ref = refs[1]
    t1_topic = created["tasks"][0]["topic"]

    r = await client.put(f"{API}/admin/writing-tests/{eid}/tasks/{t12_ref}",
                         json=dict(T12, topic="Updated formal letter topic"), headers=h)
    assert r.status_code == 200, r.text
    tasks = {t["id"]: t for t in r.json()["tasks"]}
    assert tasks[t12_ref]["topic"] == "Updated formal letter topic"
    assert tasks[refs[0]]["topic"] == t1_topic, "boshqa topshiriq o'zgarmasligi kerak"
    assert len(tasks) == 3

    # Takroriy qism/sub-part (part 2, sub yo'q) — rad etilishi kerak, topshiriqlar soni o'zgarmaydi
    dup = await client.post(f"{API}/admin/writing-tests/{eid}/tasks", json=dict(T2, topic="Second essay"), headers=h)
    assert dup.status_code in (409, 422), dup.text
    from sqlalchemy import func, select
    from app.modules.tests.writing.models import WritingTask
    async with dbm.AsyncSessionLocal() as s:
        count = await s.scalar(select(func.count()).select_from(WritingTask).where(WritingTask.exam_id == eid))
    assert count == 3

    # Yangi, takrorlanmas qism qo'shiladi
    add = await client.post(f"{API}/admin/writing-tests/{eid}/tasks",
                            json={"part_number": 1, "sub_part": 3, "topic": "Extra task topic",
                                  "instruction": "Extra instruction."}, headers=h)
    assert add.status_code in (200, 201), add.text
