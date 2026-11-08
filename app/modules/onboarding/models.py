"""Onboarding entities: foydalanuvchi javoblari va admin boshqaradigan imtihon sanalari."""
import uuid
from datetime import date, datetime
from typing import Optional

from sqlalchemy import JSON, Boolean, Date, DateTime, Float, ForeignKey, Integer, String, Uuid, false, func
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class ExamDate(Base):
    """Admin tomonidan yuklanadigan imtihon sanasi va mygov ro'yxatdan o'tish linki."""

    __tablename__ = "onboarding_exam_dates"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    exam_date: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    register_url: Mapped[str] = mapped_column(String(500), nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )


class OnboardingProfile(Base):
    """Har bir foydalanuvchining onboarding javoblari va so'nggi AI rejasi (bitta qator)."""

    __tablename__ = "onboarding_profiles"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("users.id", ondelete="CASCADE"), unique=True, nullable=False
    )

    # CEFR
    cefr_taken: Mapped[Optional[bool]] = mapped_column(Boolean, nullable=True)
    cefr_level: Mapped[Optional[str]] = mapped_column(String(2), nullable=True)  # topshirgan bo'lsa
    estimated_level: Mapped[Optional[str]] = mapped_column(String(2), nullable=True)  # topshirmagan bo'lsa
    target_level: Mapped[Optional[str]] = mapped_column(String(2), nullable=True)
    vocab_band: Mapped[Optional[str]] = mapped_column(String(16), nullable=True)

    # Kuchli / zaif tomonlar: ["READING", "WRITING", ...]
    strengths: Mapped[Optional[list]] = mapped_column(JSON, nullable=True)
    weaknesses: Mapped[Optional[list]] = mapped_column(JSON, nullable=True)

    # Reja
    exam_date_id: Mapped[Optional[int]] = mapped_column(
        Integer, ForeignKey("onboarding_exam_dates.id", ondelete="SET NULL"), nullable=True
    )
    # Ro'yxatda sana yo'q bo'lganda foydalanuvchi "sana hali e'lon qilinmagan" deb davom etadi
    exam_date_pending: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=false()
    )
    daily_hours: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    teacher_hours: Mapped[Optional[float]] = mapped_column(Float, nullable=True)

    completed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)

    plan: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)
    plan_version: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    plan_generated_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )