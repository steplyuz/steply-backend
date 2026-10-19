"""Authoritative Mock/Exam result finalization.

The existing ``MockExamAttempt`` + ``MockSkillAttempt`` tables are the
project's central official-exam attempt graph.  This module is the single
place that decides whether the four persisted skill results are complete and
whether an overall result may become FINALIZED.
"""
from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from app.modules.tests.attempts.timer import transition_mock_attempt

from app.modules.tests.scoring_policy import cefr_for_score
from .models import (
    AttemptStatus,
    MockExamAttempt,
    MockExamResult,
    MockRegistration,
    MockSkillAttempt,
    RegistrationStatus,
    SkillType,
)

_REQUIRED_SKILLS = (
    SkillType.READING,
    SkillType.LISTENING,
    SkillType.WRITING,
    SkillType.SPEAKING,
)


overall_cefr_level = cefr_for_score


def _score(value: float | None) -> float:
    value = float(value or 0.0)
    if value < 0 or value > 75:
        raise ValueError("skill scaled score must be between 0 and 75")
    return round(value, 2)


async def _lock_attempt(db: AsyncSession, attempt_id: int) -> MockExamAttempt | None:
    # SQLite ignores FOR UPDATE, so a write UPDATE is used as the portable
    # row-lock primitive. PostgreSQL also holds the row lock until commit.
    await db.execute(
        update(MockExamAttempt)
        .where(MockExamAttempt.id == attempt_id)
        .values(status=MockExamAttempt.status)
    )
    return await db.get(MockExamAttempt, attempt_id)


async def _skill_state(db: AsyncSession, attempt_id: int):
    rows = (
        await db.execute(
            select(MockSkillAttempt).where(MockSkillAttempt.attempt_id == attempt_id)
        )
    ).scalars().all()
    by_skill = {row.skill: row for row in rows}
    states: dict[str, str] = {}
    scores: dict[SkillType, float] = {}
    for skill in _REQUIRED_SKILLS:
        row = by_skill.get(skill)
        if row is None:
            states[skill.value] = "NOT_STARTED"
            continue
        if row.is_checked:
            states[skill.value] = "COMPLETED"
            scores[skill] = _score(row.scaled_score)
        elif row.submitted_at is not None:
            states[skill.value] = "CHECKING"
        elif row.last_saved_at is not None or row.current_part is not None or row.current_question is not None:
            states[skill.value] = "IN_PROGRESS"
        else:
            states[skill.value] = "NOT_STARTED"
    return rows, by_skill, states, scores


async def _get_or_create_overall(db: AsyncSession, attempt: MockExamAttempt) -> MockExamResult:
    result = (
        await db.execute(
            select(MockExamResult).where(MockExamResult.attempt_id == attempt.id).limit(1)
        )
    ).scalar_one_or_none()
    if result is not None:
        return result
    try:
        async with db.begin_nested():
            result = MockExamResult(
                user_id=attempt.user_id,
                attempt_id=attempt.id,
                status="PENDING",
                reading_ball=0.0,
                listening_ball=0.0,
                writing_ball=0.0,
                speaking_ball=0.0,
                overall_score=0.0,
                cefr_level="",
            )
            db.add(result)
            await db.flush()
    except IntegrityError:
        result = (
            await db.execute(
                select(MockExamResult).where(MockExamResult.attempt_id == attempt.id).limit(1)
            )
        ).scalar_one_or_none()
    if result is None:
        raise RuntimeError("Unable to create/load overall result")
    return result


async def _refresh_overall_snapshot(
    db: AsyncSession,
    attempt: MockExamAttempt,
    result: MockExamResult,
    states: dict[str, str],
    scores: dict[SkillType, float],
    *,
    finalized: bool,
) -> MockExamResult:
    result.user_id = attempt.user_id
    result.reading_ball = scores.get(SkillType.READING, result.reading_ball or 0.0)
    result.listening_ball = scores.get(SkillType.LISTENING, result.listening_ball or 0.0)
    result.writing_ball = scores.get(SkillType.WRITING, result.writing_ball or 0.0)
    result.speaking_ball = scores.get(SkillType.SPEAKING, result.speaking_ball or 0.0)
    result.overall_score = round(
        (result.reading_ball + result.listening_ball + result.writing_ball + result.speaking_ball) / 4,
        2,
    ) if finalized else result.overall_score
    result.status = "FINALIZED" if finalized else "PENDING"
    result.cefr_level = overall_cefr_level(result.overall_score) if finalized else ""
    if finalized:
        result.finalized_at = result.finalized_at or datetime.now(timezone.utc)
    return result


async def try_finalize_mock_attempt(db: AsyncSession, attempt_id: int) -> bool:
    """Finalize only when all four persisted skill attempts are complete.

    Returns True only when this call performs the final state transition.
    Existing FINALIZED results are left untouched, making repeated calls
    idempotent and preserving historical snapshots.
    """
    attempt = await _lock_attempt(db, attempt_id)
    if attempt is None:
        return False
    if attempt.is_finished or attempt.status == AttemptStatus.FINALIZED:
        return False
    if attempt.status not in {AttemptStatus.IN_PROGRESS, AttemptStatus.EXPIRED, AttemptStatus.READY_FOR_FINAL, AttemptStatus.PAPER_COMPLETED, AttemptStatus.SPEAKING_COMPLETED}:
        return False

    _rows, _by_skill, states, scores = await _skill_state(db, attempt_id)
    if len(states) != 4 or any(states.get(skill.value) != "COMPLETED" for skill in _REQUIRED_SKILLS):
        result = await _get_or_create_overall(db, attempt)
        await _refresh_overall_snapshot(db, attempt, result, states, scores, finalized=False)
        return False

    result = await _get_or_create_overall(db, attempt)
    if result.status == "FINALIZED" and attempt.is_finished:
        return False

    await _refresh_overall_snapshot(db, attempt, result, states, scores, finalized=True)
    now = result.finalized_at or datetime.now(timezone.utc)
    transition_mock_attempt(attempt, AttemptStatus.FINALIZED)
    attempt.is_finished = True
    attempt.finished_at = attempt.finished_at or now
    # Active-start uniqueness is only for an unfinished online attempt.
    attempt.active_scope_key = None
    if attempt.registration_id is not None:
        registration = await db.get(MockRegistration, attempt.registration_id)
        if registration and registration.status != RegistrationStatus.CANCELLED:
            registration.status = RegistrationStatus.COMPLETED
    return True
