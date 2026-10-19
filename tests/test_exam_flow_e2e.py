"""2-bosqich: imtihon oqimi E2E (haqiqiy ilova, haqiqiy SQLite, Alembic sxemasi).

Oqim: admin 35 savollik Reading va Listening + Writing + Speaking yaratadi -> MockExam ->
ExamSession -> nomzod /apply/{slug} orqali ro'yxatdan o'tadi -> exams-auth login (cookie) ->
kontentni oladi -> javoblarni saqlaydi -> submit -> baholash -> natija.

Admin `X-Test-User` sarlavhasi bilan aniqlanadi (get_current_user almashtiriladi);
nomzod esa HAQIQIY exams-auth cookie'lari bilan ishlaydi. AI baholash mock qilinadi.
"""
import os
import tempfile
import uuid

_tmp = tempfile.mkdtemp()
os.environ["DATABASE_URL"] = f"sqlite+aiosqlite:///{_tmp}/e2e.db"
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


# --------------------------------------------------------------------------
# Kontent generatorlari
# --------------------------------------------------------------------------
def reading_payload() -> dict:
    """Shablon: 5 qism (1-6, 7-14, 15-20, 21-29, 30-35) = 35 savol.
    Juft raqamli savollar MULTIPLE_CHOICE (to'g'ri: B), toqlari GAP_FILL."""
    parts, n = [], 0
    for p, size in enumerate((6, 8, 6, 9, 6), start=1):
        questions = []
        for _ in range(size):
            n += 1
            if n % 2 == 0:
                questions.append({
                    "question_number": n, "type": "MULTIPLE_CHOICE", "text": f"Savol {n}?",
                    "correct_answer": ["B"],
                    "options": [{"label": "A", "value": "birinchi"}, {"label": "B", "value": "ikkinchi"},
                                {"label": "C", "value": "uchinchi"}],
                })
            else:
                questions.append({"question_number": n, "type": "GAP_FILL", "text": f"Gap {n}: ____",
                                  "correct_answer": [f"word{n}"], "word_limit": 1})
        parts.append({"title": f"Part {p}", "passage": f"Matn {p}. " * 20, "questions": questions})
    return {"title": "E2E Reading 35", "cefr_level": "B2", "duration_minutes": 60,
            "is_practice": False, "parts": parts}


def listening_payload() -> dict:
    """6 qism, jami 35 savol (8+6+4+5+6+6). Audio draft bosqichida ixtiyoriy."""
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
        parts.append({"part_number": part_number, "title": f"Listening part {part_number}",
                      "questions": questions})
    return {"title": "E2E Listening 35", "cefr_level": "B2", "duration_minutes": 40,
            "is_practice": False, "parts": parts}


def writing_payload() -> dict:
    return {
        "title": "E2E Writing", "cefr_level": "B2", "duration_minutes": 60, "is_practice": False,
        "tasks": [
            {"part_number": 1, "sub_part": 1, "topic": "Informal letter", "instruction": "Write a letter to a friend."},
            {"part_number": 2, "topic": "Essay", "instruction": "Write an essay about technology."},
        ],
    }


def speaking_payload() -> dict:
    return {
        "title": "E2E Speaking",
        "part_1_1": {"question_1": "Where do you live?", "question_2": "What do you do?",
                     "question_3": "Do you like music?"},
        "part_1_2": {"image_1_url": "/static/a.jpg", "image_2_url": "/static/b.jpg",
                     "question_describe": "Compare the pictures.", "question_2": "Why?", "question_3": "How?"},
        "part_2": {"image_url": "/static/c.jpg", "question_1": "Describe it.", "question_2": "Why?",
                   "question_3": "When?"},
        "part_3": {"topic": "Citizens should vote.", "for_points": ["a", "b", "c"],
                   "against_points": ["d", "e", "f"]},
    }


WAV_BYTES = b"RIFF" + (36).to_bytes(4, "little") + b"WAVEfmt " + b"\x00" * 64


# --------------------------------------------------------------------------
# Fixture
# --------------------------------------------------------------------------
async def _mk_admin() -> uuid.UUID:
    async with dbm.AsyncSessionLocal() as s:
        u = User(global_role=UserRole.ADMIN, is_active=True)
        s.add(u)
        await s.flush()
        s.add(UserProfile(user_id=u.id, full_name="E2E Admin"))
        await s.commit()
        return u.id


@pytest_asyncio.fixture(scope="module")
async def env():
    await init_db()
    from app.main import app  # haqiqiy ilova: barcha router va middleware

    async def _user(request: Request):
        async with dbm.AsyncSessionLocal() as s:
            return await s.get(User, uuid.UUID(request.headers["X-Test-User"]))

    app.dependency_overrides[get_current_user] = _user
    transport = ASGITransport(app=app)
    admin_id = await _mk_admin()
    async with AsyncClient(transport=transport, base_url="http://localhost",
                           headers={"X-Test-User": str(admin_id)}) as admin, \
            AsyncClient(transport=transport, base_url="http://localhost") as cand:
        yield {"admin": admin, "cand": cand, "app": app, "state": {}}
    app.dependency_overrides.pop(get_current_user, None)
    await dbm.engine.dispose()


@pytest.fixture(autouse=True)
def _reset_limiter():
    limiter.reset()


def ok(resp, *codes):
    codes = codes or (200,)
    assert resp.status_code in codes, (
        f"{resp.request.method} {resp.request.url.path} -> {resp.status_code}: {resp.text[:700]}")
    return resp.json()


# --------------------------------------------------------------------------
# Oqim (tartib bilan; holat env["state"] da)
# --------------------------------------------------------------------------
async def test_01_admin_creates_content_via_json_body(env):
    """Regressiya: Reading/Listening import oddiy JSON body bilan ishlashi shart (ilgari doim 400)."""
    admin, st = env["admin"], env["state"]
    st["reading"] = ok(await admin.post(f"{API}/admin/reading-tests", json=reading_payload()), 201)
    assert sum(len(p["questions"]) for p in st["reading"]["parts"]) == 35
    st["listening"] = ok(await admin.post(f"{API}/admin/listening-tests", json=listening_payload()), 201)
    assert sum(len(p["questions"]) for p in st["listening"]["parts"]) == 35
    st["writing"] = ok(await admin.post(f"{API}/admin/writing-tests", json=writing_payload()), 201)
    st["speaking"] = ok(await admin.post(f"{API}/admin/speaking-tests", json=speaking_payload()), 201)


async def test_01b_reading_part_mini_tests_are_standalone_and_idempotent(env):
    admin, st = env["admin"], env["state"]
    source_id = st["reading"]["id"]
    first = ok(await admin.post(f"{API}/admin/reading-tests/{source_id}/part-mini-tests"), 200)
    assert first["created_count"] == 5
    assert first["total_count"] == 5
    assert [item["practice_part_number"] for item in first["tests"]] == [1, 2, 3, 4, 5]

    # Repeating the operation must not create duplicate mini-tests.
    second = ok(await admin.post(f"{API}/admin/reading-tests/{source_id}/part-mini-tests"), 200)
    assert second["created_count"] == 0
    assert second["total_count"] == 5
    mini_ids = {item["id"] for item in second["tests"]}
    # Admin catalog includes non-free mini-tests; the public catalog is intentionally filtered.
    catalog = ok(await admin.get(f"{API}/admin/reading-tests"), 200)
    assert mini_ids.issubset({item["id"] for item in catalog})


async def test_02_import_via_multipart_file_still_works(env):
    admin = env["admin"]
    import json as _json
    payload = reading_payload()
    payload["title"] = "E2E Reading multipart"
    r = await admin.post(f"{API}/admin/reading-tests",
                         files={"file": ("r.json", _json.dumps(payload), "application/json")})
    ok(r, 201)
    lp = listening_payload()
    lp["title"] = "E2E Listening multipart"
    ok(await admin.post(f"{API}/admin/listening-tests",
                        files={"file": ("l.json", _json.dumps(lp), "application/json")}), 201)
    # Bo'sh/yaroqsiz so'rovlar aniq xato beradi
    assert (await admin.post(f"{API}/admin/reading-tests", content=b"", headers={"content-type": "application/json"})).status_code == 400
    assert (await admin.post(f"{API}/admin/reading-tests", content=b"{bad", headers={"content-type": "application/json"})).status_code == 422


async def test_03_mock_exam_session_and_registration(env):
    admin, cand, st = env["admin"], env["cand"], env["state"]
    st["mock"] = ok(await admin.post(f"{API}/admin/mock-exams", json={
        "title": "E2E Mock", "reading_id": st["reading"]["id"], "listening_id": st["listening"]["id"],
        "writing_id": st["writing"]["id"], "speaking_id": st["speaking"]["id"],
    }), 201)
    st["session"] = ok(await admin.post(f"{API}/admin/mock-exams/sessions", json={
        "mock_exam_id": st["mock"]["id"], "exam_date": "2030-01-01T09:00:00",
        "location_address": "Toshkent, Test markazi", "delivery_mode": "COMPUTER", "capacity": 5,
    }), 201)
    slug = st["session"]["registration_slug"]
    assert slug
    info = ok(await cand.get(f"{API}/apply/{slug}"))
    assert info["id"] == st["session"]["id"]
    import io
    from PIL import Image
    buf = io.BytesIO(); Image.new("RGB", (40, 30), (10, 90, 200)).save(buf, format="PNG")
    photo = ok(await cand.post(f"{API}/apply/{slug}/photo", files={"file": ("p.png", buf.getvalue(), "image/png")}), 201)
    reg = ok(await cand.post(f"{API}/apply/{slug}", json={
        "first_name": "Ali", "last_name": "Valiyev", "gender": "M", "birth_date": "2000-05-17",
        "phone": "+998901234567", "photo_url": photo["photo_url"], "address": "Toshkent",
    }), 201)
    assert reg["exam_id"].startswith("MK-")
    st["mk_id"] = reg["exam_id"]
    regs = ok(await admin.get(f"{API}/admin/mock-exams/sessions/{st['session']['id']}/registrations"))
    assert [r["student_id"] for r in regs] == [st["mk_id"]]
    st["exam_access_id"] = regs[0]["exam_access_id"]


async def test_04_candidate_login_and_content(env):
    cand, st = env["cand"], env["state"]
    # Cookie'siz endpoint yopiq
    assert (await cand.get(f"{API}/exams")).status_code == 401
    login = ok(await cand.post(f"{API}/exams-auth/login", json={"exam_id": st["exam_access_id"]}))
    assert login["status"] == "success"
    me = ok(await cand.get(f"{API}/exams-auth/me"))
    assert me["student_id"] == st["mk_id"] if "student_id" in me else True
    exams = ok(await cand.get(f"{API}/exams"))
    assert [e["id"] for e in exams] == [st["mock"]["id"]]

    start = ok(await cand.post(f"{API}/exams/{st['mock']['id']}/start"))
    st["attempt_id"] = start["attempt_id"]
    assert start["remaining_seconds"] and start["remaining_seconds"] > 0

    reading = ok(await cand.get(f"{API}/exams/reading/{st['reading']['id']}"))
    questions = [q for p in reading["parts"] for q in p["questions"]]
    assert len(questions) == 35
    assert all("correct_answer" not in q for q in questions)  # javob nomzodga oshkor bo'lmasin
    listening = ok(await cand.get(f"{API}/exams/listening/{st['listening']['id']}"))
    assert sum(len(p["questions"]) for p in listening["parts"]) == 35
    assert "correct_answer" not in str(listening)
    # Boshqa (biriktirilmagan) testni olib bo'lmaydi
    other = ok(await env["admin"].post(f"{API}/admin/reading-tests", json={**reading_payload(), "title": "Other"}), 201)
    assert (await cand.get(f"{API}/exams/reading/{other['id']}")).status_code == 403
    st["writing_content"] = ok(await cand.get(f"{API}/exams/writing/{st['writing']['id']}"))


async def test_05_reading_answers_submit_and_score(env):
    cand, st = env["cand"], env["state"]
    aid = st["attempt_id"]
    # 30 ta to'g'ri, 5 ta noto'g'ri (31..35)
    for n in range(1, 36):
        correct = n <= 30
        if n % 2 == 0:
            ans = "B" if correct else "C"
        else:
            ans = f"word{n}" if correct else "wrong"
        ok(await cand.put(f"{API}/exams/attempts/{aid}/answers/READING",
                          json={"question_number": n, "answer": ans}))
    # Idempotent: qayta yuborish yangilaydi
    ok(await cand.put(f"{API}/exams/attempts/{aid}/answers/READING", json={"question_number": 1, "answer": "word1"}))
    saved = ok(await cand.get(f"{API}/exams/attempts/{aid}/answers/READING"))
    assert saved["answered_count"] == 35 and saved["total_questions"] == 35

    sub = ok(await cand.post(f"{API}/exams/attempts/{aid}/submit/READING"))
    assert sub["raw_score"] == 30
    assert sub["is_checked"] is True
    assert sub["scaled_score"] == pytest.approx(67.9, abs=0.1)  # 28 dan yuqori: 65 + (30-28)*10/7
    # Topshirilgandan keyin javobni o'zgartirib bo'lmaydi
    r = await cand.put(f"{API}/exams/attempts/{aid}/answers/READING", json={"question_number": 1, "answer": "x"})
    assert r.status_code == 409


async def test_06_listening_answers_submit_and_score(env):
    cand, st = env["cand"], env["state"]
    aid = st["attempt_id"]
    gap_numbers = set(range(9, 15)) | set(range(30, 36))  # Part 2 va Part 6
    for n in range(1, 36):
        correct = n <= 33
        if n in gap_numbers:
            ans = f"ans{n}" if correct else "nope"
        else:
            ans = "A" if correct else "B"
        ok(await cand.put(f"{API}/exams/attempts/{aid}/answers/LISTENING",
                          json={"question_number": n, "answer": ans}))
    sub = ok(await cand.post(f"{API}/exams/attempts/{aid}/submit/LISTENING"))
    assert sub["raw_score"] == 33
    assert sub["is_checked"] is True
    assert sub["scaled_score"] == pytest.approx(72.1, abs=0.1)  # 65 + (33-28)*10/7


async def test_07_writing_submit_ai_draft_then_human_final(env, monkeypatch):
    cand, admin, st = env["cand"], env["admin"], env["state"]
    aid = st["attempt_id"]
    tasks = st["writing_content"]["tasks"]
    assert len(tasks) == 2
    sub = ok(await cand.post(f"{API}/exams/writing/{st['writing']['id']}/submit", json={
        "answers": [{
            "task_id": t["id"],
            "content": f"This is my answer for task {t['id']}. " * (25 if t["part_number"] == 2 else 5),
        } for t in tasks],
    }))
    result_id = sub["id"]
    assert sub["is_finalized"] is False and len(sub["answers"]) == 2
    # Ikkinchi marta topshirib bo'lmaydi
    again = await cand.post(f"{API}/exams/writing/{st['writing']['id']}/submit", json={
        "answers": [{"task_id": tasks[0]["id"], "content": "Another attempt text here."}]})
    assert again.status_code == 409

    # AI baholash (tashqi chaqiruvsiz): admin endpointi faqat QORALAMA qaytaradi.
    from app.modules.tests.writing.services import ai_grader

    async def fake_evaluate(self, *, spec, task, answer, word_count=0, cefr_level=None, max_raw=24):
        # Har bir amaldagi task rubrikasida maksimal ball; jami 16/16 -> scaled 75.
        return {"score": spec.max_score, "feedback": "Yaxshi.", "criteria_comments": {},
                "errors": [], "corrected_text": ""}

    monkeypatch.setattr(ai_grader.WritingAIGrader, "__init__", lambda self: setattr(self, "model", "fake-model"))
    monkeypatch.setattr(ai_grader.WritingAIGrader, "evaluate", fake_evaluate)
    draft = ok(await admin.post(f"{API}/admin/writing-tests/results/{result_id}/ai-evaluate", json={}))
    assert draft["evaluation_status"] == "AI_DRAFT" and draft["is_finalized"] is False
    assert draft["scaled_score"] == pytest.approx(57)  # 5 + 6 = 11/16 → amaldagi CEFR jadvali

    # AI qoralama mock skill'ni "tekshirilgan" qilmaydi
    status = ok(await cand.get(f"{API}/exams/attempts/{aid}/status"))
    writing = next(s for s in status if s["skill"] == "WRITING")
    assert writing["is_submitted"] is True and writing["is_checked"] is False

    # Inson yakuniy baho beradi
    final = ok(await admin.post(f"{API}/admin/writing-tests/results/{result_id}/grade", json={
        "raw_score": 14.5, "cefr_level": "C1", "feedback": "Yakuniy izoh.",
        "criterion_scores": [{"criterion": "grammar", "score": 11}],
    }))
    assert final["is_finalized"] is True and final["evaluation_status"] == "HUMAN_FINAL"
    status = ok(await cand.get(f"{API}/exams/attempts/{aid}/status"))
    writing = next(s for s in status if s["skill"] == "WRITING")
    assert writing["is_checked"] is True


async def test_08_speaking_upload_closes_exam_session_then_evaluation(env):
    cand, admin, st = env["cand"], env["admin"], env["state"]
    aid = st["attempt_id"]
    bad = await cand.post(f"{API}/exams/speaking/{st['speaking']['id']}/submit",
                          files={"file": ("a.wav", b"not-audio-data", "audio/wav")})
    assert bad.status_code == 415  # tarkib kengaytmaga mos emas
    sub = ok(await cand.post(f"{API}/exams/speaking/{st['speaking']['id']}/submit",
                             files={"file": ("a.wav", WAV_BYTES, "audio/wav")}))
    assert sub["audio_url"].endswith("/audio")
    st["speaking_submission_id"] = sub["id"]

    # 4 skill ham topshirildi: registration COMPLETED, nomzod sessiyasi bekor qilinadi.
    assert (await cand.get(f"{API}/exams/attempts/{aid}/status")).status_code == 401
    relogin = await cand.post(f"{API}/exams-auth/login", json={"exam_id": st["exam_access_id"]})
    assert relogin.status_code == 403  # "Imtihon yakunlangan. Qayta kirib bo'lmaydi."

    # Admin audioni eshita oladi
    audio = await admin.get(f"{API}/speaking-tests/submissions/{sub['id']}/audio")
    assert audio.status_code == 200 and audio.content.startswith(b"RIFF")

    pending = ok(await admin.get(f"{API}/admin/speaking-tests/pending"))
    assert any(p["id"] == sub["id"] for p in pending)
    res = ok(await admin.post(f"{API}/admin/speaking-tests/submissions/{sub['id']}/result", json={
        "overall_raw": 30, "fluency_coherence": 55, "cefr_level": "B2", "comment": "Yaxshi."}))
    assert res["overall_band"] == 66  # raw 30 → jadval → 66


async def test_09_overall_result_finalized_and_visible_by_qr(env):
    admin, anon, st = env["admin"], env["cand"], env["state"]
    aid = st["attempt_id"]
    detail = ok(await admin.get(f"{API}/admin/mock-exams/attempts/{aid}"))
    assert detail["is_finished"] is True
    scores = {s["skill"]: s["scaled_score"] for s in detail["skills"]}
    assert scores["READING"] == pytest.approx(67.9, abs=0.1)
    assert scores["LISTENING"] == pytest.approx(72.1, abs=0.1)
    assert scores["WRITING"] == 67 and scores["SPEAKING"] == 66  # Writing: 14.5/16 → 67; Speaking: 30/36 → 66
    assert all(s["is_checked"] for s in detail["skills"])

    regs = ok(await admin.get(f"{API}/admin/mock-exams/sessions/{st['session']['id']}/registrations"))
    qr = regs[0]["qr_token"]
    public = ok(await anon.get(f"{API}/public/results/by-qr/{qr}"))
    blob = str(public)
    assert "FINALIZED" in blob or "finalized" in blob.lower()
    assert (await anon.get(f"{API}/public/results/by-qr/{'x' * 24}")).status_code == 404

    # Yakunlangan natijani admin o'zgartira olmaydi
    r = await admin.post(f"{API}/admin/mock-exams/attempts/{aid}/manual-grade",
                         json={"skill": "SPEAKING", "scaled_score": 10})
    assert r.status_code == 409


# --------------------------------------------------------------------------
# Duman-testlar: PDF generatsiya va OMR
# --------------------------------------------------------------------------
async def test_10_lock_and_answer_sheet_pdf(env):
    admin, st = env["admin"], env["state"]
    lock = ok(await admin.post(f"{API}/admin/reading-tests/{st['reading']['id']}/lock"), 200, 201)
    assert lock
    pdf = await admin.get(f"{API}/admin/reading-tests/{st['reading']['id']}/answer-sheet")
    assert pdf.status_code == 200, pdf.text[:500]
    assert pdf.content.startswith(b"%PDF")
    st["answer_sheet_pdf"] = pdf.content


async def test_11_question_paper_pdf_works_without_master_templates(env):
    """Master PDF bo'lsa official layout, bo'lmasa o'qiladigan fallback PDF qaytadi."""
    admin, st = env["admin"], env["state"]
    r = await admin.get(f"{API}/admin/reading-tests/{st['reading']['id']}/paper-pdf")
    assert r.status_code == 200, r.text[:500]
    assert r.content.startswith(b"%PDF")


async def test_12_permit_and_result_pdf_for_candidate(env):
    admin, st = env["admin"], env["state"]
    regs = ok(await admin.get(f"{API}/admin/mock-exams/sessions/{st['session']['id']}/registrations"))
    reg = regs[0]
    # Ro'yxatdan o'tgan nomzodning asosiy (dummy) akkaunti sifatida
    as_candidate = {"X-Test-User": str(reg["user_id"])}
    permit = await admin.get(f"{API}/mock-exams/center/me/registrations/{reg['id']}/permit.pdf", headers=as_candidate)
    assert permit.status_code == 200, permit.text[:400]
    assert permit.content.startswith(b"%PDF")
    results = ok(await admin.get(f"{API}/mock-exams/center/me/results", headers=as_candidate))
    assert len(results) == 1
    pdf = await admin.get(f"{API}/mock-exams/center/me/results/{results[0]['id']}/pdf", headers=as_candidate)
    assert pdf.status_code == 200, pdf.text[:400]
    assert pdf.content.startswith(b"%PDF")
    # Boshqa nomzodning ruxsat varag'ini olib bo'lmaydi
    other = await admin.get(f"{API}/mock-exams/center/me/registrations/{reg['id']}/permit.pdf")  # admin o'zi (egasi emas)
    assert other.status_code in (403, 404)


async def test_13_omr_reads_rendered_answer_sheet(env, tmp_path):
    """OMR smoke: javoblar varaqasini 200 dpi rasmga aylantirib o'qiydi (belgilanmagan varaq)."""
    import shutil
    import subprocess
    if not shutil.which("pdftoppm"):
        pytest.skip("pdftoppm (poppler) o'rnatilmagan")
    admin, st = env["admin"], env["state"]
    pdf = tmp_path / "sheet.pdf"
    pdf.write_bytes(st["answer_sheet_pdf"])
    subprocess.run(["pdftoppm", "-png", "-r", "200", "-f", "1", "-l", "1", str(pdf), str(tmp_path / "page")], check=True)
    png = next(tmp_path.glob("page*.png")).read_bytes()
    out = ok(await admin.post(f"{API}/admin/mock-exams/grade/answer-sheets",
                              files=[("images", (f"{st['mk_id']}.png", png, "image/png"))]))
    assert out["total"] == 1
    r = out["results"][0]
    assert r["candidate_id"] == st["mk_id"]  # fayl nomidan aniqlanadi
    assert "listening" in r and "reading" in r
    # Belgilanmagan varaqda hech bir javob "topilmasligi" kerak (soxta javob yaratmasin)
    assert not any(v for v in (r["listening"] or {}).values() if v)
    assert not any(v for v in (r["reading"] or {}).values() if v)


async def test_14_omr_rejects_bad_uploads(env):
    admin = env["admin"]
    r = await admin.post(f"{API}/admin/mock-exams/grade/answer-sheets",
                         files=[("images", ("x.png", b"not an image", "image/png"))])
    assert r.status_code == 415  # tarkib MIME'ga mos emas
    r = await admin.post(f"{API}/admin/mock-exams/grade/answer-sheets",
                         files=[("images", ("x.txt", b"hello", "text/plain"))])
    assert r.status_code == 415
    # Yaroqli PNG sarlavhali, lekin buzuq rasm: 500 emas, qo'lda ko'rib chiqishga yo'naltiriladi
    broken = b"\x89PNG\r\n\x1a\n" + b"\x00" * 32
    out = ok(await admin.post(f"{API}/admin/mock-exams/grade/answer-sheets",
                              files=[("images", ("MK-010100-001.png", broken, "image/png"))]))
    assert out["manual_review_count"] == 1


# --------------------------------------------------------------------------
# Birlashtirilgan yo'llar: check-in va qo'lda baholash (bitta amalga oshirish)
# --------------------------------------------------------------------------
async def _apply_candidate(env, slug: str, first: str, phone: str) -> dict:
    import io
    from PIL import Image
    cand = env["cand"]
    buf = io.BytesIO(); Image.new("RGB", (40, 30), (10, 90, 200)).save(buf, format="PNG")
    photo = ok(await cand.post(f"{API}/apply/{slug}/photo", files={"file": ("p.png", buf.getvalue(), "image/png")}), 201)
    return ok(await cand.post(f"{API}/apply/{slug}", json={
        "first_name": first, "last_name": "Test", "gender": "M", "birth_date": "2000-05-17",
        "phone": phone, "photo_url": photo["photo_url"], "address": "Toshkent",
    }), 201)


async def test_15_single_check_in_and_grading_paths(env):
    admin, st = env["admin"], env["state"]
    # Listening lock'i audio talab qiladi (testda audio yo'q) — lock qilingan versiya bazaga qo'yiladi.
    from app.modules.exams.paper.models import ExamVersion, PaperModule
    async with dbm.AsyncSessionLocal() as s:
        s.add(ExamVersion(module=PaperModule.LISTENING, exam_id=st["listening"]["id"], version_number=1,
                          total_questions=35, answer_key={}, is_locked=True))
        await s.commit()
    session = ok(await admin.post(f"{API}/admin/mock-exams/sessions", json={
        "mock_exam_id": st["mock"]["id"], "exam_date": "2030-02-01T09:00:00",
        "location_address": "Toshkent, Qog'oz markazi", "delivery_mode": "PAPER", "capacity": 5,
    }), 201)
    slug = session["registration_slug"]
    r1 = await _apply_candidate(env, slug, "Bir", "+998901110001")
    r2 = await _apply_candidate(env, slug, "Ikki", "+998901110002")
    regs = {r["student_id"]: r for r in ok(await admin.get(f"{API}/admin/mock-exams/sessions/{session['id']}/registrations"))}

    # 1) Mock Center check-in (strict): attempt + qog'oz versiyalari biriktiriladi
    ci = ok(await admin.post(f"{API}/admin/mock-exams/center/check-in",
                             json={"student_id": r1["exam_id"], "session_id": session["id"]}))
    assert ci["allowed"] and not ci["already_checked_in"] and ci["attempt_id"]
    detail = ok(await admin.get(f"{API}/admin/mock-exams/attempts/{ci['attempt_id']}"))
    by_skill = {s["skill"]: s for s in detail["skills"]}
    assert len(by_skill) == 4
    again = ok(await admin.post(f"{API}/admin/mock-exams/center/check-in",
                                json={"student_id": r1["exam_id"], "session_id": session["id"]}))
    assert again["already_checked_in"] and again["attempt_id"] == ci["attempt_id"]

    # 2) Admin "keldi" yo'li ham xuddi shu funksiya: attempt bo'ladi, ikkinchi marta 400
    reg2 = regs[r2["exam_id"]]
    conf = ok(await admin.post(f"{API}/admin/mock-exams/registrations/{reg2['id']}/confirm-attendance"))
    assert conf["status"] == "CHECKED_IN"
    assert (await admin.post(f"{API}/admin/mock-exams/registrations/{reg2['id']}/confirm-attendance")).status_code == 400

    # 3) Qo'lda baholash: ikki endpoint bitta mantiq
    aid = ci["attempt_id"]
    wr = ok(await admin.post(f"{API}/admin/mock-exams/center/attempts/{aid}/skills/WRITING/score",
                             json={"scaled_score": 66}))
    assert wr["is_checked"] is True and wr["cefr_level"] == "C1"
    assert (await admin.post(f"{API}/admin/mock-exams/center/attempts/{aid}/skills/READING/score",
                             json={"scaled_score": 50})).status_code == 400  # faqat Writing/Speaking
    sp = ok(await admin.post(f"{API}/admin/mock-exams/attempts/{aid}/manual-grade",
                             json={"skill": "SPEAKING", "scaled_score": 52}))
    assert sp["cefr_level"] == "B2"
    # Yakunlanmagan (Reading/Listening yo'q): finalize 409, holatni qo'lda o'zgartirmaydi
    fin = await admin.post(f"{API}/admin/mock-exams/center/attempts/{aid}/finalize", json={})
    assert fin.status_code == 409
    detail = ok(await admin.get(f"{API}/admin/mock-exams/attempts/{aid}"))
    assert detail["is_finished"] is False
