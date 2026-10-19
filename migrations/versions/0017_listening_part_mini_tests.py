"""Add standalone Listening part mini-test metadata.

Revision ID: 0017_listening_part_mini_tests
Revises: 0016_teacher_review_credits

Reading'dan farqli o'laroq source_part_id FK yo'q: Listening update_exam()
partlarni o'chirib qayta yaratadi, shuning uchun idempotentlik
(parent_test_id, practice_part_number) juftligi bilan ta'minlanadi.
"""
from alembic import op
import sqlalchemy as sa

revision = "0017_listening_part_mini_tests"
down_revision = "0016_teacher_review_credits"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("listening_tests", recreate="auto") as batch_op:
        batch_op.add_column(sa.Column("parent_test_id", sa.String(length=64), nullable=True))
        batch_op.add_column(sa.Column("practice_part_number", sa.Integer(), nullable=True))
        batch_op.create_index("ix_listening_tests_parent_test_id", ["parent_test_id"])
        batch_op.create_foreign_key(
            "fk_listening_tests_parent_test_id", "listening_tests", ["parent_test_id"], ["id"], ondelete="SET NULL"
        )
        batch_op.create_unique_constraint(
            "uq_listening_mini_test_part_number", ["parent_test_id", "practice_part_number"]
        )


def downgrade() -> None:
    with op.batch_alter_table("listening_tests", recreate="auto") as batch_op:
        batch_op.drop_constraint("uq_listening_mini_test_part_number", type_="unique")
        batch_op.drop_constraint("fk_listening_tests_parent_test_id", type_="foreignkey")
        batch_op.drop_index("ix_listening_tests_parent_test_id")
        batch_op.drop_column("practice_part_number")
        batch_op.drop_column("parent_test_id")
