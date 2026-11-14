"""Multi-team, stage 2: one auto-share team per person (dark)

`user_settings.auto_share_team_id`: the team new meetings of this person are shared to when their
notes are first written (docs/superpowers/specs/2026-11-13-multi-team-design.md 6.3). Null for every
row: nobody has chosen one, and the team default that exists today (`teams.share_new_meetings`, for
the owner and admins of the team) keeps applying exactly as before. Additive and nullable, so the
old code ignores it. Deleting the team clears it (`ON DELETE SET NULL`).

Revision ID: 0045
Revises: 0044
Create Date: 2026-11-14
"""

import sqlalchemy as sa
from alembic import op

revision = "0045"
down_revision = "0044"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "user_settings",
        sa.Column(
            "auto_share_team_id",
            sa.BigInteger(),
            sa.ForeignKey("teams.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )
    # Deleting a team nulls this column: the partial index keeps that from scanning every row.
    op.create_index(
        "ix_user_settings_auto_share_team_id",
        "user_settings",
        ["auto_share_team_id"],
        postgresql_where=sa.text("auto_share_team_id is not null"),
    )


def downgrade() -> None:
    op.drop_index("ix_user_settings_auto_share_team_id", table_name="user_settings")
    op.drop_column("user_settings", "auto_share_team_id")
