"""Speaking AI umumiy AI kredit pool'idan: speaking_ai_usage ledger; tarifdagi alohida limit olib tashlanadi.

Idempotent (online rejim): ilova startup'idagi create_all jadvalni oldindan yaratgan bo'lishi mumkin,
shuning uchun mavjud jadval/indeks/ustun qayta yaratilmaydi. Offline (--sql) rejimda inspeksiya
mumkin emas, shuning uchun DDL to'g'ridan-to'g'ri chiqariladi.

Revision ID: 0023_speaking_shared_ai_pool
Revises: 0022_inpay_payments
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect

revision = "0023_speaking_shared_ai_pool"
down_revision = "0022_inpay_payments"
branch_labels = None
depends_on = None

TABLE = "speaking_ai_usage"
INDEXES = {
    "ix_speaking_ai_usage_user_id": (["user_id"], False),
    "ix_speaking_ai_usage_job_id": (["job_id"], True),
    "ix_speaking_ai_usage_user_source_created": (["user_id", "billing_source", "created_at"], False),
}


def _state():
    """(tables, index_names, plan_columns) — offline rejimda None."""
    if op.get_context().as_sql:
        return None
    insp = inspect(op.get_bind())
    tables = set(insp.get_table_names())
    indexes = {ix["name"] for ix in insp.get_indexes(TABLE)} if TABLE in tables else set()
    plan_cols = {c["name"] for c in insp.get_columns("subscription_plans")}
    return tables, indexes, plan_cols


def upgrade() -> None:
    state = _state()
    table_exists = state is not None and TABLE in state[0]
    existing_indexes = state[1] if state is not None else set()
    plan_cols = state[2] if state is not None else {"speaking_ai_limit"}

    if not table_exists:
        op.create_table(
            TABLE,
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("user_id", sa.Uuid(as_uuid=True, native_uuid=True), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
            sa.Column("job_id", sa.Integer(), nullable=False),
            sa.Column("billing_source", sa.String(20), nullable=False),
            sa.Column("purchased_units", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
            sa.CheckConstraint("billing_source IN ('subscription', 'purchased')", name="ck_speaking_ai_usage_source"),
        )
    for name, (cols, unique) in INDEXES.items():
        if name not in existing_indexes:
            op.create_index(name, TABLE, cols, unique=unique)

    if "speaking_ai_limit" in plan_cols:
        with op.batch_alter_table("subscription_plans") as batch:
            batch.drop_column("speaking_ai_limit")


def downgrade() -> None:
    state = _state()
    table_exists = state is None or TABLE in state[0]
    plan_cols = state[2] if state is not None else set()

    if "speaking_ai_limit" not in plan_cols:
        with op.batch_alter_table("subscription_plans") as batch:
            batch.add_column(sa.Column("speaking_ai_limit", sa.Integer(), nullable=False, server_default="0"))
    if table_exists:
        op.drop_table(TABLE)
