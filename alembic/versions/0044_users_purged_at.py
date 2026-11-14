"""Multi-team, stage 2: an erased account's row can stay (a team keeps the meetings moved to it)

A meeting moved to a team belongs to the team, and survives its recorder deleting their account
(docs/superpowers/specs/2026-11-13-multi-team-design.md sections 6.4 and 9.1). `meetings.
owner_user_id` is a required foreign key to `users`, so the account's row cannot go while such a
meeting exists. The purge now erases the person from the row (name, email, password, Google id)
and keeps the hollow row, status `deleted`, and stamps `users.purged_at` so the worker does not
purge it again. The meeting shows its recorder as "Former member".

* `users.purged_at`: null for every row; set only on an erased account whose row was kept.
  Additive and nullable, so the old code ignores it.

* `ck_meetings_team_xor` on `meetings`: `owner_team_id is null or shared_team_id is null`. A meeting
  is private, shared with a team or owned by one, never owned by one team and shared with another
  (two tabs racing a share and a move could do that). True of every existing row: nothing has an
  `owner_team_id` yet.

Downgrade drops the constraint and the column. A hollow row then looks like a deleted account
waiting for its purge, which the old purge would try to finish: it deletes the meetings that point
at it, including the moved ones. Once anything has been moved, do not downgrade or revert (see
docs/sop/2026-11-14-multi-team-stage-2.md).

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
    op.create_check_constraint(
        "ck_meetings_team_xor", "meetings", "owner_team_id is null or shared_team_id is null"
    )


def downgrade() -> None:
    op.drop_constraint("ck_meetings_team_xor", "meetings", type_="check")
    op.drop_column("users", "purged_at")
