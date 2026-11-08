"""A meeting remembers which bot provider ran it: `meetings.bot_provider`, and when the provider's
copy was deleted: `meetings.provider_copy_deleted_at`

A second bot provider (Skribby, `app/skribby.py`) sits behind the `BOT_PROVIDER` setting. The
setting says where NEW bots go; a meeting already in flight, and the cleanup of a finished one,
must keep talking to the provider that ran it, or flipping the setting would ask Vexa about a
Skribby bot (or delete a Skribby recording's handle at Vexa). `meetings.vexa_meeting_id` is text and
already holds either provider's id, so this one column is all the schema needs.

`provider_copy_deleted_at` is set when a hosted provider's copy of the meeting (recording,
transcript, the record of the bot) has been deleted at the provider, which the pipeline does as soon
as it has the audio and the speaker names (`pipeline.delete_fetched_copy`); null means "not yet",
and a retry job tries again. Vexa's copy is on our own server and keeps the retention rule, so it
stays null for Vexa rows.

Every existing row is `vexa` (the server default fills them in; adding a NOT NULL column with a
constant default is a metadata change on Postgres 11 and later, no table rewrite).

Revision ID: 0038
Revises: 0037
Create Date: 2026-11-09
"""

import sqlalchemy as sa
from alembic import op

revision = "0038"
down_revision = "0037"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "meetings",
        sa.Column("bot_provider", sa.String(32), nullable=False, server_default="vexa"),
    )
    op.add_column("meetings", sa.Column("provider_copy_deleted_at", sa.DateTime(timezone=True)))


def downgrade() -> None:
    op.drop_column("meetings", "provider_copy_deleted_at")
    op.drop_column("meetings", "bot_provider")
