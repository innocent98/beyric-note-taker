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

from app import birth_workspace, titles
from app.models import Meeting, Team, TeamMember, User

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
    """The workspace a LIST is about (the `X-Team-Id` header, else the person's personal
    workspace): its id and whether it is the caller's own personal team. Made by
    `team_admin.active_team`."""

    id: int
    personal: bool


def in_workspace(user: User, team: ActiveTeam | Team) -> ColumnElement[bool]:
    """The one `where` condition for "the caller may read this meeting AND it lives in this
    workspace" (workspaces design, sections 3 and 12.1). `team` is the active workspace (a `Team`
    or the `ActiveTeam` of a list). The workspace is the meeting's CONTAINER (`workspace_team_id`);
    who may read it is still `can_read_clause`: the header only narrows, it never widens, so a
    teammate's unshared meeting in the team's workspace is not listed for anyone but its recorder.
    Meetings, search, Ask, decisions, review items, tasks' meetings and every count and badge
    filter through this one function: a count equals its list, and nothing of another workspace is
    ever on screen."""
    return and_(can_read_clause(user), Meeting.workspace_team_id == team.id)


def active_or_default(session: Session, user: User, active: ActiveTeam | None) -> ActiveTeam:
    """The workspace a service-level list narrows by. A route passes the `ActiveTeam` it got from
    `team_admin.active_team`; a caller that names none (an old page, a script) gets the caller's
    default workspace, never "everything the caller may read"."""
    if active is not None:
        return active
    team_id = birth_workspace.default_workspace_id(session.connection(), user.id)
    team = session.get(Team, team_id)
    return ActiveTeam(id=team_id, personal=team.is_personal and team.owner_user_id == user.id)


def readable_in(user: User, active: ActiveTeam) -> ColumnElement[bool]:
    """`in_workspace` for the `ActiveTeam` a list was given. There is no `None` any more: every
    list is about one workspace (call `active_or_default` first when you may have none)."""
    return in_workspace(user, active)


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


def in_meeting_workspace(session: Session, user: User, meeting: Meeting) -> bool:
    """True while `user` is a current member of the workspace the meeting is filed in. What a
    meeting is written with (its workspace's templates and vocabulary) is read for the person who
    asks only if they still belong to that workspace: a recorder who left, or anyone who never was
    in it, gets nothing of it, whatever they may otherwise do with the meeting."""
    return (
        session.scalar(
            select(TeamMember.id).where(
                TeamMember.team_id == meeting.workspace_team_id, TeamMember.user_id == user.id
            )
        )
        is not None
    )


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


def scope_team_of(
    session: Session, meeting: Meeting, *, personal_is_private: bool = True
) -> int | None:
    """The ordinary team a meeting belongs to for a recurring meeting's row ("scope"): the team that
    owns it, else the team it is shared to, else the team's workspace it was recorded into (a
    private meeting kept in a team's workspace stays in that team's scope); None for a meeting
    filed in its recorder's own personal workspace, which is their personal scope
    (docs/superpowers/specs/2026-11-14-series-follow-meetings.md section 3). A meeting "shared" to
    its recorder's OWN personal team is private: nobody else is in that team.
    `personal_is_private=False` is the flag-off reading, the raw columns, as before.
    `series_scope_clause` is the same rule as a query: change them together."""
    if meeting.owner_team_id is not None:
        return meeting.owner_team_id
    shared = meeting.shared_team_id
    if not personal_is_private:
        return shared
    home = shared if shared is not None else meeting.workspace_team_id
    own_personal = session.scalar(
        select(Team.id).where(
            Team.id == home,
            Team.is_personal.is_(True),
            Team.owner_user_id == meeting.owner_user_id,
        )
    )
    return None if own_personal is not None else home


def series_scope_clause(team: Team) -> ColumnElement[bool]:
    """`where` condition: the meetings whose scope is `team`. An ordinary team: those shared to or
    owned by it. A personal team: those its owner recorded that no other team has (not shared to
    another team, not moved; one shared to this personal team itself is private). Narrows only:
    always combine with `can_read_clause` for a reader. A series row of this team may hold exactly
    these meetings, and no others. A private meeting filed in a team's workspace (workspaces
    design) is in that team's scope, not in its recorder's personal one."""
    if team.is_personal:
        return and_(
            Meeting.owner_user_id == team.owner_user_id,
            or_(Meeting.shared_team_id.is_(None), Meeting.shared_team_id == team.id),
            Meeting.owner_team_id.is_(None),
            Meeting.workspace_team_id == team.id,
        )
    return or_(in_team_clause(team.id), Meeting.workspace_team_id == team.id)


def is_shared(meeting: Meeting) -> bool:
    """True when the meeting is out of its recorder's private space (shared to, or owned by, a
    team): the flag the lists and the Ask answers show."""
    return bool(meeting_team_ids(meeting))
