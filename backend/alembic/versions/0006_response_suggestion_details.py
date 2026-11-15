"""Stage 5: link each drafted reply to the customer message it answers and keep its retrieval evidence."""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0006_suggestion_details"
down_revision = "0005_analysis_language"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("response_suggestions", sa.Column("message_id", postgresql.UUID(as_uuid=True), nullable=True))
    op.add_column("response_suggestions", sa.Column("supported", sa.Boolean(), server_default=sa.text("true"), nullable=False))
    op.add_column("response_suggestions", sa.Column("note", sa.Text(), nullable=True))
    # Every retrieved passage with its similarity score: [{"chunk_id": "...", "score": 0.87}, ...]
    op.add_column("response_suggestions", sa.Column("retrieval", postgresql.JSONB(), server_default=sa.text("'[]'::jsonb"), nullable=False))
    op.create_foreign_key("fk_suggestion_message", "response_suggestions", "messages", ["message_id"], ["id"], ondelete="RESTRICT")


def downgrade() -> None:
    op.drop_constraint("fk_suggestion_message", "response_suggestions", type_="foreignkey")
    op.drop_column("response_suggestions", "retrieval")
    op.drop_column("response_suggestions", "note")
    op.drop_column("response_suggestions", "supported")
    op.drop_column("response_suggestions", "message_id")
