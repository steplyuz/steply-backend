"""Admin: referal sozlamalari, statistika va ro'yxat."""
import uuid
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
from app.modules.referral.models import Referral, ReferralCode, ReferralCredit

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
    status: Optional[str] = Query(None, pattern="^(pending|qualified|capped|voided)$"),
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


# ---------- User bo'yicha to'liq boshqaruv ----------

class ReferralCreditAdjustIn(BaseModel):
    delta: int = Field(..., ge=-1000, le=1000, description="Musbat: qo'shish, manfiy: ayirish")
    note: str = Field(min_length=3, max_length=200)


class ReferralVoidIn(BaseModel):
    reason: str = Field(min_length=3, max_length=200)


def _ref_row(r: Referral) -> dict:
    return {
        "id": r.id,
        "referrer_id": str(r.referrer_id),
        "referred_id": str(r.referred_id),
        "status": r.status,
        "created_at": r.created_at,
        "qualified_at": r.qualified_at,
    }


def _credit_row(c: ReferralCredit) -> dict:
    return {
        "id": c.id,
        "amount_total": c.amount_total,
        "amount_remaining": c.amount_remaining,
        "expires_at": c.expires_at,
        "referral_id": c.referral_id,
        "reason": c.reason,
        "created_at": c.created_at,
    }


def _parse_uuid(value: str) -> uuid.UUID:
    try:
        return uuid.UUID(value)
    except ValueError as exc:
        raise HTTPException(422, "user_id noto'g'ri") from exc


@router.get("/users/{user_id}")
async def referral_user_detail(
    user_id: str,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_role(*ADMIN_ROLES)),
):
    uid = _parse_uuid(user_id)
    if await db.get(User, uid) is None:
        raise HTTPException(404, "Foydalanuvchi topilmadi")
    summary = await service.user_summary(db, uid)
    referred_by = await db.scalar(select(Referral).where(Referral.referred_id == uid))
    referrals = (await db.execute(
        select(Referral).where(Referral.referrer_id == uid).order_by(Referral.id.desc()).limit(100)
    )).scalars().all()
    credits = (await db.execute(
        select(ReferralCredit).where(ReferralCredit.user_id == uid).order_by(ReferralCredit.id.desc()).limit(100)
    )).scalars().all()
    return {
        "user_id": str(uid),
        "summary": summary,
        "referred_by": _ref_row(referred_by) if referred_by else None,
        "referrals": [_ref_row(r) for r in referrals],
        "credits": [_credit_row(c) for c in credits],
    }


@router.post("/users/{user_id}/credits")
async def adjust_referral_credits(
    user_id: str,
    payload: ReferralCreditAdjustIn,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_role(*ADMIN_ROLES)),
):
    uid = _parse_uuid(user_id)
    if await db.get(User, uid) is None:
        raise HTTPException(404, "Foydalanuvchi topilmadi")
    if payload.delta == 0:
        raise HTTPException(422, "delta 0 bo'lmasligi kerak")
    try:
        await service.adjust_credits(db, uid, payload.delta, payload.note)
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc
    await db.commit()
    return {"user_id": str(uid), "delta": payload.delta, "summary": await service.user_summary(db, uid)}


@router.post("/users/{user_id}/reset-code")
async def reset_referral_code(
    user_id: str,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_role(*ADMIN_ROLES)),
):
    """Eski kodni bekor qilib yangi kod beradi (suiiste'mol bo'lsa)."""
    uid = _parse_uuid(user_id)
    if await db.get(User, uid) is None:
        raise HTTPException(404, "Foydalanuvchi topilmadi")
    old = await db.scalar(select(ReferralCode).where(ReferralCode.user_id == uid))
    if old is not None:
        await db.delete(old)
        await db.flush()
    code = await service.ensure_code(db, uid)
    await db.commit()
    return {"user_id": str(uid), "code": code}


@router.post("/referrals/{referral_id}/void")
async def void_referral_endpoint(
    referral_id: int,
    payload: ReferralVoidIn,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_role(*ADMIN_ROLES)),
):
    ref = await db.get(Referral, referral_id)
    if ref is None:
        raise HTTPException(404, "Referal topilmadi")
    if ref.status == service.STATUS_VOIDED:
        raise HTTPException(409, "Referal allaqachon bekor qilingan")
    await service.void_referral(db, ref, payload.reason)
    await db.commit()
    return _ref_row(ref)
