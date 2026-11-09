"""A raw Microsoft Graph calendar event to the few fields auto-join needs (Outlook spec section 5).

Pure, like `app/gcal_events.py`: no database, no network, no logging. It reads the body and the
locations only to look for a meeting link, then drops them; attendee addresses are reduced to a
count, the organiser's address to one boolean, and the user's own reply to one word. The result is
the same `ParsedEvent` Google and the pasted-link source produce, so the sync, the rules
(`gcal_rules.will_join`), the joins and the screens read Microsoft rows exactly as they read
Google ones.

What Graph sends is untrusted: anyone can send an invite, so the title, the body and the locations
are attacker-controlled. The title is only passed on (the sync cleans and cuts it). The body and
locations are scanned for links (the body at most `SCAN_LIMIT` characters) and dropped. A link is
used only if the existing `_classify` accepts it (a Zoom or Google Meet meeting link or a Teams
meeting link: https, no credentials, no port). A Microsoft Safe Links wrapper is unwrapped only
when its host is exactly under `.safelinks.protection.outlook.com`, and only the one level, and
the target must then pass `_classify` too. A reply we cannot read is "unanswered", never
"accepted": the unanswered-invite safeguard in `will_join` depends on it.

Times (spec 5.3). The sync asks Graph for UTC (`Prefer: outlook.timezone="UTC"`), so `start` and
`end` arrive as `{"dateTime": "2026-11-05T10:00:00.0000000", "timeZone": "UTC"}`. Anything in
another zone is refused (`EventParseError`) rather than converted: this module never maps a
Windows zone name ("W. Europe Standard Time") itself, so a wrong offset can never reach a join. An
all-day event keeps its midnight-to-midnight instants with `all_day=True` and is never joined.
"""

import re
from datetime import UTC, datetime
from urllib.parse import parse_qs, urlsplit

from app.gcal_events import (
    EventParseError,
    ParsedEvent,
    _classify,
    _obj,
    _urls_in_text,
)

# At most this much of an event body is scanned for links: a hostile invite can carry megabytes.
SCAN_LIMIT = 200_000
LOCATION_LIMIT = 20  # `locations` entries read
SAFE_LINKS_SUFFIX = ".safelinks.protection.outlook.com"

# `responseStatus.response` to the word `will_join` reads. `notResponded` is the invitee's own
# "has not answered" and must stay `needsAction`. `organizer` is the organiser's own value.
# `none` appears on meetings with no invitees: no answer to wait for.
RESPONSES = {
    "accepted": "accepted",
    "tentativelyAccepted": "tentative",
    "declined": "declined",
    "notResponded": "needsAction",
    "organizer": "accepted",
    "none": None,
}


def _instant(point: object) -> datetime:
    """The UTC instant of a Graph `dateTimeTimeZone`, which must say `UTC`."""
    if not isinstance(point, dict) or not point:
        raise EventParseError("the event has no start or end time")
    if point.get("timeZone") != "UTC":
        raise EventParseError("the event time is not in UTC")
    text = point.get("dateTime")
    if not isinstance(text, str):
        raise EventParseError("the event time has no dateTime")
    try:
        moment = datetime.fromisoformat(text)
    except ValueError:
        raise EventParseError("the event time is not a valid date") from None
    if moment.tzinfo is None:
        return moment.replace(tzinfo=UTC)
    if moment.utcoffset() != UTC.utcoffset(None):
        raise EventParseError("the event time carries an offset")
    return moment.astimezone(UTC)


def _safelinks_target(url: str) -> str | None:
    """The real link inside a Microsoft Safe Links wrapper, or None when `url` is not one."""
    try:
        parts = urlsplit(url.strip())
        host = (parts.hostname or "").lower()
    except ValueError:
        return None
    if parts.scheme != "https" or not host.endswith(SAFE_LINKS_SUFFIX):
        return None
    target = parse_qs(parts.query).get("url")
    return target[0] if target else None


def _resolve(url: str) -> tuple[str, str | None, str] | None:
    """`_classify` for a link, looking one level inside a Safe Links wrapper first."""
    target = _safelinks_target(url)
    return _classify(target if target is not None else url)


def _candidate_links(raw: dict) -> list[str]:
    """Every link the event offers, in the spec 5.5 order: the Teams join URL of an online
    meeting, then the location texts (Zoom and Meet add-ins write the link there), then the body
    (HTML: `_urls_in_text` unescapes entities, so a hidden `href` is found)."""
    links: list[str] = []
    meeting = _obj(raw, "onlineMeeting")
    if raw.get("isOnlineMeeting") is True and isinstance(meeting.get("joinUrl"), str):
        links.append(meeting["joinUrl"])
    texts = [_obj(raw, "location").get("displayName")]
    locations = raw.get("locations")
    if isinstance(locations, list):
        texts += [_name(item) for item in locations[:LOCATION_LIMIT]]
    for text in texts:
        links += _urls_in_text(text[:SCAN_LIMIT] if isinstance(text, str) else None)
    body = _obj(raw, "body").get("content")
    links += _urls_in_text(body[:SCAN_LIMIT] if isinstance(body, str) else None)
    return links


def _name(location: object) -> object:
    return location.get("displayName") if isinstance(location, dict) else None


def _link(raw: dict) -> tuple[str | None, str | None, str | None]:
    for url in _candidate_links(raw):
        found = _resolve(url)
        if found:
            return found[0], found[2], found[1]
    return None, None, None


def _domains(tenant_domains: str | None) -> frozenset[str]:
    """The organisation's verified domains, from the connection's `tenant_domains` text
    (separated by whitespace, commas or semicolons), lower-cased."""
    if not tenant_domains:
        return frozenset()
    return frozenset(d for d in re.split(r"[\s,;]+", tenant_domains.lower()) if d)


def _same_org(organizer: object, domains: frozenset[str]) -> bool:
    """True only when the organiser's address is in one of the organisation's domains. The address
    is never kept. Always false while `domains` is empty (personal accounts, or a work account
    whose domains were not read): false is the safe direction, an unanswered invite then waits."""
    address = _obj(organizer, "emailAddress").get("address")
    if not domains or not isinstance(address, str) or address.count("@") != 1:
        return False
    return address.rsplit("@", 1)[1].strip().lower() in domains


def _attendee_count(attendees: object) -> int:
    """Graph's `attendees` leaves the organiser out (ASSUMED, to confirm in M0), where Google's
    list has everyone: add one so "N people" means the same across providers. No attendees at all
    is a solo event: 0."""
    if not isinstance(attendees, list):
        return 0
    people = [a for a in attendees if isinstance(a, dict)]
    return len(people) + 1 if people else 0


def parse_event(raw: dict, *, tenant_domains: str | None = None) -> ParsedEvent:
    """`tenant_domains`: the connection's verified organisation domains (work), or None."""
    event_id = raw.get("id") if isinstance(raw, dict) else None
    if not isinstance(event_id, str) or not event_id:
        raise EventParseError("the event has no id")
    starts_at = _instant(raw.get("start"))
    ends_at = _instant(raw.get("end"))
    all_day = raw.get("isAllDay") is True

    platform, meeting_url, native_id = _link(raw)

    attendees = raw.get("attendees")
    is_organizer = raw.get("isOrganizer")
    if isinstance(is_organizer, bool):
        organizer_self: bool | None = is_organizer
    else:
        # Graph always sends the flag; a missing one on an event with other people is read as
        # "not the organiser", the cautious side of the unanswered-invite rule.
        organizer_self = False if _attendee_count(attendees) else None
    response = _obj(raw, "responseStatus").get("response")

    title = raw.get("subject")
    series = raw.get("seriesMasterId")
    return ParsedEvent(
        source_event_id=event_id,
        recurring_event_id=series if isinstance(series, str) and series else None,
        title=title if isinstance(title, str) else "",
        starts_at=starts_at,
        ends_at=ends_at,
        platform=platform,
        meeting_url=meeting_url,
        native_id=native_id,
        organizer_self=organizer_self,
        attendee_count=_attendee_count(attendees),
        self_response=RESPONSES.get(response) if isinstance(response, str) else None,
        status="cancelled" if raw.get("isCancelled") is True else "confirmed",
        event_type="default",
        all_day=all_day,
        organizer_same_org=_same_org(raw.get("organizer"), _domains(tenant_domains)),
    )
