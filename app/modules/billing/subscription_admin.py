"""Admin: obunalar boshqaruvi.

Ro'yxat (holat, tarif, user bo'yicha filtr), muddatni uzaytirish, tarifni o'zgartirish,
muddatni qo'lda o'rnatish, faollashtirish/o'chirish. Har bir o'zgarish BillingOrder
(0 so'm, admin_note) orqali audit qilinadi.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from typing import Literal, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.modules.auth.dependencies import require_role
from app.modules.auth.models import User, UserProfile, UserRole
from app.modules.billing.models import (
    BillingOrder, BillingOrderStatus, BillingProductType, SubscriptionPlan, UserSubscription,
)

router = APIRouter(prefix="/admin/subscriptions", tags=["Admin Subscriptions"])
ADMIN_ROLES = (UserRole.ADMIN, UserRole.SUPER_ADMIN)


class SubscriptionPatchIn(BaseModel):
    plan_id: Optional[int] = None
    end_date: Optional[datetime] = None
    is_active: Optional[bool] = None
    note: str = Field(min_length=3, max_length=300)


class SubscriptionExtendIn(BaseModel):
    days: int = Field(ge=1, le=3650)
    note: str = Field(min_length=3, max_length=300)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _utc(dt: datetime) -> datetime:
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _out(sub: UserSubscription, plan_name: str, full_name: Optional[str]) -> dict:
    now = _now()
    end = _utc(sub.end_date)
    active_now = sub.is_active and end > now
    return {
        "id": sub.id,
        "user_id": str(sub.user_id),
        "full_name": full_name,
        "plan_id": sub.plan_id,
        "plan_name": plan_name,
        "start_date": sub.start_date,
        "end_date": sub.end_date,
        "is_active": sub.is_active,
        "days_left": max(0, (end - now).days) if active_now else 0,
    }


async def _get_sub(db: AsyncSession, sub_id: int) -> UserSubscription:
    sub = await db.get(UserSubscription, sub_id)
    if sub is None:
        raise HTTPException(404, "Obuna topilmadi")
    return sub


async def _row(db: AsyncSession, sub: UserSubscription) -> dict:
    plan_name = await db.scalar(select(SubscriptionPlan.name).where(SubscriptionPlan.id == sub.plan_id))
    full_name = await db.scalar(select(UserProfile.full_name).where(UserProfile.user_id == sub.user_id))
    return _out(sub, plan_name or "", full_name)


async def _audit(db: AsyncSession, sub: UserSubscription, admin_id: uuid.UUID, title: str, note: str) -> None:
    db.add(BillingOrder(
        user_id=sub.user_id,
        product_type=BillingProductType.SUBSCRIPTION,
        product_id=str(sub.plan_id),
        product_title=title[:200],
        amount=0,
        original_amount=0,
        discount_amount=0,
        status=BillingOrderStatus.PAID,
        reviewed_by=admin_id,
        reviewed_at=_now(),
        admin_note=f"{title}. {note}"[:1000],
    ))
    await db.flush()


@router.get("")
async def list_subscriptions(
    status: Literal["active", "expired", "all"] = "all",
    plan_id: Optional[int] = None,
    user_id: Optional[str] = None,
    offset: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=200),
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_role(*ADMIN_ROLES)),
):
    now = _now()
    stmt = (
        select(UserSubscription, SubscriptionPlan.name, UserProfile.full_name)
        .join(SubscriptionPlan, SubscriptionPlan.id == UserSubscription.plan_id)
        .outerjoin(UserProfile, UserProfile.user_id == UserSubscription.user_id)
    )
    if status == "active":
        stmt = stmt.where(UserSubscription.is_active.is_(True), UserSubscription.end_date > now)
    elif status == "expired":
        stmt = stmt.where(or_(UserSubscription.is_active.is_(False), UserSubscription.end_date <= now))
    if plan_id is not None:
        stmt = stmt.where(UserSubscription.plan_id == plan_id)
    if user_id:
        try:
            stmt = stmt.where(UserSubscription.user_id == uuid.UUID(user_id))
        except ValueError as exc:
            raise HTTPException(422, "user_id noto‘g‘ri") from exc
    total = await db.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    rows = (await db.execute(
        stmt.order_by(UserSubscription.end_date.desc(), UserSubscription.id.desc()).offset(offset).limit(limit)
    )).all()
    return {"total": int(total), "items": [_out(sub, plan_name, full_name) for sub, plan_name, full_name in rows]}


@router.patch("/{sub_id}")
async def update_subscription(
    sub_id: int,
    data: SubscriptionPatchIn,
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(require_role(*ADMIN_ROLES)),
):
    sub = await _get_sub(db, sub_id)
    changes: list[str] = []
    if data.plan_id is not None and data.plan_id != sub.plan_id:
        plan = await db.get(SubscriptionPlan, data.plan_id)
        if plan is None:
            raise HTTPException(422, "Tarif topilmadi")
        changes.append(f"tarif {sub.plan_id} -> {data.plan_id}")
        sub.plan_id = data.plan_id
    if data.end_date is not None:
        end = _utc(data.end_date)
        if end <= _utc(sub.start_date):
            raise HTTPException(422, "Tugash sanasi boshlanish sanasidan keyin bo‘lishi kerak")
        changes.append(f"muddat {sub.end_date.isoformat()} -> {end.isoformat()}")
        sub.end_date = end
    if data.is_active is not None and data.is_active != sub.is_active:
        changes.append("faollashtirildi" if data.is_active else "o‘chirildi")
        sub.is_active = data.is_active
    if not changes:
        raise HTTPException(422, "O‘zgarish yo‘q")
    await _audit(db, sub, admin.id, "Admin obunani o‘zgartirdi", f"{'; '.join(changes)}. {data.note}")
    await db.commit()
    return await _row(db, sub)


@router.post("/{sub_id}/extend")
async def extend_subscription(
    sub_id: int,
    data: SubscriptionExtendIn,
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(require_role(*ADMIN_ROLES)),
):
    """Muddatni N kunga uzaytiradi. Muddati tugagan bo‘lsa, hozirdan boshlab hisoblanadi va obuna faollashadi."""
    sub = await _get_sub(db, sub_id)
    now = _now()
    base = _utc(sub.end_date) if _utc(sub.end_date) > now else now
    new_end = base + timedelta(days=data.days)
    before = sub.end_date.isoformat()
    sub.end_date = new_end
    sub.is_active = True
    await _audit(db, sub, admin.id, "Admin obuna muddatini uzaytirdi", f"+{data.days} kun ({before} -> {new_end.isoformat()}). {data.note}")
    await db.commit()
    return await _row(db, sub)
