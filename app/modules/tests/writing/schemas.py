from datetime import datetime
from typing import List, Optional, Union
from pydantic import BaseModel, Field, model_validator, field_validator, computed_field

import uuid
from .models import (
    CEFRLevel,
    WritingStyle,
    ScoringMode,
    WritingCriterion,
    FeedbackSource,
)


# ==========================================================
# BASE
# ==========================================================

class ORMModel(BaseModel):
    model_config = {
        "from_attributes": True,
        "extra": "forbid",
    }


# ==========================================================
# FORMAT
# ==========================================================

class WritingCriterionConfigRead(BaseModel):
    criterion: WritingCriterion
    weight: float


class WritingCriterionWeightCreate(BaseModel):
    criterion: WritingCriterion
    weight: float = Field(..., gt=0, le=1)


class WritingFormatBase(BaseModel):
    name: str = Field(..., min_length=2, max_length=50)
    cefr_level: CEFRLevel

    min_words: int = Field(..., ge=1)
    max_words: int = Field(..., ge=1)

    style: WritingStyle
    scoring_mode: ScoringMode
    penalty_enabled: bool = False

    @model_validator(mode="after")
    def validate_words(self):
        if self.min_words >= self.max_words:
            raise ValueError("min_words must be < max_words")
        return self


class WritingFormatCreate(WritingFormatBase):
    criterion_weights: List[WritingCriterionWeightCreate]

    @model_validator(mode="after")
    def validate_weights(self):
        total = sum(c.weight for c in self.criterion_weights)
        if abs(total - 1.0) > 1e-6:
            raise ValueError("Criterion weights must sum to 1.0")
        return self


class WritingFormatRead(ORMModel):
    id: int
    name: str
    cefr_level: CEFRLevel
    min_words: int
    max_words: int
    style: WritingStyle
    scoring_mode: ScoringMode
    penalty_enabled: bool
    created_at: datetime
    criterion_configs: List[WritingCriterionConfigRead]


# ==========================================================
# TASK
# ==========================================================

class WritingWordLimit(BaseModel):
    min: int = Field(..., ge=1)
    max: int = Field(..., ge=1)

    @model_validator(mode="after")
    def validate_range(self):
        if self.min > self.max:
            raise ValueError("word_limit.min must be <= word_limit.max")
        return self


class WritingTaskBase(BaseModel):
    external_id: Optional[str] = Field(None, min_length=1, max_length=128)
    part_number: int = Field(..., ge=1)
    sub_part: Optional[int] = Field(None, ge=1)
    cefr_level: Optional[CEFRLevel] = None

    topic: str = Field(..., min_length=5)
    instruction: str = Field(..., min_length=5)
    context_text: Optional[str] = None
    question: Optional[str] = None
    word_limit: Optional[Union[int, WritingWordLimit]] = None

    format_id: Optional[int] = None


class WritingTaskCreate(WritingTaskBase):
    pass


class WritingTaskRead(ORMModel):
    id: int
    exam_id: str
    part_number: int
    sub_part: Optional[int]
    cefr_level: Optional[CEFRLevel] = None
    topic: str
    instruction: str
    context_text: Optional[str]
    question: Optional[str] = None
    word_limit: Optional[WritingWordLimit] = None
    created_at: datetime
    format: WritingFormatRead


# ==========================================================
# EXAM
# ==========================================================

class WritingExamBase(BaseModel):
    id: Optional[str] = Field(None, min_length=1, max_length=64)
    title: str = Field(..., min_length=3)
    cefr_level: CEFRLevel
    duration_minutes: int = Field(default=60, ge=10, le=240)

    is_demo: bool = False
    is_free: bool = False
    is_practice: bool = False
    is_active: bool = True


# 🔥 CLIENT year/sequence_number yubormaydi
class WritingExamCreate(WritingExamBase):
    tasks: List[WritingTaskCreate]

    @model_validator(mode="after")
    def validate_unique_tasks(self):
        seen = set()
        for t in self.tasks:
            key = (t.part_number, t.sub_part)
            if key in seen:
                raise ValueError("Duplicate task structure")
            seen.add(key)
        return self


class WritingExamUpdate(BaseModel):
    model_config = {"extra": "forbid"}

    title: Optional[str] = Field(None, min_length=3)
    cefr_level: Optional[CEFRLevel] = None
    duration_minutes: Optional[int] = Field(None, ge=10, le=240)

    is_demo: Optional[bool] = None
    is_free: Optional[bool] = None
    is_practice: Optional[bool] = None
    is_active: Optional[bool] = None

    tasks: Optional[List[WritingTaskCreate]] = None

    @model_validator(mode="after")
    def validate_unique_tasks(self):
        if self.tasks is None:
            return self
        seen = set()
        for t in self.tasks:
            key = (t.part_number, t.sub_part)
            if key in seen:
                raise ValueError("Duplicate task structure")
            seen.add(key)
        return self


class WritingExamRead(ORMModel):
    id: str
    year: int
    sequence_number: int
    title: str
    cefr_level: CEFRLevel
    duration_minutes: int
    is_demo: bool
    is_free: bool
    is_practice: bool
    is_active: bool
    created_at: datetime
    tasks: List[WritingTaskRead]


# ==========================================================
# SUBMISSION
# ==========================================================

class WritingAnswerSubmit(BaseModel):
    task_id: int
    # Empty content is a valid default for a task left unanswered when time expires.
    content: str = ""

    @field_validator("content")
    @classmethod
    def strip_text(cls, v: str):
        return (v or "").strip()


class WritingSubmitRequest(BaseModel):
    answers: List[WritingAnswerSubmit] = Field(..., min_length=1)
    exam_attempt_id: Optional[int] = None
    part_number: Optional[int] = Field(default=None, ge=1)

    @model_validator(mode="after")
    def validate_unique_answers(self):
        ids = [item.task_id for item in self.answers]
        if len(ids) != len(set(ids)):
            raise ValueError("Har bir Writing task uchun faqat bitta javob yuborish mumkin")
        return self


# ==========================================================
# RESULT / SCORE
# ==========================================================

class WritingScoreRead(ORMModel):
    id: int
    criterion: WritingCriterion
    score: float
    created_at: datetime


class WritingFeedbackRead(ORMModel):
    id: int
    source: FeedbackSource
    model_name: Optional[str]
    content: str
    created_at: datetime


class WritingAnswerResultRead(ORMModel):
    id: int
    task_id: int
    content: str
    word_count: int
    penalty: float
    raw_score: Optional[float]
    scaled_score: Optional[float]
    created_at: datetime
    scores: List[WritingScoreRead]
    feedbacks: List[WritingFeedbackRead]


class WritingResultRead(ORMModel):
    id: int
    user_id: uuid.UUID
    full_name: Optional[str] = None
    email: Optional[str] = None
    phone: Optional[str] = None
    username: Optional[str] = None
    birth_date: Optional[datetime] = None
    gender: Optional[str] = None
    language: Optional[str] = None
    exam_id: str
    raw_score: Optional[float]
    scaled_score: Optional[float]
    cefr_level: Optional[CEFRLevel]
    is_finalized: bool
    evaluation_status: str
    human_evaluator_id: Optional[uuid.UUID]
    ai_evaluated_at: Optional[datetime]
    ai_model: Optional[str]
    created_at: datetime
    answers: List[WritingAnswerResultRead]

    @computed_field
    @property
    def evaluation_source(self) -> str:
        """UI uchun yakuniy bahoni kim berganini barqaror ko'rsatadi."""
        if self.evaluation_status in {"HUMAN_FINAL", "TEACHER_PENDING"} or self.human_evaluator_id is not None:
            return "TEACHER"
        if self.evaluation_status in {"AI_FINAL", "AI_DRAFT"} or self.ai_evaluated_at is not None:
            return "AI"
        return "PENDING"


class WritingCriterionScoreRequest(BaseModel):
    """Bitta writing mezoni bo'yicha o'qituvchi yakuniy bahosi."""
    criterion: WritingCriterion
    score: float = Field(..., ge=0, le=15)


class WritingGradeRequest(BaseModel):
    """Admin/teacher tomonidan qo'lda baholash — AI ishlatilmaydi."""
    answer_id: Optional[int] = Field(default=None, description="Qaysi javobga baho berilayotgani (agar kiritilmasa, birinchisiga yoziladi)")
    # Ekspert ballari yig‘indisi (1.1: 0–5 + 1.2: 0–5 + 2: 0–6 = 0–16).
    # Yakuniy ball (0–75) jadval bo'yicha server tomonida hisoblanadi.
    raw_score: float = Field(..., ge=0, le=16)
    cefr_level: CEFRLevel
    feedback: Optional[str] = Field(default=None, max_length=4000)
    criterion_scores: List[WritingCriterionScoreRequest] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_criterion_scores(self):
        seen = set()
        for item in self.criterion_scores:
            if item.criterion in seen:
                raise ValueError(f"Duplicate criterion: {item.criterion.value}")
            seen.add(item.criterion)
        return self


class WritingAIEvaluateRequest(BaseModel):
    """AI draft uchun ixtiyoriy bitta answer ID. Teacher keyin final qiladi."""
    answer_id: Optional[int] = Field(default=None, ge=1)