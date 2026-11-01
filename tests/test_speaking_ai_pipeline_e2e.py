"""Speaking AI baholash: yuklash -> baholash boshlash -> natija (STT va GPT mock qilingan)."""
import io
import json
import os
import tempfile
import types
import uuid
from datetime import datetime, timedelta, timezone

_tmp = tempfile.mkdtemp()
os.environ["DATABASE_URL"] = f"sqlite+aiosqlite:///{_tmp}/speaking_ai_e2e.db"
os.environ["ENV"] = "local"

import pytest
import pytest_asyncio
from fastapi import Request
from httpx import ASGITransport, AsyncClient

from app.core import database as dbm
from app.core.config import settings
from app.core.database import init_db
from app.modules.auth.dependencies import get_current_user
from app.modules.auth.models import User, UserProfile, UserRole
from app.modules.billing.models import SubscriptionPlan, SubscriptionTier, UserSubscription
from app.modules.tests.speaking import ai_grader, transcription

pytestmark = pytest.mark.asyncio
API = "/api/v1"
WEBM = b"\x1a\x45\xdf\xa3" + b"0" * 200

SPEAKING_JSON = {
    "title": "AI Pipeline", "is_practice": True, "is_free": False,
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
        plan = SubscriptionPlan(name="ai-pipe", price=1, duration_days=30, tier=SubscriptionTier.PRO,
                                ai_limit=5, ai_enabled=True, exam_limit=0,
                                included_skills=["READING", "LISTENING", "WRITING", "SPEAKING"])
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


async def test_ai_pipeline_runs_to_done(env, monkeypatch):
    client, ids = env
    admin_h = {"X-Test-User": ids["admin"]}
    student_h = {"X-Test-User": ids["student"]}

    async def fake_transcribe(path):
        words = [{"w": "I", "s": 0.0, "e": 0.3}, {"w": "like", "s": 0.4, "e": 0.8}, {"w": "tea", "s": 1.0, "e": 1.4}]
        return {"text": "I like tea", "words": words, "duration": 2.0}

    class _Completions:
        async def create(self, **_kw):
            payload = {"score": 3, "questions": [{"n": 1, "on_topic": True}],
                       "feedback_uz": "Yaxshi, lekin grammatikada xatolar bor."}
            msg = types.SimpleNamespace(content=json.dumps(payload))
            return types.SimpleNamespace(choices=[types.SimpleNamespace(message=msg)])

    fake_client = types.SimpleNamespace(chat=types.SimpleNamespace(completions=_Completions()))
    monkeypatch.setattr(transcription, "transcribe", fake_transcribe)
    monkeypatch.setattr(ai_grader, "_client", fake_client)
    monkeypatch.setattr(settings, "SPEAKING_AI_ENABLED", True)
    monkeypatch.setattr(settings, "OPENAI_API_KEY", "sk-test")

    created = (await client.post(f"{API}/admin/speaking-tests", json=SPEAKING_JSON, headers=admin_h)).json()
    test_id = created["id"]
    qid = created["part_1_1"]["question_1_id"]
    part = created["part_1_1"]["external_id"]

    up = await client.post(f"{API}/speaking-tests/{test_id}/practice/submit",
                           params={"question_external_id": qid, "part_external_id": part},
                           files={"file": ("a.webm", io.BytesIO(WEBM), "audio/webm")}, headers=student_h)
    assert up.status_code == 200, up.text
    attempt_id = up.json()["practice_attempt_id"]
    assert attempt_id

    start = await client.post(f"{API}/speaking-tests/practice/attempts/{attempt_id}/ai-evaluation", headers=student_h)
    assert start.status_code == 202, start.text

    ev = await client.get(f"{API}/speaking-tests/practice/attempts/{attempt_id}/ai-evaluation", headers=student_h)
    body = ev.json()
    assert body["status"] == "done", f"status={body.get('status')} error={body.get('error')}"
    assert body["parts"] and body["parts"][0]["part"] == "1.1"
