"""The Google Calendar client: `GoogleSource` (the `CalendarSource` for provider `google`) and
`AccessTokenCache`.

What it calls, and nothing else: Google's token endpoint to turn a refresh token into an access
token, and the primary calendar's `events.list` and `events.get`. Both URLs are constants. No URL,
host or path fragment comes from event data or from Google's answers (a `nextPageToken` is only
ever a query value, and an event id is quoted into a single path segment), redirects are not
followed, no proxy is read from the environment, and every call has a timeout on all four stages.

Tokens. The refresh token is decrypted from the connection row for the length of one refresh
request (`app/vault.py`) and goes only into that request's form body. Access tokens live in
`AccessTokenCache`, in this process's memory, never in the database, and are refreshed five
minutes before they expire. A token travels in an `Authorization` header, never in a URL. No log
line, exception message or `repr` holds a token, a response body, a URL or event data: failures
carry an HTTP status or an exception type and nothing more.

Sync. A full list covers now minus one hour to now plus fourteen days and follows `pageToken` to
the last page. An incremental call sends the stored `syncToken` and no time bounds (Google refuses
the two together). The batch is `complete` only when the last page arrived with a
`nextSyncToken`. A page that fails after the first returns what was read as an incomplete batch
with no token, so the caller applies no cancellations and the token does not move; a 410 or a
revoked grant raises instead, because the caller must act on those.
"""

import logging
import threading
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from urllib.parse import quote

import httpx

from app import vault
from app.calendar_source import (
    AuthRevoked,
    SyncBatch,
    SyncTokenExpired,
    TransientError,
)
from app.config import Settings, get_settings
from app.google_oauth import TOKEN_URL
from app.models import CalendarConnection

logger = logging.getLogger(__name__)

EVENTS_URL = "https://www.googleapis.com/calendar/v3/calendars/primary/events"
WINDOW_BEFORE = timedelta(hours=1)
WINDOW_AFTER = timedelta(days=14)
PAGE_SIZE = 250
# A runaway chain (a bug, a hostile answer) stops here and the batch is incomplete: 40 pages of 250
# is two orders of magnitude over what one person's two weeks hold.
MAX_PAGES = 40
# Refresh this long before Google's stated expiry, so a token never dies in flight.
REFRESH_EARLY = timedelta(minutes=5)
DEFAULT_LIFETIME = timedelta(hours=1)
# A transient refresh failure falls back to a cached token only while it has at least this long.
REUSE_MARGIN = timedelta(seconds=30)
TIMEOUT = httpx.Timeout(connect=5.0, read=15.0, write=10.0, pool=5.0)
# 403 reasons that mean the user's grant does not cover this call; every other 403 (rate limits,
# quota, a project switch) is not something reconnecting would fix.
GRANT_REASONS = {"insufficientPermissions"}


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _client(transport: httpx.BaseTransport | None) -> httpx.Client:
    return httpx.Client(
        timeout=TIMEOUT, transport=transport, trust_env=False, follow_redirects=False
    )


def _send(client: httpx.Client, method: str, url: str, **kwargs) -> httpx.Response:
    try:
        return client.request(method, url, **kwargs)
    except httpx.HTTPError as exc:
        # The exception text can hold the URL; only its type is kept.
        raise TransientError(f"Google request failed ({type(exc).__name__})") from None


def _json_object(response: httpx.Response) -> dict:
    try:
        body = response.json()
    except ValueError:
        raise TransientError("Google answered with something that is not JSON") from None
    if not isinstance(body, dict):
        raise TransientError("Google answered with an unexpected JSON shape")
    return body


def _error_reason(response: httpx.Response) -> str | None:
    """Google's machine-readable reason from `{"error": {"errors": [{"reason": ...}]}}`."""
    try:
        errors = response.json()["error"]["errors"]
        reason = errors[0]["reason"]
    except (ValueError, KeyError, IndexError, TypeError):
        return None
    return reason if isinstance(reason, str) else None


@dataclass
class _Cached:
    token: vault.Secret
    expires_at: datetime


class AccessTokenCache:
    """Access tokens minted from refresh tokens, per connection, in memory only.

    `get` returns a usable token, minting one when there is none or less than five minutes remain.
    Concurrent callers for one connection mint once. The tokens are held as `vault.Secret`, so even
    a dump of the cache shows `<redacted>`.
    """

    def __init__(
        self,
        clock: Callable[[], datetime] | None = None,
        *,
        settings: Settings | None = None,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self._clock = clock or _utc_now
        self._settings = settings
        self._transport = transport
        self._tokens: dict[int, _Cached] = {}
        self._locks: dict[int, threading.Lock] = {}
        self._guard = threading.Lock()

    def __repr__(self) -> str:
        return f"<AccessTokenCache {len(self._tokens)} tokens, redacted>"

    __str__ = __repr__

    def __reduce__(self):
        raise TypeError("an AccessTokenCache cannot be pickled")

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
        """Drop one connection's cached token (Google refused it); the next `get` mints again."""
        with self._guard:
            self._tokens.pop(conn_id, None)

    def forget(self, conn_id: int) -> None:
        """Drop everything held for a connection (disconnect)."""
        with self._guard:
            self._tokens.pop(conn_id, None)
            self._locks.pop(conn_id, None)

    def _mint(self, conn: CalendarConnection) -> tuple[str, timedelta]:
        settings = self._settings or get_settings()
        if not (settings.google_client_id and settings.google_client_secret):
            raise TransientError("the Google OAuth client is not configured")
        refresh_token = _refresh_token(conn)
        with _client(self._transport) as client:
            response = _send(
                client,
                "POST",
                TOKEN_URL,
                data={
                    "grant_type": "refresh_token",
                    "refresh_token": refresh_token,
                    "client_id": settings.google_client_id,
                    "client_secret": settings.google_client_secret,
                },
            )
        if response.status_code == 400 and _token_error(response) == "invalid_grant":
            raise AuthRevoked("Google no longer honours the refresh token")
        if response.status_code != 200:
            # Includes `invalid_client`: our own OAuth client is misconfigured, so reconnecting
            # would not help the user.
            raise TransientError(f"the Google token endpoint answered HTTP {response.status_code}")
        body = _json_object(response)
        token = body.get("access_token")
        if not isinstance(token, str) or not token:
            raise TransientError("the Google token endpoint sent no access token")
        expires_in = body.get("expires_in")
        usable = isinstance(expires_in, (int, float)) and not isinstance(expires_in, bool)
        lifetime = timedelta(seconds=expires_in) if usable and expires_in > 0 else DEFAULT_LIFETIME
        return token, lifetime


def _token_error(response: httpx.Response) -> str | None:
    try:
        error = response.json()["error"]
    except (ValueError, KeyError, TypeError):
        return None
    return error if isinstance(error, str) else None


def _refresh_token(conn: CalendarConnection) -> str:
    if conn.refresh_token_enc is None or conn.key_version is None:
        raise AuthRevoked("the connection has no refresh token")
    try:
        secret = vault.decrypt(
            conn.refresh_token_enc, conn.key_version, purpose="refresh_token", owner=conn.user_id
        )
    except vault.VaultUnavailable:
        # The server's key is missing or broken: the user's grant is fine.
        raise TransientError("the token vault is unavailable") from None
    except vault.VaultError:
        raise AuthRevoked("the stored refresh token cannot be read") from None
    return secret.reveal()


class GoogleSource:
    """`CalendarSource` for a Google connection (primary calendar, `singleEvents=true`)."""

    provider = "google"

    def __init__(
        self,
        settings: Settings | None = None,
        *,
        cache: AccessTokenCache | None = None,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self._transport = transport
        self._cache = cache or AccessTokenCache(settings=settings, transport=transport)

    @property
    def cache(self) -> AccessTokenCache:
        return self._cache

    def sync(self, conn: CalendarConnection, *, now: datetime, full: bool) -> SyncBatch:
        if now.tzinfo is None:
            raise ValueError("now must be timezone-aware")
        incremental = bool(conn.sync_token) and not full
        params: dict[str, str | int] = {"singleEvents": "true", "maxResults": PAGE_SIZE}
        if incremental:
            params["syncToken"] = conn.sync_token
        else:
            params["timeMin"] = _rfc3339(now - WINDOW_BEFORE)
            params["timeMax"] = _rfc3339(now + WINDOW_AFTER)

        events: list[dict] = []
        page_token: str | None = None
        for page in range(MAX_PAGES):
            page_params = {**params, "pageToken": page_token} if page_token else params
            try:
                body = self._list_page(conn, page_params, incremental=incremental)
            except TransientError as exc:
                if page == 0:
                    raise
                return self._incomplete(conn, events, incremental, f"page {page + 1}: {exc}")
            events.extend(body.get("items", []))
            page_token = body.get("nextPageToken") or None
            if page_token is None:
                token = body.get("nextSyncToken")
                if isinstance(token, str) and token:
                    return SyncBatch(events, token, complete=True, full=not incremental)
                return self._incomplete(conn, events, incremental, "no sync token on last page")
            if not isinstance(page_token, str):
                return self._incomplete(conn, events, incremental, "unusable page token")
        return self._incomplete(conn, events, incremental, f"over {MAX_PAGES} pages")

    def get_event(self, conn: CalendarConnection, source_event_id: str) -> dict | None:
        # "." and ".." would be folded into the URL path by the HTTP client and reach another
        # resource; everything else is quoted into one path segment.
        if source_event_id in ("", ".", ".."):
            raise ValueError("not an event id")
        url = f"{EVENTS_URL}/{quote(source_event_id, safe='')}"
        response = self._call(conn, url, None)
        if response.status_code in (404, 410):
            return None
        self._check(response, incremental=False)
        return _json_object(response)

    # ---- internals ----

    def _list_page(self, conn: CalendarConnection, params: dict, *, incremental: bool) -> dict:
        response = self._call(conn, EVENTS_URL, params)
        self._check(response, incremental=incremental)
        body = _json_object(response)
        _items(body)  # a malformed list fails here, as part of the page, not after it
        return body

    def _call(self, conn: CalendarConnection, url: str, params: dict | None) -> httpx.Response:
        """One authorised GET. A 401 means the cached token died early: mint a fresh one and
        try once more."""
        for attempt in (1, 2):
            token = self._cache.get(conn)
            with _client(self._transport) as client:
                response = _send(
                    client, "GET", url, params=params, headers={"Authorization": f"Bearer {token}"}
                )
            if response.status_code != 401 or attempt == 2:
                return response
            self._cache.invalidate(conn.id)
        raise AssertionError("unreachable")  # pragma: no cover

    @staticmethod
    def _check(response: httpx.Response, *, incremental: bool) -> None:
        status = response.status_code
        if status == 200:
            return
        if status == 401:
            raise AuthRevoked("Google refused a freshly minted access token")
        if status == 403 and _error_reason(response) in GRANT_REASONS:
            raise AuthRevoked("the Google grant does not cover the calendar")
        if status == 410 and incremental:
            raise SyncTokenExpired("Google no longer honours the sync token")
        # 410 on a full list has no token to blame; rate limits, 5xx, a redirect and anything
        # else are retried later.
        raise TransientError(f"Google Calendar answered HTTP {status}")

    @staticmethod
    def _incomplete(
        conn: CalendarConnection, events: list[dict], incremental: bool, why: str
    ) -> SyncBatch:
        logger.warning("calendar sync for connection %s is incomplete (%s)", conn.id, why)
        return SyncBatch(events, None, complete=False, full=not incremental)


def _rfc3339(moment: datetime) -> str:
    return moment.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _items(body: dict) -> list[dict]:
    items = body.get("items", [])
    if not isinstance(items, list) or not all(isinstance(item, dict) for item in items):
        raise TransientError("Google sent a malformed list of events")
    return items
