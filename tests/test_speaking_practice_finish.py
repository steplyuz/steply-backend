"""Speaking practice urinishini yakunlash: keyingi topshiriq yangi urinishda boshlanishi kerak.

Oldin urinish IN_PROGRESS qolardi va qayta topshirishda o'sha savollarga yozuv 409 bo'lardi.
"""
import os
import tempfile
import uuid

_tmp = tempfile.mkdtemp()
os.environ["DATABASE_URL"] = f"sqlite+aiosqlite:///{_tmp}/test_speaking_finish.db"

import pytest
import pytest_asyncio
from sqlalchemy import select

from app.core import database as dbm
from app.core.database import init_db
from app.modules.auth.models import User, UserRole
from app.modules.exams.mock.models import PracticeAttempt, PracticeAttemptStatus, SkillType
from app.modules.tests.speaking import router as speaking_router


@pytest_asyncio.fixture(scope="module", autouse=True)
async def _db():
    await init_db()
    yield
    await dbm.engine.dispose()


async def _user() -> User:
    async with dbm.AsyncSessionLocal() as s:
        u = User(global_role=UserRole.STUDENT, is_active=True)
        s.add(u)
        await s.commit()
        await s.refresh(u)
        return u


async def _attempt(user_id, status=PracticeAttemptStatus.IN_PROGRESS) -> int:
    async with dbm.AsyncSessionLocal() as s:
        a = PracticeAttempt(user_id=user_id, skill=SkillType.SPEAKING, test_id=f"SPK-{uuid.uuid4().hex[:8]}",
                            mode="FULL_PRACTICE", part_number=None, status=status)
        s.add(a)
        await s.commit()
        return a.id


@pytest.mark.asyncio
async def test_finish_marks_in_progress_attempt_submitted():
    user = await _user()
    attempt_id = await _attempt(user.id)
    async with dbm.AsyncSessionLocal() as s:
        db_user = await s.get(User, user.id)
        res = await speaking_router.finish_practice_attempt(attempt_id, current_user=db_user, db=s)
    assert res["status"] == "SUBMITTED"
    async with dbm.AsyncSessionLocal() as s:
        a = await s.get(PracticeAttempt, attempt_id)
        assert a.status == PracticeAttemptStatus.SUBMITTED
        assert a.submitted_at is not None


@pytest.mark.asyncio
async def test_finish_is_idempotent():
    user = await _user()
    attempt_id = await _attempt(user.id)
    for _ in range(2):
        async with dbm.AsyncSessionLocal() as s:
            db_user = await s.get(User, user.id)
            res = await speaking_router.finish_practice_attempt(attempt_id, current_user=db_user, db=s)
        assert res["status"] == "SUBMITTED"


@pytest.mark.asyncio
async def test_finish_rejects_other_users_attempt():
    owner = await _user()
    other = await _user()
    attempt_id = await _attempt(owner.id)
    async with dbm.AsyncSessionLocal() as s:
        db_other = await s.get(User, other.id)
        with pytest.raises(Exception) as exc:
            await speaking_router.finish_practice_attempt(attempt_id, current_user=db_other, db=s)
        assert getattr(exc.value, "status_code", None) == 404
    async with dbm.AsyncSessionLocal() as s:
        assert (await s.get(PracticeAttempt, attempt_id)).status == PracticeAttemptStatus.IN_PROGRESS
