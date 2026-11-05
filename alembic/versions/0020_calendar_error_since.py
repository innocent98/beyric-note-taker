"""When a calendar connection's run of failed syncs began

`calendar_connections.error_since`: set on the first failed sync after a good one, cleared by the
next good sync. A connection whose failures are 30 minutes old is `degraded` (C13). Null for every
existing row, which is right: none is failing as this ships.

Revision ID: 0020
Revises: 0018
Create Date: 2026-11-05
"""

import sqlalchemy as sa
from alembic import op

revision = "0020"
down_revision = "0018"  # 0019 (W0.4, PR #84) is not merged yet: renumber the chain when it lands
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "calendar_connections", sa.Column("error_since", sa.DateTime(timezone=True), nullable=True)
    )


def downgrade() -> None:
    op.drop_column("calendar_connections", "error_since")
