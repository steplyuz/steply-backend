"""Onboarding: imtihon sanasi e'lon qilinmagan holati (exam_date_pending).

Revision ID: 0028_onboarding_exam_pending
Revises: 0027_onboarding
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect

revision = "0028_onboarding_exam_pending"
down_revision = "0027_onboarding"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    if bind.__class__.__name__ == "MockConnection":
        raise RuntimeError("0028_onboarding_exam_pending offline (--sql) rejimda ishlamaydi")
    columns = {c["name"] for c in inspect(bind).get_columns("onboarding_profiles")}
    if "exam_date_pending" not in columns:
        op.add_column(
            "onboarding_profiles",
            sa.Column("exam_date_pending", sa.Boolean(), nullable=False, server_default=sa.false()),
        )


def downgrade() -> None:
    op.drop_column("onboarding_profiles", "exam_date_pending")