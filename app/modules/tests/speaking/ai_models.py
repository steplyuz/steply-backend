import uuid
from typing import Optional

from sqlalchemy import JSON, ForeignKey, Integer, String, UniqueConstraint, Uuid
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base
from app.core.mixins import TimestampMixin


class SpeakingAIJob(Base, TimestampMixin):
    """Bitta practice urinish uchun AI baholash ishi."""
    __tablename__ = "speaking_ai_jobs"

    id: Mapped[int] = mapped_column(primary_key=True)
    practice_attempt_id: Mapped[int] = mapped_column(
        ForeignKey("practice_attempts.id", ondelete="CASCADE"), unique=True
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True, native_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    status: Mapped[str] = mapped_column(String(16), default="queued", index=True)  # queued|running|done|failed
    error: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)

    evaluations: Mapped[list["SpeakingAIEvaluation"]] = relationship(
        back_populates="job", cascade="all, delete-orphan", lazy="selectin"
    )


class SpeakingAIEvaluation(Base, TimestampMixin):
    __tablename__ = "speaking_ai_evaluations"
    __table_args__ = (UniqueConstraint("job_id", "part", name="uq_speaking_ai_job_part"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    job_id: Mapped[int] = mapped_column(ForeignKey("speaking_ai_jobs.id", ondelete="CASCADE"), index=True)
    part: Mapped[str] = mapped_column(String(8))
    score: Mapped[int] = mapped_column(Integer)
    max_score: Mapped[int] = mapped_column(Integer)
    level: Mapped[str] = mapped_column(String(32))
    payload: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)

    job: Mapped[SpeakingAIJob] = relationship(back_populates="evaluations")
