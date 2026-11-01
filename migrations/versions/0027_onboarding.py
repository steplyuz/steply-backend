"""Onboarding: CEFR/maqsad javoblari, AI rejasi va admin imtihon sanalari.

Revision ID: 0027_onboarding
Revises: 0026_unified_ai_credits
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect

revision = "0027_onboarding"
down_revision = "0026_unified_ai_credits"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    if bind.__class__.__name__ == "MockConnection":
        raise RuntimeError("0027_onboarding offline (--sql) rejimda ishlamaydi")
    tables = set(inspect(bind).get_table_names())

    if "onboarding_exam_dates" not in tables:
        op.create_table(
            "onboarding_exam_dates",
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column("title", sa.String(255), nullable=False),
            sa.Column("exam_date", sa.Date(), nullable=False),
            sa.Column("register_url", sa.String(500), nullable=False),
            sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        )
        op.create_index("ix_onboarding_exam_dates_exam_date", "onboarding_exam_dates", ["exam_date"])

    if "onboarding_profiles" not in tables:
        op.create_table(
            "onboarding_profiles",
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column("user_id", sa.Uuid(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False, unique=True),
            sa.Column("cefr_taken", sa.Boolean(), nullable=True),
            sa.Column("cefr_level", sa.String(2), nullable=True),
            sa.Column("estimated_level", sa.String(2), nullable=True),
            sa.Column("target_level", sa.String(2), nullable=True),
            sa.Column("vocab_band", sa.String(16), nullable=True),
            sa.Column("strengths", sa.JSON(), nullable=True),
            sa.Column("weaknesses", sa.JSON(), nullable=True),
            sa.Column("exam_date_id", sa.Integer(),
                      sa.ForeignKey("onboarding_exam_dates.id", ondelete="SET NULL"), nullable=True),
            sa.Column("daily_hours", sa.Float(), nullable=True),
            sa.Column("teacher_hours", sa.Float(), nullable=True),
            sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("plan", sa.JSON(), nullable=True),
            sa.Column("plan_version", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("plan_generated_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        )


def downgrade() -> None:
    op.drop_table("onboarding_profiles")
    op.drop_index("ix_onboarding_exam_dates_exam_date", table_name="onboarding_exam_dates")
    op.drop_table("onboarding_exam_dates")
