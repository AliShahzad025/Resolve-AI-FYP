"""Stage 2 Shopify OAuth credentials and one-time authorization state."""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0003_shopify_order_sync"
down_revision = "0002_stage1_sessions"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "shopify_credentials",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("store_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("encrypted_token_bundle", sa.Text(), nullable=False),
        sa.Column("scopes", sa.Text(), nullable=False),
        sa.Column("webhooks_registered", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("webhook_error", sa.Text(), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["store_id"], ["stores.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("store_id"),
    )
    op.create_table(
        "shopify_oauth_states",
        sa.Column("state", sa.String(length=64), nullable=False),
        sa.Column("store_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("shop_domain", sa.String(length=255), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["store_id"], ["stores.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("state"),
    )
    op.create_index("ix_shopify_oauth_states_store_id", "shopify_oauth_states", ["store_id"])


def downgrade() -> None:
    op.drop_index("ix_shopify_oauth_states_store_id", table_name="shopify_oauth_states")
    op.drop_table("shopify_oauth_states")
    op.drop_table("shopify_credentials")
