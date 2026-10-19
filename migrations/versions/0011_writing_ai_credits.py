"""Add separately purchasable Writing AI credits.

Revision ID: 0011_writing_ai_credits
Revises: 0010_unified_practice_analysis
"""
from alembic import op
import sqlalchemy as sa

revision = "0011_writing_ai_credits"
down_revision = "0010_unified_practice_analysis"
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()
    # This migration uses live-schema inspection to be idempotent. Alembic's
    # offline --sql mode exposes a MockConnection, which cannot be inspected.
    # Match 0010_unified_practice_analysis: omit conditional DDL in offline mode.
    if bind.__class__.__name__ == "MockConnection":
        return
    if bind.dialect.name == "postgresql":
        # BillingProductType is a native PostgreSQL enum in production.
        op.execute("ALTER TYPE billingproducttype ADD VALUE IF NOT EXISTS 'WRITING_AI_CREDITS'")

    inspector = sa.inspect(bind)
    if "writing_ai_credit_grants" not in inspector.get_table_names():
        op.create_table(
            "writing_ai_credit_grants",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("user_id", sa.Uuid(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
            sa.Column("order_id", sa.String(length=36), sa.ForeignKey("billing_orders.id", ondelete="CASCADE"), nullable=False, unique=True),
            sa.Column("credits_total", sa.Integer(), nullable=False),
            sa.Column("credits_remaining", sa.Integer(), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
            sa.CheckConstraint("credits_total > 0", name="ck_writing_ai_credits_total_positive"),
            sa.CheckConstraint("credits_remaining >= 0 AND credits_remaining <= credits_total", name="ck_writing_ai_credits_remaining_range"),
        )
    inspector = sa.inspect(bind)
    if "writing_ai_usage" not in inspector.get_table_names():
        op.create_table(
            "writing_ai_usage",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("user_id", sa.Uuid(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
            sa.Column("answer_id", sa.Integer(), sa.ForeignKey("writing_answers.id", ondelete="CASCADE"), nullable=False, unique=True),
            sa.Column("billing_source", sa.String(length=20), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
            sa.CheckConstraint("billing_source IN ('subscription', 'purchased')", name="ck_writing_ai_usage_source"),
        )
    usage_indexes = {i["name"] for i in sa.inspect(bind).get_indexes("writing_ai_usage")}
    if "ix_writing_ai_usage_user_id" not in usage_indexes:
        op.create_index("ix_writing_ai_usage_user_id", "writing_ai_usage", ["user_id"])
    if "ix_writing_ai_usage_user_source_created" not in usage_indexes:
        op.create_index("ix_writing_ai_usage_user_source_created", "writing_ai_usage", ["user_id", "billing_source", "created_at"])

    inspector = sa.inspect(bind)
    indexes = {i["name"] for i in inspector.get_indexes("writing_ai_credit_grants")}
    if "ix_writing_ai_credit_grants_user_id" not in indexes:
        op.create_index("ix_writing_ai_credit_grants_user_id", "writing_ai_credit_grants", ["user_id"])
    if "ix_writing_ai_credits_user_remaining" not in indexes:
        op.create_index("ix_writing_ai_credits_user_remaining", "writing_ai_credit_grants", ["user_id", "credits_remaining"])


def downgrade():
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if "writing_ai_usage" in inspector.get_table_names():
        op.drop_table("writing_ai_usage")
    if "writing_ai_credit_grants" in inspector.get_table_names():
        op.drop_table("writing_ai_credit_grants")
    # PostgreSQL enum values cannot be removed safely in a portable migration.
