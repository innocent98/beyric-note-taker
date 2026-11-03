"""What every calendar source (Google now, a pasted ICS link later) gives the sync loop.

A source turns "what changed on this connection" into a `SyncBatch` of raw event dicts, which
`app/gcal_events.py` parses; the sync loop (C8, C8b) decides what to store. The contract that keeps
a half-read calendar from cancelling meetings: a batch is only `complete` when every page was read
and the source handed back the token for the next incremental call. An incomplete batch never
carries a token, so the caller cannot advance one by accident.
"""

from dataclasses import dataclass
from datetime import datetime
from typing import Protocol, runtime_checkable


class CalendarSourceError(Exception):
    """Base of the three ways a source call fails. Messages carry a status or an exception type and
    never a token, a URL or any event data."""


class SyncTokenExpired(CalendarSourceError):
    """The stored sync token is no longer honoured (Google's 410). The caller runs a full sync."""


class AuthRevoked(CalendarSourceError):
    """The user's grant is gone or too narrow (a revoked or expired refresh token, a missing
    scope). Only a reconnect helps: the caller marks the connection `needs_reconnect`."""


class TransientError(CalendarSourceError):
    """A timeout, a 5xx, a rate limit or anything else that may pass on its own. The caller keeps
    its state, records the error and retries later."""


@dataclass(frozen=True)
class SyncBatch:
    """The result of one `sync` call.

    `raw_events` are the events read, unparsed. `full` says the call listed the whole window (so
    events missing from it were removed or moved out), as opposed to returning only changes.
    `complete` says every page was read. `next_sync_token` is the token for the next incremental
    call, and is `None` unless the batch is complete.
    """

    raw_events: list[dict]
    next_sync_token: str | None
    complete: bool
    full: bool

    def __post_init__(self) -> None:
        if not self.complete and self.next_sync_token is not None:
            raise ValueError("an incomplete batch must not carry a sync token")


@runtime_checkable
class CalendarSource(Protocol):
    provider: str

    def sync(self, conn, *, now: datetime, full: bool) -> SyncBatch:
        """Read the connection's events. `full` forces a listing of the whole window; otherwise
        the connection's stored sync token is used when there is one. Raises `SyncTokenExpired`,
        `AuthRevoked` or `TransientError`."""
        ...

    def get_event(self, conn, source_event_id: str) -> dict | None:
        """One event as it is now, or `None` when the source no longer has it."""
        ...
