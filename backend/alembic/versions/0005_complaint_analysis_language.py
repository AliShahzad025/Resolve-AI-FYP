"""Stage 5: store the detected language of each analysed complaint message."""
from alembic import op
import sqlalchemy as sa

revision = "0005_analysis_language"
down_revision = "0004_whatsapp_connection"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("complaint_analyses", sa.Column("language", sa.String(length=20), nullable=True))


def downgrade() -> None:
    op.drop_column("complaint_analyses", "language")
