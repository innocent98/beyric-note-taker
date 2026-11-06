"""Teams (spec 8.1). Every account has one from sign-up: a team of one, owned by the account.

W0.4 builds only that foundation. Invites, roles and the Team screen are W3.
"""

from datetime import datetime

from sqlalchemy import case, delete, func, select, update
from sqlalchemy.orm import Session

from app.models import Meeting, Task, Team, TeamInvite, TeamMember, User


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


# ---- W3: joining, leaving, the share default ----

MANAGER_ROLES = ("owner", "admin")


class TeamChangeRefused(Exception):
    """A membership change that cannot happen; `code` and `message` are safe to show."""

    def __init__(self, status: int, code: str, message: str) -> None:
        super().__init__(message)
        self.status, self.code, self.message = status, code, message


def membership(session: Session, user_id: int) -> TeamMember | None:
    return session.scalars(select(TeamMember).where(TeamMember.user_id == user_id)).first()


def _lock_teams(session: Session, *team_ids: int) -> None:
    """Row-lock these teams, lowest id first, so two people joining each other's team at once
    cannot deadlock or leave a member in a team that was just dissolved."""
    for team_id in sorted(set(team_ids)):
        session.execute(select(Team.id).where(Team.id == team_id).with_for_update())


def move_team_content(session: Session, old_team_id: int, new_team_id: int) -> None:
    """Everything that belongs to a team of one moves into the team its owner joins: tasks now;
    W4 (projects) and W8 (series) add theirs HERE, in the same function, so a joining account
    never loses them (spec 8.1). Meetings are not team content: they stay theirs, and a meeting
    shared with the dissolved team becomes private (`shared_team_id` null)."""
    session.execute(update(Task).where(Task.team_id == old_team_id).values(team_id=new_team_id))
    from app import series  # series imports this module

    series.move_team_series(session, old_team_id, new_team_id)
    session.execute(
        update(Meeting).where(Meeting.shared_team_id == old_team_id).values(shared_team_id=None)
    )


def join(
    session: Session,
    user: User,
    team_id: int,
    role: str,
    alias: str | None,
    now: datetime,
) -> TeamMember:
    """Make `user` a member of `team_id`. Their own team of one is dissolved first (their tasks
    move; their meetings stay theirs and private). Someone who is on a team with other people must
    leave it first (409 `already_on_team`). In the caller's transaction, no commit."""
    current = membership(session, user.id)
    _lock_teams(session, team_id, *([current.team_id] if current else []))
    if session.get(Team, team_id) is None:  # dissolved while we waited
        raise TeamChangeRefused(404, "invite_invalid", "That invitation is not valid any more.")
    current = membership(session, user.id)
    if current is not None and current.team_id == team_id:
        return current
    if current is not None:
        others = session.scalar(
            select(func.count())
            .select_from(TeamMember)
            .where(TeamMember.team_id == current.team_id, TeamMember.user_id != user.id)
        )
        if others:
            raise TeamChangeRefused(
                409,
                "already_on_team",
                "You are on another team. Leave it first, then accept this invitation.",
            )
        old_team_id = current.team_id
        move_team_content(session, old_team_id, team_id)
        # What the person invited from their team of one goes with it (`team_invites.team_id` is
        # ON DELETE CASCADE): those links promised a place in a team that no longer exists, and
        # their places are free again.
        session.delete(current)
        session.flush()
        old = session.get(Team, old_team_id)
        if old is not None:
            session.delete(old)
        session.flush()
    member = TeamMember(
        team_id=team_id, user_id=user.id, role=role, aliases=[alias] if alias else []
    )
    session.add(member)
    session.flush()
    return member


def leave(session: Session, user: User, now: datetime) -> Team:
    """Take `user` out of their team and give them a fresh team of one; returns it. What happens
    to what was theirs (spec 8.1): the open tasks assigned to them are unassigned; the tasks read
    from THEIR meetings go with them, unassigned unless they hold them (the team can no longer
    read those meetings); every meeting of theirs shared with the team is unshared; the
    invitations they sent stop working. Their meetings never move. The caller has checked who may
    do this. In the caller's transaction, no commit."""
    member = membership(session, user.id)
    if member is None:
        return ensure_team(session, user)
    old_team_id = member.team_id
    _lock_teams(session, old_team_id)
    session.refresh(member)  # re-read under the lock; gone means a removal beat us to it
    member = membership(session, user.id)
    if member is None or member.team_id != old_team_id:
        return ensure_team(session, user)
    fresh = Team(owner_user_id=user.id)
    session.add(fresh)
    session.flush()
    own_meetings = select(Meeting.id).where(Meeting.owner_user_id == user.id)
    session.execute(
        update(Task)
        .where(
            Task.team_id == old_team_id,
            Task.source == "ai",
            Task.meeting_id.in_(own_meetings),
        )
        .values(
            team_id=fresh.id,
            assignee_user_id=case((Task.assignee_user_id == user.id, user.id), else_=None),
        )
    )
    session.execute(
        update(Task)
        .where(
            Task.team_id == old_team_id,
            Task.assignee_user_id == user.id,
            Task.status != "done",
        )
        .values(assignee_user_id=None)
    )
    session.execute(
        update(Meeting)
        .where(Meeting.owner_user_id == user.id, Meeting.shared_team_id == old_team_id)
        .values(shared_team_id=None)
    )
    from app import series  # series imports this module

    series.forget_meetings_of(session, user.id, old_team_id)
    session.execute(
        update(TeamInvite)
        .where(
            TeamInvite.team_id == old_team_id,
            TeamInvite.invited_by == user.id,
            TeamInvite.accepted_at.is_(None),
            TeamInvite.revoked_at.is_(None),
        )
        .values(revoked_at=now)
    )
    session.delete(member)
    session.flush()
    session.add(TeamMember(team_id=fresh.id, user_id=user.id, role="owner"))
    session.flush()
    return fresh


def apply_share_default(session: Session, meeting: Meeting) -> None:
    """When a meeting's notes are first written: share it with the owner's team if the team's
    default says so (spec 8.2, decision D3). Read NOW, never when the bot joined and never again
    for a rewrite, so a calendar rule of "all meetings" cannot publish a call by itself and an
    owner's "unshare" stays unshared.

    Only the meetings of the team's owner and admins are covered: an admin switching the default
    on must not publish a member's calls; a member shares their own with the switch on the
    meeting page."""
    member = membership(session, meeting.owner_user_id)
    if member is None or member.role not in MANAGER_ROLES:
        return
    team = session.get(Team, member.team_id)
    if team is not None and team.share_new_meetings:
        meeting.shared_team_id = team.id


def owns_team_with_others(session: Session, user_id: int) -> bool:
    """True when this account owns a team that still has other active members. Deleting it is
    refused until the pilot admin hands the team over by hand (spec section 4; `transfer_owner`,
    `scripts/transfer_team_owner.py`)."""
    return (
        session.scalar(
            select(func.count())
            .select_from(TeamMember)
            .join(Team, Team.id == TeamMember.team_id)
            .join(User, User.id == TeamMember.user_id)
            .where(
                Team.owner_user_id == user_id,
                TeamMember.user_id != user_id,
                User.status != "deleted",
            )
        )
        > 0
    )


def transfer_owner(session: Session, team_id: int, new_owner_user_id: int) -> None:
    """Hand a team to one of its members (the pilot admin does this by hand: spec section 4).
    The old owner becomes an admin. In the caller's transaction, no commit."""
    team = session.get(Team, team_id)
    heir = session.scalars(
        select(TeamMember).where(
            TeamMember.team_id == team_id, TeamMember.user_id == new_owner_user_id
        )
    ).first()
    if team is None or heir is None:
        raise TeamChangeRefused(404, "not_found", "That person is not a member of that team.")
    for member in session.scalars(
        select(TeamMember).where(TeamMember.team_id == team_id, TeamMember.role == "owner")
    ):
        member.role = "admin"
    heir.role, team.owner_user_id = "owner", heir.user_id
    session.flush()
