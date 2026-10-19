"""5-bosqich: ochiq ro'yxatdan o'tish (/apply) mustahkamlash.

Qamrab olinadi: atomik joy band qilish (parallel so'rovlar), rasm yuklash va
validatsiya, CAPTCHA (fail-closed), region normalizatsiyasi, yangi MK ID formati,
ikkinchi omil (telefon oxirgi 4 raqami) bilan exams kirish.
"""
import asyncio
import io
import os
import re
import tempfile
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

_tmp = tempfile.mkdtemp()
os.environ["DATABASE_URL"] = f"sqlite+aiosqlite:///{_tmp}/test_stage5.db"

import pytest
import pytest_asyncio
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from PIL import Image
from sqlalchemy import select, text

from app.core import database as dbm
from app.core.config import settings
from app.core.database import init_db
from app.core.rate_limit import limiter
from app.core import captcha as captcha_mod
from app.modules.auth.models import Region
from app.modules.exams.auth.router import router as exams_auth_router
from app.modules.exams.mock import applicant_media
from app.modules.exams.mock.apply_router import router as b2b_router
from app.modules.exams.mock.mk_id import ANY_MK_ID_RE, build_mk_id
from app.modules.exams.mock.models import ExamSession, MockExam, MockRegistration

API = "/api/v1"
NEW_MK_ID_RE = re.compile(r"^MK-\d{6}-[2-9A-HJ-KMNP-TV-Z]{6}$")


def _png() -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (40, 30), (20, 120, 220)).save(buf, format="PNG")
    return buf.getvalue()


async def _mk_session(**kw) -> tuple[str, int]:
    async with dbm.AsyncSessionLocal() as s:
        await s.execute(text("PRAGMA foreign_keys=OFF"))  # speaking_id FK: skill jadvallarini talab qilmaslik uchun
        exam = MockExam(id=f"mx-{uuid.uuid4().hex[:8]}", title="Mock", 
                        price=kw.pop("price", 0), speaking_id="sp-test")
        s.add(exam)
        await s.flush()
        sess = ExamSession(
            mock_exam_id=exam.id,
            exam_date=datetime.now(timezone.utc) + timedelta(days=3),
            location_address="Tashkent",
            registration_slug=f"slug{uuid.uuid4().hex[:10]}",
            **kw,
        )
        s.add(sess)
        await s.commit()
        return sess.registration_slug, sess.id


def _applicant(**overrides) -> dict:
    body = {
        "first_name": "Ali", "last_name": "Valiyev", "gender": "M",
        "birth_date": "2000-05-17", "phone": "+998901234567", "address": "Toshkent shahri",
    }
    body.update(overrides)
    return body


async def _upload(env, slug: str, data: bytes = None, name: str = "p.png", ctype: str = "image/png"):
    return await env.post(
        f"{API}/apply/{slug}/photo",
        files={"file": (name, data if data is not None else _png(), ctype)},
    )


@pytest_asyncio.fixture(scope="module")
async def env():
    await init_db()
    app = FastAPI()
    app.state.limiter = limiter
    app.include_router(b2b_router, prefix=API)
    app.include_router(exams_auth_router, prefix=API)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        yield c
    await dbm.engine.dispose()


@pytest.fixture(autouse=True)
def _isolate(monkeypatch):
    limiter.reset()
    # Rasmlar haqiqiy static/ papkasiga emas, vaqtinchalik papkaga yozilsin.
    monkeypatch.setattr(applicant_media, "PHOTO_DIR", Path(tempfile.mkdtemp()) / "applicant_photos")
    monkeypatch.setattr(settings, "APPLY_CAPTCHA_ENABLED", False)
    monkeypatch.setattr(settings, "EXAMS_MK_ID_REQUIRE_PHONE_LAST4", True)
    monkeypatch.setattr(settings, "TRUST_PROXY_HEADERS", True)
    yield
    limiter.reset()


# --------------------------------------------------------------------------
# 1. Atomik joy band qilish
# --------------------------------------------------------------------------

async def test_parallel_applies_never_exceed_capacity(env):
    slug, sid = await _mk_session(capacity=2)
    up = await _upload(env, slug)
    assert up.status_code == 201, up.text
    photo_url = up.json()["photo_url"]

    async def apply(i: int):
        return await env.post(
            f"{API}/apply/{slug}",
            json=_applicant(first_name=f"Ali{i}", phone=f"+9989012345{i:02d}", photo_url=photo_url),
        )

    responses = await asyncio.gather(*(apply(i) for i in range(6)))
    codes = sorted(r.status_code for r in responses)
    assert codes.count(201) == 2, codes
    assert codes.count(400) == 4, codes

    async with dbm.AsyncSessionLocal() as s:
        session = await s.get(ExamSession, sid)
        regs = (await s.execute(select(MockRegistration).where(MockRegistration.session_id == sid))).scalars().all()
    assert session.seats_taken == 2
    assert len(regs) == 2


async def test_failed_insert_does_not_leak_a_seat(env):
    slug, sid = await _mk_session(capacity=1)
    photo_url = (await _upload(env, slug)).json()["photo_url"]
    # Yaroqsiz tug'ilgan sana: validatsiya xatosi — joy band qilinmasligi kerak.
    bad = await env.post(f"{API}/apply/{slug}", json=_applicant(birth_date="2999-01-01", photo_url=photo_url))
    assert bad.status_code == 400
    ok = await env.post(f"{API}/apply/{slug}", json=_applicant(photo_url=photo_url))
    assert ok.status_code == 201, ok.text


# --------------------------------------------------------------------------
# 2. Rasm: yuklash, validatsiya, faqat o'z URL'i
# --------------------------------------------------------------------------

async def test_photo_upload_is_reencoded_jpeg(env):
    slug, _ = await _mk_session()
    r = await _upload(env, slug)
    assert r.status_code == 201, r.text
    url = r.json()["photo_url"]
    assert applicant_media.is_own_photo_url(url)
    with Image.open(applicant_media.PHOTO_DIR / Path(url).name) as img:
        assert img.format == "JPEG"
        assert "exif" not in img.info


@pytest.mark.parametrize("data,ctype", [
    (b"not an image at all", "text/plain"),
    (b"GIF89a" + b"\x00" * 20, "image/gif"),
])
async def test_photo_rejects_non_images(env, data, ctype):
    slug, _ = await _mk_session()
    r = await _upload(env, slug, data=data, ctype=ctype)
    assert r.status_code == 400, r.text


async def test_photo_rejects_oversize_upload(env):
    slug, _ = await _mk_session()
    big = b"\xff" * (applicant_media.MAX_BYTES + 1)
    r = await _upload(env, slug, data=big)
    assert r.status_code == 413


@pytest.mark.parametrize("url", [
    "https://example.com/p.jpg",
    "data:image/png;base64,AAAA",
    "/static/applicant_photos/../../etc/passwd.jpg",
    "/static/applicant_photos/00000000000000000000000000000000.jpg",  # mavjud emas
])
async def test_apply_accepts_only_own_uploaded_photo(env, url):
    slug, _ = await _mk_session()
    r = await env.post(f"{API}/apply/{slug}", json=_applicant(photo_url=url))
    assert r.status_code == 400, (url, r.text)


# --------------------------------------------------------------------------
# 3. CAPTCHA: yoqilganda token majburiy (fail closed)
# --------------------------------------------------------------------------

async def test_captcha_required_when_enabled(env, monkeypatch):
    slug, _ = await _mk_session()
    photo_url = (await _upload(env, slug)).json()["photo_url"]  # rasm CAPTCHA'dan oldin yuklanadi
    monkeypatch.setattr(settings, "APPLY_CAPTCHA_ENABLED", True)

    no_token = await env.post(f"{API}/apply/{slug}", json=_applicant(photo_url=photo_url))
    assert no_token.status_code == 400

    async def fake_ok(token, ip):
        return token == "good-token"

    monkeypatch.setattr(captcha_mod, "verify_turnstile", fake_ok)
    bad = await env.post(f"{API}/apply/{slug}", json=_applicant(photo_url=photo_url, captcha_token="bad"))
    assert bad.status_code == 400
    good = await env.post(f"{API}/apply/{slug}", json=_applicant(photo_url=photo_url, captcha_token="good-token"))
    assert good.status_code == 201, good.text


async def test_turnstile_without_secret_fails_closed(monkeypatch):
    monkeypatch.setattr(settings, "TURNSTILE_SECRET_KEY", "")
    assert await captcha_mod.verify_turnstile("any-token", "1.2.3.4") is False


# --------------------------------------------------------------------------
# 4. Region normalizatsiyasi
# --------------------------------------------------------------------------

async def test_region_is_normalized_to_enum_value(env):
    slug, _ = await _mk_session()
    photo_url = (await _upload(env, slug)).json()["photo_url"]
    reg = await env.post(f"{API}/apply/{slug}", json=_applicant(photo_url=photo_url, address="Andijon"))
    assert reg.status_code == 201, reg.text
    async with dbm.AsyncSessionLocal() as s:
        row = await s.get(MockRegistration, int(reg.json()["id"]))
    assert row.region == Region.ANDIJAN.value


async def test_unknown_or_long_address_becomes_unknown_region(env):
    from app.modules.exams.mock.applicant_rules import normalize_region

    assert normalize_region("Qayerdadir uzoqda") == "unknown"
    assert normalize_region("x" * 500) == "unknown"
    assert normalize_region(None) == "unknown"


# --------------------------------------------------------------------------
# 5. MK ID formati va ikkinchi omil
# --------------------------------------------------------------------------

def test_new_mk_id_format_and_legacy_still_accepted():
    new = build_mk_id(datetime(2006, 7, 29).date())
    assert NEW_MK_ID_RE.fullmatch(new), new
    assert ANY_MK_ID_RE.fullmatch(new)
    assert ANY_MK_ID_RE.fullmatch("MK-290706-785")  # eski 3 xonali format
    assert ANY_MK_ID_RE.fullmatch("MC-290706785")   # juda eski format
    assert not ANY_MK_ID_RE.fullmatch("MK-290706-0OIL12")  # 0/O/I/L taqiqlangan


async def test_applicant_mk_id_uses_new_format(env):
    slug, _ = await _mk_session()
    photo_url = (await _upload(env, slug)).json()["photo_url"]
    reg = await env.post(f"{API}/apply/{slug}", json=_applicant(photo_url=photo_url))
    assert reg.status_code == 201, reg.text
    assert NEW_MK_ID_RE.fullmatch(reg.json()["exam_id"]), reg.json()["exam_id"]


async def test_mk_id_login_requires_phone_last4_by_default(env):
    slug, _ = await _mk_session()
    photo_url = (await _upload(env, slug)).json()["photo_url"]
    reg = await env.post(f"{API}/apply/{slug}", json=_applicant(photo_url=photo_url, phone="+998901234567"))
    mk_id = reg.json()["exam_id"]

    without = await env.post(f"{API}/exams-auth/login", json={"student_id": mk_id})
    wrong = await env.post(f"{API}/exams-auth/login", json={"student_id": mk_id, "phone_last4": "0000"})
    assert without.status_code == 404 and wrong.status_code == 404
    assert without.json() == wrong.json()  # bir xil umumiy xabar

    ok = await env.post(f"{API}/exams-auth/login", json={"student_id": mk_id, "phone_last4": "4567"})
    assert ok.status_code == 200, ok.text


async def test_apply_requires_phone_when_second_factor_enabled(env):
    slug, _ = await _mk_session()
    photo_url = (await _upload(env, slug)).json()["photo_url"]
    r = await env.post(f"{API}/apply/{slug}", json=_applicant(photo_url=photo_url, phone=None))
    assert r.status_code == 400


async def test_same_phone_can_register_two_applicants(env):
    """Ota-ona bir raqam bilan ikki farzandni ro'yxatdan o'tkazishi mumkin."""
    slug, _ = await _mk_session()
    photo_url = (await _upload(env, slug)).json()["photo_url"]
    first = await env.post(f"{API}/apply/{slug}", json=_applicant(photo_url=photo_url, first_name="A"))
    second = await env.post(f"{API}/apply/{slug}", json=_applicant(photo_url=photo_url, first_name="B"))
    assert first.status_code == 201 and second.status_code == 201
