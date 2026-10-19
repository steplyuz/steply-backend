"""Tashkilot balansi BigInteger; listening audio ochiq static'dan private storage'ga

1. organization_accounts.balance va organization_transactions.balance_after: Integer → BigInteger.
   Postgres'da Integer ~2,1 mlrd so'mda to'ladi.
2. Listening audio: `/static/listening_audio/<f>` URL'lari `/api/v1/exams/listening-audio/<f>` ga
   o'tkaziladi (listening_tests.audio_url, listening_parts.audio_url), fayllar esa
   `static/listening_audio/` dan `storage/private/listening_audio/` ga ko'chiriladi.
   Ko'chirish idempotent: fayl allaqachon joyida bo'lsa o'tkazib yuboriladi.

Revision ID: 0005_bigint_balance_private_audio
Revises: 0004_exam_session_seats
"""
import shutil
from pathlib import Path
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0005_bigint_balance_private_audio"
down_revision: Union[str, None] = "0004_exam_session_seats"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_ROOT = Path(__file__).resolve().parents[2]
_OLD_DIR = _ROOT / "static" / "listening_audio"
_NEW_DIR = _ROOT / "storage" / "private" / "listening_audio"
_OLD_PREFIX = "/static/listening_audio/"
_NEW_PREFIX = "/api/v1/exams/listening-audio/"


def _move_audio_files() -> None:
    _NEW_DIR.mkdir(parents=True, exist_ok=True)
    if not _OLD_DIR.is_dir():
        return
    for path in _OLD_DIR.iterdir():
        if path.is_file():
            target = _NEW_DIR / path.name
            if not target.exists():
                shutil.move(str(path), str(target))


def upgrade() -> None:
    with op.batch_alter_table("organization_accounts") as batch:
        batch.alter_column(
            "balance",
            existing_type=sa.Integer(),
            type_=sa.BigInteger(),
            existing_nullable=False,
            existing_server_default="0",
        )
    with op.batch_alter_table("organization_transactions") as batch:
        batch.alter_column(
            "balance_after",
            existing_type=sa.Integer(),
            type_=sa.BigInteger(),
            existing_nullable=False,
        )

    for table in ("listening_tests", "listening_parts"):
        op.execute(
            sa.text(
                f"UPDATE {table} SET audio_url = REPLACE(audio_url, :old, :new) "
                f"WHERE audio_url LIKE :like"
            ).bindparams(old=_OLD_PREFIX, new=_NEW_PREFIX, like=_OLD_PREFIX + "%")
        )
    _move_audio_files()


def downgrade() -> None:
    for table in ("listening_tests", "listening_parts"):
        op.execute(
            sa.text(
                f"UPDATE {table} SET audio_url = REPLACE(audio_url, :new, :old) "
                f"WHERE audio_url LIKE :like"
            ).bindparams(old=_OLD_PREFIX, new=_NEW_PREFIX, like=_NEW_PREFIX + "%")
        )
    with op.batch_alter_table("organization_transactions") as batch:
        batch.alter_column("balance_after", existing_type=sa.BigInteger(), type_=sa.Integer(), existing_nullable=False)
    with op.batch_alter_table("organization_accounts") as batch:
        batch.alter_column(
            "balance",
            existing_type=sa.BigInteger(),
            type_=sa.Integer(),
            existing_nullable=False,
            existing_server_default="0",
        )
