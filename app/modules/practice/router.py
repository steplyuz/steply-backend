"""Unified public practice-test catalog across all four exam skills."""
from typing import Any

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.database import get_db
from app.modules.auth.dependencies import get_current_user_optional
from app.modules.auth.models import UserRole
from app.modules.billing.entitlements import get_user_tier, plan_allows_skill
from app.modules.billing.models import SubscriptionTier
from app.modules.tests.listening.models import ListeningTest
from app.modules.tests.reading.models import ReadingTest
from app.modules.tests.writing.models import WritingExam
from app.modules.tests.speaking.models import SpeakingTest

router = APIRouter(prefix="/practice", tags=["Practice"])

_STAFF_ROLES = (UserRole.ADMIN, UserRole.SUPER_ADMIN)


class _LockResolver:
    """Katalogdagi har bir test uchun premium qulfni hisoblaydi (so'rov davomida keshlanadi).

    Qoida `billing.entitlements.ensure_practice_access` bilan bir xil:
      * is_free testlar                           -> hamma uchun ochiq (content_access bilan bir xil)
      * ADMIN / SUPER_ADMIN                       -> hech narsa qulflanmaydi
      * pullik test + kirmagan foydalanuvchi      -> qulf
      * pullik test + tarifi shu ko'nikmani o'z ichiga olmasa yoki FREE -> qulf
      * PLUS / PRO (ko'nikma tarifda bor)         -> hammasi ochiq
    Bu faqat UI uchun ma'lumot; asl himoya start/attempt endpointlarida (403).
    """

    def __init__(self, db: AsyncSession, user) -> None:
        self.db = db
        self.user = user
        self._tier: SubscriptionTier | None = None
        self._skill_ok: dict[str, bool] = {}

    async def is_locked(self, skill: str, is_free: bool) -> bool:
        if is_free:
            return False
        if self.user is None:
            return True
        if self.user.global_role in _STAFF_ROLES:
            return False
        if self._tier is None:
            self._tier = await get_user_tier(self.db, self.user.id)
        if skill not in self._skill_ok:
            self._skill_ok[skill] = await plan_allows_skill(self.db, self.user.id, skill.upper())
        if not self._skill_ok[skill]:
            return True
        return self._tier == SubscriptionTier.FREE


async def _item(resolver: _LockResolver, skill: str, x: Any, **fields: Any) -> dict[str, Any]:
    is_free = bool(x.is_free)
    return {
        "id": x.id, "title": x.title, "skill": skill, **fields,
        "is_free": is_free,
        "access": "free" if is_free else "premium",
        "is_locked": await resolver.is_locked(skill, is_free),
    }


@router.get("/tests")
async def list_practice_tests(
    skill: str | None = Query(default=None, pattern="^(listening|reading|writing|speaking)$"),
    db: AsyncSession = Depends(get_db),
    user=Depends(get_current_user_optional),
) -> dict[str, Any]:
    """Return active practice tests grouped by skill; excludes mock-only entries.

    Har bir element `access` ("free" | "premium") va `is_locked` (joriy foydalanuvchi uchun qulf) maydonlarini
    o'z ichiga oladi — premium testlar ro'yxatdan yashirilmaydi, qulflangan holda ko'rsatiladi.
    """
    resolver = _LockResolver(db, user)
    groups: dict[str, list[dict[str, Any]]] = {
        "listening": [], "reading": [], "writing": [], "speaking": []
    }

    if skill in (None, "listening"):
        rows = (await db.execute(select(ListeningTest).where(
            ListeningTest.is_active.is_(True), ListeningTest.is_practice.is_(True)
        ).order_by(ListeningTest.created_at.desc()))).scalars().all()
        groups["listening"] = [
            await _item(resolver, "listening", x, cefr_level=x.cefr_level,
                        duration_minutes=x.duration_minutes, total_questions=x.total_questions)
            for x in rows
        ]

    if skill in (None, "reading"):
        rows = (await db.execute(select(ReadingTest).where(
            ReadingTest.is_active.is_(True), ReadingTest.is_practice.is_(True)
        ).order_by(ReadingTest.created_at.desc()))).scalars().all()
        groups["reading"] = [
            await _item(resolver, "reading", x, cefr_level=x.cefr_level,
                        duration_minutes=x.duration_minutes, total_questions=x.total_questions,
                        practice_part_number=getattr(x, "practice_part_number", None),
                        parent_test_id=getattr(x, "parent_test_id", None))
            for x in rows
        ]

    if skill in (None, "writing"):
        rows = (await db.execute(select(WritingExam).options(selectinload(WritingExam.tasks)).where(
            WritingExam.is_active.is_(True), WritingExam.is_practice.is_(True)
        ).order_by(WritingExam.created_at.desc()))).scalars().all()
        groups["writing"] = [
            await _item(resolver, "writing", x, cefr_level=getattr(x.cefr_level, "value", x.cefr_level),
                        duration_minutes=x.duration_minutes, total_questions=len(x.tasks or []))
            for x in rows
        ]

    if skill in (None, "speaking"):
        rows = (await db.execute(select(SpeakingTest).where(
            SpeakingTest.is_active.is_(True), SpeakingTest.is_practice.is_(True)
        ).order_by(SpeakingTest.created_at.desc()))).scalars().all()
        groups["speaking"] = [
            await _item(resolver, "speaking", x, cefr_level=None, duration_minutes=None, total_questions=None)
            for x in rows
        ]

    return {"items": groups, "total": sum(map(len, groups.values()))}
