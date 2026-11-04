"""Auto-join: when the runner first led, and when its switch was turned off

The join runner closes, without a notice, the joins that were due before it ever ran (a meeting
that started before the feature existed is not joined retroactively), and, when the operator's
switch (`AUTOJOIN_ENABLED`) is turned back on, the joins whose 15 minutes passed while it was off.
Two nullable timestamps on the single `app_state` row hold those two facts. Additive: an existing
row has both unset, which is exactly "the runner has never led".

Revision ID: 0016
Revises: 0015
Create Date: 2026-11-04
"""

import sqlalchemy as sa
from alembic import op

revision = "0016"
down_revision = "0015"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "app_state", sa.Column("autojoin_started_at", sa.DateTime(timezone=True), nullable=True)
    )
    op.add_column(
        "app_state", sa.Column("autojoin_paused_since", sa.DateTime(timezone=True), nullable=True)
    )


def downgrade() -> None:
    op.drop_column("app_state", "autojoin_paused_since")
    op.drop_column("app_state", "autojoin_started_at")
