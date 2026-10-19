"""Speaking: is_demo bayrog'i va URINISH darajasidagi umumiy baholash."""
import os
import tempfile
import uuid

_tmp = tempfile.mkdtemp()
os.environ["DATABASE_URL"] = f"sqlite+aiosqlite:///{_tmp}/speaking_overall.db"
os.environ["ENV"] = "local"

import pytest
import pytest_asyncio
from fastapi import Request
from httpx import ASGITransport, AsyncClient

from app.core import database as dbm
from app.core.database import init_db
from app.modules.auth.dependencies import get_current_user
from app.modules.auth.models import User, UserProfile, UserRole
from app.modules.exams.mock.models import PracticeAttempt, PracticeAttemptStatus, SkillType
from app.modules.tests.speaking.models import SpeakingResult, SpeakingSubmission

pytestmark = pytest.mark.asyncio
API = "/api/v1"

SPEAKING_JSON = {
    "title": "Demo Speaking",
    "is_demo": True,
    "is_free": True,
    "part_1_1": {"question_1": "q1", "question_2": "q2", "question_3": "q3"},
    "part_1_2": {"image_1_url": "/static/a.png", "image_2_url": "/static/b.png",
                 "question_describe": "d", "question_2": "q2", "question_3": "q3"},
    "part_2": {"image_url": "/static/c.png", "question_1": "q1", "question_2": "q2", "question_3": "q3"},
    "part_3": {"topic": "t", "for_points": ["a", "b", "c"], "against_points": ["a", "b", "c"]},
}


async def _make_user(role: UserRole, name: str) -> uuid.UUID:
    async with dbm.AsyncSessionLocal() as s:
        u = User(global_role=role, is_active=True)
        s.add(u)
        await s.flush()
        s.add(UserProfile(user_id=u.id, full_name=name))
        await s.commit()
        return u.id


@pytest_asyncio.fixture(scope="module")
async def env():
    await init_db()
    from app.main import app

    admin_id = await _make_user(UserRole.ADMIN, "Admin")
    student_id = await _make_user(UserRole.STUDENT, "Student")

    async def _user(request: Request):
        async with dbm.AsyncSessionLocal() as s:
            return await s.get(User, uuid.UUID(request.headers["X-Test-User"]))

    app.dependency_overrides[get_current_user] = _user
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://localhost") as c:
        yield c, admin_id, student_id
    app.dependency_overrides.pop(get_current_user, None)
    await dbm.engine.dispose()


async def test_is_demo_roundtrip(env):
    c, admin_id, _ = env
    h = {"X-Test-User": str(admin_id)}
    res = await c.post(f"{API}/admin/speaking-tests", headers=h, json=SPEAKING_JSON)
    assert res.status_code == 201, res.text
    body = res.json()
    assert body["is_demo"] is True
    tid = body["id"]

    listing = (await c.get(f"{API}/admin/speaking-tests", headers=h)).json()
    assert next(t for t in listing if t["id"] == tid)["is_demo"] is True

    upd = await c.put(f"{API}/admin/speaking-tests/{tid}", headers=h, json={"is_demo": False})
    assert upd.status_code == 200, upd.text
    assert upd.json()["is_demo"] is False

    default = await c.post(f"{API}/admin/speaking-tests", headers=h,
                           json={**SPEAKING_JSON, "title": "Plain", "is_demo": False})
    assert default.json()["is_demo"] is False


async def test_one_attempt_is_graded_once_and_listed_once(env):
    c, admin_id, student_id = env
    h = {"X-Test-User": str(admin_id)}
    test_id = (await c.post(f"{API}/admin/speaking-tests", headers=h,
                            json={**SPEAKING_JSON, "title": "Practice"})).json()["id"]

    async with dbm.AsyncSessionLocal() as s:
        attempt = PracticeAttempt(user_id=student_id, skill=SkillType.SPEAKING, test_id=test_id,
                                  mode="FULL_PRACTICE", status=PracticeAttemptStatus.IN_PROGRESS)
        s.add(attempt)
        await s.flush()
        attempt_id = attempt.id
        for i in range(1, 4):
            s.add(SpeakingSubmission(
                user_id=student_id, speaking_test_id=test_id, practice_attempt_id=attempt_id,
                part_external_id="part-1.1", question_external_id=f"q-{i}",
                audio_url=f"/speaking-tests/submissions/x{i}/audio", duration_seconds=20, status="pending"))
        await s.commit()

    pending = (await c.get(f"{API}/admin/speaking-tests/pending-attempts", headers=h)).json()
    mine = [p for p in pending if p["practice_attempt_id"] == attempt_id]
    assert len(mine) == 1
    assert mine[0]["recordings_count"] == 3 and mine[0]["total_duration_seconds"] == 60

    res = await c.post(f"{API}/admin/speaking-tests/submissions/{mine[0]['submission_id']}/result",
                       headers=h, json={"overall_raw": 24, "comment": "ok"})
    assert res.status_code == 200, res.text

    async with dbm.AsyncSessionLocal() as s:
        from sqlalchemy import select
        subs = (await s.execute(select(SpeakingSubmission).where(
            SpeakingSubmission.practice_attempt_id == attempt_id))).scalars().all()
        assert {x.status for x in subs} == {"checked"}
        results = (await s.execute(select(SpeakingResult).where(
            SpeakingResult.submission_id.in_([x.id for x in subs])))).scalars().all()
        assert len(results) == 3 and len({r.overall_band for r in results}) == 1
        assert (await s.get(PracticeAttempt, attempt_id)).status == PracticeAttemptStatus.SUBMITTED

    pending_after = (await c.get(f"{API}/admin/speaking-tests/pending-attempts", headers=h)).json()
    assert not [p for p in pending_after if p["practice_attempt_id"] == attempt_id]

    mine_results = (await c.get(f"{API}/speaking-tests/me/results",
                                headers={"X-Test-User": str(student_id)})).json()
    assert len(mine_results) == 1  # 3 ta yozuv, lekin bitta umumiy natija
