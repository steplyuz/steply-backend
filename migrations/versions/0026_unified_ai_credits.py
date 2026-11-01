"""Umumiy AI kreditlar: Writing + Speaking uchun bitta ledger va bitta kredit jadvali.

  * `writing_ai_usage` + `speaking_ai_usage` -> `ai_usage` (feature = writing | speaking).
    Ledger'ga yozilmagan eski Writing AI feedbacklar (kod ilgari ularni "legacy" deb sanardi)
    shu yerda `subscription` sifatida ledger'ga ko'chiriladi, shuning uchun kvota hisobi o'zgarmaydi.
  * `writing_ai_credit_grants` -> `ai_credit_grants` (qatorlar ko'chiriladi).
  * `billingproducttype`: WRITING_AI_CREDITS -> AI_CREDITS (billing_orders / billing_products qiymatlari ham).
  * Ishlatilmagan `speaking_ai_credits` jadvali va `speaking_ai_jobs.credit_id` olib tashlanadi.

Idempotent (online): mavjud jadvallar tekshiriladi. Offline (--sql) rejim qo'llab-quvvatlanmaydi.

Revision ID: 0026_unified_ai_credits
Revises: 0025_align_mini_test_constraints
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect

revision = "0026_unified_ai_credits"
down_revision = "0025_align_mini_test_constraints"
branch_labels = None
depends_on = None


def _tables():
    return set(inspect(op.get_bind()).get_table_names())


def upgrade() -> None:
    bind = op.get_bind()
    if bind.__class__.__name__ == "MockConnection":
        raise RuntimeError("0026_unified_ai_credits offline (--sql) rejimda ishlamaydi")
    pg = bind.dialect.name == "postgresql"
    tables = _tables()

    # ---- 1. ai_usage ----
    if "ai_usage" not in tables:
        op.create_table(
            "ai_usage",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("user_id", sa.Uuid(as_uuid=True, native_uuid=True), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
            sa.Column("feature", sa.String(16), nullable=False),
            sa.Column("answer_id", sa.Integer(), sa.ForeignKey("writing_answers.id", ondelete="CASCADE"), nullable=True, unique=True),
            sa.Column("job_id", sa.Integer(), nullable=True, unique=True),
            sa.Column("billing_source", sa.String(20), nullable=False),
            sa.Column("purchased_units", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
            sa.CheckConstraint("billing_source IN ('subscription', 'purchased')", name="ck_ai_usage_source"),
            sa.CheckConstraint("feature IN ('writing', 'speaking')", name="ck_ai_usage_feature"),
        )
        op.create_index("ix_ai_usage_user_id", "ai_usage", ["user_id"])
        op.create_index("ix_ai_usage_user_source_created", "ai_usage", ["user_id", "billing_source", "created_at"])

    if "writing_ai_usage" in tables:
        op.execute(
            "INSERT INTO ai_usage (user_id, feature, answer_id, billing_source, purchased_units, created_at) "
            "SELECT user_id, 'writing', answer_id, billing_source, "
            "CASE WHEN billing_source = 'purchased' THEN 1 ELSE 0 END, created_at FROM writing_ai_usage "
            "WHERE answer_id NOT IN (SELECT answer_id FROM ai_usage WHERE answer_id IS NOT NULL)"
        )
    if "speaking_ai_usage" in tables:
        op.execute(
            "INSERT INTO ai_usage (user_id, feature, job_id, billing_source, purchased_units, created_at) "
            "SELECT user_id, 'speaking', job_id, billing_source, purchased_units, created_at FROM speaking_ai_usage "
            "WHERE job_id NOT IN (SELECT job_id FROM ai_usage WHERE job_id IS NOT NULL)"
        )
    # Ledger'siz eski AI feedbacklar -> subscription sarfi sifatida (oldin `legacy_used` hisoblagan).
    if {"writing_feedbacks", "writing_answers", "writing_results"} <= tables:
        op.execute(
            "INSERT INTO ai_usage (user_id, feature, answer_id, billing_source, purchased_units, created_at) "
            "SELECT r.user_id, 'writing', a.id, 'subscription', 0, MIN(f.created_at) "
            "FROM writing_feedbacks f "
            "JOIN writing_answers a ON a.id = f.answer_id "
            "JOIN writing_results r ON r.id = a.result_id "
            "WHERE f.source = 'AI' AND f.model_name IS NOT NULL AND TRIM(a.content) != '' "
            "AND a.id NOT IN (SELECT answer_id FROM ai_usage WHERE answer_id IS NOT NULL) "
            "GROUP BY a.id, r.user_id"
        )
    for old in ("writing_ai_usage", "speaking_ai_usage"):
        if old in tables:
            op.drop_table(old)

    # ---- 2. ai_credit_grants ----
    if "ai_credit_grants" not in tables:
        op.create_table(
            "ai_credit_grants",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("user_id", sa.Uuid(as_uuid=True, native_uuid=True), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
            sa.Column("order_id", sa.String(36), sa.ForeignKey("billing_orders.id", ondelete="CASCADE"), nullable=False, unique=True),
            sa.Column("credits_total", sa.Integer(), nullable=False),
            sa.Column("credits_remaining", sa.Integer(), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
            sa.CheckConstraint("credits_total > 0", name="ck_ai_credits_total_positive"),
            sa.CheckConstraint("credits_remaining >= 0 AND credits_remaining <= credits_total", name="ck_ai_credits_remaining_range"),
        )
        op.create_index("ix_ai_credit_grants_user_id", "ai_credit_grants", ["user_id"])
        op.create_index("ix_ai_credits_user_remaining", "ai_credit_grants", ["user_id", "credits_remaining"])
    if "writing_ai_credit_grants" in tables:
        op.execute(
            "INSERT INTO ai_credit_grants (id, user_id, order_id, credits_total, credits_remaining, created_at) "
            "SELECT id, user_id, order_id, credits_total, credits_remaining, created_at FROM writing_ai_credit_grants"
        )
        op.drop_table("writing_ai_credit_grants")

    # ---- 3. enum: WRITING_AI_CREDITS -> AI_CREDITS ----
    if pg:
        op.execute("ALTER TYPE billingproducttype RENAME VALUE 'WRITING_AI_CREDITS' TO 'AI_CREDITS'")
    else:
        for t in ("billing_orders", "billing_products"):
            if t in tables:
                op.execute(f"UPDATE {t} SET product_type = 'AI_CREDITS' WHERE product_type = 'WRITING_AI_CREDITS'")

    # ---- 4. ishlatilmaydigan speaking_ai_credits ----
    if "speaking_ai_jobs" in tables:
        cols = {c["name"] for c in inspect(bind).get_columns("speaking_ai_jobs")}
        if "credit_id" in cols:
            with op.batch_alter_table("speaking_ai_jobs") as batch:
                batch.drop_column("credit_id")
    if "speaking_ai_credits" in tables:
        op.drop_table("speaking_ai_credits")


def downgrade() -> None:
    bind = op.get_bind()
    pg = bind.dialect.name == "postgresql"
    tables = _tables()

    if "speaking_ai_credits" not in tables:
        op.create_table(
            "speaking_ai_credits",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("user_id", sa.Uuid(as_uuid=True, native_uuid=True), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
            sa.Column("credits_remaining", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        )
        op.create_index("ix_speaking_ai_credits_user_id", "speaking_ai_credits", ["user_id"])
    if "speaking_ai_jobs" in tables:
        cols = {c["name"] for c in inspect(bind).get_columns("speaking_ai_jobs")}
        if "credit_id" not in cols:
            with op.batch_alter_table("speaking_ai_jobs") as batch:
                batch.add_column(sa.Column("credit_id", sa.Integer(), nullable=True))
                batch.create_foreign_key("fk_speaking_ai_jobs_credit_id", "speaking_ai_credits", ["credit_id"], ["id"], ondelete="SET NULL")

    if pg:
        op.execute("ALTER TYPE billingproducttype RENAME VALUE 'AI_CREDITS' TO 'WRITING_AI_CREDITS'")
    else:
        for t in ("billing_orders", "billing_products"):
            op.execute(f"UPDATE {t} SET product_type = 'WRITING_AI_CREDITS' WHERE product_type = 'AI_CREDITS'")

    op.create_table(
        "writing_ai_credit_grants",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("user_id", sa.Uuid(as_uuid=True, native_uuid=True), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("order_id", sa.String(36), sa.ForeignKey("billing_orders.id", ondelete="CASCADE"), nullable=False, unique=True),
        sa.Column("credits_total", sa.Integer(), nullable=False),
        sa.Column("credits_remaining", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint("credits_total > 0", name="ck_writing_ai_credits_total_positive"),
        sa.CheckConstraint("credits_remaining >= 0 AND credits_remaining <= credits_total", name="ck_writing_ai_credits_remaining_range"),
    )
    op.create_index("ix_writing_ai_credit_grants_user_id", "writing_ai_credit_grants", ["user_id"])
    op.create_index("ix_writing_ai_credits_user_remaining", "writing_ai_credit_grants", ["user_id", "credits_remaining"])
    op.execute(
        "INSERT INTO writing_ai_credit_grants (id, user_id, order_id, credits_total, credits_remaining, created_at) "
        "SELECT id, user_id, order_id, credits_total, credits_remaining, created_at FROM ai_credit_grants"
    )
    op.drop_table("ai_credit_grants")

    op.create_table(
        "writing_ai_usage",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("user_id", sa.Uuid(as_uuid=True, native_uuid=True), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("answer_id", sa.Integer(), sa.ForeignKey("writing_answers.id", ondelete="CASCADE"), nullable=False, unique=True),
        sa.Column("billing_source", sa.String(20), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint("billing_source IN ('subscription', 'purchased')", name="ck_writing_ai_usage_source"),
    )
    op.create_index("ix_writing_ai_usage_user_id", "writing_ai_usage", ["user_id"])
    op.create_index("ix_writing_ai_usage_user_source_created", "writing_ai_usage", ["user_id", "billing_source", "created_at"])
    op.create_table(
        "speaking_ai_usage",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("user_id", sa.Uuid(as_uuid=True, native_uuid=True), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("job_id", sa.Integer(), nullable=False),
        sa.Column("billing_source", sa.String(20), nullable=False),
        sa.Column("purchased_units", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint("billing_source IN ('subscription', 'purchased')", name="ck_speaking_ai_usage_source"),
    )
    op.create_index("ix_speaking_ai_usage_user_id", "speaking_ai_usage", ["user_id"])
    op.create_index("ix_speaking_ai_usage_job_id", "speaking_ai_usage", ["job_id"], unique=True)
    op.create_index("ix_speaking_ai_usage_user_source_created", "speaking_ai_usage", ["user_id", "billing_source", "created_at"])
    op.execute("INSERT INTO writing_ai_usage (user_id, answer_id, billing_source, created_at) "
               "SELECT user_id, answer_id, billing_source, created_at FROM ai_usage WHERE feature = 'writing'")
    op.execute("INSERT INTO speaking_ai_usage (user_id, job_id, billing_source, purchased_units, created_at) "
               "SELECT user_id, job_id, billing_source, purchased_units, created_at FROM ai_usage WHERE feature = 'speaking'")
    op.drop_table("ai_usage")
