"""Obuna hodisalari: muddat tugashidan oldin ogohlantirish va tugash.

`scan_subscription_events()` worker tomonidan davriy chaqiriladi (scripts/process_notifications.py).
Har bir xabar `ref` orqali faqat bir marta navbatga qo'yiladi, shuning uchun takror chaqirish xavfsiz.
Sotib olish xabari esa to'lov tasdiqlanganda (billing.review.emit_order_paid) yuboriladi.
"""
from __future__ import annotations

import math
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.billing.models import SubscriptionPlan, UserSubscription

# Muddat tugashidan necha kun oldin ogohlantirish (har biri alohida, bir marta)
EXPIRY_REMINDER_DAYS: tuple[int, ...] = (3, 1)
# Tugagandan keyin shu oraliqda "tugadi" xabari yuboriladi (worker uzoq to'xtab qolsa ham xabar ketadi)
EXPIRED_WINDOW = timedelta(days=1)
PRODUCT_TZ = ZoneInfo("Asia/Tashkent")


def _aware(dt: datetime) -> datetime:
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _format_date(dt: datetime) -> str:
    return _aware(dt).astimezone(PRODUCT_TZ).strftime("%d.%m.%Y")


async def scan_subscription_events(db: AsyncSession, now: datetime | None = None) -> dict[str, int]:
    from app.modules.notifications.service import emit_event

    now = now or datetime.now(timezone.utc)
    stats = {"expiring_checked": 0, "expired_checked": 0}

    # 1) Muddat tugashidan oldin
    horizon = now + timedelta(days=max(EXPIRY_REMINDER_DAYS))
    expiring = (await db.execute(
        select(UserSubscription, SubscriptionPlan.name)
        .join(SubscriptionPlan, UserSubscription.plan_id == SubscriptionPlan.id)
        .where(
            UserSubscription.is_active.is_(True),
            UserSubscription.end_date > now,
            UserSubscription.end_date <= horizon,
        )
    )).all()
    for sub, plan_name in expiring:
        end = _aware(sub.end_date)
        days_left = max(1, math.ceil((end - now).total_seconds() / 86400))
        for days in EXPIRY_REMINDER_DAYS:
            if end > now + timedelta(days=days):
                continue
            stats["expiring_checked"] += 1
            await emit_event(
                db, "subscription_expiring", user_id=sub.user_id, ref=f"sub-{sub.id}-expiring-{days}",
                variables={"plan": plan_name, "end_date": _format_date(end), "days": str(days_left)},
            )

    # 2) Yaqinda tugagan (boshqa faol obunasi bo'lmaganlarga)
    expired = (await db.execute(
        select(UserSubscription, SubscriptionPlan.name)
        .join(SubscriptionPlan, UserSubscription.plan_id == SubscriptionPlan.id)
        .where(
            UserSubscription.end_date <= now,
            UserSubscription.end_date > now - EXPIRED_WINDOW,
        )
    )).all()
    for sub, plan_name in expired:
        still_active = await db.scalar(select(UserSubscription.id).where(
            UserSubscription.user_id == sub.user_id,
            UserSubscription.is_active.is_(True),
            UserSubscription.end_date > now,
        ).limit(1))
        if still_active is not None:
            continue
        stats["expired_checked"] += 1
        await emit_event(
            db, "subscription_expired", user_id=sub.user_id, ref=f"sub-{sub.id}-expired",
            variables={"plan": plan_name, "end_date": _format_date(_aware(sub.end_date))},
        )

    await db.commit()
    return stats
