"""The Settings routes for the team's integrations (W10): the Slack webhook. Included into
`app.api.router`. Everyone on the team may read the state; only an owner or admin connects or
disconnects (403 `team_forbidden`). The webhook address is a secret: it is accepted on PUT and
never returned by any route.
"""

from datetime import UTC, datetime

from fastapi import APIRouter, Depends, Response
from sqlalchemy.orm import Session

from app import auth, integrations, team_admin
from app.api_schemas import ErrorResponse, SlackIn, SlackOut
from app.db import get_session
from app.models import Integration, TeamMember, User

router = APIRouter(prefix="/integrations", tags=["integrations"])

FORBIDDEN = {403: {"model": ErrorResponse, "description": "`team_forbidden`"}}


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
    session: Session = Depends(get_session), user: User = Depends(auth.current_user)
) -> SlackOut:
    """Whether the team posts new tasks to Slack, and how the last post went."""
    me = team_admin.my_membership(session, user)
    return _out(integrations.get(session, me.team_id), me)


@router.put(
    "/slack",
    responses={
        **FORBIDDEN,
        400: {"model": ErrorResponse, "description": "`slack_webhook_invalid`"},
        429: {"model": ErrorResponse, "description": "`integration_rate`"},
        502: {"model": ErrorResponse, "description": "`slack_unreachable`"},
        503: {"model": ErrorResponse, "description": "`integrations_unavailable`"},
    },
)
def connect_slack(
    body: SlackIn,
    session: Session = Depends(get_session),
    user: User = Depends(auth.current_user),
) -> SlackOut:
    """Connect Slack with an incoming-webhook address (owner or admin). One test message is posted
    to the channel first; a webhook Slack refuses is not stored. Replaces an earlier one."""
    me = team_admin.my_membership(session, user)
    team_admin.require_manager(me)
    row = integrations.connect_slack(session, me.team_id, user, body.webhook_url, datetime.now(UTC))
    return _out(row, me)


@router.delete("/slack", status_code=204, responses=FORBIDDEN)
def disconnect_slack(
    session: Session = Depends(get_session), user: User = Depends(auth.current_user)
) -> Response:
    """Disconnect Slack and delete the stored address (owner or admin). Safe to repeat."""
    me = team_admin.my_membership(session, user)
    team_admin.require_manager(me)
    integrations.disconnect(session, me.team_id)
    return Response(status_code=204)
