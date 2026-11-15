"""Referal tizimi: kod, bog'lash (ro'yxatdan o'tishda), mukofot (onboarding tugaganda), bonus kreditlar."""
from __future__ import annotations

import secrets
import string
import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.billing.models import SubscriptionPlan, UserSubscription
from app.modules.referral.models import Referral, ReferralCode, ReferralCredit, ReferralSettings

STATUS_PENDING = "pending"
STATUS_QUALIFIED = "qualified"
STATUS_CAPPED = "capped"

_ALPHABET = string.ascii_uppercase + string.digits


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _aware(dt: datetime | None) -> datetime | None:
    if dt is None:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


async def get_settings(db: AsyncSession) -> ReferralSettings:
    row = await db.get(ReferralSettings, 1)
    if row is None:
        row = ReferralSettings(id=1, enabled=False, reward_credits=0, reward_tier_days=0)
        db.add(row)
        await db.flush()
    return row


async def ensure_code(db: AsyncSession, user_id: uuid.UUID) -> str:
    existing = await db.scalar(select(ReferralCode.code).where(ReferralCode.user_id == user_id))
    if existing:
        return existing
    for _ in range(10):
        code = "".join(secrets.choice(_ALPHABET) for _ in range(8))
        taken = await db.scalar(select(ReferralCode.id).where(ReferralCode.code == code))
        if taken is None:
            db.add(ReferralCode(user_id=user_id, code=code))
            await db.flush()
            return code
    raise RuntimeError("Referal kod yaratib bo'lmadi")


async def attach_referral(db: AsyncSession, referred_id: uuid.UUID, raw_code: str | None) -> Referral | None:
    """Ro'yxatdan o'tishda chaqiriladi. Noto'g'ri kod jim e'tiborsiz qoldiriladi (ro'yxatdan o'tish buzilmasin)."""
    if not raw_code:
        return None
    settings = await get_settings(db)
    if not settings.enabled:
        return None
    referrer_id = await db.scalar(select(ReferralCode.user_id).where(ReferralCode.code == raw_code.strip().upper()))
    if referrer_id is None or referrer_id == referred_id:
        return None
    if await db.scalar(select(Referral.id).where(Referral.referred_id == referred_id)) is not None:
        return None
    ref = Referral(referrer_id=referrer_id, referred_id=referred_id, status=STATUS_PENDING)
    db.add(ref)
    await db.flush()
    return ref


async def qualify_referral(db: AsyncSession, referred_id: uuid.UUID) -> Referral | None:
    """Taklif qilingan user to'liq ro'yxatdan o'tganda (onboarding tugaganda) chaqiriladi. Faqat bir marta mukofot beradi."""
    ref = await db.scalar(
        select(Referral).where(Referral.referred_id == referred_id, Referral.status == STATUS_PENDING)
    )
    if ref is None:
        return None
    settings = await get_settings(db)
    if not settings.enabled:
        return None

    now = _now()
    if settings.max_rewards_per_user is not None:
        done = await db.scalar(
            select(func.count(Referral.id)).where(
                Referral.referrer_id == ref.referrer_id, Referral.status == STATUS_QUALIFIED
            )
        ) or 0
        if done >= settings.max_rewards_per_user:
            ref.status = STATUS_CAPPED
            await db.flush()
            return ref

    if settings.reward_credits > 0:
        expires = now + timedelta(days=settings.credit_expiry_days) if settings.credit_expiry_days else None
        db.add(ReferralCredit(
            user_id=ref.referrer_id,
            referral_id=ref.id,
            amount_total=settings.reward_credits,
            amount_remaining=settings.reward_credits,
            expires_at=expires,
        ))

    if settings.reward_tier_days > 0 and settings.reward_plan_id:
        await _grant_subscription(db, ref.referrer_id, settings.reward_plan_id, settings.reward_tier_days, now)

    ref.status = STATUS_QUALIFIED
    ref.qualified_at = now
    await db.flush()
    return ref


async def _grant_subscription(db: AsyncSession, user_id: uuid.UUID, plan_id: int, days: int, now: datetime) -> None:
    """Mavjud obuna tugagach davom etadigan qilib qo'shadi (obunalar ustma-ust yozilmaydi)."""
    plan = await db.get(SubscriptionPlan, plan_id)
    if plan is None:
        return
    latest_end = await db.scalar(
        select(func.max(UserSubscription.end_date)).where(
            UserSubscription.user_id == user_id, UserSubscription.is_active.is_(True)
        )
    )
    start = max(now, _aware(latest_end)) if latest_end else now
    db.add(UserSubscription(
        user_id=user_id, plan_id=plan_id, start_date=start,
        end_date=start + timedelta(days=days), is_active=True,
    ))


def _available_credits_filter(user_id: uuid.UUID, now: datetime):
    return (
        ReferralCredit.user_id == user_id,
        ReferralCredit.amount_remaining > 0,
        or_(ReferralCredit.expires_at.is_(None), ReferralCredit.expires_at > now),
    )


async def consume_bonus_credit(db: AsyncSession, user_id: uuid.UUID) -> bool:
    """Limit tugaganda chaqiriladi. Eng tez tugaydigan kreditdan bittasini sarflaydi."""
    row = (await db.execute(
        select(ReferralCredit)
        .where(*_available_credits_filter(user_id, _now()))
        .order_by(ReferralCredit.expires_at.is_(None), ReferralCredit.expires_at, ReferralCredit.id)
        .limit(1)
        .with_for_update()
    )).scalar_one_or_none()
    if row is None:
        return False
    row.amount_remaining -= 1
    await db.flush()
    return True


async def user_summary(db: AsyncSession, user_id: uuid.UUID) -> dict:
    settings = await get_settings(db)
    code = await ensure_code(db, user_id)
    rows = (await db.execute(
        select(Referral.status, func.count(Referral.id))
        .where(Referral.referrer_id == user_id)
        .group_by(Referral.status)
    )).all()
    counts = {s: n for s, n in rows}
    credits = await db.scalar(
        select(func.coalesce(func.sum(ReferralCredit.amount_remaining), 0))
        .where(*_available_credits_filter(user_id, _now()))
    ) or 0
    return {
        "code": code,
        "enabled": settings.enabled,
        "invited": sum(counts.values()),
        "pending": counts.get(STATUS_PENDING, 0),
        "qualified": counts.get(STATUS_QUALIFIED, 0),
        "capped": counts.get(STATUS_CAPPED, 0),
        "credits_available": int(credits),
        "reward": {"credits": settings.reward_credits, "tier_days": settings.reward_tier_days},
    }
