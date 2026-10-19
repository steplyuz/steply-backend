"""active_scope_key uchun takroriy UNIQUE indekslarni olib tashlash

Eski `init_db()` modeldagi `ix_*_active_scope_key` (unique) indeksiga qo'shimcha
`uq_mock_exam_attempts_active_scope_key` va `uq_practice_attempts_active_scope_key`
indekslarini qo'lda yaratardi. Ular modelda yo'q va ortiqcha (bir ustunda ikkita
bir xil UNIQUE). Yangi bazalarda ular umuman yaratilmaydi; eski bazalarda
(`alembic stamp 0001_baseline` bilan o'tkazilganlarda) bu migratsiya ularni olib tashlaydi.

Revision ID: 0002_drop_dup_scope_idx
Revises: 0001_baseline
"""
from typing import Sequence, Union

from alembic import op

revision: str = "0002_drop_dup_scope_idx"
down_revision: Union[str, None] = "0001_baseline"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # IF EXISTS: SQLite va Postgres'da bir xil ishlaydi; yangi bazada indeks yo'q.
    op.execute("DROP INDEX IF EXISTS uq_mock_exam_attempts_active_scope_key")
    op.execute("DROP INDEX IF EXISTS uq_practice_attempts_active_scope_key")


def downgrade() -> None:
    # Takroriy indekslar qayta yaratilmaydi: ular hech qachon kerak bo'lmagan.
    pass
