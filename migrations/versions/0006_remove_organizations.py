"""Tashkilot (B2B organization) qismini olib tashlash

1. users.global_role = 'ORGANIZATION' bo'lganlar STUDENT ga o'tkaziladi.
2. Tashkilotga bog'liq jadvallar o'chiriladi (bolalar -> ota tartibida).
3. billing_orders, mock_exams, reading_tests, listening_tests, speaking_tests,
   writing_exams jadvallaridan organization_id ustuni (FK va indeks bilan) olib tashlanadi.

Qaytarib bo'lmaydi: downgrade qo'llab-quvvatlanmaydi (zaxira nusxadan tiklang).

Revision ID: 0006_remove_organizations
Revises: 0005_bigint_balance_private_audio
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import context, op

revision: str = "0006_remove_organizations"
down_revision: Union[str, None] = "0005_bigint_balance_private_audio"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_DROP_TABLES = (
    "evaluator_assignments",
    "group_members",
    "teacher_student_assignments",
    "organization_groups",
    "organization_members",
    "organization_transactions",
    "organization_accounts",
    "org_api_keys",
    "organizations",
)
_COLUMN_TABLES = (
    "billing_orders",
    "mock_exams",
    "reading_tests",
    "listening_tests",
    "speaking_tests",
    "writing_exams",
)


def upgrade() -> None:
    bind = op.get_bind()
    offline = context.is_offline_mode()  # --sql: bazani tekshirib bo'lmaydi, nomlar konvensiyadan olinadi
    insp = None if offline else sa.inspect(bind)
    tables = set(_DROP_TABLES) | set(_COLUMN_TABLES) | {"users"} if offline else set(insp.get_table_names())

    if "users" in tables:
        op.execute("UPDATE users SET global_role = 'STUDENT' WHERE global_role = 'ORGANIZATION'")

    if "users" in tables and bind.dialect.name != "postgresql":
        # Postgres'da native enum'dan qiymat olib tashlab bo'lmaydi (qolgan qiymat zarar qilmaydi).
        with op.batch_alter_table("users") as batch:
            batch.alter_column(
                "global_role",
                existing_type=sa.Enum("STUDENT", "ADMIN", "SUPER_ADMIN", "ORGANIZATION", "TEACHER", name="userrole"),
                type_=sa.Enum("STUDENT", "ADMIN", "SUPER_ADMIN", "TEACHER", name="userrole"),
                existing_nullable=False,
            )

    for table in _COLUMN_TABLES:
        if table not in tables:
            continue
        if offline:
            fks = [f"{table}_organization_id_fkey"]
            indexes = [f"ix_{table}_organization_id"]
        else:
            cols = {c["name"] for c in insp.get_columns(table)}
            if "organization_id" not in cols:
                continue
            fks = [
                fk["name"] for fk in insp.get_foreign_keys(table)
                if "organization_id" in fk["constrained_columns"] and fk.get("name")
            ]
            indexes = [i["name"] for i in insp.get_indexes(table) if "organization_id" in i["column_names"]]
        with op.batch_alter_table(table) as batch:
            for name in fks:
                batch.drop_constraint(name, type_="foreignkey")
            for name in indexes:
                batch.drop_index(name)
            batch.drop_column("organization_id")

    for table in _DROP_TABLES:
        if table in tables:
            op.drop_table(table)


def downgrade() -> None:
    raise NotImplementedError("0006_remove_organizations qaytarilmaydi; zaxira nusxadan tiklang")
