"""Persistent (DB-backed) tizim sozlamalari.

Loyihada ilgari faqat `.env` asosidagi `Settings` bor edi (restartda
o'qiladi, admin o'zgartira olmaydi). Admin runtime'da o'zgartiradigan
sozlamalar uchun minimal key/value jadval — manba (source of truth) DB.

KESH YO'Q: har o'qishda DB'ga murojaat qilinadi, shuning uchun admin
o'zgartirgan qiymat darhol (va barcha worker/process'larda) kuchga kiradi.
"""
from __future__ import annotations

import uuid
from datetime import datetime
from typing import Optional

from sqlalchemy import DateTime, ForeignKey, String, Uuid, func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base

# Sozlama kalitlari. DIQQAT: bu `RESULT_SMS_ENABLED` (.env, "natija tayyor"
# SMS) bilan BOG'LIQ EMAS.
SMS_REGISTRATION_ENABLED = "sms_registration_enabled"

# Ma'lum kalitlar uchun default'lar (qator yo'q bo'lsa shu qaytadi).
DEFAULTS: dict[str, str] = {
    SMS_REGISTRATION_ENABLED: "true",
}

_TRUE = {"true", "1", "yes", "on"}


class SystemSetting(Base):
    __tablename__ = "system_settings"

    key: Mapped[str] = mapped_column(String(100), primary_key=True)
    value: Mapped[str] = mapped_column(String(500), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )
    updated_by: Mapped[Optional[uuid.UUID]] = mapped_column(
        Uuid(as_uuid=True, native_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )


class SystemSettingsService:
    @staticmethod
    async def get_bool(db: AsyncSession, key: str, default: Optional[bool] = None) -> bool:
        row = (
            await db.execute(select(SystemSetting.value).where(SystemSetting.key == key))
        ).scalar_one_or_none()
        if row is None:
            if default is not None:
                return default
            return DEFAULTS.get(key, "false").lower() in _TRUE
        return str(row).strip().lower() in _TRUE

    @staticmethod
    async def set_bool(
        db: AsyncSession, key: str, value: bool, updated_by: Optional[uuid.UUID] = None
    ) -> bool:
        raw = "true" if value else "false"
        result = await db.execute(
            update(SystemSetting)
            .where(SystemSetting.key == key)
            .values(value=raw, updated_by=updated_by)
        )
        if result.rowcount == 0:
            try:
                async with db.begin_nested():
                    db.add(SystemSetting(key=key, value=raw, updated_by=updated_by))
                    await db.flush()
            except IntegrityError:
                # Parallel so'rov qatorni yaratib ulgurdi — yangilaymiz.
                await db.execute(
                    update(SystemSetting)
                    .where(SystemSetting.key == key)
                    .values(value=raw, updated_by=updated_by)
                )
        return value

    @classmethod
    async def sms_registration_enabled(cls, db: AsyncSession) -> bool:
        return await cls.get_bool(db, SMS_REGISTRATION_ENABLED, default=True)
