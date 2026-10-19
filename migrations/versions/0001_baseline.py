"""Baseline: joriy modellardan to'liq sxema (bo'sh bazadan boshlash uchun)

Eski zanjir (e34b785971c1 ... 20261015_org_account_payments) `create_all()` bilan
yaratilgan sxemaga tayanardi va bo'sh bazada `user_profiles` topilmay yiqilardi.
Shu sabab sxema bitta baseline'ga yig'ildi. Eski fayllar repodan olib tashlangan
(kerak bo'lsa, asl arxivda).

Bu migratsiya 20261015_org_account_payments holatiga TENG sxemani yaratadi va
standart (seed) qatorlarni qo'shadi:
  - system_settings.sms_registration_enabled = "false"
  - tier_skill_limits: FREE tarif uchun har bir ko'nikmaga haftasiga 5 ta

Mavjud (eski zanjirda yurgan) bazalar uchun: docs/MIGRATIONS.md.

Revision ID: 0001_baseline
Revises:
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '0001_baseline'
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table('billing_products',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('product_type', sa.Enum('SUBSCRIPTION', 'MOCK_EXAM', 'SPEAKING_PRACTICE', 'FULL_SKILLS', 'FULL_EXAM', name='billingproducttype'), nullable=False),
    sa.Column('code', sa.String(length=100), nullable=True),
    sa.Column('title', sa.String(length=200), nullable=False),
    sa.Column('description', sa.Text(), nullable=True),
    sa.Column('price', sa.Integer(), nullable=False),
    sa.Column('external_ref', sa.String(length=100), nullable=True),
    sa.Column('is_active', sa.Boolean(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('billing_products', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_billing_products_code'), ['code'], unique=True)
        batch_op.create_index(batch_op.f('ix_billing_products_external_ref'), ['external_ref'], unique=False)
        batch_op.create_index(batch_op.f('ix_billing_products_product_type'), ['product_type'], unique=False)

    op.create_table('org_api_keys',
    sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
    sa.Column('organization_id', sa.String(length=36), nullable=False),
    sa.Column('name', sa.String(length=100), nullable=False),
    sa.Column('key_prefix', sa.String(length=20), nullable=False),
    sa.Column('key_hash', sa.String(length=64), nullable=False),
    sa.Column('is_active', sa.Boolean(), nullable=False),
    sa.Column('expires_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('last_used_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('created_by', sa.String(length=36), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('org_api_keys', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_org_api_keys_key_prefix'), ['key_prefix'], unique=False)
        batch_op.create_index(batch_op.f('ix_org_api_keys_organization_id'), ['organization_id'], unique=False)

    op.create_table('promo_codes',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('code', sa.String(length=50), nullable=False),
    sa.Column('discount_percent', sa.Integer(), nullable=True),
    sa.Column('discount_amount', sa.Integer(), nullable=True),
    sa.Column('max_usage', sa.Integer(), nullable=True),
    sa.Column('used_count', sa.Integer(), nullable=False),
    sa.Column('valid_from', sa.DateTime(timezone=True), nullable=True),
    sa.Column('valid_until', sa.DateTime(timezone=True), nullable=True),
    sa.Column('is_active', sa.Boolean(), nullable=False),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('promo_codes', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_promo_codes_code'), ['code'], unique=True)

    op.create_table('subscription_plans',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('name', sa.String(length=100), nullable=False),
    sa.Column('description', sa.Text(), nullable=True),
    sa.Column('price', sa.Integer(), nullable=False),
    sa.Column('duration_days', sa.Integer(), nullable=False),
    sa.Column('tier', sa.Enum('FREE', 'PRO', 'PREMIUM', name='subscriptiontier'), nullable=False),
    sa.Column('ai_limit', sa.Integer(), nullable=False),
    sa.Column('exam_limit', sa.Integer(), nullable=False),
    sa.Column('is_active', sa.Boolean(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('name')
    )
    op.create_table('telegram_oidc_transactions',
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('state_hash', sa.String(length=64), nullable=False),
    sa.Column('nonce_hash', sa.String(length=64), nullable=False),
    sa.Column('nonce_encrypted', sa.Text(), nullable=False),
    sa.Column('browser_secret_hash', sa.String(length=64), nullable=False),
    sa.Column('code_verifier_encrypted', sa.Text(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    sa.Column('expires_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('used_at', sa.DateTime(timezone=True), nullable=True),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('telegram_oidc_transactions', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_telegram_oidc_transactions_expires_at'), ['expires_at'], unique=False)
        batch_op.create_index(batch_op.f('ix_telegram_oidc_transactions_state_hash'), ['state_hash'], unique=True)

    op.create_table('tier_skill_limits',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('tier', sa.Enum('FREE', 'PRO', 'PREMIUM', name='subscriptiontier'), nullable=False),
    sa.Column('skill', sa.String(length=20), nullable=False),
    sa.Column('weekly_limit', sa.Integer(), nullable=True),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('tier', 'skill', name='uq_tier_skill_limit')
    )
    op.create_table('users',
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('is_active', sa.Boolean(), nullable=False),
    sa.Column('force_password_change', sa.Boolean(), server_default=sa.false(), nullable=False),
    sa.Column('global_role', sa.Enum('STUDENT', 'ADMIN', 'SUPER_ADMIN', 'ORGANIZATION', 'TEACHER', name='userrole'), nullable=False),
    sa.Column('hashed_password', sa.String(length=255), nullable=True),
    sa.Column('last_login_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('telegram_user_id', sa.BigInteger(), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('users', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_users_telegram_user_id'), ['telegram_user_id'], unique=True)

    op.create_table('verification_codes',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('target', sa.String(length=255), nullable=False),
    sa.Column('purpose', sa.Enum('REGISTER', 'RESET_PASSWORD', name='otppurpose'), nullable=False),
    sa.Column('channel', sa.Enum('SMS', 'TELEGRAM', name='otpchannel'), nullable=False),
    sa.Column('code_hash', sa.String(length=128), nullable=True),
    sa.Column('failed_attempts', sa.Integer(), nullable=False),
    sa.Column('send_count', sa.Integer(), nullable=False),
    sa.Column('last_sent_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    sa.Column('bot_token', sa.String(length=64), nullable=True),
    sa.Column('telegram_chat_id', sa.String(length=64), nullable=True),
    sa.Column('telegram_user_id', sa.BigInteger(), nullable=True),
    sa.Column('code_encrypted', sa.Text(), nullable=True),
    sa.Column('payload', sa.Text(), nullable=True),
    sa.Column('expires_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('is_used', sa.Boolean(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('verification_codes', schema=None) as batch_op:
        batch_op.create_index('idx_active_code', ['target', 'purpose', 'is_used'], unique=False)
        batch_op.create_index(batch_op.f('ix_verification_codes_bot_token'), ['bot_token'], unique=True)
        batch_op.create_index(batch_op.f('ix_verification_codes_expires_at'), ['expires_at'], unique=False)
        batch_op.create_index(batch_op.f('ix_verification_codes_target'), ['target'], unique=False)
        batch_op.create_index(batch_op.f('ix_verification_codes_telegram_user_id'), ['telegram_user_id'], unique=False)

    op.create_table('writing_formats',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('name', sa.String(length=50), nullable=False),
    sa.Column('cefr_level', sa.Enum('B1', 'B2', 'C1', name='cefrlevel', native_enum=False), nullable=False),
    sa.Column('min_words', sa.Integer(), nullable=False),
    sa.Column('max_words', sa.Integer(), nullable=False),
    sa.Column('style', sa.Enum('INFORMAL', 'FORMAL', 'POST', name='writingstyle', native_enum=False), nullable=False),
    sa.Column('scoring_mode', sa.Enum('HOLISTIC', 'ANALYTIC', name='scoringmode', native_enum=False), nullable=False),
    sa.Column('penalty_enabled', sa.Boolean(), server_default=sa.false(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('name')
    )
    with op.batch_alter_table('writing_formats', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_writing_formats_penalty_enabled'), ['penalty_enabled'], unique=False)

    op.create_table('balance_transactions',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('user_id', sa.Uuid(), nullable=False),
    sa.Column('amount', sa.Integer(), nullable=False),
    sa.Column('reason', sa.String(length=100), nullable=False),
    sa.Column('reference_id', sa.String(length=100), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('balance_transactions', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_balance_transactions_user_id'), ['user_id'], unique=False)

    op.create_table('exam_versions',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('module', sa.Enum('LISTENING', 'READING', name='papermodule', native_enum=False), nullable=False),
    sa.Column('exam_id', sa.String(), nullable=False),
    sa.Column('version_number', sa.Integer(), nullable=False),
    sa.Column('total_questions', sa.Integer(), nullable=False),
    sa.Column('answer_key', sa.JSON(), nullable=False),
    sa.Column('is_locked', sa.Boolean(), nullable=False),
    sa.Column('locked_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('locked_by_user_id', sa.Uuid(), nullable=True),
    sa.Column('answer_sheet_layout', sa.JSON(), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=True),
    sa.ForeignKeyConstraint(['locked_by_user_id'], ['users.id'], ),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('module', 'exam_id', 'version_number', name='uq_exam_version')
    )
    with op.batch_alter_table('exam_versions', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_exam_versions_exam_id'), ['exam_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_exam_versions_id'), ['id'], unique=False)
        batch_op.create_index(batch_op.f('ix_exam_versions_module'), ['module'], unique=False)

    op.create_table('identities',
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('user_id', sa.Uuid(), nullable=False),
    sa.Column('provider', sa.String(length=32), nullable=False),
    sa.Column('provider_user_id', sa.String(length=255), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('provider', 'provider_user_id', name='uq_identity_provider_user')
    )
    with op.batch_alter_table('identities', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_identities_user_id'), ['user_id'], unique=False)
        batch_op.create_index('ix_identity_user_provider', ['user_id', 'provider'], unique=False)

    op.create_table('organizations',
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('name', sa.String(length=200), nullable=False),
    sa.Column('slug', sa.String(length=120), nullable=False),
    sa.Column('logo_url', sa.String(length=500), nullable=True),
    sa.Column('is_active', sa.Boolean(), nullable=False),
    sa.Column('online_exam_create_enabled', sa.Boolean(), server_default=sa.false(), nullable=False),
    sa.Column('created_by', sa.Uuid(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    sa.ForeignKeyConstraint(['created_by'], ['users.id'], ondelete='RESTRICT'),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('organizations', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_organizations_slug'), ['slug'], unique=True)

    op.create_table('practice_attempts',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('user_id', sa.Uuid(), nullable=False),
    sa.Column('skill', sa.Enum('READING', 'LISTENING', 'WRITING', 'SPEAKING', name='skilltype', native_enum=False), nullable=False),
    sa.Column('test_id', sa.String(length=64), nullable=False),
    sa.Column('mode', sa.String(length=32), server_default='FULL_PRACTICE', nullable=False),
    sa.Column('part_number', sa.Integer(), nullable=True),
    sa.Column('status', sa.Enum('IN_PROGRESS', 'SUBMITTED', 'EXPIRED', name='practiceattemptstatus', native_enum=False), nullable=False),
    sa.Column('started_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    sa.Column('allowed_duration_seconds', sa.Integer(), nullable=True),
    sa.Column('expires_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('active_scope_key', sa.String(length=255), nullable=True),
    sa.Column('submitted_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('last_saved_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('result_id', sa.Integer(), nullable=True),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('practice_attempts', schema=None) as batch_op:
        batch_op.create_index('ix_practice_attempt_user_skill_test', ['user_id', 'skill', 'test_id', 'mode', 'part_number', 'status'], unique=False)
        batch_op.create_index(batch_op.f('ix_practice_attempts_active_scope_key'), ['active_scope_key'], unique=True)
        batch_op.create_index(batch_op.f('ix_practice_attempts_expires_at'), ['expires_at'], unique=False)
        batch_op.create_index(batch_op.f('ix_practice_attempts_id'), ['id'], unique=False)
        batch_op.create_index(batch_op.f('ix_practice_attempts_user_id'), ['user_id'], unique=False)

    op.create_table('promo_code_usages',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('promo_code_id', sa.Integer(), nullable=False),
    sa.Column('user_id', sa.Uuid(), nullable=False),
    sa.ForeignKeyConstraint(['promo_code_id'], ['promo_codes.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('promo_code_id', 'user_id', name='uq_promo_user')
    )
    with op.batch_alter_table('promo_code_usages', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_promo_code_usages_promo_code_id'), ['promo_code_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_promo_code_usages_user_id'), ['user_id'], unique=False)

    op.create_table('system_settings',
    sa.Column('key', sa.String(length=100), nullable=False),
    sa.Column('value', sa.String(length=500), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    sa.Column('updated_by', sa.Uuid(), nullable=True),
    sa.ForeignKeyConstraint(['updated_by'], ['users.id'], ondelete='SET NULL'),
    sa.PrimaryKeyConstraint('key')
    )
    op.create_table('user_contacts',
    sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
    sa.Column('user_id', sa.Uuid(), nullable=False),
    sa.Column('contact_type', sa.Enum('EMAIL', 'PHONE', name='contacttype'), nullable=False),
    sa.Column('value', sa.String(length=255), nullable=False),
    sa.Column('is_verified', sa.Boolean(), nullable=False),
    sa.Column('is_primary', sa.Boolean(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('contact_type', 'value', name='uq_contact_type_value')
    )
    with op.batch_alter_table('user_contacts', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_user_contacts_user_id'), ['user_id'], unique=False)

    op.create_table('user_profiles',
    sa.Column('user_id', sa.Uuid(), nullable=False),
    sa.Column('full_name', sa.String(length=255), nullable=True),
    sa.Column('username', sa.String(length=50), nullable=True),
    sa.Column('avatar_url', sa.String(length=500), nullable=True),
    sa.Column('bio', sa.Text(), nullable=True),
    sa.Column('birth_date', sa.Date(), nullable=True),
    sa.Column('gender', sa.Enum('MALE', 'FEMALE', name='gender'), nullable=True),
    sa.Column('language', sa.String(length=10), nullable=True),
    sa.Column('timezone', sa.String(length=50), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('user_id'),
    sa.UniqueConstraint('username')
    )
    op.create_table('user_sessions',
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('user_id', sa.Uuid(), nullable=False),
    sa.Column('session_family_id', sa.Uuid(), nullable=False),
    sa.Column('rotated_from_id', sa.Uuid(), nullable=True),
    sa.Column('refresh_token_hash', sa.String(length=128), nullable=False),
    sa.Column('user_agent', sa.String(length=255), nullable=True),
    sa.Column('ip_address', sa.String(length=50), nullable=True),
    sa.Column('expires_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('is_revoked', sa.Boolean(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('user_sessions', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_user_sessions_expires_at'), ['expires_at'], unique=False)
        batch_op.create_index(batch_op.f('ix_user_sessions_refresh_token_hash'), ['refresh_token_hash'], unique=False)
        batch_op.create_index(batch_op.f('ix_user_sessions_session_family_id'), ['session_family_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_user_sessions_user_id'), ['user_id'], unique=False)

    op.create_table('user_subscriptions',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('user_id', sa.Uuid(), nullable=False),
    sa.Column('plan_id', sa.Integer(), nullable=False),
    sa.Column('start_date', sa.DateTime(timezone=True), nullable=False),
    sa.Column('end_date', sa.DateTime(timezone=True), nullable=False),
    sa.Column('is_active', sa.Boolean(), nullable=False),
    sa.ForeignKeyConstraint(['plan_id'], ['subscription_plans.id'], ),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('user_subscriptions', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_user_subscriptions_end_date'), ['end_date'], unique=False)
        batch_op.create_index(batch_op.f('ix_user_subscriptions_user_id'), ['user_id'], unique=False)

    op.create_table('writing_criterion_configs',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('format_id', sa.Integer(), nullable=False),
    sa.Column('criterion', sa.Enum('TASK_ACHIEVEMENT', 'COHERENCE', 'VOCABULARY', 'GRAMMAR', 'MECHANICS', 'OVERALL', name='writingcriterion', native_enum=False), nullable=False),
    sa.Column('weight', sa.Float(), nullable=False),
    sa.ForeignKeyConstraint(['format_id'], ['writing_formats.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('format_id', 'criterion', name='uq_format_criterion')
    )
    with op.batch_alter_table('writing_criterion_configs', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_writing_criterion_configs_format_id'), ['format_id'], unique=False)

    op.create_table('billing_orders',
    sa.Column('id', sa.String(length=36), nullable=False),
    sa.Column('user_id', sa.Uuid(), nullable=False),
    sa.Column('product_type', sa.Enum('SUBSCRIPTION', 'MOCK_EXAM', 'SPEAKING_PRACTICE', 'FULL_SKILLS', 'FULL_EXAM', name='billingproducttype'), nullable=False),
    sa.Column('product_id', sa.String(length=100), nullable=True),
    sa.Column('product_title', sa.String(length=200), nullable=False),
    sa.Column('amount', sa.Integer(), nullable=False),
    sa.Column('original_amount', sa.Integer(), nullable=True),
    sa.Column('discount_amount', sa.Integer(), server_default='0', nullable=False),
    sa.Column('promo_code_id', sa.Integer(), nullable=True),
    sa.Column('organization_id', sa.Uuid(), nullable=True),
    sa.Column('status', sa.Enum('PENDING_PAYMENT', 'UNDER_REVIEW', 'PAID', 'REJECTED', 'CANCELLED', name='billingorderstatus'), nullable=False),
    sa.Column('receipt_path', sa.String(length=500), nullable=True),
    sa.Column('receipt_storage_key', sa.String(length=255), nullable=True),
    sa.Column('user_note', sa.String(length=1000), nullable=True),
    sa.Column('admin_note', sa.String(length=1000), nullable=True),
    sa.Column('reviewed_by', sa.Uuid(), nullable=True),
    sa.Column('reviewed_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    sa.ForeignKeyConstraint(['organization_id'], ['organizations.id'], ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['promo_code_id'], ['promo_codes.id'], ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['reviewed_by'], ['users.id'], ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('billing_orders', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_billing_orders_organization_id'), ['organization_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_billing_orders_promo_code_id'), ['promo_code_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_billing_orders_receipt_storage_key'), ['receipt_storage_key'], unique=False)
        batch_op.create_index(batch_op.f('ix_billing_orders_status'), ['status'], unique=False)
        batch_op.create_index(batch_op.f('ix_billing_orders_user_id'), ['user_id'], unique=False)

    op.create_table('listening_tests',
    sa.Column('title', sa.String(), nullable=False),
    sa.Column('cefr_level', sa.String(), nullable=False),
    sa.Column('duration_minutes', sa.Integer(), nullable=False),
    sa.Column('total_questions', sa.Integer(), nullable=False),
    sa.Column('sections', sa.String(), nullable=True),
    sa.Column('audio_mode', sa.String(length=16), server_default='PARTS', nullable=False),
    sa.Column('audio_url', sa.String(length=500), nullable=True),
    sa.Column('id', sa.String(length=64), nullable=False),
    sa.Column('is_demo', sa.Boolean(), nullable=False),
    sa.Column('is_free', sa.Boolean(), nullable=False),
    sa.Column('is_practice', sa.Boolean(), nullable=False),
    sa.Column('is_active', sa.Boolean(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    sa.Column('organization_id', sa.Uuid(), nullable=True),
    sa.ForeignKeyConstraint(['organization_id'], ['organizations.id'], ondelete='SET NULL'),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('listening_tests', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_listening_tests_id'), ['id'], unique=False)
        batch_op.create_index(batch_op.f('ix_listening_tests_is_practice'), ['is_practice'], unique=False)
        batch_op.create_index(batch_op.f('ix_listening_tests_organization_id'), ['organization_id'], unique=False)

    op.create_table('organization_accounts',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('organization_id', sa.Uuid(), nullable=False),
    sa.Column('account_number', sa.String(length=20), nullable=False),
    sa.Column('balance', sa.Integer(), server_default='0', nullable=False),
    sa.Column('payment_details', sa.String(length=500), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    sa.ForeignKeyConstraint(['organization_id'], ['organizations.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('organization_id')
    )
    with op.batch_alter_table('organization_accounts', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_organization_accounts_account_number'), ['account_number'], unique=True)

    op.create_table('organization_groups',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('organization_id', sa.Uuid(), nullable=False),
    sa.Column('name', sa.String(length=160), nullable=False),
    sa.Column('code', sa.String(length=80), nullable=True),
    sa.Column('is_active', sa.Boolean(), nullable=False),
    sa.ForeignKeyConstraint(['organization_id'], ['organizations.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('organization_id', 'name', name='uq_org_group_name')
    )
    with op.batch_alter_table('organization_groups', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_organization_groups_organization_id'), ['organization_id'], unique=False)

    op.create_table('organization_members',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('organization_id', sa.Uuid(), nullable=False),
    sa.Column('user_id', sa.Uuid(), nullable=False),
    sa.Column('role', sa.Enum('ORG_ADMIN', 'TEACHER', 'EVALUATOR', 'EXAM_MANAGER', 'STUDENT', name='organizationrole', native_enum=False), nullable=False),
    sa.Column('is_active', sa.Boolean(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    sa.ForeignKeyConstraint(['organization_id'], ['organizations.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('organization_id', 'user_id', name='uq_organization_member_user')
    )
    with op.batch_alter_table('organization_members', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_organization_members_organization_id'), ['organization_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_organization_members_user_id'), ['user_id'], unique=False)

    op.create_table('practice_usage_log',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('user_id', sa.Uuid(), nullable=False),
    sa.Column('skill', sa.String(length=20), nullable=False),
    sa.Column('test_id', sa.String(length=64), nullable=False),
    sa.Column('mode', sa.String(length=32), server_default='FULL_PRACTICE', nullable=False),
    sa.Column('attempt_id', sa.Integer(), nullable=True),
    sa.Column('week_start', sa.Date(), nullable=False),
    sa.Column('consumed_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    sa.ForeignKeyConstraint(['attempt_id'], ['practice_attempts.id'], ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('user_id', 'skill', 'test_id', 'mode', 'week_start', name='uq_practice_usage')
    )
    with op.batch_alter_table('practice_usage_log', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_practice_usage_log_attempt_id'), ['attempt_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_practice_usage_log_user_id'), ['user_id'], unique=False)
        batch_op.create_index('ix_practice_usage_user_skill_week', ['user_id', 'skill', 'week_start'], unique=False)

    op.create_table('reading_tests',
    sa.Column('title', sa.String(), nullable=False),
    sa.Column('cefr_level', sa.String(), nullable=False),
    sa.Column('language', sa.String(), nullable=False),
    sa.Column('duration_minutes', sa.Integer(), nullable=False),
    sa.Column('total_questions', sa.Integer(), nullable=False),
    sa.Column('id', sa.String(length=64), nullable=False),
    sa.Column('is_demo', sa.Boolean(), nullable=False),
    sa.Column('is_free', sa.Boolean(), nullable=False),
    sa.Column('is_practice', sa.Boolean(), nullable=False),
    sa.Column('is_active', sa.Boolean(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    sa.Column('organization_id', sa.Uuid(), nullable=True),
    sa.ForeignKeyConstraint(['organization_id'], ['organizations.id'], ondelete='SET NULL'),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('reading_tests', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_reading_tests_id'), ['id'], unique=False)
        batch_op.create_index(batch_op.f('ix_reading_tests_is_practice'), ['is_practice'], unique=False)
        batch_op.create_index(batch_op.f('ix_reading_tests_organization_id'), ['organization_id'], unique=False)

    op.create_table('speaking_tests',
    sa.Column('title', sa.String(), nullable=False),
    sa.Column('description', sa.Text(), nullable=True),
    sa.Column('id', sa.String(length=64), nullable=False),
    sa.Column('is_demo', sa.Boolean(), nullable=False),
    sa.Column('is_free', sa.Boolean(), nullable=False),
    sa.Column('is_practice', sa.Boolean(), nullable=False),
    sa.Column('is_active', sa.Boolean(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    sa.Column('organization_id', sa.Uuid(), nullable=True),
    sa.ForeignKeyConstraint(['organization_id'], ['organizations.id'], ondelete='SET NULL'),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('speaking_tests', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_speaking_tests_id'), ['id'], unique=False)
        batch_op.create_index(batch_op.f('ix_speaking_tests_is_practice'), ['is_practice'], unique=False)
        batch_op.create_index(batch_op.f('ix_speaking_tests_organization_id'), ['organization_id'], unique=False)

    op.create_table('writing_exams',
    sa.Column('year', sa.Integer(), nullable=False),
    sa.Column('sequence_number', sa.Integer(), nullable=False),
    sa.Column('title', sa.String(length=255), nullable=False),
    sa.Column('cefr_level', sa.Enum('B1', 'B2', 'C1', name='cefrlevel', native_enum=False), nullable=False),
    sa.Column('duration_minutes', sa.Integer(), server_default='60', nullable=False),
    sa.Column('id', sa.String(length=64), nullable=False),
    sa.Column('is_demo', sa.Boolean(), nullable=False),
    sa.Column('is_free', sa.Boolean(), nullable=False),
    sa.Column('is_practice', sa.Boolean(), nullable=False),
    sa.Column('is_active', sa.Boolean(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    sa.Column('organization_id', sa.Uuid(), nullable=True),
    sa.ForeignKeyConstraint(['organization_id'], ['organizations.id'], ondelete='SET NULL'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('year', 'sequence_number', name='uq_writing_year_sequence')
    )
    with op.batch_alter_table('writing_exams', schema=None) as batch_op:
        batch_op.create_index('ix_exam_year_sequence', ['year', 'sequence_number'], unique=False)
        batch_op.create_index(batch_op.f('ix_writing_exams_id'), ['id'], unique=False)
        batch_op.create_index(batch_op.f('ix_writing_exams_is_practice'), ['is_practice'], unique=False)
        batch_op.create_index(batch_op.f('ix_writing_exams_organization_id'), ['organization_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_writing_exams_year'), ['year'], unique=False)

    op.create_table('evaluator_assignments',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('organization_id', sa.Uuid(), nullable=False),
    sa.Column('evaluator_id', sa.Uuid(), nullable=False),
    sa.Column('student_id', sa.Uuid(), nullable=True),
    sa.Column('group_id', sa.Integer(), nullable=True),
    sa.Column('skill', sa.Enum('WRITING', 'SPEAKING', name='assignmentskill', native_enum=False), nullable=False),
    sa.Column('is_active', sa.Boolean(), nullable=False),
    sa.CheckConstraint('(student_id IS NOT NULL AND group_id IS NULL) OR (student_id IS NULL AND group_id IS NOT NULL)', name='ck_evaluator_assignment_target'),
    sa.ForeignKeyConstraint(['evaluator_id'], ['users.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['group_id'], ['organization_groups.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['organization_id'], ['organizations.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['student_id'], ['users.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('organization_id', 'evaluator_id', 'student_id', 'group_id', 'skill', name='uq_evaluator_scope')
    )
    with op.batch_alter_table('evaluator_assignments', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_evaluator_assignments_evaluator_id'), ['evaluator_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_evaluator_assignments_group_id'), ['group_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_evaluator_assignments_organization_id'), ['organization_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_evaluator_assignments_student_id'), ['student_id'], unique=False)
        batch_op.create_index('uq_evaluator_assign_group', ['organization_id', 'evaluator_id', 'group_id', 'skill'], unique=True, sqlite_where=sa.text('group_id IS NOT NULL'), postgresql_where=sa.text('group_id IS NOT NULL'))
        batch_op.create_index('uq_evaluator_assign_student', ['organization_id', 'evaluator_id', 'student_id', 'skill'], unique=True, sqlite_where=sa.text('student_id IS NOT NULL'), postgresql_where=sa.text('student_id IS NOT NULL'))

    op.create_table('group_members',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('organization_id', sa.Uuid(), nullable=False),
    sa.Column('group_id', sa.Integer(), nullable=False),
    sa.Column('student_id', sa.Uuid(), nullable=False),
    sa.Column('is_active', sa.Boolean(), nullable=False),
    sa.ForeignKeyConstraint(['group_id'], ['organization_groups.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['organization_id'], ['organizations.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['student_id'], ['users.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('group_id', 'student_id', name='uq_group_member_student')
    )
    with op.batch_alter_table('group_members', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_group_members_group_id'), ['group_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_group_members_organization_id'), ['organization_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_group_members_student_id'), ['student_id'], unique=False)

    op.create_table('listening_parts',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('exam_id', sa.String(length=64), nullable=False),
    sa.Column('external_id', sa.String(length=128), nullable=True),
    sa.Column('part_number', sa.Integer(), nullable=False),
    sa.Column('title', sa.String(), nullable=False),
    sa.Column('instruction', sa.Text(), nullable=False),
    sa.Column('task_type', sa.String(), nullable=False),
    sa.Column('audio_url', sa.String(length=500), server_default='', nullable=False),
    sa.Column('context', sa.String(), nullable=True),
    sa.Column('passage', sa.Text(), nullable=True),
    sa.Column('map_image', sa.String(), nullable=True),
    sa.ForeignKeyConstraint(['exam_id'], ['listening_tests.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('exam_id', 'external_id', name='uq_listening_part_external_id')
    )
    with op.batch_alter_table('listening_parts', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_listening_parts_external_id'), ['external_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_listening_parts_id'), ['id'], unique=False)

    op.create_table('mock_exams',
    sa.Column('title', sa.String(), nullable=False),
    sa.Column('description', sa.Text(), nullable=True),
    sa.Column('image_file', sa.String(), nullable=True),
    sa.Column('cefr_level', sa.String(), nullable=False),
    sa.Column('duration_minutes', sa.Integer(), nullable=False),
    sa.Column('price', sa.Integer(), nullable=False),
    sa.Column('is_active', sa.Boolean(), nullable=False),
    sa.Column('reading_id', sa.String(length=64), nullable=True),
    sa.Column('listening_id', sa.String(length=64), nullable=True),
    sa.Column('writing_id', sa.String(length=64), nullable=True),
    sa.Column('speaking_id', sa.String(length=64), nullable=False),
    sa.Column('id', sa.String(length=64), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    sa.Column('organization_id', sa.Uuid(), nullable=True),
    sa.ForeignKeyConstraint(['listening_id'], ['listening_tests.id'], ),
    sa.ForeignKeyConstraint(['organization_id'], ['organizations.id'], ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['reading_id'], ['reading_tests.id'], ),
    sa.ForeignKeyConstraint(['speaking_id'], ['speaking_tests.id'], ),
    sa.ForeignKeyConstraint(['writing_id'], ['writing_exams.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('mock_exams', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_mock_exams_id'), ['id'], unique=False)
        batch_op.create_index(batch_op.f('ix_mock_exams_organization_id'), ['organization_id'], unique=False)

    op.create_table('organization_transactions',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('organization_id', sa.Uuid(), nullable=False),
    sa.Column('type', sa.String(length=32), nullable=False),
    sa.Column('amount', sa.Integer(), nullable=False),
    sa.Column('balance_after', sa.Integer(), nullable=False),
    sa.Column('billing_order_id', sa.String(length=36), nullable=True),
    sa.Column('note', sa.String(length=500), nullable=True),
    sa.Column('created_by', sa.Uuid(), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    sa.ForeignKeyConstraint(['billing_order_id'], ['billing_orders.id'], ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['created_by'], ['users.id'], ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['organization_id'], ['organizations.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('billing_order_id')
    )
    with op.batch_alter_table('organization_transactions', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_organization_transactions_created_at'), ['created_at'], unique=False)
        batch_op.create_index(batch_op.f('ix_organization_transactions_organization_id'), ['organization_id'], unique=False)

    op.create_table('reading_parts',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('test_id', sa.String(length=64), nullable=False),
    sa.Column('external_id', sa.String(length=128), nullable=True),
    sa.Column('title', sa.String(), nullable=False),
    sa.Column('description', sa.Text(), nullable=True),
    sa.Column('passage', sa.Text(), nullable=False),
    sa.ForeignKeyConstraint(['test_id'], ['reading_tests.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('test_id', 'external_id', name='uq_reading_part_external_id')
    )
    with op.batch_alter_table('reading_parts', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_reading_parts_external_id'), ['external_id'], unique=False)

    op.create_table('speaking_part_1_1',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('speaking_test_id', sa.String(length=64), nullable=False),
    sa.Column('external_id', sa.String(length=128), nullable=True),
    sa.Column('question_1_id', sa.String(length=128), nullable=True),
    sa.Column('question_2_id', sa.String(length=128), nullable=True),
    sa.Column('question_3_id', sa.String(length=128), nullable=True),
    sa.Column('question_1', sa.Text(), nullable=False),
    sa.Column('question_2', sa.Text(), nullable=False),
    sa.Column('question_3', sa.Text(), nullable=False),
    sa.Column('prep_seconds', sa.Integer(), nullable=False),
    sa.Column('answer_seconds', sa.Integer(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    sa.ForeignKeyConstraint(['speaking_test_id'], ['speaking_tests.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('speaking_test_id')
    )
    with op.batch_alter_table('speaking_part_1_1', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_speaking_part_1_1_external_id'), ['external_id'], unique=False)

    op.create_table('speaking_part_1_2',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('speaking_test_id', sa.String(length=64), nullable=False),
    sa.Column('external_id', sa.String(length=128), nullable=True),
    sa.Column('question_describe_id', sa.String(length=128), nullable=True),
    sa.Column('question_2_id', sa.String(length=128), nullable=True),
    sa.Column('question_3_id', sa.String(length=128), nullable=True),
    sa.Column('image_1_url', sa.String(length=500), nullable=False),
    sa.Column('image_2_url', sa.String(length=500), nullable=False),
    sa.Column('question_describe', sa.Text(), nullable=False),
    sa.Column('question_2', sa.Text(), nullable=False),
    sa.Column('question_3', sa.Text(), nullable=False),
    sa.Column('prep_seconds', sa.Integer(), nullable=False),
    sa.Column('describe_answer_seconds', sa.Integer(), nullable=False),
    sa.Column('answer_seconds', sa.Integer(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    sa.ForeignKeyConstraint(['speaking_test_id'], ['speaking_tests.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('speaking_test_id')
    )
    with op.batch_alter_table('speaking_part_1_2', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_speaking_part_1_2_external_id'), ['external_id'], unique=False)

    op.create_table('speaking_part_2',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('speaking_test_id', sa.String(length=64), nullable=False),
    sa.Column('external_id', sa.String(length=128), nullable=True),
    sa.Column('question_1_id', sa.String(length=128), nullable=True),
    sa.Column('question_2_id', sa.String(length=128), nullable=True),
    sa.Column('question_3_id', sa.String(length=128), nullable=True),
    sa.Column('image_url', sa.String(length=500), nullable=False),
    sa.Column('question_1', sa.Text(), nullable=False),
    sa.Column('question_2', sa.Text(), nullable=False),
    sa.Column('question_3', sa.Text(), nullable=False),
    sa.Column('prep_seconds', sa.Integer(), nullable=False),
    sa.Column('answer_seconds', sa.Integer(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    sa.ForeignKeyConstraint(['speaking_test_id'], ['speaking_tests.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('speaking_test_id')
    )
    with op.batch_alter_table('speaking_part_2', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_speaking_part_2_external_id'), ['external_id'], unique=False)

    op.create_table('speaking_part_3',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('speaking_test_id', sa.String(length=64), nullable=False),
    sa.Column('external_id', sa.String(length=128), nullable=True),
    sa.Column('topic_id', sa.String(length=128), nullable=True),
    sa.Column('topic', sa.Text(), nullable=False),
    sa.Column('for_points', sa.JSON(), nullable=False),
    sa.Column('against_points', sa.JSON(), nullable=False),
    sa.Column('prep_seconds', sa.Integer(), nullable=False),
    sa.Column('answer_seconds', sa.Integer(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    sa.ForeignKeyConstraint(['speaking_test_id'], ['speaking_tests.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('speaking_test_id')
    )
    with op.batch_alter_table('speaking_part_3', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_speaking_part_3_external_id'), ['external_id'], unique=False)

    op.create_table('teacher_student_assignments',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('organization_id', sa.Uuid(), nullable=False),
    sa.Column('teacher_id', sa.Uuid(), nullable=False),
    sa.Column('student_id', sa.Uuid(), nullable=True),
    sa.Column('group_id', sa.Integer(), nullable=True),
    sa.Column('is_active', sa.Boolean(), nullable=False),
    sa.CheckConstraint('(student_id IS NOT NULL AND group_id IS NULL) OR (student_id IS NULL AND group_id IS NOT NULL)', name='ck_teacher_assignment_target'),
    sa.ForeignKeyConstraint(['group_id'], ['organization_groups.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['organization_id'], ['organizations.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['student_id'], ['users.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['teacher_id'], ['users.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('organization_id', 'teacher_id', 'student_id', 'group_id', name='uq_teacher_student_scope')
    )
    with op.batch_alter_table('teacher_student_assignments', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_teacher_student_assignments_group_id'), ['group_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_teacher_student_assignments_organization_id'), ['organization_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_teacher_student_assignments_student_id'), ['student_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_teacher_student_assignments_teacher_id'), ['teacher_id'], unique=False)
        batch_op.create_index('uq_teacher_assign_group', ['organization_id', 'teacher_id', 'group_id'], unique=True, sqlite_where=sa.text('group_id IS NOT NULL'), postgresql_where=sa.text('group_id IS NOT NULL'))
        batch_op.create_index('uq_teacher_assign_student', ['organization_id', 'teacher_id', 'student_id'], unique=True, sqlite_where=sa.text('student_id IS NOT NULL'), postgresql_where=sa.text('student_id IS NOT NULL'))

    op.create_table('writing_tasks',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('exam_id', sa.String(length=64), nullable=False),
    sa.Column('external_id', sa.String(length=128), nullable=True),
    sa.Column('format_id', sa.Integer(), nullable=False),
    sa.Column('part_number', sa.Integer(), nullable=False),
    sa.Column('sub_part', sa.Integer(), nullable=True),
    sa.Column('topic', sa.Text(), nullable=False),
    sa.Column('instruction', sa.Text(), nullable=False),
    sa.Column('context_text', sa.Text(), nullable=True),
    sa.Column('question', sa.Text(), nullable=True),
    sa.Column('word_limit_min', sa.Integer(), nullable=True),
    sa.Column('word_limit_max', sa.Integer(), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    sa.ForeignKeyConstraint(['exam_id'], ['writing_exams.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['format_id'], ['writing_formats.id'], ondelete='RESTRICT'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('exam_id', 'external_id', name='uq_writing_task_external_id'),
    sa.UniqueConstraint('exam_id', 'part_number', 'sub_part', name='uq_exam_part_structure')
    )
    with op.batch_alter_table('writing_tasks', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_writing_tasks_exam_id'), ['exam_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_writing_tasks_external_id'), ['external_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_writing_tasks_format_id'), ['format_id'], unique=False)

    op.create_table('exam_sessions',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('mock_exam_id', sa.String(length=64), nullable=False),
    sa.Column('exam_date', sa.DateTime(timezone=True), nullable=False),
    sa.Column('center_name', sa.String(), nullable=True),
    sa.Column('room_name', sa.String(), nullable=True),
    sa.Column('location_address', sa.String(), nullable=False),
    sa.Column('region', sa.String(), nullable=True),
    sa.Column('delivery_mode', sa.String(length=16), server_default='PAPER', nullable=False),
    sa.Column('registration_slug', sa.String(length=40), nullable=True),
    sa.Column('capacity', sa.Integer(), nullable=True),
    sa.Column('required_documents', sa.Text(), nullable=False),
    sa.Column('requires_online_payment', sa.Boolean(), nullable=False),
    sa.Column('is_active', sa.Boolean(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    sa.ForeignKeyConstraint(['mock_exam_id'], ['mock_exams.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('exam_sessions', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_exam_sessions_id'), ['id'], unique=False)
        batch_op.create_index(batch_op.f('ix_exam_sessions_registration_slug'), ['registration_slug'], unique=True)

    op.create_table('listening_part_options',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('part_id', sa.Integer(), nullable=False),
    sa.Column('external_id', sa.String(length=128), nullable=True),
    sa.Column('value', sa.String(), nullable=False),
    sa.Column('label', sa.String(), nullable=False),
    sa.ForeignKeyConstraint(['part_id'], ['listening_parts.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('part_id', 'external_id', name='uq_listening_part_option_external_id')
    )
    with op.batch_alter_table('listening_part_options', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_listening_part_options_external_id'), ['external_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_listening_part_options_id'), ['id'], unique=False)

    op.create_table('listening_questions',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('part_id', sa.Integer(), nullable=False),
    sa.Column('external_id', sa.String(length=128), nullable=True),
    sa.Column('question_number', sa.Integer(), nullable=False),
    sa.Column('type', sa.Enum('MULTIPLE_CHOICE', 'GAP_FILL', 'MATCHING', 'MAP_DIAGRAM', 'SENTENCE_COMPLETION', 'SHORT_ANSWER', name='listeningquestiontype', native_enum=False), nullable=False),
    sa.Column('text', sa.Text(), nullable=True),
    sa.Column('correct_answer', sa.String(), nullable=False),
    sa.ForeignKeyConstraint(['part_id'], ['listening_parts.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('part_id', 'external_id', name='uq_listening_question_external_id')
    )
    with op.batch_alter_table('listening_questions', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_listening_questions_external_id'), ['external_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_listening_questions_id'), ['id'], unique=False)

    op.create_table('mock_purchases',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('user_id', sa.Uuid(), nullable=False),
    sa.Column('mock_exam_id', sa.String(length=64), nullable=False),
    sa.Column('is_active', sa.Boolean(), nullable=False),
    sa.Column('comment', sa.String(), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    sa.ForeignKeyConstraint(['mock_exam_id'], ['mock_exams.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('mock_purchases', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_mock_purchases_id'), ['id'], unique=False)

    op.create_table('reading_questions',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('part_id', sa.Integer(), nullable=False),
    sa.Column('external_id', sa.String(length=128), nullable=True),
    sa.Column('question_number', sa.Integer(), nullable=False),
    sa.Column('type', sa.Enum('GAP_FILL', 'GAP_FILL_FILL', 'MULTIPLE_CHOICE', 'TRUE_FALSE_NOT_GIVEN', 'HEADINGS_MATCH', 'TEXT_MATCH', 'MULTIPLE_SELECT', name='readingquestiontype', native_enum=False), nullable=False),
    sa.Column('text', sa.Text(), nullable=False),
    sa.Column('correct_answer', sa.JSON(), nullable=False),
    sa.Column('word_limit', sa.Integer(), nullable=False),
    sa.ForeignKeyConstraint(['part_id'], ['reading_parts.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('part_id', 'external_id', name='uq_reading_question_external_id')
    )
    with op.batch_alter_table('reading_questions', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_reading_questions_external_id'), ['external_id'], unique=False)

    op.create_table('listening_question_options',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('question_id', sa.Integer(), nullable=False),
    sa.Column('external_id', sa.String(length=128), nullable=True),
    sa.Column('value', sa.String(), nullable=False),
    sa.Column('label', sa.String(), nullable=False),
    sa.ForeignKeyConstraint(['question_id'], ['listening_questions.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('question_id', 'external_id', name='uq_listening_question_option_external_id')
    )
    with op.batch_alter_table('listening_question_options', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_listening_question_options_external_id'), ['external_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_listening_question_options_id'), ['id'], unique=False)

    op.create_table('mock_registrations',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('user_id', sa.Uuid(), nullable=False),
    sa.Column('session_id', sa.Integer(), nullable=False),
    sa.Column('mock_exam_id', sa.String(length=64), nullable=False),
    sa.Column('full_name', sa.String(), nullable=False),
    sa.Column('student_id', sa.String(length=20), nullable=False),
    sa.Column('exam_access_id', sa.String(length=48), nullable=False),
    sa.Column('qr_token', sa.String(length=64), nullable=False),
    sa.Column('age', sa.Integer(), nullable=False),
    sa.Column('date_of_birth', sa.Date(), nullable=True),
    sa.Column('gender', sa.String(), nullable=False),
    sa.Column('region', sa.String(), nullable=False),
    sa.Column('photo_url', sa.String(), nullable=False),
    sa.Column('attended_confirmed_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('billing_order_id', sa.String(length=36), nullable=True),
    sa.Column('status', sa.Enum('REGISTERED', 'CHECKED_IN', 'IN_PROGRESS', 'COMPLETED', 'CANCELLED', name='registrationstatus', native_enum=False), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    sa.ForeignKeyConstraint(['billing_order_id'], ['billing_orders.id'], ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['mock_exam_id'], ['mock_exams.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['session_id'], ['exam_sessions.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('user_id', 'session_id', name='uq_user_session_registration')
    )
    with op.batch_alter_table('mock_registrations', schema=None) as batch_op:
        batch_op.create_index('ix_mock_registration_exam_created', ['mock_exam_id', 'created_at'], unique=False)
        batch_op.create_index('ix_mock_registration_session_created', ['session_id', 'created_at'], unique=False)
        batch_op.create_index(batch_op.f('ix_mock_registrations_exam_access_id'), ['exam_access_id'], unique=True)
        batch_op.create_index(batch_op.f('ix_mock_registrations_id'), ['id'], unique=False)
        batch_op.create_index(batch_op.f('ix_mock_registrations_qr_token'), ['qr_token'], unique=True)
        batch_op.create_index(batch_op.f('ix_mock_registrations_student_id'), ['student_id'], unique=True)

    op.create_table('reading_options',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('question_id', sa.Integer(), nullable=False),
    sa.Column('external_id', sa.String(length=128), nullable=True),
    sa.Column('label', sa.String(), nullable=False),
    sa.Column('value', sa.String(), nullable=False),
    sa.ForeignKeyConstraint(['question_id'], ['reading_questions.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('question_id', 'external_id', name='uq_reading_option_external_id')
    )
    with op.batch_alter_table('reading_options', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_reading_options_external_id'), ['external_id'], unique=False)

    op.create_table('exams_auth_sessions',
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('user_id', sa.Uuid(), nullable=False),
    sa.Column('registration_id', sa.Integer(), nullable=True),
    sa.Column('session_family_id', sa.Uuid(), nullable=False),
    sa.Column('rotated_from_id', sa.Uuid(), nullable=True),
    sa.Column('refresh_token_hash', sa.String(length=128), nullable=False),
    sa.Column('user_agent', sa.String(length=255), nullable=True),
    sa.Column('ip_address', sa.String(length=50), nullable=True),
    sa.Column('expires_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('is_revoked', sa.Boolean(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    sa.ForeignKeyConstraint(['registration_id'], ['mock_registrations.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('exams_auth_sessions', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_exams_auth_sessions_expires_at'), ['expires_at'], unique=False)
        batch_op.create_index(batch_op.f('ix_exams_auth_sessions_refresh_token_hash'), ['refresh_token_hash'], unique=False)
        batch_op.create_index(batch_op.f('ix_exams_auth_sessions_registration_id'), ['registration_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_exams_auth_sessions_session_family_id'), ['session_family_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_exams_auth_sessions_user_id'), ['user_id'], unique=False)

    op.create_table('exams_verification_codes',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('user_id', sa.Uuid(), nullable=False),
    sa.Column('registration_id', sa.Integer(), nullable=True),
    sa.Column('phone_snapshot', sa.String(length=32), nullable=False),
    sa.Column('code_hash', sa.String(length=128), nullable=True),
    sa.Column('failed_attempts', sa.Integer(), nullable=False),
    sa.Column('send_count', sa.Integer(), nullable=False),
    sa.Column('last_sent_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    sa.Column('expires_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('is_used', sa.Boolean(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    sa.ForeignKeyConstraint(['registration_id'], ['mock_registrations.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('exams_verification_codes', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_exams_verification_codes_expires_at'), ['expires_at'], unique=False)
        batch_op.create_index(batch_op.f('ix_exams_verification_codes_registration_id'), ['registration_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_exams_verification_codes_user_id'), ['user_id'], unique=False)

    op.create_table('mock_exam_attempts',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('user_id', sa.Uuid(), nullable=False),
    sa.Column('mock_exam_id', sa.String(length=64), nullable=False),
    sa.Column('registration_id', sa.Integer(), nullable=True),
    sa.Column('session_id', sa.Integer(), nullable=True),
    sa.Column('status', sa.Enum('CREATED', 'EXPIRED', 'IN_PROGRESS', 'PAPER_COMPLETED', 'SPEAKING_COMPLETED', 'READY_FOR_FINAL', 'FINALIZED', name='attemptstatus', native_enum=False), nullable=False),
    sa.Column('started_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    sa.Column('allowed_duration_seconds', sa.Integer(), nullable=True),
    sa.Column('expires_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('active_scope_key', sa.String(length=255), nullable=True),
    sa.Column('finished_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('is_finished', sa.Boolean(), nullable=False),
    sa.ForeignKeyConstraint(['mock_exam_id'], ['mock_exams.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['registration_id'], ['mock_registrations.id'], ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['session_id'], ['exam_sessions.id'], ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('registration_id')
    )
    with op.batch_alter_table('mock_exam_attempts', schema=None) as batch_op:
        batch_op.create_index('ix_mock_exam_attempt_session_started', ['session_id', 'started_at'], unique=False)
        batch_op.create_index('ix_mock_exam_attempt_user_exam_finished', ['user_id', 'mock_exam_id', 'is_finished'], unique=False)
        batch_op.create_index(batch_op.f('ix_mock_exam_attempts_active_scope_key'), ['active_scope_key'], unique=True)
        batch_op.create_index(batch_op.f('ix_mock_exam_attempts_expires_at'), ['expires_at'], unique=False)
        batch_op.create_index(batch_op.f('ix_mock_exam_attempts_id'), ['id'], unique=False)
        batch_op.create_index(batch_op.f('ix_mock_exam_attempts_session_id'), ['session_id'], unique=False)

    op.create_table('omr_submissions',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('module', sa.Enum('LISTENING', 'READING', name='papermodule', native_enum=False), nullable=False),
    sa.Column('exam_id', sa.String(), nullable=False),
    sa.Column('exam_version_id', sa.Integer(), nullable=False),
    sa.Column('candidate_id', sa.String(), nullable=False),
    sa.Column('registration_id', sa.Integer(), nullable=True),
    sa.Column('image_paths', sa.JSON(), nullable=False),
    sa.Column('detected_answers', sa.JSON(), nullable=True),
    sa.Column('per_question_result', sa.JSON(), nullable=True),
    sa.Column('correct_count', sa.Integer(), nullable=True),
    sa.Column('wrong_count', sa.Integer(), nullable=True),
    sa.Column('blank_count', sa.Integer(), nullable=True),
    sa.Column('invalid_count', sa.Integer(), nullable=True),
    sa.Column('total_score', sa.Integer(), nullable=True),
    sa.Column('status', sa.Enum('PENDING', 'PROCESSING', 'PROCESSED', 'NEEDS_REVIEW', 'FAILED', 'PUBLISHED', name='omrsubmissionstatus', native_enum=False), nullable=False),
    sa.Column('processed_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=True),
    sa.ForeignKeyConstraint(['exam_version_id'], ['exam_versions.id'], ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['registration_id'], ['mock_registrations.id'], ondelete='SET NULL'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('registration_id', 'module', 'exam_version_id', name='uq_omr_registration_module_version')
    )
    with op.batch_alter_table('omr_submissions', schema=None) as batch_op:
        batch_op.create_index('ix_omr_submission_status_created', ['status', 'created_at'], unique=False)
        batch_op.create_index(batch_op.f('ix_omr_submissions_candidate_id'), ['candidate_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_omr_submissions_exam_id'), ['exam_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_omr_submissions_id'), ['id'], unique=False)
        batch_op.create_index(batch_op.f('ix_omr_submissions_module'), ['module'], unique=False)
        batch_op.create_index(batch_op.f('ix_omr_submissions_registration_id'), ['registration_id'], unique=False)

    op.create_table('listening_results',
    sa.Column('exam_id', sa.String(length=64), nullable=False),
    sa.Column('total_questions', sa.Integer(), nullable=False),
    sa.Column('correct_answers', sa.Integer(), nullable=False),
    sa.Column('percentage', sa.Float(), nullable=False),
    sa.Column('practice_attempt_id', sa.Integer(), nullable=True),
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('user_id', sa.Uuid(), nullable=False),
    sa.Column('exam_attempt_id', sa.Integer(), nullable=True),
    sa.Column('raw_score', sa.Float(), nullable=False),
    sa.Column('standard_score', sa.Float(), nullable=True),
    sa.Column('cefr_level', sa.String(length=32), nullable=True),
    sa.Column('user_answers', sa.JSON(), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    sa.ForeignKeyConstraint(['exam_attempt_id'], ['mock_exam_attempts.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['exam_id'], ['listening_tests.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['practice_attempt_id'], ['practice_attempts.id'], ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('exam_attempt_id', 'exam_id', name='uq_listening_result_attempt_exam'),
    sa.UniqueConstraint('practice_attempt_id', name='uq_listening_result_practice_attempt')
    )
    with op.batch_alter_table('listening_results', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_listening_results_user_id'), ['user_id'], unique=False)

    op.create_table('mock_exam_results',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('user_id', sa.Uuid(), nullable=False),
    sa.Column('attempt_id', sa.Integer(), nullable=False),
    sa.Column('reading_ball', sa.Float(), nullable=False),
    sa.Column('listening_ball', sa.Float(), nullable=False),
    sa.Column('writing_ball', sa.Float(), nullable=False),
    sa.Column('speaking_ball', sa.Float(), nullable=False),
    sa.Column('overall_score', sa.Float(), nullable=False),
    sa.Column('cefr_level', sa.String(), nullable=True),
    sa.Column('status', sa.String(length=20), nullable=False),
    sa.Column('finalized_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('sms_sent_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    sa.ForeignKeyConstraint(['attempt_id'], ['mock_exam_attempts.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('attempt_id')
    )
    op.create_table('mock_skill_attempts',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('user_id', sa.Uuid(), nullable=False),
    sa.Column('attempt_id', sa.Integer(), nullable=False),
    sa.Column('skill', sa.Enum('READING', 'LISTENING', 'WRITING', 'SPEAKING', name='skilltype', native_enum=False), nullable=False),
    sa.Column('raw_score', sa.Float(), nullable=False),
    sa.Column('scaled_score', sa.Float(), nullable=False),
    sa.Column('cefr_level', sa.String(), nullable=True),
    sa.Column('user_answers', sa.JSON(), nullable=True),
    sa.Column('current_part', sa.String(length=128), nullable=True),
    sa.Column('current_question', sa.String(length=128), nullable=True),
    sa.Column('draft', sa.JSON(), nullable=True),
    sa.Column('last_saved_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('is_checked', sa.Boolean(), nullable=False),
    sa.Column('submitted_at', sa.DateTime(), nullable=True),
    sa.Column('speaking_test_id', sa.String(length=64), nullable=True),
    sa.Column('paper_version_id', sa.Integer(), nullable=True),
    sa.ForeignKeyConstraint(['attempt_id'], ['mock_exam_attempts.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['paper_version_id'], ['exam_versions.id'], ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['speaking_test_id'], ['speaking_tests.id'], ),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('attempt_id', 'skill', name='uq_mock_skill_attempt_attempt_skill')
    )
    with op.batch_alter_table('mock_skill_attempts', schema=None) as batch_op:
        batch_op.create_index('ix_mock_skill_attempt_attempt_skill', ['attempt_id', 'skill'], unique=False)
        batch_op.create_index('ix_mock_skill_user_skill_test_checked', ['user_id', 'skill', 'speaking_test_id', 'is_checked'], unique=False)

    op.create_table('reading_results',
    sa.Column('test_id', sa.String(length=64), nullable=False),
    sa.Column('percentage', sa.Float(), nullable=False),
    sa.Column('total_questions', sa.Integer(), nullable=False),
    sa.Column('practice_attempt_id', sa.Integer(), nullable=True),
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('user_id', sa.Uuid(), nullable=False),
    sa.Column('exam_attempt_id', sa.Integer(), nullable=True),
    sa.Column('raw_score', sa.Float(), nullable=False),
    sa.Column('standard_score', sa.Float(), nullable=True),
    sa.Column('cefr_level', sa.String(length=32), nullable=True),
    sa.Column('user_answers', sa.JSON(), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    sa.ForeignKeyConstraint(['exam_attempt_id'], ['mock_exam_attempts.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['practice_attempt_id'], ['practice_attempts.id'], ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['test_id'], ['reading_tests.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('exam_attempt_id', 'test_id', name='uq_reading_result_attempt_test'),
    sa.UniqueConstraint('practice_attempt_id', name='uq_reading_result_practice_attempt')
    )
    with op.batch_alter_table('reading_results', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_reading_results_user_id'), ['user_id'], unique=False)

    op.create_table('writing_results',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('user_id', sa.Uuid(), nullable=False),
    sa.Column('exam_id', sa.String(length=64), nullable=False),
    sa.Column('exam_attempt_id', sa.Integer(), nullable=True),
    sa.Column('practice_attempt_id', sa.Integer(), nullable=True),
    sa.Column('raw_score', sa.Float(), nullable=True),
    sa.Column('scaled_score', sa.Float(), nullable=True),
    sa.Column('cefr_level', sa.Enum('B1', 'B2', 'C1', name='cefrlevel', native_enum=False), nullable=True),
    sa.Column('is_finalized', sa.Boolean(), server_default=sa.false(), nullable=False),
    sa.Column('evaluation_status', sa.String(length=30), server_default='PENDING', nullable=False),
    sa.Column('human_evaluator_id', sa.Uuid(), nullable=True),
    sa.Column('ai_evaluated_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('ai_model', sa.String(length=100), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    sa.ForeignKeyConstraint(['exam_attempt_id'], ['mock_exam_attempts.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['exam_id'], ['writing_exams.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['human_evaluator_id'], ['users.id'], ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['practice_attempt_id'], ['practice_attempts.id'], ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('practice_attempt_id', name='uq_writing_result_practice_attempt')
    )
    with op.batch_alter_table('writing_results', schema=None) as batch_op:
        batch_op.create_index('ix_result_user_exam', ['user_id', 'exam_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_writing_results_evaluation_status'), ['evaluation_status'], unique=False)
        batch_op.create_index(batch_op.f('ix_writing_results_exam_attempt_id'), ['exam_attempt_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_writing_results_exam_id'), ['exam_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_writing_results_human_evaluator_id'), ['human_evaluator_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_writing_results_practice_attempt_id'), ['practice_attempt_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_writing_results_user_id'), ['user_id'], unique=False)

    op.create_table('attempt_answers',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('mock_skill_attempt_id', sa.Integer(), nullable=True),
    sa.Column('practice_attempt_id', sa.Integer(), nullable=True),
    sa.Column('question_number', sa.Integer(), nullable=False),
    sa.Column('question_id', sa.Integer(), nullable=True),
    sa.Column('answer', sa.JSON(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    sa.CheckConstraint('(mock_skill_attempt_id IS NOT NULL AND practice_attempt_id IS NULL) OR (mock_skill_attempt_id IS NULL AND practice_attempt_id IS NOT NULL)', name='ck_attempt_answer_one_owner'),
    sa.ForeignKeyConstraint(['mock_skill_attempt_id'], ['mock_skill_attempts.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['practice_attempt_id'], ['practice_attempts.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('mock_skill_attempt_id', 'question_number', name='uq_attempt_answer_mock_q'),
    sa.UniqueConstraint('practice_attempt_id', 'question_number', name='uq_attempt_answer_practice_q')
    )
    with op.batch_alter_table('attempt_answers', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_attempt_answers_mock_skill_attempt_id'), ['mock_skill_attempt_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_attempt_answers_practice_attempt_id'), ['practice_attempt_id'], unique=False)

    op.create_table('speaking_recording_drafts',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('user_id', sa.Uuid(), nullable=False),
    sa.Column('mock_skill_attempt_id', sa.Integer(), nullable=False),
    sa.Column('speaking_test_id', sa.String(length=64), nullable=False),
    sa.Column('part_external_id', sa.String(length=128), nullable=False),
    sa.Column('question_external_id', sa.String(length=128), nullable=False),
    sa.Column('chunk_urls', sa.JSON(), nullable=False),
    sa.Column('next_chunk_index', sa.Integer(), nullable=False),
    sa.Column('total_bytes', sa.Integer(), nullable=False),
    sa.Column('duration_seconds', sa.Integer(), nullable=True),
    sa.Column('status', sa.String(length=20), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    sa.ForeignKeyConstraint(['mock_skill_attempt_id'], ['mock_skill_attempts.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['speaking_test_id'], ['speaking_tests.id'], ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('mock_skill_attempt_id', 'part_external_id', 'question_external_id', name='uq_speaking_recording_draft_attempt_question')
    )
    with op.batch_alter_table('speaking_recording_drafts', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_speaking_recording_drafts_mock_skill_attempt_id'), ['mock_skill_attempt_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_speaking_recording_drafts_status'), ['status'], unique=False)
        batch_op.create_index(batch_op.f('ix_speaking_recording_drafts_user_id'), ['user_id'], unique=False)

    op.create_table('speaking_submissions',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('user_id', sa.Uuid(), nullable=False),
    sa.Column('mock_skill_attempt_id', sa.Integer(), nullable=True),
    sa.Column('practice_attempt_id', sa.Integer(), nullable=True),
    sa.Column('speaking_test_id', sa.String(length=64), nullable=False),
    sa.Column('question_external_id', sa.String(length=128), nullable=True),
    sa.Column('part_external_id', sa.String(length=128), nullable=True),
    sa.Column('audio_url', sa.String(length=500), nullable=False),
    sa.Column('audio_storage_key', sa.String(length=255), nullable=True),
    sa.Column('duration_seconds', sa.Integer(), nullable=True),
    sa.Column('status', sa.String(length=20), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    sa.ForeignKeyConstraint(['mock_skill_attempt_id'], ['mock_skill_attempts.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['practice_attempt_id'], ['practice_attempts.id'], ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['speaking_test_id'], ['speaking_tests.id'], ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('mock_skill_attempt_id', 'part_external_id', 'question_external_id', name='uq_speaking_submission_attempt_question'),
    sa.UniqueConstraint('practice_attempt_id', 'part_external_id', 'question_external_id', name='uq_speaking_submission_practice_question')
    )
    with op.batch_alter_table('speaking_submissions', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_speaking_submissions_audio_storage_key'), ['audio_storage_key'], unique=False)
        batch_op.create_index(batch_op.f('ix_speaking_submissions_part_external_id'), ['part_external_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_speaking_submissions_practice_attempt_id'), ['practice_attempt_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_speaking_submissions_question_external_id'), ['question_external_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_speaking_submissions_status'), ['status'], unique=False)
        batch_op.create_index(batch_op.f('ix_speaking_submissions_user_id'), ['user_id'], unique=False)

    op.create_table('writing_answers',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('result_id', sa.Integer(), nullable=False),
    sa.Column('task_id', sa.Integer(), nullable=False),
    sa.Column('content', sa.Text(), nullable=False),
    sa.Column('word_count', sa.Integer(), nullable=False),
    sa.Column('penalty', sa.Float(), server_default=sa.text('0'), nullable=False),
    sa.Column('raw_score', sa.Float(), nullable=True),
    sa.Column('scaled_score', sa.Float(), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    sa.ForeignKeyConstraint(['result_id'], ['writing_results.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['task_id'], ['writing_tasks.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('result_id', 'task_id', name='uq_answer_per_task')
    )
    with op.batch_alter_table('writing_answers', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_writing_answers_result_id'), ['result_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_writing_answers_task_id'), ['task_id'], unique=False)

    op.create_table('speaking_results',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('submission_id', sa.Integer(), nullable=False),
    sa.Column('evaluator_id', sa.Uuid(), nullable=True),
    sa.Column('fluency_coherence', sa.Float(), nullable=True),
    sa.Column('lexical_resource', sa.Float(), nullable=True),
    sa.Column('grammar_accuracy', sa.Float(), nullable=True),
    sa.Column('pronunciation', sa.Float(), nullable=True),
    sa.Column('overall_band', sa.Float(), nullable=True),
    sa.Column('cefr_level', sa.String(length=32), nullable=True),
    sa.Column('comment', sa.Text(), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    sa.ForeignKeyConstraint(['evaluator_id'], ['users.id'], ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['submission_id'], ['speaking_submissions.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('submission_id')
    )
    op.create_table('writing_feedbacks',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('answer_id', sa.Integer(), nullable=False),
    sa.Column('source', sa.Enum('AI', 'HUMAN', name='feedbacksource', native_enum=False), nullable=False),
    sa.Column('model_name', sa.String(length=100), nullable=True),
    sa.Column('content', sa.Text(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    sa.ForeignKeyConstraint(['answer_id'], ['writing_answers.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('writing_feedbacks', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_writing_feedbacks_answer_id'), ['answer_id'], unique=False)

    op.create_table('writing_scores',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('answer_id', sa.Integer(), nullable=False),
    sa.Column('criterion', sa.Enum('TASK_ACHIEVEMENT', 'COHERENCE', 'VOCABULARY', 'GRAMMAR', 'MECHANICS', 'OVERALL', name='writingcriterion', native_enum=False), nullable=False),
    sa.Column('score', sa.Float(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    sa.ForeignKeyConstraint(['answer_id'], ['writing_answers.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('answer_id', 'criterion', name='uq_score_per_criterion')
    )
    with op.batch_alter_table('writing_scores', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_writing_scores_answer_id'), ['answer_id'], unique=False)

    _seed_defaults()


def _seed_defaults() -> None:
    """Standart qatorlar. Baseline faqat yangi (bo'sh) jadvallarga yozadi, shuning uchun
    `bulk_insert` yetarli va offline (`--sql`) rejimda ham ishlaydi."""
    system_settings = sa.table(
        "system_settings",
        sa.column("key", sa.String),
        sa.column("value", sa.String),
    )
    op.bulk_insert(system_settings, [{"key": "sms_registration_enabled", "value": "false"}])

    # FREE: har bir ko'nikma uchun haftasiga 5 ta practice (Asia/Tashkent haftasi).
    tier_skill_limits = sa.table(
        "tier_skill_limits",
        sa.column("tier", sa.Enum("FREE", "PRO", "PREMIUM", name="subscriptiontier")),
        sa.column("skill", sa.String),
        sa.column("weekly_limit", sa.Integer),
    )
    op.bulk_insert(
        tier_skill_limits,
        [{"tier": "FREE", "skill": skill, "weekly_limit": 5}
         for skill in ("READING", "LISTENING", "WRITING", "SPEAKING")],
    )


def downgrade() -> None:
    with op.batch_alter_table('writing_scores', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_writing_scores_answer_id'))

    op.drop_table('writing_scores')
    with op.batch_alter_table('writing_feedbacks', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_writing_feedbacks_answer_id'))

    op.drop_table('writing_feedbacks')
    op.drop_table('speaking_results')
    with op.batch_alter_table('writing_answers', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_writing_answers_task_id'))
        batch_op.drop_index(batch_op.f('ix_writing_answers_result_id'))

    op.drop_table('writing_answers')
    with op.batch_alter_table('speaking_submissions', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_speaking_submissions_user_id'))
        batch_op.drop_index(batch_op.f('ix_speaking_submissions_status'))
        batch_op.drop_index(batch_op.f('ix_speaking_submissions_question_external_id'))
        batch_op.drop_index(batch_op.f('ix_speaking_submissions_practice_attempt_id'))
        batch_op.drop_index(batch_op.f('ix_speaking_submissions_part_external_id'))
        batch_op.drop_index(batch_op.f('ix_speaking_submissions_audio_storage_key'))

    op.drop_table('speaking_submissions')
    with op.batch_alter_table('speaking_recording_drafts', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_speaking_recording_drafts_user_id'))
        batch_op.drop_index(batch_op.f('ix_speaking_recording_drafts_status'))
        batch_op.drop_index(batch_op.f('ix_speaking_recording_drafts_mock_skill_attempt_id'))

    op.drop_table('speaking_recording_drafts')
    with op.batch_alter_table('attempt_answers', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_attempt_answers_practice_attempt_id'))
        batch_op.drop_index(batch_op.f('ix_attempt_answers_mock_skill_attempt_id'))

    op.drop_table('attempt_answers')
    with op.batch_alter_table('writing_results', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_writing_results_user_id'))
        batch_op.drop_index(batch_op.f('ix_writing_results_practice_attempt_id'))
        batch_op.drop_index(batch_op.f('ix_writing_results_human_evaluator_id'))
        batch_op.drop_index(batch_op.f('ix_writing_results_exam_id'))
        batch_op.drop_index(batch_op.f('ix_writing_results_exam_attempt_id'))
        batch_op.drop_index(batch_op.f('ix_writing_results_evaluation_status'))
        batch_op.drop_index('ix_result_user_exam')

    op.drop_table('writing_results')
    with op.batch_alter_table('reading_results', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_reading_results_user_id'))

    op.drop_table('reading_results')
    with op.batch_alter_table('mock_skill_attempts', schema=None) as batch_op:
        batch_op.drop_index('ix_mock_skill_user_skill_test_checked')
        batch_op.drop_index('ix_mock_skill_attempt_attempt_skill')

    op.drop_table('mock_skill_attempts')
    op.drop_table('mock_exam_results')
    with op.batch_alter_table('listening_results', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_listening_results_user_id'))

    op.drop_table('listening_results')
    with op.batch_alter_table('omr_submissions', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_omr_submissions_registration_id'))
        batch_op.drop_index(batch_op.f('ix_omr_submissions_module'))
        batch_op.drop_index(batch_op.f('ix_omr_submissions_id'))
        batch_op.drop_index(batch_op.f('ix_omr_submissions_exam_id'))
        batch_op.drop_index(batch_op.f('ix_omr_submissions_candidate_id'))
        batch_op.drop_index('ix_omr_submission_status_created')

    op.drop_table('omr_submissions')
    with op.batch_alter_table('mock_exam_attempts', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_mock_exam_attempts_session_id'))
        batch_op.drop_index(batch_op.f('ix_mock_exam_attempts_id'))
        batch_op.drop_index(batch_op.f('ix_mock_exam_attempts_expires_at'))
        batch_op.drop_index(batch_op.f('ix_mock_exam_attempts_active_scope_key'))
        batch_op.drop_index('ix_mock_exam_attempt_user_exam_finished')
        batch_op.drop_index('ix_mock_exam_attempt_session_started')

    op.drop_table('mock_exam_attempts')
    with op.batch_alter_table('exams_verification_codes', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_exams_verification_codes_user_id'))
        batch_op.drop_index(batch_op.f('ix_exams_verification_codes_registration_id'))
        batch_op.drop_index(batch_op.f('ix_exams_verification_codes_expires_at'))

    op.drop_table('exams_verification_codes')
    with op.batch_alter_table('exams_auth_sessions', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_exams_auth_sessions_user_id'))
        batch_op.drop_index(batch_op.f('ix_exams_auth_sessions_session_family_id'))
        batch_op.drop_index(batch_op.f('ix_exams_auth_sessions_registration_id'))
        batch_op.drop_index(batch_op.f('ix_exams_auth_sessions_refresh_token_hash'))
        batch_op.drop_index(batch_op.f('ix_exams_auth_sessions_expires_at'))

    op.drop_table('exams_auth_sessions')
    with op.batch_alter_table('reading_options', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_reading_options_external_id'))

    op.drop_table('reading_options')
    with op.batch_alter_table('mock_registrations', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_mock_registrations_student_id'))
        batch_op.drop_index(batch_op.f('ix_mock_registrations_qr_token'))
        batch_op.drop_index(batch_op.f('ix_mock_registrations_id'))
        batch_op.drop_index(batch_op.f('ix_mock_registrations_exam_access_id'))
        batch_op.drop_index('ix_mock_registration_session_created')
        batch_op.drop_index('ix_mock_registration_exam_created')

    op.drop_table('mock_registrations')
    with op.batch_alter_table('listening_question_options', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_listening_question_options_id'))
        batch_op.drop_index(batch_op.f('ix_listening_question_options_external_id'))

    op.drop_table('listening_question_options')
    with op.batch_alter_table('reading_questions', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_reading_questions_external_id'))

    op.drop_table('reading_questions')
    with op.batch_alter_table('mock_purchases', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_mock_purchases_id'))

    op.drop_table('mock_purchases')
    with op.batch_alter_table('listening_questions', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_listening_questions_id'))
        batch_op.drop_index(batch_op.f('ix_listening_questions_external_id'))

    op.drop_table('listening_questions')
    with op.batch_alter_table('listening_part_options', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_listening_part_options_id'))
        batch_op.drop_index(batch_op.f('ix_listening_part_options_external_id'))

    op.drop_table('listening_part_options')
    with op.batch_alter_table('exam_sessions', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_exam_sessions_registration_slug'))
        batch_op.drop_index(batch_op.f('ix_exam_sessions_id'))

    op.drop_table('exam_sessions')
    with op.batch_alter_table('writing_tasks', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_writing_tasks_format_id'))
        batch_op.drop_index(batch_op.f('ix_writing_tasks_external_id'))
        batch_op.drop_index(batch_op.f('ix_writing_tasks_exam_id'))

    op.drop_table('writing_tasks')
    with op.batch_alter_table('teacher_student_assignments', schema=None) as batch_op:
        batch_op.drop_index('uq_teacher_assign_student', sqlite_where=sa.text('student_id IS NOT NULL'), postgresql_where=sa.text('student_id IS NOT NULL'))
        batch_op.drop_index('uq_teacher_assign_group', sqlite_where=sa.text('group_id IS NOT NULL'), postgresql_where=sa.text('group_id IS NOT NULL'))
        batch_op.drop_index(batch_op.f('ix_teacher_student_assignments_teacher_id'))
        batch_op.drop_index(batch_op.f('ix_teacher_student_assignments_student_id'))
        batch_op.drop_index(batch_op.f('ix_teacher_student_assignments_organization_id'))
        batch_op.drop_index(batch_op.f('ix_teacher_student_assignments_group_id'))

    op.drop_table('teacher_student_assignments')
    with op.batch_alter_table('speaking_part_3', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_speaking_part_3_external_id'))

    op.drop_table('speaking_part_3')
    with op.batch_alter_table('speaking_part_2', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_speaking_part_2_external_id'))

    op.drop_table('speaking_part_2')
    with op.batch_alter_table('speaking_part_1_2', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_speaking_part_1_2_external_id'))

    op.drop_table('speaking_part_1_2')
    with op.batch_alter_table('speaking_part_1_1', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_speaking_part_1_1_external_id'))

    op.drop_table('speaking_part_1_1')
    with op.batch_alter_table('reading_parts', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_reading_parts_external_id'))

    op.drop_table('reading_parts')
    with op.batch_alter_table('organization_transactions', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_organization_transactions_organization_id'))
        batch_op.drop_index(batch_op.f('ix_organization_transactions_created_at'))

    op.drop_table('organization_transactions')
    with op.batch_alter_table('mock_exams', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_mock_exams_organization_id'))
        batch_op.drop_index(batch_op.f('ix_mock_exams_id'))

    op.drop_table('mock_exams')
    with op.batch_alter_table('listening_parts', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_listening_parts_id'))
        batch_op.drop_index(batch_op.f('ix_listening_parts_external_id'))

    op.drop_table('listening_parts')
    with op.batch_alter_table('group_members', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_group_members_student_id'))
        batch_op.drop_index(batch_op.f('ix_group_members_organization_id'))
        batch_op.drop_index(batch_op.f('ix_group_members_group_id'))

    op.drop_table('group_members')
    with op.batch_alter_table('evaluator_assignments', schema=None) as batch_op:
        batch_op.drop_index('uq_evaluator_assign_student', sqlite_where=sa.text('student_id IS NOT NULL'), postgresql_where=sa.text('student_id IS NOT NULL'))
        batch_op.drop_index('uq_evaluator_assign_group', sqlite_where=sa.text('group_id IS NOT NULL'), postgresql_where=sa.text('group_id IS NOT NULL'))
        batch_op.drop_index(batch_op.f('ix_evaluator_assignments_student_id'))
        batch_op.drop_index(batch_op.f('ix_evaluator_assignments_organization_id'))
        batch_op.drop_index(batch_op.f('ix_evaluator_assignments_group_id'))
        batch_op.drop_index(batch_op.f('ix_evaluator_assignments_evaluator_id'))

    op.drop_table('evaluator_assignments')
    with op.batch_alter_table('writing_exams', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_writing_exams_year'))
        batch_op.drop_index(batch_op.f('ix_writing_exams_organization_id'))
        batch_op.drop_index(batch_op.f('ix_writing_exams_is_practice'))
        batch_op.drop_index(batch_op.f('ix_writing_exams_id'))
        batch_op.drop_index('ix_exam_year_sequence')

    op.drop_table('writing_exams')
    with op.batch_alter_table('speaking_tests', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_speaking_tests_organization_id'))
        batch_op.drop_index(batch_op.f('ix_speaking_tests_is_practice'))
        batch_op.drop_index(batch_op.f('ix_speaking_tests_id'))

    op.drop_table('speaking_tests')
    with op.batch_alter_table('reading_tests', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_reading_tests_organization_id'))
        batch_op.drop_index(batch_op.f('ix_reading_tests_is_practice'))
        batch_op.drop_index(batch_op.f('ix_reading_tests_id'))

    op.drop_table('reading_tests')
    with op.batch_alter_table('practice_usage_log', schema=None) as batch_op:
        batch_op.drop_index('ix_practice_usage_user_skill_week')
        batch_op.drop_index(batch_op.f('ix_practice_usage_log_user_id'))
        batch_op.drop_index(batch_op.f('ix_practice_usage_log_attempt_id'))

    op.drop_table('practice_usage_log')
    with op.batch_alter_table('organization_members', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_organization_members_user_id'))
        batch_op.drop_index(batch_op.f('ix_organization_members_organization_id'))

    op.drop_table('organization_members')
    with op.batch_alter_table('organization_groups', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_organization_groups_organization_id'))

    op.drop_table('organization_groups')
    with op.batch_alter_table('organization_accounts', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_organization_accounts_account_number'))

    op.drop_table('organization_accounts')
    with op.batch_alter_table('listening_tests', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_listening_tests_organization_id'))
        batch_op.drop_index(batch_op.f('ix_listening_tests_is_practice'))
        batch_op.drop_index(batch_op.f('ix_listening_tests_id'))

    op.drop_table('listening_tests')
    with op.batch_alter_table('billing_orders', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_billing_orders_user_id'))
        batch_op.drop_index(batch_op.f('ix_billing_orders_status'))
        batch_op.drop_index(batch_op.f('ix_billing_orders_receipt_storage_key'))
        batch_op.drop_index(batch_op.f('ix_billing_orders_promo_code_id'))
        batch_op.drop_index(batch_op.f('ix_billing_orders_organization_id'))

    op.drop_table('billing_orders')
    with op.batch_alter_table('writing_criterion_configs', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_writing_criterion_configs_format_id'))

    op.drop_table('writing_criterion_configs')
    with op.batch_alter_table('user_subscriptions', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_user_subscriptions_user_id'))
        batch_op.drop_index(batch_op.f('ix_user_subscriptions_end_date'))

    op.drop_table('user_subscriptions')
    with op.batch_alter_table('user_sessions', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_user_sessions_user_id'))
        batch_op.drop_index(batch_op.f('ix_user_sessions_session_family_id'))
        batch_op.drop_index(batch_op.f('ix_user_sessions_refresh_token_hash'))
        batch_op.drop_index(batch_op.f('ix_user_sessions_expires_at'))

    op.drop_table('user_sessions')
    op.drop_table('user_profiles')
    with op.batch_alter_table('user_contacts', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_user_contacts_user_id'))

    op.drop_table('user_contacts')
    op.drop_table('system_settings')
    with op.batch_alter_table('promo_code_usages', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_promo_code_usages_user_id'))
        batch_op.drop_index(batch_op.f('ix_promo_code_usages_promo_code_id'))

    op.drop_table('promo_code_usages')
    with op.batch_alter_table('practice_attempts', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_practice_attempts_user_id'))
        batch_op.drop_index(batch_op.f('ix_practice_attempts_id'))
        batch_op.drop_index(batch_op.f('ix_practice_attempts_expires_at'))
        batch_op.drop_index(batch_op.f('ix_practice_attempts_active_scope_key'))
        batch_op.drop_index('ix_practice_attempt_user_skill_test')

    op.drop_table('practice_attempts')
    with op.batch_alter_table('organizations', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_organizations_slug'))

    op.drop_table('organizations')
    with op.batch_alter_table('identities', schema=None) as batch_op:
        batch_op.drop_index('ix_identity_user_provider')
        batch_op.drop_index(batch_op.f('ix_identities_user_id'))

    op.drop_table('identities')
    with op.batch_alter_table('exam_versions', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_exam_versions_module'))
        batch_op.drop_index(batch_op.f('ix_exam_versions_id'))
        batch_op.drop_index(batch_op.f('ix_exam_versions_exam_id'))

    op.drop_table('exam_versions')
    with op.batch_alter_table('balance_transactions', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_balance_transactions_user_id'))

    op.drop_table('balance_transactions')
    with op.batch_alter_table('writing_formats', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_writing_formats_penalty_enabled'))

    op.drop_table('writing_formats')
    with op.batch_alter_table('verification_codes', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_verification_codes_telegram_user_id'))
        batch_op.drop_index(batch_op.f('ix_verification_codes_target'))
        batch_op.drop_index(batch_op.f('ix_verification_codes_expires_at'))
        batch_op.drop_index(batch_op.f('ix_verification_codes_bot_token'))
        batch_op.drop_index('idx_active_code')

    op.drop_table('verification_codes')
    with op.batch_alter_table('users', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_users_telegram_user_id'))

    op.drop_table('users')
    op.drop_table('tier_skill_limits')
    with op.batch_alter_table('telegram_oidc_transactions', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_telegram_oidc_transactions_state_hash'))
        batch_op.drop_index(batch_op.f('ix_telegram_oidc_transactions_expires_at'))

    op.drop_table('telegram_oidc_transactions')
    op.drop_table('subscription_plans')
    with op.batch_alter_table('promo_codes', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_promo_codes_code'))

    op.drop_table('promo_codes')
    with op.batch_alter_table('org_api_keys', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_org_api_keys_organization_id'))
        batch_op.drop_index(batch_op.f('ix_org_api_keys_key_prefix'))

    op.drop_table('org_api_keys')
    with op.batch_alter_table('billing_products', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_billing_products_product_type'))
        batch_op.drop_index(batch_op.f('ix_billing_products_external_ref'))
        batch_op.drop_index(batch_op.f('ix_billing_products_code'))

    op.drop_table('billing_products')
