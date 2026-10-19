"""Free/Plus/Pro tarif tekshiruvi, haftalik + oylik amaliyot (practice) limiti va AI kvotasi.

Bu modul MOCK EXAM bilan bog'liq emas — mock-exam ro'yxatdan
o'tish/to'lov/enrollment mock_test.services.has_active_enrollment orqali
alohida boshqariladi. Bu yerdagi limit faqat PRACTICE (mustaqil mashq)
rejimidagi Reading/Listening/Writing/Speaking testlariga tegishli
(spec 3.2-band): barcha skilllar uchun bitta umumiy haftalik/oylik limit.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo
from typing import Optional

from fastapi import HTTPException
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.exc import IntegrityError
from app.modules.auth.models import User

from .models import GlobalPracticeUsageLog, SubscriptionPlan, SubscriptionTier, TierPracticeLimit, UserSubscription

SKILLS = ("READING", "LISTENING", "WRITING", "SPEAKING")
PRODUCT_TIMEZONE = ZoneInfo("Asia/Tashkent")

# Qaysi tarif qaysi ko'nikmani ocha oladi. Speaking faqat PRO'da.
TIER_SKILLS: dict[SubscriptionTier, frozenset[str]] = {
    SubscriptionTier.FREE: frozenset({"READING", "LISTENING", "WRITING"}),
    SubscriptionTier.PLUS: frozenset({"READING", "LISTENING", "WRITING"}),
    SubscriptionTier.PRO: frozenset({"READING", "LISTENING", "WRITING", "SPEAKING"}),
}


def tier_allows_skill(tier: SubscriptionTier, skill: str) -> bool:
    # Fallback uchun; haqiqiy obuna tekshiruvi ensure_practice_access ichida tarif bo‘yicha.
    return skill in TIER_SKILLS.get(tier, frozenset())


async def plan_allows_skill(db: AsyncSession, user_id, skill: str) -> bool:
    sub = await get_active_subscription(db, user_id)
    if not sub:
        return skill in TIER_SKILLS[SubscriptionTier.FREE]
    plan = await db.get(SubscriptionPlan, sub.plan_id)
    if not plan:
        return False
    configured = getattr(plan, "included_skills", None)
    return skill in (configured or TIER_SKILLS.get(plan.tier, frozenset()))


def _month_start(now: Optional[datetime] = None) -> date:
    """Oyning 1-sanasi, Asia/Tashkent kalendariga ko'ra."""
    current = now or datetime.now(timezone.utc)
    if current.tzinfo is None:
        current = current.replace(tzinfo=timezone.utc)
    d = current.astimezone(PRODUCT_TIMEZONE).date()
    return d.replace(day=1)


def _month_start_utc(now: Optional[datetime] = None) -> datetime:
    """Joriy oy boshlanishi (Tashkent 00:00) UTC instant sifatida — `consumed_at` bilan solishtirish uchun."""
    m = _month_start(now)
    return datetime(m.year, m.month, 1, tzinfo=PRODUCT_TIMEZONE).astimezone(timezone.utc)


def _week_start(now: Optional[datetime] = None) -> date:
    """Haftaning boshi — dushanba, Asia/Tashkent kalendariga ko'ra."""
    current = now or datetime.now(timezone.utc)
    if current.tzinfo is None:
        current = current.replace(tzinfo=timezone.utc)
    d = current.astimezone(PRODUCT_TIMEZONE).date()
    return d - timedelta(days=d.weekday())


async def get_active_subscription(db: AsyncSession, user_id):
    """Return the currently entitled subscription, if any.

    ``is_active`` alone is not entitlement: both start and end timestamps
    must bracket the current UTC instant. Historical subscriptions remain
    untouched so historical results remain accessible.
    """
    now = datetime.now(timezone.utc)
    return (
        await db.execute(
            select(UserSubscription)
            .where(
                UserSubscription.user_id == user_id,
                UserSubscription.is_active.is_(True),
                UserSubscription.start_date <= now,
                UserSubscription.end_date > now,
            )
            .order_by(UserSubscription.end_date.desc())
            .limit(1)
        )
    ).scalars().first()


async def get_user_tier(db: AsyncSession, user_id) -> SubscriptionTier:
    """Current entitled tier; no active/current subscription means FREE."""
    sub = await get_active_subscription(db, user_id)
    if not sub:
        return SubscriptionTier.FREE
    plan = await db.get(SubscriptionPlan, sub.plan_id)
    return plan.tier if plan else SubscriptionTier.FREE




async def require_tier(db: AsyncSession, user_id, *tiers: SubscriptionTier) -> SubscriptionTier:
    tier = await get_user_tier(db, user_id)
    if tier not in set(tiers):
        allowed = ", ".join(t.value.upper() for t in tiers)
        raise HTTPException(403, f"Ushbu funksiya uchun faol {allowed} obunasi kerak")
    return tier


async def ensure_practice_access(db: AsyncSession, user_id, test, skill: str) -> SubscriptionTier:
    """Enforce content entitlement independently from quota.

    FREE users can only open tests explicitly marked ``is_free``; paid tiers
    may use active practice tests. This check is always server-side.
    """
    tier = await get_user_tier(db, user_id)
    if skill not in SKILLS:
        return tier
    if not getattr(test, "is_active", True) or not getattr(test, "is_practice", False):
        raise HTTPException(404, "Practice testi topilmadi")
    if not await plan_allows_skill(db, user_id, skill):
        raise HTTPException(403, f"{skill} faqat Pro obunasida mavjud" if skill == "SPEAKING" else f"{skill} sizning tarifingizda mavjud emas")
    if tier == SubscriptionTier.FREE and not bool(getattr(test, "is_free", False)):
        raise HTTPException(403, "Bu practice testi Plus yoki Pro obunasini talab qiladi")
    return tier


async def get_limits(db: AsyncSession, tier: SubscriptionTier) -> tuple[Optional[int], Optional[int]]:
    """Umumiy practice (barcha skilllar) haftalik va oylik limiti."""
    row = (await db.execute(select(TierPracticeLimit.weekly_limit, TierPracticeLimit.monthly_limit).where(TierPracticeLimit.tier == tier))).first()
    return (row[0], row[1]) if row else (None, None)


async def _count_week(db: AsyncSession, user_id, week_start: date) -> int:
    return int((await db.scalar(select(func.count()).select_from(GlobalPracticeUsageLog).where(GlobalPracticeUsageLog.user_id == user_id, GlobalPracticeUsageLog.week_start == week_start))) or 0)


async def _count_month(db: AsyncSession, user_id) -> int:
    return int((await db.scalar(select(func.count()).select_from(GlobalPracticeUsageLog).where(GlobalPracticeUsageLog.user_id == user_id, GlobalPracticeUsageLog.consumed_at >= _month_start_utc()))) or 0)


async def enforce_practice_limit(
    db: AsyncSession, user_id, skill: str, test_id: str,
    *, mode: str = "FULL_PRACTICE", attempt_id: int | None = None, test=None,
) -> None:
    """Barcha skilllar uchun yagona practice kvotasini tekshiradi va sarflaydi.

    attempt_id mavjud bo'lsa, shu attempt uchun takroriy chaqiruv ikkinchi marta sarflamaydi.
    Yangi attempt_id bilan ayni test qayta ishlansa, yana bitta birlik sarflanadi.
    """
    if skill not in SKILLS:
        return
    await db.execute(update(User).where(User.id == user_id).values(updated_at=User.updated_at))
    if test is not None:
        await ensure_practice_access(db, user_id, test, skill)
    tier = await get_user_tier(db, user_id)
    weekly_limit, monthly_limit = await get_limits(db, tier)
    week_start = _week_start()

    # Reservation is idempotent per user/test/week, including callers that do not yet
    # supply attempt_id. This prevents retries of the same practice request consuming quota.
    existing_stmt = select(GlobalPracticeUsageLog.id).where(
        GlobalPracticeUsageLog.user_id == user_id,
        GlobalPracticeUsageLog.test_id == str(test_id),
        GlobalPracticeUsageLog.week_start == week_start,
    ).limit(1)
    if attempt_id is not None:
        existing_stmt = select(GlobalPracticeUsageLog.id).where(
            (GlobalPracticeUsageLog.attempt_id == attempt_id)
            | ((GlobalPracticeUsageLog.user_id == user_id)
               & (GlobalPracticeUsageLog.test_id == str(test_id))
               & (GlobalPracticeUsageLog.week_start == week_start))
        ).limit(1)
    exists = await db.scalar(existing_stmt)
    if exists is not None:
        return
    if weekly_limit is not None and await _count_week(db, user_id, week_start) >= weekly_limit:
        raise HTTPException(429, f"Bu hafta uchun umumiy practice limiti ({weekly_limit} ta) tugadi")
    if monthly_limit is not None and await _count_month(db, user_id) >= monthly_limit:
        raise HTTPException(429, f"Bu oy uchun umumiy practice limiti ({monthly_limit} ta) tugadi")
    db.add(GlobalPracticeUsageLog(user_id=user_id, skill=skill, test_id=str(test_id), attempt_id=attempt_id, week_start=week_start))
    await db.flush()


async def get_ai_quota(db: AsyncSession, user_id) -> dict:
    """Writing AI: alohida oylik obuna kvotasi + xarid qilingan kreditlar."""
    from .models import WritingAICreditGrant, WritingAIUsage
    sub = await get_active_subscription(db, user_id)
    tier = SubscriptionTier.FREE
    limit = 0
    if sub:
        plan = await db.get(SubscriptionPlan, sub.plan_id)
        if plan:
            tier = plan.tier
            limit = int(plan.ai_limit or 0) if bool(getattr(plan, "ai_enabled", False)) else 0
    from app.modules.tests.writing.models import FeedbackSource, WritingAnswer, WritingFeedback, WritingResult
    month_start = _month_start_utc()
    # New provider evaluations have an explicit billing-source ledger. Legacy AI
    # feedback without a ledger row counts against subscription quota for compatibility;
    # deterministic zero-score feedback (model_name=None) did not call the provider.
    ledger_used = int((await db.scalar(select(func.count(WritingAIUsage.id)).where(
        WritingAIUsage.user_id == user_id,
        WritingAIUsage.billing_source == "subscription",
        WritingAIUsage.created_at >= month_start,
    ))) or 0)
    legacy_used = int((await db.execute(
        select(func.count()).select_from(WritingFeedback)
        .join(WritingAnswer, WritingAnswer.id == WritingFeedback.answer_id)
        .join(WritingResult, WritingResult.id == WritingAnswer.result_id)
        .outerjoin(WritingAIUsage, WritingAIUsage.answer_id == WritingAnswer.id)
        .where(WritingResult.user_id == user_id,
               WritingFeedback.source == FeedbackSource.AI,
               WritingFeedback.created_at >= month_start,
               WritingFeedback.model_name.is_not(None),
               func.trim(WritingAnswer.content) != "",
               WritingAIUsage.id.is_(None))
    )).scalar_one() or 0)
    used = ledger_used + legacy_used
    purchased = int((await db.scalar(select(func.coalesce(func.sum(WritingAICreditGrant.credits_remaining), 0)).where(
        WritingAICreditGrant.user_id == user_id,
        WritingAICreditGrant.credits_remaining > 0,
    ))) or 0)
    subscription_remaining = max(0, limit - used)
    return {
        "enabled": subscription_remaining > 0 or purchased > 0,
        "monthly_limit": limit,
        "used_this_month": used,
        "subscription_remaining": subscription_remaining,
        "purchased_remaining": purchased,
        "remaining": subscription_remaining + purchased,
    }


async def enforce_ai_quota(db: AsyncSession, user_id, units: int = 1) -> int:
    """Tekshiradi va oylik kvotadan ortgan qismini xarid kreditlaridan atomar band qiladi.

    Qaytgan son — xarid qilingan kreditlardan band qilingan birlik. Caller provider xatosida
    tranzaksiyani rollback qilishi kerak; muvaffaqiyatli Writing service commit'i bandni saqlaydi.
    """
    from .models import WritingAICreditGrant
    units = max(1, int(units))
    # Serialize quota reservations across different Writing results for this user.
    await db.execute(update(User).where(User.id == user_id).values(updated_at=User.updated_at))
    quota = await get_ai_quota(db, user_id)
    monthly_units = min(units, quota["subscription_remaining"])
    purchased_needed = units - monthly_units
    if purchased_needed <= 0:
        return 0
    grants = (await db.execute(
        select(WritingAICreditGrant).where(
            WritingAICreditGrant.user_id == user_id,
            WritingAICreditGrant.credits_remaining > 0,
        ).order_by(WritingAICreditGrant.created_at, WritingAICreditGrant.id).with_for_update()
    )).scalars().all()
    available = sum(g.credits_remaining for g in grants)
    if available < purchased_needed:
        if quota["subscription_remaining"] == 0 and available == 0:
            raise HTTPException(429, "Writing AI limiti tugadi. Billing sahifasidan qo‘shimcha AI kredit sotib oling.")
        raise HTTPException(429, f"Writing AI uchun {units} kredit kerak; faqat {quota['subscription_remaining'] + available} ta qoldi")
    remaining = purchased_needed
    for grant in grants:
        if remaining <= 0:
            break
        take = min(grant.credits_remaining, remaining)
        grant.credits_remaining -= take
        remaining -= take
    await db.flush()
    return purchased_needed


async def get_remaining_limits(db: AsyncSession, user_id) -> dict:
    """Dashboard uchun bitta umumiy practice kvotasi va alohida AI kvotasi."""
    tier = await get_user_tier(db, user_id)
    week_start = _week_start()
    month_start = _month_start()
    weekly_limit, monthly_limit = await get_limits(db, tier)
    used_week = await _count_week(db, user_id, week_start)
    used_month = await _count_month(db, user_id)
    return {
        "tier": tier.value,
        "week_start": week_start.isoformat(),
        "month_start": month_start.isoformat(),
        "ai": await get_ai_quota(db, user_id),
        "practice": {
            "weekly_limit": weekly_limit,
            "used_this_week": used_week,
            "remaining": None if weekly_limit is None else max(0, weekly_limit - used_week),
            "monthly_limit": monthly_limit,
            "used_this_month": used_month,
            "remaining_this_month": None if monthly_limit is None else max(0, monthly_limit - used_month),
        },
    }


async def owns_mock_attempt(db: AsyncSession, user_id, attempt_id: Optional[int]) -> bool:
    """`exam_attempt_id` haqiqatan shu foydalanuvchining tugallanmagan mock urinishimi?

    Ilgari routerlar `if not exam_attempt_id:` bilan tekshirardi — ya'ni har qanday
    (soxta yoki boshqa birovning) ID yuborib haftalik practice limitni chetlab
    o'tish mumkin edi.
    """
    if not attempt_id:
        return False
    from app.modules.exams.mock.models import MockExamAttempt

    row = (
        await db.execute(
            select(MockExamAttempt.id).where(
                MockExamAttempt.id == attempt_id,
                MockExamAttempt.user_id == user_id,
                MockExamAttempt.is_finished.is_(False),
            )
        )
    ).scalar_one_or_none()
    return row is not None


