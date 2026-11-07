"""The recurring-meeting routes: the Recurring cards, the Series page, the agenda, "Send agenda",
the meeting page's Series link and "On next agenda" toggles, and the reminder preference. Included
into `app.api.router`, so they share its `/api` prefix, login check, CSRF rule and error shape.

A series of another team, a meeting or a question the caller may not read, is 404, never 403.
`app/series.py` holds the rules and `app/series_mail.py` the mail; no model is called here.
"""

import logging
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, Request, Response
from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session
from starlette.exceptions import HTTPException as StarletteHTTPException

from app import (
    api_tasks,
    auth,
    mail,
    mail_summary,
    series,
    series_mail,
    services,
    sprints,
    tasks,
    timezones,
)
from app.api_schemas import ErrorResponse, MeetingRef, QuestionOut, SeriesRef
from app.calendar_recurring import describe_cadence, next_text
from app.db import get_session
from app.models import (
    CalendarEvent,
    Meeting,
    OpenQuestion,
    Project,
    ScheduledJoin,
    Series,
    Task,
    TeamMember,
    User,
    UserSettings,
)
from app.series_schemas import (
    AgendaItemIn,
    AgendaItemOut,
    AgendaSent,
    AttendeeOut,
    CarriedQuestion,
    MeetingSeriesIn,
    MeetingSeriesOut,
    PastMeeting,
    ProjectLink,
    QuestionPatch,
    ReminderPreferencesIn,
    ReminderPreferencesOut,
    SeriesCard,
    SeriesCreate,
    SeriesList,
    SeriesPage,
    SeriesPatch,
    SeriesProjectIn,
)
from app.visibility import can_read_clause

logger = logging.getLogger(__name__)
router = APIRouter()

FORBIDDEN = {403: {"model": ErrorResponse, "description": "`team_forbidden`"}}
NOT_FOUND = {404: {"model": ErrorResponse}}
INVALID = {422: {"model": ErrorResponse}}
PAST_LIMIT = 50


def _refuse(exc: series.SeriesRefused) -> services.Refused:
    return services.Refused(exc.status, exc.code, exc.message)


def _series_or_404(session: Session, user: User, series_id: int) -> Series:
    found = series.get(session, user, series_id) if 0 < series_id <= services.MAX_ID else None
    if found is None:
        raise StarletteHTTPException(404, "No such recurring meeting")
    return found


def _zone_name(session: Session, user: User) -> str | None:
    return session.scalar(select(UserSettings.time_zone).where(UserSettings.user_id == user.id))


def _now() -> datetime:
    return datetime.now(UTC)


def _team_member_ids(found: Series):
    return select(TeamMember.user_id).where(TeamMember.team_id == found.team_id)


def _events(session: Session, found: Series) -> list[CalendarEvent]:
    """The stored calendar events of this recurring id on the calendars of the team's members:
    what the cadence and the next date are read from. Empty for a series made by hand."""
    if found.calendar_recurring_id is None:
        return []
    return list(
        session.scalars(
            select(CalendarEvent)
            .where(
                CalendarEvent.recurring_event_id == found.calendar_recurring_id,
                CalendarEvent.user_id.in_(_team_member_ids(found)),
                CalendarEvent.status != "cancelled",
                CalendarEvent.all_day.is_(False),
            )
            .order_by(CalendarEvent.starts_at)
        )
    )


def _timing(
    session: Session, found: Series, zone_name: str | None, now: datetime, viewer: User
) -> tuple[str, datetime | None, str | None]:
    """(cadence, next start, next text) for a series, written in the zone. The cadence reads the
    stored event times and the start times of the series' joined meetings; a claim is made only
    when those prove it (`calendar_recurring.describe_cadence`)."""
    zone = timezones.zone(zone_name)
    events = _events(session, found)
    starts = {event.starts_at for event in events}
    starts |= set(
        session.scalars(
            select(ScheduledJoin.starts_at)
            .join(Meeting, Meeting.id == ScheduledJoin.meeting_id)
            .where(Meeting.series_id == found.id, can_read_clause(viewer))
        )
    )
    cadence = describe_cadence(starts, zone)
    ahead = [event.starts_at for event in events if event.starts_at > now]
    upcoming = min(ahead) if ahead else None
    return (
        cadence,
        upcoming.astimezone(UTC) if upcoming else None,
        next_text(upcoming, zone) if upcoming else None,
    )


def _platform(found: Series) -> str | None:
    return found.platform if found.platform in ("google_meet", "zoom") else None


def _project_names(session: Session, team_id: int) -> dict[int, str]:
    rows = session.execute(select(Project.id, Project.name).where(Project.team_id == team_id))
    return {project_id: name for project_id, name in rows}


def _project_link(found: Series, names: dict[int, str]) -> ProjectLink | None:
    """The link, only when the project is still on the team (`project_id` has no foreign key)."""
    if found.project_id is None or found.project_id not in names:
        return None
    return ProjectLink(id=found.project_id, name=names[found.project_id])


def _initial_title(found: Series) -> str:
    return found.title or series.UNTITLED


def _question_out(
    session: Session, viewer: User, question: OpenQuestion, meeting: Meeting
) -> QuestionOut:
    return QuestionOut(
        id=question.id,
        text=question.text,
        carriable=meeting.series_id is not None,
        carry=bool(question.carry),
        resolved=question.resolved_at is not None,
        can_update=series.can_change_question(session, viewer, meeting),
    )


def meeting_questions(session: Session, viewer: User, meeting: Meeting) -> list[QuestionOut]:
    """A meeting's open questions in the order of the notes, for the meeting page."""
    rows = session.scalars(
        select(OpenQuestion)
        .where(OpenQuestion.meeting_id == meeting.id)
        .order_by(OpenQuestion.position, OpenQuestion.id)
    )
    return [_question_out(session, viewer, q, meeting) for q in rows]


def meeting_series(session: Session, viewer: User, meeting: Meeting) -> SeriesRef | None:
    found = series.of_meeting(session, viewer, meeting)
    return SeriesRef(id=found.id, title=found.title or series.UNTITLED) if found else None


# ---- the cards and the page ----


@router.get("/series", tags=["series"])
def list_series(
    session: Session = Depends(get_session), user: User = Depends(auth.current_user)
) -> SeriesList:
    """The Recurring cards: the caller's team's recurring meetings, soonest next meeting first
    (those with no known next date last, by name). The unfinished-task and open-question counts
    cover only what the caller may read. A recurring meeting appears once the calendar's event
    has a join scheduled (within the hour) or its first recording has notes."""
    now = _now()
    zone_name = _zone_name(session, user)
    rows = series.list_for(session, user)
    counts = series.counts_for(session, user, [s.id for s in rows])
    names = _project_names(session, rows[0].team_id) if rows else {}
    cards = []
    for found in rows:
        cadence, upcoming, upcoming_text = _timing(session, found, zone_name, now, user)
        tasks_open, questions_open = counts[found.id]
        cards.append(
            SeriesCard(
                id=found.id,
                title=_initial_title(found),
                cadence=cadence,
                platform=_platform(found),
                next_starts_at=upcoming,
                next_text=upcoming_text,
                unfinished_tasks=tasks_open,
                open_questions=questions_open,
                project=_project_link(found, names),
            )
        )
    far = datetime.max.replace(tzinfo=UTC)
    cards.sort(key=lambda c: (c.next_starts_at or far, c.title.lower(), c.id))
    return SeriesList(timezone=timezones.zone(zone_name).key, series=cards)


@router.post("/series", status_code=201, tags=["series"], responses={**INVALID})
def create_series(
    body: SeriesCreate,
    session: Session = Depends(get_session),
    user: User = Depends(auth.current_user),
) -> SeriesCard:
    """Make a recurring meeting by hand (a meeting is then attached with
    `PUT /api/meetings/{id}/series`). Any member of the team may. 409 `series_limit`."""
    try:
        found = series.create(session, user, body.title)
    except series.SeriesRefused as exc:
        raise _refuse(exc) from None
    session.commit()
    cadence, upcoming, upcoming_text = _timing(
        session, found, _zone_name(session, user), _now(), user
    )
    return SeriesCard(
        id=found.id,
        title=_initial_title(found),
        cadence=cadence,
        platform=_platform(found),
        next_starts_at=upcoming,
        next_text=upcoming_text,
        unfinished_tasks=0,
        open_questions=0,
        project=None,
    )


def _sprint_note(
    session: Session, user: User, found: Series, upcoming: datetime | None, zone_name: str | None
) -> str | None:
    """ "Opens Sprint 13" under "Next meeting": the very rule the one-hour reminder mail uses for
    its "Sprint 13 opens" (`mail_summary.opening_sprint`, given the next meeting's start and the
    caller's zone), so the page and the mail cannot disagree. It is a nicety: if the read fails the
    page is served without it, as the mail is sent without its extras."""
    if upcoming is None:
        return None
    try:
        with session.begin_nested():
            opening = mail_summary.opening_sprint(session, user, found, upcoming, zone_name)
    except Exception as exc:
        logger.warning(
            "series %s: page served without its sprint line (%s)", found.id, type(exc).__name__
        )
        return None
    return f"Opens {sprints.name_of(opening)}" if opening is not None else None


def _page(session: Session, user: User, found: Series) -> SeriesPage:
    now = _now()
    zone_name = _zone_name(session, user)
    cadence, upcoming, upcoming_text = _timing(session, found, zone_name, now, user)
    unfinished = series.unfinished_tasks(session, user, found)
    last_day = series.last_meeting_day(session, user, found, zone_name)
    finished = series.finished_since(session, user, found, last_day, zone_name) if last_day else []
    questions = series.carried_questions(session, user, found)
    agenda = series.agenda_items(session, found)
    past = series.meetings_of(session, found, user, limit=PAST_LIMIT)
    per_meeting = dict(
        session.execute(
            select(Task.meeting_id, func.count())
            .where(tasks.visible(user), Task.meeting_id.in_([m.id for m in past]))
            .group_by(Task.meeting_id)
        ).all()
        if past
        else []
    )
    team_ids = {a.user_id for a in series.attendees(session, found, user) if a.user_id}
    recipients = [a for a in series.recipients(session, found) if a.user_id != user.id][
        : series_mail.SEND_MAX_RECIPIENTS
    ]
    return SeriesPage(
        id=found.id,
        title=_initial_title(found),
        cadence=cadence,
        platform=_platform(found),
        timezone=timezones.zone(zone_name).key,
        next_starts_at=upcoming,
        next_text=upcoming_text,
        sprint_note=_sprint_note(session, user, found, upcoming, zone_name),
        can_rename=found.owner_user_id == user.id
        or series.is_manager(session, user, found.team_id),
        agenda_recipients=len(recipients),
        unfinished=api_tasks.present(session, user, unfinished),
        finished_since_text=(f"{last_day:%a} {last_day.day} {last_day:%b}" if last_day else None),
        finished=api_tasks.present(session, user, finished),
        questions=[
            CarriedQuestion(
                id=q.id,
                text=q.text,
                meeting=MeetingRef(
                    id=m.id,
                    title=services.meeting_title(m),
                    platform=m.platform,
                    created_at=m.created_at,
                ),
                can_update=series.can_change_question(session, user, m),
            )
            for q, m in questions
        ],
        agenda=[
            AgendaItemOut(
                id=i.id,
                text=i.text,
                can_remove=i.created_by == user.id
                or series.is_manager(session, user, found.team_id),
            )
            for i in agenda
        ],
        past_meetings=[
            PastMeeting(
                id=m.id,
                title=services.meeting_title(m),
                created_at=m.created_at,
                duration_seconds=m.recorded_seconds,
                tasks_count=per_meeting.get(m.id, 0),
            )
            for m in past
        ],
        attendees=[
            AttendeeOut(name=a.name, is_teammate=a.user_id in team_ids and a.user_id is not None)
            for a in series.attendees(session, found, user)
        ],
    )


@router.get("/series/{series_id:id}", tags=["series"], responses={**NOT_FOUND})
def get_series(
    series_id: int,
    session: Session = Depends(get_session),
    user: User = Depends(auth.current_user),
) -> SeriesPage:
    """The Series page: next meeting, unfinished tasks from earlier meetings, what was finished
    since the last meeting, the questions carried over, the agenda lines, past meetings and
    attendees. Everything inside is limited to what the caller may read: a teammate's private
    meeting is not listed and its tasks and questions are not counted. 404 for another team's."""
    return _page(session, user, _series_or_404(session, user, series_id))


@router.patch(
    "/series/{series_id:id}", tags=["series"], responses={**NOT_FOUND, **FORBIDDEN, **INVALID}
)
def patch_series(
    series_id: int,
    body: SeriesPatch,
    session: Session = Depends(get_session),
    user: User = Depends(auth.current_user),
) -> SeriesCard:
    """Rename a recurring meeting (its creator, or an owner or admin)."""
    found = _series_or_404(session, user, series_id)
    try:
        series.rename(session, user, found, body.title)
    except series.SeriesRefused as exc:
        raise _refuse(exc) from None
    session.commit()
    cadence, upcoming, upcoming_text = _timing(
        session, found, _zone_name(session, user), _now(), user
    )
    counts = series.counts_for(session, user, [found.id])[found.id]
    return SeriesCard(
        id=found.id,
        title=_initial_title(found),
        cadence=cadence,
        platform=_platform(found),
        next_starts_at=upcoming,
        next_text=upcoming_text,
        unfinished_tasks=counts[0],
        open_questions=counts[1],
        project=_project_link(found, _project_names(session, found.team_id)),
    )


@router.put(
    "/series/{series_id:id}/project",
    tags=["series"],
    responses={**NOT_FOUND, **FORBIDDEN},
)
def put_series_project(
    series_id: int,
    body: SeriesProjectIn,
    session: Session = Depends(get_session),
    user: User = Depends(auth.current_user),
) -> SeriesCard:
    """Link a recurring meeting to a project (`project_id`), or unlink it (null): the tasks taken
    from its meetings are filed into that project, first in the filing rule. One project per
    recurring meeting: linking it to another project moves it. Owner or admin of the team only
    (403 `team_forbidden`); a recurring meeting the caller cannot see, or a project that is not
    on the team, is 404. The same call again changes nothing."""
    found = _series_or_404(session, user, series_id)
    try:
        series.link_project(session, user, found, body.project_id)
    except series.SeriesRefused as exc:
        session.rollback()
        raise _refuse(exc) from None
    session.commit()
    cadence, upcoming, upcoming_text = _timing(
        session, found, _zone_name(session, user), _now(), user
    )
    counts = series.counts_for(session, user, [found.id])[found.id]
    return SeriesCard(
        id=found.id,
        title=_initial_title(found),
        cadence=cadence,
        platform=_platform(found),
        next_starts_at=upcoming,
        next_text=upcoming_text,
        unfinished_tasks=counts[0],
        open_questions=counts[1],
        project=_project_link(found, _project_names(session, found.team_id)),
    )


# ---- a meeting's series ----


@router.put("/meetings/{meeting_id:id}/series", tags=["series"], responses={**NOT_FOUND})
def put_meeting_series(
    meeting_id: int,
    body: MeetingSeriesIn,
    session: Session = Depends(get_session),
    user: User = Depends(auth.current_user),
) -> MeetingSeriesOut:
    """Put the caller's own meeting into one of their team's recurring meetings, or take it out
    (`series_id` null). Only the meeting's owner may; a teammate reading it gets 404. Agenda
    lines typed before the meeting began are taken by it."""
    meeting = services.owned_meeting_or_404(session, user, meeting_id)
    try:
        found = series.attach(session, user, meeting, body.series_id)
    except series.SeriesRefused as exc:
        raise StarletteHTTPException(404, exc.message) from None
    session.commit()
    return MeetingSeriesOut(
        series=SeriesRef(id=found.id, title=_initial_title(found)) if found else None
    )


@router.post(
    "/meetings/{meeting_id:id}/recurring",
    status_code=201,
    tags=["series"],
    responses={
        **NOT_FOUND,
        409: {"model": ErrorResponse, "description": "`not_reused` or `series_limit`"},
    },
)
def make_recurring(
    meeting_id: int,
    session: Session = Depends(get_session),
    user: User = Depends(auth.current_user),
) -> MeetingSeriesOut:
    """ "Make this a recurring meeting?": make a recurring meeting named after this meeting from a
    link that was used before, and put this meeting and the caller's other meetings of the same
    link code into it. Only the meeting's owner (404 for anyone else); 409 `not_reused` when the
    link was not used before; offered by `suggest_recurring` on the meeting."""
    meeting = services.owned_meeting_or_404(session, user, meeting_id)
    try:
        made = series.make_from_reused_link(session, user, meeting)
    except series.SeriesRefused as exc:
        session.rollback()
        raise _refuse(exc) from None
    session.commit()
    return MeetingSeriesOut(series=SeriesRef(id=made.id, title=_initial_title(made)))


# ---- the agenda ----


@router.get("/series/{series_id:id}/agenda", tags=["series"], responses={**NOT_FOUND})
def list_agenda(
    series_id: int,
    session: Session = Depends(get_session),
    user: User = Depends(auth.current_user),
) -> list[AgendaItemOut]:
    """The "Other agenda items" waiting for the next meeting, in the order they were added."""
    found = _series_or_404(session, user, series_id)
    manager = series.is_manager(session, user, found.team_id)
    return [
        AgendaItemOut(id=i.id, text=i.text, can_remove=manager or i.created_by == user.id)
        for i in series.agenda_items(session, found)
    ]


@router.post(
    "/series/{series_id:id}/agenda",
    status_code=201,
    tags=["series"],
    responses={**NOT_FOUND, **INVALID},
)
def add_agenda(
    series_id: int,
    body: AgendaItemIn,
    session: Session = Depends(get_session),
    user: User = Depends(auth.current_user),
) -> AgendaItemOut:
    """Add a line (plain text, at most 200 characters, no web address; any member). 422
    `invalid_agenda_item`; 409 `agenda_full` at 30 waiting lines."""
    found = _series_or_404(session, user, series_id)
    try:
        item = series.add_agenda_item(session, user, found, body.text)
    except series.SeriesRefused as exc:
        raise _refuse(exc) from None
    session.commit()
    return AgendaItemOut(id=item.id, text=item.text, can_remove=True)


@router.delete(
    "/series/{series_id:id}/agenda/{item_id:id}",
    status_code=204,
    tags=["series"],
    responses={**NOT_FOUND, **FORBIDDEN},
)
def remove_agenda(
    series_id: int,
    item_id: int,
    session: Session = Depends(get_session),
    user: User = Depends(auth.current_user),
) -> Response:
    """Remove a line: its author, or an owner or admin."""
    found = _series_or_404(session, user, series_id)
    try:
        gone = series.remove_agenda_item(session, user, found, item_id)
    except series.SeriesRefused as exc:
        raise _refuse(exc) from None
    if not gone:
        raise StarletteHTTPException(404, "No such agenda item")
    session.commit()
    return Response(status_code=204)


@router.post(
    "/series/{series_id:id}/agenda/send",
    tags=["series"],
    responses={
        **NOT_FOUND,
        409: {"model": ErrorResponse, "description": "`no_attendees` or `nothing_to_send`"},
        429: {"model": ErrorResponse, "description": "`agenda_rate_limited`"},
        503: {"model": ErrorResponse, "description": "`mail_unavailable`"},
    },
)
def send_agenda(
    series_id: int,
    request: Request,
    session: Session = Depends(get_session),
    user: User = Depends(auth.current_user),
) -> AgendaSent:
    """Email the agenda to the teammates who were in the last three meetings (not the caller):
    the waiting agenda lines, the questions carried over and the unfinished tasks, each limited
    to what that person may read. At most 10 people a send, 5 sends and 20 people a day per
    caller. Nobody outside the team is ever mailed. The answer lists who it is going to."""
    found = _series_or_404(session, user, series_id)
    if not mail.is_configured(request.app.state.settings):
        raise services.Refused(503, "mail_unavailable", "Email is not set up on this server.")
    try:
        people = series_mail.send_agenda(session, user, found, _now())
    except series.SeriesRefused as exc:
        session.rollback()
        raise _refuse(exc) from None
    session.commit()
    # Names only of people from meetings the caller may read: who spoke in a teammate's private
    # meeting is not the caller's to learn.
    known = {a.user_id for a in series.attendees(session, found, user) if a.user_id}
    return AgendaSent(count=len(people), names=[p.name for p in people if p.user_id in known])


# ---- questions ----


@router.patch(
    "/questions/{question_id:id}",
    tags=["series"],
    responses={
        **NOT_FOUND,
        **FORBIDDEN,
        409: {"model": ErrorResponse, "description": "`not_in_series`"},
    },
)
def patch_question(
    question_id: int,
    body: QuestionPatch,
    session: Session = Depends(get_session),
    user: User = Depends(auth.current_user),
) -> QuestionOut:
    """Change an open question: `carry` is "On next agenda" (only for a meeting that is in a
    recurring meeting; turning it on also reopens a resolved question) and `resolved` is "Mark
    resolved" (the same words in the series' other meetings you may change are resolved too).
    Allowed for the meeting's owner and the team's owners and admins; a teammate who can only
    read gets 403 `team_forbidden`; a question you cannot read is 404."""
    if not 0 < question_id <= services.MAX_ID:
        raise StarletteHTTPException(404, "No such question")
    found = series.question_for(session, user, question_id)
    if found is None:
        raise StarletteHTTPException(404, "No such question")
    question, meeting = found
    if body.carry is None and body.resolved is None:
        raise services.Refused(422, "validation_error", "Send carry or resolved.")
    try:
        series.set_question(
            session, user, question, meeting, carry=body.carry, resolved=body.resolved, now=_now()
        )
    except series.SeriesRefused as exc:
        session.rollback()
        raise _refuse(exc) from None
    session.commit()
    return _question_out(session, user, question, meeting)


# ---- the person's reminder preference ----


@router.get("/settings/reminders", tags=["settings"])
def read_reminder_preferences(
    session: Session = Depends(get_session), user: User = Depends(auth.current_user)
) -> ReminderPreferencesOut:
    """Whether the caller is emailed the agenda a teammate sends and the reminders before the
    recurring meetings they attend (on by default). The team's switch for reminders is
    `PATCH /api/team`."""
    value = session.scalar(
        select(UserSettings.notify_reminders).where(UserSettings.user_id == user.id)
    )
    return ReminderPreferencesOut(notify_reminders=True if value is None else value)


@router.put("/settings/reminders", tags=["settings"])
def write_reminder_preferences(
    body: ReminderPreferencesIn,
    session: Session = Depends(get_session),
    user: User = Depends(auth.current_user),
) -> ReminderPreferencesOut:
    """Turn the caller's agenda and reminder mail on or off. Checked again when each mail is
    sent, so turning it off also stops one already queued."""
    session.execute(
        insert(UserSettings)
        .values(user_id=user.id, notify_reminders=body.notify_reminders)
        .on_conflict_do_update(
            index_elements=["user_id"], set_={"notify_reminders": body.notify_reminders}
        )
    )
    session.commit()
    return ReminderPreferencesOut(notify_reminders=body.notify_reminders)
