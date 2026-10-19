import uuid
import enum
from typing import List, Optional

from sqlalchemy import (
    Uuid,
    Boolean,
    DateTime,
    Enum,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
    false,
    Index,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship
from app.core.database import Base
from app.core.mixins import ExamFlagsMixin, ExamIdMixin, TimestampMixin
from app.modules.auth.models import User


# ==========================================================
# ENUMS
# ==========================================================

class CEFRLevel(str, enum.Enum):
    B1 = "B1"
    B2 = "B2"
    C1 = "C1"


class WritingStyle(str, enum.Enum):
    INFORMAL = "INFORMAL"
    FORMAL = "FORMAL"
    POST = "POST"


class ScoringMode(str, enum.Enum):
    HOLISTIC = "HOLISTIC"
    ANALYTIC = "ANALYTIC"


class WritingCriterion(str, enum.Enum):
    TASK_ACHIEVEMENT = "task_achievement"
    COHERENCE = "coherence"
    VOCABULARY = "vocabulary"
    GRAMMAR = "grammar"
    MECHANICS = "mechanics"
    OVERALL = "overall"


class FeedbackSource(str, enum.Enum):
    AI = "AI"
    HUMAN = "HUMAN"


# ==========================================================
# WRITING FORMAT
# ==========================================================

class WritingFormat(Base, TimestampMixin):
    __tablename__ = "writing_formats"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)

    name: Mapped[str] = mapped_column(String(50), nullable=False, unique=True)
    cefr_level: Mapped[CEFRLevel] = mapped_column(Enum(CEFRLevel, native_enum=False), nullable=False)

    min_words: Mapped[int] = mapped_column(Integer, nullable=False)
    max_words: Mapped[int] = mapped_column(Integer, nullable=False)

    style: Mapped[WritingStyle] = mapped_column(Enum(WritingStyle, native_enum=False), nullable=False)
    scoring_mode: Mapped[ScoringMode] = mapped_column(Enum(ScoringMode, native_enum=False), nullable=False)

    penalty_enabled: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=false(), index=True,
    )

    tasks: Mapped[List["WritingTask"]] = relationship(
        back_populates="format", cascade="all, delete", passive_deletes=True, lazy="select",
    )
    criterion_configs: Mapped[List["WritingCriterionConfig"]] = relationship(
        back_populates="format", cascade="all, delete-orphan", passive_deletes=True, lazy="selectin",
    )


# ==========================================================
# WRITING CRITERION CONFIG (weight system)
# ==========================================================

class WritingCriterionConfig(Base):
    __tablename__ = "writing_criterion_configs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)

    format_id: Mapped[int] = mapped_column(
        ForeignKey("writing_formats.id", ondelete="CASCADE"), nullable=False, index=True,
    )

    criterion: Mapped[WritingCriterion] = mapped_column(Enum(WritingCriterion, native_enum=False), nullable=False)
    weight: Mapped[float] = mapped_column(Float, nullable=False)

    __table_args__ = (
        UniqueConstraint("format_id", "criterion", name="uq_format_criterion"),
    )

    format: Mapped["WritingFormat"] = relationship(back_populates="criterion_configs")


# ==========================================================
# WRITING EXAM
# ==========================================================

class WritingExam(Base, ExamIdMixin, ExamFlagsMixin, TimestampMixin):
    """SUB-TEST 3: WRITING — 2 qism (informal/formal xat + blogpost/forum
    posti), rasmiy DTM formati. `id` boshqa exam modullari bilan bir xil
    (String(64)) — avval String(30) edi, endi kengaytirildi.
    """

    __tablename__ = "writing_exams"

    year: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    sequence_number: Mapped[int] = mapped_column(Integer, nullable=False)

    title: Mapped[str] = mapped_column(String(255), nullable=False)
    cefr_level: Mapped[CEFRLevel] = mapped_column(Enum(CEFRLevel, native_enum=False), nullable=False)

    duration_minutes: Mapped[int] = mapped_column(
        Integer, nullable=False, default=60, server_default=text("60"),
    )

    __table_args__ = (
        UniqueConstraint("year", "sequence_number", name="uq_writing_year_sequence"),
        Index("ix_exam_year_sequence", "year", "sequence_number"),
    )

    tasks: Mapped[List["WritingTask"]] = relationship(
        back_populates="exam", cascade="all, delete-orphan", passive_deletes=True, lazy="selectin",
    )
    results: Mapped[List["WritingResult"]] = relationship(
        back_populates="exam", cascade="all, delete-orphan", passive_deletes=True, lazy="select",
    )


# ==========================================================
# WRITING TASK
# ==========================================================

class WritingTask(Base):
    __tablename__ = "writing_tasks"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)

    exam_id: Mapped[str] = mapped_column(
        ForeignKey("writing_exams.id", ondelete="CASCADE"), nullable=False, index=True,
    )
    external_id: Mapped[Optional[str]] = mapped_column(String(128), nullable=True, index=True)
    format_id: Mapped[int] = mapped_column(
        ForeignKey("writing_formats.id", ondelete="RESTRICT"), nullable=False, index=True,
    )

    part_number: Mapped[int] = mapped_column(Integer, nullable=False)
    sub_part: Mapped[Optional[int]] = mapped_column(Integer)

    topic: Mapped[str] = mapped_column(Text, nullable=False)
    instruction: Mapped[str] = mapped_column(Text, nullable=False)
    context_text: Mapped[Optional[str]] = mapped_column(Text)
    question: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    word_limit_min: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    word_limit_max: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)

    created_at: Mapped[object] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(),
    )

    __table_args__ = (
        UniqueConstraint("exam_id", "external_id", name="uq_writing_task_external_id"),
        UniqueConstraint("exam_id", "part_number", "sub_part", name="uq_exam_part_structure"),
    )

    exam: Mapped["WritingExam"] = relationship(back_populates="tasks")
    format: Mapped["WritingFormat"] = relationship(back_populates="tasks")

    answers: Mapped[List["WritingAnswer"]] = relationship(
        back_populates="task", cascade="all, delete-orphan", passive_deletes=True, lazy="select",
    )

    @property
    def word_limit(self):
        if self.word_limit_min is None and self.word_limit_max is None:
            return None
        return {"min": self.word_limit_min or 1, "max": self.word_limit_max or self.word_limit_min or 1}


# ==========================================================
# WRITING RESULT
# (scaled_score/is_finalized — writing'ga xos, shuning uchun
#  ResultBaseMixin o'rniga TimestampMixin bilan qo'lda saqlanadi.
#  Qo'lda (admin/teacher) baholash: services/result_service.py::grade_result.)
# ==========================================================

class WritingResult(Base, TimestampMixin):
    __tablename__ = "writing_results"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)

    user_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True, native_uuid=True), ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False, index=True,
    )
    exam_id: Mapped[str] = mapped_column(
        ForeignKey("writing_exams.id", ondelete="CASCADE"), nullable=False, index=True,
    )
    exam_attempt_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("mock_exam_attempts.id", ondelete="CASCADE"), nullable=True, index=True,
    )
    practice_attempt_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("practice_attempts.id", ondelete="SET NULL"), nullable=True, index=True,
    )

    raw_score: Mapped[Optional[float]] = mapped_column(Float)
    scaled_score: Mapped[Optional[float]] = mapped_column(Float)
    cefr_level: Mapped[Optional[CEFRLevel]] = mapped_column(Enum(CEFRLevel, native_enum=False))

    is_finalized: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default=false())
    evaluation_status: Mapped[str] = mapped_column(
        String(30), nullable=False, default="PENDING", server_default=text("'PENDING'"), index=True,
    )
    human_evaluator_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        Uuid(as_uuid=True, native_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True,
    )
    ai_evaluated_at: Mapped[Optional[object]] = mapped_column(DateTime(timezone=True), nullable=True)
    ai_model: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)

    __table_args__ = (
        Index("ix_result_user_exam", "user_id", "exam_id"),
        UniqueConstraint("practice_attempt_id", name="uq_writing_result_practice_attempt"),
    )

    exam: Mapped["WritingExam"] = relationship(back_populates="results")
    user: Mapped["User"] = relationship(lazy="selectin", foreign_keys=[user_id])

    answers: Mapped[List["WritingAnswer"]] = relationship(
        back_populates="result", cascade="all, delete-orphan", passive_deletes=True, lazy="selectin",
    )

    @property
    def full_name(self):
        return self.user.profile.full_name if self.user and self.user.profile else None

    @property
    def email(self):
        if not self.user:
            return None
        return next((c.value for c in self.user.contacts if getattr(c.contact_type, "value", c.contact_type) == "email" and c.is_primary), None) or next((c.value for c in self.user.contacts if getattr(c.contact_type, "value", c.contact_type) == "email"), None)

    @property
    def phone(self):
        if not self.user:
            return None
        return next((c.value for c in self.user.contacts if getattr(c.contact_type, "value", c.contact_type) == "phone" and c.is_primary), None) or next((c.value for c in self.user.contacts if getattr(c.contact_type, "value", c.contact_type) == "phone"), None)

    @property
    def username(self):
        return self.user.profile.username if self.user and self.user.profile else None

    @property
    def birth_date(self):
        return self.user.profile.birth_date if self.user and self.user.profile else None

    @property
    def gender(self):
        value = self.user.profile.gender if self.user and self.user.profile else None
        return getattr(value, "value", value)

    @property
    def language(self):
        return self.user.profile.language if self.user and self.user.profile else None


# ==========================================================
# WRITING ANSWER
# ==========================================================

class WritingAnswer(Base):
    __tablename__ = "writing_answers"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)

    result_id: Mapped[int] = mapped_column(
        ForeignKey("writing_results.id", ondelete="CASCADE"), nullable=False, index=True,
    )
    task_id: Mapped[int] = mapped_column(
        ForeignKey("writing_tasks.id", ondelete="CASCADE"), nullable=False, index=True,
    )

    content: Mapped[str] = mapped_column(Text, nullable=False)
    word_count: Mapped[int] = mapped_column(Integer, nullable=False)

    penalty: Mapped[float] = mapped_column(Float, nullable=False, default=0.0, server_default=text("0"))

    raw_score: Mapped[Optional[float]] = mapped_column(Float)
    scaled_score: Mapped[Optional[float]] = mapped_column(Float)

    created_at: Mapped[object] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(),
    )

    __table_args__ = (
        UniqueConstraint("result_id", "task_id", name="uq_answer_per_task"),
    )

    result: Mapped["WritingResult"] = relationship(back_populates="answers")
    task: Mapped["WritingTask"] = relationship(back_populates="answers")

    scores: Mapped[List["WritingScore"]] = relationship(
        back_populates="answer", cascade="all, delete-orphan", passive_deletes=True, lazy="selectin",
    )
    feedbacks: Mapped[List["WritingFeedback"]] = relationship(
        back_populates="answer", cascade="all, delete-orphan", passive_deletes=True, lazy="selectin",
    )


# ==========================================================
# WRITING SCORE
# ==========================================================

class WritingScore(Base):
    __tablename__ = "writing_scores"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)

    answer_id: Mapped[int] = mapped_column(
        ForeignKey("writing_answers.id", ondelete="CASCADE"), nullable=False, index=True,
    )
    criterion: Mapped[WritingCriterion] = mapped_column(Enum(WritingCriterion, native_enum=False), nullable=False)
    score: Mapped[float] = mapped_column(Float, nullable=False)

    created_at: Mapped[object] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(),
    )

    __table_args__ = (
        UniqueConstraint("answer_id", "criterion", name="uq_score_per_criterion"),
    )

    answer: Mapped["WritingAnswer"] = relationship(back_populates="scores")


# ==========================================================
# WRITING FEEDBACK (AI / HUMAN EXTENSIBLE)
# ==========================================================

class WritingFeedback(Base):
    __tablename__ = "writing_feedbacks"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)

    answer_id: Mapped[int] = mapped_column(
        ForeignKey("writing_answers.id", ondelete="CASCADE"), nullable=False, index=True,
    )
    source: Mapped[FeedbackSource] = mapped_column(Enum(FeedbackSource, native_enum=False), nullable=False)

    model_name: Mapped[Optional[str]] = mapped_column(String(100))
    content: Mapped[str] = mapped_column(Text, nullable=False)

    created_at: Mapped[object] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(),
    )

    answer: Mapped["WritingAnswer"] = relationship(back_populates="feedbacks")
