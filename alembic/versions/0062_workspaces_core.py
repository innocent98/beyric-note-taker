"""Workspaces core: the columns, the per-workspace preferences table and the cutover backfill

Spec: docs/superpowers/specs/2026-11-24-workspaces-design.md (sections 3, 8 and 12). A team becomes
the container of everything. This migration adds the columns and fills them so that nothing
disappears and nothing becomes visible to anyone who could not see it before:

What each column or table is filled with:

- `meetings.workspace_team_id` (NOT NULL, FK teams, no ON DELETE action): `owner_team_id`, else
  `shared_team_id`, else the recorder's personal team (made when the recorder has none).
  `shared_team_id` and `owner_team_id` are NOT touched: who may read a meeting is exactly what it
  was.
- `notifications.team_id` (NOT NULL): the meeting's workspace, else the person's personal team.
- `templates.team_id` (nullable until slice W2 owns per-workspace templates) and
  `vocabulary_terms.team_id` (NOT NULL): the owner's personal team; and a COPY of the owner's live
  rows into the workspace their calendar connection moves to, when that is not their personal one
  (so notes are written the same way after the cutover).
- `calendar_connections.team_id` (NOT NULL): the person's default workspace: their only team, else
  their only ordinary team, else their personal one (`teams.default_membership`).
- `scheduled_joins.team_id` (NOT NULL): the workspace of the join's calendar connection, else the
  person's default workspace.
- `teams.kind` (`team` or `class`, default `team`): every team is `team`.
- `plans.max_owned_teams` (null = unlimited): `plan_20h` (Starter) 3, `plan_50h` (Pro) 10,
  `plan_100h` (Scale) null; no plan, which is Free, is 0 in code.
- table `workspace_prefs` (team, user): from `user_settings` and `users.bot_name`: one row for each
  person in their personal workspace, and one in the workspace their calendar connection moved to
  when that is another.

The templates and vocabulary uniqueness grows a `team_id` (`owner_user_id, team_id, name` and
`owner_user_id, team_id, term`) so that the copies can sit beside the originals; it is exactly the
old rule inside one workspace. Slice W2 decides whether a workspace's names are unique across its
members.

Downgrade drops the columns and the table. Copied templates and vocabulary that would break the old
per-owner uniqueness are taken out of use (a template: `deleted_at`, because a meeting may name it)
or removed (a vocabulary term), the lowest id (the original) kept. Per-workspace preferences,
workspace kinds and plan limits are lost; personal teams made for people who had none stay.

Revision ID: 0062
Revises: 0061
Create Date: 2026-11-24

Follows the recipes release (0061); the workspaces migrations are 0062, 0063 and 0064.
"""

import sqlalchemy as sa
from alembic import op

revision = "0062"
down_revision = "0061"
branch_labels = None
depends_on = None

AUTO_JOIN_RULES = ("all", "organizer", "none")
TEAM_KINDS = ("team", "class")


def _in(column: str, values: tuple[str, ...]) -> str:
    return f"{column} in ({', '.join(repr(v) for v in values)})"


def _default_workspace(bind, user_id: int) -> int | None:
    """`teams.default_membership`, in SQL terms: the only membership, else the only ordinary team,
    else the personal team, else the first membership; None for someone in no team."""
    rows = bind.execute(
        sa.text(
            "select tm.team_id, (t.is_personal and t.owner_user_id = :u) as own "
            "from team_members tm join teams t on t.id = tm.team_id "
            "where tm.user_id = :u order by tm.id"
        ),
        {"u": user_id},
    ).all()
    if not rows:
        return None
    if len(rows) == 1:
        return rows[0][0]
    ordinary = [team_id for team_id, own in rows if not own]
    if len(ordinary) == 1:
        return ordinary[0]
    own_teams = [team_id for team_id, own in rows if own]
    return own_teams[0] if own_teams else rows[0][0]


def upgrade() -> None:
    bind = op.get_bind()

    # ---- teams.kind, plans.max_owned_teams
    op.add_column("teams", sa.Column("kind", sa.String(8), server_default="team", nullable=False))
    op.create_check_constraint("ck_teams_kind", "teams", _in("kind", TEAM_KINDS))
    op.add_column("plans", sa.Column("max_owned_teams", sa.Integer(), nullable=True))
    op.execute("update plans set max_owned_teams = 3 where code = 'plan_20h'")
    op.execute("update plans set max_owned_teams = 10 where code = 'plan_50h'")
    # plan_100h (Scale) stays null: unlimited.

    # ---- a personal team for everyone who needs one (a flag-off join once dissolved some): every
    # person who can still sign in, and every account (a `deleted` one waiting for its purge, a
    # hollow row that still owns kept meetings, someone with no membership) that owns any row
    # that must now point at a workspace.
    op.execute(
        """
        insert into teams (owner_user_id, is_personal)
        select u.id, true from users u
        where not exists (select 1 from teams t where t.owner_user_id = u.id and t.is_personal)
          and (u.status <> 'deleted'
               or exists (select 1 from meetings x where x.owner_user_id = u.id)
               or exists (select 1 from templates x where x.owner_user_id = u.id)
               or exists (select 1 from vocabulary_terms x where x.owner_user_id = u.id)
               or exists (select 1 from notifications x where x.user_id = u.id)
               or exists (select 1 from calendar_connections x where x.user_id = u.id)
               or exists (select 1 from scheduled_joins x where x.user_id = u.id))
        """
    )
    op.execute(
        """
        insert into team_members (team_id, user_id, role)
        select t.id, t.owner_user_id, 'owner' from teams t
        where t.is_personal
          and not exists (select 1 from team_members tm
                          where tm.team_id = t.id and tm.user_id = t.owner_user_id)
        """
    )

    personal = (
        "(select p.id from teams p where p.owner_user_id = {owner} and p.is_personal "
        "order by p.id limit 1)"
    )

    # ---- meetings.workspace_team_id
    op.add_column("meetings", sa.Column("workspace_team_id", sa.BigInteger(), nullable=True))
    op.execute(
        "update meetings m set workspace_team_id = coalesce(m.owner_team_id, m.shared_team_id, "
        + personal.format(owner="m.owner_user_id")
        + ")"
    )
    unresolved = bind.execute(
        sa.text("select count(*) from meetings where workspace_team_id is null")
    ).scalar()
    if unresolved:
        raise RuntimeError(
            f"{unresolved} meetings have no owning team, no shared team and a recorder with no "
            "personal team: nothing is changed, fix the data first"
        )
    op.alter_column("meetings", "workspace_team_id", nullable=False)
    op.create_foreign_key(
        "fk_meetings_workspace_team_id", "meetings", "teams", ["workspace_team_id"], ["id"]
    )
    op.create_index("ix_meetings_workspace_team_id", "meetings", ["workspace_team_id", "id"])

    # ---- notifications.team_id
    op.add_column("notifications", sa.Column("team_id", sa.BigInteger(), nullable=True))
    op.execute(
        "update notifications n set team_id = coalesce("
        "(select m.workspace_team_id from meetings m where m.id = n.meeting_id), "
        + personal.format(owner="n.user_id")
        + ")"
    )
    op.execute(
        "update notifications n set team_id = (select tm.team_id from team_members tm "
        "where tm.user_id = n.user_id order by tm.id limit 1) where n.team_id is null"
    )
    op.alter_column("notifications", "team_id", nullable=False)
    op.create_foreign_key(
        "fk_notifications_team_id",
        "notifications",
        "teams",
        ["team_id"],
        ["id"],
        ondelete="CASCADE",
    )
    op.create_index("ix_notifications_team_user", "notifications", ["team_id", "user_id"])

    # ---- templates and vocabulary: per workspace
    op.drop_index("uq_templates_owner_name", table_name="templates")
    op.drop_constraint("uq_vocabulary_owner_term", "vocabulary_terms", type_="unique")
    for table, owner in (("templates", "owner_user_id"), ("vocabulary_terms", "owner_user_id")):
        op.add_column(table, sa.Column("team_id", sa.BigInteger(), nullable=True))
        op.execute(
            f"update {table} x set team_id = " + personal.format(owner=f"x.{owner}")  # noqa: S608
        )
        if table == "vocabulary_terms":  # templates stay nullable until W2 owns them
            op.alter_column(table, "team_id", nullable=False)
        op.create_foreign_key(
            f"fk_{table}_team_id", table, "teams", ["team_id"], ["id"], ondelete="CASCADE"
        )
        op.create_index(f"ix_{table}_team_id", table, ["team_id"])
    op.create_index(  # team_id may be null until W2: coalesce keeps two nulls clashing
        "uq_templates_owner_team_name",
        "templates",
        ["owner_user_id", sa.text("coalesce(team_id, 0)"), "name"],
        unique=True,
        postgresql_where=sa.text("deleted_at is null"),
    )
    op.create_unique_constraint(
        "uq_vocabulary_owner_team_term", "vocabulary_terms", ["owner_user_id", "team_id", "term"]
    )

    # ---- calendar connections move to the person's default workspace
    op.add_column("calendar_connections", sa.Column("team_id", sa.BigInteger(), nullable=True))
    moved = []  # (user_id, team_id) of connections whose workspace is not the personal one
    for conn_id, user_id in bind.execute(
        sa.text("select id, user_id from calendar_connections order by id")
    ).all():
        team_id = _default_workspace(bind, user_id)
        if team_id is None:
            raise RuntimeError(f"user {user_id} has a calendar connection and no team")
        bind.execute(
            sa.text("update calendar_connections set team_id = :t where id = :c"),
            {"t": team_id, "c": conn_id},
        )
        own = bind.execute(
            sa.text("select 1 from teams where id = :t and is_personal and owner_user_id = :u"),
            {"t": team_id, "u": user_id},
        ).first()
        if own is None:
            moved.append((user_id, team_id))
    op.alter_column("calendar_connections", "team_id", nullable=False)
    op.create_foreign_key(
        "fk_calendar_connections_team_id",
        "calendar_connections",
        "teams",
        ["team_id"],
        ["id"],
        ondelete="CASCADE",
    )
    op.create_index("ix_calendar_connections_team_id", "calendar_connections", ["team_id"])

    # ---- the copies: a person whose calendar moved to a workspace takes their notes templates and
    # vocabulary there too (the originals stay in their personal workspace).
    copies: dict[tuple[int, int], dict[int, int]] = {}  # (user, team) -> {old template id: copy id}
    for user_id, team_id in moved:
        mapping: dict[int, int] = {}
        rows = bind.execute(
            sa.text(
                "select id, name, instructions from templates "
                "where owner_user_id = :u and deleted_at is null order by id"
            ),
            {"u": user_id},
        ).all()
        for old_id, name, instructions in rows:
            new_id = bind.execute(
                sa.text(
                    "insert into templates (owner_user_id, team_id, name, instructions) "
                    "values (:u, :t, :n, :i) on conflict do nothing returning id"
                ),
                {"u": user_id, "t": team_id, "n": name, "i": instructions},
            ).scalar()
            if new_id is None:  # already copied (two connections of one person in one workspace)
                new_id = bind.execute(
                    sa.text(
                        "select id from templates where owner_user_id = :u and team_id = :t "
                        "and name = :n and deleted_at is null"
                    ),
                    {"u": user_id, "t": team_id, "n": name},
                ).scalar()
            mapping[old_id] = new_id
        copies[(user_id, team_id)] = mapping
        bind.execute(
            sa.text(
                "insert into vocabulary_terms (owner_user_id, team_id, term) "
                "select owner_user_id, :t, term from vocabulary_terms "
                "where owner_user_id = :u and team_id <> :t order by id "
                "on conflict do nothing"
            ),
            {"u": user_id, "t": team_id},
        )

    # ---- scheduled joins: the connection's workspace, else the person's default workspace
    op.add_column("scheduled_joins", sa.Column("team_id", sa.BigInteger(), nullable=True))
    op.execute(
        "update scheduled_joins sj set team_id = cc.team_id from calendar_events ce, "
        "calendar_connections cc where sj.calendar_event_id = ce.id and ce.connection_id = cc.id"
    )
    for join_id, user_id in bind.execute(
        sa.text("select id, user_id from scheduled_joins where team_id is null order by id")
    ).all():
        team_id = bind.execute(
            sa.text(
                "select team_id from calendar_connections where user_id = :u order by id limit 1"
            ),
            {"u": user_id},
        ).scalar()
        team_id = team_id or _default_workspace(bind, user_id)
        bind.execute(
            sa.text("update scheduled_joins set team_id = :t where id = :j"),
            {"t": team_id, "j": join_id},
        )
    op.alter_column("scheduled_joins", "team_id", nullable=False)
    op.create_foreign_key(
        "fk_scheduled_joins_team_id",
        "scheduled_joins",
        "teams",
        ["team_id"],
        ["id"],
        ondelete="CASCADE",
    )
    op.create_index("ix_scheduled_joins_team_id", "scheduled_joins", ["team_id"])

    # ---- workspace_prefs, from the person's settings
    op.create_table(
        "workspace_prefs",
        sa.Column("team_id", sa.BigInteger(), sa.ForeignKey("teams.id", ondelete="CASCADE")),
        sa.Column("user_id", sa.BigInteger(), sa.ForeignKey("users.id", ondelete="CASCADE")),
        sa.Column("bot_name", sa.String(50), nullable=True),
        sa.Column("auto_join_rule", sa.String(16), server_default="all", nullable=False),
        sa.Column("join_unanswered", sa.Boolean(), server_default=sa.false(), nullable=False),
        sa.Column("skip_one_on_ones", sa.Boolean(), server_default=sa.false(), nullable=False),
        sa.Column(
            "calendar_template_id",
            sa.Integer(),
            sa.ForeignKey("templates.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.PrimaryKeyConstraint("team_id", "user_id", name="pk_workspace_prefs"),
        sa.CheckConstraint(_in("auto_join_rule", AUTO_JOIN_RULES), name="ck_workspace_prefs_rule"),
    )
    op.create_index("ix_workspace_prefs_user_id", "workspace_prefs", ["user_id"])
    settings = bind.execute(
        sa.text(
            "select us.user_id, u.bot_name, us.auto_join_rule, us.join_unanswered, "
            "us.skip_one_on_ones, us.calendar_template_id, "
            + personal.format(owner="us.user_id")
            + " from user_settings us join users u on u.id = us.user_id order by us.user_id"
        )
    ).all()
    insert = sa.text(
        "insert into workspace_prefs (team_id, user_id, bot_name, auto_join_rule, join_unanswered, "
        "skip_one_on_ones, calendar_template_id) values (:t, :u, :b, :r, :j, :s, :c) "
        "on conflict do nothing"
    )
    for user_id, bot_name, rule, unanswered, skip, template_id, personal_id in settings:
        row = {"u": user_id, "b": bot_name, "r": rule, "j": unanswered, "s": skip}
        if personal_id is not None:
            bind.execute(insert, {**row, "t": personal_id, "c": template_id})
        for conn_user, team_id in moved:
            if conn_user != user_id:
                continue
            mapped = copies.get((user_id, team_id), {}).get(template_id)
            bind.execute(insert, {**row, "t": team_id, "c": mapped})


def downgrade() -> None:
    op.drop_table("workspace_prefs")

    # The copies would break the old per-owner uniqueness: the original (lowest id) stays live.
    op.execute(
        """
        update templates a set deleted_at = now()
        where a.deleted_at is null and exists (
            select 1 from templates b where b.owner_user_id = a.owner_user_id and b.name = a.name
              and b.deleted_at is null and b.id < a.id)
        """
    )
    op.execute(
        """
        delete from vocabulary_terms a using vocabulary_terms b
        where a.owner_user_id = b.owner_user_id and a.term = b.term and a.id > b.id
        """
    )
    op.drop_constraint("uq_vocabulary_owner_team_term", "vocabulary_terms", type_="unique")
    op.drop_index("uq_templates_owner_team_name", table_name="templates")
    op.create_unique_constraint(
        "uq_vocabulary_owner_term", "vocabulary_terms", ["owner_user_id", "term"]
    )
    op.create_index(
        "uq_templates_owner_name",
        "templates",
        ["owner_user_id", "name"],
        unique=True,
        postgresql_where=sa.text("deleted_at is null"),
    )
    for table in ("vocabulary_terms", "templates"):
        op.drop_index(f"ix_{table}_team_id", table_name=table)
        op.drop_constraint(f"fk_{table}_team_id", table, type_="foreignkey")
        op.drop_column(table, "team_id")

    op.drop_index("ix_scheduled_joins_team_id", table_name="scheduled_joins")
    op.drop_constraint("fk_scheduled_joins_team_id", "scheduled_joins", type_="foreignkey")
    op.drop_column("scheduled_joins", "team_id")
    op.drop_index("ix_calendar_connections_team_id", table_name="calendar_connections")
    op.drop_constraint(
        "fk_calendar_connections_team_id", "calendar_connections", type_="foreignkey"
    )
    op.drop_column("calendar_connections", "team_id")
    op.drop_index("ix_notifications_team_user", table_name="notifications")
    op.drop_constraint("fk_notifications_team_id", "notifications", type_="foreignkey")
    op.drop_column("notifications", "team_id")
    op.drop_index("ix_meetings_workspace_team_id", table_name="meetings")
    op.drop_constraint("fk_meetings_workspace_team_id", "meetings", type_="foreignkey")
    op.drop_column("meetings", "workspace_team_id")

    op.drop_column("plans", "max_owned_teams")
    op.drop_constraint("ck_teams_kind", "teams", type_="check")
    op.drop_column("teams", "kind")
