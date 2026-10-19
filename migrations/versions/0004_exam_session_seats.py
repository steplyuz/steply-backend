"""exam_sessions.seats_taken (atomik joy band qilish) va mock_registrations.phone_last4 (ikkinchi omil)

Eski tekshiruv (COUNT, keyin INSERT) SQLite'da poyga bergan: ikki parallel so'rov
bitta bo'sh joyga ikkalasini ham yozib yuborardi. Endi band joylar soni qatorda
saqlanadi va shartli UPDATE (seats < capacity) bilan atomik oshiriladi.

Revision ID: 0004_exam_session_seats
Revises: 0003_otp_send_limits
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0004_exam_session_seats"
down_revision: Union[str, None] = "0003_otp_send_limits"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "exam_sessions",
        sa.Column("seats_taken", sa.Integer(), nullable=False, server_default="0"),
    )
    op.add_column("mock_registrations", sa.Column("phone_last4", sa.String(length=4), nullable=True))
    # Mavjud ro'yxatdan o'tishlardan hisoblagichni to'ldirish (bekor qilinmaganlar).
    op.execute(
        "UPDATE exam_sessions SET seats_taken = ("
        "SELECT COUNT(*) FROM mock_registrations r "
        "WHERE r.session_id = exam_sessions.id AND r.status != 'CANCELLED')"
    )


def downgrade() -> None:
    op.drop_column("mock_registrations", "phone_last4")
    op.drop_column("exam_sessions", "seats_taken")
