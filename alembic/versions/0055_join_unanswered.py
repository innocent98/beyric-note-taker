"""Settings: "Also join invites I haven't answered yet" (`user_settings.join_unanswered`)

Under the auto-join rule `all`, `will_join` skips an invite the person has not answered from
someone outside their organisation (reason `not_accepted`): every user's bot shares one Google
account, so a stranger's invite with a meeting link must not send it anywhere. A person who wants
those joined too can now say so. The column is per person, NOT NULL, and FALSE for everyone
already in the table and for every new row, so nobody's protection changes until they tick the box.

Downgrade drops the column; people who ticked the box lose that choice (the rule falls back to the
protected behaviour, which is the safe direction).

Revision ID: 0055
Revises: 0054
Create Date: 2026-11-21
"""

import sqlalchemy as sa
from alembic import op

revision = "0055"
down_revision = "0054"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "user_settings",
        sa.Column("join_unanswered", sa.Boolean(), server_default=sa.false(), nullable=False),
    )


def downgrade() -> None:
    op.drop_column("user_settings", "join_unanswered")
