"""Teams: a team of one per account, and the column that can share a meeting

`teams`, `team_members` (one team per user: `user_id` is unique) and `meetings.shared_team_id`
(null = private, which is every existing row). Every existing, not-deleted account gets a team of
one with itself as owner. The backfill is plain SQL, writes only where a row is missing, and is
safe to run again; at the pilot's size it takes well under a second. Nothing is shared by this
migration: no meeting changes visibility.

Revision ID: 0019
Revises: 0018
Create Date: 2026-11-05
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import ARRAY

revision = "0019"
down_revision = "0018"
branch_labels = None
depends_on = None

# A team for every account that has none and owns none (so a half-finished earlier run is mended
# rather than doubled), then the owner's membership for every team that lacks one.
BACKFILL_TEAMS = """
insert into teams (name, owner_user_id, timezone)
select 'My team', u.id, 'UTC'
from users u
where u.status <> 'deleted'
  and not exists (select 1 from team_members m where m.user_id = u.id)
  and not exists (select 1 from teams t where t.owner_user_id = u.id)
"""
BACKFILL_MEMBERS = """
insert into team_members (team_id, user_id, role)
select t.id, t.owner_user_id, 'owner'
from teams t
join users u on u.id = t.owner_user_id and u.status <> 'deleted'
where not exists (select 1 from team_members m where m.user_id = t.owner_user_id)
"""


def upgrade() -> None:
    op.create_table(
        "teams",
        sa.Column("id", sa.BigInteger, primary_key=True),
        sa.Column("name", sa.String(200), nullable=False, server_default="My team"),
        sa.Column("owner_user_id", sa.BigInteger, sa.ForeignKey("users.id"), nullable=False),
        sa.Column("timezone", sa.String(64), nullable=False, server_default="UTC"),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
    )
    op.create_index("ix_teams_owner_user_id", "teams", ["owner_user_id"])
    op.create_table(
        "team_members",
        sa.Column("id", sa.BigInteger, primary_key=True),
        sa.Column(
            "team_id",
            sa.BigInteger,
            sa.ForeignKey("teams.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "user_id",
            sa.BigInteger,
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
            unique=True,
        ),
        sa.Column("role", sa.String(8), nullable=False, server_default="member"),
        sa.Column(
            "aliases", ARRAY(sa.Text), nullable=False, server_default=sa.text("'{}'::text[]")
        ),
        sa.Column(
            "joined_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.CheckConstraint("role in ('owner', 'admin', 'member')", name="ck_team_members_role"),
    )
    op.create_index("ix_team_members_team_id", "team_members", ["team_id"])
    op.add_column(
        "meetings",
        sa.Column(
            "shared_team_id",
            sa.BigInteger,
            sa.ForeignKey("teams.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )
    op.create_index(
        "ix_meetings_shared_team_id",
        "meetings",
        ["shared_team_id"],
        postgresql_where=sa.text("shared_team_id is not null"),
    )
    op.execute(BACKFILL_TEAMS)
    op.execute(BACKFILL_MEMBERS)


def downgrade() -> None:
    op.drop_index("ix_meetings_shared_team_id", table_name="meetings")
    op.drop_column("meetings", "shared_team_id")
    op.drop_table("team_members")
    op.drop_table("teams")
