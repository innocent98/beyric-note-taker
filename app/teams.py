"""Teams (spec 8.1). Every account has one from sign-up: a team of one, owned by the account.

W0.4 builds only that foundation. Invites, roles and the Team screen are W3.
"""

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.models import Team, TeamMember, User


def team_of(session: Session, user_id: int) -> Team | None:
    return session.scalars(
        select(Team)
        .join(TeamMember, TeamMember.team_id == Team.id)
        .where(TeamMember.user_id == user_id)
    ).first()


def ensure_team(session: Session, user: User) -> Team:
    """The account's team, made if it has none. In the caller's transaction (flushes, no commit;
    `user` must have an id). Every account-creation path calls this through
    `accounts.add_user_defaults`, so the invariant "every account has a team" holds without the
    paths knowing about it."""
    team = team_of(session, user.id)
    if team is None:
        team = Team(owner_user_id=user.id)
        session.add(team)
        session.flush()
        session.add(TeamMember(team_id=team.id, user_id=user.id, role="owner"))
        session.flush()
    return team


def remove_users(session: Session, user_ids: list[int]) -> None:
    """Take these accounts out of their teams before the accounts are deleted. A team left with
    no members goes too (meetings shared with it become private: `shared_team_id` is set null by
    the foreign key). A team that still has other members is handed to the one who joined first;
    W3 will refuse that case until the pilot admin transfers ownership by hand (spec section 4),
    but a purge must never be blocked by it. In the caller's transaction, no commit."""
    if not user_ids:
        return
    owned = list(session.scalars(select(Team).where(Team.owner_user_id.in_(user_ids))))
    session.execute(delete(TeamMember).where(TeamMember.user_id.in_(user_ids)))
    for team in owned:
        heir = session.scalars(
            select(TeamMember)
            .where(TeamMember.team_id == team.id)
            .order_by(TeamMember.joined_at, TeamMember.id)
        ).first()
        if heir is None:
            session.delete(team)
        else:
            team.owner_user_id, heir.role = heir.user_id, "owner"
    session.flush()
