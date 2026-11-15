"""Admin statistikasi: faol vaqt va yechilgan testlar."""
import uuid
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from sqlalchemy import distinct, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.modules.activity.models import UserActivityDaily
from app.modules.auth.models import User, UserProfile
from app.modules.exams.mock.models import (
    MockSkillAttempt, PracticeAttempt, PracticeAttemptStatus, SkillType,
)
from app.modules.tests.speaking.models import SpeakingSubmission
from app.modules.tests.writing.models import WritingResult

TASHKENT = ZoneInfo("Asia/Tashkent")
SKILLS = ("reading", "listening", "writing", "speaking")
UAD = UserActivityDaily


def _empty() -> dict[str, int]:
    return {s: 0 for s in SKILLS}


def _today() -> date:
    return datetime.now(TASHKENT).date()


def _utc_start(d: date) -> datetime:
    return datetime(d.year, d.month, d.day, tzinfo=TASHKENT).astimezone(timezone.utc)


def _naive_utc(dt: datetime) -> datetime:
    """Mock natijalaridagi DateTime (timezone'siz) uchun."""
    return dt.astimezone(timezone.utc).replace(tzinfo=None)


def _aware(dt: datetime | None) -> datetime | None:
    if dt is None:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _skill_key(sk) -> str:
    return getattr(sk, "value", str(sk)).lower()


class ActivityStatsService:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def _scalar(self, stmt) -> int:
        return int((await self.db.scalar(stmt)) or 0)

    async def test_counts(
        self, *, since: datetime | None = None, user_id: uuid.UUID | None = None
    ) -> dict[uuid.UUID, dict[str, int]]:
        """user_id -> {skill: yakunlangan testlar soni}.

        reading/listening: practice (SUBMITTED) + mock reading/listening (submitted_at bor).
        writing: writing_results. speaking: speaking_submissions.
        """
        counts: dict[uuid.UUID, dict[str, int]] = defaultdict(_empty)

        # 1) Practice reading / listening
        stmt = (
            select(PracticeAttempt.user_id, PracticeAttempt.skill, func.count())
            .where(PracticeAttempt.status == PracticeAttemptStatus.SUBMITTED)
        )
        if since:
            stmt = stmt.where(PracticeAttempt.submitted_at >= since)
        if user_id:
            stmt = stmt.where(PracticeAttempt.user_id == user_id)
        stmt = stmt.group_by(PracticeAttempt.user_id, PracticeAttempt.skill)
        for uid, sk, n in (await self.db.execute(stmt)).all():
            counts[uid][_skill_key(sk)] += int(n)

        # 2) Mock reading / listening
        stmt = (
            select(MockSkillAttempt.user_id, MockSkillAttempt.skill, func.count())
            .where(
                MockSkillAttempt.submitted_at.is_not(None),
                MockSkillAttempt.skill.in_([SkillType.READING, SkillType.LISTENING]),
            )
        )
        if since:
            stmt = stmt.where(MockSkillAttempt.submitted_at >= _naive_utc(since))
        if user_id:
            stmt = stmt.where(MockSkillAttempt.user_id == user_id)
        stmt = stmt.group_by(MockSkillAttempt.user_id, MockSkillAttempt.skill)
        for uid, sk, n in (await self.db.execute(stmt)).all():
            counts[uid][_skill_key(sk)] += int(n)

        # 3) Writing (practice + mock)
        stmt = select(WritingResult.user_id, func.count()).group_by(WritingResult.user_id)
        if since:
            stmt = stmt.where(WritingResult.created_at >= since)
        if user_id:
            stmt = stmt.where(WritingResult.user_id == user_id)
        for uid, n in (await self.db.execute(stmt)).all():
            counts[uid]["writing"] += int(n)

        # 4) Speaking
        stmt = select(SpeakingSubmission.user_id, func.count()).group_by(SpeakingSubmission.user_id)
        if since:
            stmt = stmt.where(SpeakingSubmission.created_at >= since)
        if user_id:
            stmt = stmt.where(SpeakingSubmission.user_id == user_id)
        for uid, n in (await self.db.execute(stmt)).all():
            counts[uid]["speaking"] += int(n)

        return dict(counts)

    async def _activity_totals(
        self, *, since_day: date | None = None, user_id: uuid.UUID | None = None
    ) -> dict[uuid.UUID, tuple[int, datetime | None]]:
        """user_id -> (jami faol soniya, oxirgi faollik)."""
        stmt = select(
            UAD.user_id,
            func.sum(UAD.active_seconds),
            func.max(UAD.last_seen_at),
        ).group_by(UAD.user_id)
        if since_day:
            stmt = stmt.where(UAD.day >= since_day)
        if user_id:
            stmt = stmt.where(UAD.user_id == user_id)
        rows = (await self.db.execute(stmt)).all()
        return {uid: (int(sec or 0), _aware(last)) for uid, sec, last in rows}

    async def overview(self) -> dict:
        today = _today()
        d7 = today - timedelta(days=6)
        d30 = today - timedelta(days=29)
        uid = UAD.user_id

        active_today = await self._scalar(select(func.count(distinct(uid))).where(UAD.day == today))
        active_7d = await self._scalar(select(func.count(distinct(uid))).where(UAD.day >= d7))
        active_30d = await self._scalar(select(func.count(distinct(uid))).where(UAD.day >= d30))
        seconds_30d = await self._scalar(
            select(func.coalesce(func.sum(UAD.active_seconds), 0)).where(UAD.day >= d30)
        )

        all_counts = await self.test_counts()
        counts_30d = await self.test_counts(since=_utc_start(d30))

        totals_all, totals_30d = _empty(), _empty()
        for c in all_counts.values():
            for s in SKILLS:
                totals_all[s] += c[s]
        for c in counts_30d.values():
            for s in SKILLS:
                totals_30d[s] += c[s]

        return {
            "total_users": await self._scalar(select(func.count(User.id))),
            "active_today": active_today,
            "active_7d": active_7d,
            "active_30d": active_30d,
            "time_30d_seconds": seconds_30d,
            "avg_time_per_active_user_30d_seconds": seconds_30d // active_30d if active_30d else 0,
            "test_takers_all_time": len(all_counts),
            "test_takers_30d": len(counts_30d),
            "tests_all_time": {**totals_all, "total": sum(totals_all.values())},
            "tests_30d": {**totals_30d, "total": sum(totals_30d.values())},
        }

    async def activity_by_day(self, days: int) -> list[dict]:
        today = _today()
        start = today - timedelta(days=days - 1)
        rows = (
            await self.db.execute(
                select(UAD.day, func.count(UAD.user_id), func.sum(UAD.active_seconds))
                .where(UAD.day >= start)
                .group_by(UAD.day)
            )
        ).all()
        by_day = {d: (int(n), int(s or 0)) for d, n, s in rows}

        points, d = [], start
        while d <= today:
            active_users, seconds = by_day.get(d, (0, 0))
            points.append({"date": d.isoformat(), "active_users": active_users, "active_seconds": seconds})
            d += timedelta(days=1)
        return points

    async def users_table(
        self, *, sort: str, desc: bool, search: str | None, offset: int, limit: int
    ) -> dict:
        activity = await self._activity_totals()
        counts = await self.test_counts()

        stmt = select(User).options(selectinload(User.profile))
        if search:
            q = f"%{search.strip()}%"
            stmt = stmt.outerjoin(UserProfile).where(
                or_(UserProfile.full_name.ilike(q), UserProfile.username.ilike(q))
            )
        users = (await self.db.execute(stmt)).scalars().all()

        rows = []
        for u in users:
            c = counts.get(u.id, _empty())
            seconds, last_seen = activity.get(u.id, (0, None))
            rows.append({
                "id": str(u.id),
                "full_name": u.profile.full_name if u.profile else None,
                "username": u.profile.username if u.profile else None,
                "role": u.global_role.value,
                "created_at": u.created_at,
                "last_login_at": u.last_login_at,
                "last_seen_at": last_seen,
                "active_seconds": seconds,
                **c,
                "total_tests": sum(c.values()),
            })

        # Qiymati yo'q (None) qatorlar doim oxirida turadi
        present = [r for r in rows if r[sort] is not None]
        missing = [r for r in rows if r[sort] is None]
        present.sort(key=lambda r: r[sort], reverse=desc)
        rows = present + missing

        return {"total": len(rows), "offset": offset, "limit": limit, "items": rows[offset: offset + limit]}

    async def user_detail(self, user_id: uuid.UUID) -> dict | None:
        user = (
            await self.db.execute(
                select(User).options(selectinload(User.profile)).where(User.id == user_id)
            )
        ).scalar_one_or_none()
        if user is None:
            return None

        counts = (await self.test_counts(user_id=user_id)).get(user_id, _empty())
        seconds, last_seen = (await self._activity_totals(user_id=user_id)).get(user_id, (0, None))
        daily_rows = (
            await self.db.execute(
                select(UAD.day, UAD.active_seconds, UAD.heartbeats)
                .where(UAD.user_id == user_id, UAD.day >= _today() - timedelta(days=29))
                .order_by(UAD.day)
            )
        ).all()

        return {
            "id": str(user.id),
            "full_name": user.profile.full_name if user.profile else None,
            "username": user.profile.username if user.profile else None,
            "role": user.global_role.value,
            "last_login_at": user.last_login_at,
            "last_seen_at": last_seen,
            "active_seconds_all_time": seconds,
            "tests": {**counts, "total": sum(counts.values())},
            "daily": [
                {"date": d.isoformat(), "active_seconds": s, "heartbeats": h}
                for d, s, h in daily_rows
            ],
        }
