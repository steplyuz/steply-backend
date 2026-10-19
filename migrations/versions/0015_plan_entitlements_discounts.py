"""Add per-plan skill entitlements, AI toggle and direct discount."""
from alembic import op
import sqlalchemy as sa

revision = "0015_plan_entitlements_discounts"
down_revision = "0014_reading_part_mini_tests"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("subscription_plans", recreate="auto") as batch_op:
        batch_op.add_column(sa.Column("included_skills", sa.JSON(), nullable=False, server_default='["READING", "LISTENING", "WRITING"]'))
        batch_op.add_column(sa.Column("ai_enabled", sa.Boolean(), nullable=False, server_default=sa.false()))
        batch_op.add_column(sa.Column("discount_percent", sa.Integer(), nullable=False, server_default="0"))
    # Mavjud Pro tariflarida Speaking oldingi xatti-harakatga mos saqlansin.
    op.execute("UPDATE subscription_plans SET included_skills='[\"READING\", \"LISTENING\", \"WRITING\", \"SPEAKING\"]' WHERE tier='PRO'")
    op.execute("UPDATE subscription_plans SET ai_enabled=1 WHERE ai_limit > 0")


def downgrade() -> None:
    with op.batch_alter_table("subscription_plans", recreate="auto") as batch_op:
        batch_op.drop_column("discount_percent")
        batch_op.drop_column("ai_enabled")
        batch_op.drop_column("included_skills")
