"""Billing javob sxemalari (response_model). Frontend ishlatadigan maydonlar o'zgarmaydi."""
from datetime import datetime
from typing import Optional
from uuid import UUID

from pydantic import BaseModel, ConfigDict

from .models import BillingOrderStatus, BillingProductType, SubscriptionTier


class PlanPublic(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    description: Optional[str] = None
    price: int
    duration_days: int
    tier: SubscriptionTier
    ai_limit: int
    exam_limit: int
    included_skills: list[str] = []
    ai_enabled: bool = False
    discount_percent: int = 0
    is_active: bool
    created_at: datetime
    updated_at: Optional[datetime] = None


class ProductPublic(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    product_type: BillingProductType
    code: Optional[str] = None
    title: str
    description: Optional[str] = None
    price: int
    external_ref: Optional[str] = None
    is_active: bool
    created_at: datetime
    updated_at: Optional[datetime] = None


class TierLimitPublic(BaseModel):
    tier: str
    skill: str
    weekly_limit: Optional[int] = None
    monthly_limit: Optional[int] = None
    allowed: bool = True


class PromoCodePublic(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    code: str
    discount_percent: Optional[int] = None
    discount_amount: Optional[int] = None
    max_usage: Optional[int] = None
    used_count: int
    valid_from: Optional[datetime] = None
    valid_until: Optional[datetime] = None
    is_active: bool


class OrderPublic(BaseModel):
    """Buyurtma javobi. `receipt_path` / `receipt_storage_key` serverning ichki yo'llari —
    ular javobga kiritilmaydi (chek yuklash va yuklab olish uchun alohida endpointlar bor)."""
    model_config = ConfigDict(from_attributes=True)

    id: str
    user_id: UUID
    product_type: BillingProductType
    product_id: Optional[str] = None
    product_title: str
    amount: int
    original_amount: Optional[int] = None
    discount_amount: int
    promo_code_id: Optional[int] = None
    status: BillingOrderStatus
    user_note: Optional[str] = None
    admin_note: Optional[str] = None
    reviewed_by: Optional[UUID] = None
    reviewed_at: Optional[datetime] = None
    created_at: datetime
    updated_at: datetime


class SubscriptionPublic(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    user_id: UUID
    plan_id: int
    start_date: datetime
    end_date: datetime
    is_active: bool


class PracticeQuotaPublic(BaseModel):
    weekly_limit: Optional[int] = None
    used_this_week: int = 0
    remaining: Optional[int] = None
    monthly_limit: Optional[int] = None
    used_this_month: int = 0
    remaining_this_month: Optional[int] = None


class AiQuotaPublic(BaseModel):
    enabled: bool
    monthly_limit: int = 0
    used_this_month: int = 0
    subscription_remaining: int = 0
    purchased_remaining: int = 0
    remaining: int = 0


class LimitsPublic(BaseModel):
    tier: str
    week_start: str
    month_start: str
    ai: AiQuotaPublic
    practice: PracticeQuotaPublic
