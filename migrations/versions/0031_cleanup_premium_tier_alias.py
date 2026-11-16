"""cleanup premium tier alias

DB'da "premium" qiymati bo'lsa "plus"ga o'tkazadi.
SubscriptionTier enum'idan PREMIUM = "plus" (broken alias) olib tashlandi —
bu migratsiya mavjud ma'lumotlarni xavfsiz holga keltiradi.

Revision ID: 0031_cleanup_premium_tier_alias
Revises: 0030_referral_and_tier_config
"""
import sqlalchemy as sa
from alembic import op

revision = "0031_cleanup_premium_tier_alias"
down_revision = "0030_referral_and_tier_config"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Agar "premium" qiymati bo'lsa "plus"ga o'tkazish.
    # subscription_plans.tier
    op.execute(
        sa.text(
            "UPDATE subscription_plans SET tier = 'plus' WHERE tier = 'premium'"
        )
    )
    # user_subscriptions jadvali plan.tier'dan foydalanadi (direct tier yo'q) — shart yo'q.
    # Boshqa jadvallarda ham tier enum bo'lsa qo'shish mumkin.


def downgrade() -> None:
    # "plus"ni "premium"ga qaytarish mantiqsiz (ular bir xil edi),
    # shuning uchun downgrade bo'sh qoladi.
    pass
