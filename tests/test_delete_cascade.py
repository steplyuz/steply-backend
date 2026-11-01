"""Speaking testni o'chirganda bog'liq hamma natijalar (yozuvlar, urinishlar, AI jobs/baholashlar) ham o'chadi."""
import io
import os
import tempfile
import uuid
from datetime import datetime, timedelta, timezone

_tmp = tempfile.mkdtemp()
os.environ["DATABASE_URL"] = f"sqlite+aiosqlite:///{_tmp}/delete_cascade.db"
os.environ["ENV"] = "local"

import pytest
import pytest_asyncio
from fastapi import Request
from httpx import ASGITransport, AsyncClient
from sqlalchemy import func, select

from app.core import database as dbm
from app.core.database import init_db
from app.modules.auth.dependencies import get_current_user
from app.modules.auth.models import User, UserProfile, UserRole
from app.modules.billing.models import SubscriptionPlan, SubscriptionTier, UserSubscription
from app.modules.exams.mock.models import PracticeAttempt
from app.modules.tests.speaking.ai_models import SpeakingAIEvaluation, SpeakingAIJob
from app.modules.tests.speaking.models import SpeakingSubmission

pytestmark = pytest.mark.asyncio
API = "/api/v1"
WEBM = b"\x1a\x45\xdf\xa3" + b"0" * 200
SPEAKING_JSON = {
    "title": "Cascade", "is_practice": True, "is_free": False,
    "part_1_1": {"question_1": "q1", "question_2": "q2", "question_3": "q3"},
    "part_1_2": {"image_1_url": "/static/a.png", "image_2_url": "/static/b.png",
                 "question_describe": "d", "question_2": "q2", "question_3": "q3"},
    "part_2": {"image_url": "/static/c.png", "question_1": "q1", "question_2": "q2", "question_3": "q3"},
    "part_3": {"topic": "t", "for_points": ["a", "b", "c"], "against_points": ["a", "b", "c"]},
}


@pytest_asyncio.fixture(scope="module")
async def env():
    await init_db()
    from app.main import app
    async with dbm.AsyncSessionLocal() as s:
        admin = User(global_role=UserRole.ADMIN, is_active=True); s.add(admin); await s.flush()
        s.add(UserProfile(user_id=admin.id, full_name="Admin"))
        student = User(global_role=UserRole.STUDENT, is_active=True); s.add(student); await s.flush()
        s.add(UserProfile(user_id=student.id, full_name="Student"))
        plan = SubscriptionPlan(name="cascade-plan", price=1, duration_days=30, tier=SubscriptionTier.PRO,
                                ai_limit=0, exam_limit=0, included_skills=["READING", "LISTENING", "WRITING", "SPEAKING"])
        s.add(plan); await s.flush()
        now = datetime.now(timezone.utc)
        s.add(UserSubscription(user_id=student.id, plan_id=plan.id,
                               start_date=now - timedelta(days=1), end_date=now + timedelta(days=30), is_active=True))
        await s.commit()
        ids = {"admin": str(admin.id), "student": str(student.id)}

    async def _user(request: Request):
        async with dbm.AsyncSessionLocal() as s:
            return await s.get(User, uuid.UUID(request.headers["X-Test-User"]))

    app.dependency_overrides[get_current_user] = _user
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://localhost") as c:
        yield c, ids
    app.dependency_overrides.clear()


async def test_deleting_speaking_test_removes_all_related_results(env):
    client, ids = env
    admin_h = {"X-Test-User": ids["admin"]}
    student_h = {"X-Test-User": ids["student"]}

    created = (await client.post(f"{API}/admin/speaking-tests", json=SPEAKING_JSON, headers=admin_h)).json()
    test_id = created["id"]
    qid = created["part_1_1"]["question_1_id"]
    part = created["part_1_1"]["external_id"]
    up = await client.post(f"{API}/speaking-tests/{test_id}/practice/submit",
                           params={"question_external_id": qid, "part_external_id": part},
                           files={"file": ("a.webm", io.BytesIO(WEBM), "audio/webm")}, headers=student_h)
    assert up.status_code == 200, up.text
    attempt_id = up.json()["practice_attempt_id"]

    # Natija (AI baholash) yaratilgan holat
    async with dbm.AsyncSessionLocal() as s:
        job = SpeakingAIJob(practice_attempt_id=attempt_id, user_id=uuid.UUID(ids["student"]), status="done")
        s.add(job); await s.flush()
        s.add(SpeakingAIEvaluation(job_id=job.id, part="1.1", score=3, max_score=5, level="A2",
                                   payload={"feedback_uz": "ok"}))
        await s.commit()

    r = await client.delete(f"{API}/admin/speaking-tests/{test_id}", headers=admin_h)
    assert r.status_code == 200, r.text

    async with dbm.AsyncSessionLocal() as s:
        assert await s.scalar(select(func.count()).select_from(SpeakingSubmission)) == 0
        assert await s.scalar(select(func.count()).select_from(PracticeAttempt)) == 0
        assert await s.scalar(select(func.count()).select_from(SpeakingAIJob)) == 0
        assert await s.scalar(select(func.count()).select_from(SpeakingAIEvaluation)) == 0
