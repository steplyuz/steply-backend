"""Saytdagi faollik: frontend har ~60 soniyada heartbeat yuboradi."""
import uuid
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.activity.models import UserActivityDaily

TASHKENT = ZoneInfo("Asia/Tashkent")
# Ikki heartbeat orasidagi uzilish bundan katta bo'lsa, o'sha vaqt hisobga olinmaydi
# (tab yopilgan, telefon uyquda va h.k.). Client 60s da yuboradi, 120s zaxira.
MAX_GAP_SECONDS = 120


def _aware(dt: datetime | None) -> datetime | None:
    if dt is None:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


class ActivityService:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def heartbeat(self, user_id: uuid.UUID) -> None:
        now = datetime.now(timezone.utc)
        today = now.astimezone(TASHKENT).date()

        row = (
            await self.db.execute(
                select(UserActivityDaily).where(
                    UserActivityDaily.user_id == user_id,
                    UserActivityDaily.day == today,
                )
            )
        ).scalar_one_or_none()

        if row is None:
            self.db.add(
                UserActivityDaily(
                    user_id=user_id, day=today, active_seconds=0, heartbeats=1, last_seen_at=now
                )
            )
        else:
            gap = (now - _aware(row.last_seen_at)).total_seconds()
            if 0 < gap <= MAX_GAP_SECONDS:
                row.active_seconds += int(gap)
            row.heartbeats += 1
            row.last_seen_at = now

        try:
            await self.db.commit()
        except IntegrityError:
            # Ikkinchi tab/so'rov shu kunning qatorini bir vaqtda yaratgan: bu heartbeat tashlanadi.
            await self.db.rollback()
