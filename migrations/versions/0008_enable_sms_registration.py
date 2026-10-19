"""SMS orqali ro'yxatdan o'tishni yoqish.

Birinchi deploy paytida oldindan mavjud bazalardagi `false` qiymatini yoqilgan
holatga o'tkazadi. Migratsiya bir marta ishlaydi; keyinchalik admin API orqali
sozlamani erkin yoqib/o'chirish mumkin.

Revision ID: 0008_enable_sms_registration
Revises: 0007_tiers_plus_pro_monthly
"""
from typing import Sequence, Union

from alembic import op

revision: str = "0008_enable_sms_registration"
down_revision: Union[str, None] = "0007_tiers_plus_pro_monthly"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Existing DB may have the old default false. This one-time migration turns
    # it on; future admin changes are not overwritten by application startup.
    op.execute(
        "UPDATE system_settings SET value = 'true' "
        "WHERE key = 'sms_registration_enabled' AND lower(value) = 'false'"
    )
    # Also handle a database that has the settings table but no seed row.
    op.execute(
        "INSERT INTO system_settings (key, value) "
        "SELECT 'sms_registration_enabled', 'true' "
        "WHERE NOT EXISTS (SELECT 1 FROM system_settings WHERE key = 'sms_registration_enabled')"
    )


def downgrade() -> None:
    # Do not turn the feature off automatically on rollback; that could
    # unexpectedly block registrations after a rollback.
    pass
