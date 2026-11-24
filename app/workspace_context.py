"""The active workspace: required context on every route (workspaces design, section 7).

A workspace is a team; a person's personal space is a workspace of one. Every request that returns
or changes workspace data acts on exactly ONE workspace: the one named in the `X-Team-Id` header,
else the caller's personal workspace. There is no "no narrowing" any more (the multi-team switch is
gone; it was on in production already), so a person in a team who forgets the header sees their
personal space, never everything at once. Nothing is kept on the server between requests: two
browser tabs on two workspaces never fight over a "current" one.

Named `workspace_context`, not `workspace`: `app/workspace.py` is the Decisions and Review module.

How a route uses it:

* `ws: Team = Depends(workspace_context.current)`: the workspace as a `Team` row (404 `not_found`
  "No such team." when the caller is not in the team named; the personal workspace is made if the
  account somehow has none).
* `visibility.in_workspace(user, ws)`: the one `where` condition for "the caller may read this
  meeting AND it lives in this workspace". Lists, searches, counts, Ask and badges all narrow
  through it, so a count equals its list.
* A route that opens ONE thing by id (a meeting, a task) authorises by readability only, as before,
  and the response names the thing's workspace so the web can switch to it: the header never turns
  a readable meeting into a 404.

`require` is the same thing for code that has the session and the user already (services, the
`team_admin` helpers); `current` is the FastAPI dependency around it.
"""

from typing import Annotated

from fastapi import Depends, Header
from sqlalchemy.orm import Session

from app import auth, teams
from app.db import get_session
from app.models import Team, User
from app.services import MAX_ID, Refused

TEAM_HEADER = "X-Team-Id"


def requested_team(
    x_team_id: Annotated[
        int | None,
        Header(
            alias=TEAM_HEADER,
            ge=1,
            le=MAX_ID,
            description="The workspace (team) this request is about. Omitted: the caller's "
            "personal workspace. A team the caller is not in is 404 `not_found`.",
        ),
    ] = None,
) -> int | None:
    """The raw `X-Team-Id` value of a route: per request, nothing kept on the server between
    requests. Pass it to `require` (or let `current` do both)."""
    return x_team_id


def require(session: Session, user: User, header_value: int | None) -> Team:
    """The caller's active workspace: the team `header_value` names (404 `not_found` "No such
    team." for a team they are not in: it is not theirs to know), else their personal team, made if
    the account has none. Never None."""
    if header_value is None:
        return teams.ensure_personal_team(session, user)
    member = teams.membership(session, user.id, header_value)
    team = session.get(Team, header_value) if member is not None else None
    if team is None:
        raise Refused(404, "not_found", "No such team.")
    return team


def current(
    team_id: Annotated[int | None, Depends(requested_team)],
    session: Session = Depends(get_session),
    user: User = Depends(auth.current_user),
) -> Team:
    """FastAPI dependency: `require` for this request's `X-Team-Id` header."""
    return require(session, user, team_id)
