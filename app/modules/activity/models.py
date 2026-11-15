import uuid
from datetime import date, datetime

from sqlalchemy import Date, DateTime, ForeignKey, Integer, UniqueConstraint, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class UserActivityDaily(Base):
    """Foydalanuvchining bir kunlik saytdagi faol vaqti (Asia/Tashkent sanasi bo'yicha)."""

    __tablename__ = "user_activity_daily"
    __table_args__ = (UniqueConstraint("user_id", "day", name="uq_user_activity_user_day"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True, native_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    day: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    active_seconds: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    heartbeats: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
