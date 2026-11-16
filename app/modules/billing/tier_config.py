"""Admin boshqaradigan tarif sozlamalari: tarif -> skill ruxsati, tarif -> test ruxsati.

Qoidalar:
* tier_skill_access: qatorlar bo'lsa shu ishlatiladi, aks holda DEFAULT_SKILLS (tier_config.py ichida) ishlatiladi.
* tier_test_access: test uchun qator bo'lsa, test faqat shu tarif(lar)ga ochiq
  (yuqori tarif quyi tarifning testlarini ham ochadi). Qator bo'lmasa — eski qoida
  (FREE faqat is_free testlar, pullik tariflar hammasini).
"""
from __future__ import annotations

from typing import Optional

from sqlalchemy import Boolean, Enum as SAEnum, Index, Integer, String, UniqueConstraint, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base
from app.modules.billing.models import SubscriptionTier

SKILL_NAMES = ("READING", "LISTENING", "WRITING", "SPEAKING")
TIERS = (SubscriptionTier.FREE, SubscriptionTier.PLUS, SubscriptionTier.PRO)
TIER_RANK = {SubscriptionTier.FREE: 0, SubscriptionTier.PLUS: 1, SubscriptionTier.PRO: 2}

DEFAULT_SKILLS: dict[SubscriptionTier, frozenset[str]] = {
    SubscriptionTier.FREE: frozenset({"READING", "LISTENING", "WRITING"}),
    SubscriptionTier.PLUS: frozenset({"READING", "LISTENING", "WRITING", "SPEAKING"}),
    SubscriptionTier.PRO: frozenset({"READING", "LISTENING", "WRITING", "SPEAKING"}),
}


class TierSkillAccess(Base):
    __tablename__ = "tier_skill_access"
    __table_args__ = (UniqueConstraint("tier", "skill", name="uq_tier_skill_access"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    tier: Mapped[SubscriptionTier] = mapped_column(SAEnum(SubscriptionTier, length=7), nullable=False)
    skill: Mapped[str] = mapped_column(String(20), nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)


class TierTestAccess(Base):
    __tablename__ = "tier_test_access"
    __table_args__ = (
        UniqueConstraint("tier", "skill", "test_id", name="uq_tier_test_access"),
        Index("ix_tier_test_access_skill_test", "skill", "test_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    tier: Mapped[SubscriptionTier] = mapped_column(SAEnum(SubscriptionTier, length=7), nullable=False)
    skill: Mapped[str] = mapped_column(String(20), nullable=False)
    test_id: Mapped[str] = mapped_column(String(64), nullable=False)


async def tier_skill_enabled(db: AsyncSession, tier: SubscriptionTier, skill: str) -> bool:
    skill = skill.upper()
    rows = (await db.execute(
        select(TierSkillAccess.skill, TierSkillAccess.enabled).where(TierSkillAccess.tier == tier)
    )).all()
    if not rows:
        return skill in DEFAULT_SKILLS.get(tier, frozenset())
    return any(s == skill and e for s, e in rows)


async def tier_skill_matrix(db: AsyncSession) -> dict[str, dict[str, bool]]:
    rows = (await db.execute(select(TierSkillAccess.tier, TierSkillAccess.skill, TierSkillAccess.enabled))).all()
    stored = {(t, s): e for t, s, e in rows}
    return {
        tier.value: {s: stored.get((tier, s), s in DEFAULT_SKILLS[tier]) for s in SKILL_NAMES}
        for tier in TIERS
    }


async def test_tier_access(db: AsyncSession, tier: SubscriptionTier, skill: str, test_id) -> Optional[bool]:
    """True/False: test boshqarilgan. None: test uchun qoida yo'q (eski qoida ishlaydi)."""
    tiers = (await db.execute(
        select(TierTestAccess.tier).where(
            TierTestAccess.skill == skill.upper(), TierTestAccess.test_id == str(test_id)
        )
    )).scalars().all()
    if not tiers:
        return None
    return any(TIER_RANK[SubscriptionTier(t)] <= TIER_RANK[tier] for t in tiers)
