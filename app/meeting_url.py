"""Reading a Google Meet or Zoom link: which platform, which meeting code, and the clean URL a
bot is sent to. Nothing here talks to a bot provider, so every provider (`app/bot_provider.py`) and
the calendar code share one rule for what a meeting link is. `app/vexa.py` re-exports these
names."""

import re
from urllib.parse import parse_qs, urlencode, urlsplit

ZOOM_PATH = re.compile(r"^/(?:j|wc/join)/(\d+)/?$")
MEET_PATH = re.compile(r"^/([a-z]{3}-[a-z]{4}-[a-z]{3})/?$")


class UnsupportedMeetingUrl(ValueError):
    """The URL is not a Google Meet or Zoom meeting link."""


def _parse(url: str) -> tuple[str, str, str]:
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
    raise UnsupportedMeetingUrl("unsupported meeting URL")


def parse_meeting_url(url: str) -> tuple[str, str]:
    """Return (platform, native_meeting_id) for a Zoom or Google Meet URL."""
    platform, native_id, _ = _parse(url)
    return platform, native_id


def canonical_meeting_url(url: str) -> str:
    """The URL the bot is sent to: https, rebuilt from its parsed parts, the Zoom passcode kept and
    every other query parameter dropped. Raises `UnsupportedMeetingUrl` like `parse_meeting_url`."""
    return _parse(url)[2]
