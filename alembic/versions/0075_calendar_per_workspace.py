"""Calendar per workspace: a connection is unique per (person, workspace, provider)

Spec: docs/superpowers/specs/2026-11-24-workspaces-design.md (section 4 and 12.4). Migration 0070
gave `calendar_connections` and `scheduled_joins` their `team_id`; this one lets a person hold a
Google or Outlook connection in several workspaces (even the same account) and lets an OAuth round
trip remember the workspace that started it.

- `calendar_connections`: the unique `(user_id, provider)` becomes `(user_id, team_id, provider)`.
  Nothing is rewritten: every existing row is one per person already.
- `oauth_flows.team_id` (nullable, FK teams, ON DELETE CASCADE): the workspace a calendar connect
  was started from. Null for a flow begun before this migration (it lives ten minutes) and for
  every sign-in flow; the callback then files the connection in the person's default workspace,
  as the cutover did.

Downgrade restores the old uniqueness: where a person holds the same provider in several
workspaces, the OLDEST connection (lowest id, the one they had before) is kept and the others are
deleted, with their events and the scheduled joins of those events. The workspace a connect flow was
started from is lost.

Revision ID: 0075
Revises: 0071
"""

import sqlalchemy as sa
from alembic import op

revision = "0075"
down_revision = "0071"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_constraint(
        "uq_calendar_connections_user_provider", "calendar_connections", type_="unique"
    )
    op.create_unique_constraint(
        "uq_calendar_connections_user_team_provider",
        "calendar_connections",
        ["user_id", "team_id", "provider"],
    )
    op.add_column("oauth_flows", sa.Column("team_id", sa.BigInteger(), nullable=True))
    op.create_foreign_key(
        "fk_oauth_flows_team_id", "oauth_flows", "teams", ["team_id"], ["id"], ondelete="CASCADE"
    )


def downgrade() -> None:
    op.drop_constraint("fk_oauth_flows_team_id", "oauth_flows", type_="foreignkey")
    op.drop_column("oauth_flows", "team_id")
    # The extra connections go with their events (cascade) and the scheduled joins of those events
    # (a join would otherwise outlive its calendar with no event to follow).
    op.execute(
        """
        delete from scheduled_joins j
        using calendar_events e, calendar_connections c
        where j.calendar_event_id = e.id and e.connection_id = c.id
          and exists (
            select 1 from calendar_connections o
            where o.user_id = c.user_id and o.provider = c.provider and o.id < c.id
          )
        """
    )
    op.execute(
        """
        delete from calendar_connections c
        where exists (
            select 1 from calendar_connections o
            where o.user_id = c.user_id and o.provider = c.provider and o.id < c.id
        )
        """
    )
    op.create_unique_constraint(
        "uq_calendar_connections_user_provider", "calendar_connections", ["user_id", "provider"]
    )
    op.drop_constraint(
        "uq_calendar_connections_user_team_provider", "calendar_connections", type_="unique"
    )
