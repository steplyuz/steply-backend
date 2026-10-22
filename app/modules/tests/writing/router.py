from __future__ import annotations

import logging
import re
import json
from typing import List

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload
from app.core.database import get_db

from app.modules.auth.dependencies import ensure_force_allowed, get_current_user, require_role
from app.modules.tests.content_access import (
    authorize_test_content, filter_viewable, resolve_content_viewer,
)
from app.modules.auth.models import User, UserRole

from app.modules.tests.writing.models import WritingAnswer, WritingExam, WritingResult
from app.modules.tests.writing.services.pdf_service import PDFService
from app.modules.tests.writing.services.format_service import WritingFormatService
from .schemas import (
    WritingExamCreate,
    WritingExamRead,
    WritingExamUpdate,
    WritingSubmitRequest,
    WritingResultRead,
    WritingGradeRequest,
    WritingAIEvaluateRequest,
    WritingFormatCreate,
    WritingFormatRead,
)
from .services import WritingService
from app.modules.exams.paper.schemas import LockResultResponse
from app.core.clock import utcnow

logger = logging.getLogger(__name__)


async def _safe_rollback(db: AsyncSession) -> None:
    """Xato ichida rollback ham yiqilsa, asl xatoni yashirib qo'ymaslik uchun."""
    try:
        await db.rollback()
    except Exception:
        logger.exception("Rollback failed after Writing AI error")

# `router` — foydalanuvchi (public/authenticated) yo'llari: /writing-tests/...
# `admin_router` — faqat admin/teacher yo'llari: /admin/writing-tests/...
router = APIRouter(
    prefix="/writing-tests",
    tags=["Writing — Public"],
)
admin_router = APIRouter(
    prefix="/admin/writing-tests",
    tags=["Writing — Admin"],
    dependencies=[Depends(require_role(UserRole.ADMIN, UserRole.SUPER_ADMIN))],
)
evaluator_router = APIRouter(
    prefix="/admin/writing-tests",
    tags=["Writing — Evaluator"],
    dependencies=[Depends(require_role(UserRole.ADMIN, UserRole.SUPER_ADMIN, UserRole.TEACHER))],
)

# ---------------------------
# DI
# ---------------------------

def get_writing_service(db: AsyncSession = Depends(get_db)) -> WritingService:
    return WritingService(db)

def get_format_service(db: AsyncSession = Depends(get_db)) -> WritingFormatService:
    return WritingFormatService(db)

# ---------------------------
# Helpers
# ---------------------------

_PROMPT_RE = re.compile(
    r"(Task\s+1\.[12]\b|Limit:\s*\d+|\bWrite\b\s+(?:about\s+)?\d+(?:\s*-\s*\d+)?\s+words)",
    re.IGNORECASE,
)

def _is_admin(user: User) -> bool:
    # projectda qaysi biri asosiy bo'lsa, shuni qoldirasan
    if hasattr(user, "is_admin") and bool(getattr(user, "is_admin")):
        return True
    if hasattr(user, "global_role") and getattr(user, "global_role") == UserRole.ADMIN:
        return True
    return False

def _validate_submit_payload(payload: WritingSubmitRequest) -> None:
    if not payload.answers or not isinstance(payload.answers, list):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="answers is required",
        )

    for a in payload.answers:
        # task_id must be int
        if a.task_id is None or not isinstance(a.task_id, int):
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="Each answer must have integer task_id",
            )
        # Empty content represents an unanswered task and is stored as the default.
        # Do not reject short partial answers when the exam timer expires.
        content = (a.content or "").strip()
        if content and _PROMPT_RE.search(content):
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=f"Task {a.task_id}: looks like prompt/instructions, not a user answer",
            )

# ---------------------------
# WritingFormat — CRUD (yaratish/o'chirish admin, o'qish ochiq)
# (B1/B2/C1 uslub, so'z chegarasi va baholash og'irliklari sozlamalari.
#  WritingTask yaratishdan oldin kerakli format_id shu yerda tayyorlanadi.)
# ---------------------------

@admin_router.post(
    "/formats",
    response_model=WritingFormatRead,
    status_code=status.HTTP_201_CREATED,
)
async def create_format(
    data: WritingFormatCreate,
    service: WritingFormatService = Depends(get_format_service),
):
    return await service.create_format(data)

@router.get("/formats", response_model=List[WritingFormatRead])
async def list_formats(service: WritingFormatService = Depends(get_format_service)):
    return await service.list_formats()

@router.get("/formats/{format_id}", response_model=WritingFormatRead)
async def get_format(
    format_id: int,
    service: WritingFormatService = Depends(get_format_service),
):
    return await service.get_format_by_id(format_id)

@admin_router.delete("/formats/{format_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_format(
    format_id: int,
    service: WritingFormatService = Depends(get_format_service),
):
    await service.delete_format(format_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)

# ---------------------------
# Admin CRUD — WritingExam
# ---------------------------

@admin_router.post(
    "",
    response_model=WritingExamRead,
    status_code=status.HTTP_201_CREATED,
    summary="JSON fayl yoki body orqali Writing exam yaratish",
)
async def import_writing_json(
    request: Request,
    service: WritingService = Depends(get_writing_service),
):
    """Writing exam + tasks ni yaratadi: JSON body (application/json) YOKI multipart `file`.

    Eslatma: ilgari `File(None)` + `Body(None)` birga e'lon qilingani uchun FastAPI
    butun so'rovni form deb o'qir va oddiy JSON body hech qachon `body` ga tushmasdi
    (har doim 400 "JSON fayl yoki JSON body yuborish kerak").
    """
    raw = None
    file = None
    ctype = (request.headers.get("content-type") or "").lower()
    if ctype.startswith("multipart/form-data"):
        form = await request.form()
        candidate = form.get("file")
        if candidate is not None and hasattr(candidate, "read"):
            file = candidate
    elif "json" in ctype:
        try:
            raw = await request.json()
        except Exception as exc:
            raise HTTPException(422, "JSON body noto'g'ri") from exc
    body = raw
    if file and file.filename:
        filename = file.filename or ""
        if not filename.lower().endswith(".json") and file.content_type not in {"application/json", "text/json"}:
            raise HTTPException(400, "Faqat JSON fayl yuklang.")
        raw_bytes = await file.read()
        if len(raw_bytes) > 10 * 1024 * 1024:
            raise HTTPException(413, "JSON fayl hajmi 10 MB dan oshmasligi kerak.")
        try:
            raw = json.loads(raw_bytes.decode("utf-8-sig"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise HTTPException(422, "JSON fayl noto'g'ri") from exc
    elif body is not None:
        raw = body
    else:
        raise HTTPException(400, "JSON fayl (file) yoki JSON body yuborish kerak")
    payload = raw.get("exam") if isinstance(raw, dict) and isinstance(raw.get("exam"), dict) else raw
    if not isinstance(payload, dict):
        raise HTTPException(422, "JSON root object yoki {\"exam\": {...}} bo'lishi kerak.")
    # Common aliases.
    payload = dict(payload)
    if "cefr_level" not in payload and "level" in payload:
        payload["cefr_level"] = payload["level"]
    if "duration_minutes" not in payload and "duration" in payload:
        payload["duration_minutes"] = payload["duration"]
    try:
        data = WritingExamCreate.model_validate(payload)
    except Exception as exc:
        errs = getattr(exc, "errors", None)
        detail = "; ".join(
            f"{'.'.join(str(x) for x in e.get('loc', ()))}: {e.get('msg')}" for e in errs()
        ) if callable(errs) else str(exc)
        raise HTTPException(422, detail=f"Writing JSON schema xatosi: {detail}") from exc
    if not data.tasks:
        raise HTTPException(422, "Import uchun kamida bitta writing task kerak.")
    return await service.create_exam(data)


@admin_router.get(
    "",
    response_model=List[WritingExamRead],
    summary="Admin: barcha Writing testlari",
)
async def list_writing_exams_admin(
    service: WritingService = Depends(get_writing_service),
):
    """Admin katalogi barcha Writing testlarini, jumladan nofaollarini qaytaradi."""
    return await service.list_exams()


@admin_router.put("/{exam_id}", response_model=WritingExamRead)
async def update_exam(
    exam_id: str,
    data: WritingExamUpdate,
    service: WritingService = Depends(get_writing_service),
):
    updated = await service.update_exam(exam_id, data)
    if not updated:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Exam not found")
    return updated

@admin_router.post("/{exam_id}/unlock", summary="Writing testni lock'dan chiqarish (LOCK OFF)")
async def unlock_writing_exam(
    exam_id: str,
    service: WritingService = Depends(get_writing_service),
):
    """Lock'ni olib tashlaydi. Mock imtihonda yoki ishlanayotgan urinish bo'lsa 409."""
    return await service.unlock_exam(exam_id)


@admin_router.post("/{exam_id}/lock", response_model=LockResultResponse, summary="Writing testni lock qilish")
async def lock_writing_exam(
    exam_id: str,
    service: WritingService = Depends(get_writing_service),
    user: User = Depends(get_current_user),
):
    """Kontent tuzilmasini tekshiradi; muvaffaqiyatli bo'lsa test o'zgartirib/o'chirib bo'lmaydi."""
    version, errors = await service.lock_exam(exam_id, locked_by_user_id=user.id)
    if errors:
        return LockResultResponse(success=False, version=None, errors=errors)
    return LockResultResponse(success=True, version=version, errors=[])


@admin_router.delete("/{exam_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_exam(
    exam_id: str,
    force: bool = Query(False, description="Faqat SUPER_ADMIN: natija va urinishlar bilan birga o'chirish"),
    user: User = Depends(get_current_user),
    service: WritingService = Depends(get_writing_service),
):
    ensure_force_allowed(force, user)
    deleted = await service.delete_exam(exam_id, force=force)
    if not deleted:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Exam not found")
    # 204 => body bo'lmasin
    return Response(status_code=status.HTTP_204_NO_CONTENT)

@admin_router.get("/{exam_id}/paper-pdf")
async def export_writing_paper(
    exam_id: str,
    db: AsyncSession = Depends(get_db),
):
    """Admin uchun Writing question paper. Mock bundle ham shu modul bilan bir xil formatdan foydalanadi."""
    from app.modules.exams.mock.paper_pdf_service import WritingPaperPDF
    try:
        pdf_bytes = await WritingPaperPDF().generate(exam_id, db)
    except ValueError as exc:
        raise HTTPException(404, "Writing testi topilmadi") from exc
    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={"Content-Disposition": f'inline; filename="writing-{exam_id}-paper.pdf"'},
    )


# ---------------------------
# Public / user
# ---------------------------

@router.get("", response_model=List[WritingExamRead])
async def list_writing_exams(
    request: Request,
    db: AsyncSession = Depends(get_db),
    service: WritingService = Depends(get_writing_service),
):
    # Katalog: faqat faol testlar, va faqat viewer ko'ra oladiganlari (pullik practice -> tarif bo'yicha).
    exams = await service.list_exams(active_only=True)
    viewer = await resolve_content_viewer(request, db)
    ids = [e.id for e in exams]
    rows = (
        await db.execute(
            select(WritingExam).where(WritingExam.id.in_(ids), WritingExam.is_practice.is_(True))
        )
    ).scalars().all() if ids else []
    allowed = {r.id for r in await filter_viewable(db, viewer, rows, "writing")}
    return [e for e in exams if e.id in allowed]

@router.get("/me/results", response_model=List[WritingResultRead])
async def get_my_results(
    current_user: User = Depends(get_current_user),
    service: WritingService = Depends(get_writing_service),
):
    return await service.get_user_results(current_user.id)

@admin_router.get("/results", response_model=List[WritingResultRead])
async def get_all_writing_results(
    service: WritingService = Depends(get_writing_service),
):
    return await service.get_all_results()


@router.post("/results/{result_id}/ai-check", response_model=WritingResultRead)
async def ai_check_my_writing(
    request: Request,
    result_id: int,
    data: WritingAIEvaluateRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    service: WritingService = Depends(get_writing_service),
):
    """Student AI Writing evaluation; entitlement is derived server-side."""
    from app.modules.billing.entitlements import enforce_ai_quota, get_ai_quota
    from app.modules.tests.writing.models import FeedbackSource
    from app.modules.tests.writing.services.result_service import is_task_copy, build_exam_task_text

    # Serialize evaluations for the same result on databases supporting row locks.
    # This prevents duplicate provider calls/usage when the client retries or double-submits.
    await db.scalar(
        select(WritingResult.id)
        .where(WritingResult.id == result_id)
        .with_for_update()
    )
    result = await service.get_result(result_id)
    if result.user_id != current_user.id:
        raise HTTPException(404, "Result not found")

    selected_answers = result.answers
    if data.answer_id is not None:
        selected_answers = [a for a in selected_answers if a.id == data.answer_id]
        if not selected_answers:
            raise HTTPException(404, "Writing answer topilmadi")

    # AI feedback is the persisted usage ledger. Existing feedback means this
    # answer was already evaluated: retries return the saved result without billing.
    pending_answers = [
        answer for answer in selected_answers
        if not any(feedback.source == FeedbackSource.AI for feedback in answer.feedbacks)
    ]
    if not pending_answers:
        return result

    task_text = build_exam_task_text(result.exam.tasks if result.exam else [])
    # Empty answers and copied task prompts receive a deterministic zero without
    # an AI provider call, so they must not reserve subscription or purchased credits.
    billable_answers = [
        answer for answer in pending_answers
        if (answer.content or "").strip()
        and not is_task_copy((answer.content or "").strip(), task_text)
    ]
    purchased_answer_ids: set[int] = set()
    if billable_answers:
        quota_before = await get_ai_quota(db, current_user.id)
        subscription_units = min(len(billable_answers), quota_before["subscription_remaining"])
        purchased_answer_ids = {answer.id for answer in billable_answers[subscription_units:]}
        await enforce_ai_quota(db, current_user.id, len(billable_answers))
    try:
        graded = await service.ai_grade_result(result_id, data.answer_id, purchased_answer_ids=purchased_answer_ids)
        # Serializatsiya try ichida: ResponseValidationError ham shu yerda ushlanadi.
        return WritingResultRead.model_validate(graded)
    except HTTPException:
        # Credit reservation and any partial DB work must not survive provider/validation errors.
        await _safe_rollback(db)
        raise
    except Exception as exc:
        await _safe_rollback(db)
        logger.exception(
            "Writing AI student evaluate failed request_id=%s result_id=%s",
            getattr(request.state, "request_id", None), result_id,
        )
        raise HTTPException(500, "AI baholashda ichki xato yuz berdi. Qayta urinib ko'ring") from exc


@router.get("/results/{result_id}", response_model=WritingResultRead)
async def get_result(
    result_id: int,
    current_user: User = Depends(get_current_user),
    service: WritingService = Depends(get_writing_service),
):
    result = await service.get_result(result_id)
    if not result:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Result not found")

    if result.user_id != current_user.id and not _is_admin(current_user):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Forbidden")

    return result

@router.get("/results/{result_id}/pdf")
async def export_result_pdf(
    result_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    stmt = (
        select(WritingResult)
        .where(WritingResult.id == result_id)
        .options(
            selectinload(WritingResult.exam),

            # ✅ user -> profile
            selectinload(WritingResult.user).selectinload(User.profile),

            # ✅ answers
            selectinload(WritingResult.answers).selectinload(WritingAnswer.task),
            selectinload(WritingResult.answers).selectinload(WritingAnswer.scores),
            selectinload(WritingResult.answers).selectinload(WritingAnswer.feedbacks),
        )
    )

    res = await db.execute(stmt)
    result = res.scalar_one_or_none()
    if not result:
        raise HTTPException(404, "Result not found")
    if result.user_id != current_user.id and not _is_admin(current_user):
        raise HTTPException(403, "Forbidden")

    pdf_bytes = await PDFService().generate_pdf_report(result)

    filename = f'writing-{utcnow().strftime("%Y%m%d-%H%M")}-result{result_id}.pdf'
    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )

# ---------------------------
@router.get("/{exam_id}", response_model=WritingExamRead)
async def get_exam(
    exam_id: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
    service: WritingService = Depends(get_writing_service),
):
    exam = await service.get_exam(exam_id)
    if not exam:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Exam not found")
    row = await db.get(WritingExam, exam_id)
    viewer = await resolve_content_viewer(request, db)
    await authorize_test_content(db, viewer, row, "writing")
    return exam

# Submit
# ---------------------------

@router.post("/{exam_id}/submit", response_model=WritingResultRead)
async def submit_exam(
    request: Request,
    exam_id: str,
    payload: WritingSubmitRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
    service: WritingService = Depends(get_writing_service),
):
    """Javoblarni saqlaydi va AI kvotasi yetarli bo‘lsa avtomatik baholaydi.
    AI baholash muvaffaqiyatli tugasa AI_FINAL va yakuniy ball qaytariladi.
    Kvota yetmasa javoblar saqlanadi, AI_PENDING holati qaytariladi; foydalanuvchi
    AI tekshiruv kreditini olgach /results/{result_id}/ai-check orqali ishga tushirishi mumkin."""
    logger.info("SUBMIT exam_id=%s user_id=%s", exam_id, current_user.id)
    # Candidate essays may contain personal data; never log full answer bodies.
    logger.debug("Writing submission accepted: exam_id=%s answers=%d", exam_id, len(payload.answers))

    _validate_submit_payload(payload)

    result = await service.submit_practice_answers(current_user.id, exam_id, payload)

    # Submit saqlashdan keyin, faqat foydalanuvchida barcha mazmunli javoblarni
    # tekshirishga yetarli AI limiti/krediti bo'lsa, to'liq avtomatik baholaymiz.
    # Kredit yetmasa ham javob saqlanadi; AI baholash alohida endpointdan keyin bajarilishi mumkin.
    from app.modules.billing.entitlements import get_ai_quota, enforce_ai_quota
    from app.modules.tests.writing.models import FeedbackSource
    from app.modules.tests.writing.services.result_service import is_task_copy, build_exam_task_text
    try:
        result = await service.get_result(result.id)
        pending_answers = [
            answer for answer in result.answers
            if not any(feedback.source == FeedbackSource.AI for feedback in answer.feedbacks)
        ]
        task_text = build_exam_task_text(result.exam.tasks if result.exam else [])
        billable = [
            answer for answer in pending_answers
            if (answer.content or "").strip()
            and not is_task_copy((answer.content or "").strip(), task_text)
        ]
        quota = await get_ai_quota(db, current_user.id)
        if pending_answers and quota["remaining"] >= len(billable):
            purchased_answer_ids: set[int] = set()
            if billable:
                subscription_units = min(len(billable), quota["subscription_remaining"])
                purchased_answer_ids = {answer.id for answer in billable[subscription_units:]}
                await enforce_ai_quota(db, current_user.id, len(billable))
            result = await service.ai_grade_result(result.id, purchased_answer_ids=purchased_answer_ids)
        elif pending_answers:
            # No entitlement: explicitly signal that the submission is saved but not graded.
            # Never leave a generic PENDING status that can be mistaken for an evaluator failure.
            result.evaluation_status = "AI_PENDING"
            result.is_finalized = False
            await db.commit()
            result = await service.get_result(result.id)
        return WritingResultRead.model_validate(result)
    except Exception:
        # Writing javoblari avvaldan commit qilingan. Provider/quota xatosi
        # ularni yo'qotmasin; xatoni log qilib AI_FAILED holatini saqlaymiz.
        await _safe_rollback(db)
        logger.exception("Automatic Writing AI grading failed result_id=%s", result.id)
        try:
            failed_result = await service.get_result(result.id)
            if not failed_result.is_finalized:
                failed_result.evaluation_status = "AI_FAILED"
                await db.commit()
                return WritingResultRead.model_validate(await service.get_result(result.id))
        except Exception:
            await _safe_rollback(db)
        return WritingResultRead.model_validate(await service.get_result(result.id))


@router.post("/results/{result_id}/request-teacher-check", response_model=WritingResultRead)
async def request_teacher_writing_check(
    result_id: int,
    current_user: User = Depends(get_current_user),
    service: WritingService = Depends(get_writing_service),
):
    """Pullik teacher-review kreditini sarflab natijani o'qituvchi navbatiga qo'yadi."""
    return await service.request_teacher_check(result_id, current_user.id)


@evaluator_router.get("/pending", response_model=List[WritingResultRead])
async def list_pending_writing_results(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    service: WritingService = Depends(get_writing_service),
):
    """Admin/teacher uchun: hali baholanmagan Writing topshiriqlari
    (Free tarifdagi foydalanuvchilarniki bu yerda ko'rinmaydi)."""
    return await service.list_pending()


@evaluator_router.post("/results/{result_id}/grade", response_model=WritingResultRead)
async def grade_writing_result(
    result_id: int,
    data: WritingGradeRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    service: WritingService = Depends(get_writing_service),
):
    """Teacher/Expert natijani yakuniy qo'lda baholaydi."""
    result = await service.get_result(result_id)
    if not result:
        raise HTTPException(404, "Result not found")

    if current_user.global_role not in {UserRole.ADMIN, UserRole.SUPER_ADMIN, UserRole.TEACHER}:
        raise HTTPException(403, "Faqat Admin, Super Admin yoki Teacher baholay oladi.")

    return await service.grade_result(
        result_id,
        data.raw_score,
        data.cefr_level,
        data.feedback,
        [(item.criterion, item.score) for item in data.criterion_scores],
        evaluator_id=current_user.id,
        answer_id=data.answer_id,
    )


@evaluator_router.post("/results/{result_id}/ai-evaluate", response_model=WritingResultRead)
async def ai_evaluate_writing_result(
    request: Request,
    result_id: int,
    data: WritingAIEvaluateRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    service: WritingService = Depends(get_writing_service),
):
    """Admin/teacher tugmasi orqali Writing uchun AI draft baholash."""
    try:
        await service.get_result(result_id)  # mavjud bo'lmasa 404 ko'taradi
        # Teacher/admin baholashi talabaning oylik AI kvotasini hisobdan chiqarmaydi.
        graded = await service.ai_grade_result(
            result_id, data.answer_id, record_usage=False, finalize=False
        )
        return WritingResultRead.model_validate(graded)
    except HTTPException:
        await _safe_rollback(db)
        raise
    except Exception as exc:
        await _safe_rollback(db)
        logger.exception(
            "Writing AI admin evaluate failed request_id=%s result_id=%s",
            getattr(request.state, "request_id", None), result_id,
        )
        raise HTTPException(
            500,
            f"AI baholashda ichki xato yuz berdi (request_id={getattr(request.state, 'request_id', None)})",
        ) from exc
