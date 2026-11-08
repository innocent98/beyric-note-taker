"""What the one-hour reminder says about the sprint, the next milestone and the note taker: the
"· Sprint 13 opens" text in the meta line and the grey summary box (`Emails.dc.html`, "Meeting
reminder, 1 hour before"). Pure reads, nothing is written, nothing is generated: every sentence is
put together from stored rows by the rules below.

**Whose data.** Sprints and milestones belong to the series' team, and the recipient is checked
to be on that team before any of this runs (`series_mail._recipient`). The only numbers that
depend on the reader are the task counts, and they count only the tasks the recipient may see
(`tasks.visible`, as the Tasks board does); a sprint, milestone or count the reader cannot see is
never mentioned.

**The sprint line** (`opening_sprint`). The series' team runs sprints (the series' own, when its
project has a sprint length of its own: `sprints.series_key`). A sprint "opens" on its start date,
or, for a sprint that started the day after the previous one ended, on the first working day
(Monday to Friday) after the previous sprint's end date, so a sprint that starts on a Saturday
opens on Monday. It is named only when the meeting is on that day in the recipient's zone and is
the first meeting of the series since the sprint started. A team that has no sprint rows (sprints
are made the first time a task is filed or the board is opened) has nothing to say.

**The box** has up to three sentences, in this order, and is left out when none applies:

1. `closed_sprint_sentence`: "Sprint 12 closed with 7 of 11 tasks done." The most recently closed
   sprint of the series, when it closed no more than 14 days (14 x 24 hours) before the mail is
   made. Closing moves a sprint's unfinished tasks to the next sprint with `carried` set, so the
   total is rebuilt as the tasks still in the sprint plus the tasks carried into the sprint that
   followed it; the done count is the tasks in the sprint with status `done`. An approximation,
   exact unless a task was moved or deleted after the close: nothing stores the total at close.
2. `milestone_sentence`: "{name} is in 29 days." The nearest confirmed milestone of the series'
   project due after today (the recipient's today), in whole days; a milestone due today or in the
   past, an unconfirmed one, and a series with no project give nothing.
3. `auto_join_sentence`: "The note taker will join this meeting automatically." Only when the
   recipient's own calendar event for this occurrence passes `gcal_rules.will_join` with their
   own rule, their calendar connection is up (`connected`, or `degraded`: joins carry on) **and**
   the scheduler really has the join (`_join_is_live`): a `scheduled_joins` row for that event
   that is `scheduled`, `dispatching` or `no_capacity`, or `skipped` for a reason the runner
   retries (`hours_cap`, `user_limit`, `code_in_use`), and not past its deadline. No row (no usable
   link) or a final skip means no sentence: the box never promises a bot the runner will not send.
"""

from datetime import date, datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app import series as series_module
from app import sprints, tasks, timezones, titles
from app.config import Settings
from app.gcal_rules import JoinSettings, will_join
from app.models import (
    CalendarConnection,
    CalendarEvent,
    Milestone,
    ScheduledJoin,
    Series,
    Sprint,
    Task,
    User,
    UserSettings,
)

SUMMARY_DAYS = 14  # a closed sprint is mentioned for this many days (14 x 24 hours)
LIVE_CONNECTIONS = ("connected", "degraded")  # `degraded` still joins on what is stored
# A join row the runner has yet to send, or is still retrying: `scheduled` (waiting for its time),
# `dispatching` (being sent now) and `no_capacity` (retried). `joined` is not one: that bot is in.
WAITING_JOIN_STATES = ("scheduled", "dispatching", "no_capacity")
OPENS_LOOKBACK_DAYS = 2  # a sprint that starts on a Saturday opens on Monday


def _first_working_day_after(day: date) -> date:
    day += timedelta(days=1)
    while day.weekday() >= 5:
        day += timedelta(days=1)
    return day


def _series_key(session: Session, found: Series) -> int | None:
    return sprints.series_key(session, found.team_id, found.project_id)


def _opens_on(sprint: Sprint, previous: Sprint | None, day: date) -> bool:
    if day == sprint.start_date:
        return True
    return (
        previous is not None
        and sprint.start_date >= previous.end_date + timedelta(days=1)  # not started early
        and day >= sprint.start_date
        and day == _first_working_day_after(previous.end_date)
    )


def _earlier_meeting_in_sprint(
    session: Session, user: User, found: Series, sprint: Sprint, starts_at: datetime, zone
) -> bool:
    """True when an earlier meeting of the series already happened, or is on the recipient's own
    calendar, since the sprint started. Only the recipient's own calendar rows and the meetings
    they may read (`series.meetings_of`) are looked at, never a teammate's calendar. Calendar
    events are kept two days after they end, so this reaches back as far as the data does; the
    date test above does the rest."""
    window = starts_at - timedelta(days=OPENS_LOOKBACK_DAYS + 2)
    if found.calendar_recurring_id:
        events = session.scalars(
            select(CalendarEvent.starts_at).where(
                CalendarEvent.recurring_event_id == found.calendar_recurring_id,
                CalendarEvent.user_id == user.id,
                CalendarEvent.status != "cancelled",
                CalendarEvent.all_day.is_(False),
                CalendarEvent.starts_at >= window,
                CalendarEvent.starts_at < starts_at,
            )
        )
        if any(timezones.localise(e, zone)[0].date() >= sprint.start_date for e in events):
            return True
    last = next(iter(series_module.meetings_of(session, found, user, limit=1)), None)
    return (
        last is not None
        and last.created_at < starts_at
        and timezones.localise(last.created_at, zone)[0].date() >= sprint.start_date
    )


def opening_sprint(
    session: Session, user: User, found: Series, starts_at: datetime, zone: str | None
) -> Sprint | None:
    """The sprint that opens with this meeting, or None (see the module note)."""
    day = timezones.localise(starts_at, zone)[0].date()
    key = _series_key(session, found)
    candidates = session.scalars(
        sprints.series_query(found.team_id, key)
        .where(
            Sprint.start_date <= day,
            Sprint.start_date >= day - timedelta(days=OPENS_LOOKBACK_DAYS),
        )
        .order_by(Sprint.number)
    )
    for sprint in candidates:
        previous = session.scalars(
            sprints.series_query(found.team_id, key).where(Sprint.number == sprint.number - 1)
        ).first()
        if _opens_on(sprint, previous, day) and not _earlier_meeting_in_sprint(
            session, user, found, sprint, starts_at, zone
        ):
            return sprint
    return None


def closed_sprint_sentence(
    session: Session, user: User, found: Series, now: datetime
) -> str | None:
    key = _series_key(session, found)
    sprint = session.scalars(
        sprints.series_query(found.team_id, key)
        .where(
            Sprint.closed_at.is_not(None),
            Sprint.closed_at >= now - timedelta(days=SUMMARY_DAYS),
        )
        .order_by(Sprint.closed_at.desc(), Sprint.number.desc())
        .limit(1)
    ).first()
    if sprint is None:
        return None
    still_there, done = session.execute(
        select(func.count(), func.count().filter(Task.status == "done")).where(
            Task.sprint_id == sprint.id, tasks.visible(user), tasks.confirmed()
        )
    ).one()
    following = session.scalars(
        sprints.series_query(found.team_id, key).where(Sprint.number == sprint.number + 1)
    ).first()
    carried = 0
    if following is not None:
        carried = session.scalar(
            select(func.count()).where(
                Task.sprint_id == following.id,
                Task.carried.is_(True),
                tasks.visible(user),
                tasks.confirmed(),
            )
        )
    total = still_there + (carried or 0)
    if total == 0:
        return None
    tasks_word = "task" if total == 1 else "tasks"
    return f"{sprints.name_of(sprint)} closed with {done} of {total} {tasks_word} done."


def milestone_sentence(session: Session, found: Series, today: date) -> str | None:
    if found.project_id is None:
        return None
    milestone = session.scalars(
        select(Milestone)
        .where(
            Milestone.team_id == found.team_id,
            Milestone.project_id == found.project_id,
            Milestone.confirmed_at.is_not(None),
            Milestone.due_date > today,
        )
        .order_by(Milestone.due_date, Milestone.id)
        .limit(1)
    ).first()
    name = titles.mail_line(milestone.name, 80) if milestone is not None else ""
    if milestone is None or not name:
        return None
    days = (milestone.due_date - today).days
    return f"{name} is in {days} day{'s' if days != 1 else ''}."


def auto_join_sentence(
    session: Session,
    user: User,
    found: Series,
    starts_at: datetime,
    settings: Settings,
    now: datetime,
) -> str | None:
    if not found.calendar_recurring_id:
        return None
    prefs = session.get(UserSettings, user.id)
    join_settings = JoinSettings(
        rule=prefs.auto_join_rule if prefs else "all",
        teams_join_enabled=settings.teams_join_enabled,
    )
    events = session.scalars(
        select(CalendarEvent).where(
            CalendarEvent.user_id == user.id,
            CalendarEvent.recurring_event_id == found.calendar_recurring_id,
            CalendarEvent.starts_at == starts_at,
        )
    )
    for event in events:
        connection = session.get(CalendarConnection, event.connection_id)
        if connection is None or connection.status not in LIVE_CONNECTIONS:
            continue
        if will_join(event, event.override, join_settings, now=now).join and _join_is_live(
            session, user, event, now
        ):
            return "The note taker will join this meeting automatically."
    return None


def _join_is_live(session: Session, user: User, event: CalendarEvent, now: datetime) -> bool:
    """The scheduler really has this meeting to join: a `ScheduledJoin` of the recipient's for the
    event that is waiting (or retrying) and not past its deadline. The rule saying yes is not
    enough: no row exists for an event with no usable link (`gcal_sync._reconcile` schedules none),
    and the runner can end a row with `needs_reconnect`, `duplicate_meeting`, `account_suspended`
    or `meeting_has_note_taker`, which are final. A `skipped` row counts only while the runner is
    still retrying it (`join_runner.RETRYABLE_SKIPS`: `hours_cap`, `user_limit`, `code_in_use`)."""
    # Imported here, not at the top: join_runner pulls in gcal_sync, services and deletion, which
    # import pipeline, which imports mail and so this module (a cycle at start-up).
    from app.join_runner import RETRYABLE_SKIPS

    join = session.scalars(
        select(ScheduledJoin).where(
            ScheduledJoin.user_id == user.id, ScheduledJoin.calendar_event_id == event.id
        )
    ).first()
    if join is None or now > join.deadline_at:
        return False
    if join.state == "skipped":
        return join.skip_reason in RETRYABLE_SKIPS
    return join.state in WAITING_JOIN_STATES


def summary_sentences(
    session: Session,
    user: User,
    found: Series,
    starts_at: datetime,
    today: date,
    settings: Settings,
    now: datetime,
) -> list[str]:
    """The box's sentences for this recipient, in order; empty when there is nothing to say."""
    parts = (
        closed_sprint_sentence(session, user, found, now),
        milestone_sentence(session, found, today),
        auto_join_sentence(session, user, found, starts_at, settings, now),
    )
    return [p for p in parts if p]
