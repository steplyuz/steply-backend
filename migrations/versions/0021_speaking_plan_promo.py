"""Speaking AI tarif limiti va promo -> tarif bog'lanishi.

Revision ID: 0021_speaking_plan_promo
Revises: 0020_speaking_ai
"""
from alembic import op
import sqlalchemy as sa

revision = "0021_speaking_plan_promo"
down_revision = "0020_speaking_ai"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("subscription_plans") as batch:
        batch.add_column(sa.Column("speaking_ai_limit", sa.Integer(), nullable=False, server_default="0"))

    with op.batch_alter_table("promo_codes") as batch:
        batch.add_column(sa.Column("activates_plan_id", sa.Integer(), nullable=True))
        batch.create_foreign_key(
            "fk_promo_codes_activates_plan_id", "subscription_plans",
            ["activates_plan_id"], ["id"], ondelete="SET NULL",
        )
        batch.create_index("ix_promo_codes_activates_plan_id", ["activates_plan_id"])


def downgrade() -> None:
    with op.batch_alter_table("promo_codes") as batch:
        batch.drop_index("ix_promo_codes_activates_plan_id")
        batch.drop_constraint("fk_promo_codes_activates_plan_id", type_="foreignkey")
        batch.drop_column("activates_plan_id")

    with op.batch_alter_table("subscription_plans") as batch:
        batch.drop_column("speaking_ai_limit")
