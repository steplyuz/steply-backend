"""Speaking practice audio yuklash: birinchi yuklash va qayta yozish (end-to-end, haqiqiy endpoint)."""
import io
import os
import tempfile
import uuid
from datetime import datetime, timedelta, timezone

_tmp = tempfile.mkdtemp()
os.environ["DATABASE_URL"] = f"sqlite+aiosqlite:///{_tmp}/speaking_upload.db"
os.environ["ENV"] = "local"

import pytest
import pytest_asyncio
from fastapi import Request
from httpx import ASGITransport, AsyncClient

from app.core import database as dbm
from app.core.database import init_db
from app.modules.auth.dependencies import get_current_user
from app.modules.auth.models import User, UserProfile, UserRole
from app.modules.billing.models import SubscriptionPlan, SubscriptionTier, UserSubscription

pytestmark = pytest.mark.asyncio
API = "/api/v1"
WEBM = b"\x1a\x45\xdf\xa3" + b"0" * 200  # EBML magic

SPEAKING_JSON = {
    "title": "Practice Upload",
    "is_practice": True,
    "is_free": False,
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
        plan = SubscriptionPlan(name="upload-plan", price=1, duration_days=30, tier=SubscriptionTier.PRO,
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


async def test_practice_upload_first_and_rerecord(env):
    client, ids = env
    admin_h = {"X-Test-User": ids["admin"]}
    student_h = {"X-Test-User": ids["student"]}

    r = await client.post(f"{API}/admin/speaking-tests", json=SPEAKING_JSON, headers=admin_h)
    assert r.status_code in (200, 201), r.text
    created = r.json()
    test_id = created["id"]
    qid = created["part_1_1"]["question_1_id"]
    part = created["part_1_1"]["external_id"]

    params = {"question_external_id": qid, "part_external_id": part}
    files = {"file": ("answer.webm", io.BytesIO(WEBM), "audio/webm")}

    first = await client.post(f"{API}/speaking-tests/{test_id}/practice/submit", params=params, files=files, headers=student_h)
    assert first.status_code == 200, first.text
    first_id = first.json()["id"]
    assert first.json().get("practice_attempt_id")

    files2 = {"file": ("answer2.webm", io.BytesIO(WEBM), "audio/webm")}
    second = await client.post(f"{API}/speaking-tests/{test_id}/practice/submit", params=params, files=files2, headers=student_h)
    assert second.status_code == 200, second.text
    # SQLite o'chirilgan ID ni qayta ishlatishi mumkin; yangi yozuv audio fayl bo'yicha farqlanadi.
    assert second.json()["audio_url"]
    async with dbm.AsyncSessionLocal() as s:
        from sqlalchemy import select
        from app.modules.tests.speaking.models import SpeakingSubmission
        rows = (await s.execute(select(SpeakingSubmission).where(
            SpeakingSubmission.part_external_id == part,
            SpeakingSubmission.question_external_id == qid,
        ))).scalars().all()
        assert len(rows) == 1
        assert rows[0].audio_storage_key != ""
