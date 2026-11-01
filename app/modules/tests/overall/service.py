"""Barcha skill'lar bo'yicha so'nggi practice natijalaridan umumiy natija.

Qoida (scoring_policy §3): 4 bo'lim (0–75) o'rtachasi -> CEFR.
Mock imtihon finalize'iga bog'liq emas: faqat practice natijalari olinadi.
"""
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.exams.mock.models import PracticeAttempt
from app.modules.tests.listening.models import ListeningResult
from app.modules.tests.reading.models import ReadingResult
from app.modules.tests.scoring_policy import cefr_for_score, overall_cefr
from app.modules.tests.speaking.ai_models import SpeakingAIJob
from app.modules.tests.speaking.scoring import speaking_scaled_from_parts
from app.modules.tests.writing.models import WritingResult

SKILLS = ("READING", "LISTENING", "WRITING", "SPEAKING")


async def _latest_reading_like(db: AsyncSession, model, user_id) -> float | None:
    return (await db.execute(
        select(model.standard_score)
        .join(PracticeAttempt, PracticeAttempt.id == model.practice_attempt_id)
        .where(model.user_id == user_id, PracticeAttempt.mode == "FULL_PRACTICE")
        .order_by(model.created_at.desc(), model.id.desc())
        .limit(1)
    )).scalar_one_or_none()


async def _latest_writing(db: AsyncSession, user_id) -> float | None:
    return (await db.execute(
        select(WritingResult.scaled_score)
        .where(WritingResult.user_id == user_id, WritingResult.scaled_score.is_not(None))
        .order_by(WritingResult.created_at.desc(), WritingResult.id.desc())
        .limit(1)
    )).scalar_one_or_none()


async def _latest_speaking(db: AsyncSession, user_id) -> tuple[float | None, dict]:
    job = (await db.execute(
        select(SpeakingAIJob)
        .join(PracticeAttempt, PracticeAttempt.id == SpeakingAIJob.practice_attempt_id)
        .where(
            SpeakingAIJob.user_id == user_id,
            SpeakingAIJob.status == "done",
            PracticeAttempt.mode == "FULL_PRACTICE",
        )
        .order_by(SpeakingAIJob.created_at.desc(), SpeakingAIJob.id.desc())
        .limit(1)
    )).scalar_one_or_none()
    if job is None:
        return None, {}
    parts = {e.part: (e.score, e.max_score) for e in job.evaluations}
    return speaking_scaled_from_parts(parts), {p: {"score": s, "max": m} for p, (s, m) in parts.items()}


async def build_overall(db: AsyncSession, user_id) -> dict:
    reading = await _latest_reading_like(db, ReadingResult, user_id)
    listening = await _latest_reading_like(db, ListeningResult, user_id)
    writing = await _latest_writing(db, user_id)
    speaking, speaking_parts = await _latest_speaking(db, user_id)

    scores = {"READING": reading, "LISTENING": listening, "WRITING": writing, "SPEAKING": speaking}
    skills = {
        skill: {
            "scaled_score": value,
            "cefr_level": cefr_for_score(value) if value is not None else None,
        }
        for skill, value in scores.items()
    }
    skills["SPEAKING"]["parts"] = speaking_parts

    missing = [s for s in SKILLS if scores[s] is None]
    if missing:
        return {
            "status": "INCOMPLETE",
            "missing": missing,
            "overall_score": None,
            "cefr_level": None,
            "skills": skills,
        }

    average, level = overall_cefr([scores[s] for s in SKILLS])
    return {
        "status": "FINALIZED",
        "missing": [],
        "overall_score": round(average, 2),
        "cefr_level": level,
        "skills": skills,
    }
