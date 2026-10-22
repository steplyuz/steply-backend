"""Rol (SUPER_ADMIN), o'chirish (force), lock/unlock va pullik kontent kirish regressiyalari.

Testlar REAL autentifikatsiya bilan ishlaydi (phone/login -> access_token cookie),
dependency override ishlatilmaydi.
"""
import contextlib
import os
import tempfile
import uuid

_tmp = tempfile.mkdtemp()
os.environ["DATABASE_URL"] = f"sqlite+aiosqlite:///{_tmp}/roles_lock.db"
os.environ["ENV"] = "local"

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from app.core import database as dbm
from app.core.database import init_db
from app.core.rate_limit import limiter
from app.core.security import hash_password
from app.modules.auth.models import ContactType, User, UserContact, UserProfile, UserRole

API = "/api/v1"
PASSWORD = "Strong-pass-123"

def listening_body(**extra):
    layout = [(1, 8, "MULTIPLE_CHOICE"), (2, 6, "GAP_FILL"), (3, 4, "MULTIPLE_CHOICE"),
              (4, 5, "MULTIPLE_CHOICE"), (5, 6, "MULTIPLE_CHOICE"), (6, 6, "GAP_FILL")]
    parts, n = [], 0
    for part_number, count, qtype in layout:
        questions = []
        for _ in range(count):
            n += 1
            q = {"question_number": n, "type": qtype, "question": f"Q{n}"}
            if qtype == "MULTIPLE_CHOICE":
                q["correct_answer"] = "A"
                q["options"] = [{"label": "A", "value": "yes"}, {"label": "B", "value": "no"},
                                {"label": "C", "value": "maybe"}]
            else:
                q["correct_answer"] = f"ans{n}"
            questions.append(q)
        parts.append({"part_number": part_number, "title": f"Part {part_number}", "questions": questions})
    return {"title": "Roles listening", "cefr_level": "B2", "duration_minutes": 40,
            "is_practice": False, "parts": parts, **extra}


T1 = {"part_number": 1, "sub_part": 1, "topic": "Informal letter", "instruction": "Write a letter."}
T12 = {"part_number": 1, "sub_part": 2, "topic": "Formal letter", "instruction": "Write a formal letter."}
T2 = {"part_number": 2, "topic": "Essay", "instruction": "Write an essay."}
P3 = {"topic": "Citizens should vote.", "for_points": ["a", "b", "c"], "against_points": ["d", "e", "f"]}


def writing_body(**extra):
    return {"title": "Roles writing", "cefr_level": "B2", "duration_minutes": 60,
            "tasks": [T1, T12, T2], **extra}


@pytest.fixture(autouse=True)
def _reset_limiter():
    limiter.reset()


@pytest_asyncio.fixture(scope="module")
async def env():
    """Uch rol + anonim klient. Har bir rol o'z login cookie'si bilan."""
    await init_db()
    from app.main import app

    specs = [
        ("student", UserRole.STUDENT, "+998901000101"),
        ("admin", UserRole.ADMIN, "+998901000102"),
        ("super", UserRole.SUPER_ADMIN, "+998901000103"),
        ("plus", UserRole.STUDENT, "+998901000104"),
    ]
    ids = {}
    async with dbm.AsyncSessionLocal() as s:
        for key, role, phone in specs:
            u = User(global_role=role, is_active=True, hashed_password=hash_password(PASSWORD))
            s.add(u)
            await s.flush()
            s.add(UserProfile(user_id=u.id, full_name=key, username=f"{key}_roles"))
            s.add(UserContact(user_id=u.id, contact_type=ContactType.PHONE, value=phone,
                              is_primary=True, is_verified=True))
            ids[key] = (u.id, phone)
        await s.commit()

    # PLUS obuna: plan default included_skills'ni ishlatadi (SPEAKING ham kiradi)
    from datetime import datetime, timedelta, timezone
    from app.modules.billing.models import SubscriptionPlan, SubscriptionTier, UserSubscription
    async with dbm.AsyncSessionLocal() as s:
        plan = SubscriptionPlan(name=f"roles-plus-{uuid.uuid4().hex[:8]}", price=1, duration_days=30,
                                tier=SubscriptionTier.PLUS)
        s.add(plan)
        await s.flush()
        now = datetime.now(timezone.utc)
        s.add(UserSubscription(user_id=ids["plus"][0], plan_id=plan.id,
                               start_date=now - timedelta(days=1), end_date=now + timedelta(days=30),
                               is_active=True))
        await s.commit()

    stack = contextlib.AsyncExitStack()
    clients = {}
    for key, (_uid, phone) in ids.items():
        c = await stack.enter_async_context(
            AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver"))
        r = await c.post(f"{API}/auth/phone/login", json={"phone": phone, "password": PASSWORD})
        assert r.status_code == 200, f"{key} login failed: {r.text}"
        clients[key] = c
    clients["anon"] = await stack.enter_async_context(
        AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver"))
    clients["ids"] = {k: v[0] for k, v in ids.items()}
    yield clients
    await stack.aclose()
    await dbm.engine.dispose()


# =====================================================================
#  1. SUPER ADMIN — rol yaratish yo'li va himoyasi
# =====================================================================
async def test_grant_super_admin_script_promotes_existing_user(env):
    """Bootstrap: scripts/manage_super_admin.py orqali mavjud foydalanuvchi super admin bo'ladi."""
    from scripts.manage_super_admin import grant_super_admin

    phone = "+998901000199"
    async with dbm.AsyncSessionLocal() as s:
        u = User(global_role=UserRole.STUDENT, is_active=True, hashed_password=hash_password(PASSWORD))
        s.add(u)
        await s.flush()
        s.add(UserContact(user_id=u.id, contact_type=ContactType.PHONE, value=phone,
                          is_primary=True, is_verified=True))
        await s.commit()
        uid = u.id

    async with dbm.AsyncSessionLocal() as s:
        await grant_super_admin(s, phone)

    async with dbm.AsyncSessionLocal() as s:
        user = await s.get(User, uid)
        assert user.global_role == UserRole.SUPER_ADMIN


async def test_admin_cannot_deactivate_or_demote_super_admin(env):
    super_id = env["ids"]["super"]
    r = await env["admin"].patch(f"{API}/admin/users/{super_id}", json={"is_active": False})
    assert r.status_code == 403, r.text
    r = await env["admin"].patch(f"{API}/admin/users/{super_id}", json={"global_role": "student"})
    assert r.status_code == 403, r.text


async def test_admin_cannot_grant_super_admin(env):
    student_id = env["ids"]["student"]
    r = await env["admin"].patch(f"{API}/admin/users/{student_id}", json={"global_role": "super_admin"})
    assert r.status_code == 403, r.text


# =====================================================================
#  2. FORCE DELETE — faqat SUPER_ADMIN
# =====================================================================
async def test_admin_force_delete_is_forbidden_for_writing(env):
    r = await env["admin"].post(f"{API}/admin/writing-tests", json=writing_body())
    assert r.status_code == 201, r.text
    eid = r.json()["id"]
    r = await env["admin"].delete(f"{API}/admin/writing-tests/{eid}", params={"force": "true"})
    assert r.status_code == 403, r.text


async def test_super_admin_force_deletes_writing_with_results(env):
    from app.modules.tests.writing.models import WritingResult

    r = await env["super"].post(f"{API}/admin/writing-tests", json=writing_body())
    assert r.status_code == 201, r.text
    eid = r.json()["id"]
    async with dbm.AsyncSessionLocal() as s:
        s.add(WritingResult(user_id=env["ids"]["student"], exam_id=eid))
        await s.commit()

    # Oddiy DELETE natija bor bo'lgani uchun 409 bo'lishi kerak
    assert (await env["super"].delete(f"{API}/admin/writing-tests/{eid}")).status_code == 409
    r = await env["super"].delete(f"{API}/admin/writing-tests/{eid}", params={"force": "true"})
    assert r.status_code == 204, r.text


# =====================================================================
#  3. LOCK OFF — unlock endpoint (Writing)
# =====================================================================
async def test_writing_unlock_reopens_locked_test_for_edit(env):
    r = await env["admin"].post(f"{API}/admin/writing-tests", json=writing_body(is_practice=False))
    eid = r.json()["id"]
    assert (await env["admin"].post(f"{API}/admin/writing-tests/{eid}/lock")).json()["success"] is True
    assert (await env["admin"].put(f"{API}/admin/writing-tests/{eid}", json={"title": "Updated title"})).status_code == 409

    r = await env["admin"].post(f"{API}/admin/writing-tests/{eid}/unlock")
    assert r.status_code == 200, r.text
    assert (await env["admin"].put(f"{API}/admin/writing-tests/{eid}", json={"title": "Updated title"})).status_code == 200


async def test_student_cannot_unlock(env):
    r = await env["admin"].post(f"{API}/admin/writing-tests", json=writing_body(is_practice=False))
    eid = r.json()["id"]
    await env["admin"].post(f"{API}/admin/writing-tests/{eid}/lock")
    r = await env["student"].post(f"{API}/admin/writing-tests/{eid}/unlock")
    assert r.status_code == 403, r.text


# =====================================================================
#  4. PULIK KONTENT — anonim va FREE foydalanuvchi ko'ra olmasligi kerak
# =====================================================================
async def test_free_writing_visible_to_anonymous(env):
    r = await env["admin"].post(f"{API}/admin/writing-tests",
                                json=writing_body(is_practice=True, is_free=True))
    eid = r.json()["id"]
    assert (await env["anon"].get(f"{API}/writing-tests/{eid}")).status_code == 200


async def test_paid_writing_practice_hidden_from_anonymous(env):
    r = await env["admin"].post(f"{API}/admin/writing-tests",
                                json=writing_body(is_practice=True, is_free=False))
    eid = r.json()["id"]
    r = await env["anon"].get(f"{API}/writing-tests/{eid}")
    assert r.status_code in (401, 403, 404), f"Pullik writing anonim ko'rdi: {r.status_code}"
    catalog = (await env["anon"].get(f"{API}/writing-tests")).json()
    assert eid not in {x["id"] for x in catalog}


async def test_paid_writing_practice_hidden_from_free_student(env):
    r = await env["admin"].post(f"{API}/admin/writing-tests",
                                json=writing_body(is_practice=True, is_free=False))
    eid = r.json()["id"]
    r = await env["student"].get(f"{API}/writing-tests/{eid}")
    assert r.status_code == 403, f"FREE student pullik writingni ko'rdi: {r.status_code}"


async def test_paid_speaking_practice_hidden_from_anonymous(env):
    r = await env["admin"].post(f"{API}/admin/speaking-tests", json={"title": "Paid S", "part_3": P3})
    assert r.status_code == 201, r.text
    sid = r.json()["id"]
    r = await env["anon"].get(f"{API}/speaking-tests/practice/{sid}")
    assert r.status_code in (401, 403, 404), f"Pullik speaking anonim ko'rdi: {r.status_code}"
    r = await env["anon"].get(f"{API}/speaking-tests/practice/{sid}/paper.pdf")
    assert r.status_code in (401, 403, 404), f"Speaking PDF anonim yuklandi: {r.status_code}"


async def test_admin_still_sees_everything(env):
    r = await env["admin"].post(f"{API}/admin/writing-tests",
                                json=writing_body(is_practice=True, is_free=False))
    eid = r.json()["id"]
    assert (await env["admin"].get(f"{API}/writing-tests/{eid}")).status_code == 200


# =====================================================================
#  5. PLUS obunasi: pulik testlar obunaga kiradi
# =====================================================================
async def test_plus_subscriber_opens_paid_writing_and_speaking(env):
    r = await env["admin"].post(f"{API}/admin/writing-tests", json=writing_body(is_practice=True, is_free=False))
    wid = r.json()["id"]
    assert (await env["plus"].get(f"{API}/writing-tests/{wid}")).status_code == 200
    r = await env["admin"].post(f"{API}/admin/speaking-tests", json={"title": "Plus S", "part_3": P3})
    sid = r.json()["id"]
    assert (await env["plus"].get(f"{API}/speaking-tests/practice/{sid}")).status_code == 200


async def test_free_student_cannot_open_paid_speaking(env):
    r = await env["admin"].post(f"{API}/admin/speaking-tests", json={"title": "Free-denied S", "part_3": P3})
    sid = r.json()["id"]
    assert (await env["student"].get(f"{API}/speaking-tests/practice/{sid}")).status_code == 403


# =====================================================================
#  6. PRACTICE katalogi: faqat is_practice=True; mock katalogda yo'q
# =====================================================================
async def test_practice_catalog_excludes_mock_writing_and_speaking(env):
    mock_w = (await env["admin"].post(f"{API}/admin/writing-tests",
                                      json=writing_body(is_practice=False))).json()["id"]
    prac_w = (await env["admin"].post(f"{API}/admin/writing-tests",
                                      json=writing_body(is_practice=True, is_free=True))).json()["id"]
    w_ids = {x["id"] for x in (await env["plus"].get(f"{API}/writing-tests")).json()}
    assert prac_w in w_ids and mock_w not in w_ids

    # Speaking mock (is_practice=False) to'liq 4 qism talab qiladi; bu yerda flagni DB orqali qo'yamiz.
    mock_s = (await env["admin"].post(f"{API}/admin/speaking-tests",
                                      json={"title": "Mock S", "part_3": P3})).json()["id"]
    from app.modules.tests.speaking.models import SpeakingTest
    async with dbm.AsyncSessionLocal() as s:
        (await s.get(SpeakingTest, mock_s)).is_practice = False
        await s.commit()
    s_ids = {x["id"] for x in (await env["plus"].get(f"{API}/speaking-tests/practice")).json()}
    assert mock_s not in s_ids


# =====================================================================
#  7. LOCK OFF — Speaking
# =====================================================================
async def test_speaking_unlock_reopens_for_edit(env):
    sid = (await env["admin"].post(f"{API}/admin/speaking-tests", json={"title": "Lk S", "part_3": P3})).json()["id"]
    assert (await env["admin"].post(f"{API}/admin/speaking-tests/{sid}/lock")).json()["success"] is True
    assert (await env["admin"].put(f"{API}/admin/speaking-tests/{sid}", json={"title": "Changed"})).status_code == 409
    assert (await env["admin"].post(f"{API}/admin/speaking-tests/{sid}/unlock")).status_code == 200
    assert (await env["admin"].put(f"{API}/admin/speaking-tests/{sid}", json={"title": "Changed"})).status_code == 200


# =====================================================================
#  8. FORCE DELETE — Speaking (audio fayllar bilan) va Listening (natija bilan)
# =====================================================================
async def test_super_admin_force_deletes_speaking_with_audio(env):
    from app.core.file_storage import private_path
    from app.modules.tests.speaking.models import SpeakingSubmission

    sid = (await env["admin"].post(f"{API}/admin/speaking-tests", json={"title": "Force S", "part_3": P3})).json()["id"]
    key = f"{uuid.uuid4().hex}.webm"
    audio = private_path("speaking", "submissions", key)
    audio.write_bytes(b"fake-audio")
    async with dbm.AsyncSessionLocal() as s:
        s.add(SpeakingSubmission(user_id=env["ids"]["student"], speaking_test_id=sid,
                                 audio_url="", audio_storage_key=key, status="pending"))
        await s.commit()

    assert (await env["admin"].delete(f"{API}/admin/speaking-tests/{sid}")).status_code == 409
    assert (await env["admin"].delete(f"{API}/admin/speaking-tests/{sid}", params={"force": "true"})).status_code == 403
    assert (await env["super"].delete(f"{API}/admin/speaking-tests/{sid}", params={"force": "true"})).status_code == 200
    assert not audio.exists(), "audio fayl diskdan o'chmadi"


async def test_super_admin_force_deletes_listening_with_results(env):
    from app.modules.tests.listening.models import ListeningResult

    lid = (await env["admin"].post(f"{API}/admin/listening-tests", json=listening_body())).json()["id"]
    async with dbm.AsyncSessionLocal() as s:
        s.add(ListeningResult(user_id=env["ids"]["student"], exam_id=lid,
                              total_questions=35, correct_answers=0, percentage=0.0))
        await s.commit()

    assert (await env["admin"].delete(f"{API}/admin/listening-tests/{lid}")).status_code == 409
    assert (await env["admin"].delete(f"{API}/admin/listening-tests/{lid}", params={"force": "true"})).status_code == 403
    assert (await env["super"].delete(f"{API}/admin/listening-tests/{lid}", params={"force": "true"})).status_code == 204
