"""Test o'chirish va tahrirlashdan oldingi bog'liqlik tekshiruvlari (yagona manba).

Reading, Listening, Writing va Speaking uchun bir xil qoidalar:

  O'CHIRISH (delete):
    * mock imtihonda ishlatilgan test            -> HAR DOIM 409 (force yordam bermaydi)
    * skanerlangan OMR javoblari bor versiya     -> HAR DOIM 409 (haqiqiy imtihon ma'lumoti)
    * topshirilgan natija / practice urinish     -> 409; faqat force=True (SUPER_ADMIN) bilan o'chadi

  TAHRIRLASH (lock'dan keyin yangi DRAFT versiya ochish):
    * OMR javoblari bor                          -> 409 (qog'oz javoblari shu versiyaga bog'liq)
    * hozir ishlanayotgan (IN_PROGRESS) urinish  -> 409 (ball o'rtada o'zgarib ketmasligi uchun)
    * faqat yakunlangan natijalar bor            -> RUXSAT (eski natijalar saqlanadi, tarix qoladi)

Asosiy 500 xato sababi: ExamVersion'ga OMRSubmission (ondelete=RESTRICT) bog'langan bo'lsa,
versiyani to'g'ridan-to'g'ri DELETE qilish IntegrityError beradi. Endi bunday holat aniq 409 bo'ladi.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from fastapi import HTTPException
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.exams.paper.models import ExamVersion, OMRSubmission, PaperModule

from .models import (
    AttemptAnswer,
    MockExam,
    PracticeAttempt,
    PracticeAttemptStatus,
    SkillType,
)

_MOCK_COLUMN = {
    "listening": MockExam.listening_id,
    "reading": MockExam.reading_id,
    "writing": MockExam.writing_id,
    "speaking": MockExam.speaking_id,
}


@dataclass
class TestUsage:
    mock_exams: int = 0
    omr_submissions: int = 0
    results: int = 0
    attempts_total: int = 0
    attempts_in_progress: int = 0

    @property
    def has_history(self) -> bool:
        """Topshirilgan natija yoki boshlangan urinish bormi (o'chirishni cheklaydi)."""
        return bool(self.results or self.attempts_total)


async def _count(db: AsyncSession, stmt) -> int:
    return int((await db.execute(stmt)).scalar_one() or 0)


def _omr_count_stmt(module: PaperModule, test_id: str):
    return (
        select(func.count(OMRSubmission.id))
        .join(ExamVersion, OMRSubmission.exam_version_id == ExamVersion.id)
        .where(ExamVersion.module == module, ExamVersion.exam_id == test_id)
    )


async def get_test_usage(
    db: AsyncSession,
    *,
    skill: str,
    module: PaperModule,
    test_id: str,
    result_model: Any = None,
    result_col: Any = None,
) -> TestUsage:
    skill_enum = SkillType[skill.upper()]
    usage = TestUsage()
    usage.mock_exams = await _count(
        db, select(func.count()).select_from(MockExam).where(_MOCK_COLUMN[skill] == test_id)
    )
    usage.omr_submissions = await _count(db, _omr_count_stmt(module, test_id))
    if result_model is not None:
        usage.results = await _count(
            db, select(func.count()).select_from(result_model).where(result_col == test_id)
        )
    attempts = select(func.count(PracticeAttempt.id)).where(
        PracticeAttempt.skill == skill_enum, PracticeAttempt.test_id == test_id
    )
    usage.attempts_total = await _count(db, attempts)
    usage.attempts_in_progress = await _count(
        db, attempts.where(PracticeAttempt.status == PracticeAttemptStatus.IN_PROGRESS)
    )
    return usage


async def ensure_deletable(
    db: AsyncSession,
    *,
    skill: str,
    module: PaperModule,
    test_id: str,
    result_model: Any = None,
    result_col: Any = None,
    force: bool = False,
) -> TestUsage:
    usage = await get_test_usage(
        db, skill=skill, module=module, test_id=test_id, result_model=result_model, result_col=result_col
    )
    label = skill.capitalize()
    if usage.mock_exams:
        raise HTTPException(
            409,
            f"Bu {label} testi mock imtihonda ishlatilgan, o'chirib bo'lmaydi. "
            "Avval mock imtihondan olib tashlang.",
        )
    if usage.omr_submissions:
        raise HTTPException(
            409,
            "Bu test versiyasi bo'yicha skanerlangan OMR javoblari bor, o'chirib bo'lmaydi.",
        )
    if usage.has_history and not force:
        raise HTTPException(
            409,
            "Bu testda topshirilgan natijalar yoki urinishlar bor. Uni faolsizlantiring "
            "(is_active=false), yoki super admin bo'lsangiz force=true bilan majburiy o'chiring.",
        )
    return usage


async def ensure_editable(
    db: AsyncSession,
    *,
    skill: str,
    module: PaperModule,
    test_id: str,
    result_model: Any = None,
    result_col: Any = None,
) -> TestUsage:
    """Lock qilingan testni tahrirlashga ruxsat: OMR va ishlanayotgan urinishlar bo'lmasligi kerak."""
    usage = await get_test_usage(
        db, skill=skill, module=module, test_id=test_id, result_model=result_model, result_col=result_col
    )
    if usage.omr_submissions:
        raise HTTPException(
            409,
            "Skanerlangan OMR javoblari bor, bu versiyani tahrirlab bo'lmaydi. Test nusxasini yarating.",
        )
    if usage.attempts_in_progress:
        raise HTTPException(
            409,
            f"Hozir {usage.attempts_in_progress} ta urinish ishlanmoqda. Ular tugagach tahrirlang.",
        )
    return usage


# ---------------------------------------------------------------------------
# Eski API (writing/speaking va boshqa joylar uchun saqlangan)
# ---------------------------------------------------------------------------
async def ensure_not_in_mock_exam(db: AsyncSession, skill: str, test_id: str) -> None:
    used = await _count(db, select(func.count()).select_from(MockExam).where(_MOCK_COLUMN[skill] == test_id))
    if used:
        raise HTTPException(
            409,
            f"Bu {skill.capitalize()} testi mock imtihonda ishlatilgan, o'chirib bo'lmaydi. "
            "Avval mock imtihondan olib tashlang.",
        )


async def ensure_no_results(db: AsyncSession, result_model, column, test_id: str) -> None:
    """Natijasi (topshirilgan urinishi) bor testni o'chirishni bloklaydi."""
    count = await _count(db, select(func.count()).select_from(result_model).where(column == test_id))
    if count:
        raise HTTPException(
            409,
            "Bu testda topshirilgan natijalar bor, o'chirib bo'lmaydi. "
            "Uni faolsizlantiring (is_active=false).",
        )


# ---------------------------------------------------------------------------
# PURGE — o'chirish zanjiri (tartib muhim: avval bolalar, keyin test)
# ---------------------------------------------------------------------------
async def purge_results(db: AsyncSession, result_model, result_col, test_id: str) -> None:
    """Faqat force=True yoki natija yo'q bo'lganda chaqiriladi."""
    await db.execute(delete(result_model).where(result_col == test_id))


async def purge_practice_attempts(db: AsyncSession, skill: str, test_id: str) -> None:
    skill_enum = SkillType[skill.upper()]
    attempt_ids = select(PracticeAttempt.id).where(
        PracticeAttempt.skill == skill_enum, PracticeAttempt.test_id == test_id
    )
    await db.execute(delete(AttemptAnswer).where(AttemptAnswer.practice_attempt_id.in_(attempt_ids)))
    await db.execute(
        delete(PracticeAttempt).where(PracticeAttempt.skill == skill_enum, PracticeAttempt.test_id == test_id)
    )


async def purge_exam_versions(db: AsyncSession, module, exam_id: str) -> None:
    """Lock/draft versiyalarni o'chiradi. OMR bog'liq bo'lsa IntegrityError o'rniga aniq 409."""
    if await _count(db, _omr_count_stmt(module, exam_id)):
        raise HTTPException(409, "Skanerlangan OMR javoblari bor, versiyani o'chirib bo'lmaydi.")
    await db.execute(delete(ExamVersion).where(ExamVersion.module == module, ExamVersion.exam_id == exam_id))
