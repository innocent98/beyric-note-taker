"""Who may read which meeting: the one rule (spec 8.2).

`visible_meetings(user)` is the only way any service learns which meetings a caller may read: the
caller's own meetings, plus meetings whose `shared_team_id` is the caller's team. Everything
derived from a meeting (notes, transcript, translations, exports, decisions, open questions,
tasks) is read through it. Nothing else compares `shared_team_id`, and no route reads
`Meeting.owner_user_id == user.id` to decide a *read*.

Reading is not writing. Delete, rename, rewrite, translate, cancel, end and retry stay with the
owner (`owns`), and so do the things that are the owner's whatever a teammate may read: review
items, vocabulary, templates, the calendar connection and the hours cap.

A meeting nobody may read is "no such meeting" (404, never 403), and the platform admin has no
route into team content: `is_admin` is not consulted here.
"""

from sqlalchemy import ColumnElement, Select, and_, or_, select

from app.models import Meeting, TeamMember, User


def can_read_clause(user: User) -> ColumnElement[bool]:
    """The `where` condition for "this caller may read this meeting", for queries that join
    `meetings` (decisions, open questions). A caller with no team sees only their own."""
    their_team = select(TeamMember.team_id).where(TeamMember.user_id == user.id).scalar_subquery()
    # A shared meeting of an account being deleted is treated as absent (see app/deletion.py).
    live_owner = Meeting.owner_user_id.not_in(select(User.id).where(User.status == "deleted"))
    return or_(
        Meeting.owner_user_id == user.id,
        and_(Meeting.shared_team_id == their_team, live_owner),
    )


def visible_meetings(user: User) -> Select[tuple[Meeting]]:
    """`select(Meeting)` limited to what `user` may read. Add filters, ordering and limits."""
    return select(Meeting).where(can_read_clause(user))


def owns(user: User, meeting: Meeting) -> bool:
    """True when `user` recorded the meeting: the only one who may change it."""
    return meeting.owner_user_id == user.id
