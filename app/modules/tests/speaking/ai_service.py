"""Speaking AI: kredit, job va fon rejimida baholash."""
import asyncio
import logging
import os
from collections import defaultdict

from fastapi import HTTPException
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import AsyncSessionLocal
from app.core.file_storage import private_path
from app.modules.auth.models import User
from app.modules.billing.entitlements import enforce_ai_quota, plan_allows_skill, release_speaking_usage
from app.modules.billing.models import AIUsage
from app.modules.exams.mock.models import PracticeAttempt
from app.modules.tests.speaking import ai_grader, transcription
from app.modules.tests.speaking.ai_models import SpeakingAIEvaluation, SpeakingAIJob
from app.modules.tests.speaking.models import SpeakingSubmission, SpeakingTest

logger = logging.getLogger(__name__)


# ---------- job ----------

async def start_job(db: AsyncSession, user_id, attempt_id: int) -> tuple[SpeakingAIJob, bool]:
    attempt = await db.get(PracticeAttempt, attempt_id)
    if attempt is None or attempt.user_id != user_id:
        raise HTTPException(404, "Urinish topilmadi")
    if not await plan_allows_skill(db, user_id, "SPEAKING"):
        raise HTTPException(403, "Speaking tarifingizda mavjud emas")

    existing = (await db.execute(
        select(SpeakingAIJob).where(SpeakingAIJob.practice_attempt_id == attempt_id)
    )).scalar_one_or_none()
    if existing and existing.status != "failed":
        return existing, False

    # Foydalanuvchi qatorini qulflaymiz: parallel so'rovlar kvotani ikki marta sarflamasin.
    await db.execute(update(User).where(User.id == user_id).values(updated_at=User.updated_at))
    # Umumiy AI kredit pool'idan 1 birlik: oylik obuna limiti, so'ng sotib olingan kreditlar.
    purchased = await enforce_ai_quota(db, user_id, 1, feature="Speaking")
    if existing:
        # Xato bilan tugagan job: birlik qaytarilgan edi, qayta urinish yangi birlik yechadi.
        job = existing
        job.status, job.error = "queued", None
    else:
        job = SpeakingAIJob(practice_attempt_id=attempt_id, user_id=user_id, status="queued")
        db.add(job)
    await db.flush()
    db.add(AIUsage(
        user_id=user_id, feature="speaking", job_id=job.id,
        billing_source="purchased" if purchased else "subscription", purchased_units=purchased,
    ))
    await db.commit()
    await db.refresh(job)
    return job, True


async def get_job(db: AsyncSession, user_id, attempt_id: int) -> SpeakingAIJob:
    from sqlalchemy.orm import selectinload
    job = (await db.execute(
        select(SpeakingAIJob).options(selectinload(SpeakingAIJob.evaluations)).where(
            SpeakingAIJob.practice_attempt_id == attempt_id, SpeakingAIJob.user_id == user_id
        )
    )).scalar_one_or_none()
    if job is None:
        raise HTTPException(404, "Baholash topilmadi")
    return job


# ---------- baholash ----------

def _part_codes(test: SpeakingTest) -> dict[str, str]:
    mapping = {}
    for code, part in (("1.1", test.part_1_1), ("1.2", test.part_1_2), ("2", test.part_2), ("3", test.part_3)):
        if part is not None and part.external_id:
            mapping[part.external_id] = code
    return mapping


def _part3_context(test: SpeakingTest) -> str | None:
    part = test.part_3
    if part is None:
        return None
    return (f"Topic: {part.topic}\n"
            f"FOR points: {'; '.join(part.for_points or [])}\n"
            f"AGAINST points: {'; '.join(part.against_points or [])}")


def _prompt_map(test: SpeakingTest) -> dict[str, str]:
    """question_external_id -> savol matni (X_id / X ustun juftligi bo'yicha)."""
    out: dict[str, str] = {}
    for part in (test.part_1_1, test.part_1_2, test.part_2, test.part_3):
        if part is None:
            continue
        cols = set(part.__table__.columns.keys())
        for col in cols:
            if col.endswith("_id") and col[:-3] in cols:
                qid, text = getattr(part, col), getattr(part, col[:-3])
                if qid and text:
                    out[qid] = text
    return out


def _describe_error(exc: Exception) -> str:
    """Xato sababini tushunarli qiladi: OpenAI status va provayder xabari."""
    status = getattr(exc, "status_code", None)
    message = getattr(exc, "message", None)
    if status is not None and message:
        return f"OpenAI {status}: {message}"
    return f"{type(exc).__name__}: {exc}"


async def _evaluate(db: AsyncSession, job: SpeakingAIJob) -> None:
    subs = (await db.execute(
        select(SpeakingSubmission)
        .where(SpeakingSubmission.practice_attempt_id == job.practice_attempt_id)
        .order_by(SpeakingSubmission.id)
    )).scalars().all()
    if not subs:
        raise RuntimeError("Audio yozuvlar topilmadi")

    from sqlalchemy.orm import selectinload
    test = (await db.execute(
        select(SpeakingTest)
        .options(selectinload(SpeakingTest.part_1_1), selectinload(SpeakingTest.part_1_2),
                 selectinload(SpeakingTest.part_2), selectinload(SpeakingTest.part_3))
        .where(SpeakingTest.id == subs[0].speaking_test_id)
    )).scalar_one()
    codes = _part_codes(test)
    prompts = _prompt_map(test)
    audio_dir = str(private_path("speaking", "submissions"))

    sem = asyncio.Semaphore(4)

    async def _tr(sub: SpeakingSubmission) -> dict:
        async with sem:
            return await transcription.transcribe(os.path.join(audio_dir, sub.audio_storage_key))

    transcripts = await asyncio.gather(*(_tr(s) for s in subs))

    grouped: dict[str, list[dict]] = defaultdict(list)
    for sub, tr in zip(subs, transcripts):
        code = codes.get(sub.part_external_id)
        if code is None:
            logger.warning("job %s: noma'lum part %s", job.id, sub.part_external_id)
            continue
        grouped[code].append({
            "n": len(grouped[code]) + 1,
            "prompt": prompts.get(sub.question_external_id, ""),
            "transcript": tr["text"],
            "metrics": transcription.speech_metrics(tr["words"], tr.get("duration")),
        })

    # Eski natijalar: relyatsiyani lazy o'qimaymiz (async sessiyada MissingGreenlet beradi).
    old_rows = (await db.execute(
        select(SpeakingAIEvaluation).where(SpeakingAIEvaluation.job_id == job.id)
    )).scalars().all()
    for old in old_rows:
        await db.delete(old)
    await db.flush()

    if not grouped:
        raise RuntimeError("Mos qism topilmadi: test qismlari yuborilgan audio bilan bog'lanmagan")

    for code in ("1.1", "1.2", "2", "3"):
        if code not in grouped:
            continue
        topic_context = _part3_context(test) if code == "3" else None
        res = await ai_grader.grade_part(code, grouped[code], topic_context=topic_context)
        db.add(SpeakingAIEvaluation(
            job_id=job.id, part=code, score=res["score"], max_score=res["max"], level=res["level"],
            payload={
                "questions": res["questions"],
                "sides": res["sides"],
                "feedback_uz": res["feedback_uz"],
                "allowed_range": res["allowed_range"],
                "raw_model_score": res["raw_model_score"],
                "clamped": res["clamped"],
                "metrics": [a.get("metrics") for a in grouped[code]],
                "pronunciation": "not_assessed",
            },
        ))


async def run_job(job_id: int) -> None:
    """BackgroundTasks chaqiradi. Request sessiyasi ishlatilmaydi."""
    async with AsyncSessionLocal() as db:
        job = await db.get(SpeakingAIJob, job_id)
        if job is None or job.status != "queued":
            return
        job.status = "running"
        await db.commit()
        try:
            await _evaluate(db, job)
            job.status = "done"
        except Exception as exc:
            logger.exception("speaking AI job %s xato", job_id)
            await db.rollback()
            job = await db.get(SpeakingAIJob, job_id)
            job.status = "failed"
            logger.exception("speaking AI job %s xato", job_id)
            job.error = _describe_error(exc)[:500]
            # Xato bo'lgan baholash kvotani sarflamaydi: birlik qaytariladi.
            await release_speaking_usage(db, job.id)
        await db.commit()
