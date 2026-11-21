import uuid
from datetime import datetime
from typing import Optional

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, Uuid, func
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class ReferralCode(Base):
    """Har bir userning noyob referal kodi (lazy yaratiladi)."""

    __tablename__ = "referral_codes"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True, native_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), unique=True, nullable=False
    )
    code: Mapped[str] = mapped_column(String(16), unique=True, index=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)


class Referral(Base):
    """Taklif qilgan (referrer) va taklif qilingan (referred) user bog'lanishi.

    status: pending -> qualified (onboarding tugagan, mukofot berilgan)
            capped  (referrer limitiga yetgan, mukofot berilmagan)
    """

    __tablename__ = "referrals"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    referrer_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True, native_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), index=True, nullable=False
    )
    referred_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True, native_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), unique=True, nullable=False
    )
    status: Mapped[str] = mapped_column(String(16), default="pending", index=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    qualified_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)


class ReferralSettings(Base):
    """Admin boshqaradigan sozlamalar (yagona qator, id=1)."""

    __tablename__ = "referral_settings"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    enabled: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    # Har bir qualified do'st uchun bonus test kreditlari (0 = yo'q)
    reward_credits: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    # Kreditlar necha kundan keyin tugaydi (None = tugamaydi)
    credit_expiry_days: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    # Tarif mukofoti: qaysi plan va necha kun (0 = yo'q)
    reward_plan_id: Mapped[Optional[int]] = mapped_column(
        Integer, ForeignKey("subscription_plans.id", ondelete="SET NULL"), nullable=True
    )
    reward_tier_days: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    # Bitta referrer necha marta mukofot olishi mumkin (None = cheksiz)
    max_rewards_per_user: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )


class ReferralCredit(Base):
    """Bonus test kreditlari ledger'i. Har bir kredit bitta qualified referral uchun beriladi."""

    __tablename__ = "referral_credits"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True, native_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), index=True, nullable=False
    )
    # None = admin qo'lda bergan kredit (referal bilan bog'liq emas)
    referral_id: Mapped[Optional[int]] = mapped_column(
        Integer, ForeignKey("referrals.id", ondelete="CASCADE"), unique=True, nullable=True
    )
    amount_total: Mapped[int] = mapped_column(Integer, nullable=False)
    amount_remaining: Mapped[int] = mapped_column(Integer, nullable=False)
    expires_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), index=True, nullable=True)
    reason: Mapped[Optional[str]] = mapped_column(String(200), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
