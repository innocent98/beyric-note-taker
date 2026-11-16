"""Remove a recurring meeting: the calendar events a person removed from one scope

`series_ignored` remembers that the recurring calendar event `calendar_recurring_id` was removed
from the scope of `team_id` (an ordinary team, or one person's personal team), so the calendar sync
does not make its row again and later meetings of that event are not regrouped there. One row per
scope and event (`uq_series_ignored`). It holds the event's id and who removed it, nothing else.
It goes with its team (`ON DELETE CASCADE`); `created_by` goes null with the account.

Additive: a new table nothing from before reads, so the code from before this migration ignores it.

Downgrade drops the table (the marks are lost, and a removed recurring meeting may come back with
the next calendar sync; revert the code first).

Revision ID: 0048
Revises: 0047
Create Date: 2026-11-15
"""

import sqlalchemy as sa
from alembic import op

revision = "0048"
down_revision = "0047"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "series_ignored",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column(
            "team_id",
            sa.BigInteger(),
            sa.ForeignKey("teams.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("calendar_recurring_id", sa.String(1024), nullable=False),
        sa.Column(
            "created_by",
            sa.BigInteger(),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.UniqueConstraint("team_id", "calendar_recurring_id", name="uq_series_ignored"),
    )


def downgrade() -> None:
    op.drop_table("series_ignored")
