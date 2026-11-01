"""Barcha 4 skill testi uchun umumiy metadata (ExamCommonMixin).

speaking_tests: cefr_level, duration_minutes, total_questions, parent_test_id, practice_part_number
reading_tests / listening_tests: description
writing_exams: description, total_questions, parent_test_id, practice_part_number

Idempotent (online): mavjud ustun qayta qo'shilmaydi. Offline (--sql): to'g'ridan-to'g'ri DDL.

Revision ID: 0024_common_exam_fields
Revises: 0023_speaking_shared_ai_pool
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect

revision = "0024_common_exam_fields"
down_revision = "0023_speaking_shared_ai_pool"
branch_labels = None
depends_on = None

SPECS = {
    "speaking_tests": {
        "description": sa.Text(),
        "cefr_level": sa.String(16),
        "duration_minutes": sa.Integer(),
        "total_questions": sa.Integer(),
        "parent_test_id": sa.String(64),
        "practice_part_number": sa.Integer(),
    },
    "reading_tests": {"description": sa.Text()},
    "listening_tests": {"description": sa.Text()},
    "writing_exams": {
        "description": sa.Text(),
        "total_questions": sa.Integer(),
        "parent_test_id": sa.String(64),
        "practice_part_number": sa.Integer(),
    },
}


def _offline() -> bool:
    return op.get_context().as_sql


def _existing_columns(table: str) -> set:
    if _offline():
        return set()
    return {c["name"] for c in inspect(op.get_bind()).get_columns(table)}


def _has_index(table: str, name: str) -> bool:
    if _offline():
        return False
    return name in {ix["name"] for ix in inspect(op.get_bind()).get_indexes(table)}


def upgrade() -> None:
    sqlite = op.get_context().dialect.name == "sqlite"
    for table, cols in SPECS.items():
        have = _existing_columns(table)
        missing = {k: v for k, v in cols.items() if k not in have}
        if not missing:
            continue
        if sqlite:
            with op.batch_alter_table(table) as batch:
                for name, typ in missing.items():
                    batch.add_column(sa.Column(name, typ, nullable=True))
                if "parent_test_id" in missing:
                    batch.create_foreign_key(
                        f"fk_{table}_parent_test_id", table, ["parent_test_id"], ["id"], ondelete="SET NULL",
                    )
        else:
            for name, typ in missing.items():
                op.add_column(table, sa.Column(name, typ, nullable=True))
            if "parent_test_id" in missing:
                op.create_foreign_key(
                    f"fk_{table}_parent_test_id", table, table, ["parent_test_id"], ["id"], ondelete="SET NULL",
                )
        if "parent_test_id" in missing and not _has_index(table, f"ix_{table}_parent_test_id"):
            op.create_index(f"ix_{table}_parent_test_id", table, ["parent_test_id"])


def downgrade() -> None:
    sqlite = op.get_context().dialect.name == "sqlite"
    for table, cols in SPECS.items():
        have = _existing_columns(table)
        present = [c for c in cols if c in have]
        if not present:
            continue
        if "parent_test_id" in present:
            if _has_index(table, f"ix_{table}_parent_test_id"):
                op.drop_index(f"ix_{table}_parent_test_id", table_name=table)
        if sqlite:
            with op.batch_alter_table(table) as batch:
                if "parent_test_id" in present:
                    batch.drop_constraint(f"fk_{table}_parent_test_id", type_="foreignkey")
                for name in present:
                    batch.drop_column(name)
        else:
            if "parent_test_id" in present:
                op.drop_constraint(f"fk_{table}_parent_test_id", table, type_="foreignkey")
            for name in present:
                op.drop_column(table, name)
