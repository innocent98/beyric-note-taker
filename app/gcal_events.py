"""A raw Google Calendar event to the few fields auto-join needs (spec 7.4, 7.5).

Pure: no database, no network, no logging. It reads the description and location only to look for
a meeting link, then drops them; attendee addresses are reduced to a count and to the user's own
response. `ParsedEvent` is also what the pasted-link (ICS) source produces (C21), so nothing here
is Google-only except the shape of the input.
"""

import html
import re
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from app.meeting_url import UnsupportedMeetingUrl, canonical_meeting_url, parse_meeting_url

# A cancelled event in an incremental sync can come back as just `{"id", "status"}`, with no times.
# It parses to this instant for both ends; only its id and status mean anything.
NO_TIME = datetime(1970, 1, 1, tzinfo=UTC)

RESPONSES = frozenset({"accepted", "declined", "tentative", "needsAction"})
STATUSES = frozenset({"confirmed", "tentative", "cancelled"})
URL_IN_TEXT = re.compile(r"https?://[^\s<>\"']+")
TRAILING_PUNCTUATION = ".,;:!?)]}"
TEAMS_HOSTS = {
    "teams.microsoft.com": ("/l/meetup-join/", "/meet/"),
    "teams.live.com": ("/meet/",),
}


class EventParseError(ValueError):
    """The event cannot be read (no id, no usable time). The message names the problem and never
    quotes the event, which can hold a Zoom passcode."""


@dataclass(frozen=True)
class ParsedEvent:
    source_event_id: str
    recurring_event_id: str | None
    title: str
    starts_at: datetime  # always aware, always UTC
    ends_at: datetime
    platform: str | None  # google_meet, zoom, teams or None
    # Can carry a Zoom passcode, which Retry needs: kept, but out of `repr` so no log line has it.
    meeting_url: str | None = field(repr=False)
    native_id: str | None  # None for Teams until T1
    organizer_self: bool | None
    attendee_count: int
    self_response: str | None
    status: str
    event_type: str
    all_day: bool
    # True only when the organiser's address is in the connected Workspace domain. An address is
    # never kept: this is the one fact the join rule needs from it. Always False for a personal
    # Google account (no domain to share) and for a pasted link.
    organizer_same_org: bool = False


def _obj(container: object, key: str) -> dict:
    value = container.get(key) if isinstance(container, dict) else None
    return value if isinstance(value, dict) else {}


def _teams_url(url: str) -> str | None:
    """The https form of a Teams meeting link, or None when `url` is not one."""
    try:
        parts = urlsplit(url)
        host = (parts.hostname or "").lower()
    except ValueError:
        return None
    prefixes = TEAMS_HOSTS.get(host)
    if (
        parts.scheme not in ("http", "https")
        or prefixes is None
        or not parts.path.startswith(prefixes)
    ):
        return None
    # Path and query stay: the query carries the meeting context a Teams join needs.
    return f"https://{host}{parts.path}" + (f"?{parts.query}" if parts.query else "")


def _classify(url: str) -> tuple[str, str | None, str] | None:
    """(platform, native_id, canonical link) when `url` is a link we can join (or, for Teams,
    recognise). The canonical form is https, without tracking parameters."""
    url = url.strip()
    try:
        platform, native_id = parse_meeting_url(url)
        return platform, native_id, canonical_meeting_url(url)
    except UnsupportedMeetingUrl:
        pass
    teams = _teams_url(url)
    return ("teams", None, teams) if teams else None


def _urls_in_text(text: object) -> list[str]:
    if not isinstance(text, str):
        return []
    # Descriptions are often HTML: unescape entities (`&amp;` inside an href) before scanning.
    return [m.rstrip(TRAILING_PUNCTUATION) for m in URL_IN_TEXT.findall(html.unescape(text))]


def _candidate_links(raw: dict) -> list[str]:
    """Every link the event offers, in the 7.5 order: video entry points, `hangoutLink`, then the
    location and the description."""
    links: list[str] = []
    entry_points = _obj(raw, "conferenceData").get("entryPoints")
    for entry in entry_points if isinstance(entry_points, list) else []:
        if isinstance(entry, dict) and entry.get("entryPointType") == "video":
            if isinstance(entry.get("uri"), str):
                links.append(entry["uri"])
    if isinstance(raw.get("hangoutLink"), str):
        links.append(raw["hangoutLink"])
    links += _urls_in_text(raw.get("location"))
    links += _urls_in_text(raw.get("description"))
    return links


def _link(raw: dict) -> tuple[str | None, str | None, str | None]:
    for url in _candidate_links(raw):
        found = _classify(url)
        if found:
            return found[0], found[2], found[1]
    return None, None, None


def _instant(point: object, *, cancelled_stub_ok: bool) -> tuple[datetime, bool]:
    """(UTC instant, is_all_day) for a Google `start` or `end` object. The offset in `dateTime` is
    the truth: a `timeZone` field is used only when the time carries no offset at all."""
    if not isinstance(point, dict) or not point:
        if cancelled_stub_ok:
            return NO_TIME, False
        raise EventParseError("the event has no start or end time")
    try:
        if isinstance(point.get("dateTime"), str):
            moment = datetime.fromisoformat(point["dateTime"])
            if moment.tzinfo is None:
                zone = point.get("timeZone")
                if not isinstance(zone, str):
                    raise EventParseError("the event time has no offset and no time zone")
                try:
                    moment = moment.replace(tzinfo=ZoneInfo(zone))
                except (ZoneInfoNotFoundError, ValueError):
                    raise EventParseError("the event time zone is not known") from None
            return moment.astimezone(UTC), False
        if isinstance(point.get("date"), str):
            day = date.fromisoformat(point["date"])
            return datetime(day.year, day.month, day.day, tzinfo=UTC), True
    except ValueError:
        raise EventParseError("the event time is not a valid date") from None
    raise EventParseError("the event time has neither dateTime nor date")


def _same_org(organizer: object, hosted_domain: str | None) -> bool:
    email = organizer.get("email") if isinstance(organizer, dict) else None
    if not hosted_domain or not isinstance(email, str) or email.count("@") != 1:
        return False
    return email.rsplit("@", 1)[1].strip().lower() == hosted_domain.strip().lower()


def parse_event(raw: dict, *, hosted_domain: str | None = None) -> ParsedEvent:
    """`hosted_domain` is the connection's Workspace domain (`account_domain`), or None."""
    event_id = raw.get("id") if isinstance(raw, dict) else None
    if not isinstance(event_id, str) or not event_id:
        raise EventParseError("the event has no id")
    status = raw.get("status") if raw.get("status") in STATUSES else "confirmed"
    stub = status == "cancelled"
    starts_at, all_day = _instant(raw.get("start"), cancelled_stub_ok=stub)
    ends_at, _ = _instant(raw.get("end"), cancelled_stub_ok=stub)

    platform, meeting_url, native_id = _link(raw)

    organizer = raw.get("organizer")
    organizer_self = bool(organizer.get("self", False)) if isinstance(organizer, dict) else None
    attendees = raw.get("attendees")
    attendees = [a for a in attendees if isinstance(a, dict)] if isinstance(attendees, list) else []
    mine = next((a for a in attendees if a.get("self") is True), None)
    response = mine.get("responseStatus") if mine else None

    title = raw.get("summary")
    recurring = raw.get("recurringEventId")
    event_type = raw.get("eventType")
    return ParsedEvent(
        source_event_id=event_id,
        recurring_event_id=recurring if isinstance(recurring, str) and recurring else None,
        title=title if isinstance(title, str) else "",
        starts_at=starts_at,
        ends_at=ends_at,
        platform=platform,
        meeting_url=meeting_url,
        native_id=native_id,
        organizer_self=organizer_self,
        attendee_count=len(attendees),
        self_response=response if response in RESPONSES else None,
        status=status,
        event_type=event_type if isinstance(event_type, str) and event_type else "default",
        all_day=all_day,
        organizer_same_org=_same_org(organizer, hosted_domain),
    )
