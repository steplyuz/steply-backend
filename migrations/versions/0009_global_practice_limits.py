"""Global practice limits and analysis usage foundation.
Revision ID: 0009_global_practice_limits
Revises: 0008_enable_sms_registration
"""
from alembic import op
import sqlalchemy as sa
revision = "0009_global_practice_limits"
down_revision = "0008_enable_sms_registration"
branch_labels = None
depends_on = None

def upgrade():
    op.create_table("tier_practice_limits", sa.Column("id", sa.Integer(), primary_key=True), sa.Column("tier", sa.Enum("FREE", "PLUS", "PRO", name="subscriptiontier", length=7), nullable=False, unique=True), sa.Column("weekly_limit", sa.Integer(), nullable=True), sa.Column("monthly_limit", sa.Integer(), nullable=True), sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False))
    op.create_table("analysis_usage_log", sa.Column("id", sa.Integer(), primary_key=True), sa.Column("user_id", sa.Uuid(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False), sa.Column("skill", sa.String(20), nullable=False), sa.Column("test_id", sa.String(64), nullable=False), sa.Column("week_start", sa.Date(), nullable=False), sa.Column("consumed_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False))
    op.create_index("ix_analysis_usage_log_user_id", "analysis_usage_log", ["user_id"])
    op.execute("INSERT INTO tier_practice_limits (tier, weekly_limit, monthly_limit) VALUES ('FREE', 20, NULL)")
    op.execute("INSERT INTO tier_practice_limits (tier, weekly_limit, monthly_limit) VALUES ('PLUS', NULL, NULL)")
    op.execute("INSERT INTO tier_practice_limits (tier, weekly_limit, monthly_limit) VALUES ('PRO', NULL, NULL)")

def downgrade():
    op.drop_table("analysis_usage_log")
    op.drop_table("tier_practice_limits")
