
"""Unified practice quota and independent analysis quota.

Revision ID: 0010_unified_practice_analysis
Revises: 0009_global_practice_limits
"""
from alembic import op
import sqlalchemy as sa

revision = "0010_unified_practice_analysis"
down_revision = "0009_global_practice_limits"
branch_labels = None
depends_on = None


def _tables():
    return set(sa.inspect(op.get_bind()).get_table_names())


def _indexes(table):
    inspector = sa.inspect(op.get_bind())
    return {item["name"] for item in inspector.get_indexes(table)}


def _create_table_if_missing(name, *columns, **kwargs):
    if name not in _tables():
        op.create_table(name, *columns, **kwargs)


def _create_index_if_missing(name, table, columns):
    if name not in _indexes(table):
        op.create_index(name, table, columns)


def upgrade():
    # Offline SQL generation uses Alembic's MockConnection, which cannot be
    # inspected. This revision contains conditional/idempotent DDL and needs a
    # live schema to decide which statements to emit; don't crash offline mode.
    bind = op.get_bind()
    if bind.__class__.__name__ == "MockConnection":
        return
    # 1. Tahlil limitlari jadvali.
    _create_table_if_missing(
        "tier_analysis_limits",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "tier",
            sa.Enum("FREE", "PLUS", "PRO",
                    name="subscriptiontier", length=7),
            nullable=False,
            unique=True,
        ),
        sa.Column("weekly_limit", sa.Integer(), nullable=True),
        sa.Column("monthly_limit", sa.Integer(), nullable=True),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )

    # Faqat mavjud bo'lmagan tariflarni qo'shish.
    bind = op.get_bind()
    existing_tiers = set(
        bind.execute(
            sa.text("SELECT tier FROM tier_analysis_limits")
        ).scalars().all()
    )

    defaults = [
        ("FREE", 5, 20),
        ("PLUS", None, None),
        ("PRO", None, None),
    ]

    for tier, weekly, monthly in defaults:
        if tier not in existing_tiers:
            bind.execute(
                sa.text("""
                    INSERT INTO tier_analysis_limits
                        (tier, weekly_limit, monthly_limit)
                    VALUES (:tier, :weekly, :monthly)
                """),
                {
                    "tier": tier,
                    "weekly": weekly,
                    "monthly": monthly,
                },
            )

    # 2. Analysis usage jadvali 0009 da yaratilgan bo'lishi kerak.
    tables = _tables()
    if "analysis_usage_log" not in tables:
        op.create_table(
            "analysis_usage_log",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column(
                "user_id", sa.Uuid(),
                sa.ForeignKey("users.id", ondelete="CASCADE"),
                nullable=False,
            ),
            sa.Column("skill", sa.String(20), nullable=False),
            sa.Column("test_id", sa.String(64), nullable=False),
            sa.Column("week_start", sa.Date(), nullable=False),
            sa.Column(
                "consumed_at",
                sa.DateTime(timezone=True),
                server_default=sa.func.now(),
                nullable=False,
            ),
        )

    if "ix_analysis_usage_log_user_id" not in _indexes(
        "analysis_usage_log"
    ):
        op.create_index(
            "ix_analysis_usage_log_user_id",
            "analysis_usage_log",
            ["user_id"],
        )

    _create_index_if_missing(
        "ix_analysis_usage_user_week",
        "analysis_usage_log",
        ["user_id", "week_start"],
    )

    # 3. Umumiy practice usage jurnali.
    _create_table_if_missing(
        "global_practice_usage_log",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "user_id", sa.Uuid(),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("skill", sa.String(20), nullable=False),
        sa.Column("test_id", sa.String(64), nullable=False),
        sa.Column(
            "attempt_id",
            sa.Integer(),
            sa.ForeignKey("practice_attempts.id", ondelete="SET NULL"),
            nullable=True,
            unique=True,
        ),
        sa.Column("week_start", sa.Date(), nullable=False),
        sa.Column(
            "consumed_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )

    _create_index_if_missing(
        "ix_global_practice_usage_log_user_id",
        "global_practice_usage_log",
        ["user_id"],
    )
    _create_index_if_missing(
        "ix_global_practice_user_week",
        "global_practice_usage_log",
        ["user_id", "week_start"],
    )
    _create_index_if_missing(
        "ix_global_practice_user_consumed",
        "global_practice_usage_log",
        ["user_id", "consumed_at"],
    )

    # 4. Testga biriktirilgan tahlil videolari.
    _create_table_if_missing(
        "analysis_videos",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("skill", sa.String(20), nullable=False),
        sa.Column("test_id", sa.String(64), nullable=False),
        sa.Column("title", sa.String(200), nullable=False),
        sa.Column("video_path", sa.String(500), nullable=False),
        sa.Column(
            "created_by",
            sa.Uuid(),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.UniqueConstraint(
            "skill", "test_id",
            name="uq_analysis_video_skill_test",
        ),
    )

    # 5. Eski practice usage yozuvlarini ko'chirish.
    tables = _tables()
    if (
        "practice_usage_log" in tables
        and "global_practice_usage_log" in tables
    ):
        source_columns = {
            c["name"] for c in sa.inspect(op.get_bind())
            .get_columns("practice_usage_log")
        }
        required = {
            "user_id", "skill", "test_id",
            "attempt_id", "week_start", "consumed_at",
        }

        if required.issubset(source_columns):
            op.execute(sa.text("""
                INSERT OR IGNORE INTO global_practice_usage_log
                    (user_id, skill, test_id, attempt_id,
                     week_start, consumed_at)
                SELECT user_id, skill, test_id, attempt_id,
                       week_start, consumed_at
                FROM practice_usage_log
            """))

    # 6. Eski jadvallarni faqat ko'chirishdan keyin olib tashlash.
    # Practice jadvali mavjud bo'lmasa, drop qilinmaydi.
    if "practice_usage_log" in _tables():
        op.drop_table("practice_usage_log")

    if "tier_skill_limits" in _tables():
        op.drop_table("tier_skill_limits")


def downgrade():
    # Eski ma'lumotlarni qayta tiklash kafolatlanmagani uchun
    # avtomatik downgrade o'rniga backupdan tiklash tavsiya qilinadi.
    raise NotImplementedError(
        "Downgrade qo'llab-quvvatlanmaydi. "
        "Migratsiyadan oldingi backupdan foydalaning."
    )
