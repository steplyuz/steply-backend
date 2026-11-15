"""referral and tier config

Revision ID: 0030_referral_and_tier_config
Revises: 0029_user_activity_daily
"""
import sqlalchemy as sa
from alembic import op

revision = "0030_referral_and_tier_config"
down_revision = "0029_user_activity_daily"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "referral_codes",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("user_id", sa.Uuid(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False, unique=True),
        sa.Column("code", sa.String(16), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_referral_codes_code", "referral_codes", ["code"], unique=True)

    op.create_table(
        "referrals",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("referrer_id", sa.Uuid(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("referred_id", sa.Uuid(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False, unique=True),
        sa.Column("status", sa.String(16), nullable=False, server_default="pending"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("qualified_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_referrals_referrer_id", "referrals", ["referrer_id"])
    op.create_index("ix_referrals_status", "referrals", ["status"])

    op.create_table(
        "referral_settings",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("reward_credits", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("credit_expiry_days", sa.Integer(), nullable=True),
        sa.Column("reward_plan_id", sa.Integer(), sa.ForeignKey("subscription_plans.id", ondelete="SET NULL"), nullable=True),
        sa.Column("reward_tier_days", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("max_rewards_per_user", sa.Integer(), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )

    op.create_table(
        "referral_credits",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("user_id", sa.Uuid(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("referral_id", sa.Integer(), sa.ForeignKey("referrals.id", ondelete="CASCADE"), nullable=False, unique=True),
        sa.Column("amount_total", sa.Integer(), nullable=False),
        sa.Column("amount_remaining", sa.Integer(), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_referral_credits_user_id", "referral_credits", ["user_id"])
    op.create_index("ix_referral_credits_expires_at", "referral_credits", ["expires_at"])

    op.create_table(
        "tier_skill_access",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("tier", sa.String(7), nullable=False),
        sa.Column("skill", sa.String(20), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.UniqueConstraint("tier", "skill", name="uq_tier_skill_access"),
    )

    op.create_table(
        "tier_test_access",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("tier", sa.String(7), nullable=False),
        sa.Column("skill", sa.String(20), nullable=False),
        sa.Column("test_id", sa.String(64), nullable=False),
        sa.UniqueConstraint("tier", "skill", "test_id", name="uq_tier_test_access"),
    )
    op.create_index("ix_tier_test_access_skill_test", "tier_test_access", ["skill", "test_id"])


def downgrade() -> None:
    op.drop_index("ix_tier_test_access_skill_test", table_name="tier_test_access")
    op.drop_table("tier_test_access")
    op.drop_table("tier_skill_access")
    op.drop_index("ix_referral_credits_expires_at", table_name="referral_credits")
    op.drop_index("ix_referral_credits_user_id", table_name="referral_credits")
    op.drop_table("referral_credits")
    op.drop_table("referral_settings")
    op.drop_index("ix_referrals_status", table_name="referrals")
    op.drop_index("ix_referrals_referrer_id", table_name="referrals")
    op.drop_table("referrals")
    op.drop_index("ix_referral_codes_code", table_name="referral_codes")
    op.drop_table("referral_codes")
