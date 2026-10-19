"""
paper/models.py
================
Listening va Reading modullari uchun umumiy PAPER-BASED EXAM qatlami:

  - ExamVersion   — savollar/answer key snapshoti (lock qilingach o'zgarmaydi)
  - OMRSubmission — skanerlangan/fotosurat qilingan javoblar varag'i natijasi

Bu modellar `listening_tests` / `reading_tests` jadvallariga real FK bilan
bog'lanmaydi (chunki `exam_id` ikkala modulda ham alohida jadvalga ishora
qiladi) — buning o'rniga `module` + `exam_id` juftligi orqali mantiqan
bog'lanadi. Bu ENWIS/boshqa modullardagi mavjud konvensiyalarga mos.
"""
import enum

from sqlalchemy import (
    Uuid,
    JSON, Boolean, Column, DateTime, ForeignKey, Integer, String, Enum, func,
    UniqueConstraint,
)
from sqlalchemy.orm import relationship
from sqlalchemy.schema import Index

from app.core.database import Base


class PaperModule(str, enum.Enum):
    LISTENING = "LISTENING"
    READING = "READING"
    WRITING = "WRITING"
    SPEAKING = "SPEAKING"


class OMRSubmissionStatus(str, enum.Enum):
    PENDING = "PENDING"
    PROCESSING = "PROCESSING"
    PROCESSED = "PROCESSED"
    NEEDS_REVIEW = "NEEDS_REVIEW"
    FAILED = "FAILED"
    PUBLISHED = "PUBLISHED"


class ExamVersion(Base):
    """
    Har bir lock qilingan (yoki lock qilinishi kutilayotgan) savollar to'plami
    uchun immutable snapshot. `answer_key` — {"<question_number>": <answer>}.
    <answer> Listening uchun string, Reading uchun list[str] bo'lishi mumkin —
    shuning uchun JSON.
    """
    __tablename__ = "exam_versions"
    __table_args__ = (
        UniqueConstraint("module", "exam_id", "version_number", name="uq_exam_version"),
    )

    id = Column(Integer, primary_key=True, index=True)
    module = Column(Enum(PaperModule, native_enum=False), nullable=False, index=True)
    exam_id = Column(String, nullable=False, index=True)

    version_number = Column(Integer, nullable=False, default=1)
    total_questions = Column(Integer, nullable=False, default=0)
    answer_key = Column(JSON, nullable=False, default=dict)

    is_locked = Column(Boolean, default=False, nullable=False)
    locked_at = Column(DateTime(timezone=True), nullable=True)
    locked_by_user_id = Column(Uuid(as_uuid=True, native_uuid=True), ForeignKey("users.id"), nullable=True)

    # OMR Answer Sheet PDF birinchi marta generatsiya qilinganda shu yerga
    # "muzlatiladi" — shu bilan Answer Sheet generator va OMR scanner doim
    # bir xil koordinatalar bilan ishlaydi (Band 11).
    answer_sheet_layout = Column(JSON, nullable=True)

    created_at = Column(DateTime(timezone=True), server_default=func.now())

    submissions = relationship(
        "OMRSubmission", back_populates="exam_version", cascade="all, delete-orphan"
    )


class OMRSubmission(Base):
    """
    OMR bosqichi (PDF/bubble-detection) keyingi fazada shu jadvalga yoziladi.
    Hozircha faqat struktura tayyorlanadi — grading logikasi Faza 3 da qo'shiladi.
    """
    __tablename__ = "omr_submissions"
    __table_args__ = (
        UniqueConstraint(
            "registration_id", "module", "exam_version_id",
            name="uq_omr_registration_module_version",
        ),
        Index("ix_omr_submission_status_created", "status", "created_at"),
    )

    id = Column(Integer, primary_key=True, index=True)
    module = Column(Enum(PaperModule, native_enum=False), nullable=False, index=True)
    exam_id = Column(String, nullable=False, index=True)
    exam_version_id = Column(
        Integer, ForeignKey("exam_versions.id", ondelete="RESTRICT"), nullable=False
    )

    candidate_id = Column(String, nullable=False, index=True)
    registration_id = Column(
        Integer, ForeignKey("mock_registrations.id", ondelete="SET NULL"), nullable=True, index=True
    )
    image_paths = Column(JSON, nullable=False, default=list)

    detected_answers = Column(JSON, nullable=True)      # {"1": "B", ...}
    per_question_result = Column(JSON, nullable=True)   # [{question, detected, correct, result}]

    correct_count = Column(Integer, default=0)
    wrong_count = Column(Integer, default=0)
    blank_count = Column(Integer, default=0)
    invalid_count = Column(Integer, default=0)
    total_score = Column(Integer, default=0)

    status = Column(
        Enum(OMRSubmissionStatus, native_enum=False),
        default=OMRSubmissionStatus.PENDING,
        nullable=False,
    )
    processed_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    exam_version = relationship("ExamVersion", back_populates="submissions")
