import enum
from typing import List, Optional

from sqlalchemy import Enum, ForeignKey, Integer, String, Text, UniqueConstraint, Float
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base
from app.core.mixins import ExamFlagsMixin, ExamIdMixin, ResultBaseMixin, TimestampMixin


class ListeningQuestionType(str, enum.Enum):
    MULTIPLE_CHOICE = "MULTIPLE_CHOICE"
    GAP_FILL = "GAP_FILL"
    MATCHING = "MATCHING"
    MAP_DIAGRAM = "MAP_DIAGRAM"
    SENTENCE_COMPLETION = "SENTENCE_COMPLETION"
    SHORT_ANSWER = "SHORT_ANSWER"


class ListeningTest(Base, ExamIdMixin, ExamFlagsMixin, TimestampMixin):
    """PAPER 1: LISTENING — 6 qism, 35 savol, ~35 daqiqa (rasmiy DTM formati)."""

    __tablename__ = "listening_tests"

    title: Mapped[str] = mapped_column(String, nullable=False)
    cefr_level: Mapped[str] = mapped_column(String, default="B2")
    duration_minutes: Mapped[int] = mapped_column(Integer, default=35)
    total_questions: Mapped[int] = mapped_column(Integer, default=35)
    sections: Mapped[Optional[str]] = mapped_column(String, nullable=True)

    # Audio manbasi: EXAM = bitta umumiy audio, PARTS = har bir part uchun alohida audio.
    audio_mode: Mapped[str] = mapped_column(String(16), nullable=False, default="PARTS", server_default="PARTS")
    audio_url: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)

    # Part mini-test metadata. NULL for ordinary/full Listening tests.
    # Reading'dan farqli o'laroq `source_part_id` FK yo'q: update_exam() partlarni
    # o'chirib qayta yaratadi, FK(CASCADE) bo'lsa mini-testlar ham o'chib ketardi.
    # Idempotentlik (parent_test_id, practice_part_number) juftligi bilan ta'minlanadi.
    parent_test_id: Mapped[Optional[str]] = mapped_column(
        String(64), ForeignKey("listening_tests.id", ondelete="SET NULL", name="fk_listening_tests_parent_test_id"),
        nullable=True, index=True,
    )
    practice_part_number: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)

    parts: Mapped[List["ListeningPart"]] = relationship(
        back_populates="exam", cascade="all, delete-orphan", lazy="selectin",
        order_by="ListeningPart.part_number",
    )
    results: Mapped[List["ListeningResult"]] = relationship(
        back_populates="exam", cascade="all, delete-orphan"
    )

    __table_args__ = (
        UniqueConstraint("parent_test_id", "practice_part_number", name="uq_listening_mini_test_part_number"),
    )


class ListeningPart(Base):
    __tablename__ = "listening_parts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    exam_id: Mapped[str] = mapped_column(
        ForeignKey("listening_tests.id", ondelete="CASCADE"), nullable=False
    )

    external_id: Mapped[Optional[str]] = mapped_column(String(128), nullable=True, index=True)
    part_number: Mapped[int] = mapped_column(Integer, nullable=False)
    title: Mapped[str] = mapped_column(String, nullable=False)
    instruction: Mapped[str] = mapped_column(Text, nullable=False)
    task_type: Mapped[str] = mapped_column(String, nullable=False)

    # PARTS rejimida majburiy; EXAM rejimida bo'sh qoldiriladi.
    audio_url: Mapped[str] = mapped_column(String(500), nullable=False, default="", server_default="")
    # EXAM rejimida umumiy audio ichidagi part segment chegaralari (sekund).
    audio_start_seconds: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    audio_end_seconds: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    context: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    passage: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    map_image: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    image_url: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)
    example_json: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    __table_args__ = (UniqueConstraint("exam_id", "external_id", name="uq_listening_part_external_id"),)

    exam: Mapped["ListeningTest"] = relationship(back_populates="parts")
    questions: Mapped[List["ListeningQuestion"]] = relationship(
        back_populates="part", cascade="all, delete-orphan", lazy="selectin",
        order_by="ListeningQuestion.question_number",
    )
    options: Mapped[List["ListeningPartOption"]] = relationship(
        back_populates="part", cascade="all, delete-orphan", lazy="selectin",
        order_by="ListeningPartOption.label",
    )


class ListeningQuestion(Base):
    __tablename__ = "listening_questions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    part_id: Mapped[int] = mapped_column(
        ForeignKey("listening_parts.id", ondelete="CASCADE"), nullable=False
    )

    external_id: Mapped[Optional[str]] = mapped_column(String(128), nullable=True, index=True)
    question_number: Mapped[int] = mapped_column(Integer, nullable=False)
    type: Mapped[ListeningQuestionType] = mapped_column(
        Enum(ListeningQuestionType, native_enum=False), nullable=False
    )

    text: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    correct_answer: Mapped[str] = mapped_column(String, nullable=False)
    group_title: Mapped[Optional[str]] = mapped_column(String(200), nullable=True)

    __table_args__ = (UniqueConstraint("part_id", "external_id", name="uq_listening_question_external_id"),)

    part: Mapped["ListeningPart"] = relationship(back_populates="questions")
    options: Mapped[List["ListeningQuestionOption"]] = relationship(
        back_populates="question", cascade="all, delete-orphan", lazy="selectin",
        order_by="ListeningQuestionOption.label",
    )


class ListeningPartOption(Base):
    __tablename__ = "listening_part_options"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    part_id: Mapped[int] = mapped_column(
        ForeignKey("listening_parts.id", ondelete="CASCADE"), nullable=False
    )

    external_id: Mapped[Optional[str]] = mapped_column(String(128), nullable=True, index=True)
    value: Mapped[str] = mapped_column(String, nullable=False)
    label: Mapped[str] = mapped_column(String, nullable=False)

    __table_args__ = (UniqueConstraint("part_id", "external_id", name="uq_listening_part_option_external_id"),)

    part: Mapped["ListeningPart"] = relationship(back_populates="options")


class ListeningQuestionOption(Base):
    __tablename__ = "listening_question_options"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    question_id: Mapped[int] = mapped_column(
        ForeignKey("listening_questions.id", ondelete="CASCADE"), nullable=False
    )

    external_id: Mapped[Optional[str]] = mapped_column(String(128), nullable=True, index=True)
    value: Mapped[str] = mapped_column(String, nullable=False)
    label: Mapped[str] = mapped_column(String, nullable=False)

    __table_args__ = (UniqueConstraint("question_id", "external_id", name="uq_listening_question_option_external_id"),)

    question: Mapped["ListeningQuestion"] = relationship(back_populates="options")


class ListeningResult(Base, ResultBaseMixin):
    """Umumiy maydonlar ResultBaseMixin'dan (id, user_id, exam_attempt_id,
    raw_score, standard_score, cefr_level, user_answers, created_at,
    updated_at). Bu yerda faqat listening'ga xos `exam_id` FK va
    savol-sanoqlari qo'shiladi.
    """

    __tablename__ = "listening_results"

    exam_id: Mapped[str] = mapped_column(
        ForeignKey("listening_tests.id", ondelete="CASCADE"), nullable=False
    )

    total_questions: Mapped[int] = mapped_column(Integer, default=0)
    correct_answers: Mapped[int] = mapped_column(Integer, default=0)
    percentage: Mapped[float] = mapped_column(default=0.0)

    practice_attempt_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("practice_attempts.id", ondelete="SET NULL"), nullable=True
    )

    exam: Mapped["ListeningTest"] = relationship(back_populates="results")

    __table_args__ = (
        UniqueConstraint("exam_attempt_id", "exam_id", name="uq_listening_result_attempt_exam"),
        UniqueConstraint("practice_attempt_id", name="uq_listening_result_practice_attempt"),
    )
