"""Listening part mini-testlari: yaratish, idempotentlik, audio rejimlari."""
import os
import tempfile
import uuid

_tmp = tempfile.mkdtemp()
os.environ["DATABASE_URL"] = f"sqlite+aiosqlite:///{_tmp}/listening_mini.db"
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


def _part(n, first_q, count, audio, start=None, end=None):
    return {
        "part_number": n, "title": f"Part {n}", "audio_url": audio,
        "audio_start_seconds": start, "audio_end_seconds": end,
        "questions": [
            {"question_number": first_q + i, "type": "GAP_FILL", "question": f"q{first_q + i}", "correct_answer": "x"}
            for i in range(count)
        ],
    }


def _exam(mode, title):
    if mode == "PARTS":
        parts = [_part(1, 1, 8, "/a1.mp3"), _part(2, 9, 6, "/a2.mp3")]
        extra = {"audio_mode": "PARTS"}
    else:
        parts = [_part(1, 1, 8, "", 0, 100), _part(2, 9, 6, "", 100, 190)]
        extra = {"audio_mode": "EXAM", "audio_url": "/shared.mp3"}
    return {"title": title, "level": "B2", "is_practice": True, "is_free": True, "parts": parts, **extra}


@pytest_asyncio.fixture(scope="module")
async def env():
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
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://localhost") as c:
        yield c, {"X-Test-User": str(admin_id)}
    app.dependency_overrides.pop(get_current_user, None)
    await dbm.engine.dispose()


async def test_parts_mode_creates_one_mini_per_part_and_is_idempotent(env):
    c, h = env
    res = await c.post(f"{API}/admin/listening-tests", headers=h, json=_exam("PARTS", "Full A"))
    assert res.status_code == 201, res.text
    exam_id = res.json()["id"]

    first = await c.post(f"{API}/admin/listening-tests/{exam_id}/part-mini-tests", headers=h)
    assert first.status_code == 200, first.text
    body = first.json()
    assert body["created_count"] == 2 and body["total_count"] == 2
    assert [t["practice_part_number"] for t in body["tests"]] == [1, 2]

    again = (await c.post(f"{API}/admin/listening-tests/{exam_id}/part-mini-tests", headers=h)).json()
    assert again["created_count"] == 0 and again["total_count"] == 2
    assert [t["id"] for t in again["tests"]] == [t["id"] for t in body["tests"]]

    mini = (await c.get(f"{API}/admin/listening-tests/{body['tests'][1]['id']}", headers=h)).json()
    assert mini["parent_test_id"] == exam_id and mini["practice_part_number"] == 2
    assert mini["is_practice"] is True and mini["total_questions"] == 6
    assert len(mini["parts"]) == 1 and mini["parts"][0]["audio_url"] == "/a2.mp3"
    # savol raqamlari asl holicha (instruction/gap raqamlari buzilmasligi uchun)
    assert [q["question_number"] for q in mini["parts"][0]["questions"]] == [9, 10, 11, 12, 13, 14]

    # parent partlari qayta yaratilganda mini-testlar saqlanib qoladi
    upd = await c.put(f"{API}/admin/listening-tests/{exam_id}", headers=h,
                      json={"parts": _exam("PARTS", "x")["parts"]})
    assert upd.status_code == 200, upd.text
    still = (await c.post(f"{API}/admin/listening-tests/{exam_id}/part-mini-tests", headers=h)).json()
    assert still["created_count"] == 0 and still["total_count"] == 2


async def test_exam_audio_mode_keeps_shared_audio_and_segment(env):
    c, h = env
    exam_id = (await c.post(f"{API}/admin/listening-tests", headers=h, json=_exam("EXAM", "Full B"))).json()["id"]
    body = (await c.post(f"{API}/admin/listening-tests/{exam_id}/part-mini-tests", headers=h)).json()
    mini = (await c.get(f"{API}/admin/listening-tests/{body['tests'][1]['id']}", headers=h)).json()
    assert mini["audio_mode"] == "EXAM" and mini["audio_url"] == "/shared.mp3"
    part = mini["parts"][0]
    assert part["audio_start_seconds"] == 100 and part["audio_end_seconds"] == 190


async def test_mini_of_mini_and_missing_audio_are_rejected(env):
    c, h = env
    exam_id = (await c.post(f"{API}/admin/listening-tests", headers=h, json=_exam("PARTS", "Full C"))).json()["id"]
    mini_id = (await c.post(f"{API}/admin/listening-tests/{exam_id}/part-mini-tests", headers=h)).json()["tests"][0]["id"]
    assert (await c.post(f"{API}/admin/listening-tests/{mini_id}/part-mini-tests", headers=h)).status_code == 400

    draft = _exam("PARTS", "No audio")
    for p in draft["parts"]:
        p["audio_url"] = ""
    draft["audio_mode"] = "EXAM"
    draft["audio_url"] = "/shared.mp3"
    for p in draft["parts"]:
        p["audio_start_seconds"] = p["audio_end_seconds"] = None
    bad = (await c.post(f"{API}/admin/listening-tests", headers=h, json=draft)).json()["id"]
    res = await c.post(f"{API}/admin/listening-tests/{bad}/part-mini-tests", headers=h)
    assert res.status_code == 400
