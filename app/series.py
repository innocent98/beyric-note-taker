"""Series: a team's recurring meetings, their agenda and what is carried from one to the next
(spec 7). Everything the Series page, the Recurring cards and the meeting page's Series row need
that is not a request or a mail.

**Where a series comes from.** The calendar's recurring-event id: when a calendar meeting's notes
are written (`link_from_calendar`, called by the pipeline) and, for the events the scheduler will
join, by an hourly sync (`sync_from_joins`) so a card and its reminders exist before the first
recording. Or a person attaches a meeting by hand (`attach`). A series belongs to a TEAM: two
teammates who both have the same recurring event share one series (unique on team and recurring
id).

**Who sees what.** The title and settings are the team's. Every list and count inside a series is
filtered by what the viewer may read: tasks through `tasks.visible`, meetings and their open
questions through `visibility.can_read_clause`. A teammate's private meeting is not a past
meeting, its tasks are not "unfinished", its questions are not carried, for anyone else. A series
of another team is "no such series" (404). This module never compares `shared_team_id` itself.

**Who changes what.** Meeting links: the meeting's owner. Questions: the meeting's owner, or an
owner or admin of the team who can read the meeting. Agenda lines: any member adds; the author or
a manager removes. Renaming a series: its creator or a manager.

This module imports neither the services nor the API (the pipeline imports it).
"""

import logging
import re
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, date, datetime

from sqlalchemy import func, or_, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app import tasks, teams, titles
from app.models import (
    CalendarEvent,
    Meeting,
    OpenQuestion,
    ScheduledJoin,
    Series,
    SeriesAgendaItem,
    Task,
    TeamMember,
    User,
)
from app.notes_document import parse_transcript
from app.visibility import can_read_clause, owns

logger = logging.getLogger(__name__)

MAX_TITLE_CHARS = 200
MAX_AGENDA_CHARS = 200
MAX_AGENDA_ITEMS = 30
MAX_SERIES_PER_TEAM = 100
RECENT_MEETINGS = 3  # "attended any of the last three meetings" (decision D16)
PAGE_LIST_LIMIT = 100
SYNC_BATCH = 200
MANAGER_ROLES = ("owner", "admin")
UNTITLED = "Recurring meeting"


class SeriesRefused(Exception):
    """A change that cannot happen; `status`, `code` and `message` are safe to show."""

    def __init__(self, status: int, code: str, message: str) -> None:
        super().__init__(message)
        self.status, self.code, self.message = status, code, message


FORBIDDEN = "You can't do that here. Ask an owner or admin of your team."


def _forbidden() -> SeriesRefused:
    return SeriesRefused(403, "team_forbidden", FORBIDDEN)


def _clean(text: str | None, limit: int) -> str:
    """One plain line: no markup-looking control characters, collapsed white space."""
    return " ".join(titles.clean_title(text, limit).split())


# ---- finding a series ----


def team_id_of(session: Session, user: User) -> int:
    return teams.ensure_team(session, user).id


def membership(session: Session, user: User) -> TeamMember | None:
    return session.scalars(select(TeamMember).where(TeamMember.user_id == user.id)).first()


def is_manager(session: Session, user: User, team_id: int) -> bool:
    member = membership(session, user)
    return member is not None and member.team_id == team_id and member.role in MANAGER_ROLES


def _seen_by(user: User):
    """A series the caller's team owns is shown to the caller only when it is not someone else's
    private calendar event: it was made by hand, or the caller made it, or the caller has a
    meeting of it they may read, or its recurring event is on the caller's own calendar. Else the
    title, cadence and next date of a teammate's private recurring call (a 1:1) would be published
    to the team (spec 8.2: private by default)."""
    readable = select(Meeting.id).where(Meeting.series_id == Series.id, can_read_clause(user))
    own_event = select(CalendarEvent.id).where(
        CalendarEvent.user_id == user.id,
        CalendarEvent.recurring_event_id == Series.calendar_recurring_id,
    )
    return or_(
        Series.calendar_recurring_id.is_(None),
        Series.owner_user_id == user.id,
        readable.exists(),
        own_event.exists(),
    )


def get(session: Session, user: User, series_id: int) -> Series | None:
    """The series, only when it belongs to the caller's team and the caller may see it: anything
    else is None (404)."""
    member = membership(session, user)
    if member is None:
        return None
    return session.scalars(
        select(Series).where(
            Series.id == series_id, Series.team_id == member.team_id, _seen_by(user)
        )
    ).first()


def list_for(session: Session, user: User) -> list[Series]:
    member = membership(session, user)
    if member is None:
        return []
    return list(
        session.scalars(
            select(Series)
            .where(Series.team_id == member.team_id, _seen_by(user))
            .order_by(Series.title, Series.id)
            .limit(MAX_SERIES_PER_TEAM)
        )
    )


def of_meeting(session: Session, user: User, meeting: Meeting) -> Series | None:
    """The series a meeting the caller may read belongs to, if the caller's team owns it. A
    meeting whose series is another team's (its owner left that team) shows no series."""
    if meeting.series_id is None:
        return None
    return get(session, user, meeting.series_id)


# ---- making and linking ----


def _get_or_create(
    session: Session,
    team_id: int,
    recurring_id: str,
    title: str,
    platform: str | None,
    owner_id: int,
) -> Series:
    """One series per team and recurring id, however many workers ask at once."""
    session.execute(
        insert(Series)
        .values(
            team_id=team_id,
            calendar_recurring_id=recurring_id,
            title=title,
            platform=platform,
            owner_user_id=owner_id,
        )
        .on_conflict_do_nothing(
            index_elements=["team_id", "calendar_recurring_id"],
            index_where=Series.calendar_recurring_id.is_not(None),
        )
    )
    return session.scalars(
        select(Series).where(
            Series.team_id == team_id, Series.calendar_recurring_id == recurring_id
        )
    ).one()


def _series_title(*candidates: str | None) -> str:
    for text in candidates:
        title = _clean(titles.event_title(text), MAX_TITLE_CHARS)
        if title:
            return title
    return UNTITLED


def link_from_calendar(session: Session, meeting: Meeting) -> Series | None:
    """Put a calendar meeting into the series of its recurring event, making the series when the
    team has none yet. Idempotent. The event is found through the join that made the meeting
    (`scheduled_joins.meeting_id`); once the event row has gone (two days after it ends) a meeting
    that was not linked by then stays unlinked, which is why this runs when the notes are
    written. A meeting that is already in a series is left alone: a person's choice stands."""
    if meeting.series_id is not None:
        return session.get(Series, meeting.series_id)
    if meeting.source != "calendar":
        return None
    row = session.execute(
        select(CalendarEvent.recurring_event_id, CalendarEvent.title, CalendarEvent.platform)
        .join(ScheduledJoin, ScheduledJoin.calendar_event_id == CalendarEvent.id)
        .where(
            ScheduledJoin.meeting_id == meeting.id, ScheduledJoin.user_id == meeting.owner_user_id
        )
        .limit(1)
    ).first()
    if row is None or not row.recurring_event_id:
        return None
    owner = session.get(User, meeting.owner_user_id)
    if owner is None or owner.status != "active":
        return None
    team_id = team_id_of(session, owner)
    series = _get_or_create(
        session,
        team_id,
        row.recurring_event_id,
        _series_title(row.title, meeting.title),
        row.platform or meeting.platform,
        owner.id,
    )
    join(session, meeting, series, consume=True)
    return series


def join(session: Session, meeting: Meeting, series: Series, *, consume: bool = False) -> None:
    """Make `meeting` a meeting of `series`. With `consume` (only when the pipeline links a
    recorded meeting) the agenda lines typed before it began are consumed by it (spec 7). A hand
    attach never consumes: it would let any member clear lines they may not remove. In the
    caller's transaction; the caller has checked who may."""
    first = meeting.series_id != series.id
    meeting.series_id = series.id
    session.flush()
    if first and consume:
        session.execute(
            update(SeriesAgendaItem)
            .where(
                SeriesAgendaItem.series_id == series.id,
                SeriesAgendaItem.consumed_at.is_(None),
                SeriesAgendaItem.created_at <= (meeting.created_at or datetime.now(UTC)),
            )
            .values(consumed_by_meeting_id=meeting.id, consumed_at=datetime.now(UTC))
        )


def sync_from_joins(session: Session, now: datetime) -> int:
    """Make the series of the recurring events the scheduler will join, so the Recurring cards
    and the reminders exist before the first recording. Only events with a join that is not
    skipped or cancelled: a recurring lunch nobody records gets no series. Returns how many were
    made. Never reads the calendar; the rows are what the last sync stored."""
    rows = session.execute(
        select(
            CalendarEvent.user_id,
            CalendarEvent.recurring_event_id,
            func.min(CalendarEvent.title),
            func.min(CalendarEvent.platform),
        )
        .join(ScheduledJoin, ScheduledJoin.calendar_event_id == CalendarEvent.id)
        .join(User, User.id == CalendarEvent.user_id)
        .where(
            CalendarEvent.recurring_event_id.is_not(None),
            CalendarEvent.status != "cancelled",
            CalendarEvent.all_day.is_(False),
            ScheduledJoin.state.not_in(("skipped", "cancelled")),
            User.status == "active",
        )
        .group_by(CalendarEvent.user_id, CalendarEvent.recurring_event_id)
        .limit(SYNC_BATCH)
    ).all()
    made = 0
    for user_id, recurring_id, title, platform in rows:
        user = session.get(User, user_id)
        team_id = team_id_of(session, user)
        exists = session.scalar(
            select(Series.id).where(
                Series.team_id == team_id, Series.calendar_recurring_id == recurring_id
            )
        )
        if exists is not None:
            continue
        if (
            session.scalar(
                select(func.count()).select_from(Series).where(Series.team_id == team_id)
            )
            >= MAX_SERIES_PER_TEAM
        ):
            continue
        _get_or_create(session, team_id, recurring_id, _series_title(title), platform, user_id)
        made += 1
    session.commit()
    return made


def create(session: Session, user: User, title: str) -> Series:
    """A recurring meeting made by hand (no calendar id), in the caller's team."""
    clean = _clean(title, MAX_TITLE_CHARS)
    if not clean:
        raise SeriesRefused(422, "invalid_title", "Give the recurring meeting a name.")
    team_id = team_id_of(session, user)
    count = session.scalar(
        select(func.count()).select_from(Series).where(Series.team_id == team_id)
    )
    if count >= MAX_SERIES_PER_TEAM:
        raise SeriesRefused(409, "series_limit", "Your team has as many recurring meetings as fit.")
    series = Series(team_id=team_id, owner_user_id=user.id, title=clean)
    session.add(series)
    session.flush()
    return series


def rename(session: Session, user: User, series: Series, title: str) -> None:
    clean = _clean(title, MAX_TITLE_CHARS)
    if not clean:
        raise SeriesRefused(422, "invalid_title", "Give the recurring meeting a name.")
    if series.owner_user_id != user.id and not is_manager(session, user, series.team_id):
        raise _forbidden()
    series.title = clean


def attach(session: Session, user: User, meeting: Meeting, series_id: int | None) -> Series | None:
    """Put the caller's own meeting into a series of their team, or take it out (`None`). Only the
    meeting's owner may (a teammate who can read it may not move it). 404 for a series that is
    not the caller's team's."""
    if not owns(user, meeting):
        raise SeriesRefused(404, "not_found", "No such meeting.")
    if series_id is None:
        meeting.series_id = None
        session.flush()
        return None
    series = get(session, user, series_id)
    if series is None:
        raise SeriesRefused(404, "not_found", "No such recurring meeting.")
    join(session, meeting, series)
    return series


def reused_code(session: Session, meeting: Meeting) -> bool:
    """Whether the owner has another meeting with this meeting's link code (same platform and
    code): the sign of a call that repeats, which is when the page may offer "Make this a
    recurring meeting?" (spec 7, D12). False for a meeting already in a series."""
    if meeting.series_id is not None:
        return False
    return (
        session.scalar(
            select(func.count())
            .select_from(Meeting)
            .where(
                Meeting.owner_user_id == meeting.owner_user_id,
                Meeting.platform == meeting.platform,
                Meeting.native_id == meeting.native_id,
                Meeting.id != meeting.id,
            )
        )
        > 0
    )


def make_from_reused_link(session: Session, user: User, meeting: Meeting) -> Series:
    """Make a recurring meeting from a link that was used again: named after the meeting, with this
    meeting and the caller's other meetings of the same code that are in no series. Only the
    meeting's owner may (404); 409 `not_reused` when the code was not used before."""
    if not owns(user, meeting):
        raise SeriesRefused(404, "not_found", "No such meeting.")
    if not reused_code(session, meeting):
        raise SeriesRefused(
            409,
            "not_reused",
            "This link has not been used for another meeting, so it is not recurring.",
        )
    made = create(session, user, titles.meeting_name(meeting))
    others = session.scalars(
        select(Meeting).where(
            Meeting.owner_user_id == user.id,
            Meeting.platform == meeting.platform,
            Meeting.native_id == meeting.native_id,
            Meeting.series_id.is_(None),
        )
    )
    for row in others:
        row.series_id = made.id
    session.flush()
    return made


def forget_meetings_of(session: Session, user_id: int, team_id: int) -> None:
    """A person leaves a team: their meetings leave that team's series (they never move, and the
    team must not keep a pointer into meetings it can no longer read)."""
    in_team = select(Series.id).where(Series.team_id == team_id)
    session.execute(
        update(Meeting)
        .where(Meeting.owner_user_id == user_id, Meeting.series_id.in_(in_team))
        .values(series_id=None)
    )


def move_team_series(session: Session, old_team_id: int, new_team_id: int) -> None:
    """A team of one is dissolved into another team: its series go with it. When the new team
    already has the series of the same recurring event, the meetings and agenda lines move into
    that one and the duplicate goes."""
    for old in list(session.scalars(select(Series).where(Series.team_id == old_team_id))):
        twin = None
        if old.calendar_recurring_id is not None:
            twin = session.scalars(
                select(Series).where(
                    Series.team_id == new_team_id,
                    Series.calendar_recurring_id == old.calendar_recurring_id,
                )
            ).first()
        if twin is None:
            old.team_id = new_team_id
            continue
        session.execute(
            update(Meeting).where(Meeting.series_id == old.id).values(series_id=twin.id)
        )
        session.execute(
            update(SeriesAgendaItem)
            .where(SeriesAgendaItem.series_id == old.id)
            .values(series_id=twin.id)
        )
        session.flush()
        session.delete(old)
    session.flush()


# ---- the series' meetings, attendance ----


def meetings_of(
    session: Session, series: Series, viewer: User | None, *, limit: int | None = None
) -> list[Meeting]:
    """The series' ready meetings, newest first. With a viewer: only those they may read. With
    none (the mail sender deciding who attended): all, which is fine for names and never for
    content."""
    query = (
        select(Meeting)
        .where(Meeting.series_id == series.id, Meeting.status == "ready")
        .order_by(Meeting.created_at.desc(), Meeting.id.desc())
    )
    if viewer is not None:
        query = query.where(can_read_clause(viewer))
    if limit is not None:
        query = query.limit(limit)
    return list(session.scalars(query))


def speaker_names(meeting: Meeting) -> list[str]:
    if not meeting.speaker_transcript:
        return []
    names: list[str] = []
    for turn in parse_transcript(meeting.speaker_transcript):
        if turn.speaker and turn.speaker not in names:
            names.append(turn.speaker)
    return names


@dataclass(frozen=True)
class Attendee:
    """A person who spoke in a recent meeting. `user_id` and `email` exist only for a teammate
    the name matched (an address is never stored for anyone else)."""

    name: str
    user_id: int | None = None
    email: str | None = None


def attendees(
    session: Session, series: Series, viewer: User | None, *, limit: int = RECENT_MEETINGS
) -> list[Attendee]:
    """Who attended the last `limit` meetings: the people named in their speaker labels, matched
    against the team's members, plus each of those meetings' owners (the account whose bot
    recorded may not have spoken). A matched teammate appears once, under their own name."""
    members = tasks.members_of(session, series.team_id)
    by_id = {m.user_id: m for m in members}
    seen_users: set[int] = set()
    seen_names: set[str] = set()
    out: list[Attendee] = []

    def add_member(user_id: int) -> None:
        if user_id in seen_users or user_id not in by_id:
            return
        seen_users.add(user_id)
        member = by_id[user_id]
        out.append(Attendee(member.display_name, member.user_id, member.email))

    for meeting in meetings_of(session, series, viewer, limit=limit):
        add_member(meeting.owner_user_id)
        for name in speaker_names(meeting):
            matched = tasks.match_owner(name, members)
            if matched is not None:
                add_member(matched)
                continue
            key = name.casefold()
            if key not in seen_names:
                seen_names.add(key)
                out.append(Attendee(_clean(name, 100) or "Guest"))
    return out


def recipients(session: Session, series: Series) -> list[Attendee]:
    """The teammates mail about this series may go to: attendees of the last three meetings who
    are matched to an ACTIVE member of the series' team and have not switched series mail off.
    No outside address is ever included."""
    from app.models import UserSettings

    found = [a for a in attendees(session, series, None) if a.user_id is not None]
    out = []
    for attendee in found:
        user = session.get(User, attendee.user_id)
        if user is None or user.status != "active":
            continue
        wanted = session.scalar(
            select(UserSettings.notify_reminders).where(UserSettings.user_id == user.id)
        )
        if wanted is False:
            continue
        out.append(attendee)
    return out


# ---- carry-over: tasks and questions ----


def _series_meeting_ids(series: Series):
    return select(Meeting.id).where(Meeting.series_id == series.id)


def unfinished_tasks(session: Session, viewer: User, series: Series) -> list[Task]:
    """Tasks of the series' meetings that are not done, that the viewer may see, oldest first."""
    return list(
        session.scalars(
            select(Task)
            .where(
                tasks.visible(viewer),
                Task.meeting_id.in_(_series_meeting_ids(series)),
                Task.status != "done",
            )
            .order_by(Task.id)
            .limit(PAGE_LIST_LIMIT)
        )
    )


def finished_since(
    session: Session, viewer: User, series: Series, since: date, zone: str | None
) -> list[Task]:
    """Tasks of the series' meetings done on or after `since` (the last meeting's date), in the
    viewer's time zone, newest first."""
    from app import timezones

    rows = session.scalars(
        select(Task)
        .where(
            tasks.visible(viewer),
            Task.meeting_id.in_(_series_meeting_ids(series)),
            Task.status == "done",
            Task.done_at.is_not(None),
        )
        .order_by(Task.done_at.desc())
        .limit(PAGE_LIST_LIMIT)
    )
    return [t for t in rows if timezones.localise(t.done_at, zone)[0].date() >= since]


def carried_questions(
    session: Session, viewer: User, series: Series
) -> list[tuple[OpenQuestion, Meeting]]:
    """The questions still open and on the next agenda, from meetings the viewer may read, newest
    meeting first. A question asked again in a later meeting (same words) shows once, from the
    newest."""
    rows = session.execute(
        select(OpenQuestion, Meeting)
        .join(Meeting, Meeting.id == OpenQuestion.meeting_id)
        .where(
            Meeting.series_id == series.id,
            can_read_clause(viewer),
            OpenQuestion.carry.is_(True),
            OpenQuestion.resolved_at.is_(None),
        )
        .order_by(Meeting.created_at.desc(), Meeting.id.desc(), OpenQuestion.position)
        .limit(PAGE_LIST_LIMIT)
    ).all()
    seen: set[str] = set()
    out = []
    for question, meeting in rows:
        if question.fingerprint in seen:
            continue
        seen.add(question.fingerprint)
        out.append((question, meeting))
    return out


def counts_for(
    session: Session, viewer: User, series_ids: Iterable[int]
) -> dict[int, tuple[int, int]]:
    """(unfinished tasks, carried questions) per series for the Recurring cards, in two queries,
    each filtered to what the viewer may read."""
    ids = list(series_ids)
    if not ids:
        return {}
    task_counts = dict(
        session.execute(
            select(Meeting.series_id, func.count(Task.id))
            .join(Task, Task.meeting_id == Meeting.id)
            .where(tasks.visible(viewer), Meeting.series_id.in_(ids), Task.status != "done")
            .group_by(Meeting.series_id)
        ).all()
    )
    question_counts = dict(
        session.execute(
            select(Meeting.series_id, func.count(func.distinct(OpenQuestion.fingerprint)))
            .join(OpenQuestion, OpenQuestion.meeting_id == Meeting.id)
            .where(
                can_read_clause(viewer),
                Meeting.series_id.in_(ids),
                OpenQuestion.carry.is_(True),
                OpenQuestion.resolved_at.is_(None),
            )
            .group_by(Meeting.series_id)
        ).all()
    )
    return {i: (task_counts.get(i, 0), question_counts.get(i, 0)) for i in ids}


def question_for(
    session: Session, viewer: User, question_id: int
) -> tuple[OpenQuestion, Meeting] | None:
    """A question of a meeting the viewer may read, else None (404)."""
    return session.execute(
        select(OpenQuestion, Meeting)
        .join(Meeting, Meeting.id == OpenQuestion.meeting_id)
        .where(OpenQuestion.id == question_id, can_read_clause(viewer))
    ).first()


def can_change_question(session: Session, viewer: User, meeting: Meeting) -> bool:
    """The meeting's owner, or an owner or admin of the team the meeting is shared with."""
    if owns(viewer, meeting):
        return True
    return meeting.shared_team_id is not None and is_manager(
        session, viewer, meeting.shared_team_id
    )


def set_question(
    session: Session,
    viewer: User,
    question: OpenQuestion,
    meeting: Meeting,
    *,
    carry: bool | None,
    resolved: bool | None,
    now: datetime,
) -> None:
    """Change "On next agenda" and/or "Resolved". Turning `carry` on also reopens a resolved
    question (the prototype's toggle does); resolving applies to the same words in the series'
    other meetings the viewer may change, so a question raised twice does not come back. A
    question of a meeting outside a series cannot be carried (409 `not_in_series`)."""
    if not can_change_question(session, viewer, meeting):
        raise _forbidden()
    if carry is not None and meeting.series_id is None:
        raise SeriesRefused(
            409, "not_in_series", "Only a question from a recurring meeting can be carried."
        )
    if carry is not None:
        question.carry = carry
        if carry and resolved is None:
            resolved = False
    if resolved is True:
        _resolve(session, viewer, question, meeting, now)
    elif resolved is False:
        question.resolved_at, question.resolved_by = None, None
    session.flush()


def _resolve(
    session: Session, viewer: User, question: OpenQuestion, meeting: Meeting, now: datetime
) -> None:
    twins = [question]
    if meeting.series_id is not None:
        rows = session.execute(
            select(OpenQuestion, Meeting)
            .join(Meeting, Meeting.id == OpenQuestion.meeting_id)
            .where(
                Meeting.series_id == meeting.series_id,
                OpenQuestion.fingerprint == question.fingerprint,
                OpenQuestion.id != question.id,
                OpenQuestion.resolved_at.is_(None),
                can_read_clause(viewer),
            )
        ).all()
        twins += [q for q, m in rows if can_change_question(session, viewer, m)]
    for row in twins:
        row.resolved_at, row.resolved_by = now, viewer.id


# ---- agenda ----


def agenda_items(session: Session, series: Series) -> list[SeriesAgendaItem]:
    """The lines waiting for the next meeting, in the order they were added."""
    return list(
        session.scalars(
            select(SeriesAgendaItem)
            .where(
                SeriesAgendaItem.series_id == series.id,
                SeriesAgendaItem.consumed_at.is_(None),
            )
            .order_by(SeriesAgendaItem.position, SeriesAgendaItem.id)
        )
    )


_LINK = re.compile(r"https?://|www\.", re.IGNORECASE)


def add_agenda_item(session: Session, user: User, series: Series, text: str) -> SeriesAgendaItem:
    """Add a plain-text line. Any member of the team may. At most 30 waiting lines; no web address
    (a line goes into mail to teammates as text, and nobody should be able to plant a link)."""
    clean = _clean(text, MAX_AGENDA_CHARS)
    if not clean:
        raise SeriesRefused(422, "invalid_agenda_item", "Type what should be discussed.")
    if _LINK.search(clean):
        raise SeriesRefused(422, "invalid_agenda_item", "Write the item without a web address.")
    waiting = session.scalar(
        select(func.count())
        .select_from(SeriesAgendaItem)
        .where(
            SeriesAgendaItem.series_id == series.id,
            SeriesAgendaItem.consumed_at.is_(None),
        )
    )
    if waiting >= MAX_AGENDA_ITEMS:
        raise SeriesRefused(
            409, "agenda_full", "The agenda has as many items as fit. Remove one first."
        )
    last = session.scalar(
        select(func.max(SeriesAgendaItem.position)).where(SeriesAgendaItem.series_id == series.id)
    )
    item = SeriesAgendaItem(
        series_id=series.id, text=clean, position=(last or 0) + 1, created_by=user.id
    )
    session.add(item)
    session.flush()
    return item


def remove_agenda_item(session: Session, user: User, series: Series, item_id: int) -> bool:
    """Remove a waiting line: its author, or an owner or admin. False when there is no such line
    in this series."""
    item = session.scalars(
        select(SeriesAgendaItem).where(
            SeriesAgendaItem.id == item_id,
            SeriesAgendaItem.series_id == series.id,
            SeriesAgendaItem.consumed_at.is_(None),
        )
    ).first()
    if item is None:
        return False
    if item.created_by != user.id and not is_manager(session, user, series.team_id):
        raise _forbidden()
    session.delete(item)
    session.flush()
    return True


def last_meeting_day(
    session: Session, viewer: User, series: Series, zone: str | None
) -> date | None:
    from app import timezones

    meeting = next(iter(meetings_of(session, series, viewer, limit=1)), None)
    return timezones.localise(meeting.created_at, zone)[0].date() if meeting else None
