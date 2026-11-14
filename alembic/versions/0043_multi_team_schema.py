"""Multi-team, stage 1: personal teams, many memberships, team-owned meetings (dark)

Stage 1 of docs/superpowers/specs/2026-11-13-multi-team-design.md. Nothing changes for anyone yet:
the app refuses a second membership while `MULTI_TEAM_ENABLED` is off, and the routes that name no
team keep landing on the team a person has today.

* `teams.is_personal`, with a partial unique index: at most one personal team per owner.
* `team_members`: `UNIQUE (team_id, user_id)` replaces the old `UNIQUE (user_id)` (one team per
  person), and an index on `user_id` takes over the lookups the old unique index served.
* `meetings.owner_team_id`: the team that owns a meeting moved to it. Null for every row and unused
  until stage 2; deleting the team nulls it (the meeting goes back to its recorder).

The backfill (the spec's rule):

* A team with exactly one member (its owner) and no pending invitation, integration, project,
  sprint or milestone is that person's personal team.
* A solo team that has any of those stays an ordinary team, its content where it is; its owner gets
  a separate, new personal team.
* A person who sits in a team with others lost their personal team when they joined it: they get a
  new one, so every live account ends with exactly one.
Nobody's membership is removed and no team's content moves, so the people who were in one team are
in the same team and one more (their empty personal team); the default-team rule in `app/teams.py`
keeps picking the team they had.

Downgrade is only defined while nobody has two memberships. It first deletes the personal teams
that are plainly empty (one member, no tasks, projects, sprints, milestones, series, integrations
or invitations) of people who have another membership: the ones this migration made. If any person
still belongs to two teams after that, it refuses with a message naming how many; nothing has been
changed (the whole downgrade runs in one transaction). The real roll-back for the application is to
revert the code and leave the schema.

Revision ID: 0043
Revises: 0042
Create Date: 2026-11-13
"""

import sqlalchemy as sa
from alembic import op

revision = "0043"
down_revision = "0042"
branch_labels = None
depends_on = None

# Marks the solo teams that are a person's personal space: one member (the owner) and nothing
# that makes it a working team (a pending invitation, an integration, a project, a sprint or a
# milestone; tasks do not count). One per owner at most (the lowest id), so the partial unique
# index cannot be hit by an owner of two such teams. Plain literal SQL: no value is composed in.
BACKFILL_PERSONAL = """
update teams set is_personal = true
where id in (
    select id from (
        select c.id,
               row_number() over (partition by c.owner_user_id order by c.id) as rn
        from teams c
        where (select count(*) from team_members m where m.team_id = c.id) = 1
          and exists (select 1 from team_members m
                      where m.team_id = c.id and m.user_id = c.owner_user_id)
          and not exists (select 1 from team_invites x where x.team_id = c.id
                          and x.accepted_at is null and x.revoked_at is null)
          and not exists (select 1 from integrations x where x.team_id = c.id)
          and not exists (select 1 from projects x where x.team_id = c.id)
          and not exists (select 1 from sprints x where x.team_id = c.id)
          and not exists (select 1 from milestones x where x.team_id = c.id)
    ) ranked
    where rn = 1
)
"""

# Everyone live who still has no personal team gets one (owner of it, its only member).
BACKFILL_NEW_PERSONAL = """
with needy as (
    select u.id from users u
    where u.status <> 'deleted'
      and not exists (select 1 from teams t where t.owner_user_id = u.id and t.is_personal)
), made as (
    insert into teams (owner_user_id, is_personal)
    select id, true from needy
    returning id, owner_user_id
)
insert into team_members (team_id, user_id, role)
select id, owner_user_id, 'owner' from made
"""

# The personal teams the downgrade may drop: empty, one member, and the owner is in another team.
_DROPPABLE_PERSONAL = """
delete from teams t
where t.is_personal
  and (select count(*) from team_members m where m.team_id = t.id) = 1
  and exists (select 1 from team_members m
              where m.user_id = t.owner_user_id and m.team_id <> t.id)
  and not exists (select 1 from team_invites x where x.team_id = t.id
                  and x.accepted_at is null and x.revoked_at is null)
  and not exists (select 1 from integrations x where x.team_id = t.id)
  and not exists (select 1 from projects x where x.team_id = t.id)
  and not exists (select 1 from sprints x where x.team_id = t.id)
  and not exists (select 1 from milestones x where x.team_id = t.id)
  and not exists (select 1 from tasks x where x.team_id = t.id)
  and not exists (select 1 from series x where x.team_id = t.id)
"""


def upgrade() -> None:
    op.add_column(
        "teams",
        sa.Column("is_personal", sa.Boolean, nullable=False, server_default=sa.false()),
    )
    # The new unique first (the old one implies it, so it cannot fail), then the old one goes.
    op.create_unique_constraint("uq_team_members_team_user", "team_members", ["team_id", "user_id"])
    op.drop_constraint("team_members_user_id_key", "team_members", type_="unique")
    op.create_index("ix_team_members_user_id", "team_members", ["user_id"])
    op.add_column(
        "meetings",
        sa.Column(
            "owner_team_id",
            sa.BigInteger,
            sa.ForeignKey("teams.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )
    op.create_index(
        "ix_meetings_owner_team_id",
        "meetings",
        ["owner_team_id"],
        postgresql_where=sa.text("owner_team_id is not null"),
    )
    op.execute(sa.text(BACKFILL_PERSONAL))
    op.create_index(
        "uq_teams_one_personal_per_owner",
        "teams",
        ["owner_user_id"],
        unique=True,
        postgresql_where=sa.text("is_personal"),
    )
    op.execute(sa.text(BACKFILL_NEW_PERSONAL))


def downgrade() -> None:
    bind = op.get_bind()
    bind.execute(sa.text(_DROPPABLE_PERSONAL))
    doubled = bind.execute(
        sa.text(
            "select count(*) from "
            "(select 1 from team_members group by user_id having count(*) > 1) d"
        )
    ).scalar()
    if doubled:
        raise RuntimeError(
            f"Cannot downgrade 0043: {doubled} account(s) still belong to more than one team "
            "(after dropping the empty personal teams). The old schema allows one team per person. "
            "Have those people leave all but one team, or keep the schema and revert the code "
            "(the schema is safe to leave: MULTI_TEAM_ENABLED off behaves as before)."
        )
    op.drop_index("uq_teams_one_personal_per_owner", table_name="teams")
    op.drop_index("ix_meetings_owner_team_id", table_name="meetings")
    op.drop_column("meetings", "owner_team_id")
    op.drop_index("ix_team_members_user_id", table_name="team_members")
    op.create_unique_constraint("team_members_user_id_key", "team_members", ["user_id"])
    op.drop_constraint("uq_team_members_team_user", "team_members", type_="unique")
    op.drop_column("teams", "is_personal")
