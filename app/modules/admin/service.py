"""
admin/service.py
================
Admin operatsiyalari: statistika va Excel eksport.
"""

import tempfile
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import openpyxl
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.modules.auth.models import User, UserProfile, ContactType
from app.modules.exams.mock.models import MockExamResult, MockRegistration
from app.modules.admin.schemas import PeriodStats, StatisticsResponse, TrafficPoint, TrafficResponse

TASHKENT = ZoneInfo("Asia/Tashkent")


def _local_date(dt: datetime) -> date:
    """DB'dan kelgan vaqt (SQLite'da naive = UTC) -> Asia/Tashkent sanasi."""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(TASHKENT).date()


def _utc_start(d: date) -> datetime:
    return datetime(d.year, d.month, d.day, tzinfo=TASHKENT).astimezone(timezone.utc)


class AdminService:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def get_statistics(self) -> StatisticsResponse:
        total_users = (await self.db.scalar(select(func.count(User.id)))) or 0
        total_exams = (await self.db.scalar(select(func.count(MockExamResult.id)))) or 0

        today = datetime.now(TASHKENT).date()
        week_start = today - timedelta(days=today.weekday())
        month_start = today.replace(day=1)

        users_today = (
            await self.db.scalar(
                select(func.count(User.id)).where(User.created_at >= _utc_start(today))
            )
        ) or 0

        week = await self._period_stats(_utc_start(week_start))
        month = await self._period_stats(_utc_start(month_start))
        return StatisticsResponse(
            total_users=total_users,
            total_exams_taken=total_exams,
            users_today=users_today,
            week=week,
            month=month,
        )

    async def _period_stats(self, since_utc: datetime) -> PeriodStats:
        from app.modules.exams.mock.models import PracticeAttempt
        from app.modules.tests.writing.models import FeedbackSource, WritingFeedback

        async def _count(col, *extra):
            return (await self.db.scalar(select(func.count()).select_from(col.class_).where(col >= since_utc, *extra))) or 0

        return PeriodStats(
            new_users=await _count(User.created_at),
            practice_attempts=await _count(PracticeAttempt.started_at),
            mock_results=await _count(MockExamResult.created_at),
            ai_checks=await _count(WritingFeedback.created_at, WritingFeedback.source == FeedbackSource.AI),
        )

    async def get_traffic(self, period: str) -> TrafficResponse:
        """Kunlik trafik: `week` = joriy hafta (dushanba..bugun), `month` = joriy kalendar oy,
        `last7` / `last30` = oxirgi 7 / 30 kun. Hisob Asia/Tashkent sanalari bo'yicha."""
        from app.modules.exams.mock.models import PracticeAttempt
        from app.modules.tests.writing.models import FeedbackSource, WritingFeedback

        today = datetime.now(TASHKENT).date()
        if period == "week":
            start = today - timedelta(days=today.weekday())
        elif period == "month":
            start = today.replace(day=1)
        elif period == "last7":
            start = today - timedelta(days=6)
        elif period == "last30":
            start = today - timedelta(days=29)
        else:
            raise ValueError("period: week | month | last7 | last30")

        since = _utc_start(start)
        series = {
            "new_users": (User.created_at, ()),
            "practice_attempts": (PracticeAttempt.started_at, ()),
            "mock_results": (MockExamResult.created_at, ()),
            "ai_checks": (WritingFeedback.created_at, (WritingFeedback.source == FeedbackSource.AI,)),
        }
        per_day: dict[str, dict[date, int]] = {k: defaultdict(int) for k in series}
        for key, (col, extra) in series.items():
            rows = (await self.db.execute(select(col).where(col >= since, *extra))).scalars().all()
            for ts in rows:
                if ts is not None:
                    per_day[key][_local_date(ts)] += 1

        points, totals = [], {k: 0 for k in series}
        d = start
        while d <= today:
            row = {k: per_day[k].get(d, 0) for k in series}
            for k, v in row.items():
                totals[k] += v
            points.append(TrafficPoint(date=d.isoformat(), **row))
            d += timedelta(days=1)
        return TrafficResponse(
            period=period, start=start.isoformat(), end=today.isoformat(),
            points=points, totals=PeriodStats(**totals),
        )

    async def generate_users_excel(self) -> str:
        """Barcha foydalanuvchilarni Excel faylga yozib, faylning vaqtinchalik yo'lini qaytaradi."""
        stmt = (
            select(User)
            .options(selectinload(User.profile), selectinload(User.contacts))
            .order_by(User.created_at.desc())
        )
        users = (await self.db.execute(stmt)).scalars().all()

        # MK ID endi profilda emas — eng so'nggi imtihon ro'yxatidan olinadi.
        mk_rows = (
            await self.db.execute(
                select(MockRegistration.user_id, MockRegistration.student_id)
                .order_by(MockRegistration.created_at.asc())
            )
        ).all()
        mk_by_user = {user_id: student_id for user_id, student_id in mk_rows}

        wb = openpyxl.Workbook()
        ws = wb.active
        ws.title = "Foydalanuvchilar"

        # Sarlavhalar
        headers = [
            "ID", "Ism Familiya", "Telefon", "Email",
            "MK ID", "Tug'ilgan sana", "Jinsi",
            "Ro'yxatdan o'tgan sana", "Rol",
        ]
        ws.append(headers)

        # Sarlavha stilini qalin qilish
        from openpyxl.styles import Font
        for cell in ws[1]:
            cell.font = Font(bold=True)

        for user in users:
            phone = email = "-"
            for contact in user.contacts:
                if contact.contact_type == ContactType.PHONE and phone == "-":
                    phone = contact.value
                if contact.contact_type == ContactType.EMAIL and email == "-":
                    email = contact.value

            profile: UserProfile | None = user.profile
            full_name = profile.full_name if profile else "-"
            mk_id = mk_by_user.get(user.id, "-")
            birth_date = (
                profile.birth_date.strftime("%Y-%m-%d")
                if profile and profile.birth_date
                else "-"
            )
            gender = (
                ("Erkak" if profile.gender == "male" else "Ayol") if profile else "-"
            )
            created_at = (
                user.created_at.strftime("%Y-%m-%d %H:%M") if user.created_at else "-"
            )
            role_val = (
                user.global_role.value
                if hasattr(user.global_role, "value")
                else str(user.global_role)
            )

            ws.append([
                str(user.id), full_name, phone, email,
                mk_id, birth_date, gender, created_at, role_val,
            ])

        # Ustun kengliklarini avtomatik moslash
        for col in ws.columns:
            max_len = max((len(str(cell.value or "")) for cell in col), default=0)
            ws.column_dimensions[col[0].column_letter].width = min(max_len + 4, 40)

        tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".xlsx")
        wb.save(tmp.name)
        wb.close()
        return tmp.name