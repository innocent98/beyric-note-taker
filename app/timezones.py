"""A person's time zone: which names are accepted, and how a time is written in one.

The label is the same style the web pages show ("UTC", "GMT", "GMT+1", "GMT+5:30"), never the
abbreviation tables ("WAT", "EST"), so a mail and the page it links to read alike.
"""

from datetime import UTC, datetime
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


def zone(name: str | None) -> ZoneInfo:
    """The zone, or UTC for no name or one the server does not know."""
    if not name:
        return ZoneInfo("UTC")
    try:
        return ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError, OSError):
        return ZoneInfo("UTC")


def is_known(name: str) -> bool:
    if not name or len(name) > 64:
        return False
    try:
        ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError, OSError):
        return False
    return True


def label(moment: datetime) -> str:
    """ "UTC" for the UTC zone itself, else "GMT" and the offset: "GMT", "GMT+1", "GMT+5:30"."""
    offset = moment.utcoffset()
    if moment.tzname() == "UTC" or offset is None:
        return "UTC"
    minutes = int(offset.total_seconds() // 60)
    if minutes == 0:
        return "GMT"
    sign = "+" if minutes > 0 else "-"
    hours, rest = divmod(abs(minutes), 60)
    return f"GMT{sign}{hours}" + (f":{rest:02d}" if rest else "")


def localise(moment: datetime, name: str | None) -> tuple[datetime, str]:
    """The instant in the named zone (UTC when none), and its label."""
    local = moment.astimezone(zone(name) if name else UTC)
    return local, label(local)
