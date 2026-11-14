"""Teams (spec 8.1). Every account has a personal team from sign-up: a team of one, owned by the
account. Since the multi-team design (2026-11-13) a person may also belong to other teams.

The rules that make that safe live here, in one place:

* `is_personal` marks a person's own permanent space. It is never left, never joined and nobody is
  invited into it.
* `default_membership` / `team_of` is the ONE rule for a route that names no team: the caller's
  single ordinary team if they have exactly one, else their personal team. For everyone who has one
  membership, and for everyone while `MULTI_TEAM_ENABLED` is off, that is the team they have always
  had.
* `MULTI_TEAM_ENABLED` (off by default) decides whether a second membership may be made. Off:
  joining works as it always did (a team of one is dissolved into the team joined; someone on a
  team with others is refused with `already_on_team`). On: joining adds a membership and dissolves
  nothing.
"""

from datetime import datetime

from sqlalchemy import case, delete, func, select, update
from sqlalchemy.orm import Session

from app.config import get_settings
from app.models import (
    Meeting,
    Milestone,
    Project,
    Task,
    Team,
    TeamInvite,
    TeamMember,
    User,
    UserSettings,
)


def multi_team_enabled() -> bool:
    """`MULTI_TEAM_ENABLED`: may a person belong to more than one team. Read on each call (the
    settings are cached), so a test may replace this function."""
    return get_settings().multi_team_enabled


def is_personal_of(team: Team, user_id: int) -> bool:
    """True when `team` is this person's own personal team. A team flagged personal that someone
    else joined by hand (old test fixtures, a flag-off join of a team of one) is simply a team to
    its other members."""
    return team.is_personal and team.owner_user_id == user_id


def team_ids_of(session: Session, user_id: int) -> list[int]:
    """Every team the person belongs to, oldest membership first."""
    return list(
        session.scalars(
            select(TeamMember.team_id).where(TeamMember.user_id == user_id).order_by(TeamMember.id)
        )
    )


def personal_team(session: Session, user_id: int) -> Team | None:
    return session.scalars(
        select(Team).where(Team.owner_user_id == user_id, Team.is_personal.is_(True))
    ).first()


def ensure_personal_team(session: Session, user: User) -> Team:
    """The person's personal team, made if they have none (a flag-off join dissolves it). In the
    caller's transaction (flushes, no commit; `user` must have an id)."""
    team = personal_team(session, user.id)
    if team is None:
        team = Team(owner_user_id=user.id, is_personal=True)
        session.add(team)
        session.flush()
        session.add(TeamMember(team_id=team.id, user_id=user.id, role="owner"))
        session.flush()
    return team


def default_membership(session: Session, user_id: int) -> TeamMember | None:
    """The membership a route that names no team acts on: the person's only one; else their only
    ordinary team; else their personal team (several ordinary teams, or none). No membership at all
    is None. Never raises however many memberships there are."""
    rows = session.execute(
        select(TeamMember, Team)
        .join(Team, Team.id == TeamMember.team_id)
        .where(TeamMember.user_id == user_id)
        .order_by(TeamMember.id)
    ).all()
    if not rows:
        return None
    if len(rows) == 1:
        return rows[0][0]
    ordinary = [m for m, t in rows if not is_personal_of(t, user_id)]
    if len(ordinary) == 1:
        return ordinary[0]
    own = [m for m, t in rows if is_personal_of(t, user_id)]
    return own[0] if own else rows[0][0]


def ordinary_teams_of(session: Session, user_id: int) -> list[Team]:
    """The teams the person belongs to that are not their own personal one, oldest first."""
    rows = session.scalars(
        select(Team)
        .join(TeamMember, TeamMember.team_id == Team.id)
        .where(TeamMember.user_id == user_id)
        .order_by(TeamMember.id)
    )
    return [t for t in rows if not is_personal_of(t, user_id)]


def team_of(session: Session, user_id: int) -> Team | None:
    """The person's default team (see `default_membership`)."""
    member = default_membership(session, user_id)
    return session.get(Team, member.team_id) if member is not None else None


def ensure_team(session: Session, user: User) -> Team:
    """The account's default team, a personal team made if it has none. In the caller's transaction
    (flushes, no commit; `user` must have an id). Every account-creation path calls this through
    `accounts.add_user_defaults`, so the invariant "every account has a team" holds without the
    paths knowing about it."""
    team = team_of(session, user.id)
    return team if team is not None else ensure_personal_team(session, user)


def team_for_meeting(session: Session, meeting: Meeting) -> Team:
    """The team a meeting's extracted tasks belong to (spec 6.6): the team that owns it, else the
    team it is shared to, else (switch on) its recorder's personal team. While the switch is off
    it is the recorder's default team, as it has always been. In the caller's transaction (it may
    make a personal team)."""
    for team_id in (meeting.owner_team_id, meeting.shared_team_id):
        if team_id is not None:
            team = session.get(Team, team_id)
            if team is not None:
                return team
    recorder = session.get(User, meeting.owner_user_id)
    if multi_team_enabled():
        return ensure_personal_team(session, recorder)
    return ensure_team(session, recorder)


def rehome_tasks(session: Session, meeting: Meeting, target: Team | None = None) -> None:
    """Sharing, unsharing, moving and moving back: the tasks the AI took from the meeting follow it
    to the meeting's team (spec 6.6). Tasks typed in by hand stay where they were made. What
    belonged to the old team goes with it: the project, milestone and sprint are cleared, an
    assignee who is not in the new team is unassigned, and a date proposed from the meeting stays
    with the meeting. With no `target` given, nothing happens while `MULTI_TEAM_ENABLED` is off: a
    person with one team has nowhere else to put them, and the old rule (tasks stay, hidden by the
    meeting check) is the one that applies to a team of several. A `target` (a team being deleted
    gives its meetings back: `app/deletion.py`) always re-files. In the caller's transaction, no
    commit."""
    if target is None:
        if not multi_team_enabled():
            return
        target = team_for_meeting(session, meeting)
    members = select(TeamMember.user_id).where(TeamMember.team_id == target.id)
    session.execute(
        update(Task)
        .where(Task.meeting_id == meeting.id, Task.source == "ai", Task.team_id != target.id)
        .values(
            team_id=target.id,
            assignee_user_id=case((Task.assignee_user_id.in_(members), Task.assignee_user_id)),
            project_id=None,
            milestone_id=None,
            check_project=False,
            sprint_id=None,
            carried=False,
        )
    )
    session.execute(
        update(Milestone)
        .where(
            Milestone.source_meeting_id == meeting.id,
            Milestone.confirmed_at.is_(None),
            Milestone.team_id != target.id,
        )
        .values(team_id=target.id, project_id=None)
    )


def create_owned_team(session: Session, user: User, name: str) -> Team:
    """Make a team the person owns: at most `MAX_OWNED_TEAMS` besides the personal one (409
    `team_limit`). The person's row is locked first, so two creates at once count one after the
    other. In the caller's transaction (flushes, no commit)."""
    session.execute(select(User.id).where(User.id == user.id).with_for_update())
    owned = session.scalar(
        select(func.count())
        .select_from(Team)
        .where(Team.owner_user_id == user.id, Team.is_personal.is_(False))
    )
    if owned >= MAX_OWNED_TEAMS:
        raise TeamChangeRefused(409, "team_limit", f"You can own up to {MAX_OWNED_TEAMS} teams.")
    team = Team(owner_user_id=user.id, name=name, is_personal=False)
    session.add(team)
    session.flush()
    session.add(TeamMember(team_id=team.id, user_id=user.id, role="owner"))
    session.flush()
    return team


def doomed_teams(session: Session, user_id: int) -> list[int]:
    """The teams that go when this account is deleted: those it owns that nobody else is in (see
    `remove_users`, which deletes them). What the team owned is not the team's to keep then:
    `app/deletion.py` decides each moved meeting's fate (spec 6.8, proposed: back to its recorder
    when that account still exists, otherwise deleted with the team)."""
    owned = session.scalars(select(Team.id).where(Team.owner_user_id == user_id))
    return [team_id for team_id in owned if not _has_other_members(session, team_id, user_id)]


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
            # A team handed on is an ordinary team, whatever it was: the heir may already own a
            # personal team, and `uq_teams_one_personal_per_owner` must never block a deletion.
            team.owner_user_id, heir.role, team.is_personal = heir.user_id, "owner", False
    session.flush()


# ---- W3: joining, leaving, the share default ----

MANAGER_ROLES = ("owner", "admin")
# Teams one account may own besides its personal one (a plan setting later; checked in the route,
# not by a database constraint).
MAX_OWNED_TEAMS = 3


class TeamChangeRefused(Exception):
    """A membership change that cannot happen; `code` and `message` are safe to show."""

    def __init__(self, status: int, code: str, message: str) -> None:
        super().__init__(message)
        self.status, self.code, self.message = status, code, message


def membership(session: Session, user_id: int, team_id: int | None = None) -> TeamMember | None:
    """The person's membership of `team_id`, or, naming none, of their default team."""
    if team_id is None:
        return default_membership(session, user_id)
    return session.scalars(
        select(TeamMember).where(TeamMember.user_id == user_id, TeamMember.team_id == team_id)
    ).first()


def _has_other_members(session: Session, team_id: int, user_id: int) -> bool:
    return (
        session.scalar(
            select(func.count())
            .select_from(TeamMember)
            .where(TeamMember.team_id == team_id, TeamMember.user_id != user_id)
        )
        > 0
    )


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
    # The old team's sprints go with it: its tasks arrive in the new team's Backlog (W9).
    session.execute(
        update(Task)
        .where(Task.team_id == old_team_id)
        .values(team_id=new_team_id, sprint_id=None, carried=False)
    )
    session.execute(
        update(Project).where(Project.team_id == old_team_id).values(team_id=new_team_id)
    )
    session.execute(
        update(Milestone).where(Milestone.team_id == old_team_id).values(team_id=new_team_id)
    )
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
    """Make `user` a member of `team_id`, in the caller's transaction (no commit).

    `MULTI_TEAM_ENABLED` on: the person gains a membership and nothing is dissolved or moved; their
    personal team is made if a flag-off join dissolved it. A personal team cannot be joined (409
    `personal_team`). Joining a team they are already in returns that membership.

    Off (today's behaviour, exactly): their own team of one is dissolved first (their tasks move;
    their meetings stay theirs and private), and someone on a team with other people must leave it
    first (409 `already_on_team`). A team of one that somebody joins stops being "personal" (it is
    now the team its owner runs)."""
    on = multi_team_enabled()
    current = default_membership(session, user.id)
    _lock_teams(session, team_id, *([current.team_id] if current else []))
    target = session.get(Team, team_id)
    if target is None:  # dissolved while we waited
        raise TeamChangeRefused(404, "invite_invalid", "That invitation is not valid any more.")
    held = membership(session, user.id, team_id)
    if held is not None:
        return held
    if on:
        if target.is_personal:
            raise TeamChangeRefused(
                409, "personal_team", "Nobody can join a personal space. Ask for a team invitation."
            )
        ensure_personal_team(session, user)
    else:
        current = default_membership(session, user.id)
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
            # What the person invited from their team of one goes with it (`team_invites.team_id`
            # is ON DELETE CASCADE): those links promised a place in a team that no longer exists,
            # and their places are free again.
            session.delete(current)
            session.flush()
            old = session.get(Team, old_team_id)
            if old is not None:
                session.delete(old)
            session.flush()
        if target.is_personal:
            # The owner invited people into their team of one (today's way to make a team): it is
            # a team now, no longer a personal space. Nothing else changes while the switch is off
            # (the owner's new personal team is made later, by `ensure_personal_team`, the first
            # time something needs it).
            target.is_personal = False
            session.flush()
    member = TeamMember(
        team_id=team_id, user_id=user.id, role=role, aliases=[alias] if alias else []
    )
    session.add(member)
    session.flush()
    return member


def leave(session: Session, user: User, now: datetime, team_id: int | None = None) -> Team:
    """Take `user` out of `team_id` (naming none: their default team) and return their personal
    team, made if they have none. The person already has a personal team, so no new team of one is
    created. A personal team cannot be left (409 `personal_team`). What happens to what was theirs
    (spec 8.1): the open tasks assigned to them are unassigned; the tasks read from THEIR meetings
    go with them to their personal team, unassigned unless they hold them (the team can no longer
    read those meetings); every meeting of theirs shared with the team is unshared; the invitations
    they sent stop working. Their meetings never move. The caller has checked who may do this. In
    the caller's transaction, no commit."""
    member = membership(session, user.id, team_id)
    if member is None:
        return ensure_personal_team(session, user)
    old_team_id = member.team_id
    _lock_teams(session, old_team_id)
    member = membership(session, user.id, old_team_id)  # re-read under the lock
    if member is None:  # a removal beat us to it
        return ensure_personal_team(session, user)
    old_team = session.get(Team, old_team_id)
    if (
        old_team is not None
        and is_personal_of(old_team, user.id)
        and not _has_other_members(session, old_team_id, user.id)
    ):
        raise TeamChangeRefused(409, "personal_team", "Your personal space cannot be left.")
    home = ensure_personal_team(session, user)
    # A meeting MOVED to this team is the team's, not theirs to take away: it stays, and so do its
    # tasks. Only what the person recorded and kept as their own leaves with them.
    own_meetings = select(Meeting.id).where(
        Meeting.owner_user_id == user.id, Meeting.owner_team_id.is_(None)
    )
    session.execute(
        update(Task)
        .where(
            Task.team_id == old_team_id,
            Task.source == "ai",
            Task.meeting_id.in_(own_meetings),
        )
        .values(
            team_id=home.id,
            assignee_user_id=case((Task.assignee_user_id == user.id, user.id), else_=None),
            # The old team's projects and milestones stay with it.
            project_id=None,
            milestone_id=None,
            check_project=False,
            # ...and so do its sprints: the task is in their Backlog.
            sprint_id=None,
            carried=False,
        )
    )
    # A proposal from their meeting must not be confirmable by a team that cannot read it.
    session.execute(
        delete(Milestone).where(
            Milestone.team_id == old_team_id,
            Milestone.confirmed_at.is_(None),
            Milestone.source_meeting_id.in_(own_meetings),
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
    return home


def auto_share_team(session: Session, user: User) -> int | None:
    """The team the person chose for new meetings (`user_settings.auto_share_team_id`), only while
    they are still a member of it and it is not a personal space; else None. Always None while
    `MULTI_TEAM_ENABLED` is off: then only today's team-wide rule applies (owners and admins), and a
    row written while the switch was on lies dormant (it comes back if the switch does)."""
    if not multi_team_enabled():
        return None
    team_id = session.scalar(
        select(UserSettings.auto_share_team_id).where(UserSettings.user_id == user.id)
    )
    if team_id is None or membership(session, user.id, team_id) is None:
        return None
    team = session.get(Team, team_id)
    return team_id if team is not None and not team.is_personal else None


def apply_share_default(session: Session, meeting: Meeting) -> None:
    """When a meeting's notes are first written: share it with the owner's team if the team's
    default says so (spec 8.2, decision D3). Read NOW, never when the bot joined and never again
    for a rewrite, so a calendar rule of "all meetings" cannot publish a call by itself and an
    owner's "unshare" stays unshared.

    Only the meetings of the team's owner and admins are covered: an admin switching the default
    on must not publish a member's calls; a member shares their own with the switch on the
    meeting page.

    A person who chose one team for their new meetings (`auto_share_team`, at most one) is shared
    to THAT team, whatever their role in it: it is their own choice for their own meetings."""
    recorder = session.get(User, meeting.owner_user_id)
    chosen = auto_share_team(session, recorder) if recorder is not None else None
    if chosen is not None:
        meeting.shared_team_id = chosen
        return
    member = default_membership(session, meeting.owner_user_id)
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
    team.is_personal = False  # handed on: an ordinary team (the heir may own a personal one)
    session.flush()
