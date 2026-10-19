"""
exams_auth/models.py
=====================
Exams platformasi (examx.steply.uz) uchun MUSTAQIL autentifikatsiya
jadvallari.

Ataylab asosiy `app.modules.auth.models` jadvallaridan (users_sessions,
verification_codes) TO'LIQ AJRATILGAN:
  - ExamsVerificationCode  — asosiy `VerificationCode` jadvaliga tegmaydi,
    OtpPurpose enumiga ham yangi qiymat qo'shilmaydi (Postgres native enum
    migratsiyasi xavfli va keraksiz bo'lardi).
  - ExamsAuthSession       — asosiy `UserSession` jadvaliga tegmaydi, shu
    sabab Exams logout/refresh/revoke asosiy Steply seansiga ta'sir
    qilmaydi va aksincha.

Ikkalasi ham `users.id` ga FOREIGN KEY orqali bog'lanadi — foydalanuvchi
modeli TAKRORLANMAYDI, faqat mavjud userga ishora qilinadi (band 2: "Agar
umumiy User modeli mavjud bo'lsa, uni takroran yaratma").
"""

import uuid
from datetime import datetime
from typing import Optional

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, func
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy import Uuid

from app.core.database import Base


class _TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )


class ExamsVerificationCode(Base, _TimestampMixin):
    """Exams login uchun bir martalik SMS kod — asosiy VerificationCode'dan
    mustaqil. `user_id` orqali bog'lanadi (target sifatida telefon emas,
    to'g'ridan-to'g'ri user — chunki Exams login MK ID orqali user'ni
    avval aniqlab, keyin uning profildagi telefoniga kod yuboradi)."""

    __tablename__ = "exams_verification_codes"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)

    user_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True, native_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    # Kod qaysi ro'yxat (ochiq seans) uchun so'ralgani. MK ID endi
    # registrationga tegishli bo'lgani uchun, bir foydalanuvchida bir
    # nechta faol MK ID bo'lishi mumkin — kod ham o'shanga bog'lanadi.
    registration_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("mock_registrations.id", ondelete="CASCADE"),
        nullable=True,
        index=True,
    )

    # Yuborilgan telefon raqamining snapshot'i — audit uchun.
    phone_snapshot: Mapped[str] = mapped_column(String(32), nullable=False)

    code_hash: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    failed_attempts: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    send_count: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    last_sent_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    is_used: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)


class ExamsAuthSession(Base, _TimestampMixin):
    """Exams uchun mustaqil refresh-token sessiyasi.

    Asosiy `user_sessions` jadvali bilan bog'liq EMAS — shu sababli
    Exams logout asosiy Steply login holatini bekor qilmaydi va
    aksincha (band: "Exams logout boshqa Steply sessiyalarini bekor
    qilmasligi kerak").
    """

    __tablename__ = "exams_auth_sessions"

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True, native_uuid=True), primary_key=True, default=uuid.uuid4
    )

    user_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True, native_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    # Exams seansi AYNAN bitta imtihon ro'yxatiga (ochiq seansga) bog'lanadi:
    # MK ID endi userda emas, `mock_registrations.student_id` da yaratiladi,
    # ya'ni login "qaysi odam" emas, "qaysi seansdagi qaysi odam" degani.
    # Shuning uchun token ham, sessiya ham shu registration bilan bog'liq.
    registration_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("mock_registrations.id", ondelete="CASCADE"),
        nullable=True,
        index=True,
    )

    session_family_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True, native_uuid=True), nullable=False, index=True, default=uuid.uuid4
    )
    rotated_from_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        Uuid(as_uuid=True, native_uuid=True), nullable=True
    )

    refresh_token_hash: Mapped[str] = mapped_column(String(128), nullable=False, index=True)

    user_agent: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    ip_address: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)

    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)
    is_revoked: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
