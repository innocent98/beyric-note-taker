"""`GET /api/calendar/recurring`: the Recurring cards on the Meetings screen.

A read of what the calendar sync stored, grouped by the source's own `recurring_event_id`: one card
per series with its name, a cadence and the next occurrence. Nothing is fetched from Google here
and nothing is written. The sync keeps events from an hour back to two weeks ahead (and two days
after they end), so every statement about the cadence is made from that handful of times only; what
those times do not prove is simply "Recurring".

Not built here, on purpose: the unfinished-tasks count (needs W2 Tasks) and the open-questions
count (a past meeting's link to its series goes when the event row is deleted two days after it
ends, so any count would shrink silently; W8 owns series and their carry-over). The cards are not
links: the Series page is W8.

Kept apart from `app/calendar_api.py` (other work edits it); included by `app/api.py`.
"""

from collections import defaultdict
from collections.abc import Iterable
from datetime import UTC, date, datetime, timedelta, tzinfo
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from fastapi import APIRouter, Depends, Query, Request
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from app import auth, calendar_connect, calendar_providers, google_oauth, services
from app.api_schemas import ErrorResponse
from app.db import get_session
from app.models import CalendarConnection, CalendarEvent, User

router = APIRouter(prefix="/calendar", tags=["calendar"])

RESPONSES = {404: {"model": ErrorResponse}, 422: {"model": ErrorResponse}}

SERIES_LIMIT = 50
UNKNOWN_CADENCE = "Recurring"

PLURAL_WEEKDAYS = (
    "Mondays",
    "Tuesdays",
    "Wednesdays",
    "Thursdays",
    "Fridays",
    "Saturdays",
    "Sundays",
)
SHORT_WEEKDAYS = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")
SHORT_MONTHS = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")


class RecurringSeries(BaseModel):
    """One recurring meeting. `name` is the event's title; `cadence` is `Daily, 14:00`,
    `Weekly on Mondays, 14:00` (several days: `Mondays and Wednesdays`) or just `Recurring` when
    the stored times do not prove more. `next_starts_at` is the next occurrence that has not
    started, and `next_text` the same moment in the asked-for zone as `Mon 9 Nov, 14:00`. Show the
    texts as they are. No attendees, link, passcode or source id is carried."""

    name: str
    cadence: str
    next_starts_at: datetime
    next_text: str


class RecurringOut(BaseModel):
    """The cards, soonest next occurrence first; empty when no calendar is connected or it holds no
    recurring meeting (draw nothing then). `timezone` is the zone the texts are written in."""

    timezone: str
    series: list[RecurringSeries]


def _now() -> datetime:
    return datetime.now(UTC)


def _hhmm(moment: datetime) -> str:
    return f"{moment.hour:02d}:{moment.minute:02d}"


def _join(names: list[str]) -> str:
    if len(names) == 1:
        return names[0]
    return ", ".join(names[:-1]) + " and " + names[-1]


def next_text(start: datetime, zone: tzinfo) -> str:
    """`Mon 9 Nov, 14:00`: the designer's form, in English whatever the server's locale."""
    local = start.astimezone(zone)
    return (
        f"{SHORT_WEEKDAYS[local.weekday()]} {local.day} {SHORT_MONTHS[local.month - 1]}, "
        f"{_hhmm(local)}"
    )


def describe_cadence(starts: Iterable[datetime], zone: tzinfo) -> str:
    """The cadence of a series from the start times of the occurrences the calendar holds.

    A claim is made only when the times back it. All occurrences must start at the same wall-clock
    time in `zone` (a clock change between them does not matter), on different days:
    - `Daily, 14:00`: at least three, on consecutive days;
    - `Weekly on Mondays, 14:00`: every weekday that appears is seen at least twice, and the
      occurrences are an unbroken run of that weekly pattern (a missing week in the middle, a
      moved occurrence or a day seen once all give `Recurring`);
    - anything else, including a single occurrence, is `Recurring`.
    Two days in a row or two dates 14 days apart prove nothing, so they are not named."""
    local = sorted(start.astimezone(zone) for start in starts)
    if len(local) < 2:
        return UNKNOWN_CADENCE
    times = {(moment.hour, moment.minute) for moment in local}
    dates = [moment.date() for moment in local]
    if len(times) != 1 or len(set(dates)) != len(dates):
        return UNKNOWN_CADENCE
    at = _hhmm(local[0])

    if len(dates) >= 3 and all(
        (b - a) == timedelta(days=1) for a, b in zip(dates, dates[1:], strict=False)
    ):
        return f"Daily, {at}"

    by_weekday: dict[int, list[date]] = defaultdict(list)
    for day in dates:
        by_weekday[day.weekday()].append(day)
    if any(len(days) < 2 for days in by_weekday.values()):
        return UNKNOWN_CADENCE
    # The slots of the weekly pattern from the first week to the last; what the calendar holds
    # must be consecutive slots (the window may cut the first and last week, never the middle).
    first_week = dates[0] - timedelta(days=dates[0].weekday())
    last_week = dates[-1] - timedelta(days=dates[-1].weekday())
    slots: list[date] = []
    week = first_week
    while week <= last_week:
        slots.extend(week + timedelta(days=weekday) for weekday in sorted(by_weekday))
        week += timedelta(days=7)
    positions = [slots.index(day) for day in dates]  # every date is a slot by construction
    if any(b - a != 1 for a, b in zip(positions, positions[1:], strict=False)):
        return UNKNOWN_CADENCE
    names = [PLURAL_WEEKDAYS[weekday] for weekday in sorted(by_weekday)]
    return f"Weekly on {_join(names)}, {at}"


def _zone(name: str | None) -> ZoneInfo:
    if name is None:
        return ZoneInfo("UTC")
    try:
        return ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError, OSError):
        raise services.Refused(
            422, "invalid_timezone", "That time zone is not known. Use a name like Africa/Lagos."
        ) from None


def _enabled(request: Request, google: google_oauth.GoogleOAuth | None) -> None:
    settings = request.app.state.settings
    microsoft_on = calendar_providers.PROVIDERS["microsoft"].enabled(settings)
    if not settings.calendar_enabled or (google is None and not microsoft_on):
        raise services.Refused(404, "calendar_unavailable", "Google Calendar is not available.")


def _series(
    session: Session, conn: CalendarConnection, zone: ZoneInfo, now: datetime
) -> list[RecurringSeries]:
    """Meetings only: not cancelled, not all-day, not out-of-office or focus time and the like."""
    rows = session.scalars(
        select(CalendarEvent)
        .where(
            CalendarEvent.connection_id == conn.id,
            CalendarEvent.user_id == conn.user_id,
            CalendarEvent.recurring_event_id.is_not(None),
            CalendarEvent.status != "cancelled",
            CalendarEvent.all_day.is_(False),
            CalendarEvent.event_type == "default",
        )
        .order_by(CalendarEvent.starts_at, CalendarEvent.id)
    ).all()
    groups: dict[str, list[CalendarEvent]] = defaultdict(list)
    for row in rows:
        groups[row.recurring_event_id].append(row)
    cards = []
    for events in groups.values():
        ahead = [event for event in events if event.starts_at > now]
        if not ahead or not ahead[0].title.strip():
            continue  # nothing to be next, or no name to draw
        upcoming = ahead[0]
        cards.append(
            RecurringSeries(
                name=upcoming.title,
                cadence=describe_cadence((event.starts_at for event in events), zone),
                next_starts_at=upcoming.starts_at.astimezone(UTC),
                next_text=next_text(upcoming.starts_at, zone),
            )
        )
    cards.sort(key=lambda card: (card.next_starts_at, card.name))
    return cards[:SERIES_LIMIT]


@router.get("/recurring", responses=RESPONSES)
def calendar_recurring(
    request: Request,
    tz: str | None = Query(None, max_length=64, description="IANA zone name, e.g. Africa/Lagos"),
    session: Session = Depends(get_session),
    user: User = Depends(auth.current_user),
    google: google_oauth.GoogleOAuth | None = Depends(google_oauth.get_google),
) -> RecurringOut:
    """The Recurring cards of the Meetings screen (no CSRF header needed): each recurring meeting
    on the connected calendar with a cadence worked out from its stored times, and its next
    occurrence. `tz` is the viewer's zone (the browser's `Intl.DateTimeFormat().resolvedOptions().
    timeZone`); without it the texts are in UTC, and the answer says which zone it used. Empty
    when no calendar is connected or nothing recurs. Nothing is fetched from Google. 404
    `calendar_unavailable` while the calendar features are off, 422 `invalid_timezone` for a zone
    name that is not known."""
    _enabled(request, google)
    zone = _zone(tz)
    conn = calendar_connect.live_calendar_connection(session, user)
    series = _series(session, conn, zone, _now()) if conn is not None else []
    return RecurringOut(timezone=zone.key, series=series)
