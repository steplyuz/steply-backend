"""tier_ai_limits: obunasiz (FREE) foydalanuvchilar uchun oylik AI kredit

Revision ID: 0037_tier_ai_limits
Revises: 0036_email_html_broadcast
"""
import sqlalchemy as sa
from alembic import op

revision = "0037_tier_ai_limits"
down_revision = "0036_email_html_broadcast"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "tier_ai_limits",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("tier", sa.String(length=7), nullable=False),
        sa.Column("monthly_limit", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("tier"),
    )


def downgrade() -> None:
    op.drop_table("tier_ai_limits")
