"""Baseline for the user's existing Resolve AI PostgreSQL schema.

Before upgrading, stamp this revision on the database where the supplied schema
has already been created. It intentionally does not recreate or alter existing
business tables.
"""

revision = "0001_existing_schema"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
