from pydantic import BaseModel, ConfigDict

class PeriodStats(BaseModel):
    new_users: int = 0
    practice_attempts: int = 0
    mock_results: int = 0
    ai_checks: int = 0


class StatisticsResponse(BaseModel):
    total_users: int
    total_exams_taken: int
    users_today: int
    # Joriy hafta (dushanbadan) va joriy kalendar oy (Asia/Tashkent) kesimi.
    week: PeriodStats = PeriodStats()
    month: PeriodStats = PeriodStats()


class TrafficPoint(BaseModel):
    date: str
    new_users: int = 0
    practice_attempts: int = 0
    mock_results: int = 0
    ai_checks: int = 0


class TrafficResponse(BaseModel):
    period: str
    start: str
    end: str
    points: list[TrafficPoint]
    totals: PeriodStats

import uuid
from datetime import datetime
from typing import Optional
from pydantic import Field
from app.modules.auth.models import UserRole

class AdminUserUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    is_active: Optional[bool] = None
    global_role: Optional[UserRole] = None

class AdminUserSummary(BaseModel):
    id: uuid.UUID
    is_active: bool
    global_role: UserRole
    full_name: Optional[str] = None
    username: Optional[str] = None
    # Eng so'nggi imtihon ro'yxatidagi MK ID (profilda saqlanmaydi).
    mk_id: Optional[str] = None
    created_at: datetime

class SmsRegistrationSetting(BaseModel):
    """Admin boshqaradigan `sms_registration_enabled` sozlamasi (GET javobi / PUT tanasi)."""
    model_config = ConfigDict(extra="forbid")
    sms_registration_enabled: bool

class AdminRegistrationStatusUpdate(BaseModel):
    status: str = Field(..., min_length=3, max_length=30)


class AdminDashboardResponse(BaseModel):
    users: int
    active_users: int
    mock_exams: int
    sessions: int
    registrations: int
    attempts: int
    final_results: int
    omr_pending: int
    teachers: int = 0
    paid_orders: int = 0
    pending_orders: int = 0
    confirmed_revenue: int = 0
