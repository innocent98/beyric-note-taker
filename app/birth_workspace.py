"""The workspace a row is born in, as column defaults (workspaces design, section 3).

Every meeting, notification, template, vocabulary term, calendar connection and scheduled join
belongs to exactly one workspace (a team) from the moment it is inserted. The code that knows the
answer (the route's active workspace, the connection a join came from) sets the column itself;
these defaults are the net under it, so a row made by a path that does not know, or by a test, never
lands with no workspace:

* a meeting: the team that owns it, else the one it is shared to, else its recorder's personal team;
* a notification: its meeting's workspace, else the person's personal team;
* a template or vocabulary term: its owner's personal team;
* a calendar connection or join: the connection's own workspace for a join with an event, else the
  person's default workspace (`teams.default_membership`, restated here in plain SQL so the models
  need not import the service layer).

A person with no personal team gets one made on the spot (a team of one with them as owner), the
same as `teams.ensure_personal_team`. Everything here runs on the flush's own connection.
"""

import sqlalchemy as sa


def personal_team_id(connection, user_id: int) -> int:
    """The person's personal team id, made if they have none."""
    found = connection.execute(
        sa.text(
            "select id from teams where owner_user_id = :u and is_personal order by id limit 1"
        ),
        {"u": user_id},
    ).scalar()
    if found is not None:
        return found
    created = connection.execute(
        sa.text("insert into teams (owner_user_id, is_personal) values (:u, true) returning id"),
        {"u": user_id},
    ).scalar()
    connection.execute(
        sa.text("insert into team_members (team_id, user_id, role) values (:t, :u, 'owner')"),
        {"t": created, "u": user_id},
    )
    return created


def default_workspace_id(connection, user_id: int) -> int:
    """`teams.default_membership` for a person: their only team, else their only ordinary team,
    else their personal one (made if they have none)."""
    rows = connection.execute(
        sa.text(
            "select tm.team_id, (t.is_personal and t.owner_user_id = :u) as own "
            "from team_members tm join teams t on t.id = tm.team_id "
            "where tm.user_id = :u order by tm.id"
        ),
        {"u": user_id},
    ).all()
    if not rows:
        return personal_team_id(connection, user_id)
    if len(rows) == 1:
        return rows[0][0]
    ordinary = [team_id for team_id, own in rows if not own]
    if len(ordinary) == 1:
        return ordinary[0]
    own_teams = [team_id for team_id, own in rows if own]
    return own_teams[0] if own_teams else rows[0][0]


def meeting_default(context) -> int:
    params = context.get_current_parameters()
    return (
        params.get("owner_team_id")
        or params.get("shared_team_id")
        or personal_team_id(context.connection, params["owner_user_id"])
    )


def notification_default(context) -> int:
    params = context.get_current_parameters()
    meeting_id = params.get("meeting_id")
    if meeting_id is not None:
        found = context.connection.execute(
            sa.text("select workspace_team_id from meetings where id = :m"), {"m": meeting_id}
        ).scalar()
        if found is not None:
            return found
    return personal_team_id(context.connection, params["user_id"])


def owner_personal_default(context) -> int:
    return personal_team_id(context.connection, context.get_current_parameters()["owner_user_id"])


def connection_default(context) -> int:
    return default_workspace_id(context.connection, context.get_current_parameters()["user_id"])


def join_default(context) -> int:
    params = context.get_current_parameters()
    event_id = params.get("calendar_event_id")
    if event_id is not None:
        found = context.connection.execute(
            sa.text(
                "select cc.team_id from calendar_events ce "
                "join calendar_connections cc on cc.id = ce.connection_id where ce.id = :e"
            ),
            {"e": event_id},
        ).scalar()
        if found is not None:
            return found
    return default_workspace_id(context.connection, params["user_id"])
