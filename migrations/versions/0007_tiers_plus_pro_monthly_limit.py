"""Tariflar: PRO->PLUS, PREMIUM->PRO; tier_skill_limits.monthly_limit

Yangi tarif tuzilmasi:
  FREE  — Reading/Listening/Writing (haftalik limit bilan)
  PLUS  — Reading/Listening/Writing; Writing'ni AI tekshiradi (eski PRO)
  PRO   — + Speaking; Writing/Speaking'ni teacher tekshiradi (eski PREMIUM)

Ma'lumotlar o'tkaziladi: avval PRO->PLUS, keyin PREMIUM->PRO (tartib muhim).
FREE'ning Speaking limit qatori qolsa ham `entitlements.TIER_SKILLS` Speaking'ni FREE/PLUS uchun yopadi.

Revision ID: 0007_tiers_plus_pro_monthly
Revises: 0006_remove_organizations
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import context, op

revision: str = "0007_tiers_plus_pro_monthly"
down_revision: Union[str, None] = "0006_remove_organizations"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    dialect = bind.dialect.name

    if dialect == "postgresql":
        # Native enum: qiymatlarni joyida qayta nomlash (qatorlar avtomatik yangilanadi).
        op.execute("ALTER TYPE subscriptiontier RENAME VALUE 'PRO' TO 'PLUS'")
        op.execute("ALTER TYPE subscriptiontier RENAME VALUE 'PREMIUM' TO 'PRO'")
    else:
        # SQLite va boshqalar: VARCHAR ustun. Tartib muhim.
        for table in ("subscription_plans", "tier_skill_limits"):
            op.execute(f"UPDATE {table} SET tier = 'PLUS' WHERE tier = 'PRO'")
            op.execute(f"UPDATE {table} SET tier = 'PRO' WHERE tier = 'PREMIUM'")

    # Oddiy ADD COLUMN (nullable): SQLite va PostgreSQL'da jadvalni qayta yaratmaydi.
    op.add_column("tier_skill_limits", sa.Column("monthly_limit", sa.Integer(), nullable=True))


def downgrade() -> None:
    raise NotImplementedError("0007 qaytarilmaydi (zaxira nusxadan tiklang).")
