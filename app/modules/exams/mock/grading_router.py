"""Deterministic answer-sheet processing endpoints.

OMR is deliberately local: OpenCV + optional local Tesseract only. No AI
vision API is called. The supplied fixed answer sheet is the source of truth.
"""
from __future__ import annotations

import asyncio
import json
import re
from pathlib import Path
from datetime import datetime, timezone
from typing import Any, Optional

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.modules.auth.dependencies import require_role
from app.modules.auth.models import UserRole
from app.core.file_storage import generated_name, private_path, ensure_private_file
from app.modules.exams.mock.models import (
    ExamSession, MockExam, MockExamAttempt, MockRegistration, MockSkillAttempt,
    SkillType,
)
from app.modules.exams.paper.models import ExamVersion, OMRSubmission, OMRSubmissionStatus, PaperModule
from app.modules.exams.mock.omr_service import read_sheet, compare_answers, scale_score

router = APIRouter(
    prefix="/admin/mock-exams/grade",
    tags=["Answer Sheet OMR"],
    dependencies=[Depends(require_role(UserRole.ADMIN, UserRole.SUPER_ADMIN))],
)

_ALLOWED_TYPES = {"image/jpeg", "image/png", "image/webp", "image/jpg"}
_MAX_SIZE_MB = 20

async def _validate_image(file: UploadFile) -> tuple[bytes, str]:
    ct = (file.content_type or "").lower()
    if ct not in _ALLOWED_TYPES:
        raise HTTPException(415, f"{file.filename}: faqat JPG/PNG/WebP rasm qabul qilinadi")
    max_bytes = _MAX_SIZE_MB * 1024 * 1024
    data = await file.read(max_bytes + 1)
    if not data:
        raise HTTPException(400, f"{file.filename}: rasm bo'sh")
    if len(data) > max_bytes:
        raise HTTPException(413, f"{file.filename}: maksimal hajm {_MAX_SIZE_MB} MB")
    actual = None
    if data.startswith(b"\xff\xd8\xff"):
        actual = "image/jpeg"
    elif data.startswith(b"\x89PNG\r\n\x1a\n"):
        actual = "image/png"
    elif data.startswith(b"RIFF") and data[8:12] == b"WEBP":
        actual = "image/webp"
    if actual != ("image/jpeg" if ct == "image/jpg" else ct):
        raise HTTPException(415, f"{file.filename}: fayl tarkibi MIME turiga mos kelmadi")
    return data, actual



async def _keys_from_mock(db: AsyncSession, exam_id: str) -> tuple[Optional[dict], Optional[dict]]:
    """Return immutable locked keys for the exact offline component version.

    A paper scan is only scoreable against a locked ExamVersion. Current live
    question rows are deliberately not used as an answer-key substitute.
    """
    stmt = select(MockExam).where(MockExam.id == exam_id)
    mock = (await db.execute(stmt)).scalar_one_or_none()
    if not mock:
        raise HTTPException(404, "Mock imtihon topilmadi")
    l_key = r_key = None
    if mock.listening_id:
        v = (await db.execute(
            select(ExamVersion).where(
                ExamVersion.module == PaperModule.LISTENING,
                ExamVersion.exam_id == mock.listening_id,
                ExamVersion.is_locked.is_(True),
            ).order_by(ExamVersion.version_number.desc()).limit(1)
        )).scalar_one_or_none()
        if v is None:
            raise HTTPException(409, "Listening uchun locked ExamVersion mavjud emas")
        l_key = {str(k): val for k, val in (v.answer_key or {}).items()}
    if mock.reading_id:
        v = (await db.execute(
            select(ExamVersion).where(
                ExamVersion.module == PaperModule.READING,
                ExamVersion.exam_id == mock.reading_id,
                ExamVersion.is_locked.is_(True),
            ).order_by(ExamVersion.version_number.desc()).limit(1)
        )).scalar_one_or_none()
        if v is None:
            raise HTTPException(409, "Reading uchun locked ExamVersion mavjud emas")
        r_key = {str(k): val for k, val in (v.answer_key or {}).items()}
    return l_key, r_key


def _validate_35(key: Optional[dict], name: str) -> None:
    if key is None:
        return
    nums = sorted(int(k) for k in key.keys() if str(k).isdigit())
    if nums != list(range(1, 36)):
        raise HTTPException(422, f"{name} 1..35 savollarni to'liq o'z ichiga olishi kerak")


def _process_one_sync(data: bytes, candidate_id: Optional[str], l_key, r_key) -> dict[str, Any]:
    try:
        result = read_sheet(data, candidate_id=candidate_id)
    except Exception as exc:
        return {
            "candidate_id": candidate_id or "UNKNOWN",
            "listening": {}, "reading": {}, "confidence": "low",
            "manual_review": True, "notes": f"OMR processing error: {exc}",
            "error": str(exc),
        }
    if l_key:
        result["listening_score"] = compare_answers(result["listening"], l_key)
        result["listening_scaled"], result["listening_cefr"] = scale_score(result["listening_score"]["raw_score"], len(l_key))
    else:
        result["listening_score"] = None
    if r_key:
        result["reading_score"] = compare_answers(result["reading"], r_key)
        result["reading_scaled"], result["reading_cefr"] = scale_score(result["reading_score"]["raw_score"], len(r_key))
    else:
        result["reading_score"] = None
    return result


@router.post("/answer-sheets", summary="Answer sheet OMR preview")
async def read_answer_sheets(
    images: list[UploadFile] = File(...),
    candidate_ids: Optional[str] = Form(None, description='JSON array: ["MK-...", "MK-..."]'),
):
    if not images or len(images) > 30:
        raise HTTPException(400, "1 dan 30 tagacha rasm yuboring")
    ids = None
    if candidate_ids:
        try: ids = json.loads(candidate_ids)
        except json.JSONDecodeError as exc: raise HTTPException(422, "candidate_ids JSON noto'g'ri") from exc
        if not isinstance(ids, list) or len(ids) != len(images):
            raise HTTPException(422, "candidate_ids soni rasmlar soniga teng bo'lishi kerak")
    results = []
    for i, f in enumerate(images):
        data, _ = await _validate_image(f)
        cid = (ids[i] if ids else None) or _candidate_from_filename(f.filename)
        results.append(await asyncio.to_thread(_process_one_sync, data, cid, None, None))
    return {"total": len(results), "manual_review_count": sum(r["manual_review"] for r in results), "results": results}


def _candidate_from_filename(filename: Optional[str]) -> Optional[str]:
    name = filename or ""
    # MK ID formati o'zgardi (eski: "MC-DDMMYYJXX", yangi: "MK-DDMMYY-NNN")
    # — ikkalasini ham tanib olamiz, chunki bazada eski formatdagi
    # ro'yxatlar hali ham mavjud bo'lishi mumkin.
    m = re.search(r"(M[CK]-[A-Za-z0-9_-]+)", name, re.I)
    return m.group(1).upper() if m else None


@router.post("/answer-sheets/with-keys", summary="OMR + answer key + score")
async def grade_answer_sheets_with_keys(
    images: list[UploadFile] = File(...),
    listening_answer_key: Optional[str] = Form(None),
    reading_answer_key: Optional[str] = Form(None),
    exam_id: Optional[str] = Form(None),
    mock_exam_id: Optional[str] = Form(None),
    candidate_ids: Optional[str] = Form(None),
    db: AsyncSession = Depends(get_db),
):
    if not images or len(images) > 30:
        raise HTTPException(400, "1 dan 30 tagacha rasm yuboring")
    exam_id = exam_id or mock_exam_id
    # Authoritative grading MUST use the answer key stored on the server.
    # Client-provided answer keys are intentionally ignored/rejected so a
    # caller cannot manufacture a passing score by changing the key.
    if not exam_id:
        raise HTTPException(422, "exam_id majburiy: answer key serverdan olinadi")
    if listening_answer_key is not None or reading_answer_key is not None:
        raise HTTPException(422, "Answer key clientdan qabul qilinmaydi; exam_id orqali server key ishlatiladi")
    l_key, r_key = await _keys_from_mock(db, exam_id)
    _validate_35(l_key, "Listening answer key")
    _validate_35(r_key, "Reading answer key")
    ids = None
    if candidate_ids:
        try: ids = json.loads(candidate_ids)
        except json.JSONDecodeError as exc: raise HTTPException(422, "candidate_ids JSON noto'g'ri") from exc
        if not isinstance(ids, list) or len(ids) != len(images):
            raise HTTPException(422, "candidate_ids soni rasmlar soniga teng bo'lishi kerak")
    results = []
    for i, f in enumerate(images):
        data, _ = await _validate_image(f)
        cid = (ids[i] if ids else None) or _candidate_from_filename(f.filename)
        result = await asyncio.to_thread(_process_one_sync, data, cid, l_key, r_key)
        results.append(result)
    return {
        "total": len(results),
        "graded": sum(bool(r.get("listening_score") or r.get("reading_score")) for r in results),
        "manual_review_count": sum(bool(r.get("manual_review")) for r in results),
        "listening_key_used": l_key is not None, "reading_key_used": r_key is not None,
        "results": results,
    }


@router.post("/answer-sheets/save", summary="OMR natijalarini MockAttemptga xavfsiz saqlash")
async def save_graded_results(payload: dict[str, Any], db: AsyncSession = Depends(get_db)):
    """Persist OMR answers and recompute objective scores server-side.

    The request may contain detected answers and an image path, but never an
    authoritative score or answer key. Scores/scaled scores are recalculated
    from the locked server-side answer key for the exact exam component.
    """
    session_id = payload.get("session_id")
    exam_id = payload.get("exam_id")
    results = payload.get("results") or []
    if not results:
        raise HTTPException(400, "results bo'sh")

    session = None
    if session_id is not None:
        session_stmt = (
            select(ExamSession)
            .join(MockExam, MockExam.id == ExamSession.mock_exam_id)
            .where(ExamSession.id == int(session_id))
        )
        session = (await db.execute(session_stmt)).scalar_one_or_none()
        if session is None:
            raise HTTPException(404, "Sessiya topilmadi")
        if exam_id and str(exam_id) != str(session.mock_exam_id):
            raise HTTPException(422, "exam_id sessiyaga mos emas")
        exam_id = session.mock_exam_id

    if not exam_id:
        raise HTTPException(422, "exam_id majburiy")

    exam_stmt = select(MockExam).where(MockExam.id == str(exam_id))
    exam = (await db.execute(exam_stmt)).scalar_one_or_none()
    if exam is None:
        raise HTTPException(404, "Mock imtihon topilmadi")

    l_key, r_key = await _keys_from_mock(db, exam.id)
    _validate_35(l_key, "Listening answer key")
    _validate_35(r_key, "Reading answer key")

    saved, skipped = [], []
    for item in results:
        cid = str(item.get("candidate_id") or item.get("student_id") or "").strip().upper()
        if not cid or cid == "UNKNOWN":
            skipped.append({"candidate_id": cid, "reason": "Candidate ID berilmagan"})
            continue

        reg_stmt = (
            select(MockRegistration)
            .join(ExamSession, ExamSession.id == MockRegistration.session_id)
            .join(MockExam, MockExam.id == ExamSession.mock_exam_id)
            .where(
                MockRegistration.student_id == cid,
                MockRegistration.mock_exam_id == exam.id,
                MockExam.id == exam.id,
            )
        )
        if session_id is not None:
            reg_stmt = reg_stmt.where(MockRegistration.session_id == int(session_id))
        reg = (await db.execute(reg_stmt.order_by(MockRegistration.created_at.desc()).limit(1))).scalar_one_or_none()
        if reg is None:
            skipped.append({"candidate_id": cid, "reason": "Bu MK ID bo'yicha ro'yxat topilmadi"})
            continue

        attempt_stmt = select(MockExamAttempt).where(
            MockExamAttempt.user_id == reg.user_id,
            MockExamAttempt.mock_exam_id == exam.id,
            MockExamAttempt.registration_id == reg.id,
        )
        attempt = (await db.execute(attempt_stmt.order_by(MockExamAttempt.started_at.desc()).limit(1))).scalar_one_or_none()
        if attempt is None:
            skipped.append({"candidate_id": cid, "reason": "MockExamAttempt topilmadi"})
            continue

        # A failed OMR run is persisted as FAILED for audit/retry; it never
        # changes MockSkillAttempt or the overall result.
        if item.get("error"):
            for skill, component_id, module in (
                (SkillType.LISTENING, exam.listening_id, PaperModule.LISTENING),
                (SkillType.READING, exam.reading_id, PaperModule.READING),
            ):
                if not component_id:
                    continue
                version = (await db.execute(
                    select(ExamVersion).where(
                        ExamVersion.module == module,
                        ExamVersion.exam_id == component_id,
                        ExamVersion.is_locked.is_(True),
                    ).order_by(ExamVersion.version_number.desc()).limit(1)
                )).scalar_one_or_none()
                if version is None:
                    continue
                failed = (await db.execute(select(OMRSubmission).where(
                    OMRSubmission.registration_id == reg.id,
                    OMRSubmission.module == module,
                    OMRSubmission.exam_version_id == version.id,
                ).limit(1))).scalar_one_or_none()
                if failed is None:
                    failed = OMRSubmission(
                        module=module, exam_id=component_id, exam_version_id=version.id,
                        candidate_id=cid, registration_id=reg.id,
                    )
                    db.add(failed)
                if item.get("image_path"):
                    try:
                        ensure_private_file(Path(str(item.get("image_path"))), "omr")
                    except HTTPException:
                        raise HTTPException(422, "OMR image_path server storage ichida bo'lishi kerak")
                    failed.image_paths = [str(item.get("image_path"))]
                failed.status = OMRSubmissionStatus.FAILED
                failed.per_question_result = {"error": str(item.get("error"))}
                failed.processed_at = datetime.now(timezone.utc)
            await db.commit()
            skipped.append({"candidate_id": cid, "reason": "OMR processing failed; retry allowed"})
            continue

        detected_listening = item.get("listening") or {}
        detected_reading = item.get("reading") or {}
        if not isinstance(detected_listening, dict) or not isinstance(detected_reading, dict):
            skipped.append({"candidate_id": cid, "reason": "OMR detected answers object emas"})
            continue

        updated = []
        omr_records = []
        for skill, detected, key, component_id in (
            (SkillType.LISTENING, detected_listening, l_key, exam.listening_id),
            (SkillType.READING, detected_reading, r_key, exam.reading_id),
        ):
            if not key or not component_id or not detected:
                continue
            # Reject answers for a different test/version rather than silently
            # accepting arbitrary question identifiers from the client.
            unknown_questions = [str(q) for q in detected.keys() if str(q) not in key]
            if unknown_questions:
                raise HTTPException(422, f"{skill.value}: noma'lum question ID: {unknown_questions[:10]}")
            score = compare_answers(detected, key)
            scaled, cefr = scale_score(score["raw_score"], len(key))
            manual_review = bool(item.get("manual_review")) or bool(score.get("ambiguous_questions"))
            skill_attempt = (
                await db.execute(
                    select(MockSkillAttempt).where(
                        MockSkillAttempt.attempt_id == attempt.id,
                        MockSkillAttempt.skill == skill,
                    )
                )
            ).scalar_one_or_none()
            if skill_attempt is None:
                skill_attempt = MockSkillAttempt(
                    attempt_id=attempt.id, user_id=reg.user_id, skill=skill,
                )
                db.add(skill_attempt)

            skill_attempt.raw_score = float(score["raw_score"])
            skill_attempt.scaled_score = float(scaled)
            skill_attempt.cefr_level = cefr
            skill_attempt.user_answers = detected
            skill_attempt.is_checked = not manual_review
            skill_attempt.submitted_at = datetime.now(timezone.utc)

            if skill == SkillType.READING:
                from app.modules.tests.reading.models import ReadingResult
                existing = (await db.execute(select(ReadingResult).where(
                    ReadingResult.exam_attempt_id == attempt.id,
                    ReadingResult.test_id == exam.reading_id,
                    ReadingResult.user_id == reg.user_id,
                ).limit(1))).scalar_one_or_none()
                values = dict(
                    raw_score=score["raw_score"], standard_score=scaled,
                    cefr_level=cefr, user_answers=detected,
                    percentage=round(score["raw_score"] / len(key) * 100, 2),
                    total_questions=len(key),
                )
                if existing:
                    for k, v in values.items(): setattr(existing, k, v)
                else:
                    db.add(ReadingResult(
                        user_id=reg.user_id, exam_attempt_id=attempt.id,
                        test_id=exam.reading_id, **values,
                    ))
            else:
                from app.modules.tests.listening.models import ListeningResult
                existing = (await db.execute(select(ListeningResult).where(
                    ListeningResult.exam_attempt_id == attempt.id,
                    ListeningResult.exam_id == exam.listening_id,
                    ListeningResult.user_id == reg.user_id,
                ).limit(1))).scalar_one_or_none()
                values = dict(
                    raw_score=score["raw_score"], standard_score=scaled,
                    cefr_level=cefr, user_answers=detected,
                    percentage=round(score["raw_score"] / len(key) * 100, 2),
                    total_questions=len(key), correct_answers=score["raw_score"],
                )
                if existing:
                    for k, v in values.items(): setattr(existing, k, v)
                else:
                    db.add(ListeningResult(
                        user_id=reg.user_id, exam_attempt_id=attempt.id,
                        exam_id=exam.listening_id, **values,
                    ))

            # Lock to the latest immutable version for this exact component.
            version = (await db.execute(
                select(ExamVersion)
                .where(
                    ExamVersion.module == (PaperModule.LISTENING if skill == SkillType.LISTENING else PaperModule.READING),
                    ExamVersion.exam_id == component_id,
                    ExamVersion.is_locked.is_(True),
                )
                .order_by(ExamVersion.version_number.desc())
                .limit(1)
            )).scalar_one_or_none()
            if version is not None:
                module = PaperModule.LISTENING if skill == SkillType.LISTENING else PaperModule.READING
                existing_omr = (await db.execute(select(OMRSubmission).where(
                    OMRSubmission.registration_id == reg.id,
                    OMRSubmission.module == module,
                    OMRSubmission.exam_version_id == version.id,
                ).limit(1))).scalar_one_or_none()
                if existing_omr is None:
                    existing_omr = OMRSubmission(
                        module=module, exam_id=component_id, exam_version_id=version.id,
                        candidate_id=cid, registration_id=reg.id,
                    )
                    db.add(existing_omr)
                if item.get("image_path"):
                    try:
                        ensure_private_file(Path(str(item.get("image_path"))), "omr")
                    except HTTPException:
                        raise HTTPException(422, "OMR image_path server storage ichida bo'lishi kerak")
                    existing_omr.image_paths = [str(item.get("image_path"))]
                existing_omr.detected_answers = detected
                existing_omr.per_question_result = score
                existing_omr.correct_count = len(score["correct_questions"])
                existing_omr.wrong_count = len(score["wrong_questions"])
                existing_omr.blank_count = len(score["blank_questions"])
                existing_omr.invalid_count = len(score["ambiguous_questions"])
                existing_omr.total_score = score["raw_score"]
                existing_omr.status = OMRSubmissionStatus.NEEDS_REVIEW if manual_review else OMRSubmissionStatus.PROCESSED
                existing_omr.processed_at = datetime.now(timezone.utc)
            updated.append({"skill": skill.value, "raw_score": score["raw_score"], "scaled_score": scaled, "manual_review": manual_review})

        if updated:
            await db.commit()
            saved.append({"candidate_id": cid, "user_id": str(reg.user_id), "attempt_id": attempt.id, "updated_skills": updated})
        else:
            skipped.append({"candidate_id": cid, "reason": "Yaroqli OMR component topilmadi"})

    return {"saved": len(saved), "skipped": len(skipped), "details": {"saved": saved, "skipped": skipped}}


@router.post("/answer-sheets/process-and-save", summary="OMRni ishlash va Mock Examga birdan saqlash")
async def process_and_save_answer_sheets(
    images: list[UploadFile] = File(...),
    exam_id: str = Form(...),
    session_id: Optional[int] = Form(None),
    candidate_ids: Optional[str] = Form(None),
    db: AsyncSession = Depends(get_db),
):
    if not images or len(images) > 30:
        raise HTTPException(400, "1 dan 30 tagacha rasm yuboring")
    ids = None
    if candidate_ids:
        try: ids = json.loads(candidate_ids)
        except json.JSONDecodeError as exc: raise HTTPException(422, "candidate_ids JSON noto'g'ri") from exc
        if not isinstance(ids, list) or len(ids) != len(images): raise HTTPException(422, "candidate_ids soni rasmlar soniga teng bo'lishi kerak")
    l_key, r_key = await _keys_from_mock(db, exam_id)
    _validate_35(l_key, "Listening answer key"); _validate_35(r_key, "Reading answer key")
    # The answer keys are now fully materialized. Release the read transaction
    # before CPU-heavy OpenCV/Tesseract work so OMR processing never holds a DB
    # transaction while the event loop/thread pool is busy.
    await db.rollback()
    out=[]
    omr_dir = private_path("omr")
    ext_by_type = {"image/jpeg": ".jpg", "image/png": ".png", "image/webp": ".webp"}
    for i,f in enumerate(images):
        data, content_type = await _validate_image(f)
        cid=(ids[i] if ids else None) or _candidate_from_filename(f.filename)
        result=await asyncio.to_thread(_process_one_sync, data,cid,l_key,r_key)
        path=omr_dir / generated_name(ext_by_type[content_type])
        await asyncio.to_thread(path.write_bytes, data)
        # Internal filesystem path only; never expose the scan through /static.
        result["image_path"] = str(path)
        out.append(result)
    saved = await save_graded_results({"exam_id": exam_id, "session_id": session_id, "results": out}, db)
    return {"processed": len(out), "manual_review_count": sum(bool(x.get("manual_review")) for x in out), "results": out, "save": saved}


@router.get("/omr/{submission_id}/image/{image_index}", summary="OMR scanini yuklash")
async def get_omr_image(
    submission_id: int,
    image_index: int,
    db: AsyncSession = Depends(get_db),
):
    stmt = (
        select(OMRSubmission)
        .join(ExamVersion, ExamVersion.id == OMRSubmission.exam_version_id)
        .where(OMRSubmission.id == submission_id)
    )
    submission = (await db.execute(stmt)).scalar_one_or_none()
    if submission is None:
        raise HTTPException(404, "OMR submission topilmadi")
    if image_index < 0 or image_index >= len(submission.image_paths or []):
        raise HTTPException(404, "OMR image topilmadi")
    raw_path = Path(str(submission.image_paths[image_index]))
    try:
        path = ensure_private_file(raw_path, "omr")
    except HTTPException:
        raise HTTPException(404, "OMR image topilmadi")
    return FileResponse(path, headers={"Cache-Control": "private, no-store", "X-Content-Type-Options": "nosniff", "Content-Disposition": f'inline; filename="{path.name}"'})


@router.post("/answer-sheets/preview", summary="Bitta answer sheet preview")
async def preview_single_sheet(image: UploadFile = File(...)):
    data, _ = await _validate_image(image)
    return await asyncio.to_thread(_process_one_sync, data, _candidate_from_filename(image.filename), None, None)
