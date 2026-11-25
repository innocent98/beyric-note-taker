"""The teams a person belongs to: the list behind the switcher, and creating one (multi-team design
6.1, 6.7). Included into `app.api.router`, so it shares its `/api` prefix, login check, CSRF rule
and error shape. Which team a request is about is a different thing: the `X-Team-Id` header of
the team-implicit routes (`app/team_admin.py`)."""

from datetime import UTC, datetime

from fastapi import APIRouter, Depends, Response
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app import auth, calendar_connect, entitlements, google_oauth, team_delete, teams, titles
from app.api_schemas import ErrorResponse, TeamCreate, TeamList, TeamSummary
from app.db import get_session
from app.models import Team, TeamMember, User
from app.services import Refused, get_bot_provider

router = APIRouter()


def _summaries(session: Session, user: User) -> list[TeamSummary]:
    default = teams.default_membership(session, user.id)
    counts = {
        team_id: n
        for team_id, n in session.execute(
            select(TeamMember.team_id, func.count())
            .join(User, User.id == TeamMember.user_id)
            .where(
                User.status != "deleted",
                TeamMember.team_id.in_(
                    select(TeamMember.team_id).where(TeamMember.user_id == user.id)
                ),
            )
            .group_by(TeamMember.team_id)
        )
    }
    rows = session.execute(
        select(TeamMember, Team)
        .join(Team, Team.id == TeamMember.team_id)
        .where(TeamMember.user_id == user.id)
        .order_by(TeamMember.id)
    ).all()
    return [
        TeamSummary(
            id=team.id,
            name=team.name,
            role=member.role,
            member_count=counts.get(team.id, 1),
            is_personal=teams.is_personal_of(team, user.id),
            is_default=default is not None and default.team_id == team.id,
            can_manage=member.role in teams.MANAGER_ROLES,
            kind=team.kind,
        )
        for member, team in rows
    ]


@router.get("/teams", tags=["team"])
def list_teams(
    session: Session = Depends(get_session), user: User = Depends(auth.current_user)
) -> TeamList:
    """Every team the caller belongs to, oldest membership first: its id and name, the caller's
    role, how many people are in it, whether it is their personal space and which one a request
    with no `X-Team-Id` acts on. The personal space is made here if the account has none (a person
    who joined a team before multi-team lost theirs)."""
    teams.ensure_personal_team(session, user)
    session.commit()
    now = datetime.now(UTC)
    allowed = entitlements.max_owned_teams(session, user, now)
    owned = teams.owned_team_count(session, user)
    plan = entitlements.live_plan(session, user, now)
    return TeamList(
        teams=_summaries(session, user),
        can_create_more=None if allowed is None else max(0, allowed - owned),
        owned_teams=owned,
        max_owned_teams=allowed,
        plan_name=plan.name if plan is not None and not user.is_admin else None,
        limit_message=teams.limit_message(session, user, now),
    )


@router.post(
    "/teams",
    status_code=201,
    tags=["team"],
    responses={
        409: {"model": ErrorResponse, "description": "`team_limit`"},
        422: {"model": ErrorResponse, "description": "`invalid_name`"},
    },
)
def create_team(
    body: TeamCreate,
    session: Session = Depends(get_session),
    user: User = Depends(auth.current_user),
) -> TeamSummary:
    """Make a team the caller owns, as many as their plan allows besides the personal one (Free 0,
    Starter 3, Pro 10, Scale unlimited; 409 `team_limit` with a message naming the plan to upgrade
    to), 422 `invalid_name` for an empty name."""
    name = titles.clean_title(body.name, 100)
    if not name:
        raise Refused(422, "invalid_name", "Give the team a name.")
    teams.ensure_personal_team(session, user)
    try:
        team = teams.create_owned_team(session, user, name, kind=body.kind)
    except teams.TeamChangeRefused as exc:
        raise Refused(exc.status, exc.code, exc.message) from None
    session.commit()
    return next(t for t in _summaries(session, user) if t.id == team.id)


@router.delete(
    "/teams/{team_id:id}",
    status_code=204,
    tags=["team"],
    responses={
        403: {"model": ErrorResponse, "description": "`team_forbidden`: not the owner"},
        404: {"model": ErrorResponse, "description": "No such team (of the caller's)"},
        409: {
            "model": ErrorResponse,
            "description": "`personal_team`, `team_has_members`, `meeting_active`",
        },
        502: {"model": ErrorResponse, "description": "`recording_delete_failed`"},
    },
)
def delete_team(
    team_id: int,
    session: Session = Depends(get_session),
    user: User = Depends(auth.current_user),
    provider=Depends(get_bot_provider),
    google: google_oauth.GoogleOAuth | None = Depends(google_oauth.get_google),
) -> Response:
    """Delete a team workspace. Only its owner, and only when nobody else is in it (409
    `team_has_members`); the personal space cannot be deleted (409 `personal_team`). Its tasks,
    projects, series and integrations go with it. Its meetings go back to the people who recorded
    them, private, in their personal workspaces; a meeting whose recorder's account is gone is
    deleted. Deleting never depends on a plan: a lapsed plan keeps every workspace until its owner
    deletes it."""
    team_delete.delete_team(session, provider, team_id, user)
    calendar_connect.settle_released(session, google)  # the provider side, after the commit
    return Response(status_code=204)
