"""Listening paper layout: part image, example block, question group title.

Revision ID: 0018_listening_paper_layout
Revises: 0017_listening_part_mini_tests
"""
from alembic import op
import sqlalchemy as sa

revision = "0018_listening_paper_layout"
down_revision = "0017_listening_part_mini_tests"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("listening_parts") as batch_op:
        batch_op.add_column(sa.Column("image_url", sa.String(length=500), nullable=True))
        batch_op.add_column(sa.Column("example_json", sa.Text(), nullable=True))
    with op.batch_alter_table("listening_questions") as batch_op:
        batch_op.add_column(sa.Column("group_title", sa.String(length=200), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("listening_questions") as batch_op:
        batch_op.drop_column("group_title")
    with op.batch_alter_table("listening_parts") as batch_op:
        batch_op.drop_column("example_json")
        batch_op.drop_column("image_url")