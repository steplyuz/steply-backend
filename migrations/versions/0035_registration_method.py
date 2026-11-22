"""users.registration_method: foydalanuvchi qanday ro'yxatdan o'tgani

Eski akkauntlar uchun taxminiy backfill: identities (google / telegram) bo'yicha; qolganlari 'unknown'.
Eslatma: telegram identity bot orqali telefon tasdiqlashda ham yaratiladi, shuning uchun eski
akkauntlarning 'telegram_login' belgisi aniq emas.

Revision ID: 0035_registration_method
Revises: 0034_in_app_notifications
"""
import sqlalchemy as sa
from alembic import op

revision = "0035_registration_method"
down_revision = "0034_in_app_notifications"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("users", sa.Column("registration_method", sa.String(length=24), nullable=True))
    op.create_index("ix_users_registration_method", "users", ["registration_method"])
    op.execute(
        "UPDATE users SET registration_method = 'google' "
        "WHERE registration_method IS NULL AND id IN (SELECT user_id FROM identities WHERE provider = 'google')"
    )
    op.execute(
        "UPDATE users SET registration_method = 'telegram_login' "
        "WHERE registration_method IS NULL AND id IN (SELECT user_id FROM identities WHERE provider = 'telegram')"
    )
    op.execute("UPDATE users SET registration_method = 'unknown' WHERE registration_method IS NULL")


def downgrade() -> None:
    op.drop_index("ix_users_registration_method", table_name="users")
    op.drop_column("users", "registration_method")
