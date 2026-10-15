"""Stage 1 session tracking and unconnected store setup."""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0002_stage1_sessions"
down_revision = "0001_existing_schema"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # A new store can be configured before Shopify OAuth is implemented.
    op.alter_column("stores", "shopify_domain", existing_type=sa.String(length=255), nullable=True)
    op.alter_column("stores", "shopify_token_ref", existing_type=sa.String(length=255), nullable=True)
    op.create_table(
        "auth_sessions",
        sa.Column("jti", sa.String(length=36), primary_key=True, nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("store_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["store_id"], ["stores.id"], ondelete="RESTRICT"),
    )
    op.create_index("ix_auth_sessions_user_id", "auth_sessions", ["user_id"])
    op.create_index("ix_auth_sessions_store_id", "auth_sessions", ["store_id"])


def downgrade() -> None:
    op.drop_index("ix_auth_sessions_store_id", table_name="auth_sessions")
    op.drop_index("ix_auth_sessions_user_id", table_name="auth_sessions")
    op.drop_table("auth_sessions")
    # Do not restore NOT NULL: new stores without Shopify are valid Stage 1 data.
