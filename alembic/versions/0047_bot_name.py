"""A per-user note taker name, and the name each meeting's bot was sent with

* `users.bot_name`: the name the person chose for the note taker (Settings, "Note taker name");
  null = the server's default (`Settings.skribby_bot_name`). Validated by `app/bot_names.py`.
* `meetings.bot_name`: the name actually sent with this meeting's bot when it was created, so a
  later step (naming speakers by elimination, the owner's "Admit '...'" line, the chat notice) knows
  which name this bot joined with even after the person changes the setting. Null on every row made
  before this migration and on meetings of a provider that takes no name: those use the server
  default, exactly as before.

Both are additive and nullable, so the code from before this migration ignores them.

Downgrade drops both columns (the stored names are lost; revert the code first).

Revision ID: 0047
Revises: 0046
Create Date: 2026-11-14
"""

import sqlalchemy as sa
from alembic import op

revision = "0047"
down_revision = "0046"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("users", sa.Column("bot_name", sa.String(50), nullable=True))
    op.add_column("meetings", sa.Column("bot_name", sa.String(50), nullable=True))


def downgrade() -> None:
    op.drop_column("meetings", "bot_name")
    op.drop_column("users", "bot_name")
