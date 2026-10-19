"""
Speaking moduli — ko'p darajali (multilevel) gapirish testi.

Struktura (rasmiy format bo'yicha):
    Part 1.1 (A1-A2) — 3 ta shaxsiy savol
    Part 1.2 (B1)    — 2 ta rasm + 3 ta savol
    Part 2   (B2)    — 1 ta rasm + 3 ta savol
    Part 3   (C1)    — mavzu + FOR/AGAINST ro'yxati

Admin 40+ ta to'liq SpeakingTest yaratadi. Imtihon (MockSkillAttempt)
boshlanganda ular orasidan FAQAT BITTASI tasodifiy tanlanadi va shu
attemptga biriktiriladi.

Vaqt ustunlari (prep_seconds, answer_seconds, ...) test yaratilganda
speaking_config.SPEAKING_TIMINGS dan yoziladi (snapshot) — admin ularni
JSON orqali o'zgartira olmaydi.

O'ZGARISHLAR (migratsiya kerak):
  * SpeakingSubmission.speaking_test_id va SpeakingRecordingDraft.speaking_test_id:
    ondelete CASCADE -> RESTRICT. Ishlatilgan testni o'chirish talabalarning
    audio yozuvlari va ballarini jimgina o'chirib yubormasligi uchun.
  * SpeakingSubmission: (mock_skill_attempt_id, part_external_id,
    question_external_id) bo'yicha UniqueConstraint (finalize race / dublikat).
"""

import uuid
from typing import List, Optional

from sqlalchemy import (
    Uuid,
    Float,
    ForeignKey,
    Integer,
    JSON,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base
from app.modules.auth.models import User
from app.core.mixins import ExamFlagsMixin, ExamIdMixin, TimestampMixin


class SpeakingTest(Base, ExamIdMixin, ExamFlagsMixin, TimestampMixin):
    """Bitta to'liq speaking test (40+ tadan biri). Admin tomonidan yaratiladi."""

    __tablename__ = "speaking_tests"

    title: Mapped[str] = mapped_column(String, nullable=False)
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    part_1_1: Mapped[Optional["SpeakingPart11"]] = relationship(
        back_populates="test", uselist=False, cascade="all, delete-orphan"
    )
    part_1_2: Mapped[Optional["SpeakingPart12"]] = relationship(
        back_populates="test", uselist=False, cascade="all, delete-orphan"
    )
    part_2: Mapped[Optional["SpeakingPart2"]] = relationship(
        back_populates="test", uselist=False, cascade="all, delete-orphan"
    )
    part_3: Mapped[Optional["SpeakingPart3"]] = relationship(
        back_populates="test", uselist=False, cascade="all, delete-orphan"
    )


class SpeakingPart11(Base, TimestampMixin):
    """Part 1.1 — A1-A2. 3 ta shaxsiy savol, har biriga javob vaqti bor."""

    __tablename__ = "speaking_part_1_1"

    id: Mapped[int] = mapped_column(primary_key=True)
    speaking_test_id: Mapped[str] = mapped_column(
        ForeignKey("speaking_tests.id", ondelete="CASCADE"), unique=True
    )
    external_id: Mapped[Optional[str]] = mapped_column(String(128), nullable=True, index=True)
    question_1_id: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    question_2_id: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    question_3_id: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)

    question_1: Mapped[str] = mapped_column(Text, nullable=False)
    question_2: Mapped[str] = mapped_column(Text, nullable=False)
    question_3: Mapped[str] = mapped_column(Text, nullable=False)

    # Qiymatlar speaking_config.SPEAKING_TIMINGS dan yoziladi.
    prep_seconds: Mapped[int] = mapped_column(Integer, default=0)
    answer_seconds: Mapped[int] = mapped_column(Integer, default=30)

    test: Mapped["SpeakingTest"] = relationship(back_populates="part_1_1")


class SpeakingPart12(Base, TimestampMixin):
    """Part 1.2 — B1. 2 ta rasm + 3 ta savol (4-savol 45s, 5-6-savollar 30s)."""

    __tablename__ = "speaking_part_1_2"

    id: Mapped[int] = mapped_column(primary_key=True)
    speaking_test_id: Mapped[str] = mapped_column(
        ForeignKey("speaking_tests.id", ondelete="CASCADE"), unique=True
    )
    external_id: Mapped[Optional[str]] = mapped_column(String(128), nullable=True, index=True)
    question_describe_id: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    question_2_id: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    question_3_id: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)

    image_1_url: Mapped[str] = mapped_column(String(500), nullable=False)
    image_2_url: Mapped[str] = mapped_column(String(500), nullable=False)

    question_describe: Mapped[str] = mapped_column(Text, nullable=False)  # 45s
    question_2: Mapped[str] = mapped_column(Text, nullable=False)  # 30s
    question_3: Mapped[str] = mapped_column(Text, nullable=False)  # 30s

    prep_seconds: Mapped[int] = mapped_column(Integer, default=0)
    describe_answer_seconds: Mapped[int] = mapped_column(Integer, default=45)
    answer_seconds: Mapped[int] = mapped_column(Integer, default=30)

    test: Mapped["SpeakingTest"] = relationship(back_populates="part_1_2")


class SpeakingPart2(Base, TimestampMixin):
    """Part 2 — B2. 1 rasm + 3 savol. Tayyorgarlik 1 daqiqa, javob 2 daqiqa (UMUMIY)."""

    __tablename__ = "speaking_part_2"

    id: Mapped[int] = mapped_column(primary_key=True)
    speaking_test_id: Mapped[str] = mapped_column(
        ForeignKey("speaking_tests.id", ondelete="CASCADE"), unique=True
    )
    external_id: Mapped[Optional[str]] = mapped_column(String(128), nullable=True, index=True)
    question_1_id: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    question_2_id: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    question_3_id: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)

    image_url: Mapped[str] = mapped_column(String(500), nullable=False)
    question_1: Mapped[str] = mapped_column(Text, nullable=False)
    question_2: Mapped[str] = mapped_column(Text, nullable=False)
    question_3: Mapped[str] = mapped_column(Text, nullable=False)

    prep_seconds: Mapped[int] = mapped_column(Integer, default=60)
    answer_seconds: Mapped[int] = mapped_column(Integer, default=120)

    test: Mapped["SpeakingTest"] = relationship(back_populates="part_2")


class SpeakingPart3(Base, TimestampMixin):
    """Part 3 — C1. Mavzu + FOR/AGAINST ro'yxati. Tayyorgarlik 1 daqiqa, javob 2 daqiqa."""

    __tablename__ = "speaking_part_3"

    id: Mapped[int] = mapped_column(primary_key=True)
    speaking_test_id: Mapped[str] = mapped_column(
        ForeignKey("speaking_tests.id", ondelete="CASCADE"), unique=True
    )
    external_id: Mapped[Optional[str]] = mapped_column(String(128), nullable=True, index=True)
    topic_id: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)

    topic: Mapped[str] = mapped_column(Text, nullable=False)
    for_points: Mapped[List[str]] = mapped_column(JSON, nullable=False)
    against_points: Mapped[List[str]] = mapped_column(JSON, nullable=False)

    prep_seconds: Mapped[int] = mapped_column(Integer, default=60)
    answer_seconds: Mapped[int] = mapped_column(Integer, default=120)

    test: Mapped["SpeakingTest"] = relationship(back_populates="part_3")


class SpeakingSubmission(Base, TimestampMixin):
    """Foydalanuvchining audio formatdagi javob yozuvi (bitta savol = bitta yozuv)."""

    __tablename__ = "speaking_submissions"

    id: Mapped[int] = mapped_column(primary_key=True)

    user_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True, native_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    mock_skill_attempt_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("mock_skill_attempts.id", ondelete="CASCADE"), nullable=True
    )
    practice_attempt_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("practice_attempts.id", ondelete="SET NULL"), nullable=True, index=True
    )
    # RESTRICT: yozuvi bor testni o'chirib bo'lmaydi (soft delete: is_active=False).
    speaking_test_id: Mapped[str] = mapped_column(
        ForeignKey("speaking_tests.id", ondelete="RESTRICT")
    )
    question_external_id: Mapped[Optional[str]] = mapped_column(String(128), nullable=True, index=True)
    part_external_id: Mapped[Optional[str]] = mapped_column(String(128), nullable=True, index=True)

    audio_url: Mapped[str] = mapped_column(String(500), nullable=False)
    # Server-generated physical filename/key; never a client-controlled path.
    audio_storage_key: Mapped[Optional[str]] = mapped_column(String(255), nullable=True, index=True)
    duration_seconds: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)

    status: Mapped[str] = mapped_column(
        String(20), default="pending", index=True
    )  # pending | checked | draft

    result: Mapped[Optional["SpeakingResult"]] = relationship(
        back_populates="submission", uselist=False,
        cascade="all, delete-orphan",
    )

    user: Mapped[User] = relationship(lazy="selectin")

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

    __table_args__ = (
        # NULL mock_skill_attempt_id (legacy) qatorlar bunga tushmaydi (PostgreSQL).
        UniqueConstraint(
            "mock_skill_attempt_id", "part_external_id", "question_external_id",
            name="uq_speaking_submission_attempt_question",
        ),
        UniqueConstraint(
            "practice_attempt_id", "part_external_id", "question_external_id",
            name="uq_speaking_submission_practice_question",
        ),
    )


class SpeakingRecordingDraft(Base, TimestampMixin):
    """Server-side speaking recording recovery state.

    Har nomzod/savol uchun bitta draft. Audio chunklar diskda saqlanadi;
    `chunk_urls` ustunida chunk fayllarining DISKDAGI YO'LLARI turadi
    (ustun nomi legacy sabab o'zgartirilmadi; eski yozuvlarda URL bo'lishi
    mumkin — services._chunk_path ikkalasini ham qabul qiladi).
    """

    __tablename__ = "speaking_recording_drafts"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True, native_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    mock_skill_attempt_id: Mapped[int] = mapped_column(
        ForeignKey("mock_skill_attempts.id", ondelete="CASCADE"), index=True
    )
    speaking_test_id: Mapped[str] = mapped_column(
        ForeignKey("speaking_tests.id", ondelete="RESTRICT")
    )
    part_external_id: Mapped[str] = mapped_column(String(128), nullable=False)
    question_external_id: Mapped[str] = mapped_column(String(128), nullable=False)
    chunk_urls: Mapped[list] = mapped_column(JSON, default=list, nullable=False)
    next_chunk_index: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    total_bytes: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    duration_seconds: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    status: Mapped[str] = mapped_column(String(20), default="recording", index=True)

    __table_args__ = (
        UniqueConstraint(
            "mock_skill_attempt_id", "part_external_id", "question_external_id",
            name="uq_speaking_recording_draft_attempt_question",
        ),
    )


class SpeakingResult(Base, TimestampMixin):
    """Baholovchi (TEACHER) tomonidan qo'lda kiritilgan natija.

    Natija URINISHGA tegishli: baholovchi bir marta ball kiritadi va u shu
    urinishning barcha savol-yozuvlariga bir xil yoziladi (services.submit_result).
    """

    __tablename__ = "speaking_results"

    id: Mapped[int] = mapped_column(primary_key=True)

    submission_id: Mapped[int] = mapped_column(
        ForeignKey("speaking_submissions.id", ondelete="CASCADE"), unique=True
    )
    evaluator_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        Uuid(as_uuid=True, native_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )

    fluency_coherence: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    lexical_resource: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    grammar_accuracy: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    pronunciation: Mapped[Optional[float]] = mapped_column(Float, nullable=True)

    overall_band: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    cefr_level: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)
    comment: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    submission: Mapped["SpeakingSubmission"] = relationship(back_populates="result")