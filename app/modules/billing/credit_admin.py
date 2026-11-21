"""Admin: AI kreditlarni userga yoki tarif obunachilariga qo'shish va ayirish.

Har bir o'zgarish audit uchun BillingOrder (0 so'm, PAID, admin_note) va BalanceTransaction yozuvi bilan qayd etiladi.
Ayirish FIFO: eng eski kreditlardan olinadi (enforce_ai_quota bilan bir xil tartib).
"""
from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.modules.auth.dependencies import require_role
from app.modules.auth.models import User, UserRole
from app.modules.billing.models import (
    AICreditGrant, BalanceTransaction, BillingOrder, BillingOrderStatus, BillingProductType,
    SubscriptionPlan, UserSubscription,
)

router = APIRouter(prefix="/admin/credits", tags=["Admin AI Credits"])
ADMIN_ROLES = (UserRole.ADMIN, UserRole.SUPER_ADMIN)


class AICreditAdjustIn(BaseModel):
    delta: int = Field(..., ge=-100000, le=100000, description="Musbat: qo'shish, manfiy: ayirish")
    note: str = Field(min_length=3, max_length=300)


def _now() -> datetime:
    return datetime.now(timezone.utc)


async def balance(db: AsyncSession, user_id) -> int:
    rows = (await db.execute(select(AICreditGrant.credits_remaining).where(
        AICreditGrant.user_id == user_id, AICreditGrant.credits_remaining > 0,
    ))).scalars().all()
    return int(sum(rows))


async def _audit_order(db: AsyncSession, user_id, admin_id, title: str, note: str, amount: int) -> BillingOrder:
    order = BillingOrder(
        user_id=user_id, product_type=BillingProductType.AI_CREDITS, product_id=None,
        product_title=title[:200], amount=0, original_amount=0, discount_amount=0,
        status=BillingOrderStatus.PAID, reviewed_by=admin_id, reviewed_at=_now(),
        admin_note=f"{title} ({amount:+d}). {note}"[:1000],
    )
    db.add(order)
    await db.flush()
    return order


async def grant_ai_credits(db: AsyncSession, user_id, amount: int, admin_id, note: str) -> None:
    order = await _audit_order(db, user_id, admin_id, "Admin AI kredit qo'shdi", note, amount)
    db.add(AICreditGrant(user_id=user_id, order_id=order.id, credits_total=amount, credits_remaining=amount))
    db.add(BalanceTransaction(user_id=user_id, amount=amount, reason="admin_ai_credit_grant", reference_id=order.id))
    await db.flush()


async def deduct_ai_credits(db: AsyncSession, user_id, amount: int, admin_id, note: str) -> int:
    """Ko'pi bilan `amount` ayiradi, olingan miqdorni qaytaradi (yetmasa qolganini olmaydi)."""
    grants = (await db.execute(
        select(AICreditGrant)
        .where(AICreditGrant.user_id == user_id, AICreditGrant.credits_remaining > 0)
        .order_by(AICreditGrant.created_at, AICreditGrant.id)
        .with_for_update()
    )).scalars().all()
    left, taken = amount, 0
    for grant in grants:
        if left == 0:
            break
        step = min(grant.credits_remaining, left)
        grant.credits_remaining -= step
        left -= step
        taken += step
    if taken == 0:
        return 0
    order = await _audit_order(db, user_id, admin_id, "Admin AI kredit ayirdi", note, -taken)
    db.add(BalanceTransaction(user_id=user_id, amount=-taken, reason="admin_ai_credit_deduct", reference_id=order.id))
    await db.flush()
    return taken


# ---------- Endpointlar ----------

@router.get("/users/{user_id}")
async def user_credit_detail(
    user_id: str,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_role(*ADMIN_ROLES)),
):
    import uuid as _uuid

    try:
        uid = _uuid.UUID(user_id)
    except ValueError as exc:
        raise HTTPException(422, "user_id noto'g'ri") from exc
    if await db.get(User, uid) is None:
        raise HTTPException(404, "Foydalanuvchi topilmadi")
    grants = (await db.execute(
        select(AICreditGrant).where(AICreditGrant.user_id == uid)
        .order_by(AICreditGrant.id.desc()).limit(50)
    )).scalars().all()
    return {
        "user_id": str(uid),
        "balance": await balance(db, uid),
        "grants": [
            {"id": g.id, "credits_total": g.credits_total, "credits_remaining": g.credits_remaining,
             "created_at": g.created_at}
            for g in grants
        ],
    }


@router.post("/users/{user_id}/adjust")
async def adjust_user_credits(
    user_id: str,
    data: AICreditAdjustIn,
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(require_role(*ADMIN_ROLES)),
):
    import uuid as _uuid

    if data.delta == 0:
        raise HTTPException(422, "delta 0 bo'lmasligi kerak")
    try:
        uid = _uuid.UUID(user_id)
    except ValueError as exc:
        raise HTTPException(422, "user_id noto'g'ri") from exc
    if await db.get(User, uid) is None:
        raise HTTPException(404, "Foydalanuvchi topilmadi")

    if data.delta > 0:
        await grant_ai_credits(db, uid, data.delta, admin.id, data.note)
    else:
        current = await balance(db, uid)
        if current < -data.delta:
            raise HTTPException(409, f"Yetarli kredit yo'q (mavjud: {current}, so'ralgan: {-data.delta})")
        await deduct_ai_credits(db, uid, -data.delta, admin.id, data.note)
    await db.commit()
    return {"user_id": str(uid), "delta": data.delta, "balance": await balance(db, uid)}


@router.post("/plans/{plan_id}/adjust")
async def adjust_plan_subscribers_credits(
    plan_id: int,
    data: AICreditAdjustIn,
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(require_role(*ADMIN_ROLES)),
):
    """Tarifdagi (hozir faol obunasi bor) barcha foydalanuvchilarga kredit qo'shadi yoki ayiradi."""
    if data.delta == 0:
        raise HTTPException(422, "delta 0 bo'lmasligi kerak")
    plan = await db.get(SubscriptionPlan, plan_id)
    if plan is None:
        raise HTTPException(404, "Tarif topilmadi")

    now = _now()
    user_ids = set((await db.execute(
        select(UserSubscription.user_id).where(
            UserSubscription.plan_id == plan_id,
            UserSubscription.is_active.is_(True),
            UserSubscription.end_date > now,
        ).distinct()
    )).scalars().all())

    affected = skipped = 0
    changed = 0
    for uid in user_ids:
        if data.delta > 0:
            await grant_ai_credits(db, uid, data.delta, admin.id, data.note)
            affected += 1
            changed += data.delta
        else:
            taken = await deduct_ai_credits(db, uid, -data.delta, admin.id, data.note)
            if taken:
                affected += 1
                changed -= taken
            else:
                skipped += 1
    await db.commit()
    return {
        "plan_id": plan.id,
        "plan_name": plan.name,
        "subscribers": len(user_ids),
        "affected_users": affected,
        "skipped_users": skipped,
        "credits_changed_total": changed,
    }
