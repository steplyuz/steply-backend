"""user activity daily

Revision ID: 0029_user_activity_daily
Revises: 0028_onboarding_exam_pending
"""
import sqlalchemy as sa
from alembic import op

revision = "0029_user_activity_daily"
down_revision = "0028_onboarding_exam_pending"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "user_activity_daily",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("user_id", sa.Uuid(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("day", sa.Date(), nullable=False),
        sa.Column("active_seconds", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("heartbeats", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("user_id", "day", name="uq_user_activity_user_day"),
    )
    op.create_index("ix_user_activity_daily_user_id", "user_activity_daily", ["user_id"])
    op.create_index("ix_user_activity_daily_day", "user_activity_daily", ["day"])


def downgrade() -> None:
    op.drop_index("ix_user_activity_daily_day", table_name="user_activity_daily")
    op.drop_index("ix_user_activity_daily_user_id", table_name="user_activity_daily")
    op.drop_table("user_activity_daily")
