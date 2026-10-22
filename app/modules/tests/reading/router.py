from fastapi import APIRouter, Depends, status, HTTPException, Query, Request, UploadFile, File
from pathlib import Path
import aiofiles
from app.core.paths import STATIC_DIR
from app.core.file_storage import generated_name, safe_suffix
from fastapi.responses import Response
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from typing import List

from app.core.database import get_db
from app.modules.auth.dependencies import get_current_user, require_role
from app.modules.auth.models import User, UserRole

# Service & Schemas
from app.modules.tests.content_access import (
    authorize_test_content,
    resolve_content_viewer,
    visible_catalog_filter,
)
from .services import ReadingService
from app.modules.tests.attempts.schemas import AnswerSaveRequest, AttemptStateResponse, SavedAnswer
from .schemas import (
    ReadingTestCreate,
    ReadingTestUpdate,
    ReadingTestResponse,
    ReadingTestAdminResponse,
    ReadingSubmitRequest,
    ReadingResultResponse,
    ReadingResultDetailResponse,
)
from app.modules.exams.paper.schemas import (
    AnswerKeyResponse, ExamVersionResponse, LockResultResponse,
)

from app.modules.tests.json_import import IMPORT_OPENAPI_EXTRA, read_import_json
from app.modules.practice import service as _prs
from app.modules.exams.paper.template_paper_pdf import TemplateFitError

router = APIRouter(
    prefix="/reading-tests",
    tags=["Reading — Public"],
)
admin_router = APIRouter(
    prefix="/admin/reading-tests",
    tags=["Reading — Admin"],
    dependencies=[Depends(require_role(UserRole.ADMIN, UserRole.SUPER_ADMIN))],
)


def _normalize_reading_payload(payload: dict) -> dict:
    """JSON payload ni ReadingTestCreate ga mos formatga o'tkazadi."""
    payload = dict(payload)
    if "parts" not in payload and isinstance(payload.get("sections"), list):
        payload["parts"] = payload.pop("sections")
    parts = []
    for part in payload.get("parts") or []:
        p = dict(part)
        if "questions" not in p and isinstance(p.get("items"), list):
            p["questions"] = p["items"]
        qs = []
        for q in p.get("questions") or []:
            item = dict(q)
            if "text" not in item and "question" in item:
                item["text"] = item["question"]
            if "correct_answer" not in item:
                item["correct_answer"] = item.get("answer", item.get("correct", item.get("key", [])))
            if isinstance(item.get("correct_answer"), str):
                item["correct_answer"] = [x.strip() for x in item["correct_answer"].split("/") if x.strip()]
            opts = []
            for i, opt in enumerate(item.get("options") or item.get("choices") or []):
                if isinstance(opt, str):
                    opts.append({"label": chr(65 + i), "value": opt})
                else:
                    d = dict(opt)
                    d["label"] = str(d.get("label") or d.get("key") or chr(65 + i))
                    d["value"] = str(d.get("value") or d.get("text") or d.get("content") or "")
                    opts.append(d)
            item["options"] = opts
            qs.append(item)
        p["questions"] = qs
        parts.append(p)
    payload["parts"] = parts
    return payload


# =================================================================
#  IMPORT — JSON fayl YOKI JSON body orqali (ikkalasi ham ishlaydi)
# =================================================================



@admin_router.post("/images/upload")
async def upload_reading_images_image(file: UploadFile = File(...)):
    """Admin uchun rasm yuklash; 10 MB gacha va fayl imzosi tekshiriladi."""
    allowed = {".jpg", ".jpeg", ".png", ".webp", ".gif"}
    ext = safe_suffix(Path(file.filename or "").suffix.lower(), allowed)
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
    directory = STATIC_DIR / "reading_images"
    directory.mkdir(parents=True, exist_ok=True)
    filename = generated_name(ext)
    async with aiofiles.open(directory / filename, "wb") as output:
        await output.write(data)
    return {"url": "/static/reading_images/" + filename, "filename": filename, "size": len(data)}


@admin_router.post(
    "",
    response_model=ReadingTestAdminResponse,
    status_code=status.HTTP_201_CREATED,
    summary="JSON fayldan yoki body dan Reading test yaratish",
    openapi_extra=IMPORT_OPENAPI_EXTRA,
)
async def import_reading_json(
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """
    Reading testni JSON orqali import qiladi.
    - **Fayl orqali**: multipart/form-data da `file` field ga JSON fayl yuboring.
    - **Body orqali**: application/json content-type bilan JSON body yuboring.
    Ikkala usul ham bir xil natija beradi.
    """
    raw = await read_import_json(request)

    payload = raw.get("exam") if isinstance(raw, dict) and isinstance(raw.get("exam"), dict) else raw
    if not isinstance(payload, dict):
        raise HTTPException(422, "JSON root object bo'lishi kerak")

    payload = _normalize_reading_payload(payload)

    try:
        data = ReadingTestCreate.model_validate(payload)
    except Exception as exc:
        msg = f"Reading JSON schema xatosi: {exc}"
        raise HTTPException(422, msg) from exc

    return await ReadingService(db).create_test(data)


# =================================================================
#  READ (O'QISH) - ruxsat content_access.py orqali tekshiriladi
# =================================================================
@router.get(
    "",
    response_model=List[ReadingTestResponse],
)
async def list_reading_tests(
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """Ko'rishga ruxsat berilgan faol Reading testlar ro'yxati.

    Anonim va FREE foydalanuvchi faqat bepul practice testlarni, PLUS/PRO
    barcha platforma practice testlarini ko'radi; mock imtihon va tashkilot
    testlari bu ro'yxatda chiqmaydi (content_access.py ga qarang).
    """
    viewer = await resolve_content_viewer(request, db)
    filters = await visible_catalog_filter(db, viewer, "reading")
    service = ReadingService(db)
    return await service.get_all_tests(**filters)


@router.get(
    "/{test_id}",
    response_model=ReadingTestResponse,
)
async def get_reading_test(
    test_id: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """Testni ishlash uchun to'liq ma'lumotlari bilan olish (ruxsat tekshiriladi)"""
    service = ReadingService(db)
    test = await service.get_test_by_id(test_id)
    if not test:
        raise HTTPException(status_code=404, detail="Reading test topilmadi")
    viewer = await resolve_content_viewer(request, db)
    await authorize_test_content(db, viewer, test, "reading")
    return test

# DIQQAT: literal '/results' har doim '/{test_id}' dan OLDIN e'lon qilinadi,
# aks holda 'results' test_id deb talqin qilinib 404 qaytadi.
@admin_router.get("/results")
async def get_all_reading_results(db: AsyncSession = Depends(get_db)):
    from .models import ReadingResult, ReadingTest
    stmt = select(ReadingResult).order_by(ReadingResult.created_at.desc())
    return (await db.execute(stmt)).scalars().all()


@admin_router.post("/{test_id}/part-mini-tests")
async def create_reading_part_mini_tests(
    test_id: str,
    db: AsyncSession = Depends(get_db),
):
    """Har bir partdan mustaqil mini-test yaratadi; takroriy chaqiruv dublikat yaratmaydi."""
    return await ReadingService(db).create_part_mini_tests(test_id)


@admin_router.get(
    "",
    response_model=List[ReadingTestAdminResponse],
    summary="Admin: barcha Reading testlari",
)
async def list_reading_tests_admin(db: AsyncSession = Depends(get_db)):
    """Admin katalogi: faol/nofaol va barcha kontent turidagi testlar."""
    return await ReadingService(db).get_all_tests()


@admin_router.get(
    "/{test_id}",
    response_model=ReadingTestAdminResponse,
)
async def get_reading_test_admin(
    test_id: str,
    db: AsyncSession = Depends(get_db),
):
    """Admin uchun test ma'lumotlari"""
    service = ReadingService(db)
    test = await service.get_test_by_id(test_id)
    if not test:
        raise HTTPException(status_code=404, detail="Reading test topilmadi")
    return test


# =================================================================
#  3. UPDATE (YANGILASH) - ADMIN ONLY
# =================================================================
@admin_router.put(
    "/{test_id}",
    response_model=ReadingTestAdminResponse,
)
async def update_reading_test(
    test_id: str,
    data: ReadingTestUpdate,
    db: AsyncSession = Depends(get_db),
):
    """Test ma'lumotlarini yangilash"""
    service = ReadingService(db)
    return await service.update_test(test_id, data)


# =================================================================
#  4. DELETE (O'CHIRISH) - ADMIN ONLY
# =================================================================
@admin_router.delete(
    "/{test_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def delete_reading_test(
    test_id: str,
    force: bool = Query(False, description="Faqat SUPER_ADMIN: natija va urinishlar bilan birga o'chirish"),
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Testni o'chirish. Natija bor bo'lsa 409 (faolsizlantiring yoki super admin force=true)."""
    if force and user.global_role != UserRole.SUPER_ADMIN:
        raise HTTPException(status_code=403, detail="force=true faqat super admin uchun")
    service = ReadingService(db)
    success = await service.delete_test(test_id, force=force)
    if not success:
        raise HTTPException(status_code=404, detail="Test topilmadi")


# =================================================================
#  5. ACTIONS (JAVOB BERISH VA NATIJALAR) - AUTHENTICATED USER
# =================================================================
@router.post(
    "/{test_id}/submit",
    response_model=ReadingResultDetailResponse,
)
async def submit_reading_answers(
    test_id: str,
    data: ReadingSubmitRequest,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """
    Javoblarni yuborish.
    Agar 'exam_attempt_id' yuborilsa, Mock Exam tizimiga ulanadi.
    """
    service = ReadingService(db)
    return await service.submit_answers(
        user_id=user.id,
        test_id=test_id,
        data=data,
    )


@router.get(
    "/me/results",
    response_model=List[ReadingResultResponse],
)
async def get_my_reading_results(
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Foydalanuvchining shaxsiy Reading natijalari tarixi"""
    service = ReadingService(db)
    return await service.get_user_results(user.id)


@router.get(
    "/me/results/{result_id}",
    response_model=ReadingResultDetailResponse,
)
async def get_reading_result_detail(
    result_id: int,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """
    Natija tahlili (Review).
    Faqat o'zining natijasini ko'ra oladi.
    """
    service = ReadingService(db)
    result = await service.get_result_with_review(result_id, user.id)
    if not result:
        raise HTTPException(status_code=404, detail="Natija topilmadi yoki ruxsat yo'q")
    return result


# =================================================================
#  PRACTICE ATTEMPT: start -> save answer -> resume -> finish -> result
#  Javoblar har saqlashda DB'ga yoziladi; frontend holati manba emas.
# =================================================================
def _reading_spec():
    from app.modules.tests.attempts import common as at
    from app.modules.exams.mock.models import SkillType
    return at, at.spec_for(SkillType.READING)


@router.post("/{test_id}/attempts", response_model=AttemptStateResponse)
async def start_reading_attempt(
    test_id: str,
    part_number: int | None = Query(default=None, ge=1),
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Urinishni boshlaydi; tugallanmagan urinish bo'lsa o'shani qaytaradi (resume)."""
    at, spec = _reading_spec()
    mode = "PART_PRACTICE" if part_number is not None else "FULL_PRACTICE"
    attempt = await _prs.start_practice_attempt(db, spec, user.id, test_id, mode=mode, part_number=part_number)
    await db.commit()
    return await _prs.practice_state(db, spec, user.id, attempt.id)


@router.get("/attempts/{attempt_id}", response_model=AttemptStateResponse)
async def get_reading_attempt(
    attempt_id: int,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Resume: holat + saqlangan javoblar."""
    at, spec = _reading_spec()
    return await _prs.practice_state(db, spec, user.id, attempt_id)


@router.put("/attempts/{attempt_id}/answers", response_model=SavedAnswer)
async def save_reading_answer(
    attempt_id: int,
    data: AnswerSaveRequest,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Bitta javobni saqlaydi/yangilaydi (idempotent)."""
    at, spec = _reading_spec()
    try:
        row = await _prs.save_practice_answer(
            db, spec, user.id, attempt_id, data.question_id, data.question_number, data.normalized()
        )
        await db.commit()
    except Exception:
        await db.rollback()
        raise
    return at.answer_item(row)


@router.post("/attempts/{attempt_id}/finish", response_model=ReadingResultDetailResponse)
async def finish_reading_attempt(
    attempt_id: int,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Saqlangan javoblardan baholaydi va natijani DB'ga yozadi. Takroriy chaqiruv — mavjud natija."""
    at, spec = _reading_spec()
    result = await _prs.finalize_practice(db, spec, user.id, attempt_id)
    return await ReadingService(db).get_result_with_review(result.id, user.id)


@router.get("/attempts/{attempt_id}/result", response_model=ReadingResultDetailResponse)
async def get_reading_attempt_result(
    attempt_id: int,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    at, spec = _reading_spec()
    state = await _prs.practice_state(db, spec, user.id, attempt_id)
    if state["result_id"] is None:
        raise HTTPException(404, "Natija hali mavjud emas")
    return await ReadingService(db).get_result_with_review(state["result_id"], user.id)


# =================================================================
#  PAPER / ANSWER-KEY / VERSIONING (ADMIN ONLY)
# =================================================================
@admin_router.get(
    "/{test_id}/answer-key",
    response_model=AnswerKeyResponse,
)
async def get_reading_answer_key(
    test_id: str,
    version: int = None,
    db: AsyncSession = Depends(get_db),
):
    """Answer key faqat admin uchun."""
    service = ReadingService(db)
    v = await service.get_answer_key(test_id, version)
    return AnswerKeyResponse(
        exam_id=test_id,
        version_number=v.version_number,
        total_questions=v.total_questions,
        is_locked=v.is_locked,
        answer_key=v.answer_key,
    )


@admin_router.get(
    "/{test_id}/versions",
    response_model=List[ExamVersionResponse],
)
async def list_reading_versions(test_id: str, db: AsyncSession = Depends(get_db)):
    service = ReadingService(db)
    return await service.list_versions(test_id)


@admin_router.post(
    "/{test_id}/lock",
    response_model=LockResultResponse,
)
async def lock_reading_exam(
    test_id: str,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Testni PDF/OMR generatsiyasi uchun lock qiladi."""
    service = ReadingService(db)
    version, errors = await service.lock_exam(test_id, locked_by_user_id=user.id)
    if errors:
        return LockResultResponse(success=False, version=None, errors=errors)
    return LockResultResponse(success=True, version=version, errors=[])


@admin_router.post(
    "/{test_id}/new-version",
    response_model=ExamVersionResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_reading_new_version(test_id: str, db: AsyncSession = Depends(get_db)):
    """Lock qilingan Reading testni tahrirlash uchun yangi DRAFT versiya ochadi."""
    return await ReadingService(db).create_new_version(test_id)


@admin_router.get("/{test_id}/paper-pdf")
async def get_reading_paper_pdf(test_id: str, db: AsyncSession = Depends(get_db)):
    """Student uchun savol qog'ozi (javobsiz)."""
    service = ReadingService(db)
    try:
        pdf_bytes = await service.generate_paper_pdf(test_id)
    except (FileNotFoundError, TemplateFitError) as exc:
        raise HTTPException(
            503,
            f"Qog'oz shabloni topilmadi yoki mos kelmadi: {exc}. "
            "static/templates/ papkasiga to'liq READING_MASTER.pdf faylini joylang.",
        ) from exc
    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={"Content-Disposition": f'inline; filename="reading-{test_id}-paper.pdf"'},
    )


@admin_router.get("/{test_id}/answer-sheet")
async def get_reading_answer_sheet_pdf(test_id: str, db: AsyncSession = Depends(get_db)):
    """OMR Answer Sheet."""
    service = ReadingService(db)
    pdf_bytes = await service.generate_answer_sheet_pdf(test_id)
    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={"Content-Disposition": f'inline; filename="reading-{test_id}-answer-sheet.pdf"'},
    )
