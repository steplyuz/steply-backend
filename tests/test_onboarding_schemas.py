"""Onboarding sxemasi qoidalari (DB'siz)."""
import pytest
from pydantic import ValidationError

from app.modules.onboarding.schemas import OnboardingUpdate, PlanSchema


def test_valid_partial_update():
    OnboardingUpdate(strengths=["READING"], weaknesses=["WRITING"], daily_hours=1.0, teacher_hours=0.5)


def test_strength_and_weakness_cannot_overlap():
    with pytest.raises(ValidationError):
        OnboardingUpdate(strengths=["READING"], weaknesses=["READING"])


def test_teacher_hours_cannot_exceed_daily_hours():
    with pytest.raises(ValidationError):
        OnboardingUpdate(daily_hours=0.5, teacher_hours=1.0)


@pytest.mark.parametrize("payload", [{"target_level": "C2"}, {"daily_hours": 0.75}, {"cefr_level": "B3"}])
def test_invalid_values_rejected(payload):
    with pytest.raises(ValidationError):
        OnboardingUpdate(**payload)


def test_plan_schema_requires_months_and_breakdown():
    with pytest.raises(ValidationError):
        PlanSchema(summary="x", months=[], daily_minutes=60, daily_breakdown=[],
                   teacher_sessions_per_week=1, vocabulary_words_per_day=10, milestones=[])
