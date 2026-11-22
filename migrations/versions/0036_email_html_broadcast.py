"""email HTML: shablon va xabarlarda html_body

Revision ID: 0036_email_html_broadcast
Revises: 0035_registration_method
"""
import sqlalchemy as sa
from alembic import op

revision = "0036_email_html_broadcast"
down_revision = "0035_registration_method"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("notification_templates", sa.Column("html_body", sa.Text(), nullable=True))
    op.add_column("notification_messages", sa.Column("html_body", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("notification_messages", "html_body")
    op.drop_column("notification_templates", "html_body")
