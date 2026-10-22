"""Pulik testlar pulik obunaga kiradi: PLUS planlariga SPEAKING qo'shiladi.

Revision ID: 0019_plus_includes_speaking
Revises: 0018_listening_paper_layout
"""
from alembic import op

revision = "0019_plus_includes_speaking"
down_revision = "0018_listening_paper_layout"
branch_labels = None
depends_on = None

_WITH_SPEAKING = '["READING", "LISTENING", "WRITING", "SPEAKING"]'
_WITHOUT_SPEAKING = '["READING", "LISTENING", "WRITING"]'


def upgrade() -> None:
    op.execute(f"UPDATE subscription_plans SET included_skills='{_WITH_SPEAKING}' WHERE tier='PLUS'")


def downgrade() -> None:
    op.execute(f"UPDATE subscription_plans SET included_skills='{_WITHOUT_SPEAKING}' WHERE tier='PLUS'")
