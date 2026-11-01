"""Speaking AI baholash jadvallari (kredit, job, baholash).

Revision ID: 0020_speaking_ai
Revises: 0019_plus_includes_speaking
"""
from alembic import op
import sqlalchemy as sa

revision = "0020_speaking_ai"
down_revision = "0019_plus_includes_speaking"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "speaking_ai_credits",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("user_id", sa.Uuid(as_uuid=True, native_uuid=True),
                  sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("credits_remaining", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_speaking_ai_credits_user_id", "speaking_ai_credits", ["user_id"])

    op.create_table(
        "speaking_ai_jobs",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("practice_attempt_id", sa.Integer(),
                  sa.ForeignKey("practice_attempts.id", ondelete="CASCADE"), nullable=False, unique=True),
        sa.Column("user_id", sa.Uuid(as_uuid=True, native_uuid=True),
                  sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("credit_id", sa.Integer(),
                  sa.ForeignKey("speaking_ai_credits.id", ondelete="SET NULL"), nullable=True),
        sa.Column("status", sa.String(16), nullable=False, server_default="queued"),
        sa.Column("error", sa.String(500), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_speaking_ai_jobs_user_id", "speaking_ai_jobs", ["user_id"])
    op.create_index("ix_speaking_ai_jobs_status", "speaking_ai_jobs", ["status"])

    op.create_table(
        "speaking_ai_evaluations",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("job_id", sa.Integer(),
                  sa.ForeignKey("speaking_ai_jobs.id", ondelete="CASCADE"), nullable=False),
        sa.Column("part", sa.String(8), nullable=False),
        sa.Column("score", sa.Integer(), nullable=False),
        sa.Column("max_score", sa.Integer(), nullable=False),
        sa.Column("level", sa.String(32), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False, server_default="{}"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("job_id", "part", name="uq_speaking_ai_job_part"),
    )
    op.create_index("ix_speaking_ai_evaluations_job_id", "speaking_ai_evaluations", ["job_id"])


def downgrade() -> None:
    op.drop_index("ix_speaking_ai_evaluations_job_id", table_name="speaking_ai_evaluations")
    op.drop_table("speaking_ai_evaluations")
    op.drop_index("ix_speaking_ai_jobs_status", table_name="speaking_ai_jobs")
    op.drop_index("ix_speaking_ai_jobs_user_id", table_name="speaking_ai_jobs")
    op.drop_table("speaking_ai_jobs")
    op.drop_index("ix_speaking_ai_credits_user_id", table_name="speaking_ai_credits")
    op.drop_table("speaking_ai_credits")
