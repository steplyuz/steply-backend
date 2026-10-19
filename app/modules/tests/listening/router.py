from fastapi import APIRouter, Depends, status, HTTPException, UploadFile, File, Query, Request
from fastapi.responses import Response
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from typing import List

from app.core.database import get_db
from app.modules.auth.dependencies import get_current_user, require_role
from app.modules.auth.models import User, UserRole

# Servis va Sxemalar
from app.modules.tests.content_access import (
    authorize_test_content,
    resolve_content_viewer,
    visible_catalog_filter,
)
from .services import ListeningService
from app.modules.tests.attempts.schemas import AnswerSaveRequest, AttemptStateResponse, SavedAnswer
from .schemas import (
    ListeningExamResponse,
    ListeningExamAdminResponse,
    ListeningExamCreate,
    ListeningExamUpdate,
    ListeningResultResponse,
    ListeningSubmission,
    ListeningResultDetailResponse
)
from app.modules.exams.paper.schemas import (
    AnswerKeyResponse, ExamVersionResponse, LockResultResponse,
)

# `router` — foydalanuvchi yo'llari: /listening-tests/...
# `admin_router` — faqat admin yo'llari: /admin/listening-tests/...
router = APIRouter(
    prefix="/listening-tests",
    tags=["Listening — Public"],
)
from app.modules.tests.json_import import IMPORT_OPENAPI_EXTRA, read_import_json
from app.modules.practice import service as _prs
from app.modules.exams.paper.template_paper_pdf import TemplateFitError

admin_router = APIRouter(
    prefix="/admin/listening-tests",
    tags=["Listening — Admin"],
    dependencies=[Depends(require_role(UserRole.ADMIN, UserRole.SUPER_ADMIN))],
)


@admin_router.post("/maps/upload")
async def upload_listening_map(file: UploadFile = File(...)):
    """Listening partlari uchun xarita rasmini tekshirib, statik media katalogiga saqlaydi."""
    import aiofiles
    import os
    from app.core.paths import STATIC_DIR
    from app.core.file_storage import generated_name, safe_suffix

    allowed = {".jpg", ".jpeg", ".png", ".webp", ".gif"}
    ext = safe_suffix(os.path.splitext(file.filename or "")[1].lower(), allowed)
    data = await file.read(10 * 1024 * 1024 + 1)
    if not data:
        raise HTTPException(400, "Rasm fayli bo'sh.")
    if len(data) > 10 * 1024 * 1024:
        raise HTTPException(413, "Rasm hajmi 10 MB dan oshmasligi kerak.")
    valid = (
        (data.startswith(b"\xff\xd8\xff") and ext in {".jpg", ".jpeg"})
        or (data.startswith(b"\x89PNG\r\n\x1a\n") and ext == ".png")
        or (data[:4] == b"RIFF" and data[8:12] == b"WEBP" and ext == ".webp")
        or (data.startswith((b"GIF87a", b"GIF89a")) and ext == ".gif")
    )
    if not valid:
        raise HTTPException(415, "Rasm tarkibi fayl kengaytmasiga mos kelmadi.")
    directory = STATIC_DIR / "listening_maps"
    directory.mkdir(parents=True, exist_ok=True)
    filename = generated_name(ext)
    async with aiofiles.open(directory / filename, "wb") as output:
        await output.write(data)
    from app.core.media import media_url
    return {"url": media_url(f"/static/listening_maps/{filename}"), "filename": filename, "size": len(data)}


@admin_router.post("/audio/upload")
async def upload_listening_audio(
    file: UploadFile = File(...),
):
    """Listening audio-ni lokal static storage'ga yuklash. Keyin URL JSON'da ishlatiladi."""
    service = ListeningService(None)
    return await service.upload_audio(file)

@admin_router.post(
    "",
    response_model=ListeningExamAdminResponse,
    status_code=status.HTTP_201_CREATED,
    summary="JSON fayl yoki body orqali Listening test yaratish",
    openapi_extra=IMPORT_OPENAPI_EXTRA,
)
async def import_listening_json(
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """
    To'liq Listening testni JSON fayldan bir requestda import qiladi.

    Qabul qilinadigan variantlar:
    - root object
    - {"exam": {...}} wrapper
    - `level` yoki `cefr_level`
    - `duration` yoki `duration_minutes`
    - `question` yoki `text`
    - `correct_answer: "A"` yoki `["A"]`
    - options: ["A", "B", "C"] yoki [{"label":"A","value":"..."}]
    - TEXT_MATCH/MAP_MATCH/HEADINGS_MATCH kabi legacy type nomlari
    Audio draft bosqichida optional; final /lock paytida tekshiriladi.
    """
    raw = await read_import_json(request)
    payload = raw.get("exam") if isinstance(raw, dict) and isinstance(raw.get("exam"), dict) else raw
    if not isinstance(payload, dict):
        raise HTTPException(422, "JSON root object yoki {\"exam\": {...}} bo'lishi kerak.")

    payload = dict(payload)
    if "cefr_level" not in payload and "level" in payload:
        payload["cefr_level"] = payload["level"]
    if "duration_minutes" not in payload and "duration" in payload:
        payload["duration_minutes"] = payload["duration"]
    if "parts" not in payload and isinstance(payload.get("sections"), list):
        payload["parts"] = payload.pop("sections")
    normalized_parts = []
    for part in payload.get("parts") or []:
        p = dict(part)
        if "instruction" not in p and "description" in p:
            p["instruction"] = p["description"]
        if "questions" not in p and isinstance(p.get("items"), list):
            p["questions"] = p["items"]
        normalized_parts.append(p)
    payload["parts"] = normalized_parts
    try:
        data = ListeningExamCreate.model_validate(payload)
    except Exception as exc:
        raise HTTPException(422, detail=f"Listening JSON schema xatosi: {exc}") from exc

    service = ListeningService(db)
    return await service.create_exam(data)


@router.get("", response_model=List[ListeningExamResponse])
async def list_listening_tests(request: Request, db: AsyncSession = Depends(get_db)):
    """Ko'rishga ruxsat berilgan faol imtihonlar ro'yxati (content_access.py)"""
    viewer = await resolve_content_viewer(request, db)
    filters = await visible_catalog_filter(db, viewer, "listening")
    service = ListeningService(db)
    return await service.get_all_exams(**filters)


@router.get("/{exam_id}", response_model=ListeningExamResponse)
async def get_listening_test(exam_id: str, request: Request, db: AsyncSession = Depends(get_db)):
    """Imtihon detallari va savollari (ruxsat tekshiriladi)"""
    service = ListeningService(db)
    exam = await service.get_exam_by_id(exam_id)
    if not exam:
        raise HTTPException(status_code=404, detail="Listening test topilmadi")
    viewer = await resolve_content_viewer(request, db)
    await authorize_test_content(db, viewer, exam, "listening")
    return exam

# DIQQAT: literal '/results' har doim '/{exam_id}' dan OLDIN e'lon qilinadi,
# aks holda 'results' exam_id deb talqin qilinib 404 qaytadi.
@admin_router.get("/results")
async def get_all_listening_results(db: AsyncSession = Depends(get_db)):
    from .models import ListeningResult, ListeningTest
    stmt = select(ListeningResult).order_by(ListeningResult.created_at.desc())
    return (await db.execute(stmt)).scalars().all()


@admin_router.post("/{exam_id}/part-mini-tests")
async def create_listening_part_mini_tests(
    exam_id: str,
    db: AsyncSession = Depends(get_db),
):
    """Har bir partdan mustaqil mini-test yaratadi; takroriy chaqiruv dublikat yaratmaydi."""
    return await ListeningService(db).create_part_mini_tests(exam_id)


@admin_router.get(
    "",
    response_model=List[ListeningExamAdminResponse],
    summary="Admin: barcha Listening testlari",
)
async def list_listening_tests_admin(db: AsyncSession = Depends(get_db)):
    """Admin katalogi: faol/nofaol va barcha kontent turidagi testlar."""
    return await ListeningService(db).get_all_exams()


@admin_router.get("/{exam_id}", response_model=ListeningExamAdminResponse)
async def get_listening_test_admin(exam_id: str, db: AsyncSession = Depends(get_db)):
    """Admin uchun imtihon detallari"""
    service = ListeningService(db)
    exam = await service.get_exam_by_id(exam_id)
    if not exam:
        raise HTTPException(status_code=404, detail="Listening test topilmadi")
    return exam


@admin_router.put("/{exam_id}", response_model=ListeningExamAdminResponse)
async def update_listening_exam(
    exam_id: str,
    data: ListeningExamUpdate,
    db: AsyncSession = Depends(get_db)
):
    """Imtihon ma'lumotlarini yangilash"""
    service = ListeningService(db)
    return await service.update_exam(exam_id, data)


@admin_router.delete("/{exam_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_listening_exam(
    exam_id: str,
    db: AsyncSession = Depends(get_db)
):
    """Imtihonni o'chirish"""
    service = ListeningService(db)
    success = await service.delete_exam(exam_id)
    if not success:
        raise HTTPException(404, detail="Imtihon topilmadi")


@router.post("/{exam_id}/submit", response_model=ListeningResultDetailResponse)
async def submit_listening_answers(
    exam_id: str,
    submission: ListeningSubmission,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user)
):
    """
    Javoblarni yuborish va natijani olish.
    Mock Exam tizimiga ulash uchun 'exam_attempt_id' yuborish mumkin.
    """
    # URL'dagi exam_id — yagona haqiqiy manba (body'dagi eski `exam_id`
    # maydoni bilan boshqa skill'lar — reading/speaking — kabi bir xil
    # uslubga keltirildi).
    submission.exam_id = exam_id

    service = ListeningService(db)
    return await service.submit_exam_and_get_result(
        user_id=user.id,
        data=submission
    )


@router.get("/me/results", response_model=List[ListeningResultResponse])
async def get_my_listening_results(
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user)
):
    """Foydalanuvchining shaxsiy natijalari tarixi"""
    service = ListeningService(db)
    return await service.get_user_results(user.id)


@router.get("/me/results/{result_id}", response_model=ListeningResultDetailResponse)
async def get_listening_result_detail(
    result_id: int,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user)
):
    """
    Natija tahlili (Review).
    Faqat o'zining natijasini ko'ra oladi.
    """
    service = ListeningService(db)
    result = await service.get_result_with_review(result_id, user.id)

    if not result:
        raise HTTPException(status_code=404, detail="Natija topilmadi yoki ruxsat yo'q")

    return result


# =================================================================
#  PRACTICE ATTEMPT: start -> save answer -> resume -> finish -> result
#  Javoblar har saqlashda DB'ga yoziladi; frontend holati manba emas.
# =================================================================
def _listening_spec():
    from app.modules.tests.attempts import common as at
    from app.modules.exams.mock.models import SkillType
    return at, at.spec_for(SkillType.LISTENING)


@router.post("/{exam_id}/attempts", response_model=AttemptStateResponse)
async def start_listening_attempt(
    exam_id: str,
    part_number: int | None = Query(default=None, ge=1),
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Urinishni boshlaydi; tugallanmagan urinish bo'lsa o'shani qaytaradi (resume)."""
    at, spec = _listening_spec()
    mode = "PART_PRACTICE" if part_number is not None else "FULL_PRACTICE"
    attempt = await _prs.start_practice_attempt(db, spec, user.id, exam_id, mode=mode, part_number=part_number)
    await db.commit()
    return await _prs.practice_state(db, spec, user.id, attempt.id)


@router.get("/attempts/{attempt_id}", response_model=AttemptStateResponse)
async def get_listening_attempt(
    attempt_id: int,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Resume: holat + saqlangan javoblar."""
    at, spec = _listening_spec()
    return await _prs.practice_state(db, spec, user.id, attempt_id)


@router.put("/attempts/{attempt_id}/answers", response_model=SavedAnswer)
async def save_listening_answer(
    attempt_id: int,
    data: AnswerSaveRequest,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Bitta javobni saqlaydi/yangilaydi (idempotent)."""
    at, spec = _listening_spec()
    try:
        row = await _prs.save_practice_answer(
            db, spec, user.id, attempt_id, data.question_id, data.question_number, data.normalized()
        )
        await db.commit()
    except Exception:
        await db.rollback()
        raise
    return at.answer_item(row)


@router.post("/attempts/{attempt_id}/finish", response_model=ListeningResultDetailResponse)
async def finish_listening_attempt(
    attempt_id: int,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Saqlangan javoblardan baholaydi va natijani DB'ga yozadi. Takroriy chaqiruv — mavjud natija."""
    at, spec = _listening_spec()
    result = await _prs.finalize_practice(db, spec, user.id, attempt_id)
    return await ListeningService(db).get_result_with_review(result.id, user.id)


@router.get("/attempts/{attempt_id}/result", response_model=ListeningResultDetailResponse)
async def get_listening_attempt_result(
    attempt_id: int,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    at, spec = _listening_spec()
    state = await _prs.practice_state(db, spec, user.id, attempt_id)
    if state["result_id"] is None:
        raise HTTPException(404, "Natija hali mavjud emas")
    return await ListeningService(db).get_result_with_review(state["result_id"], user.id)


# =================================================================
#  PAPER / ANSWER-KEY / VERSIONING (ADMIN ONLY) — Band 8, 15, 16, 21
# =================================================================
@admin_router.get(
    "/{exam_id}/answer-key",
    response_model=AnswerKeyResponse,
)
async def get_listening_answer_key(
    exam_id: str,
    version: int = None,
    db: AsyncSession = Depends(get_db),
):
    """Answer key faqat admin uchun. Studentga hech qachon berilmaydi (Band 22)."""
    service = ListeningService(db)
    v = await service.get_answer_key(exam_id, version)
    return AnswerKeyResponse(
        exam_id=exam_id,
        version_number=v.version_number,
        total_questions=v.total_questions,
        is_locked=v.is_locked,
        answer_key=v.answer_key,
    )


@admin_router.get(
    "/{exam_id}/versions",
    response_model=List[ExamVersionResponse],
)
async def list_listening_versions(exam_id: str, db: AsyncSession = Depends(get_db)):
    service = ListeningService(db)
    return await service.list_versions(exam_id)


@admin_router.post(
    "/{exam_id}/new-version",
    response_model=ExamVersionResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_listening_new_version(exam_id: str, db: AsyncSession = Depends(get_db)):
    """Locked Listening test uchun yangi editable draft versiya ochadi."""
    service = ListeningService(db)
    return await service.create_new_version(exam_id)


@admin_router.post(
    "/{exam_id}/lock",
    response_model=LockResultResponse,
)
async def lock_listening_exam(
    exam_id: str,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """
    Examni PDF/OMR generatsiyasi uchun tayyor holatga o'tkazadi (lock).
    Validatsiyadan o'tmasa (Band 21) lock qilinmaydi va aniq xatolar qaytariladi.
    """
    service = ListeningService(db)
    version, errors = await service.lock_exam(exam_id, locked_by_user_id=user.id)
    if errors:
        return LockResultResponse(success=False, version=None, errors=errors)
    return LockResultResponse(success=True, version=version, errors=[])


# =================================================================
#  PAPER PDF / ANSWER SHEET (ADMIN ONLY) — Band 9, 10, 11
# =================================================================
@admin_router.get("/{exam_id}/paper-pdf")
async def get_listening_paper_pdf(exam_id: str, db: AsyncSession = Depends(get_db)):
    """Student uchun savol qog'ozi (javobsiz). Faqat locked exam uchun ishlaydi."""
    service = ListeningService(db)
    try:
        pdf_bytes = await service.generate_paper_pdf(exam_id)
    except (FileNotFoundError, TemplateFitError) as exc:
        raise HTTPException(
            503,
            f"Qog'oz shabloni topilmadi yoki mos kelmadi: {exc}. "
            "static/templates/ papkasiga to'liq LISTENING_MASTER.pdf faylini joylang.",
        ) from exc
    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={"Content-Disposition": f'inline; filename="listening-{exam_id}-paper.pdf"'},
    )


@admin_router.get("/{exam_id}/answer-sheet")
async def get_listening_answer_sheet_pdf(exam_id: str, db: AsyncSession = Depends(get_db)):
    """OMR Answer Sheet — deterministik bubble geometriya bilan (Band 11)."""
    service = ListeningService(db)
    pdf_bytes = await service.generate_answer_sheet_pdf(exam_id)
    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={"Content-Disposition": f'inline; filename="listening-{exam_id}-answer-sheet.pdf"'},
    )
