"""Speaking savollar varag'i PDF: rasmlar haqiqatan joylanishi va xavfsiz yo'l yechimi."""
import os
import tempfile
import uuid
from io import BytesIO

_tmp = tempfile.mkdtemp()
os.environ["DATABASE_URL"] = f"sqlite+aiosqlite:///{_tmp}/speaking_pdf.db"
os.environ["ENV"] = "local"

import pytest
import pytest_asyncio
from fastapi import Request
from httpx import ASGITransport, AsyncClient
from PIL import Image
from pypdf import PdfReader

from app.core import database as dbm
from app.core.database import init_db
from app.core.paths import STATIC_DIR
from app.modules.auth.dependencies import get_current_user
from app.modules.auth.models import User, UserProfile, UserRole
from app.modules.tests.speaking.question_paper_pdf import resolve_local_image

pytestmark = pytest.mark.asyncio
API = "/api/v1"


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
    folder = STATIC_DIR / "speaking_images"
    folder.mkdir(parents=True, exist_ok=True)
    names = []
    for color in ("red", "green", "blue"):
        name = f"pdf-test-{uuid.uuid4().hex}.png"
        Image.new("RGB", (400, 250), color).save(folder / name)
        names.append(name)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://localhost") as c:
        yield c, {"X-Test-User": str(admin_id)}, names
    for name in names:
        (folder / name).unlink(missing_ok=True)
    app.dependency_overrides.pop(get_current_user, None)
    await dbm.engine.dispose()


def _payload(names, **over):
    img = lambda i: f"/static/speaking_images/{names[i]}"
    data = {
        "title": "PDF Speaking", "is_practice": True, "is_free": True,
        "part_1_1": {"question_1": "Tell me about your best friend.", "question_2": "q2", "question_3": "q3"},
        "part_1_2": {"image_1_url": img(0), "image_2_url": img(1), "question_describe": "What do you see?",
                     "question_2": "q2", "question_3": "q3"},
        "part_2": {"image_url": img(2), "question_1": "q1", "question_2": "q2", "question_3": "q3"},
        "part_3": {"topic": "Citizens should be allowed to carry personal guns.",
                   "for_points": ["f1", "f2", "f3"], "against_points": ["a1", "a2", "a3"]},
    }
    data.update(over)
    return data


async def test_pdf_embeds_images_and_follows_official_layout(env):
    c, h, names = env
    tid = (await c.post(f"{API}/admin/speaking-tests", headers=h, json=_payload(names))).json()["id"]
    res = await c.get(f"{API}/speaking-tests/practice/{tid}/paper.pdf")
    assert res.status_code == 200 and res.headers["content-type"] == "application/pdf"

    reader = PdfReader(BytesIO(res.content))
    assert len(reader.pages) == 4  # har Part alohida sahifada
    images_per_page = [len(list(p.images)) for p in reader.pages]
    assert images_per_page == [0, 2, 1, 0]  # Part 1.2 -> 2 ta, Part 2 -> 1 ta rasm
    text = "\n".join(p.extract_text() for p in reader.pages)
    assert "Part 1.1" in text and "Part 3" in text and "FOR" in text and "AGAINST" in text
    assert "You will have 30 seconds to reply to each question" in " ".join(text.split())
    assert "/static/speaking_images" not in text  # URL matn sifatida chiqmaydi


async def test_missing_image_does_not_break_pdf(env):
    c, h, names = env
    body = _payload(names)
    body["part_2"]["image_url"] = "/static/speaking_images/does-not-exist.png"
    tid = (await c.post(f"{API}/admin/speaking-tests", headers=h, json=body)).json()["id"]
    res = await c.get(f"{API}/speaking-tests/practice/{tid}/paper.pdf")
    assert res.status_code == 200
    reader = PdfReader(BytesIO(res.content))
    assert "image not available" in reader.pages[2].extract_text()


def test_image_resolution_is_confined_to_static_dir():
    assert resolve_local_image("/static/../app/main.py") is None
    assert resolve_local_image("/static/%2e%2e/app/main.py") is None
    assert resolve_local_image("https://evil.example.com/static/speaking_images/x.png") is None
    assert resolve_local_image("/etc/passwd") is None
    assert resolve_local_image("") is None
