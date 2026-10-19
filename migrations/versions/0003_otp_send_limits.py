"""OTP yuborish limiti: telefon + IP bo'yicha hisoblagich jadvali

Eski mexanizm bloklashni faqat telefon bo'yicha qo'llar edi, shu sababli begona
kishi 3 marta so'rov yuborib egasini 60 daqiqaga bloklay olardi. Endi hisoblagich
(target, purpose, ip_hash) juftligi bo'yicha saqlanadi.

Revision ID: 0003_otp_send_limits
Revises: 0002_drop_dup_scope_idx
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0003_otp_send_limits"
down_revision: Union[str, None] = "0002_drop_dup_scope_idx"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "otp_send_limits",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("target", sa.String(length=255), nullable=False),
        sa.Column("purpose", sa.String(length=32), nullable=False),
        sa.Column("ip_hash", sa.String(length=64), nullable=False),
        sa.Column("send_count", sa.Integer(), nullable=False),
        sa.Column("window_started_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("target", "purpose", "ip_hash", name="uq_otp_send_limits_target_purpose_ip"),
    )


def downgrade() -> None:
    op.drop_table("otp_send_limits")
