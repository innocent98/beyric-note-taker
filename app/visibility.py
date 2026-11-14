"""Who may read which meeting: the one rule (spec 8.2).

`visible_meetings(user)` is the only way any service learns which meetings a caller may read: the
caller's own meetings, plus meetings shared to, or owned by, ANY team the caller belongs to (a
person may belong to several: multi-team design, 2026-11-13). `owner_team_id` is filled from stage 2
on (a meeting moved to a team); until then only `shared_team_id` can match.

NOT YET DONE for team-owned meetings (stage 2 prerequisites, see docs/sop/2026-11-13-multi-team-
stage-1.md): the clause below only lets a team's members READ a moved meeting. Spec 6.4 also says
the recorder keeps reading it only while they are a member of the owning team, and that the team
(not only the recorder) may change it. So `can_read_clause` (its `owner_user_id == user.id`
branch) and `owns()` must change in stage 2, and `deletion.py` must stop deleting moved meetings.
Until then nothing sets `owner_team_id`, so none of this can happen. Everything derived from
a meeting (notes, transcript, translations, exports, decisions, open questions, tasks) is read
through it. Nothing else compares `shared_team_id` or `owner_team_id`: code that needs to ask "is
this meeting the team's" uses `in_team`, `in_team_clause`, `via_team` or `meeting_team_ids` below,
and no route reads `Meeting.owner_user_id == user.id` to decide a *read*.

Reading is not writing. Delete, rename, rewrite, translate, cancel, end and retry stay with the
owner (`owns`), and so do the things that are the owner's whatever a teammate may read: review
items, vocabulary, templates, the calendar connection and the hours cap.

A meeting nobody may read is "no such meeting" (404, never 403), and the platform admin has no
route into team content: `is_admin` is not consulted here.
"""

from sqlalchemy import ColumnElement, Select, and_, or_, select
from sqlalchemy.orm import Session

from app.models import Meeting, TeamMember, User


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


def can_read_clause(user: User) -> ColumnElement[bool]:
    """The `where` condition for "this caller may read this meeting", for queries that join
    `meetings` (decisions, open questions). A caller with no team sees only their own."""
    return or_(Meeting.owner_user_id == user.id, team_read_clause(user))


def visible_meetings(user: User) -> Select[tuple[Meeting]]:
    """`select(Meeting)` limited to what `user` may read. Add filters, ordering and limits."""
    return select(Meeting).where(can_read_clause(user))


def owns(user: User, meeting: Meeting) -> bool:
    """True when `user` recorded the meeting: the only one who may change it."""
    return meeting.owner_user_id == user.id


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
