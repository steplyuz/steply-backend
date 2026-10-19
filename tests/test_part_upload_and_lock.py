"""Part-only yuklash, partlar tartibi va barcha test turlari uchun /lock regressiyalari."""
import os
import tempfile
import uuid

_tmp = tempfile.mkdtemp()
os.environ["DATABASE_URL"] = f"sqlite+aiosqlite:///{_tmp}/part_lock.db"
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


def reading_part(first: int, size: int, title: str) -> dict:
    qs = [{"question_number": n, "type": "GAP_FILL", "text": f"Gap {n}: ____",
           "correct_answer": [f"w{n}"], "word_limit": 1} for n in range(first, first + size)]
    return {"title": title, "passage": "Matn. " * 10, "questions": qs}


FULL_LAYOUT = [(1, 6), (7, 8), (15, 6), (21, 9), (30, 6)]  # 3-part 7-14 / 15-20 bilan mos


def full_parts():
    return [reading_part(1, 6, "Part 1"), reading_part(7, 8, "Part 2"), reading_part(15, 6, "Part 3"),
            reading_part(21, 9, "Part 4"), reading_part(30, 6, "Part 5")]


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


# ----------------------------- READING -----------------------------
async def test_reading_shuffled_parts_are_sorted_and_lockable(admin):
    """Foydalanuvchi JSON'i: Part 5,4,2,3,1 tartibida — endi 1..5 ga keltiriladi va lock bo'ladi."""
    parts = full_parts()
    shuffled = [parts[4], parts[3], parts[1], parts[2], parts[0]]
    r = await admin.post(f"{API}/admin/reading-tests",
                         json={"title": "Shuffled", "cefr_level": "B1", "parts": shuffled})
    assert r.status_code == 201, r.text
    body = r.json()
    assert [p["title"] for p in body["parts"]] == [f"Part {i}" for i in range(1, 6)]
    assert body["is_practice"] is False
    lock = (await admin.post(f"{API}/admin/reading-tests/{body['id']}/lock")).json()
    assert lock["success"] is True, lock

    # lock'dan keyin tahrirlash/o'chirish mumkin emas
    put = await admin.put(f"{API}/admin/reading-tests/{body['id']}", json={"title": "x"})
    assert put.status_code == 409


async def test_reading_single_part_upload_and_lock(admin):
    r = await admin.post(f"{API}/admin/reading-tests",
                         json={"title": "Only Part 3", "cefr_level": "B1", "parts": [reading_part(15, 6, "Part 3")]})
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["is_practice"] is True
    lock = (await admin.post(f"{API}/admin/reading-tests/{body['id']}/lock")).json()
    assert lock["success"] is True, lock


async def test_reading_single_part_update_works(admin):
    r = await admin.post(f"{API}/admin/reading-tests",
                         json={"title": "Part 1", "cefr_level": "B1", "parts": [reading_part(1, 6, "Part 1")]})
    tid = r.json()["id"]
    put = await admin.put(f"{API}/admin/reading-tests/{tid}", json={"parts": [reading_part(1, 5, "Part 1")]})
    assert put.status_code == 200, put.text


async def test_reading_gap_in_numbers_rejected_on_lock(admin):
    bad = reading_part(1, 6, "Part 1")
    bad["questions"].pop(2)
    r = await admin.post(f"{API}/admin/reading-tests", json={"title": "Gap", "cefr_level": "B1", "parts": [bad]})
    assert r.status_code == 422  # uzluksiz emas — yuklashdayoq aniq xabar
    assert "uzluksiz" in r.text


async def test_reading_full_test_wrong_numbers_has_clear_error(admin):
    parts = full_parts()
    parts[4] = reading_part(31, 6, "Part 5")  # 31-36
    r = await admin.post(f"{API}/admin/reading-tests", json={"title": "Bad", "cefr_level": "B1", "parts": parts})
    assert r.status_code == 422
    assert "Part 5" in r.text and "30-35" in r.text


# ----------------------------- LISTENING -----------------------------
async def test_listening_single_part_upload_and_update(admin):
    qs = [{"question_number": n, "type": "MULTIPLE_CHOICE", "question": f"Q{n}", "correct_answer": "A",
           "options": [{"label": "A", "value": "a"}, {"label": "B", "value": "b"}]} for n in range(15, 19)]
    part = {"part_number": 3, "title": "Listening part 3", "questions": qs}
    r = await admin.post(f"{API}/admin/listening-tests",
                         json={"title": "L3", "cefr_level": "B1", "parts": [part]})
    assert r.status_code == 201, r.text
    assert r.json()["is_practice"] is True
    put = await admin.put(f"{API}/admin/listening-tests/{r.json()['id']}", json={"title": "L3 v2", "parts": [part]})
    assert put.status_code == 200, put.text


# ----------------------------- WRITING -----------------------------
def writing(tasks, **extra):
    return {"title": "Writing test", "cefr_level": "B2", "duration_minutes": 60, "tasks": tasks, **extra}


T1 = {"part_number": 1, "sub_part": 1, "topic": "Informal letter", "instruction": "Write a letter."}
T12 = {"part_number": 1, "sub_part": 2, "topic": "Formal letter", "instruction": "Write a formal letter."}
T2 = {"part_number": 2, "topic": "Essay", "instruction": "Write an essay."}


async def test_writing_lock_full_and_blocks_edit(admin):
    r = await admin.post(f"{API}/admin/writing-tests", json=writing([T1, T12, T2], is_practice=False))
    assert r.status_code == 201, r.text
    eid = r.json()["id"]
    lock = (await admin.post(f"{API}/admin/writing-tests/{eid}/lock")).json()
    assert lock["success"] is True, lock
    assert (await admin.put(f"{API}/admin/writing-tests/{eid}", json={"title": "New title"})).status_code == 409
    # Lock qilingan, lekin natijasi yo'q test o'chiriladi (edit esa 409 bo'lib qoladi)
    assert (await admin.delete(f"{API}/admin/writing-tests/{eid}")).status_code == 204


async def test_writing_full_missing_part_rejected_but_part_only_ok(admin):
    r = await admin.post(f"{API}/admin/writing-tests", json=writing([T1], is_practice=False))
    eid = r.json()["id"]
    lock = (await admin.post(f"{API}/admin/writing-tests/{eid}/lock")).json()
    assert lock["success"] is False and "'2'" in lock["errors"][0]["message"] and "'1.2'" in lock["errors"][0]["message"]

    r2 = await admin.post(f"{API}/admin/writing-tests", json=writing([T2], is_practice=True))
    lock2 = (await admin.post(f"{API}/admin/writing-tests/{r2.json()['id']}/lock")).json()
    assert lock2["success"] is True, lock2


# ----------------------------- SPEAKING -----------------------------
P3 = {"topic": "Citizens should vote.", "for_points": ["a", "b", "c"], "against_points": ["d", "e", "f"]}
P11 = {"question_1": "Where do you live?", "question_2": "What do you do?", "question_3": "Music?"}


async def test_speaking_part_only_upload_and_lock(admin):
    r = await admin.post(f"{API}/admin/speaking-tests", json={"title": "S3", "part_3": P3})
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["is_practice"] is True and body["part_3"] and not body["part_1_1"]
    sid = body["id"]
    lock = (await admin.post(f"{API}/admin/speaking-tests/{sid}/lock")).json()
    assert lock["success"] is True, lock
    assert (await admin.put(f"{API}/admin/speaking-tests/{sid}", json={"title": "n"})).status_code == 409
    # Lock qilingan, lekin yozuvi/urinishi yo'q test o'chiriladi
    assert (await admin.delete(f"{API}/admin/speaking-tests/{sid}")).status_code == 200  # router 200 + detail qaytaradi


async def test_speaking_explicit_full_requires_all_parts(admin):
    r = await admin.post(f"{API}/admin/speaking-tests",
                         json={"title": "S", "is_practice": False, "part_1_1": P11, "part_3": P3})
    assert r.status_code == 422