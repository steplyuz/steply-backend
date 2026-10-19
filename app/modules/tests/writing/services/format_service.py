"""
WritingFormat uchun admin CRUD servisi.

Format — Writing topshirig'ining talablarini belgilaydi: CEFR darajasi, so'z chegarasi,
uslub, baholash rejimi va mezonlar og'irligi. Writing topshirig'i yaratishdan oldin
format_id shu servis orqali tayyorlanadi.
"""

import logging
from typing import List

from fastapi import HTTPException
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from ..models import WritingFormat, WritingCriterionConfig, WritingTask
from ..schemas import WritingFormatCreate

logger = logging.getLogger(__name__)


def _validate_format_payload(data: WritingFormatCreate) -> None:
    """Schema'da tekshirilmagan biznes qoidalari.

    - min_words max_words dan katta bo'la olmaydi;
    - bir mezon ikki marta berilmaydi (DB'da uq_format_criterion bor, aks holda 500 chiqardi).
    """
    if data.min_words > data.max_words:
        raise HTTPException(422, "min_words max_words dan katta bo'lishi mumkin emas")
    criteria = [item.criterion for item in data.criterion_weights]
    if len(criteria) != len(set(criteria)):
        raise HTTPException(422, "Bir xil mezon ikki marta ko'rsatilgan")


class WritingFormatService:
    def __init__(self, db: AsyncSession):
        self.db = db

    # ==========================================================
    # CREATE
    # ==========================================================
    async def create_format(self, data: WritingFormatCreate) -> WritingFormat:
        _validate_format_payload(data)

        if await self._name_exists(data.name):
            raise HTTPException(400, f"Bu nomdagi format allaqachon mavjud: {data.name}")

        fmt = WritingFormat(
            name=data.name,
            cefr_level=data.cefr_level,
            min_words=data.min_words,
            max_words=data.max_words,
            style=data.style,
            scoring_mode=data.scoring_mode,
            penalty_enabled=data.penalty_enabled,
        )
        try:
            self.db.add(fmt)
            await self.db.flush()

            self.db.add_all([
                WritingCriterionConfig(
                    format_id=fmt.id,
                    criterion=item.criterion,
                    weight=item.weight,
                )
                for item in data.criterion_weights
            ])
            await self.db.flush()
            await self.db.commit()
        except IntegrityError as exc:
            # Parallel so'rov bir xil nomni oldinroq yozib qo'ygan bo'lishi mumkin.
            await self.db.rollback()
            logger.warning("Writing format create conflict name=%s", data.name)
            raise HTTPException(400, "Format yaratib bo'lmadi: nom yoki mezon takrorlangan") from exc

        return await self.get_format_by_id(fmt.id)

    # ==========================================================
    # READ
    # ==========================================================
    async def get_format_by_id(self, format_id: int) -> WritingFormat:
        stmt = (
            select(WritingFormat)
            .where(WritingFormat.id == format_id)
            .options(selectinload(WritingFormat.criterion_configs))
        )
        fmt = (await self.db.execute(stmt)).scalar_one_or_none()
        if not fmt:
            raise HTTPException(404, "Format topilmadi")
        return fmt

    async def list_formats(self) -> List[WritingFormat]:
        stmt = select(WritingFormat).options(selectinload(WritingFormat.criterion_configs))
        result = await self.db.execute(stmt)
        return list(result.scalars().unique().all())

    # ==========================================================
    # DELETE
    # ==========================================================
    async def delete_format(self, format_id: int) -> dict[str, str]:
        fmt = await self.get_format_by_id(format_id)

        used = await self.db.scalar(
            select(func.count()).select_from(WritingTask).where(WritingTask.format_id == format_id)
        )
        if used:
            raise HTTPException(
                400,
                "Bu format allaqachon test topshirig'ida ishlatilgan, o'chirib bo'lmaydi",
            )

        try:
            await self.db.delete(fmt)
            await self.db.commit()
        except IntegrityError as exc:
            await self.db.rollback()
            raise HTTPException(400, "Format o'chirilmadi: unga bog'liq yozuvlar mavjud") from exc

        return {"status": "deleted"}

    # ==========================================================
    # HELPERS
    # ==========================================================
    async def _name_exists(self, name: str) -> bool:
        found = await self.db.scalar(
            select(WritingFormat.id).where(WritingFormat.name == name).limit(1)
        )
        return found is not None