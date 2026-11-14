"""Who may read which meeting: the one rule (spec 8.2).

`visible_meetings(user)` is the only way any service learns which meetings a caller may read: the
caller's own meetings, plus meetings shared to, or owned by, ANY team the caller belongs to (a
person may belong to several: multi-team design, 2026-11-13).

A meeting a TEAM owns (`owner_team_id`, "moved": stage 2) is read by every member of that team and
by nobody else, the recorder included: the recorder reads it only while they are a member. A
meeting nobody moved is its recorder's, plus the team it is shared to. Everything derived from a
meeting (notes, transcript, translations, exports, decisions, open questions, tasks) is read
through it. Nothing else compares `shared_team_id` or `owner_team_id`: code that needs to ask "is
this meeting the team's" uses `in_team`, `in_team_clause`, `via_team` or `meeting_team_ids` below,
and no route reads `Meeting.owner_user_id == user.id` to decide a *read*.

Reading is not writing. Delete, rename, rewrite, translate, cancel, end and retry belong to the
recorder (`owns`); for a moved meeting they belong to the recorder while still in the owning team
AND to that team's owner and admins. The things that are the recorder's whatever a teammate may
read stay theirs: review items, vocabulary, templates, the calendar connection and the hours cap.

A meeting nobody may read is "no such meeting" (404, never 403), and the platform admin has no
route into team content: `is_admin` is not consulted here.
"""

from dataclasses import dataclass

from sqlalchemy import ColumnElement, Select, and_, or_, select
from sqlalchemy.orm import Session

from app import titles
from app.models import Meeting, TeamMember, User

MANAGER_ROLES = ("owner", "admin")
FORMER_MEMBER = "Former member"


def former_or_name(user: User) -> str:
    """How a recorder is shown to a teammate: their name or the start of their email, or "Former
    member" once the account is deleted (a team keeps the meetings moved to it; the person's name
    and email are gone, `app/deletion.py`)."""
    if user.status == "deleted":
        return FORMER_MEMBER
    return titles.clean_title(user.name, 100) or user.email.split("@")[0]


def _my_team_ids(user: User):
    """A subquery of the ids of every team the caller belongs to (none: it is empty and matches
    nothing, as a null never matches a meeting's null team)."""
    return select(TeamMember.team_id).where(TeamMember.user_id == user.id)


def team_read_clause(user: User) -> ColumnElement[bool]:
    """The part of the rule that goes through a team: shared to, or owned by, one of the caller's
    teams. A shared meeting of an account being deleted is treated as absent (see
    app/deletion.py); a meeting a TEAM owns (stage 2) outlives its recorder, so it has no such
    guard."""
    mine = _my_team_ids(user)
    live_owner = Meeting.owner_user_id.not_in(select(User.id).where(User.status == "deleted"))
    return or_(
        and_(Meeting.shared_team_id.in_(mine), live_owner),
        Meeting.owner_team_id.in_(mine),
    )


def recorder_not_being_deleted() -> ColumnElement[bool]:
    """For the worker's claims and the topics input: a meeting whose recorder's account is being
    deleted is left alone (the purge deletes it), EXCEPT a meeting a team owns that still has a
    live member: it outlives its recorder, whose hollow row stays `status = 'deleted'`
    (`app/deletion.py`), so the team can still have it translated, re-read for tasks and counted in
    the repeat topics. A team with no live member left is going with the account (the purge gives
    the meeting back or deletes it), so nothing more is sent to the model for it meanwhile."""
    live_teams = (
        select(TeamMember.team_id)
        .join(User, User.id == TeamMember.user_id)
        .where(User.status != "deleted")
    )
    return or_(
        Meeting.owner_team_id.in_(live_teams),
        Meeting.owner_user_id.not_in(select(User.id).where(User.status == "deleted")),
    )


def can_read_clause(user: User) -> ColumnElement[bool]:
    """The `where` condition for "this caller may read this meeting", for queries that join
    `meetings` (decisions, open questions): their own meeting that no team owns, or one shared to
    or owned by a team they belong to. A caller with no team sees only their own. The recorder of a
    MOVED meeting is not special: they read it as a member of its team, or not at all."""
    return or_(
        and_(Meeting.owner_user_id == user.id, Meeting.owner_team_id.is_(None)),
        team_read_clause(user),
    )


def visible_meetings(user: User) -> Select[tuple[Meeting]]:
    """`select(Meeting)` limited to what `user` may read. Add filters, ordering and limits."""
    return select(Meeting).where(can_read_clause(user))


@dataclass(frozen=True)
class ActiveTeam:
    """The team a LIST is about (the `X-Team-Id` header, else the person's default team): its id and
    whether it is the caller's own personal team. Made by `team_admin.active_team`."""

    id: int
    personal: bool


def active_team_clause(user: User, active: ActiveTeam) -> ColumnElement[bool]:
    """Which meetings belong on the active team's list: for an ordinary team, those shared to it or
    owned by (moved to) it, whoever recorded them; for the caller's personal team, only those they
    recorded that no team has (neither shared nor moved). It only NARROWS a list: always combine it
    with the read rule (`readable_in`), never use it alone."""
    if active.personal:
        return and_(
            Meeting.owner_user_id == user.id,
            Meeting.shared_team_id.is_(None),
            Meeting.owner_team_id.is_(None),
        )
    return in_team_clause(active.id)


def readable_in(user: User, active: ActiveTeam | None) -> ColumnElement[bool]:
    """The `where` condition for "the caller may read this meeting AND it is on the active team's
    list" (`active` None: every meeting they may read, as before). Meetings, decisions, review
    items and the sidebar counts all filter through this one function: a count equals its list."""
    if active is None:
        return can_read_clause(user)
    return and_(can_read_clause(user), active_team_clause(user, active))


def owns(session: Session, user: User, meeting: Meeting) -> bool:
    """True when `user` may CHANGE the meeting. A meeting nobody moved: only its recorder. A moved
    meeting: a member of the owning team who is its recorder, or the team's owner or admin. Someone
    who left the team (or was removed) changes nothing, even their own recording."""
    if meeting.owner_team_id is None:
        return meeting.owner_user_id == user.id
    role = session.scalar(
        select(TeamMember.role).where(
            TeamMember.team_id == meeting.owner_team_id, TeamMember.user_id == user.id
        )
    )
    if role is None:
        return False
    return meeting.owner_user_id == user.id or role in MANAGER_ROLES


def recorded_by(session: Session, user: User, meeting: Meeting) -> bool:
    """True when `user` recorded the meeting AND may still change it (a moved meeting's recorder
    who left its team does not). For what only the recorder does, even on a team's meeting:
    putting it in their own recurring series."""
    return meeting.owner_user_id == user.id and owns(session, user, meeting)


def via_team(session: Session, user: User, meeting: Meeting) -> bool:
    """True when `user` may read `meeting` THROUGH a team (it is shared to, or owned by, a team they
    belong to), whoever recorded it. The recorder reading their own meeting is not counted: a mail
    that links a teammate to a shared meeting asks this, not `can_read_clause`."""
    return (
        session.scalar(select(Meeting.id).where(Meeting.id == meeting.id, team_read_clause(user)))
        is not None
    )


def meeting_team_ids(meeting: Meeting) -> list[int]:
    """The teams a meeting belongs to (shared to, or owned by): at most two ids, usually one or
    none. For a rule that depends on the meeting's team, such as who may change a question."""
    return [t for t in (meeting.shared_team_id, meeting.owner_team_id) if t is not None]


def in_team(meeting: Meeting, team_id: int) -> bool:
    """True when the meeting is shared to, or owned by, this team."""
    return team_id in meeting_team_ids(meeting)


def in_team_clause(team_id: int) -> ColumnElement[bool]:
    """`in_team` as a `where` condition."""
    return or_(Meeting.shared_team_id == team_id, Meeting.owner_team_id == team_id)


def is_shared(meeting: Meeting) -> bool:
    """True when the meeting is out of its recorder's private space (shared to, or owned by, a
    team): the flag the lists and the Ask answers show."""
    return bool(meeting_team_ids(meeting))
