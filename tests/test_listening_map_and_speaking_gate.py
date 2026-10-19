"""Listening MAP rasm yuklash va obunasiz Speaking topshirishni bloklash regressiyalari."""
import os
import tempfile
import uuid

_tmp = tempfile.mkdtemp()
os.environ["DATABASE_URL"] = f"sqlite+aiosqlite:///{_tmp}/map_speaking.db"
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
PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64


async def _make_user(role: UserRole, name: str) -> uuid.UUID:
    async with dbm.AsyncSessionLocal() as s:
        u = User(global_role=role, is_active=True)
        s.add(u)
        await s.flush()
        s.add(UserProfile(user_id=u.id, full_name=name))
        await s.commit()
        return u.id


def _override(app):
    async def _user(request: Request):
        async with dbm.AsyncSessionLocal() as s:
            return await s.get(User, uuid.UUID(request.headers["X-Test-User"]))
    app.dependency_overrides[get_current_user] = _user


@pytest_asyncio.fixture(scope="module")
async def ids():
    await init_db()
    from app.main import app

    admin_id = await _make_user(UserRole.ADMIN, "Admin")
    student_id = await _make_user(UserRole.STUDENT, "Student")
    _override(app)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://localhost") as c:
        yield c, admin_id, student_id
    app.dependency_overrides.pop(get_current_user, None)
    await dbm.engine.dispose()


async def test_admin_can_upload_listening_map_png(ids):
    c, admin_id, _ = ids
    res = await c.post(f"{API}/admin/listening-tests/maps/upload", headers={"X-Test-User": str(admin_id)},
                       files={"file": ("map.png", PNG, "image/png")})
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["url"].find("/static/listening_maps/") >= 0 and body["url"].endswith(".png")


async def test_map_upload_rejects_mismatched_signature(ids):
    c, admin_id, _ = ids
    res = await c.post(f"{API}/admin/listening-tests/maps/upload", headers={"X-Test-User": str(admin_id)},
                       files={"file": ("map.jpg", PNG, "image/jpeg")})
    assert res.status_code == 415


async def test_map_upload_rejects_empty_file(ids):
    c, admin_id, _ = ids
    res = await c.post(f"{API}/admin/listening-tests/maps/upload", headers={"X-Test-User": str(admin_id)},
                       files={"file": ("map.png", b"", "image/png")})
    assert res.status_code == 400


async def test_student_without_subscription_cannot_submit_speaking_practice(ids):
    c, _, student_id = ids
    res = await c.post(
        f"{API}/speaking-tests/{uuid.uuid4()}/practice/submit",
        headers={"X-Test-User": str(student_id)},
        params={"question_external_id": "q1"},
        files={"file": ("a.webm", b"\x1a\x45\xdf\xa3" + b"\x00" * 32, "audio/webm")},
    )
    assert res.status_code == 403, res.text
