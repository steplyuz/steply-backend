"""inPAY avtomatik to'lov maydonlari (billing_orders).

Idempotent: ilova startup'idagi create_all ustunlarni oldindan yaratgan bo'lishi mumkin.
Offline (--sql) rejimda inspeksiya mumkin emas, shuning uchun DDL to'g'ridan-to'g'ri chiqariladi.

Revision ID: 0022_inpay_payments
Revises: 0021_speaking_plan_promo
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect

revision = "0022_inpay_payments"
down_revision = "0021_speaking_plan_promo"
branch_labels = None
depends_on = None

TABLE = "billing_orders"
COLUMNS = {
    "payment_provider": sa.String(20),
    "provider_payment_id": sa.String(64),
    "provider_checkout_url": sa.String(500),
}
INDEX = "ix_billing_orders_provider_payment_id"


def _state():
    if op.get_context().as_sql:
        return None
    insp = inspect(op.get_bind())
    cols = {c["name"] for c in insp.get_columns(TABLE)}
    idx = {ix["name"] for ix in insp.get_indexes(TABLE)}
    return cols, idx


def upgrade() -> None:
    state = _state()
    existing_cols, existing_idx = state if state is not None else (set(), set())
    missing = {k: v for k, v in COLUMNS.items() if k not in existing_cols} if state is not None else COLUMNS

    if missing:
        with op.batch_alter_table(TABLE) as batch:
            for name, col_type in missing.items():
                batch.add_column(sa.Column(name, col_type, nullable=True))
    if INDEX not in existing_idx:
        op.create_index(INDEX, TABLE, ["provider_payment_id"], unique=True)


def downgrade() -> None:
    state = _state()
    existing_cols, existing_idx = state if state is not None else (set(COLUMNS), {INDEX})
    if INDEX in existing_idx:
        op.drop_index(INDEX, table_name=TABLE)
    present = [k for k in COLUMNS if k in existing_cols]
    if present:
        with op.batch_alter_table(TABLE) as batch:
            for name in present:
                batch.drop_column(name)
