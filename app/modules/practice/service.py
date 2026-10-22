"""Practice (is_practice=True) urinishlari: boshlash, javob saqlash, finalizatsiya, holat.

Faqat practice testlar uchun; rasmiy imtihon (mock) mantiqi `app.modules.exams.attempts` da.
"""

from __future__ import annotations


import uuid
from datetime import datetime, timezone
from typing import List, Optional

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.tests.attempts.timer import calculate_deadline, is_expired, transition_practice_attempt, utc_now
from app.modules.exams.mock.models import (
    AttemptAnswer,
    PracticeAttempt,
    PracticeAttemptStatus,
    SkillType,
)


from app.modules.tests.attempts.common import (
    SkillSpec,
    _Owner,
    _lock_row,
    _score,
    _state_dict,
    load_answers,
    resolve_question,
    upsert_answer,
)


def _questions_for_attempt(spec: SkillSpec, test, attempt: PracticeAttempt) -> list:
    questions = spec.questions_of(test)
    if attempt.part_number is None:
        return questions
    parts = list(getattr(test, "parts", []) or [])
    if spec.skill == SkillType.READING:
        selected = parts[attempt.part_number - 1:attempt.part_number] if 1 <= attempt.part_number <= len(parts) else []
    else:
        selected = [p for p in parts if getattr(p, "part_number", None) == attempt.part_number]
    if not selected:
        raise HTTPException(404, "Practice qismi topilmadi")
    selected_ids = {q.id for p in selected for q in getattr(p, "questions", [])}
    return [q for q in questions if q.id in selected_ids]



# Reading practice vaqti: to'liq test = 60 daqiqa, alohida part (mini-test yoki PART_PRACTICE) = 20 daqiqa.
READING_FULL_PRACTICE_MINUTES = 60
READING_PART_PRACTICE_MINUTES = 20


def practice_duration_seconds(spec, test, mode: str | None = None, part_number: int | None = None) -> int:
    """Practice urinishining davomiyligi (soniya). Serverdagi yagona manba."""
    if spec.skill == SkillType.READING:
        is_part = (
            part_number is not None
            or mode == "PART_PRACTICE"
            or getattr(test, "practice_part_number", None) is not None
            or getattr(test, "parent_test_id", None) is not None
        )
        return (READING_PART_PRACTICE_MINUTES if is_part else READING_FULL_PRACTICE_MINUTES) * 60
    return int(getattr(test, "duration_minutes", 0) or 0) * 60

async def _owned_practice(db: AsyncSession, attempt_id: int, user_id: uuid.UUID, skill: SkillType) -> PracticeAttempt:
    a = (await db.execute(
        select(PracticeAttempt).where(PracticeAttempt.id == attempt_id).execution_options(populate_existing=True)
    )).scalar_one_or_none()
    # Boshqa birovniki yoki boshqa skill — mavjudligini oshkor qilmaymiz.
    if a is None or a.user_id != user_id or a.skill != skill:
        raise HTTPException(404, "Urinish topilmadi")
    return a


async def start_practice_attempt(
    db: AsyncSession, spec: SkillSpec, user_id: uuid.UUID, test_id: str,
    *, mode: str = "FULL_PRACTICE", part_number: int | None = None,
) -> PracticeAttempt:
    """Create/resume one practice attempt and reserve quota exactly once."""
    test = await spec.load_test(db, test_id)
    if not test:
        raise HTTPException(404, "Test topilmadi")
    require_practice_test(test)   # mock-only (is_practice=False) test practice orqali ochilmaydi
    from app.modules.billing.entitlements import enforce_practice_limit
    if part_number is not None and part_number < 1:
        raise HTTPException(422, "part_number 1 yoki undan katta bo'lishi kerak")
    if part_number is not None:
        parts = list(getattr(test, "parts", []) or [])
        if spec.skill == SkillType.READING:
            valid = 1 <= part_number <= len(parts)
        else:
            valid = any(getattr(part, "part_number", None) == part_number for part in parts)
        if not valid:
            raise HTTPException(404, "Practice qismi topilmadi")
    existing = (await db.execute(
        select(PracticeAttempt).where(
            PracticeAttempt.user_id == user_id,
            PracticeAttempt.skill == spec.skill,
            PracticeAttempt.test_id == test_id,
            PracticeAttempt.mode == mode,
            PracticeAttempt.part_number == part_number,
            PracticeAttempt.status == PracticeAttemptStatus.IN_PROGRESS,
        ).order_by(PracticeAttempt.id.desc()).limit(1)
    )).scalar_one_or_none()
    if existing:
        return existing
    scope_key = f"{user_id}:{spec.skill.value}:{test_id}:{mode}:{part_number if part_number is not None else '-'}"
    existing = (await db.execute(select(PracticeAttempt).where(PracticeAttempt.active_scope_key == scope_key).limit(1))).scalar_one_or_none()
    if existing and existing.status == PracticeAttemptStatus.IN_PROGRESS:
        return existing
    if existing and existing.status == PracticeAttemptStatus.EXPIRED:
        # EXPIRED is no longer an active reservation. Release the scope so a
        # later practice start can create a fresh attempt without mutating the
        # historical expired row.
        existing.active_scope_key = None
        await db.flush()
    now = utc_now()
    duration_seconds = practice_duration_seconds(spec, test, mode, part_number)
    attempt = PracticeAttempt(
        user_id=user_id, skill=spec.skill, test_id=test_id, mode=mode, part_number=part_number, started_at=now,
        allowed_duration_seconds=duration_seconds or None,
        expires_at=calculate_deadline(now, duration_seconds) if duration_seconds > 0 else None,
        active_scope_key=scope_key,
    )
    db.add(attempt)
    try:
        await db.flush()
    except IntegrityError:
        await db.rollback()
        existing = (await db.execute(select(PracticeAttempt).where(PracticeAttempt.active_scope_key == scope_key).limit(1))).scalar_one_or_none()
        if existing is None:
            raise
        return existing
    await enforce_practice_limit(
        db, user_id, spec.skill.value, test_id, mode=mode, attempt_id=attempt.id, test=test
    )
    return attempt


async def save_practice_answer(
    db: AsyncSession, spec: SkillSpec, user_id: uuid.UUID, attempt_id: int,
    question_id: Optional[int], question_number: Optional[int], answer: List[str],
) -> AttemptAnswer:
    attempt = await _owned_practice(db, attempt_id, user_id, spec.skill)
    if attempt.status != PracticeAttemptStatus.IN_PROGRESS:
        if attempt.status == PracticeAttemptStatus.EXPIRED:
            raise HTTPException(409, "Urinish vaqti tugagan — javobni o'zgartirib bo'lmaydi")
        raise HTTPException(409, "Urinish yakunlangan — javobni o'zgartirib bo'lmaydi")
    if attempt.expires_at and is_expired(attempt.expires_at):
        transition_practice_attempt(attempt, PracticeAttemptStatus.EXPIRED)
        await db.flush()
        raise HTTPException(409, "Practice vaqti tugagan")
    test = await spec.load_test(db, attempt.test_id)
    if not test:
        raise HTTPException(404, "Test topilmadi")
    q = resolve_question(_questions_for_attempt(spec, test, attempt), question_id, question_number)
    row = await upsert_answer(db, _Owner(practice_attempt_id=attempt.id), q.question_number, q.id, answer)
    attempt.last_saved_at = datetime.now(timezone.utc)
    return row


async def finalize_practice(db: AsyncSession, spec: SkillSpec, user_id: uuid.UUID, attempt_id: int):
    """Saqlangan javoblardan baholab natijani yozadi. Idempotent. Natija (ORM) qaytadi."""
    try:
        await _owned_practice(db, attempt_id, user_id, spec.skill)   # 404 tekshiruvi
        await _lock_row(db, PracticeAttempt, attempt_id)
        attempt = await _owned_practice(db, attempt_id, user_id, spec.skill)  # qulfdan keyin yangi holat

        if attempt.status == PracticeAttemptStatus.SUBMITTED and attempt.result_id is not None:
            return await db.get(spec.result_model, attempt.result_id)
        if attempt.status not in {PracticeAttemptStatus.IN_PROGRESS, PracticeAttemptStatus.EXPIRED}:
            raise HTTPException(409, "Urinish holati finalizatsiyaga mos emas")
        if attempt.status == PracticeAttemptStatus.IN_PROGRESS and attempt.expires_at and is_expired(attempt.expires_at):
            transition_practice_attempt(attempt, PracticeAttemptStatus.EXPIRED)

        test = await spec.load_test(db, attempt.test_id)
        if not test:
            raise HTTPException(404, "Test topilmadi")
        questions = _questions_for_attempt(spec, test, attempt)
        saved = await load_answers(db, _Owner(practice_attempt_id=attempt.id))
        answers = {n: list(r.answer or []) for n, r in saved.items()}
        correct, total, std, cefr, pct = _score(spec, questions, answers)

        result = spec.result_model(
            user_id=user_id,
            practice_attempt_id=attempt.id,
            raw_score=correct,
            standard_score=std,
            cefr_level=cefr,
            user_answers=spec.answers_snapshot(answers),
            **{spec.result_test_col: attempt.test_id},
        )
        spec.set_result_extra(result, correct, total, pct)
        db.add(result)
        await db.flush()

        transition_practice_attempt(attempt, PracticeAttemptStatus.SUBMITTED)
        attempt.submitted_at = datetime.now(timezone.utc)
        attempt.result_id = result.id
        attempt.active_scope_key = None
        await db.commit()
        return result
    except Exception:
        await db.rollback()
        raise


async def practice_state(db: AsyncSession, spec: SkillSpec, user_id: uuid.UUID, attempt_id: int) -> dict:
    attempt = await _owned_practice(db, attempt_id, user_id, spec.skill)
    test = await spec.load_test(db, attempt.test_id)
    if attempt.expires_at is None and attempt.status == PracticeAttemptStatus.IN_PROGRESS and test is not None:
        duration_seconds = practice_duration_seconds(spec, test, attempt.mode, attempt.part_number)
        if duration_seconds > 0:
            attempt.allowed_duration_seconds = duration_seconds
            attempt.expires_at = calculate_deadline(attempt.started_at, duration_seconds)
            await db.commit()
    if attempt.status == PracticeAttemptStatus.IN_PROGRESS and attempt.expires_at and is_expired(attempt.expires_at):
        # Expiry is server-driven; finalize from persisted answers, never from client timer.
        result = await finalize_practice(db, spec, user_id, attempt.id)
        attempt = await _owned_practice(db, attempt_id, user_id, spec.skill)
    saved = await load_answers(db, _Owner(practice_attempt_id=attempt.id))
    duration = attempt.allowed_duration_seconds or (practice_duration_seconds(spec, test, attempt.mode, attempt.part_number) if test else None)
    return _state_dict(
        attempt_id=attempt.id, test_id=attempt.test_id, skill=spec.skill,
        status=attempt.status.value, started_at=attempt.started_at, submitted_at=attempt.submitted_at,
        expires_at=attempt.expires_at, last_saved_at=attempt.last_saved_at,
        duration_seconds=duration, total_questions=len(_questions_for_attempt(spec, test, attempt)) if test else 0,
        rows=saved, result_id=attempt.result_id, exam_attempt_id=None, mode=attempt.mode, part_number=attempt.part_number,
    )


def require_practice_test(test) -> None:
    """Practice oqimi faqat faol va is_practice=True testlar uchun (mock-only test -> 404)."""
    if test is None or not getattr(test, "is_active", True) or not getattr(test, "is_practice", False):
        raise HTTPException(404, "Practice testi topilmadi")


async def submit_practice_with_answers(
    db: AsyncSession, spec: SkillSpec, user_id: uuid.UUID, test, incoming: dict,
):
    """Eski `/submit` kontraktining practice tarmog'i: yangi urinish ochadi, body'dagi
    javoblarni upsert qiladi va saqlangan javoblardan baholaydi. Natija (ORM) qaytadi."""
    require_practice_test(test)
    try:
        from app.modules.billing.entitlements import enforce_practice_limit
        now = utc_now()
        duration_seconds = practice_duration_seconds(spec, test, "FULL_PRACTICE")
        attempt = PracticeAttempt(
            user_id=user_id, skill=spec.skill, test_id=getattr(test, "id"), mode="FULL_PRACTICE", started_at=now,
            allowed_duration_seconds=duration_seconds or None,
            expires_at=calculate_deadline(now, duration_seconds) if duration_seconds > 0 else None,
        )
        db.add(attempt)
        await db.flush()
        await enforce_practice_limit(
            db, user_id, spec.skill.value, getattr(test, "id"),
            mode="FULL_PRACTICE", attempt_id=attempt.id, test=test,
        )
        owner = _Owner(practice_attempt_id=attempt.id)
        qs = {q.question_number: q for q in spec.questions_of(test)}
        for number, ans in incoming.items():
            if number in qs:
                await upsert_answer(db, owner, number, qs[number].id, ans)
        await db.flush()
        attempt_id = attempt.id
        await db.commit()
    except Exception:
        await db.rollback()
        raise
    return await finalize_practice(db, spec, user_id, attempt_id)
