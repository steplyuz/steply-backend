"""Admin: referal sozlamalari, statistika va ro'yxat."""
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.modules.auth.dependencies import require_role
from app.modules.auth.models import User, UserProfile, UserRole
from app.modules.billing.models import SubscriptionPlan
from app.modules.referral import service
from app.modules.referral.models import Referral, ReferralCredit

router = APIRouter(prefix="/admin/referral", tags=["Admin Referral"])
ADMIN_ROLES = (UserRole.ADMIN, UserRole.SUPER_ADMIN)


class ReferralSettingsIn(BaseModel):
    enabled: bool
    reward_credits: int = Field(default=0, ge=0, le=1000)
    credit_expiry_days: Optional[int] = Field(default=None, ge=1, le=3650)
    reward_plan_id: Optional[int] = None
    reward_tier_days: int = Field(default=0, ge=0, le=3650)
    max_rewards_per_user: Optional[int] = Field(default=None, ge=1)


def _settings_out(s) -> dict:
    return {
        "enabled": s.enabled,
        "reward_credits": s.reward_credits,
        "credit_expiry_days": s.credit_expiry_days,
        "reward_plan_id": s.reward_plan_id,
        "reward_tier_days": s.reward_tier_days,
        "max_rewards_per_user": s.max_rewards_per_user,
    }


@router.get("/settings")
async def get_referral_settings(db: AsyncSession = Depends(get_db), _: User = Depends(require_role(*ADMIN_ROLES))):
    s = await service.get_settings(db)
    await db.commit()
    return _settings_out(s)


@router.put("/settings")
async def update_referral_settings(
    payload: ReferralSettingsIn,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_role(*ADMIN_ROLES)),
):
    if payload.reward_tier_days > 0:
        if not payload.reward_plan_id:
            raise HTTPException(400, "Tarif mukofoti uchun reward_plan_id ko'rsating")
        if await db.get(SubscriptionPlan, payload.reward_plan_id) is None:
            raise HTTPException(400, "Plan topilmadi")
    s = await service.get_settings(db)
    for key, value in payload.model_dump().items():
        setattr(s, key, value)
    await db.commit()
    return _settings_out(s)


@router.get("/stats")
async def referral_stats(db: AsyncSession = Depends(get_db), _: User = Depends(require_role(*ADMIN_ROLES))):
    status_rows = (await db.execute(select(Referral.status, func.count(Referral.id)).group_by(Referral.status))).all()
    granted = await db.scalar(select(func.coalesce(func.sum(ReferralCredit.amount_total), 0))) or 0
    remaining = await db.scalar(select(func.coalesce(func.sum(ReferralCredit.amount_remaining), 0))) or 0
    top = (await db.execute(
        select(Referral.referrer_id, func.count(Referral.id).label("n"))
        .where(Referral.status == service.STATUS_QUALIFIED)
        .group_by(Referral.referrer_id)
        .order_by(func.count(Referral.id).desc())
        .limit(10)
    )).all()
    return {
        "by_status": {s: n for s, n in status_rows},
        "credits_granted": int(granted),
        "credits_remaining": int(remaining),
        "top_referrers": [{"user_id": str(uid), "qualified": int(n)} for uid, n in top],
    }


@router.get("/referrals")
async def list_referrals(
    status: Optional[str] = Query(None, pattern="^(pending|qualified|capped)$"),
    offset: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=200),
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_role(*ADMIN_ROLES)),
):
    stmt = select(Referral)
    if status:
        stmt = stmt.where(Referral.status == status)
    total = await db.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    refs = (await db.execute(stmt.order_by(Referral.id.desc()).offset(offset).limit(limit))).scalars().all()

    ids = {r.referrer_id for r in refs} | {r.referred_id for r in refs}
    names: dict = {}
    if ids:
        rows = (await db.execute(
            select(UserProfile.user_id, UserProfile.full_name).where(UserProfile.user_id.in_(ids))
        )).all()
        names = {uid: name for uid, name in rows}

    return {
        "total": total,
        "items": [{
            "id": r.id,
            "referrer": {"id": str(r.referrer_id), "name": names.get(r.referrer_id)},
            "referred": {"id": str(r.referred_id), "name": names.get(r.referred_id)},
            "status": r.status,
            "created_at": r.created_at,
            "qualified_at": r.qualified_at,
        } for r in refs],
    }
