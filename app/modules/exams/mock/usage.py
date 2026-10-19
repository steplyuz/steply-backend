"""Test mock-imtihon ichida ishlatilganini tekshirish (o'chirishdan oldin).

MockExam jadvalidagi FK (listening_id, reading_id, writing_id) ondelete qoidasiz,
shuning uchun bunday testni o'chirish DB IntegrityError (500) bilan tugardi.
Bu yerda aniq 409 xabari qaytariladi.
"""
from fastapi import HTTPException
from sqlalchemy import exists, select
from sqlalchemy.ext.asyncio import AsyncSession

from .models import MockExam


async def ensure_not_in_mock_exam(db: AsyncSession, skill: str, test_id: str) -> None:
    column = {
        "listening": MockExam.listening_id,
        "reading": MockExam.reading_id,
        "writing": MockExam.writing_id,
        "speaking": MockExam.speaking_id,
    }[skill]
    used = (await db.execute(select(exists().where(column == test_id)))).scalar()
    if used:
        raise HTTPException(
            409,
            f"Bu {skill.capitalize()} testi mock imtihonda ishlatilgan, o'chirib bo'lmaydi. "
            "Avval mock imtihondan olib tashlang.",
        )


async def ensure_no_results(db: AsyncSession, result_model, column, test_id: str) -> None:
    """Natijasi (topshirilgan urinishi) bor testni o'chirishni bloklaydi."""
    from sqlalchemy import func

    count = (await db.execute(select(func.count()).select_from(result_model).where(column == test_id))).scalar_one()
    if count:
        raise HTTPException(
            409,
            "Bu testda topshirilgan natijalar bor, o'chirib bo'lmaydi. "
            "Uni faolsizlantiring (is_active=false).",
        )


async def purge_exam_versions(db: AsyncSession, module, exam_id: str) -> None:
    """Test o'chirilganda uning lock/draft versiyalari (answer key snapshotlari) ham o'chadi."""
    from sqlalchemy import delete

    from app.modules.exams.paper.models import ExamVersion

    await db.execute(delete(ExamVersion).where(ExamVersion.module == module, ExamVersion.exam_id == exam_id))