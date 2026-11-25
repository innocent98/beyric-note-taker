"""A meeting says whether the person's own auto-share rule shared it

`meetings.shared_by_auto_rule` (default false): true while `shared_team_id` was set by the
recorder's "Share my new meetings with" setting (`teams.apply_share_default`) and by nothing else.
The meeting page uses it to say "Shared with <team> by your auto-share rule", because people read
"Personal" as private. It changes nobody's access: `app/visibility.py` never reads it.

Nothing is backfilled: the database never recorded why an existing meeting was shared, so every
existing row is false and shows no label (the label is only ever shown when it is certain). A
meeting shared by the rule from now on carries it until the recorder shares, unshares or moves it.

Downgrade drops the column; nothing else depends on it.

Revision ID: 0066
Revises: 0065
"""

import sqlalchemy as sa
from alembic import op

revision = "0066"
down_revision = "0065"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "meetings",
        sa.Column("shared_by_auto_rule", sa.Boolean(), nullable=False, server_default=sa.false()),
    )


def downgrade() -> None:
    op.drop_column("meetings", "shared_by_auto_rule")
