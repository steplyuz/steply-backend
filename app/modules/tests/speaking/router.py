from typing import List, Optional
import json

from fastapi import APIRouter, Depends, File, HTTPException, Query, Request, UploadFile
from pathlib import Path
import aiofiles
from app.core.paths import STATIC_DIR
from app.core.file_storage import generated_name, safe_suffix
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import AppError
from app.core.database import get_db
from app.core.file_storage import PRIVATE_ROOT as _PRIVATE_ROOT, ensure_private_file
from fastapi.responses import FileResponse
from app.modules.auth.dependencies import ensure_force_allowed, get_current_user, require_role
from app.modules.tests.content_access import (
    authorize_test_content, filter_viewable, resolve_content_viewer,
)
from app.modules.auth.models import User, UserRole
from app.modules.tests.speaking.schemas import (
    SpeakingResultCreate,
    SpeakingResultResponse,
    SpeakingSubmissionResponse,
    SpeakingTestCreate,
    SpeakingTestListItem,
    SpeakingTestResponse,
    SpeakingTestUpdate,
    SpeakingRecordingDraftResponse,
    SpeakingAttemptPendingResponse,
)
from app.modules.tests.speaking.services import SpeakingService
from app.modules.exams.paper.schemas import LockResultResponse

MAX_JSON_BYTES = 10 * 1024 * 1024

# `router` — foydalanuvchi (public/authenticated) yo'llari: /speaking-tests/...
# `admin_router` — admin yo'llari (test CRUD): /admin/speaking-tests/...
# `evaluator_router` — admin YOKI baholovchi yo'llari, xuddi shu prefiks ostida.
router = APIRouter(prefix="/speaking-tests", tags=["Speaking — Public"])
admin_router = APIRouter(
    prefix="/admin/speaking-tests",
    tags=["Speaking — Admin"],
    dependencies=[Depends(require_role(UserRole.ADMIN, UserRole.SUPER_ADMIN))],
)
evaluator_router = APIRouter(
    prefix="/admin/speaking-tests",
    tags=["Speaking — Evaluator"],
    dependencies=[Depends(require_role(UserRole.ADMIN, UserRole.SUPER_ADMIN, UserRole.TEACHER))],
)


def get_service(db: AsyncSession = Depends(get_db)) -> SpeakingService:
    return SpeakingService(db)


# =====================================================================
# ADMIN: SpeakingTest CRUD (40+ ta test qo'shish uchun)
# =====================================================================


@admin_router.post("/images/upload")
async def upload_speaking_images_image(file: UploadFile = File(...)):
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
    directory = STATIC_DIR / "speaking_images"
    directory.mkdir(parents=True, exist_ok=True)
    filename = generated_name(ext)
    async with aiofiles.open(directory / filename, "wb") as output:
        await output.write(data)
    return {"url": "/static/speaking_images/" + filename, "filename": filename, "size": len(data)}


@admin_router.post(
    "",
    response_model=SpeakingTestResponse,
    status_code=201,
    summary="JSON fayl yoki body orqali Speaking test yaratish",
    openapi_extra={
        "requestBody": {
            "required": True,
            "content": {
                "application/json": {"schema": {"type": "object"}},
                "multipart/form-data": {
                    "schema": {
                        "type": "object",
                        "properties": {"file": {"type": "string", "format": "binary"}},
                        "required": ["file"],
                    }
                },
            },
        }
    },
)
async def import_speaking_json(
    request: Request,
    service: SpeakingService = Depends(get_service),
):
    """
    Speaking testni (4 qism bilan) bitta JSON requestda yaratadi.
    - **Fayl orqali**: multipart/form-data da `file` maydoniga JSON fayl yuboring.
    - **Body orqali**: application/json content-type bilan JSON body yuboring.

    Vaqtlar (prep/answer seconds) JSON da KERAK EMAS: server speaking_config dan to'ldiradi.

    (Oldingi versiyada File va Body(Any) bitta endpointda aralashtirilgan edi;
    FastAPI uni forma sifatida parse qilib, JSON body bo'sh kelardi.)
    """
    content_type = (request.headers.get("content-type") or "").lower()
    if content_type.startswith("multipart/form-data"):
        form = await request.form()
        file = form.get("file")
        if file is None or not hasattr(file, "read"):
            raise HTTPException(400, "multipart so'rovda 'file' maydoni bo'lishi kerak")
        filename = (getattr(file, "filename", "") or "").lower()
        if not filename.endswith(".json") and getattr(file, "content_type", None) not in {"application/json", "text/json"}:
            raise HTTPException(400, "Faqat JSON fayl yuklang")
        raw_bytes = await file.read()
    else:
        raw_bytes = await request.body()

    if not raw_bytes:
        raise HTTPException(400, "JSON fayl (file) yoki JSON body yuborish kerak")
    if len(raw_bytes) > MAX_JSON_BYTES:
        raise HTTPException(413, "JSON hajmi 10 MB dan oshmasligi kerak")
    try:
        raw = json.loads(raw_bytes.decode("utf-8-sig"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise HTTPException(422, "JSON noto'g'ri formatda") from exc

    payload = raw.get("exam") if isinstance(raw, dict) and isinstance(raw.get("exam"), dict) else raw
    if not isinstance(payload, dict):
        raise HTTPException(422, "JSON root object yoki {\"exam\": {...}} bo'lishi kerak")
    try:
        data = SpeakingTestCreate.model_validate(payload)
    except ValidationError as exc:
        fields: dict[str, list[str]] = {}
        for err in exc.errors():
            loc = ".".join(str(part) for part in err.get("loc", [])) or "body"
            fields.setdefault(loc, []).append(err.get("msg", "Noto'g'ri qiymat"))
        raise AppError(
            status_code=422,
            code="SPEAKING_VALIDATION_ERROR",
            message="Speaking ma'lumotlari yaroqsiz",
            fields=fields,
        ) from exc
    return await service.create_test(data)


@admin_router.get("", response_model=List[SpeakingTestListItem])
async def list_speaking_tests(
    service: SpeakingService = Depends(get_service),
):
    """Admin: barcha speaking testlar ro'yxati."""
    return await service.list_tests()


@admin_router.get("/{test_id}", response_model=SpeakingTestResponse)
async def get_speaking_test(
    test_id: str,
    service: SpeakingService = Depends(get_service),
):
    """Admin: bitta speaking testni to'liq (4 qism bilan) ko'rish."""
    return await service.get_test(test_id)


@admin_router.put("/{test_id}", response_model=SpeakingTestResponse)
async def update_speaking_test(
    test_id: str,
    data: SpeakingTestUpdate,
    service: SpeakingService = Depends(get_service),
):
    """Admin: testni tahrirlash. Audio yozuvlari bor testda qism kontenti o'zgarmaydi (409)."""
    return await service.update_test(test_id, data)


@admin_router.post("/{test_id}/unlock", summary="Speaking testni lock'dan chiqarish (LOCK OFF)")
async def unlock_speaking_test(
    test_id: str,
    service: SpeakingService = Depends(get_service),
):
    """Lock'ni olib tashlaydi. Mock imtihonda yoki ishlanayotgan urinish bo'lsa 409."""
    return await service.unlock_test(test_id)


@admin_router.post("/{test_id}/lock", response_model=LockResultResponse, summary="Speaking testni lock qilish")
async def lock_speaking_test(
    test_id: str,
    service: SpeakingService = Depends(get_service),
    user: User = Depends(get_current_user),
):
    """Qismlar to'liqligini tekshiradi; lock qilingach test o'zgartirilmaydi va o'chirilmaydi."""
    version, errors = await service.lock_test(test_id, locked_by_user_id=user.id)
    if errors:
        return LockResultResponse(success=False, version=None, errors=errors)
    return LockResultResponse(success=True, version=version, errors=[])


@admin_router.delete("/{test_id}")
async def delete_speaking_test(
    test_id: str,
    force: bool = Query(False, description="Faqat SUPER_ADMIN: yozuvlar va audio fayllar bilan birga o'chirish"),
    user: User = Depends(get_current_user),
    service: SpeakingService = Depends(get_service),
):
    """Speaking testni o'chirish. Yozuv/urinish bo'lsa 409; force=true faqat super admin uchun."""
    ensure_force_allowed(force, user)
    await service.delete_test(test_id, force=force)
    return {"detail": "Speaking test o'chirildi"}


# =====================================================================
# PUBLIC: SPEAKING PRACTICE TESTS
# =====================================================================
@router.get("/practice", response_model=List[SpeakingTestListItem])
async def list_practice_speaking_tests(
    request: Request,
    db: AsyncSession = Depends(get_db),
    service: SpeakingService = Depends(get_service),
):
    """Katalog: faol practice testlar, faqat viewer ko'ra oladiganlari (pullik -> PRO)."""
    tests = await service.list_tests()
    candidates = [test for test in tests if test.is_active and test.is_practice]
    viewer = await resolve_content_viewer(request, db)
    return await filter_viewable(db, viewer, candidates, "speaking")


@router.get("/practice/{test_id}", response_model=SpeakingTestResponse)
async def get_practice_speaking_test(
    test_id: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
    service: SpeakingService = Depends(get_service),
):
    """Practice Speaking testi detali — tarif bo'yicha ruxsat tekshiriladi."""
    test = await service.get_test(test_id)
    if not test.is_active or not test.is_practice:
        raise HTTPException(404, "Speaking testi topilmadi")
    viewer = await resolve_content_viewer(request, db)
    await authorize_test_content(db, viewer, test, "speaking")
    return test


# =====================================================================
# MOCK: STUDENT ID -> ATTENDED REGISTRATION -> ASSIGNED SPEAKING TEST
# =====================================================================
@router.post("/{speaking_test_id}/practice/submit", response_model=SpeakingSubmissionResponse)
async def submit_practice_speaking(
    speaking_test_id: str,
    file: UploadFile = File(...),
    part_external_id: str | None = Query(default=None, min_length=1, max_length=128),
    question_external_id: str = Query(..., min_length=1, max_length=128),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    service: SpeakingService = Depends(get_service),
):
    """Real Speaking practice submission; no mock attempt is created.

    Speaking imtihoni faqat faol obunada (SPEAKING ruxsati bor tarifda) topshiriladi.
    """
    from app.modules.billing.entitlements import plan_allows_skill
    from app.modules.exams.mock.models import PracticeAttempt, PracticeAttemptStatus, SkillType

    if not await plan_allows_skill(db, current_user.id, "SPEAKING"):
        raise HTTPException(403, "Speaking imtihoni faqat faol obunada mavjud")

    test = await service.get_test(speaking_test_id)
    if not test.is_active or not test.is_practice:
        raise HTTPException(404, "Speaking practice testi topilmadi")
    requested_part_practice = part_external_id is not None
    if part_external_id is None:
        matches = [(part.external_id, getattr(part, attr, None)) for part in (test.part_1_1, test.part_1_2, test.part_2, test.part_3) if part is not None for attr in ("question_1_id", "question_2_id", "question_3_id", "question_describe_id", "topic_id") if getattr(part, attr, None)]
        matching_parts = [part_id for part_id, qid in matches if qid == question_external_id]
        if not matching_parts:
            raise HTTPException(400, "Speaking savol IDsi ushbu testga tegishli emas")
        part_external_id = matching_parts[0]
    mode = "PART_PRACTICE" if requested_part_practice else "FULL_PRACTICE"
    attempt = (await db.execute(
        select(PracticeAttempt).where(
            PracticeAttempt.user_id == current_user.id,
            PracticeAttempt.skill == SkillType.SPEAKING,
            PracticeAttempt.test_id == speaking_test_id,
            PracticeAttempt.mode == mode,
            PracticeAttempt.status == PracticeAttemptStatus.IN_PROGRESS,
        ).order_by(PracticeAttempt.id.desc()).limit(1)
    )).scalar_one_or_none()
    if attempt is None:
        attempt = PracticeAttempt(
            user_id=current_user.id, skill=SkillType.SPEAKING, test_id=speaking_test_id,
            mode=mode, part_number=None, status=PracticeAttemptStatus.IN_PROGRESS,
        )
        db.add(attempt)
        # Speaking audio topshirish tarifdan mustaqil. Baholash huquqi
        # alohida tekshiriladi; shu sabab bu yerda tarif/quota bilan bloklamaymiz.
        await db.flush()
    return await service.upload_submission(
        current_user.id, speaking_test_id, file,
        mock_skill_attempt_id=None, practice_attempt_id=attempt.id,
        question_external_id=question_external_id, part_external_id=part_external_id,
    )


@router.get("/practice/{test_id}/paper.pdf", summary="Speaking test savollarini PDF yuklab olish")
async def download_speaking_question_paper(
    test_id: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
    service: SpeakingService = Depends(get_service),
):
    from fastapi.responses import Response
    test = await service.get_test(test_id)
    viewer = await resolve_content_viewer(request, db)
    await authorize_test_content(db, viewer, test, "speaking")
    pdf_bytes = await service.generate_question_paper_pdf(test_id)
    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={"Content-Disposition": 'attachment; filename="speaking-question-paper.pdf"', "X-Content-Type-Options": "nosniff"},
    )


@router.post("/mock-access")
async def speaking_mock_access_disabled():
    """Legacy alternate login is disabled: Exams access is by Exam Access ID only."""
    raise HTTPException(410, "Legacy Speaking mock-access o'chirilgan. Examga /exams-auth/login orqali Exam Access ID bilan kiring.")


@router.post("/{speaking_test_id}/recording-draft/chunk", response_model=SpeakingRecordingDraftResponse)
async def save_speaking_recording_chunk(
    speaking_test_id: str,
    skill_attempt_id: int,
    part_external_id: str,
    question_external_id: str,
    chunk_index: int,
    file: UploadFile = File(...),
    duration_seconds: Optional[int] = None,
    current_user: User = Depends(get_current_user),
    service: SpeakingService = Depends(get_service),
):
    """Microphone yozuvining chunkini serverga saqlaydi; browser crash bo'lsa davom ettirish mumkin."""
    return await service.save_recording_chunk(
        current_user.id, skill_attempt_id, speaking_test_id, part_external_id,
        question_external_id, file, chunk_index, duration_seconds,
    )


@router.get("/recording-draft", response_model=SpeakingRecordingDraftResponse)
async def get_speaking_recording_draft(
    skill_attempt_id: int,
    part_external_id: str,
    question_external_id: str,
    current_user: User = Depends(get_current_user),
    service: SpeakingService = Depends(get_service),
):
    return await service.get_recording_draft(
        current_user.id, skill_attempt_id, part_external_id, question_external_id
    )


@router.post("/recording-draft/{draft_id}/finalize", response_model=SpeakingSubmissionResponse)
async def finalize_speaking_recording_draft(
    draft_id: int,
    current_user: User = Depends(get_current_user),
    service: SpeakingService = Depends(get_service),
):
    return await service.finalize_recording_draft(current_user.id, draft_id)


@router.get("/submissions/{submission_id}/audio", summary="Private Speaking audio")
async def download_speaking_audio(
    submission_id: int,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    from app.modules.auth.models import UserRole
    from app.modules.tests.speaking.models import SpeakingSubmission

    submission = await db.get(SpeakingSubmission, submission_id)
    if not submission:
        raise HTTPException(404, "Speaking submission topilmadi")

    allowed = submission.user_id == current_user.id or current_user.global_role in {UserRole.ADMIN, UserRole.SUPER_ADMIN, UserRole.TEACHER}
    if not allowed:
        raise HTTPException(404, "Audio topilmadi")

    if not submission.audio_storage_key:
        raise HTTPException(404, "Audio topilmadi")
    path = ensure_private_file(_PRIVATE_ROOT / "speaking" / "submissions" / submission.audio_storage_key, "speaking")
    # Legacy rows may point at a generated URL but the physical path is always server-derived.
    return FileResponse(
        path,
        media_type="audio/webm" if path.suffix.lower() == ".webm" else None,
        headers={"Cache-Control": "private, no-store", "X-Content-Type-Options": "nosniff", "Content-Disposition": f'inline; filename="{path.name}"'},
    )


@router.get("/me/results", response_model=List[SpeakingResultResponse])
async def get_my_speaking_results(
    current_user: User = Depends(get_current_user),
    service: SpeakingService = Depends(get_service),
):
    return await service.list_user_results(current_user.id)


@evaluator_router.get("/pending", response_model=List[SpeakingSubmissionResponse])
async def list_pending_submissions(
    limit: int = Query(200, ge=1, le=500),
    offset: int = Query(0, ge=0),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    service: SpeakingService = Depends(get_service),
):
    """Baholovchi uchun: hali tekshirilmagan speaking yozuvlari.

    Yozuvlar urinish bo'yicha guruhlanadi (mock_skill_attempt_id); bitta natija butun urinishga yoziladi.
    """
    return await service.list_pending(limit=limit, offset=offset)


@evaluator_router.get("/pending-attempts", response_model=List[SpeakingAttemptPendingResponse])
async def list_pending_attempts(
    limit: int = Query(200, ge=1, le=500),
    offset: int = Query(0, ge=0),
    service: SpeakingService = Depends(get_service),
):
    """Baholovchi navbati URINISH bo'yicha: bitta urinish = bitta element.

    Savollar alohida baholanmaydi — baholovchi urinishdagi barcha yozuvlarni
    tinglab UMUMIY bitta ball kiritadi (`submission_id` orqali natija yuboriladi).
    """
    return await service.list_pending_attempts(limit=limit, offset=offset)


@evaluator_router.post("/submissions/{submission_id}/result", response_model=SpeakingResultResponse)
async def submit_speaking_result(
    submission_id: int,
    data: SpeakingResultCreate,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    service: SpeakingService = Depends(get_service),
):
    """Teacher/Expert natijani qo'lda kiritadi. Natija shu urinishning barcha savol-yozuvlariga qo'llanadi."""
    submission = await service.get_submission(submission_id)
    if not submission:
        raise HTTPException(404, "Speaking submission topilmadi")

    if current_user.global_role not in {UserRole.ADMIN, UserRole.SUPER_ADMIN, UserRole.TEACHER}:
        raise HTTPException(403, "Faqat Admin, Super Admin yoki Teacher baholay oladi.")

    return await service.submit_result(submission_id, current_user.id, data)
