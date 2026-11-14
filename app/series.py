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

**Follow the meetings (`SERIES_FOLLOW_MEETINGS`).** With the flag on (`follow_enabled`) a row of a
team holds exactly the meetings whose SCOPE is that team (`visibility.series_scope_clause`: shared
to or owned by it; for a personal team, its owner's private ones), one row per recurring event per
team scope. `follow_meeting` moves a meeting to the row of its scope when it changes team. Off,
none of that runs and a row is the recorder's default team's, as it always was.

This module imports neither the services nor the API (the pipeline imports it).
"""

import logging
import re
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, date, datetime

from sqlalchemy import and_, func, or_, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app import tasks, teams, titles
from app.config import get_settings
from app.models import (
    CalendarEvent,
    Meeting,
    OpenQuestion,
    Project,
    ScheduledJoin,
    Series,
    SeriesAgendaItem,
    SeriesTopic,
    Task,
    TeamMember,
    User,
)
from app.notes_document import parse_transcript
from app.visibility import (
    can_read_clause,
    meeting_team_ids,
    owns,
    recorded_by,
    scope_team_of,
    visible_meetings,
)

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
    """The team a series is made in and read from: the person's default team (their only ordinary
    team, else their personal one: `teams.default_membership`)."""
    return teams.ensure_team(session, user).id


def follow_enabled() -> bool:
    """`SERIES_FOLLOW_MEETINGS` and `MULTI_TEAM_ENABLED`, both on: a series row then belongs to a
    team scope and meetings follow it. Read on each call (the settings are cached), so a test may
    replace this function."""
    return get_settings().series_follow_meetings and teams.multi_team_enabled()


def scope_team_id(session: Session, recorder: User, meeting: Meeting, *, follow: bool) -> int:
    """The team a meeting's series row belongs to: the team that owns it, else the team it is
    shared to (`visibility.scope_team_of`), else for a private meeting the recorder's PERSONAL team
    (`follow`) or, as before multi-team, their default team. In the caller's transaction (it may
    make a personal team)."""
    home = scope_team_of(meeting)
    if home is not None:
        return home
    if follow:
        return teams.ensure_personal_team(session, recorder).id
    return team_id_of(session, recorder)


def membership(session: Session, user: User, team_id: int | None = None) -> TeamMember | None:
    return teams.membership(session, user.id, team_id)


def is_manager(session: Session, user: User, team_id: int) -> bool:
    member = membership(session, user, team_id)
    return member is not None and member.role in MANAGER_ROLES


def _seen_by(user: User):
    """A series the caller's team owns is shown to the caller only when the caller made it, has a
    meeting of it they may read, or have its recurring event on their own calendar. A series made
    by hand is no different: it is named after a person's meeting ("Make this a recurring
    meeting?" uses the meeting's own name), so it stays theirs until a meeting of it is shared.
    Else the title, cadence and next date of a teammate's private recurring call (a 1:1) would be
    published to the team (spec 8.2: private by default).

    With the flag on, "made it" and "has the event on their calendar" show an EMPTY card (made
    before the first recording) and nothing more: once a row has any meeting, only a meeting the
    caller may read lists it, so a creator whose own meetings went to their personal row does not
    keep seeing the team's card."""
    readable = select(Meeting.id).where(Meeting.series_id == Series.id, can_read_clause(user))
    own_event = select(CalendarEvent.id).where(
        CalendarEvent.user_id == user.id,
        CalendarEvent.recurring_event_id == Series.calendar_recurring_id,
    )
    mine = or_(Series.owner_user_id == user.id, own_event.exists())
    if follow_enabled():
        any_meeting = select(Meeting.id).where(Meeting.series_id == Series.id)
        return or_(readable.exists(), and_(~any_meeting.exists(), mine))
    return or_(readable.exists(), mine)


def get(session: Session, user: User, series_id: int) -> Series | None:
    """The series, only when it belongs to a team the caller is in and the caller may see it:
    anything else is None (404). The series' OWN team decides, so a person on two teams reads the
    series of either through the same URL (no team has to be named for an id), and a series of a
    team they are not in does not exist for them."""
    mine = select(TeamMember.team_id).where(TeamMember.user_id == user.id)
    return session.scalars(
        select(Series).where(Series.id == series_id, Series.team_id.in_(mine), _seen_by(user))
    ).first()


def list_for(session: Session, user: User, team_id: int | None = None) -> list[Series]:
    """The Recurring cards of one team: `team_id` (the route has checked the caller is in it) or,
    naming none, the caller's default team. A series whose meetings were shared to ANOTHER team
    is not part of this team's list: only what the caller may read counts (`_seen_by`)."""
    member = membership(session, user, team_id)
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
    owner_id: int | None,
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
    follow = follow_enabled()
    team_id = scope_team_id(session, owner, meeting, follow=follow)
    if follow:
        _rekey_empty_card(session, owner, row.recurring_event_id, team_id)
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


def _rekey_empty_card(session: Session, recorder: User, recurring_id: str, scope_id: int) -> None:
    """The recorder's first meeting of a recurring event lands in a scope other than the team their
    pre-recording card (`sync_from_joins`) was made in: the card goes to that scope instead of
    staying behind as an empty duplicate. Only the recorder's OWN empty card: no meeting, no waiting
    agenda line (those are the team's and wait there for its next meeting), no linked project (a
    project is the old team's), and not already a row of the scope. Anything else stays; the caller
    makes or finds the scope's row."""
    default_id = team_id_of(session, recorder)
    if default_id == scope_id:
        return
    card = session.scalars(
        select(Series).where(
            Series.team_id == default_id, Series.calendar_recurring_id == recurring_id
        )
    ).first()
    if card is None or card.owner_user_id != recorder.id or card.project_id is not None:
        return
    held = session.scalar(
        select(func.count()).select_from(Meeting).where(Meeting.series_id == card.id)
    )
    waiting = session.scalar(
        select(func.count())
        .select_from(SeriesAgendaItem)
        .where(SeriesAgendaItem.series_id == card.id, SeriesAgendaItem.consumed_at.is_(None))
    )
    taken = session.scalar(
        select(Series.id).where(
            Series.team_id == scope_id, Series.calendar_recurring_id == recurring_id
        )
    )
    if held or waiting or taken is not None:
        return
    card.team_id = scope_id
    session.flush()


def join(session: Session, meeting: Meeting, series: Series, *, consume: bool = False) -> None:
    """Make `meeting` a meeting of `series`. With `consume` (only when the pipeline links a
    recorded meeting) the agenda lines typed before it began are consumed by it (spec 7). A hand
    attach never consumes: it would let any member clear lines they may not remove. In the
    caller's transaction; the caller has checked who may."""
    first = meeting.series_id != series.id
    if first and meeting.series_id is not None:
        mark_topics_stale(session, meeting.series_id)
    meeting.series_id = series.id
    mark_topics_stale(session, series.id)
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


def _has_row_with_meetings(session: Session, user_id: int, recurring_id: str) -> bool:
    """Whether a team of the person's has a row of this recurring event that holds a meeting."""
    mine = select(TeamMember.team_id).where(TeamMember.user_id == user_id)
    held = select(Meeting.id).where(Meeting.series_id == Series.id)
    return (
        session.scalar(
            select(Series.id)
            .where(
                Series.team_id.in_(mine),
                Series.calendar_recurring_id == recurring_id,
                held.exists(),
            )
            .limit(1)
        )
        is not None
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
    follow = follow_enabled()
    for user_id, recurring_id, title, platform in rows:
        user = session.get(User, user_id)
        if follow and _has_row_with_meetings(session, user_id, recurring_id):
            continue  # their meetings already have rows (maybe their personal one): no empty card
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


def create(session: Session, user: User, title: str, team_id: int | None = None) -> Series:
    """A recurring meeting made by hand (no calendar id), in `team_id` (the route has checked the
    caller is in it) or, naming none, the caller's default team."""
    clean = _clean(title, MAX_TITLE_CHARS)
    if not clean:
        raise SeriesRefused(422, "invalid_title", "Give the recurring meeting a name.")
    team_id = team_id if team_id is not None else team_id_of(session, user)
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


def link_project(
    session: Session, user: User, series: Series, project_id: int | None
) -> Project | None:
    """Link the series to one of its team's projects, or unlink it (`None`): the tasks of its
    meetings are then filed into that project (`filing.series_project`, step 1 of the rule). A
    series is in at most one project, so linking it to a second moves it (the designer's checklist
    shows "Linked to {other project}" on a row that belongs to another project).

    Who may: an owner or admin of the series' team, as for creating, changing and deleting a
    project (`api_projects`): linking decides where the whole team's tasks are filed, so it is not
    left to the series' creator, nor to every member who can read it. The caller has already seen
    the series (`get`); a project that is not on the series' team is 404. The project row is
    locked FOR SHARE before the series is written, and `projects.delete` locks it FOR UPDATE
    before it clears the links, so a link either lands first and is cleared by the delete, or
    waits and finds the project gone: no link survives a delete."""
    if not is_manager(session, user, series.team_id):
        raise _forbidden()
    if project_id is None:
        series.project_id = None
        session.flush()
        return None
    project = session.scalars(
        select(Project)
        .where(Project.id == project_id, Project.team_id == series.team_id)
        .with_for_update(read=True)
    ).first()
    if project is None:
        raise SeriesRefused(404, "not_found", "No such project.")
    series.project_id = project.id
    session.flush()
    return project


def attach(session: Session, user: User, meeting: Meeting, series_id: int | None) -> Series | None:
    """Put the caller's own meeting into a series of their team, or take it out (`None`). Only the
    meeting's owner may (a teammate who can read it may not move it). 404 for a series that is
    not the caller's team's."""
    if not recorded_by(session, user, meeting):
        raise SeriesRefused(404, "not_found", "No such meeting.")
    if series_id is None:
        if meeting.series_id is not None:
            mark_topics_stale(session, meeting.series_id)
        meeting.series_id = None
        session.flush()
        return None
    series = get(session, user, series_id)
    if series is None or series.team_id != meeting_team_id(session, user, meeting):
        raise SeriesRefused(404, "not_found", "No such recurring meeting.")
    join(session, meeting, series)
    return series


def meeting_team_id(session: Session, recorder: User, meeting: Meeting) -> int:
    """The team a meeting's series must belong to: the team that owns it, else the one it is shared
    with, else (a private meeting) its recorder's PERSONAL team with the flag on, or their default
    team as before multi-team. A series of any other team would mail people named in the meeting,
    in a team that cannot read it."""
    return scope_team_id(session, recorder, meeting, follow=follow_enabled())


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
    if not recorded_by(session, user, meeting):
        raise SeriesRefused(404, "not_found", "No such meeting.")
    if not reused_code(session, meeting):
        raise SeriesRefused(
            409,
            "not_reused",
            "This link has not been used for another meeting, so it is not recurring.",
        )
    team_id = meeting_team_id(session, user, meeting)
    made = create(session, user, titles.meeting_name(meeting), team_id)
    others = session.scalars(
        select(Meeting).where(
            Meeting.owner_user_id == user.id,
            Meeting.platform == meeting.platform,
            Meeting.native_id == meeting.native_id,
            Meeting.series_id.is_(None),
        )
    )
    for row in others:
        if meeting_team_id(session, user, row) == team_id:  # never mix two teams in one series
            row.series_id = made.id
    session.flush()
    return made


def forget_meetings_of(session: Session, user_id: int, team_id: int) -> None:
    """A person leaves a team: their meetings leave that team's series (they never move, and the
    team must not keep a pointer into meetings it can no longer read). With the flag on they
    FOLLOW the person instead: the meeting is private now, so it goes to its recorder's personal
    row. A meeting the team owns stays in the team's row either way."""
    in_team = select(Series.id).where(Series.team_id == team_id)
    mine = [
        Meeting.owner_user_id == user_id,
        Meeting.owner_team_id.is_(None),  # a meeting the team owns stays in its series
        Meeting.series_id.in_(in_team),
    ]
    if follow_enabled():
        team_rows = set(session.scalars(in_team))
        stay = select(Meeting).where(*mine).order_by(Meeting.id)
        for meeting in session.scalars(stay.execution_options(populate_existing=True)):
            if follow_meeting(session, meeting) is None or meeting.series_id in team_rows:
                meeting.series_id = None  # no row to follow to: out of the team's series, as before
        return
    session.execute(update(Meeting).where(*mine).values(series_id=None))


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


# ---- following the meetings (SERIES_FOLLOW_MEETINGS) ----


@dataclass(frozen=True)
class Placement:
    """What `place_meeting` did: the row the meeting is in now (None: in no series), and whether it
    moved, whether a new row was made for it, whether it was cut loose because the target team is
    full, whether it was skipped (a private meeting of an account that is gone)."""

    series: Series | None
    moved: bool = False
    made: bool = False
    cut_loose: bool = False
    skipped: bool = False


def _row_for_scope(
    session: Session, current: Series, scope_id: int, recorder: User | None
) -> tuple[Series | None, bool]:
    """The row of `current`'s recurring meeting in the team `scope_id`, made when the team has none:
    (row, made). A calendar row is found by its recurring id; a hand-made one (no id) by the same
    title among that team's hand-made rows. A new row copies title and platform and is made by the
    recorder; it NEVER copies the project link (a project belongs to one team) or the agenda lines
    (they are the team's). No row (None) when the team already has as many as fit."""
    if current.calendar_recurring_id is not None:
        found = session.scalars(
            select(Series).where(
                Series.team_id == scope_id,
                Series.calendar_recurring_id == current.calendar_recurring_id,
            )
        ).first()
    else:
        found = session.scalars(
            select(Series)
            .where(
                Series.team_id == scope_id,
                Series.calendar_recurring_id.is_(None),
                Series.title == current.title,
            )
            .order_by(Series.id)
        ).first()
    if found is not None:
        return found, False
    count = session.scalar(
        select(func.count()).select_from(Series).where(Series.team_id == scope_id)
    )
    if count >= MAX_SERIES_PER_TEAM:
        return None, False
    creator = recorder.id if recorder is not None and recorder.status == "active" else None
    if current.calendar_recurring_id is not None:
        made = _get_or_create(
            session,
            scope_id,
            current.calendar_recurring_id,
            current.title,
            current.platform,
            creator,
        )
    else:
        made = Series(
            team_id=scope_id, owner_user_id=creator, title=current.title, platform=current.platform
        )
        session.add(made)
        session.flush()
    return made, True


def place_meeting(session: Session, meeting: Meeting) -> Placement:
    """Put the meeting into the row of its scope, whatever the flag says (the backfill calls this
    directly; the hooks call `follow_meeting`, which checks the flag). A meeting in no series is
    left alone. When the meeting is in another scope's row, it moves to the scope's row for the
    same recurring meeting (found or made), and BOTH rows' repeat topics are marked stale. If the
    scope's team has no room for another row, the meeting is cut loose from its series instead of
    being left in a row of another scope. Never consumes agenda lines: only the pipeline's link
    does. In the caller's transaction."""
    if meeting.series_id is None:
        return Placement(None)
    current = session.get(Series, meeting.series_id)
    if current is None:
        return Placement(None)
    recorder = session.get(User, meeting.owner_user_id)
    if scope_team_of(meeting) is None and (recorder is None or recorder.status != "active"):
        return Placement(current, skipped=True)  # no personal team to take it to
    scope_id = scope_team_id(session, recorder, meeting, follow=True)
    if current.team_id == scope_id:
        return Placement(current)
    target, made = _row_for_scope(session, current, scope_id, recorder)
    meeting.series_id = target.id if target is not None else None
    mark_topics_stale(session, current.id)
    if target is not None:
        mark_topics_stale(session, target.id)
    session.flush()
    if target is None:
        logger.warning(
            "meeting %s: left its series, the new team has no room for a row", meeting.id
        )
        return Placement(None, moved=True, cut_loose=True)
    return Placement(target, moved=True, made=made)


def follow_meeting(session: Session, meeting: Meeting) -> Series | None:
    """The hook for every change of a meeting's team (share, unshare, move, move back, its
    recorder leaves, its team is deleted): with the flag on, put the meeting into the row of its
    scope (`place_meeting`). Off: nothing, exactly as before. Returns the meeting's row."""
    if not follow_enabled():
        return None
    return place_meeting(session, meeting).series


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
    """Tasks of the series' meetings that are not done, that the viewer may see, oldest first.
    A suggested task is not one yet, so it is left out (and so of the reminder mails built on this).
    """
    return list(
        session.scalars(
            select(Task)
            .where(
                tasks.visible(viewer),
                tasks.confirmed(),
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
            tasks.confirmed(),
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
            .where(
                tasks.visible(viewer),
                tasks.confirmed(),
                Meeting.series_id.in_(ids),
                Task.status != "done",
            )
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
    if owns(session, viewer, meeting):
        return True
    return any(is_manager(session, viewer, t) for t in meeting_team_ids(meeting))


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


# ---- repeat topics ("Keeps coming up") and the suggested agenda ----


def mark_topics_stale(session: Session, series_id: int) -> None:
    """Tell the worker's topics job to look at this series again: its notes changed or a meeting
    joined or left it. In the caller's transaction."""
    session.execute(
        update(Series)
        .where(Series.id == series_id)
        .values(topics_checked_at=None, topics_attempts=0)
    )


@dataclass(frozen=True)
class TopicView:
    """A stored topic as one viewer may see it: `meetings` are only the ones they may read."""

    id: int
    label: str
    agenda: str
    reason: str
    meetings: list[Meeting]


def topics_for(session: Session, viewer: User, series: Series) -> list[TopicView]:
    """The stored topics of the series that the viewer may be shown. No model call: rows only.

    The job reads only meetings shared with the team (`topics.source_meetings`), so every
    reader of the series could read every word's source when it was written. Sharing can change
    after that, so this is the guard: a topic is shown only if the viewer can **still read every
    meeting behind it**, the cited ones (`meeting_ids`) and every one the model was shown
    (`source_meeting_ids`), and each must still be a ready meeting of this series. If one was
    unshared, moved or deleted the topic is hidden (the job recomputes soon after: sharing marks
    the series stale). Text the model wrote must never reach someone who cannot read what it was
    written from. `meetings` are the cited meetings, oldest first."""
    rows = list(
        session.scalars(
            select(SeriesTopic)
            .where(SeriesTopic.series_id == series.id)
            .order_by(SeriesTopic.position, SeriesTopic.id)
        )
    )

    def ids_of(row: SeriesTopic) -> set[int]:
        return {
            i for i in [*row.meeting_ids, *(row.source_meeting_ids or [])] if isinstance(i, int)
        }

    wanted = set().union(*(ids_of(row) for row in rows)) if rows else set()
    if not wanted:
        return []
    readable = {
        m.id: m
        for m in session.scalars(
            visible_meetings(viewer).where(
                Meeting.id.in_(wanted), Meeting.series_id == series.id, Meeting.status == "ready"
            )
        )
    }
    out = []
    for row in rows:
        cited = list(dict.fromkeys(row.meeting_ids))
        if len(cited) < 2 or not ids_of(row) <= readable.keys():
            continue
        seen = sorted((readable[i] for i in cited), key=lambda m: (m.created_at, m.id))
        out.append(TopicView(row.id, row.label, row.agenda, row.reason, seen))
    return out


_SUBLINES = {
    "no_decision": "Discussed in {n} meetings without a decision",
    "no_owner": "Raised in {n} meetings, still no owner or date",
    "not_final": "Discussed in {n} meetings, approval rules not final",
}


def topic_line(reason: str, count: int) -> str:
    """The sub-line under a topic: a template by reason, with the number of meetings the viewer
    may read it was raised in."""
    return _SUBLINES[reason].format(n=count)


def topic_dates(days: list[date]) -> str:
    """ "Mon 26 Oct, Fri 30 Oct and Mon 2 Nov 2026": the meetings' days, oldest first. The year
    follows the last one; an earlier day from another year carries its own."""
    last_year = days[-1].year
    parts = [f"{d:%a} {d.day} {d:%b}" + (f" {d.year}" if d.year != last_year else "") for d in days]
    text = parts[0] if len(parts) == 1 else ", ".join(parts[:-1]) + " and " + parts[-1]
    return f"{text} {last_year}"


def suggested_task_count(session: Session, viewer: User, series: Series) -> int:
    """Suggested tasks (waiting for someone to accept them) of the series' **last** meeting that the
    viewer may read, and may see: "Confirm N suggested tasks from last time". They are in no other
    count."""
    last = next(iter(meetings_of(session, series, viewer, limit=1)), None)
    if last is None:
        return 0
    return (
        session.scalar(
            select(func.count())
            .select_from(Task)
            .where(tasks.visible(viewer), Task.suggested.is_(True), Task.meeting_id == last.id)
        )
        or 0
    )


def agenda_suggestions(
    topics: list[TopicView],
    unfinished: list[Task],
    carried: int,
    suggested: int,
    today: date,
) -> list[tuple[str, str]]:
    """The "Suggested agenda" lines as (kind, text), built in code, no model, in the designer's
    order: each repeat topic's agenda sentence, the overdue tasks, the carried-over questions, the
    suggested tasks to confirm. A line with nothing behind it is left out. `today` is the viewer's
    day, and `unfinished` must already be what the viewer may see (suggested tasks are not in it).
    """
    lines: list[tuple[str, str]] = [("topic", t.agenda) for t in topics]
    overdue = sum(1 for t in unfinished if t.due_date is not None and t.due_date < today)
    if overdue:
        lines.append(
            (
                "overdue",
                "Review the overdue task"
                if overdue == 1
                else f"Review the {overdue} overdue tasks",
            )
        )
    if carried:
        lines.append(
            (
                "questions",
                "Settle 1 open question carried over"
                if carried == 1
                else f"Settle {carried} open questions carried over",
            )
        )
    if suggested:
        lines.append(
            (
                "suggested",
                "Confirm 1 suggested task from last time"
                if suggested == 1
                else f"Confirm {suggested} suggested tasks from last time",
            )
        )
    return lines
