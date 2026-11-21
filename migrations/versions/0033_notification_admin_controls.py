"""notification admin controls: email hisoblar, hodisa qoidalari, opt-out, AI/referal admin uchun ustunlar

Revision ID: 0033_notification_admin_controls
Revises: 0032_notifications
"""
import sqlalchemy as sa
from alembic import op

revision = "0033_notification_admin_controls"
down_revision = "0032_notifications"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "email_accounts",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("provider", sa.String(length=16), nullable=False),
        sa.Column("email", sa.String(length=255), nullable=False),
        sa.Column("display_name", sa.String(length=120), nullable=True),
        sa.Column("refresh_token_encrypted", sa.Text(), nullable=True),
        sa.Column("is_default", sa.Boolean(), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.Column("status", sa.String(length=24), nullable=False),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("email"),
    )

    op.create_table(
        "notification_rules",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("event_code", sa.String(length=64), nullable=False),
        sa.Column("channel", sa.String(length=16), nullable=False),
        sa.Column("template_code", sa.String(length=64), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("event_code", "channel", name="uq_notification_rule_event_channel"),
    )
    op.create_index("ix_notification_rules_event_code", "notification_rules", ["event_code"])

    op.create_table(
        "notification_optouts",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("channel", sa.String(length=16), nullable=False),
        sa.Column("recipient", sa.String(length=255), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("channel", "recipient", name="uq_notification_optout"),
    )

    with op.batch_alter_table("notification_messages") as batch:
        batch.add_column(sa.Column("category", sa.String(length=16), server_default="transactional", nullable=False))
        batch.add_column(sa.Column("email_account_id", sa.Integer(), nullable=True))
        batch.create_foreign_key(
            "fk_notification_messages_email_account", "email_accounts", ["email_account_id"], ["id"],
            ondelete="SET NULL",
        )

    with op.batch_alter_table("referral_credits") as batch:
        batch.alter_column("referral_id", existing_type=sa.Integer(), nullable=True)
        batch.add_column(sa.Column("reason", sa.String(length=200), nullable=True))


def downgrade() -> None:
    # Eslatma: referral_credits.referral_id NOT NULL ga qaytarilganda admin kreditlari (NULL) bo'lsa xato beradi.
    with op.batch_alter_table("referral_credits") as batch:
        batch.drop_column("reason")
        batch.alter_column("referral_id", existing_type=sa.Integer(), nullable=False)

    with op.batch_alter_table("notification_messages") as batch:
        batch.drop_constraint("fk_notification_messages_email_account", type_="foreignkey")
        batch.drop_column("email_account_id")
        batch.drop_column("category")

    op.drop_table("notification_optouts")
    op.drop_index("ix_notification_rules_event_code", table_name="notification_rules")
    op.drop_table("notification_rules")
    op.drop_table("email_accounts")
