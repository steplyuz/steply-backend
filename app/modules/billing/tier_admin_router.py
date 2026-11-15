"""Admin: tarif skill matritsasi, amaliyot limitlari, tarif -> test ruxsati."""
from typing import Literal, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.modules.auth.dependencies import require_role
from app.modules.auth.models import User, UserRole
from app.modules.billing.models import SubscriptionTier, TierPracticeLimit
from app.modules.billing.tier_config import (
    DEFAULT_SKILLS, SKILL_NAMES, TIERS, TierSkillAccess, TierTestAccess, tier_skill_matrix,
)

router = APIRouter(prefix="/admin/tiers", tags=["Admin Tiers"])
ADMIN_ROLES = (UserRole.ADMIN, UserRole.SUPER_ADMIN)


class SkillMatrixIn(BaseModel):
    skills: dict[str, bool]


class LimitIn(BaseModel):
    weekly_limit: Optional[int] = Field(default=None, ge=1, le=10000)
    monthly_limit: Optional[int] = Field(default=None, ge=1, le=100000)


class TestAccessIn(BaseModel):
    tiers: list[Literal["free", "plus", "pro"]]


def _tier(value: str) -> SubscriptionTier:
    try:
        return SubscriptionTier(value.lower())
    except ValueError as exc:
        raise HTTPException(404, "Tarif topilmadi") from exc


@router.get("/skills")
async def get_skill_matrix(db: AsyncSession = Depends(get_db), _: User = Depends(require_role(*ADMIN_ROLES))):
    return await tier_skill_matrix(db)


@router.put("/skills/{tier}")
async def put_skill_matrix(
    tier: str,
    payload: SkillMatrixIn,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_role(*ADMIN_ROLES)),
):
    t = _tier(tier)
    given = {k.upper(): v for k, v in payload.skills.items()}
    unknown = set(given) - set(SKILL_NAMES)
    if unknown:
        raise HTTPException(400, f"Noma'lum skill: {', '.join(sorted(unknown))}")
    await db.execute(delete(TierSkillAccess).where(TierSkillAccess.tier == t))
    for s in SKILL_NAMES:
        enabled = given.get(s, s in DEFAULT_SKILLS.get(t, frozenset()))
        db.add(TierSkillAccess(tier=t, skill=s, enabled=bool(enabled)))
    await db.commit()
    return await tier_skill_matrix(db)


@router.get("/limits")
async def get_limits(db: AsyncSession = Depends(get_db), _: User = Depends(require_role(*ADMIN_ROLES))):
    rows = {r.tier: r for r in (await db.execute(select(TierPracticeLimit))).scalars().all()}
    return [{
        "tier": t.value,
        "weekly_limit": rows[t].weekly_limit if t in rows else None,
        "monthly_limit": rows[t].monthly_limit if t in rows else None,
    } for t in TIERS]


@router.put("/limits/{tier}")
async def put_limits(
    tier: str,
    payload: LimitIn,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_role(*ADMIN_ROLES)),
):
    t = _tier(tier)
    row = (await db.execute(select(TierPracticeLimit).where(TierPracticeLimit.tier == t))).scalar_one_or_none()
    if row is None:
        row = TierPracticeLimit(tier=t)
        db.add(row)
    row.weekly_limit = payload.weekly_limit
    row.monthly_limit = payload.monthly_limit
    await db.commit()
    return {"tier": t.value, "weekly_limit": row.weekly_limit, "monthly_limit": row.monthly_limit}


@router.get("/test-access")
async def list_test_access(
    skill: Optional[str] = Query(None),
    test_id: Optional[str] = Query(None, max_length=64),
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_role(*ADMIN_ROLES)),
):
    stmt = select(TierTestAccess)
    if skill:
        stmt = stmt.where(TierTestAccess.skill == skill.upper())
    if test_id:
        stmt = stmt.where(TierTestAccess.test_id == test_id)
    rows = (await db.execute(stmt.order_by(TierTestAccess.skill, TierTestAccess.test_id))).scalars().all()
    return [{"skill": r.skill, "test_id": r.test_id, "tier": r.tier.value} for r in rows]


@router.put("/test-access/{skill}/{test_id}")
async def set_test_access(
    skill: str,
    test_id: str,
    payload: TestAccessIn,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_role(*ADMIN_ROLES)),
):
    """Testning tariflarini almashtiradi. Bo'sh ro'yxat = qoida o'chiriladi (eski qoida qaytadi)."""
    s = skill.upper()
    if s not in SKILL_NAMES:
        raise HTTPException(400, "Noma'lum skill")
    tiers = {_tier(t) for t in payload.tiers}
    await db.execute(delete(TierTestAccess).where(TierTestAccess.skill == s, TierTestAccess.test_id == test_id))
    for t in tiers:
        db.add(TierTestAccess(tier=t, skill=s, test_id=test_id))
    await db.commit()
    return {"skill": s, "test_id": test_id, "tiers": sorted(t.value for t in tiers)}
