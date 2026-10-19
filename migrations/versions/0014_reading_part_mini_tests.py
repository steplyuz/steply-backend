"""Add standalone Reading part mini-test metadata."""
from alembic import op
import sqlalchemy as sa

revision = "0014_reading_part_mini_tests"
down_revision = "0013_reading_part_images"
branch_labels = None
depends_on = None

def upgrade() -> None:
    # Batch mode supports SQLite deployments as well as databases with ALTER TABLE constraints.
    with op.batch_alter_table("reading_tests", recreate="auto") as batch_op:
        batch_op.add_column(sa.Column("parent_test_id", sa.String(length=64), nullable=True))
        batch_op.add_column(sa.Column("source_part_id", sa.Integer(), nullable=True))
        batch_op.add_column(sa.Column("practice_part_number", sa.Integer(), nullable=True))
        batch_op.create_index("ix_reading_tests_parent_test_id", ["parent_test_id"])
        batch_op.create_index("ix_reading_tests_source_part_id", ["source_part_id"])
        batch_op.create_foreign_key("fk_reading_tests_parent_test_id", "reading_tests", ["parent_test_id"], ["id"], ondelete="SET NULL")
        batch_op.create_foreign_key("fk_reading_tests_source_part_id", "reading_parts", ["source_part_id"], ["id"], ondelete="CASCADE")
        batch_op.create_unique_constraint("uq_reading_mini_test_source_part", ["parent_test_id", "source_part_id"])

def downgrade() -> None:
    with op.batch_alter_table("reading_tests", recreate="auto") as batch_op:
        batch_op.drop_constraint("uq_reading_mini_test_source_part", type_="unique")
        batch_op.drop_constraint("fk_reading_tests_source_part_id", type_="foreignkey")
        batch_op.drop_constraint("fk_reading_tests_parent_test_id", type_="foreignkey")
        batch_op.drop_index("ix_reading_tests_source_part_id")
        batch_op.drop_index("ix_reading_tests_parent_test_id")
        batch_op.drop_column("practice_part_number")
        batch_op.drop_column("source_part_id")
        batch_op.drop_column("parent_test_id")
