"""Settings: "Skip one-on-one meetings" (`user_settings.skip_one_on_ones`)

When a person ticks the box, `will_join` does not send the bot to an event whose stored
`attendee_count` is exactly 2 (the reason `one_on_one`): the count is the people INVITED, the
person included, not who attends. The column is per person, NOT NULL, and FALSE for everyone
already in the table and for every new row, so nothing changes for anyone until they tick the box.

Downgrade drops the column; people who ticked the box lose that choice and their one-on-ones are
joined again by the rule they chose (the box only ever narrowed the rule).

Revision ID: 0056
Revises: 0055
Create Date: 2026-11-24
"""

import sqlalchemy as sa
from alembic import op

revision = "0056"
down_revision = "0055"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "user_settings",
        sa.Column("skip_one_on_ones", sa.Boolean(), server_default=sa.false(), nullable=False),
    )


def downgrade() -> None:
    op.drop_column("user_settings", "skip_one_on_ones")
