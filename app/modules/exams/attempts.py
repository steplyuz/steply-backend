"""Rasmiy imtihon (mock) urinishlari: javob saqlash, bo'lim finalizatsiyasi, holat.

Practice mantiqi `app.modules.practice.service` da.
"""

from __future__ import annotations


import uuid
from datetime import datetime, timezone
from typing import List, Optional

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.exams.mock.finalize import try_finalize_mock_attempt
from app.modules.tests.attempts.timer import is_expired
from app.modules.exams.mock.models import (
    AttemptAnswer,
    MockExam,
    MockExamAttempt,
    MockSkillAttempt,
    AttemptStatus,
    SkillType,
)


from app.modules.tests.attempts.common import (
    SkillSpec,
    _Owner,
    _lock_row,
    _score,
    _state_dict,
    _utcnow_naive,
    load_answers,
    logger,
    normalize_answer,
    resolve_question,
    spec_for,
    upsert_answer,
)


async def _owned_mock_skill(
    db: AsyncSession, attempt_id: int, user_id: uuid.UUID, skill: SkillType
):
    attempt = await db.get(MockExamAttempt, attempt_id)
    if not attempt or attempt.user_id != user_id:
        raise HTTPException(404, "Imtihon urinish topilmadi")
    row = (await db.execute(
        select(MockSkillAttempt).where(
            MockSkillAttempt.attempt_id == attempt_id,
            MockSkillAttempt.skill == skill,
            MockSkillAttempt.user_id == user_id,
        ).execution_options(populate_existing=True)
    )).scalar_one_or_none()
    if row is None:
        raise HTTPException(404, "Bo'lim urinishi topilmadi")
    return attempt, row


async def mock_test_id(db: AsyncSession, spec: SkillSpec, attempt: MockExamAttempt) -> str:
    exam = await db.get(MockExam, attempt.mock_exam_id)
    test_id = getattr(exam, spec.mock_test_attr, None) if exam else None
    if not test_id:
        raise HTTPException(404, "Bu imtihonga test biriktirilmagan")
    return test_id


async def find_owned_mock_skill(db: AsyncSession, attempt_id: Optional[int], user_id: uuid.UUID, skill: SkillType):
    """Legacy submit uchun: topilmasa None (eski xatti-harakat: practice sifatida davom etadi)."""
    if attempt_id is None:
        return None
    try:
        return await _owned_mock_skill(db, attempt_id, user_id, skill)
    except HTTPException:
        return None


async def save_mock_answer(
    db: AsyncSession, spec: SkillSpec, user_id: uuid.UUID, attempt_id: int,
    question_id: Optional[int], question_number: Optional[int], answer: List[str],
) -> AttemptAnswer:
    from app.modules.exams.mock.services import ensure_mock_attempt_active
    attempt = await ensure_mock_attempt_active(db, attempt_id, user_id)
    attempt, skill_row = await _owned_mock_skill(db, attempt_id, user_id, spec.skill)
    if skill_row.submitted_at is not None:
        raise HTTPException(409, "Bo'lim topshirilgan — javobni o'zgartirib bo'lmaydi")
    test = await spec.load_test(db, await mock_test_id(db, spec, attempt))
    if not test:
        raise HTTPException(404, "Test topilmadi")
    q = resolve_question(spec.questions_of(test), question_id, question_number)
    row = await upsert_answer(db, _Owner(mock_skill_attempt_id=skill_row.id), q.question_number, q.id, answer)
    skill_row.last_saved_at = datetime.now(timezone.utc)
    return row


async def save_mock_answers_bulk(
    db: AsyncSession, user_id: uuid.UUID, attempt_id: int, skill: SkillType, user_answers: dict
) -> int:
    """Eski `PATCH .../progress/{skill}` (`user_answers` dict) uchun: savol raqami -> javob.

    Mavjud qatorlar o'chirilmaydi (merge), noma'lum savol raqamlari o'tkazib
    yuboriladi (butun autosave yiqilib qolmasligi uchun) va log'ga yoziladi.
    """
    spec = spec_for(skill)
    from app.modules.exams.mock.services import ensure_mock_attempt_active
    attempt = await ensure_mock_attempt_active(db, attempt_id, user_id)
    attempt, skill_row = await _owned_mock_skill(db, attempt_id, user_id, skill)
    if skill_row.submitted_at is not None:
        raise HTTPException(409, "Bo'lim topshirilgan — javobni o'zgartirib bo'lmaydi")
    test = await spec.load_test(db, await mock_test_id(db, spec, attempt))
    if not test:
        return 0
    by_number = {q.question_number: q for q in spec.questions_of(test)}
    saved = 0
    for key, value in (user_answers or {}).items():
        try:
            number = int(key)
        except (TypeError, ValueError):
            logger.warning("autosave: noto'g'ri savol kaliti %r (attempt=%s)", key, attempt_id)
            continue
        q = by_number.get(number)
        if q is None:
            logger.warning("autosave: testda %s-savol yo'q (attempt=%s)", number, attempt_id)
            continue
        await upsert_answer(db, _Owner(mock_skill_attempt_id=skill_row.id), number, q.id, normalize_answer(value))
        saved += 1
    return saved


async def finalize_mock_skill(db: AsyncSession, spec: SkillSpec, user_id: uuid.UUID, attempt_id: int):
    """Mock Reading/Listening bo'limini saqlangan javoblardan baholaydi. Idempotent.

    Natija (ORM) qaytadi. Barcha 4 bo'lim baholangan bo'lsa umumiy
    `MockExamResult` ham shu tranzaksiyada yaratiladi; aks holda (Writing/
    Speaking kutilmoqda) umumiy natija yaratilmaydi — "pending" holat saqlanadi.
    """
    try:
        _, skill_row = await _owned_mock_skill(db, attempt_id, user_id, spec.skill)
        await _lock_row(db, MockSkillAttempt, skill_row.id)
        attempt, skill_row = await _owned_mock_skill(db, attempt_id, user_id, spec.skill)

        test_id = await mock_test_id(db, spec, attempt)
        existing = (await db.execute(
            select(spec.result_model).where(
                spec.result_model.exam_attempt_id == attempt_id,
                spec.result_test_attr() == test_id,
            ).limit(1)
        )).scalar_one_or_none()
        if existing is not None:
            return existing                      # idempotent: yakuniy natija o'zgarmaydi
        if skill_row.submitted_at is not None:
            # Bo'lim topshirilgan, lekin natija qatori yo'q (eski/qo'lda baholangan yozuv).
            raise HTTPException(409, "Bu bo'lim allaqachon topshirilgan")
        if attempt.is_finished:
            raise HTTPException(409, "Bu imtihon allaqachon yakunlangan")

        test = await spec.load_test(db, test_id)
        if not test:
            raise HTTPException(404, "Test topilmadi")
        questions = spec.questions_of(test)
        saved = await load_answers(db, _Owner(mock_skill_attempt_id=skill_row.id))
        answers = {n: list(r.answer or []) for n, r in saved.items()}
        correct, total, std, cefr, pct = _score(spec, questions, answers)
        snapshot = spec.answers_snapshot(answers)

        result = spec.result_model(
            user_id=user_id,
            exam_attempt_id=attempt_id,
            raw_score=correct,
            standard_score=std,
            cefr_level=cefr,
            user_answers=snapshot,
            **{spec.result_test_col: test_id},
        )
        spec.set_result_extra(result, correct, total, pct)
        db.add(result)
        await db.flush()

        skill_row.raw_score = float(correct)
        skill_row.scaled_score = float(std)
        skill_row.cefr_level = cefr
        skill_row.user_answers = snapshot
        skill_row.is_checked = True
        skill_row.submitted_at = _utcnow_naive()
        await db.flush()
        await try_finalize_mock_attempt(db, attempt_id)
        await db.commit()
        return result
    except Exception:
        await db.rollback()
        raise


async def get_mock_result(db: AsyncSession, spec: SkillSpec, user_id: uuid.UUID, attempt_id: int):
    attempt, _ = await _owned_mock_skill(db, attempt_id, user_id, spec.skill)
    test_id = await mock_test_id(db, spec, attempt)
    result = (await db.execute(
        select(spec.result_model).where(
            spec.result_model.exam_attempt_id == attempt_id,
            spec.result_test_attr() == test_id,
            spec.result_model.user_id == user_id,
        ).limit(1)
    )).scalar_one_or_none()
    if result is None:
        raise HTTPException(404, "Natija hali mavjud emas")
    return result


async def mock_state(db: AsyncSession, spec: SkillSpec, user_id: uuid.UUID, attempt_id: int) -> dict:
    attempt, skill_row = await _owned_mock_skill(db, attempt_id, user_id, spec.skill)
    if attempt.status == AttemptStatus.IN_PROGRESS and attempt.expires_at and is_expired(attempt.expires_at):
        from app.modules.exams.mock.services import expire_mock_attempt
        await expire_mock_attempt(db, attempt.id)
        await db.commit()
        attempt, skill_row = await _owned_mock_skill(db, attempt_id, user_id, spec.skill)
    test_id = await mock_test_id(db, spec, attempt)
    test = await spec.load_test(db, test_id)
    saved = await load_answers(db, _Owner(mock_skill_attempt_id=skill_row.id))
    submitted = skill_row.submitted_at is not None
    result_id = None
    if submitted:
        result_id = (await db.execute(
            select(spec.result_model.id).where(
                spec.result_model.exam_attempt_id == attempt_id, spec.result_test_attr() == test_id
            ).limit(1)
        )).scalar_one_or_none()
    return _state_dict(
        attempt_id=attempt.id, test_id=test_id, skill=spec.skill,
        status=attempt.status.value if attempt.status == AttemptStatus.EXPIRED else ("SUBMITTED" if submitted else "IN_PROGRESS"),
        started_at=attempt.started_at, submitted_at=skill_row.submitted_at, expires_at=attempt.expires_at,
        last_saved_at=skill_row.last_saved_at, duration_seconds=attempt.allowed_duration_seconds,
        total_questions=len(spec.questions_of(test)) if test else 0, rows=saved, result_id=result_id,
        exam_attempt_id=attempt.id, mode="OFFICIAL_EXAM", part_number=None,
    )


async def submit_mock_with_answers(
    db: AsyncSession, spec: SkillSpec, user_id: uuid.UUID, test, exam_attempt_id: Optional[int],
    incoming: dict,
):
    """Eski `/submit` kontraktining mock tarmog'i. Mock urinish topilmasa `None` qaytadi
    (chaqiruvchi practice tarmog'iga o'tadi). Natija (ORM) qaytadi."""
    found = await find_owned_mock_skill(db, exam_attempt_id, user_id, spec.skill)
    if found is None:
        return None
    attempt, skill_row = found
    assigned = await mock_test_id(db, spec, attempt)
    if assigned != getattr(test, "id"):
        raise HTTPException(403, "Bu skill uchun boshqa testdan foydalanish mumkin emas")
    if skill_row.submitted_at is None and not attempt.is_finished:
        from app.modules.exams.mock.services import ensure_mock_attempt_active
        await ensure_mock_attempt_active(db, attempt.id, user_id)
        try:
            owner = _Owner(mock_skill_attempt_id=skill_row.id)
            for number, ans in incoming.items():
                q = next(q for q in spec.questions_of(test) if q.question_number == number)
                await upsert_answer(db, owner, number, q.id, ans)
            await db.flush()
        except Exception:
            await db.rollback()
            raise
    return await finalize_mock_skill(db, spec, user_id, attempt_id=exam_attempt_id)
