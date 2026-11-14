"""Multi-team, stage 2: an erased account's row can stay (a team keeps the meetings moved to it)

A meeting moved to a team belongs to the team, and survives its recorder deleting their account
(docs/superpowers/specs/2026-11-13-multi-team-design.md sections 6.4 and 9.1). `meetings.
owner_user_id` is a required foreign key to `users`, so the account's row cannot go while such a
meeting exists. The purge now erases the person from the row (name, email, password, Google id)
and keeps the hollow row, status `deleted`, and stamps `users.purged_at` so the worker does not
purge it again. The meeting shows its recorder as "Former member".

* `users.purged_at`: null for every row; set only on an erased account whose row was kept.
  Additive and nullable, so the old code ignores it.

Downgrade drops the column. A hollow row then looks like a deleted account waiting for its purge,
which the old purge would try to finish: it deletes the meetings that point at it, including the
moved ones. Revert the code before downgrading and expect that.

Revision ID: 0044
Revises: 0043
Create Date: 2026-11-14
"""

import sqlalchemy as sa
from alembic import op

revision = "0044"
down_revision = "0043"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("users", sa.Column("purged_at", sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    op.drop_column("users", "purged_at")
