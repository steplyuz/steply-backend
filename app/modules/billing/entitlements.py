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

# Qaysi tarif qaysi ko'nikmani ocha oladi. Pulik testlar barcha pulik obunaga kiradi
# (PLUS va PRO); FREE faqat is_free testlarni ochadi.
TIER_SKILLS: dict[SubscriptionTier, frozenset[str]] = {
    SubscriptionTier.FREE: frozenset({"READING", "LISTENING", "WRITING"}),
    SubscriptionTier.PLUS: frozenset({"READING", "LISTENING", "WRITING", "SPEAKING"}),
    SubscriptionTier.PRO: frozenset({"READING", "LISTENING", "WRITING", "SPEAKING"}),
}


def tier_allows_skill(tier: SubscriptionTier, skill: str) -> bool:
    # Fallback uchun; haqiqiy obuna tekshiruvi ensure_practice_access ichida tarif bo‘yicha.
    return skill in TIER_SKILLS.get(tier, frozenset())


async def plan_allows_skill(db: AsyncSession, user_id, skill: str) -> bool:
    """Barcha faol obunalar ko'nikmalarining birlashmasi (obunalar qo'shiladi)."""
    subs = await get_active_subscriptions(db, user_id)
    if not subs:
        return skill in TIER_SKILLS[SubscriptionTier.FREE]
    for sub in subs:
        plan = await db.get(SubscriptionPlan, sub.plan_id)
        if plan is None:
            continue
        configured = getattr(plan, "included_skills", None)
        if skill in (configured or TIER_SKILLS.get(plan.tier, frozenset())):
            return True
    return False


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


async def get_active_subscriptions(db: AsyncSession, user_id) -> list[UserSubscription]:
    """Hozir faol BARCHA obunalar (bir nechta tarif bir vaqtda bo'lishi mumkin)."""
    now = datetime.now(timezone.utc)
    rows = await db.execute(
        select(UserSubscription)
        .where(
            UserSubscription.user_id == user_id,
            UserSubscription.is_active.is_(True),
            UserSubscription.start_date <= now,
            UserSubscription.end_date > now,
        )
        .order_by(UserSubscription.end_date.desc())
    )
    return list(rows.scalars().all())


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
    """Umumiy AI kreditlar (Writing + Speaking): oylik obuna kvotasi + xarid qilingan kreditlar."""
    from .models import AICreditGrant, AIUsage
    subs = await get_active_subscriptions(db, user_id)
    tier = SubscriptionTier.FREE
    limit = 0
    for sub in subs:
        plan = await db.get(SubscriptionPlan, sub.plan_id)
        if plan is None:
            continue
        tier = plan.tier
        if bool(getattr(plan, "ai_enabled", False)):
            limit += int(plan.ai_limit or 0)
    month_start = _month_start_utc()
    # Barcha AI sarfi (Writing + Speaking) bitta ledger'dan: faqat obuna limitidan yechilganlari.
    used = int((await db.scalar(select(func.count(AIUsage.id)).where(
        AIUsage.user_id == user_id,
        AIUsage.billing_source == "subscription",
        AIUsage.created_at >= month_start,
    ))) or 0)
    purchased = int((await db.scalar(select(func.coalesce(func.sum(AICreditGrant.credits_remaining), 0)).where(
        AICreditGrant.user_id == user_id,
        AICreditGrant.credits_remaining > 0,
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


async def enforce_ai_quota(db: AsyncSession, user_id, units: int = 1, feature: str = "AI") -> int:
    """Tekshiradi va oylik kvotadan ortgan qismini xarid kreditlaridan atomar band qiladi.

    Qaytgan son — xarid qilingan kreditlardan band qilingan birlik. Caller provider xatosida
    tranzaksiyani rollback qilishi kerak; muvaffaqiyatli Writing service commit'i bandni saqlaydi.
    """
    from .models import AICreditGrant
    units = max(1, int(units))
    # Serialize quota reservations across different Writing results for this user.
    await db.execute(update(User).where(User.id == user_id).values(updated_at=User.updated_at))
    quota = await get_ai_quota(db, user_id)
    monthly_units = min(units, quota["subscription_remaining"])
    purchased_needed = units - monthly_units
    if purchased_needed <= 0:
        return 0
    grants = (await db.execute(
        select(AICreditGrant).where(
            AICreditGrant.user_id == user_id,
            AICreditGrant.credits_remaining > 0,
        ).order_by(AICreditGrant.created_at, AICreditGrant.id).with_for_update()
    )).scalars().all()
    available = sum(g.credits_remaining for g in grants)
    if available < purchased_needed:
        if quota["subscription_remaining"] == 0 and available == 0:
            raise HTTPException(429, f"AI kreditlari tugadi ({feature}). Billing sahifasidan qo‘shimcha AI kredit sotib oling.")
        raise HTTPException(429, f"{feature} uchun {units} AI kredit kerak; faqat {quota['subscription_remaining'] + available} ta qoldi")
    remaining = purchased_needed
    for grant in grants:
        if remaining <= 0:
            break
        take = min(grant.credits_remaining, remaining)
        grant.credits_remaining -= take
        remaining -= take
    await db.flush()
    return purchased_needed


async def refund_purchased_ai_units(db: AsyncSession, user_id, units: int) -> None:
    """Sotib olingan kreditdan yechilgan birliklarni qaytaradi (xato bo'lgan baholash uchun)."""
    from .models import AICreditGrant
    remaining = max(0, int(units))
    if remaining <= 0:
        return
    grants = (await db.execute(
        select(AICreditGrant).where(AICreditGrant.user_id == user_id)
        .order_by(AICreditGrant.created_at.desc(), AICreditGrant.id.desc()).with_for_update()
    )).scalars().all()
    for grant in grants:
        if remaining <= 0:
            break
        room = grant.credits_total - grant.credits_remaining
        if room <= 0:
            continue
        give = min(room, remaining)
        grant.credits_remaining += give
        remaining -= give
    await db.flush()


async def release_speaking_usage(db: AsyncSession, job_id: int) -> None:
    """Speaking baholash xato bilan tugasa: ledger qatorini o'chiradi, sotib olingan kreditni qaytaradi."""
    from .models import AIUsage
    row = (await db.execute(select(AIUsage).where(AIUsage.job_id == job_id, AIUsage.feature == "speaking"))).scalar_one_or_none()
    if row is None:
        return
    if row.purchased_units:
        await refund_purchased_ai_units(db, row.user_id, row.purchased_units)
    await db.delete(row)
    await db.flush()


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
