"""Meeting recipes: `documents.ledger_run_id`

The `ai_runs` row written when a draft is asked for (`pending` until the worker settles it), kept on
the draft so the worker can settle exactly that row. No foreign key: the ledger outlives the draft
(a discarded draft's call still cost). Nullable, empty for every row that exists.

Downgrade drops the column.

Revision ID: 0060
Revises: 0059
Create Date: 2026-11-24
"""

import sqlalchemy as sa
from alembic import op

revision = "0060"
down_revision = "0059"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("documents", sa.Column("ledger_run_id", sa.BigInteger()))


def downgrade() -> None:
    op.drop_column("documents", "ledger_run_id")
