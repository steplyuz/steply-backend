"""in_app bildirishnomalar: foydalanuvchi inbox jadvali va notification_messages.link

Revision ID: 0034_in_app_notifications
Revises: 0033_notification_admin_controls
"""
import sqlalchemy as sa
from alembic import op

revision = "0034_in_app_notifications"
down_revision = "0033_notification_admin_controls"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "user_notifications",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("title", sa.String(length=255), nullable=False),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("link", sa.String(length=500), nullable=True),
        sa.Column("category", sa.String(length=16), server_default="transactional", nullable=False),
        sa.Column("is_read", sa.Boolean(), server_default=sa.false(), nullable=False),
        sa.Column("message_id", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("read_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["message_id"], ["notification_messages.id"], ondelete="SET NULL"),
    )
    op.create_index("ix_user_notifications_user_id", "user_notifications", ["user_id"])
    op.create_index("ix_user_notifications_user_unread", "user_notifications", ["user_id", "is_read"])

    with op.batch_alter_table("notification_messages") as batch:
        batch.add_column(sa.Column("link", sa.String(length=500), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("notification_messages") as batch:
        batch.drop_column("link")
    op.drop_index("ix_user_notifications_user_unread", table_name="user_notifications")
    op.drop_index("ix_user_notifications_user_id", table_name="user_notifications")
    op.drop_table("user_notifications")
