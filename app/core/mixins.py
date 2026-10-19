"""
Umumiy mixinlar — barcha exam modullari (reading/listening/writing/
speaking/mock/paper_test) shu yerdan meros oladi.

Maqsad: modellarning STRUKTURAVIY qismi (id turi, timestamp, flag'lar,
natija maydonlari) hamma joyda bir xil bo'lsin; domenga xos maydonlar
(masalan reading'da total_questions, listening'da audio_mode) har bir
modulning o'zida qoladi — ular o'z yo'nalishi bo'yicha erkin.

Uslub: hamma joyda zamonaviy `Mapped`/`mapped_column` (speaking'dagi
uslub) standart qilib olinadi; eski `Column(...)` uslubi bosqichma-
bosqich shunga o'tkaziladi.
"""

import uuid
from datetime import datetime
from typing import Optional

from sqlalchemy import Boolean, DateTime, ForeignKey, Float, Integer, JSON, String, Uuid
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.sql import func


# ==========================================================
# 1. TIMESTAMP — hamma jadvalda bir xil
# ==========================================================

class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )


# ==========================================================
# 2. EXAM ID — Test-darajasidagi jadvallar uchun standart slug-id
#    (reading_tests.id, listening_tests.id, speaking_tests.id,
#     writing_exams.id, mock_exams.id) — hammasi String(64), limit bilan.
# ==========================================================

class ExamIdMixin:
    id: Mapped[str] = mapped_column(String(64), primary_key=True, index=True)


# ==========================================================
# 3. EXAM FLAGS — is_demo/is_free/is_mock/is_active barcha test
#    jadvallarida bir xil ma'noda ishlatiladi.
# ==========================================================



class ExamFlagsMixin:
    is_demo: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    is_free: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    is_practice: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False, index=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)


# ==========================================================
# 4. RESULT BASE — reading_results / listening_results /
#    writing_results / speaking_results / paper natijalari uchun
#    umumiy o'zak. Har biri o'z FK'sini (test_id/exam_id) va domenga
#    xos maydonlarini (masalan writing'da scaled_score/raw_score 0-48)
#    o'zi qo'shadi.
# ==========================================================

class ResultBaseMixin(TimestampMixin):
    id: Mapped[int] = mapped_column(Integer, primary_key=True)

    user_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True, native_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    exam_attempt_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("mock_exam_attempts.id", ondelete="CASCADE"), nullable=True
    )

    raw_score: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    standard_score: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    cefr_level: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)

    user_answers: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)
