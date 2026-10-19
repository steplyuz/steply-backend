import uuid
from datetime import datetime
from enum import Enum as PyEnum
from typing import List, Optional

from sqlalchemy import (
    Uuid,
    BigInteger,
    Boolean,
    Date,
    DateTime,
    Enum,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
    Index,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base


# =========================
# Mixins
# =========================


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )


# =========================
# Enums
# =========================


class ContactType(str, PyEnum):
    EMAIL = "email"
    PHONE = "phone"


class UserRole(str, PyEnum):
    STUDENT = "student"
    ADMIN = "admin"
    SUPER_ADMIN = "super_admin"
    TEACHER = "teacher"


class Gender(str, PyEnum):
    MALE = "male"
    FEMALE = "female"


class OtpPurpose(str, PyEnum):
    REGISTER = "register"
    RESET_PASSWORD = "reset_password"


class OtpChannel(str, PyEnum):
    SMS = "sms"
    TELEGRAM = "telegram"


class Region(str, PyEnum):
    TASHKENT_CITY = "tashkent_city"
    TASHKENT_REGION = "tashkent_region"
    ANDIJAN = "andijan"
    BUKHARA = "bukhara"
    FERGANA = "fergana"
    JIZZAKH = "jizzakh"
    KASHKADARYA = "kashkadarya"
    NAVOIY = "navoiy"
    NAMANGAN = "namangan"
    SAMARKAND = "samarkand"
    SURKHANDARYA = "surkhandarya"
    SYRDARYA = "syrdarya"
    KHOREZM = "khorezm"
    KARAKALPAKSTAN = "karakalpakstan"


# =========================
# Models
# =========================


class User(Base, TimestampMixin):
    __tablename__ = "users"

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True, native_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )

    is_active: Mapped[bool] = mapped_column(
        Boolean,
        default=True,
        nullable=False,
    )

    # Admin-created accounts may authenticate once with a server-generated
    # temporary password and must change it before normal protected operations.
    force_password_change: Mapped[bool] = mapped_column(
        Boolean, default=False, nullable=False, server_default="0"
    )

    global_role: Mapped[UserRole] = mapped_column(
        Enum(UserRole, native_enum=True),
        default=UserRole.STUDENT,
        nullable=False,
    )

    hashed_password: Mapped[Optional[str]] = mapped_column(
        String(255),
        nullable=True,
    )

    last_login_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    # Telegramning barqaror raqamli user ID'si (from_user.id). Username /
    # ism / telefon identifikator EMAS — ular o'zgarishi yoki boshqaga
    # o'tishi mumkin. Bitta Telegram akkaunt ko'pi bilan bitta Steply
    # akkauntga bog'lanadi (UNIQUE).
    telegram_user_id: Mapped[Optional[int]] = mapped_column(
        BigInteger,
        unique=True,
        index=True,
        nullable=True,
    )

    profile: Mapped[Optional["UserProfile"]] = relationship(
        back_populates="user",
        cascade="all, delete-orphan",
        uselist=False,
    )

    contacts: Mapped[List["UserContact"]] = relationship(
        back_populates="user",
        cascade="all, delete-orphan",
        lazy="selectin",
    )

    sessions: Mapped[List["UserSession"]] = relationship(
        back_populates="user",
        cascade="all, delete-orphan",
    )

    identities: Mapped[List["Identity"]] = relationship(
        back_populates="user",
        cascade="all, delete-orphan",
        lazy="selectin",
    )


class UserProfile(Base, TimestampMixin):
    __tablename__ = "user_profiles"

    user_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True, native_uuid=True), ForeignKey("users.id", ondelete="CASCADE"),
        primary_key=True,
    )

    full_name: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    username: Mapped[Optional[str]] = mapped_column(String(50), unique=True, nullable=True)
    avatar_url: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)
    bio: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    birth_date: Mapped[Optional[datetime]] = mapped_column(Date, nullable=True)

    gender: Mapped[Optional[Gender]] = mapped_column(
        Enum(Gender, native_enum=True),
        nullable=True,
    )

    # DIQQAT: `mk_id` ustuni BU YERDA EMAS.
    # Ilgari MK ID har bir foydalanuvchiga ro'yxatdan o'tishda (signup)
    # bir marta, umrbod beriladigan kod sifatida shu profilda saqlanardi.
    # Endi MK ID — IMTIHON SEANSIGA tegishli: ochiq seansga yozilganda
    # `mock_registrations.student_id` da generatsiya qilinadi
    # (qarang: mock_test/mk_id.py va mock_test/registration_service.py).
    # Sabab: bitta odam turli seanslarga yozilishi mumkin, MK ID esa
    # aynan o'sha imtihon kunidagi ishtirokni bildiradi.

    language: Mapped[Optional[str]] = mapped_column(String(10), nullable=True)
    timezone: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)

    user: Mapped["User"] = relationship(back_populates="profile", uselist=False)


# =========================
# Contacts (Phone / Email)
# =========================


class UserContact(Base, TimestampMixin):
    __tablename__ = "user_contacts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)

    user_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True, native_uuid=True), ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    contact_type: Mapped[ContactType] = mapped_column(
        Enum(ContactType, native_enum=True),
        nullable=False,
    )

    value: Mapped[str] = mapped_column(String(255), nullable=False)

    is_verified: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    is_primary: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    __table_args__ = (
        UniqueConstraint("contact_type", "value", name="uq_contact_type_value"),
        # PostgreSQL uchun partial unique index (SQLite uchun ishlamaydi — migration kerak)
        # Index("uq_user_primary_contact", "user_id", unique=True,
        #       postgresql_where=text("is_primary = true")),
    )

    user: Mapped["User"] = relationship(back_populates="contacts")


# =========================
# External Identities
# =========================


class Identity(Base, TimestampMixin):
    """External authentication identity linked to an application User.

    Provider-specific identifiers are intentionally kept out of User so that
    Telegram can later coexist with Google, Apple, email, etc.
    """

    __tablename__ = "identities"

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True, native_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )

    user_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True, native_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    provider: Mapped[str] = mapped_column(String(32), nullable=False)
    provider_user_id: Mapped[str] = mapped_column(String(255), nullable=False)

    user: Mapped["User"] = relationship(back_populates="identities")

    __table_args__ = (
        UniqueConstraint(
            "provider",
            "provider_user_id",
            name="uq_identity_provider_user",
        ),
        Index(
            "ix_identity_user_provider",
            "user_id",
            "provider",
        ),
    )


# =========================
# Telegram OIDC transactions
# =========================


class TelegramOIDCTransaction(Base):
    """Short-lived server-side state for one Telegram OIDC authorization."""

    __tablename__ = "telegram_oidc_transactions"

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True, native_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )

    state_hash: Mapped[str] = mapped_column(
        String(64), unique=True, index=True, nullable=False
    )
    nonce_hash: Mapped[str] = mapped_column(
        String(64), nullable=False
    )
    nonce_encrypted: Mapped[str] = mapped_column(
        Text, nullable=False
    )
    browser_secret_hash: Mapped[str] = mapped_column(
        String(64), nullable=False
    )
    code_verifier_encrypted: Mapped[str] = mapped_column(
        Text, nullable=False
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), index=True, nullable=False
    )
    used_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )


# =========================
# Sessions
# =========================


class UserSession(Base, TimestampMixin):
    __tablename__ = "user_sessions"

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True, native_uuid=True), primary_key=True, default=uuid.uuid4
    )

    user_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True, native_uuid=True), ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    session_family_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True, native_uuid=True),
        nullable=False,
        index=True,
        default=uuid.uuid4,
    )

    rotated_from_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        Uuid(as_uuid=True, native_uuid=True),
        nullable=True,
    )

    refresh_token_hash: Mapped[str] = mapped_column(
        String(128), nullable=False, index=True
    )

    user_agent: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    ip_address: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)

    expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        index=True,
    )

    is_revoked: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    user: Mapped["User"] = relationship(back_populates="sessions")


# =========================
# OTP Kodlar
# =========================


class VerificationCode(Base, TimestampMixin):
    __tablename__ = "verification_codes"

    id: Mapped[int] = mapped_column(primary_key=True)
    target: Mapped[str] = mapped_column(String(255), index=True)

    purpose: Mapped[OtpPurpose] = mapped_column(
        Enum(OtpPurpose, native_enum=True),
        default=OtpPurpose.REGISTER,
        nullable=False,
    )
    channel: Mapped[OtpChannel] = mapped_column(
        Enum(OtpChannel, native_enum=True),
        default=OtpChannel.SMS,
        nullable=False,
    )

    code_hash: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    failed_attempts: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    # Necha marta kod yuborilgani (resend cheklovi uchun)
    send_count: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    last_sent_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    # Deep-link secret is stored only as SHA-256. The raw token exists only in the URL.
    bot_token: Mapped[Optional[str]] = mapped_column(
        String(64), unique=True, nullable=True, index=True
    )
    telegram_chat_id: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    telegram_user_id: Mapped[Optional[int]] = mapped_column(BigInteger, nullable=True, index=True)

    # The OTP is hashed for verification and encrypted for the short-lived
    # Telegram "copy code" operation. It is never logged.
    code_encrypted: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    # Temporary registration payload. Telegram profile data is added after /start.
    payload: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    is_used: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    __table_args__ = (Index("idx_active_code", "target", "purpose", "is_used"),)


class OtpSendLimit(Base, TimestampMixin):
    """OTP yuborish hisoblagichi (telefon + IP juftligi bo'yicha).

    Bloklash egasiga emas, aynan so'rov yuborayotgan IP'ga qo'llanadi: begona
    kishi boshqa raqam uchun ham, egasining raqami uchun ham o'z IP'idan limitga
    tushadi, egasi esa o'z IP'idan bemalol kod olishda davom etadi.
    purpose matn sifatida saqlanadi (OtpPurpose.value) — Postgres enum turi bilan
    to'qnashmaslik uchun.
    """

    __tablename__ = "otp_send_limits"
    __table_args__ = (
        UniqueConstraint("target", "purpose", "ip_hash", name="uq_otp_send_limits_target_purpose_ip"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    target: Mapped[str] = mapped_column(String(255), nullable=False)
    purpose: Mapped[str] = mapped_column(String(32), nullable=False)
    ip_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    send_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    window_started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
