"""The Settings routes for the team's integrations (W10): Slack and Linear. Included into
`app.api.router`. Everyone on the team may read the state; only an owner or admin connects or
disconnects (403 `team_forbidden`). The webhook address and the Linear key are secrets: they are
accepted on PUT and never returned by any route.
"""

from datetime import UTC, datetime

from fastapi import APIRouter, Depends, Response
from sqlalchemy.orm import Session

from app import auth, integrations, team_admin, teams
from app.api_schemas import (
    ErrorResponse,
    LinearIn,
    LinearKeyIn,
    LinearOut,
    LinearTeamsOut,
    SlackIn,
    SlackOut,
)
from app.db import get_session
from app.models import Integration, Team, TeamMember, User
from app.services import Refused

router = APIRouter(prefix="/integrations", tags=["integrations"])

FORBIDDEN = {403: {"model": ErrorResponse, "description": "`team_forbidden`"}}


def refuse_personal_team(session: Session, me: TeamMember) -> None:
    """Slack and Linear belong to a team; the personal space has none (multi-team design 6.9). While
    `MULTI_TEAM_ENABLED` is on, connecting either to the caller's personal team is 409
    `personal_team`. With it off the team of one stays allowed: it is the only team there is."""
    if not teams.multi_team_enabled():
        return
    team = session.get(Team, me.team_id)
    if team is not None and teams.is_personal_of(team, me.user_id):
        raise Refused(
            409,
            "personal_team",
            "Slack and Linear belong to a team. Your personal space has none; choose a team first.",
        )


def _out(row: Integration | None, me: TeamMember) -> SlackOut:
    return SlackOut(
        connected=row is not None,
        status=row.status if row else None,
        last_error=row.last_error if row else None,
        last_posted_at=row.last_posted_at if row else None,
        can_manage=me.role in team_admin.MANAGER_ROLES,
    )


@router.get("/slack")
def read_slack(
    team_id: int | None = Depends(team_admin.requested_team),
    session: Session = Depends(get_session),
    user: User = Depends(auth.current_user),
) -> SlackOut:
    """Whether the team posts new tasks to Slack, and how the last post went."""
    me = team_admin.my_membership(session, user, team_id)
    return _out(integrations.get(session, me.team_id), me)


@router.put(
    "/slack",
    responses={
        **FORBIDDEN,
        400: {"model": ErrorResponse, "description": "`slack_webhook_invalid`"},
        409: {"model": ErrorResponse, "description": "`personal_team`"},
        429: {"model": ErrorResponse, "description": "`integration_rate`"},
        502: {"model": ErrorResponse, "description": "`slack_unreachable`"},
        503: {"model": ErrorResponse, "description": "`integrations_unavailable`"},
    },
)
def connect_slack(
    body: SlackIn,
    team_id: int | None = Depends(team_admin.requested_team),
    session: Session = Depends(get_session),
    user: User = Depends(auth.current_user),
) -> SlackOut:
    """Connect Slack with an incoming-webhook address (owner or admin). One test message is posted
    to the channel first; a webhook Slack refuses is not stored. Replaces an earlier one."""
    me = team_admin.my_membership(session, user, team_id)
    team_admin.require_manager(me)
    refuse_personal_team(session, me)
    row = integrations.connect_slack(session, me.team_id, user, body.webhook_url, datetime.now(UTC))
    return _out(row, me)


@router.delete("/slack", status_code=204, responses=FORBIDDEN)
def disconnect_slack(
    team_id: int | None = Depends(team_admin.requested_team),
    session: Session = Depends(get_session),
    user: User = Depends(auth.current_user),
) -> Response:
    """Disconnect Slack and delete the stored address (owner or admin). Safe to repeat."""
    me = team_admin.my_membership(session, user, team_id)
    team_admin.require_manager(me)
    integrations.disconnect(session, me.team_id)
    return Response(status_code=204)


LINEAR_ERRORS = {
    **FORBIDDEN,
    400: {
        "model": ErrorResponse,
        "description": "`linear_key_invalid`, `linear_key_rejected`, `linear_team_invalid`",
    },
    409: {"model": ErrorResponse, "description": "`personal_team`"},
    429: {"model": ErrorResponse, "description": "`integration_rate`"},
    502: {"model": ErrorResponse, "description": "`linear_unreachable`"},
    503: {"model": ErrorResponse, "description": "`integrations_unavailable`"},
}


def _linear_out(row: Integration | None, me: TeamMember) -> LinearOut:
    return LinearOut(
        connected=row is not None,
        status=row.status if row else None,
        last_error=row.last_error if row else None,
        last_synced_at=row.last_posted_at if row else None,
        team_name=row.label if row else None,
        can_manage=me.role in team_admin.MANAGER_ROLES,
    )


@router.get("/linear")
def read_linear(
    team_id: int | None = Depends(team_admin.requested_team),
    session: Session = Depends(get_session),
    user: User = Depends(auth.current_user),
) -> LinearOut:
    """Whether the team pushes new tasks to Linear, to which team, and how the last push went."""
    me = team_admin.my_membership(session, user, team_id)
    return _linear_out(integrations.get(session, me.team_id, integrations.LINEAR), me)


@router.post("/linear/teams", responses=LINEAR_ERRORS)
def linear_teams(
    body: LinearKeyIn,
    team_id: int | None = Depends(team_admin.requested_team),
    session: Session = Depends(get_session),
    user: User = Depends(auth.current_user),
) -> LinearTeamsOut:
    """The Linear teams a pasted key can reach, for the choice before connecting (owner or admin).
    The key is checked with Linear and not stored."""
    me = team_admin.my_membership(session, user, team_id)
    team_admin.require_manager(me)
    refuse_personal_team(session, me)
    found = integrations.linear_teams(session, me.team_id, body.api_key, datetime.now(UTC))
    return LinearTeamsOut(teams=found)


@router.put("/linear", responses=LINEAR_ERRORS)
def connect_linear(
    body: LinearIn,
    team_id: int | None = Depends(team_admin.requested_team),
    session: Session = Depends(get_session),
    user: User = Depends(auth.current_user),
) -> LinearOut:
    """Connect Linear with a personal API key and one of its teams (owner or admin). New tasks from
    shared meetings are then created as issues in that team, once each. Replaces an earlier key."""
    me = team_admin.my_membership(session, user, team_id)
    team_admin.require_manager(me)
    refuse_personal_team(session, me)
    row = integrations.connect_linear(
        session, me.team_id, user, body.api_key, body.team_id, datetime.now(UTC)
    )
    return _linear_out(row, me)


@router.delete("/linear", status_code=204, responses=FORBIDDEN)
def disconnect_linear(
    team_id: int | None = Depends(team_admin.requested_team),
    session: Session = Depends(get_session),
    user: User = Depends(auth.current_user),
) -> Response:
    """Disconnect Linear and delete the stored key (owner or admin). Issues already created stay in
    Linear. Safe to repeat."""
    me = team_admin.my_membership(session, user, team_id)
    team_admin.require_manager(me)
    integrations.disconnect(session, me.team_id, integrations.LINEAR)
    return Response(status_code=204)
