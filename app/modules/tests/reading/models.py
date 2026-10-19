import enum
from typing import List, Optional

from sqlalchemy import Enum, ForeignKey, Integer, JSON, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base
from app.core.mixins import ExamFlagsMixin, ExamIdMixin, ResultBaseMixin, TimestampMixin


class ReadingQuestionType(str, enum.Enum):
    GAP_FILL = "GAP_FILL"
    GAP_FILL_FILL = "GAP_FILL_FILL"
    MULTIPLE_CHOICE = "MULTIPLE_CHOICE"
    TRUE_FALSE_NOT_GIVEN = "TRUE_FALSE_NOT_GIVEN"
    HEADINGS_MATCH = "HEADINGS_MATCH"
    TEXT_MATCH = "TEXT_MATCH"
    MULTIPLE_SELECT = "MULTIPLE_SELECT"


class ReadingTest(Base, ExamIdMixin, ExamFlagsMixin, TimestampMixin):
    """PAPER 2: READING — 5 qism, 35 savol, 1 soat (rasmiy DTM formati)."""

    __tablename__ = "reading_tests"

    title: Mapped[str] = mapped_column(String, nullable=False)
    cefr_level: Mapped[str] = mapped_column(String, nullable=False)
    language: Mapped[str] = mapped_column(String, default="en")
    duration_minutes: Mapped[int] = mapped_column(Integer, default=60)
    total_questions: Mapped[int] = mapped_column(Integer, nullable=False)

    # Part mini-test source metadata. NULL for ordinary/full Reading tests.
    parent_test_id: Mapped[Optional[str]] = mapped_column(
        String(64), ForeignKey("reading_tests.id", ondelete="SET NULL", name="fk_reading_tests_parent_test_id"), nullable=True, index=True
    )
    source_part_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("reading_parts.id", ondelete="CASCADE", name="fk_reading_tests_source_part_id"), nullable=True, index=True
    )
    practice_part_number: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)

    parts: Mapped[List["ReadingPart"]] = relationship(
        back_populates="test",
        cascade="all, delete-orphan",
        foreign_keys="ReadingPart.test_id",
        order_by="ReadingPart.id",
    )
    results: Mapped[List["ReadingResult"]] = relationship(
        back_populates="test", cascade="all, delete-orphan"
    )
    parent_test: Mapped[Optional["ReadingTest"]] = relationship(
        remote_side="ReadingTest.id", foreign_keys=[parent_test_id], uselist=False
    )

    __table_args__ = (
        UniqueConstraint("parent_test_id", "source_part_id", name="uq_reading_mini_test_source_part"),
    )


class ReadingPart(Base):
    __tablename__ = "reading_parts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    test_id: Mapped[str] = mapped_column(
        ForeignKey("reading_tests.id", ondelete="CASCADE"), nullable=False
    )

    external_id: Mapped[Optional[str]] = mapped_column(String(128), nullable=True, index=True)
    title: Mapped[str] = mapped_column(String, nullable=False)
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    passage: Mapped[str] = mapped_column(Text, nullable=False)
    image_url: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)

    __table_args__ = (UniqueConstraint("test_id", "external_id", name="uq_reading_part_external_id"),)

    test: Mapped["ReadingTest"] = relationship(
        back_populates="parts", foreign_keys=[test_id]
    )
    questions: Mapped[List["ReadingQuestion"]] = relationship(
        back_populates="part", cascade="all, delete-orphan",
        order_by="ReadingQuestion.question_number",
    )


class ReadingQuestion(Base):
    __tablename__ = "reading_questions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    part_id: Mapped[int] = mapped_column(
        ForeignKey("reading_parts.id", ondelete="CASCADE", name="fk_reading_tests_source_part_id"), nullable=False
    )

    external_id: Mapped[Optional[str]] = mapped_column(String(128), nullable=True, index=True)
    question_number: Mapped[int] = mapped_column(Integer, nullable=False)
    type: Mapped[ReadingQuestionType] = mapped_column(
        Enum(ReadingQuestionType, native_enum=False), nullable=False
    )
    text: Mapped[str] = mapped_column(Text, nullable=False)
    correct_answer: Mapped[dict] = mapped_column(JSON, nullable=False)
    word_limit: Mapped[int] = mapped_column(Integer, default=0)

    __table_args__ = (UniqueConstraint("part_id", "external_id", name="uq_reading_question_external_id"),)

    part: Mapped["ReadingPart"] = relationship(back_populates="questions")
    options: Mapped[List["ReadingOption"]] = relationship(
        back_populates="question", cascade="all, delete-orphan",
        order_by="ReadingOption.label",
    )


class ReadingOption(Base):
    __tablename__ = "reading_options"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    question_id: Mapped[int] = mapped_column(
        ForeignKey("reading_questions.id", ondelete="CASCADE"), nullable=False
    )

    external_id: Mapped[Optional[str]] = mapped_column(String(128), nullable=True, index=True)
    label: Mapped[str] = mapped_column(String, nullable=False)
    value: Mapped[str] = mapped_column(String, nullable=False)

    __table_args__ = (UniqueConstraint("question_id", "external_id", name="uq_reading_option_external_id"),)

    question: Mapped["ReadingQuestion"] = relationship(back_populates="options")


class ReadingResult(Base, ResultBaseMixin):
    """Umumiy natija maydonlari ResultBaseMixin'dan keladi
    (id, user_id, exam_attempt_id, raw_score, standard_score, cefr_level,
    user_answers, created_at, updated_at). Bu yerda faqat reading'ga xos
    `test_id` FK va `percentage` qo'shiladi.
    """

    __tablename__ = "reading_results"

    test_id: Mapped[str] = mapped_column(
        ForeignKey("reading_tests.id", ondelete="CASCADE"), nullable=False
    )
    percentage: Mapped[float] = mapped_column(nullable=False)
    # ILGARI TOPILGAN XATO: bu ustun umuman yo'q edi, va
    # `ReadingResultResponse.total_questions` shu sabab har doim qattiq
    # yozilgan standart qiymat (35) ni ko'rsatardi — 35 ta savoldan farqli
    # (masalan qo'lda import qilingan) testlarda natija noto'g'ri
    # ko'rsatilardi. Endi ListeningResult'dagi bilan bir xil naqshda
    # haqiqiy qiymat saqlanadi.
    total_questions: Mapped[int] = mapped_column(Integer, default=0)

    # Practice urinish -> bitta yakuniy natija (DB darajasida kafolat).
    practice_attempt_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("practice_attempts.id", ondelete="SET NULL"), nullable=True
    )

    test: Mapped["ReadingTest"] = relationship(back_populates="results")

    __table_args__ = (
        # Mock: bitta skill-attempt + test uchun bitta natija. exam_attempt_id
        # NULL (practice) qatorlar unique hisoblanmaydi.
        UniqueConstraint("exam_attempt_id", "test_id", name="uq_reading_result_attempt_test"),
        UniqueConstraint("practice_attempt_id", name="uq_reading_result_practice_attempt"),
    )
