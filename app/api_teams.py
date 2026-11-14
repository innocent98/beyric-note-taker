"""The teams a person belongs to: the list behind the switcher, and creating one (multi-team design
6.1, 6.7). Included into `app.api.router`, so it shares its `/api` prefix, login check, CSRF rule
and error shape. Which team a request is about is a different thing: the `X-Team-Id` header of
the team-implicit routes (`app/team_admin.py`)."""

from fastapi import APIRouter, Depends
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app import auth, teams, titles
from app.api_schemas import ErrorResponse, TeamCreate, TeamList, TeamSummary
from app.db import get_session
from app.models import Team, TeamMember, User
from app.services import Refused

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
        )
        for member, team in rows
    ]


def _owned_count(session: Session, user: User) -> int:
    return session.scalar(
        select(func.count())
        .select_from(Team)
        .where(Team.owner_user_id == user.id, Team.is_personal.is_(False))
    )


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
    return TeamList(
        teams=_summaries(session, user),
        can_create_more=max(0, teams.MAX_OWNED_TEAMS - _owned_count(session, user)),
    )


@router.post(
    "/teams",
    status_code=201,
    tags=["team"],
    responses={
        409: {"model": ErrorResponse, "description": "`multi_team_disabled`, `team_limit`"},
        422: {"model": ErrorResponse, "description": "`invalid_name`"},
    },
)
def create_team(
    body: TeamCreate,
    session: Session = Depends(get_session),
    user: User = Depends(auth.current_user),
) -> TeamSummary:
    """Make a team the caller owns. At most three teams per account besides the personal one (409
    `team_limit`). 409 `multi_team_disabled` while the switch is off (a second team is refused
    then), 422 `invalid_name` for an empty name."""
    if not teams.multi_team_enabled():
        raise Refused(409, "multi_team_disabled", "Creating a team is not available yet.")
    name = titles.clean_title(body.name, 100)
    if not name:
        raise Refused(422, "invalid_name", "Give the team a name.")
    teams.ensure_personal_team(session, user)
    if _owned_count(session, user) >= teams.MAX_OWNED_TEAMS:
        raise Refused(
            409,
            "team_limit",
            f"You can own up to {teams.MAX_OWNED_TEAMS} teams.",
        )
    team = Team(owner_user_id=user.id, name=name, is_personal=False)
    session.add(team)
    session.flush()
    session.add(TeamMember(team_id=team.id, user_id=user.id, role="owner"))
    session.commit()
    return next(t for t in _summaries(session, user) if t.id == team.id)
