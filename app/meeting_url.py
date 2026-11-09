"""Reading a Google Meet, Zoom or Microsoft Teams link: which platform, which meeting code, and the
clean URL a bot is sent to. Nothing here talks to a bot provider, so every provider
(`app/bot_provider.py`) and the calendar code share one rule for what a meeting link is.
`app/vexa.py` re-exports these names."""

import hashlib
import re
from urllib.parse import parse_qs, unquote, urlencode, urlsplit

ZOOM_PATH = re.compile(r"^/(?:j|wc/join)/(\d+)/?$")
MEET_PATH = re.compile(r"^/([a-z]{3}-[a-z]{4}-[a-z]{3})/?$")
# Teams: `/l/meetup-join/<thread id>/<n>` (the thread id is percent-encoded, sometimes raw) and the
# newer short `/meet/<digits>`.
TEAMS_JOIN_PATH = re.compile(r"^/l/meetup-join/([A-Za-z0-9%._:@~-]+)(?:/[0-9]{1,4})?/?$")
# The thread id (decoded) must have Teams' own shape, `19:meeting_<token>@thread.v2`, so junk
# does not become a meeting code.
TEAMS_THREAD = re.compile(r"^19:[A-Za-z0-9_=-]+@thread\.[A-Za-z0-9]+$")
TEAMS_MEET_PATH = re.compile(r"^/meet/([0-9]{6,20})/?$")
# The query of a Teams link is kept exactly as written (it carries the tenant and organiser context
# or the `p` passcode, percent-encoded): only these characters may be in it.
TEAMS_QUERY = re.compile(r"^[A-Za-z0-9._~%:,@+=&/;!*()$-]*$")
MAX_TEAMS_URL_CHARS = 1500
SAFELINKS_HOST = re.compile(r"^[a-z0-9-]+\.safelinks\.protection\.outlook\.com$")


class UnsupportedMeetingUrl(ValueError):
    """The URL is not a Google Meet, Zoom or Microsoft Teams meeting link."""


def _teams(host: str, parts, url: str) -> tuple[str, str, str] | None:
    """(platform, native_id, canonical) for a Teams link on `host`, or None when its path is not a
    Teams meeting's.

    A Teams link has no short meeting code, so the code is derived: `teams-` and the first 24 hex
    digits of the SHA-256 of the link's identity (the percent-decoded thread id for a `meetup-join`
    link, the digits for a `/meet/` one, each with the host family). The query (tenant context,
    passcode) is not part of the identity, so two links to one meeting give one code and the "one
    bot per meeting code" rule holds; the code is a fixed-length token that fits the code columns
    and `[A-Za-z0-9-]+`, and the thread id is not readable back from it. The link sent on is
    the pasted one, rebuilt https, host lower-cased, path and query untouched, fragment dropped."""
    if len(url) > MAX_TEAMS_URL_CHARS or not TEAMS_QUERY.match(parts.query):
        raise UnsupportedMeetingUrl("unsupported meeting URL")
    if host == "teams.microsoft.com" and (m := TEAMS_JOIN_PATH.match(parts.path)):
        thread = unquote(m.group(1))
        if not TEAMS_THREAD.match(thread):
            raise UnsupportedMeetingUrl("unsupported meeting URL")
        identity = f"teams.microsoft.com/meetup-join/{thread}"
    elif host in ("teams.microsoft.com", "teams.live.com") and (
        m := TEAMS_MEET_PATH.match(parts.path)
    ):
        identity = f"{host}/meet/{m.group(1)}"
    else:
        return None
    native_id = "teams-" + hashlib.sha256(identity.encode()).hexdigest()[:24]
    canonical = f"https://{host}{parts.path}" + (f"?{parts.query}" if parts.query else "")
    return "teams", native_id, canonical


def _parse(url: str, _wrapped: bool = False) -> tuple[str, str, str]:
    """Return (platform, native_id, canonical_url), rejecting ambiguous input."""
    if not isinstance(url, str):
        raise UnsupportedMeetingUrl("not a string")
    url = url.strip()
    if "\\" in url or any(ch.isspace() or ord(ch) < 32 or ord(ch) == 127 for ch in url):
        raise UnsupportedMeetingUrl("unsupported characters in meeting URL")
    try:
        parts = urlsplit(url)
        host = (parts.hostname or "").lower()
        port = parts.port
    except ValueError as exc:
        raise UnsupportedMeetingUrl("malformed meeting URL") from exc
    if parts.scheme not in ("http", "https") or "@" in parts.netloc or port is not None:
        raise UnsupportedMeetingUrl("unsupported meeting URL")
    if host == "zoom.us" or host.endswith(".zoom.us"):
        match = ZOOM_PATH.match(parts.path)
        if match:
            canonical = f"https://{host}{parts.path}"
            pwd = parse_qs(parts.query).get("pwd")
            if pwd and pwd[0]:
                canonical += "?" + urlencode({"pwd": pwd[0]})
            return "zoom", match.group(1), canonical
    elif host == "meet.google.com":
        match = MEET_PATH.match(parts.path)
        if match:
            return "google_meet", match.group(1), f"https://{host}{parts.path}"
    elif host in ("teams.microsoft.com", "teams.live.com"):
        found = _teams(host, parts, url)
        if found:
            return found
    elif not _wrapped and SAFELINKS_HOST.match(host) and parts.path in ("", "/"):
        # Outlook's Safe Links wrapper: read the Teams link inside (Skribby would unwrap it, but the
        # code and the dedupe need the inner link), and send that inner link. One level here: a
        # wrapper inside a wrapper is refused. (Outlook's own event reader, `graph_events`,
        # unwraps its first level itself before calling this, so from an Outlook event a link
        # wrapped twice is read; a pasted one is not.)
        inner = parse_qs(parts.query).get("url")
        if inner and inner[0]:
            found = _parse(inner[0], _wrapped=True)
            if found[0] == "teams":
                return found
    raise UnsupportedMeetingUrl("unsupported meeting URL")


def parse_meeting_url(url: str) -> tuple[str, str]:
    """Return (platform, native_meeting_id) for a Zoom, Google Meet or Teams URL."""
    platform, native_id, _ = _parse(url)
    return platform, native_id


def canonical_meeting_url(url: str) -> str:
    """The URL the bot is sent to: https, rebuilt from its parsed parts, the Zoom passcode kept and
    every other query parameter dropped (a Teams link keeps its whole path and query). Raises
    `UnsupportedMeetingUrl` like `parse_meeting_url`."""
    return _parse(url)[2]
