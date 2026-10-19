"""Sessiyadagi bo'sh joylarni atomik band qilish.

Nega COUNT emas: COUNT(...) keyin INSERT — ikki parallel so'rov bir vaqtda
"joy bor" deb ko'rib, sig'imdan oshib ketardi (ayniqsa SQLite'da, bu yerda
SELECT ... FOR UPDATE ishlamaydi).

Yechim: `exam_sessions.seats_taken` hisoblagichini shartli UPDATE bilan oshiramiz.
UPDATE bitta atomik operatsiya: sig'im to'lgan bo'lsa 0 qator o'zgaradi. Bu
Postgres va SQLite'da bir xil ishlaydi. Oshirish ro'yxatdan o'tish bilan BIR
tranzaksiyada — insert xato bo'lsa, oshirish ham qaytariladi.
"""
from fastapi import HTTPException
from sqlalchemy import or_, update
from sqlalchemy.ext.asyncio import AsyncSession

from .models import ExamSession


async def reserve_seat(db: AsyncSession, session_id: int) -> None:
    result = await db.execute(
        update(ExamSession)
        .where(
            ExamSession.id == session_id,
            or_(ExamSession.capacity.is_(None), ExamSession.seats_taken < ExamSession.capacity),
        )
        .values(seats_taken=ExamSession.seats_taken + 1)
        .execution_options(synchronize_session=False)
    )
    if result.rowcount != 1:
        raise HTTPException(400, "Bu sanada bo'sh joy qolmagan")


async def release_seat(db: AsyncSession, session_id: int) -> None:
    """Ro'yxatdan o'tish bekor qilinganda chaqiriladi (hozircha kodda bekor qilish yo'li yo'q)."""
    await db.execute(
        update(ExamSession)
        .where(ExamSession.id == session_id, ExamSession.seats_taken > 0)
        .values(seats_taken=ExamSession.seats_taken - 1)
        .execution_options(synchronize_session=False)
    )
