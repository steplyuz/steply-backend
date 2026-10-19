"""Add optional image to Reading parts."""
from alembic import op
import sqlalchemy as sa

revision = "0013_reading_part_images"
down_revision = "0012_listening_audio_segments"
branch_labels = None
depends_on = None

def upgrade() -> None:
    op.add_column("reading_parts", sa.Column("image_url", sa.String(length=500), nullable=True))

def downgrade() -> None:
    op.drop_column("reading_parts", "image_url")
