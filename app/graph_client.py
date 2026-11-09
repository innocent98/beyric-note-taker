"""The Microsoft Graph calendar client: `MicrosoftSource` (the `CalendarSource` for provider
`microsoft`) and `MicrosoftTokenCache` (Outlook spec sections 4.3, 5.1, 5.7, 8.1 #1, #6, #9).

What it calls, and nothing else: Microsoft's token endpoint (through `MicrosoftOAuth.refresh`) to
turn a refresh token into an access token, and the primary calendar's `calendarView` and
`events/{id}`. Every Graph URL is `graph_http.GRAPH_BASE` plus a path this module wrote, or an
`@odata.nextLink` that starts with `https://graph.microsoft.com/v1.0/` (anything else ends the
read as an incomplete batch and the `Authorization` header never goes there). Redirects are not
followed, no proxy is read from the environment, every call has a timeout on all four stages
(`app/calendar_http.py`), and an event id is quoted into one path segment.

Sync (spec 5.1, D7). A **full window every time**: now minus one hour to now plus fourteen days,
`$top=250`, following `@odata.nextLink` to the last page. There is no delta token, so a batch is
`complete` when the last page arrived, and `full=True` makes the sync's existing "events not named
were removed or moved out" sweep apply. A page that fails after the first returns what was read as
an incomplete batch (no cancellations are applied); the first page failing, a revoked grant or a
refused token raises. `calendarView` already expands a series into occurrences, so nothing is
expanded here. Both `Prefer` values go on every event call as two header lines:
`outlook.timezone="UTC"` (so `app/graph_events.py` never converts a zone) and `IdType="ImmutableId"`
(so an event keeps its id when it moves between folders).

Tokens. The refresh token is decrypted for the length of one refresh request (`vault`, purpose
`ms_refresh_token`) and goes only into that request's form body. **Microsoft rotates the refresh
token on every use** (spec 4.3): `MicrosoftTokenCache._mint` saves the new one with a
compare-and-swap, `UPDATE calendar_connections SET refresh_token_enc = :new WHERE id = :id AND
refresh_token_enc = :previous`, in its own short transaction, so a stale writer never overwrites a
newer token. If the write fails or loses the race the sync carries on with the access token and the
old refresh token still works until its own expiry: a lost write is not an outage. The new token
is encrypted under the current `TOKEN_ENC_KEY` version, so Microsoft rows migrate to a new key by
themselves. Access tokens live in memory only (`vault.Secret`), five minutes short of their expiry.
No log line, exception message or `repr` holds a token, a response body, a URL or event data:
failures carry an HTTP status, a short Graph error code from a fixed list, or an exception type.

Throttling (spec 8.1 #9). Graph allows far more (10,000 requests per 10 minutes, four at once per
mailbox) than two or three calls a minute; a 429, a 503 or a 504 is a `TransientError` and the
connection retries at the pace the sync already uses after any error. One connection is never read
by two calls at once (the per-connection lock around a mint, and the sync loop's own order).

The client secret. `invalid_client` at the token endpoint means OUR secret is wrong or expired, not
the user's grant: it is transient for the connection, and when Microsoft names the expiry
(AADSTS7000222) the admins are told once a day (`app/microsoft_alerts.py`). From 30 days before
`MICROSOFT_SECRET_EXPIRES_ON` the sync also reminds them, once a day.
"""

import logging
import threading
from collections.abc import Callable
from contextlib import AbstractContextManager
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from urllib.parse import quote

import httpx
from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app import calendar_http, graph_http, microsoft_alerts, microsoft_oauth, vault
from app.calendar_source import (
    AuthRevoked,
    RefreshTokenUnreadable,
    SyncBatch,
    TransientError,
)
from app.config import Settings, get_settings
from app.microsoft_oauth import ClientRejected, MicrosoftOAuth
from app.models import CalendarConnection

logger = logging.getLogger(__name__)

WINDOW_BEFORE = timedelta(hours=1)
WINDOW_AFTER = timedelta(days=14)
PAGE_SIZE = 250
# A runaway chain (a bug, a hostile answer) stops here and the batch is incomplete: 40 pages of
# 250 is two orders of magnitude over what one person's two weeks hold.
MAX_PAGES = 40
REFRESH_EARLY = timedelta(minutes=5)  # refresh this long before the stated expiry
REUSE_MARGIN = timedelta(seconds=30)  # a transient refresh failure rides a token this long
SELECT = (
    "id,iCalUId,subject,start,end,isAllDay,isCancelled,isDraft,isOrganizer,organizer,"
    "responseStatus,attendees,onlineMeeting,onlineMeetingProvider,isOnlineMeeting,location,"
    "locations,body,seriesMasterId,type,sensitivity,showAs"
)
# Both preferences, as two header lines (spec 5.1; the M0 run confirms Graph honours both).
PREFER = (("Prefer", 'outlook.timezone="UTC"'), ("Prefer", 'IdType="ImmutableId"'))
# 403 codes that mean the user's grant does not cover this call; every other 403 (a throttle in
# disguise, a tenant policy that may lift) is not something reconnecting would fix. These strings
# are documented by experience, not by Microsoft's pages: the M0 run (S6) confirms them.
GRANT_CODES = frozenset({"ErrorAccessDenied", "Authorization_RequestDenied"})


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _graph_time(moment: datetime) -> str:
    return moment.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _error_code(response: httpx.Response) -> str | None:
    """Graph's `error.code` when it is a plain identifier, else None."""
    try:
        code = response.json()["error"]["code"]
    except (ValueError, KeyError, TypeError):
        return None
    return code if isinstance(code, str) and code.isidentifier() and len(code) <= 80 else None


# ---- the refresh token on the connection row ----


class ConnectionTokenStore:
    """Reads and swaps the encrypted refresh token of one connection row, each in its own short
    transaction (no lock is held while Microsoft is being asked)."""

    def __init__(self, session_factory: Callable[[], AbstractContextManager[Session]]) -> None:
        self._session_factory = session_factory

    def read(self, conn_id: int) -> tuple[bytes, int, int, str | None] | None:
        """(blob, key version, user id, tenant id) of the row now, or None if it has no token."""
        with self._session_factory() as session:
            row = session.execute(
                select(
                    CalendarConnection.refresh_token_enc,
                    CalendarConnection.key_version,
                    CalendarConnection.user_id,
                    CalendarConnection.tenant_id,
                ).where(
                    CalendarConnection.id == conn_id, CalendarConnection.provider == "microsoft"
                )
            ).first()
        if row is None or row[0] is None or row[1] is None:
            return None
        return bytes(row[0]), row[1], row[2], row[3]

    def swap(self, conn_id: int, previous: bytes, new: bytes, key_version: int) -> bool:
        """Compare-and-swap: the new token replaces `previous` only if `previous` is still what
        the row holds. False when another writer got there first (or the row is gone)."""
        with self._session_factory() as session:
            result = session.execute(
                update(CalendarConnection)
                .where(
                    CalendarConnection.id == conn_id,
                    CalendarConnection.provider == "microsoft",
                    CalendarConnection.refresh_token_enc == previous,
                )
                .values(refresh_token_enc=new, key_version=key_version)
            )
            session.commit()
            return result.rowcount == 1


@dataclass
class _Cached:
    token: vault.Secret
    expires_at: datetime


class MicrosoftTokenCache:
    """Access tokens minted from refresh tokens, per connection, in memory only, with the
    refresh token's rotation saved to the database (module docstring).

    `get` returns a usable token, minting one when there is none or less than five minutes
    remain. Concurrent callers for one connection mint once.
    """

    def __init__(
        self,
        clock: Callable[[], datetime] | None = None,
        *,
        settings: Settings | None = None,
        transport: httpx.BaseTransport | None = None,
        store: ConnectionTokenStore | None = None,
    ) -> None:
        self._clock = clock or _utc_now
        self._settings = settings
        self._transport = transport
        self._store = store
        self._tokens: dict[int, _Cached] = {}
        self._locks: dict[int, threading.Lock] = {}
        self._guard = threading.Lock()

    def __repr__(self) -> str:
        return f"<MicrosoftTokenCache {len(self._tokens)} tokens, redacted>"

    __str__ = __repr__

    def __reduce__(self):
        raise TypeError("a MicrosoftTokenCache cannot be pickled")

    def _token_store(self) -> ConnectionTokenStore:
        if self._store is None:
            from app.db import SessionLocal  # not at the top: keeps this module light to import

            self._store = ConnectionTokenStore(SessionLocal)
        return self._store

    def get(self, conn: CalendarConnection) -> str:
        with self._guard:
            lock = self._locks.setdefault(conn.id, threading.Lock())
        with lock:
            now = self._clock()
            cached = self._tokens.get(conn.id)
            if cached and now < cached.expires_at - REFRESH_EARLY:
                return cached.token.reveal()
            try:
                token, lifetime = self._mint(conn)
            except TransientError:
                # Inside the early window the old token still works: ride it out rather than fail.
                if cached and now < cached.expires_at - REUSE_MARGIN:
                    return cached.token.reveal()
                raise
            except AuthRevoked:
                self._tokens.pop(conn.id, None)
                raise
            self._tokens[conn.id] = _Cached(vault.Secret(token), now + lifetime)
            return token

    def invalidate(self, conn_id: int) -> None:
        """Drop one connection's cached token (Graph refused it); the next `get` mints again."""
        with self._guard:
            self._tokens.pop(conn_id, None)

    def forget(self, conn_id: int) -> None:
        """Drop everything held for a connection (disconnect)."""
        with self._guard:
            self._tokens.pop(conn_id, None)
            self._locks.pop(conn_id, None)

    def _mint(self, conn: CalendarConnection) -> tuple[str, timedelta]:
        settings = self._settings or get_settings()
        if not microsoft_oauth.enabled(settings):
            raise TransientError("the Microsoft OAuth client is not configured")
        store = self._token_store()
        current = store.read(conn.id)
        if current is None:
            raise AuthRevoked("the connection has no refresh token")
        blob, key_version, user_id, tenant_id = current
        if not tenant_id:
            raise AuthRevoked("the connection has no tenant")
        try:
            refresh_token = vault.decrypt(
                blob, key_version, purpose="ms_refresh_token", owner=user_id
            )
        except vault.VaultUnavailable:
            # The server's key is missing or broken: the user's grant is fine.
            raise TransientError("the token vault is unavailable") from None
        except vault.VaultError:
            raise RefreshTokenUnreadable("the stored refresh token cannot be read") from None
        oauth = MicrosoftOAuth(settings, transport=self._transport)
        try:
            refreshed = oauth.refresh(refresh_token, tenant_id=tenant_id)
        except ClientRejected as exc:
            if exc.secret_expired:
                microsoft_alerts.secret_expired(self._clock())
            raise
        if refreshed.refresh_token is not None:
            self._save_rotation(conn.id, user_id, blob, refreshed.refresh_token)
        return refreshed.access_token.reveal(), refreshed.lifetime

    def _save_rotation(
        self, conn_id: int, user_id: int, previous: bytes, new_token: vault.Secret
    ) -> None:
        """Save the NEW refresh token over `previous` (compare-and-swap). Never raises: a lost
        write leaves the old token, which still works until its own expiry."""
        try:
            new_blob, version = vault.encrypt(
                new_token.reveal(), purpose="ms_refresh_token", owner=user_id
            )
            saved = self._token_store().swap(conn_id, previous, new_blob, version)
        except Exception as exc:  # a database or vault problem must not fail the sync
            logger.warning(
                "microsoft refresh token for connection %s not saved (%s)",
                conn_id,
                type(exc).__name__,
            )
            return
        if not saved:
            logger.warning(
                "microsoft refresh token for connection %s not saved (another writer won)",
                conn_id,
            )


# ---- the source ----


class MicrosoftSource:
    """`CalendarSource` for a Microsoft connection (primary calendar, a full window each sync)."""

    provider = "microsoft"

    def __init__(
        self,
        settings: Settings | None = None,
        *,
        cache: MicrosoftTokenCache | None = None,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self._settings = settings
        self._transport = transport
        self._cache = cache or MicrosoftTokenCache(settings=settings, transport=transport)
        self._reminded_on: date | None = None

    @property
    def cache(self) -> MicrosoftTokenCache:
        return self._cache

    def sync(self, conn: CalendarConnection, *, now: datetime, full: bool) -> SyncBatch:
        """Read the whole window. `full` is ignored: there is no delta token, every sync is a full
        listing, so events missing from a complete one are treated as removed (spec D7)."""
        if now.tzinfo is None:
            raise ValueError("now must be timezone-aware")
        self._remind_about_the_secret(now)
        params: dict[str, str | int] = {
            "startDateTime": _graph_time(now - WINDOW_BEFORE),
            "endDateTime": _graph_time(now + WINDOW_AFTER),
            "$select": SELECT,
            "$top": PAGE_SIZE,
        }
        url: str = graph_http.graph_url("/me/calendarView")
        events: list[dict] = []
        for page in range(MAX_PAGES):
            try:
                body = self._list_page(conn, url, params if page == 0 else None)
            except TransientError as exc:
                if page == 0:
                    raise
                return self._incomplete(conn, events, f"page {page + 1}: {exc}")
            events.extend(body["value"])
            next_link = body.get("@odata.nextLink")
            if next_link is None:
                return SyncBatch(events, None, complete=True, full=True)
            if not graph_http.under_graph_base(next_link):
                # A URL from Microsoft's answer that is not under the Graph base is never
                # followed, and the Authorization header never goes there.
                return self._incomplete(conn, events, "unusable next link")
            url = next_link
        return self._incomplete(conn, events, f"over {MAX_PAGES} pages")

    def get_event(self, conn: CalendarConnection, source_event_id: str) -> dict | None:
        # "." and ".." would be folded into the URL path by the HTTP client and reach another
        # resource; everything else is quoted into one path segment.
        if not isinstance(source_event_id, str) or source_event_id in ("", ".", ".."):
            raise ValueError("not an event id")
        url = graph_http.graph_url(f"/me/events/{quote(source_event_id, safe='')}")
        response = self._call(conn, url, {"$select": SELECT})
        if response.status_code in (404, 410):
            return None  # ErrorItemNotFound: the source no longer has it
        self._check(response)
        return calendar_http.json_object(response, "Microsoft")

    # ---- internals ----

    def _list_page(self, conn: CalendarConnection, url: str, params: dict | None) -> dict:
        response = self._call(conn, url, params)
        self._check(response)
        body = calendar_http.json_object(response, "Microsoft")
        items = body.get("value")
        if not isinstance(items, list) or not all(isinstance(item, dict) for item in items):
            raise TransientError("Microsoft sent a malformed list of events")
        return body

    def _call(self, conn: CalendarConnection, url: str, params: dict | None) -> httpx.Response:
        """One authorised GET. A 401 means the cached token died early: mint a fresh one and try
        once more."""
        for attempt in (1, 2):
            token = self._cache.get(conn)
            response = graph_http.get(
                token, url, params=params, headers=list(PREFER), transport=self._transport
            )
            if response.status_code != 401 or attempt == 2:
                return response
            self._cache.invalidate(conn.id)
        raise AssertionError("unreachable")  # pragma: no cover

    @staticmethod
    def _check(response: httpx.Response) -> None:
        status = response.status_code
        if status == 200:
            return
        if status == 401:
            raise AuthRevoked("Microsoft refused a freshly minted access token")
        if status == 403 and _error_code(response) in GRANT_CODES:
            raise AuthRevoked("the Microsoft grant does not cover the calendar")
        # 429, 503, 504 and any other answer are retried later.
        raise TransientError(f"Microsoft Graph answered HTTP {status}")

    @staticmethod
    def _incomplete(conn: CalendarConnection, events: list[dict], why: str) -> SyncBatch:
        logger.warning("calendar sync for connection %s is incomplete (%s)", conn.id, why)
        return SyncBatch(events, None, complete=False, full=True)

    def _remind_about_the_secret(self, now: datetime) -> None:
        """From 30 days before the client secret expires, tell the admins once a day."""
        settings = self._settings or get_settings()
        today = now.astimezone(UTC).date()
        if self._reminded_on == today or not microsoft_oauth.secret_expiry_due(settings, today):
            return
        self._reminded_on = today
        microsoft_alerts.secret_expiring(settings, now)
