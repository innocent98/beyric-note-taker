"""The recording-consent box: when each person confirmed it, and which wording

Sign-up now asks the person to confirm they will tell the people in their meetings that Beyric
records them (`signup.RECORDING_ACK_VERSION` names the wording). The evidence is kept the way the
terms are: a time and a version on the account.

* `users.recording_ack_at`: null until the box was ticked at sign-up, or the one-time card on the
  Meetings page was clicked (accounts older than the box). Nothing reads it to allow or refuse a
  meeting, the bot or auto-join.
* `users.recording_ack_version`: the wording that was accepted.

Both are additive and nullable, so the code from before this migration ignores them. Existing rows
stay null: they get the card, they are never blocked.

Downgrade drops both columns (the evidence is lost; revert the code first).

Revision ID: 0046
Revises: 0043
Create Date: 2026-11-14
"""

import sqlalchemy as sa
from alembic import op

revision = "0046"
# 0044 and 0045 are taken by the open multi-team PRs (#182, #184). This points at 0043 so the
# branch has one head on its own; once those merge, the lead (or the author) changes this one line
# to "0045" so main keeps a single head (tests/test_migrations.py::test_single_head says if not).
down_revision = "0043"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("users", sa.Column("recording_ack_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("users", sa.Column("recording_ack_version", sa.String(32), nullable=True))


def downgrade() -> None:
    op.drop_column("users", "recording_ack_version")
    op.drop_column("users", "recording_ack_at")
