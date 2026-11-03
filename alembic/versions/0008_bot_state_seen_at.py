"""when the worker last saw the bot's live state

The state itself lives in `meetings.completion_reason` while a meeting is `bot_sent`; this is the
time of the poll that wrote it, so the page can stop trusting a state the worker has not renewed.

Revision ID: 0008
Revises: 0007
Create Date: 2026-11-03
"""

import sqlalchemy as sa
from alembic import op

revision = "0008"
down_revision = "0007"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("meetings", sa.Column("bot_state_seen_at", sa.DateTime(timezone=True)))


def downgrade() -> None:
    op.drop_column("meetings", "bot_state_seen_at")
