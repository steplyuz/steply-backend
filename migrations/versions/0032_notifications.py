"""notifications: shablonlar va yuborish navbati (outbox)

Revision ID: 0032_notifications
Revises: 0031_cleanup_premium_tier_alias
"""
import sqlalchemy as sa
from alembic import op

revision = "0032_notifications"
down_revision = "0031_cleanup_premium_tier_alias"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "notification_templates",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("code", sa.String(length=64), nullable=False),
        sa.Column("channel", sa.String(length=16), nullable=False),
        sa.Column("title", sa.String(length=200), nullable=False),
        sa.Column("subject", sa.String(length=255), nullable=True),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_notification_templates_code", "notification_templates", ["code"], unique=True)

    op.create_table(
        "notification_messages",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("channel", sa.String(length=16), nullable=False),
        sa.Column("recipient", sa.String(length=255), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=True),
        sa.Column("template_code", sa.String(length=64), nullable=True),
        sa.Column("subject", sa.String(length=255), nullable=True),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("max_attempts", sa.Integer(), nullable=False),
        sa.Column("next_attempt_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("provider_message_id", sa.String(length=128), nullable=True),
        sa.Column("idempotency_key", sa.String(length=128), nullable=True),
        sa.Column("created_by", sa.Uuid(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("sent_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["created_by"], ["users.id"], ondelete="SET NULL"),
        sa.UniqueConstraint("idempotency_key"),
    )
    op.create_index("ix_notification_messages_channel", "notification_messages", ["channel"])
    op.create_index("ix_notification_messages_user_id", "notification_messages", ["user_id"])
    op.create_index("ix_notification_messages_status", "notification_messages", ["status"])
    op.create_index("ix_notification_messages_due", "notification_messages", ["status", "next_attempt_at"])


def downgrade() -> None:
    op.drop_index("ix_notification_messages_due", table_name="notification_messages")
    op.drop_index("ix_notification_messages_status", table_name="notification_messages")
    op.drop_index("ix_notification_messages_user_id", table_name="notification_messages")
    op.drop_index("ix_notification_messages_channel", table_name="notification_messages")
    op.drop_table("notification_messages")
    op.drop_index("ix_notification_templates_code", table_name="notification_templates")
    op.drop_table("notification_templates")
