"""Xabarnomalar: shablonlar, navbat (outbox), email hisoblari (Gmail/Resend), hodisa qoidalari, opt-out."""
from __future__ import annotations

import uuid
from datetime import datetime
from typing import Optional

from sqlalchemy import Boolean, DateTime, ForeignKey, Index, Integer, String, Text, UniqueConstraint, Uuid, false, func
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class NotificationChannel:
    SMS = "sms"
    TELEGRAM = "telegram"
    EMAIL = "email"
    # Ilova ichidagi bildirishnoma (foydalanuvchi inbox'i). Tashqi API chaqirmaydi.
    IN_APP = "in_app"
    ALL = ("sms", "telegram", "email", "in_app")


class NotificationStatus:
    QUEUED = "queued"
    SENT = "sent"
    FAILED = "failed"
    CANCELLED = "cancelled"


class NotificationCategory:
    # Tranzaksion: buyurtma, ro'yxatdan o'tish — doim yuboriladi
    TRANSACTIONAL = "transactional"
    # Reklama/yangilik: opt-out qilganlarga yuborilmaydi, unsubscribe havolasi qo'shiladi
    MARKETING = "marketing"


class EmailAccountStatus:
    OK = "ok"
    REAUTH_REQUIRED = "reauth_required"


class NotificationTemplate(Base):
    __tablename__ = "notification_templates"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    code: Mapped[str] = mapped_column(String(64), unique=True, index=True, nullable=False)
    channel: Mapped[str] = mapped_column(String(16), nullable=False)
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    subject: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    body: Mapped[str] = mapped_column(Text, nullable=False)
    # Email uchun HTML (ixtiyoriy). Bo'lmasa matndan avtomatik HTML yasaladi.
    html_body: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )


class EmailAccount(Base):
    """Admin ulagan yuboruvchi email (hozircha Gmail OAuth). Refresh token shifrlangan holda saqlanadi."""

    __tablename__ = "email_accounts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    provider: Mapped[str] = mapped_column(String(16), nullable=False)
    email: Mapped[str] = mapped_column(String(255), unique=True, nullable=False)
    display_name: Mapped[Optional[str]] = mapped_column(String(120), nullable=True)
    refresh_token_encrypted: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    is_default: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    status: Mapped[str] = mapped_column(String(24), default=EmailAccountStatus.OK, nullable=False)
    last_error: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )


class NotificationRule(Base):
    """Hodisa -> shablon. Masalan: order_paid -> email -> order_receipt."""

    __tablename__ = "notification_rules"
    __table_args__ = (UniqueConstraint("event_code", "channel", name="uq_notification_rule_event_channel"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    event_code: Mapped[str] = mapped_column(String(64), index=True, nullable=False)
    channel: Mapped[str] = mapped_column(String(16), nullable=False)
    template_code: Mapped[str] = mapped_column(String(64), nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )


class NotificationOptOut(Base):
    """Marketing xabarlardan voz kechganlar. Manzil bo'yicha (user bo'lmagan manzillar uchun ham ishlaydi)."""

    __tablename__ = "notification_optouts"
    __table_args__ = (UniqueConstraint("channel", "recipient", name="uq_notification_optout"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    channel: Mapped[str] = mapped_column(String(16), nullable=False)
    recipient: Mapped[str] = mapped_column(String(255), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)


class NotificationMessage(Base):
    __tablename__ = "notification_messages"
    __table_args__ = (
        Index("ix_notification_messages_due", "status", "next_attempt_at"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    channel: Mapped[str] = mapped_column(String(16), nullable=False, index=True)
    # sms: telefon (998...), telegram: chat_id, email: manzil
    recipient: Mapped[str] = mapped_column(String(255), nullable=False)
    user_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        Uuid(as_uuid=True, native_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True
    )
    template_code: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    subject: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    body: Mapped[str] = mapped_column(Text, nullable=False)
    html_body: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    # in_app kanal uchun: bildirishnoma bosilganda ochiladigan havola
    link: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)
    category: Mapped[str] = mapped_column(
        String(16), default=NotificationCategory.TRANSACTIONAL, server_default=NotificationCategory.TRANSACTIONAL,
        nullable=False,
    )
    # None = default email hisob (yoki Resend fallback)
    email_account_id: Mapped[Optional[int]] = mapped_column(
        Integer, ForeignKey("email_accounts.id", ondelete="SET NULL"), nullable=True
    )
    status: Mapped[str] = mapped_column(String(16), default=NotificationStatus.QUEUED, nullable=False, index=True)
    attempts: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    max_attempts: Mapped[int] = mapped_column(Integer, default=5, nullable=False)
    next_attempt_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    last_error: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    provider_message_id: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    # Bir xil so'rov ikki marta navbatga tushmasligi uchun
    idempotency_key: Mapped[Optional[str]] = mapped_column(String(128), unique=True, nullable=True)
    created_by: Mapped[Optional[uuid.UUID]] = mapped_column(
        Uuid(as_uuid=True, native_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    sent_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)


class UserNotification(Base):
    """Foydalanuvchining ilova ichidagi bildirishnomalari (inbox)."""

    __tablename__ = "user_notifications"
    __table_args__ = (Index("ix_user_notifications_user_unread", "user_id", "is_read"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True, native_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), index=True, nullable=False
    )
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    body: Mapped[str] = mapped_column(Text, nullable=False)
    link: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)
    category: Mapped[str] = mapped_column(
        String(16), default=NotificationCategory.TRANSACTIONAL, server_default=NotificationCategory.TRANSACTIONAL,
        nullable=False,
    )
    is_read: Mapped[bool] = mapped_column(Boolean, default=False, server_default=false(), nullable=False)
    message_id: Mapped[Optional[int]] = mapped_column(
        Integer, ForeignKey("notification_messages.id", ondelete="SET NULL"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    read_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
