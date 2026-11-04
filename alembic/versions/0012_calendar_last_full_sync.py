"""Calendar sync: when the last full listing ran

The sync loop reads only changes between full listings (a sync token cannot be combined with a
time window), so it needs to know when the window was last listed in full: a full resync is due
every 6 hours, which also picks up recurring instances that came into range. Additive and
nullable: an existing connection has never been marked, so its next sync is a full one.

Revision ID: 0012
Revises: 0011
Create Date: 2026-11-04
"""

import sqlalchemy as sa
from alembic import op

revision = "0012"
down_revision = "0011"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "calendar_connections",
        sa.Column("last_full_sync_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("calendar_connections", "last_full_sync_at")
