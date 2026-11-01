"""Modellar bilan migratsiyalarni tenglashtirish (`alembic check` toza bo'lishi uchun).

Reading mini-testlar `source_part_id` o'rniga `(parent_test_id, practice_part_number)` bilan aniqlanadi
(Listening/0017 dagi kabi), shuning uchun:
  * reading_tests: source_part_id ustuni, uning indeksi, FK'i va eski unique'i olib tashlanadi;
    `uq_reading_mini_test_part_number` qo'shiladi.
  * speaking_tests / writing_exams: `uq_*_mini_test_part_number` qo'shiladi.

Ma'lumot yo'qolishi: faqat `reading_tests.source_part_id` (kodda ishlatilmaydi). Unique qo'shishdan oldin
dublikat (parent_test_id, practice_part_number) tekshiriladi — bo'lsa, aniq xabar bilan to'xtaydi.

Revision ID: 0025_align_mini_test_constraints
Revises: 0024_common_exam_fields
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect

revision = "0025_align_mini_test_constraints"
down_revision = "0024_common_exam_fields"
branch_labels = None
depends_on = None

UNIQUES = {
    "reading_tests": "uq_reading_mini_test_part_number",
    "speaking_tests": "uq_speaking_mini_test_part_number",
    "writing_exams": "uq_writing_mini_test_part_number",
}
COLS = ["parent_test_id", "practice_part_number"]


def _offline() -> bool:
    return op.get_context().as_sql


def _insp():
    return inspect(op.get_bind())


def _has_unique(table: str, name: str) -> bool:
    if _offline():
        return False
    return name in {u["name"] for u in _insp().get_unique_constraints(table)}


def _assert_no_duplicates(table: str) -> None:
    if _offline():
        return
    dup = op.get_bind().execute(sa.text(
        f"SELECT parent_test_id, practice_part_number, COUNT(*) c FROM {table} "
        "WHERE parent_test_id IS NOT NULL AND practice_part_number IS NOT NULL "
        "GROUP BY parent_test_id, practice_part_number HAVING COUNT(*) > 1"
    )).fetchall()
    if dup:
        raise RuntimeError(
            f"{table}: takroriy (parent_test_id, practice_part_number) topildi: "
            f"{[tuple(r) for r in dup][:5]}. Avval dublikat mini-testlarni o'chiring."
        )


def upgrade() -> None:
    # --- reading_tests: source_part_id olib tashlanadi ---
    if _offline():  # --sql: bazani tekshirib bo'lmaydi, 0014 dagi holat deb olinadi
        cols = {"source_part_id"}
        idx = {"ix_reading_tests_source_part_id"}
        fks = {"fk_reading_tests_source_part_id"}
        uqs = {"uq_reading_mini_test_source_part"}
    else:
        insp = _insp()
        cols = {c["name"] for c in insp.get_columns("reading_tests")}
        idx = {i["name"] for i in insp.get_indexes("reading_tests")}
        fks = {f["name"] for f in insp.get_foreign_keys("reading_tests")}
        uqs = {u["name"] for u in insp.get_unique_constraints("reading_tests")}
    if "source_part_id" in cols:
        with op.batch_alter_table("reading_tests", recreate="auto") as batch:
            if "uq_reading_mini_test_source_part" in uqs:
                batch.drop_constraint("uq_reading_mini_test_source_part", type_="unique")
            if "fk_reading_tests_source_part_id" in fks:
                batch.drop_constraint("fk_reading_tests_source_part_id", type_="foreignkey")
            if "ix_reading_tests_source_part_id" in idx:
                batch.drop_index("ix_reading_tests_source_part_id")
            batch.drop_column("source_part_id")

    # --- (parent_test_id, practice_part_number) unique ---
    for table, name in UNIQUES.items():
        if _has_unique(table, name):
            continue
        _assert_no_duplicates(table)
        with op.batch_alter_table(table, recreate="auto") as batch:
            batch.create_unique_constraint(name, COLS)


def downgrade() -> None:
    for table, name in UNIQUES.items():
        if _has_unique(table, name):
            with op.batch_alter_table(table, recreate="auto") as batch:
                batch.drop_constraint(name, type_="unique")
    cols = set() if _offline() else {c["name"] for c in _insp().get_columns("reading_tests")}
    if "source_part_id" not in cols:
        with op.batch_alter_table("reading_tests", recreate="auto") as batch:
            batch.add_column(sa.Column("source_part_id", sa.Integer(), nullable=True))
            batch.create_index("ix_reading_tests_source_part_id", ["source_part_id"])
            batch.create_foreign_key("fk_reading_tests_source_part_id", "reading_parts", ["source_part_id"], ["id"], ondelete="CASCADE")
            batch.create_unique_constraint("uq_reading_mini_test_source_part", ["parent_test_id", "source_part_id"])
