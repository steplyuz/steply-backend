"""Billing entities. Payments are manually verified from uploaded receipts in MVP."""
from datetime import date, datetime
from enum import Enum
from typing import Optional
import uuid
from sqlalchemy import Date, Uuid, Boolean, DateTime, Enum as SAEnum, ForeignKey, Integer, String, Text, UniqueConstraint, Index, CheckConstraint, func, JSON
from sqlalchemy.orm import Mapped, mapped_column, relationship
from app.core.database import Base

class BillingOrderStatus(str, Enum):
    PENDING_PAYMENT = "pending_payment"
    UNDER_REVIEW = "under_review"
    PAID = "paid"
    REJECTED = "rejected"
    CANCELLED = "cancelled"

class BillingProductType(str, Enum):
    SUBSCRIPTION = "subscription"
    MOCK_EXAM = "mock_exam"
    SPEAKING_PRACTICE = "speaking_practice"
    FULL_SKILLS = "full_skills"  # legacy
    FULL_EXAM = "full_exam"
    WRITING_AI_CREDITS = "writing_ai_credits"
    TEACHER_REVIEW_CREDITS = "teacher_review_credits"


class SubscriptionTier(str, Enum):
    """Free/Plus/Pro — platformaning uchta foydalanish darajasi.

    PLUS: Reading, Listening, Writing; Writing'ni AI (OpenAI) tekshiradi.
    PRO: barcha 4 ko'nikma (Speaking PLUS'da ham bor); Writing/Speaking'ni teacher tekshiradi, AI yo'q.

    FREE hech qanday `UserSubscription` talab qilmaydi — bu faol
    (is_active + start_date <= now < end_date) obunasi bo'lmagan HAR BIR foydalanuvchi
    uchun standart holat (qarang: entitlements.get_user_tier).
    """
    FREE = "free"
    PLUS = "plus"
    # Backward-compatible product label used by the previous billing API.
    PREMIUM = "plus"
    PRO = "pro"


class SubscriptionPlan(Base):
    __tablename__ = "subscription_plans"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(100), unique=True, nullable=False)
    description: Mapped[Optional[str]] = mapped_column(Text)
    price: Mapped[int] = mapped_column(Integer, nullable=False)
    duration_days: Mapped[int] = mapped_column(Integer, nullable=False)
    # Ushbu tarif sotib olinganda foydalanuvchi qaysi darajaga o'tishi.
    tier: Mapped[SubscriptionTier] = mapped_column(
        SAEnum(SubscriptionTier, length=7), default=SubscriptionTier.PLUS, nullable=False
    )
    # PLUS tarifda: bir kalendar oyda AI tekshiriladigan Writing javoblari (topshiriqlar) soni. 0 = AI yo'q.
    ai_limit: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    exam_limit: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    # Tarifga xos imkoniyatlar; FREE/PLUS/PRO faqat legacy/default daraja sifatida qoladi.
    included_skills: Mapped[list] = mapped_column(JSON, default=lambda: ["READING", "LISTENING", "WRITING", "SPEAKING"], nullable=False)
    ai_enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    discount_percent: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)


class TierPracticeLimit(Base):
    """Barcha practice skilllari uchun bitta tarif kvotasi."""
    __tablename__ = "tier_practice_limits"
    id: Mapped[int] = mapped_column(primary_key=True)
    tier: Mapped[SubscriptionTier] = mapped_column(SAEnum(SubscriptionTier, length=7), unique=True, nullable=False)
    weekly_limit: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    monthly_limit: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)


class TierAnalysisLimit(Base):
    """Practice kvotasidan mustaqil tahlil kvotasi."""
    __tablename__ = "tier_analysis_limits"
    id: Mapped[int] = mapped_column(primary_key=True)
    tier: Mapped[SubscriptionTier] = mapped_column(SAEnum(SubscriptionTier, length=7), unique=True, nullable=False)
    weekly_limit: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    monthly_limit: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)


class GlobalPracticeUsageLog(Base):
    """Practice uchun umumiy kvota: skilldan qat'i nazar har bir urinish bitta birlik."""
    __tablename__ = "global_practice_usage_log"
    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True, native_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), index=True, nullable=False)
    skill: Mapped[str] = mapped_column(String(20), nullable=False)
    test_id: Mapped[str] = mapped_column(String(64), nullable=False)
    attempt_id: Mapped[Optional[int]] = mapped_column(ForeignKey("practice_attempts.id", ondelete="SET NULL"), nullable=True, unique=True)
    week_start: Mapped[date] = mapped_column(Date, nullable=False)
    consumed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    __table_args__ = (Index("ix_global_practice_user_week", "user_id", "week_start"), Index("ix_global_practice_user_consumed", "user_id", "consumed_at"))


class AnalysisUsageLog(Base):
    """Tahlil uchun mustaqil kvota sarfi."""
    __tablename__ = "analysis_usage_log"
    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True, native_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), index=True, nullable=False)
    skill: Mapped[str] = mapped_column(String(20), nullable=False)
    test_id: Mapped[str] = mapped_column(String(64), nullable=False)
    week_start: Mapped[date] = mapped_column(Date, nullable=False)
    consumed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    __table_args__ = (Index("ix_analysis_usage_user_week", "user_id", "week_start"),)


class AnalysisVideo(Base):
    """Butun testga biriktiriladigan tahlil videosi."""
    __tablename__ = "analysis_videos"
    id: Mapped[int] = mapped_column(primary_key=True)
    skill: Mapped[str] = mapped_column(String(20), nullable=False)
    test_id: Mapped[str] = mapped_column(String(64), nullable=False)
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    video_path: Mapped[str] = mapped_column(String(500), nullable=False)
    created_by: Mapped[Optional[uuid.UUID]] = mapped_column(Uuid(as_uuid=True, native_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    __table_args__ = (UniqueConstraint("skill", "test_id", name="uq_analysis_video_skill_test"),)


class BillingProduct(Base):
    __tablename__ = "billing_products"
    id: Mapped[int] = mapped_column(primary_key=True)
    product_type: Mapped[BillingProductType] = mapped_column(SAEnum(BillingProductType), nullable=False, index=True)
    # Stable admin/frontend catalog key, e.g. FULL_EXAM_B2, PRO_MONTHLY.
    code: Mapped[Optional[str]] = mapped_column(String(100), unique=True, index=True)
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[Optional[str]] = mapped_column(Text)
    price: Mapped[int] = mapped_column(Integer, nullable=False)
    external_ref: Mapped[Optional[str]] = mapped_column(String(100), index=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)

class UserSubscription(Base):
    __tablename__ = "user_subscriptions"
    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True, native_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), index=True, nullable=False)
    plan_id: Mapped[int] = mapped_column(ForeignKey("subscription_plans.id"), nullable=False)
    start_date: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    end_date: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    plan: Mapped["SubscriptionPlan"] = relationship()



class WritingAICreditGrant(Base):
    """Purchased Writing AI credits granted once per approved billing order."""
    __tablename__ = "writing_ai_credit_grants"
    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True, native_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), index=True, nullable=False)
    order_id: Mapped[str] = mapped_column(ForeignKey("billing_orders.id", ondelete="CASCADE"), unique=True, nullable=False)
    credits_total: Mapped[int] = mapped_column(Integer, nullable=False)
    credits_remaining: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    __table_args__ = (
        Index("ix_writing_ai_credits_user_remaining", "user_id", "credits_remaining"),
        CheckConstraint("credits_total > 0", name="ck_writing_ai_credits_total_positive"),
        CheckConstraint("credits_remaining >= 0 AND credits_remaining <= credits_total", name="ck_writing_ai_credits_remaining_range"),
    )


class TeacherReviewCreditGrant(Base):
    """Purchased teacher-review credits granted once per approved billing order."""
    __tablename__ = "teacher_review_credit_grants"
    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True, native_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), index=True, nullable=False)
    order_id: Mapped[str] = mapped_column(ForeignKey("billing_orders.id", ondelete="CASCADE"), unique=True, nullable=False)
    credits_total: Mapped[int] = mapped_column(Integer, nullable=False)
    credits_remaining: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    __table_args__ = (
        Index("ix_teacher_review_credits_user_remaining", "user_id", "credits_remaining"),
        CheckConstraint("credits_total > 0", name="ck_teacher_review_credits_total_positive"),
        CheckConstraint("credits_remaining >= 0 AND credits_remaining <= credits_total", name="ck_teacher_review_credits_remaining_range"),
    )


class WritingAIUsage(Base):
    """Per-answer idempotent ledger identifying subscription vs purchased credit usage."""
    __tablename__ = "writing_ai_usage"
    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True, native_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), index=True, nullable=False)
    answer_id: Mapped[int] = mapped_column(ForeignKey("writing_answers.id", ondelete="CASCADE"), unique=True, nullable=False)
    billing_source: Mapped[str] = mapped_column(String(20), nullable=False)  # subscription | purchased
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    __table_args__ = (
        Index("ix_writing_ai_usage_user_source_created", "user_id", "billing_source", "created_at"),
        CheckConstraint("billing_source IN ('subscription', 'purchased')", name="ck_writing_ai_usage_source"),
    )


class BillingOrder(Base):
    __tablename__ = "billing_orders"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    user_id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True, native_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), index=True, nullable=False)
    product_type: Mapped[BillingProductType] = mapped_column(SAEnum(BillingProductType), nullable=False)
    product_id: Mapped[Optional[str]] = mapped_column(String(100))
    product_title: Mapped[str] = mapped_column(String(200), nullable=False)
    amount: Mapped[int] = mapped_column(Integer, nullable=False)
    original_amount: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    discount_amount: Mapped[int] = mapped_column(Integer, default=0, server_default="0", nullable=False)
    promo_code_id: Mapped[Optional[int]] = mapped_column(ForeignKey("promo_codes.id", ondelete="SET NULL"), nullable=True, index=True)
    # Tashkilot imtihoni uchun to'lov bo'lsa — to'lov shu tashkilot hisobiga tegishli.
    status: Mapped[BillingOrderStatus] = mapped_column(SAEnum(BillingOrderStatus), default=BillingOrderStatus.PENDING_PAYMENT, index=True, nullable=False)
    receipt_path: Mapped[Optional[str]] = mapped_column(String(500))
    receipt_storage_key: Mapped[Optional[str]] = mapped_column(String(255), nullable=True, index=True)
    user_note: Mapped[Optional[str]] = mapped_column(String(1000))
    admin_note: Mapped[Optional[str]] = mapped_column(String(1000))
    reviewed_by: Mapped[Optional[uuid.UUID]] = mapped_column(Uuid(as_uuid=True, native_uuid=True), ForeignKey("users.id", ondelete="SET NULL"))
    reviewed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)

class BalanceTransaction(Base):
    __tablename__ = "balance_transactions"
    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True, native_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), index=True, nullable=False)
    amount: Mapped[int] = mapped_column(Integer, nullable=False)
    reason: Mapped[str] = mapped_column(String(100), nullable=False)
    reference_id: Mapped[Optional[str]] = mapped_column(String(100))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)

class PromoCode(Base):
    __tablename__ = "promo_codes"
    id: Mapped[int] = mapped_column(primary_key=True)
    code: Mapped[str] = mapped_column(String(50), unique=True, index=True, nullable=False)
    discount_percent: Mapped[Optional[int]] = mapped_column(Integer)
    discount_amount: Mapped[Optional[int]] = mapped_column(Integer)
    max_usage: Mapped[Optional[int]] = mapped_column(Integer)
    used_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    valid_from: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    valid_until: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

class PromoCodeUsage(Base):
    __tablename__ = "promo_code_usages"
    id: Mapped[int] = mapped_column(primary_key=True)
    promo_code_id: Mapped[int] = mapped_column(ForeignKey("promo_codes.id", ondelete="CASCADE"), index=True)
    user_id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True, native_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), index=True)
    __table_args__ = (UniqueConstraint("promo_code_id", "user_id", name="uq_promo_user"),)
