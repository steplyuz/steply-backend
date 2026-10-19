"""Add per-part segment timestamps for single-file Listening audio."""
from alembic import op
import sqlalchemy as sa

revision = "0012_listening_audio_segments"
down_revision = "0011_writing_ai_credits"
branch_labels = None
depends_on = None

def upgrade() -> None:
    op.add_column("listening_parts", sa.Column("audio_start_seconds", sa.Float(), nullable=True))
    op.add_column("listening_parts", sa.Column("audio_end_seconds", sa.Float(), nullable=True))

def downgrade() -> None:
    op.drop_column("listening_parts", "audio_end_seconds")
    op.drop_column("listening_parts", "audio_start_seconds")
