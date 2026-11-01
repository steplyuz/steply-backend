"""Onboarding API sxemalari va validatsiya."""
from datetime import date
from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, model_validator

CefrLevel = Literal["A1", "A2", "B1", "B2", "C1"]
TargetLevel = Literal["A2", "B1", "B2", "C1"]
VocabBand = Literal["0-500", "500-1500", "1500-3000", "3000+"]
Skill = Literal["READING", "LISTENING", "WRITING", "SPEAKING", "GRAMMAR", "VOCABULARY"]
DailyHours = Literal[0.5, 1.0, 1.5, 2.0]
TeacherHours = Literal[0.0, 0.5, 1.0, 1.5, 2.0]


class OnboardingUpdate(BaseModel):
    """Qisman saqlash mumkin: barcha maydonlar ixtiyoriy, to'liqlik plan generatsiyasida tekshiriladi."""

    cefr_taken: Optional[bool] = None
    cefr_level: Optional[CefrLevel] = None
    estimated_level: Optional[CefrLevel] = None
    target_level: Optional[TargetLevel] = None
    vocab_band: Optional[VocabBand] = None
    strengths: Optional[list[Skill]] = Field(default=None, max_length=6)
    weaknesses: Optional[list[Skill]] = Field(default=None, max_length=6)
    exam_date_id: Optional[int] = None
    daily_hours: Optional[DailyHours] = None
    teacher_hours: Optional[TeacherHours] = None

    @model_validator(mode="after")
    def _check_consistency(self):
        if self.strengths is not None and self.weaknesses is not None:
            overlap = set(self.strengths) & set(self.weaknesses)
            if overlap:
                raise ValueError(f"Bir xil tomon ham kuchli, ham zaif bo'lishi mumkin emas: {sorted(overlap)}")
        if self.daily_hours is not None and self.teacher_hours is not None:
            if self.teacher_hours > self.daily_hours:
                raise ValueError("O'qituvchi bilan vaqt kunlik umumiy vaqtdan oshmasligi kerak")
        return self


class OnboardingResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    cefr_taken: Optional[bool] = None
    cefr_level: Optional[str] = None
    estimated_level: Optional[str] = None
    target_level: Optional[str] = None
    vocab_band: Optional[str] = None
    strengths: Optional[list[str]] = None
    weaknesses: Optional[list[str]] = None
    exam_date_id: Optional[int] = None
    daily_hours: Optional[float] = None
    teacher_hours: Optional[float] = None
    is_complete: bool = False
    plan_version: int = 0
    plan: Optional[dict] = None


class ExamDateIn(BaseModel):
    title: str = Field(min_length=1, max_length=255)
    exam_date: date
    register_url: str = Field(min_length=8, max_length=500, pattern=r"^https?://")
    is_active: bool = True


class ExamDateOut(ExamDateIn):
    model_config = ConfigDict(from_attributes=True)

    id: int


class ExamDatePatch(BaseModel):
    title: Optional[str] = Field(default=None, min_length=1, max_length=255)
    exam_date: Optional[date] = None
    register_url: Optional[str] = Field(default=None, min_length=8, max_length=500, pattern=r"^https?://")
    is_active: Optional[bool] = None


class PlanMonth(BaseModel):
    month: str = Field(max_length=32)
    focus: str = Field(max_length=500)
    weekly_goals: list[str] = Field(max_length=6)


class DailyBreakdownItem(BaseModel):
    skill: Skill
    minutes: int = Field(ge=0, le=240)


class PlanSchema(BaseModel):
    """AI javobining qat'iy sxemasi. Server ichki tekshiruvlarni (soat, sana) qo'shimcha bajaradi."""

    summary: str = Field(max_length=1000)
    months: list[PlanMonth] = Field(min_length=1, max_length=12)
    daily_minutes: int = Field(ge=30, le=120)
    daily_breakdown: list[DailyBreakdownItem] = Field(min_length=1, max_length=6)
    teacher_sessions_per_week: int = Field(ge=0, le=7)
    vocabulary_words_per_day: int = Field(ge=0, le=100)
    milestones: list[str] = Field(max_length=10)
