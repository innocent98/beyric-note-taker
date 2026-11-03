"""The Google Calendar client (plan task C5): the `CalendarSource` protocol, `AccessTokenCache` and
`GoogleSource`, against `tests/fakes/google.py` through httpx's `MockTransport`.

The bodies are SYNTHETIC (built from Google's documented shapes). The plan's live capture, a real
`events.list` and `events.get` with only `calendar.events.readonly`, has not been done.
"""

import base64
import json
import logging
import pickle
import traceback
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path

import httpx
import pytest

from app import gcal_client, vault
from app.calendar_source import (
    AuthRevoked,
    CalendarSource,
    SyncBatch,
    SyncTokenExpired,
    TransientError,
)
from app.gcal_client import AccessTokenCache, GoogleSource
from app.gcal_events import parse_event
from app.models import CalendarConnection
from tests.fakes.google import (
    CALENDAR_HOST,
    EVENTS_PATH,
    TOKEN_HOST,
    FakeGoogleCalendar,
    gone,
    not_json,
)
from tests.test_web import make_settings

KEY = base64.b64encode(b"C" * 32).decode()
NOW = datetime(2026, 11, 3, 12, 0, tzinfo=UTC)
FIXTURES = Path(__file__).parent / "fixtures" / "gcal"
REFRESH = FakeGoogleCalendar.REFRESH_TOKEN
SETTINGS = make_settings(
    google_client_id=FakeGoogleCalendar.CLIENT_ID,
    google_client_secret=FakeGoogleCalendar.CLIENT_SECRET,
    token_enc_key=KEY,
)


class Clock:
    def __init__(self, start: datetime = NOW) -> None:
        self.now = start

    def __call__(self) -> datetime:
        return self.now

    def advance(self, **delta) -> None:
        self.now += timedelta(**delta)


@pytest.fixture(autouse=True)
def vault_key(monkeypatch):
    monkeypatch.setattr(vault, "get_settings", lambda: SETTINGS)


def event(name: str = "meet_event") -> dict:
    return json.loads((FIXTURES / f"{name}.json").read_text())


def stub(event_id: str) -> dict:
    return {"kind": "calendar#event", "id": event_id, "status": "cancelled"}


def connection(*, sync_token: str | None = None, user_id: int = 7, conn_id: int = 1):
    blob, version = vault.encrypt(REFRESH, purpose="refresh_token", owner=user_id)
    return CalendarConnection(
        id=conn_id,
        user_id=user_id,
        provider="google",
        refresh_token_enc=blob,
        key_version=version,
        sync_token=sync_token,
    )


@pytest.fixture
def google():
    return FakeGoogleCalendar()


@pytest.fixture
def clock():
    return Clock()


@pytest.fixture
def source(google, clock):
    cache = AccessTokenCache(clock, settings=SETTINGS, transport=google.transport)
    return GoogleSource(SETTINGS, cache=cache, transport=google.transport)


# ---- the protocol ----


def test_google_source_satisfies_the_calendar_source_protocol(source):
    assert isinstance(source, CalendarSource)
    assert source.provider == "google"


def test_the_three_errors_are_exceptions_and_distinct():
    assert not issubclass(AuthRevoked, TransientError)
    assert not issubclass(SyncTokenExpired, TransientError)
    assert issubclass(TransientError, Exception)


def test_raw_events_pass_through_unchanged_and_parse(google, source):
    google.pages = [[event("meet_event"), event("zoom_event"), event("cancelled_stub")]]
    batch = source.sync(connection(), now=NOW, full=True)
    assert batch.raw_events == google.pages[0]
    assert [parse_event(raw).status for raw in batch.raw_events][-1] == "cancelled"


# ---- a full list ----


def test_full_list_follows_page_tokens_until_last_page(google, source):
    google.pages = [[event()], [event("zoom_event")], [event("teams_event")]]
    batch = source.sync(connection(), now=NOW, full=True)
    assert isinstance(batch, SyncBatch)
    assert [e["id"] for e in batch.raw_events] == [
        "synthmeet0001",
        google.pages[1][0]["id"],
        google.pages[2][0]["id"],
    ]
    assert batch.complete is True and batch.full is True
    assert batch.next_sync_token == "sync-token-1"
    queries = [google.query(r) for r in google.list_requests()]
    assert len(queries) == 3
    assert "pageToken" not in queries[0]
    assert [q["pageToken"] for q in queries[1:]] == ["page-token-1", "page-token-2"]
    # the window and singleEvents travel on every page: the page token is only valid for the
    # same query
    for q in queries:
        assert q["singleEvents"] == "true"
        assert q["timeMin"] == "2026-11-03T11:00:00Z"
        assert q["timeMax"] == "2026-11-17T12:00:00Z"
        assert "syncToken" not in q


def test_window_is_one_hour_back_to_fourteen_days_ahead_in_utc_whatever_the_zone_of_now(
    google, source
):
    lagos = timezone(timedelta(hours=1))
    source.sync(connection(), now=datetime(2026, 11, 3, 13, 0, tzinfo=lagos), full=True)
    q = google.query(google.list_requests()[0])
    assert (q["timeMin"], q["timeMax"]) == ("2026-11-03T11:00:00Z", "2026-11-17T12:00:00Z")
    assert gcal_client.WINDOW_BEFORE == timedelta(hours=1)
    assert gcal_client.WINDOW_AFTER == timedelta(days=14)


def test_a_naive_now_is_refused(source):
    with pytest.raises(ValueError):
        source.sync(connection(), now=datetime(2026, 11, 3, 12, 0), full=True)


def test_full_list_reads_the_primary_calendar_only(google, source):
    source.sync(connection(), now=NOW, full=True)
    assert google.list_requests()[0].url.path == EVENTS_PATH
    assert "/calendars/primary/" in EVENTS_PATH


def test_full_true_ignores_a_stored_sync_token(google, source):
    source.sync(connection(sync_token="stored-token"), now=NOW, full=True)
    q = google.query(google.list_requests()[0])
    assert "syncToken" not in q and "timeMin" in q


def test_an_empty_calendar_is_a_complete_empty_batch(google, source):
    google.pages = [[]]
    batch = source.sync(connection(), now=NOW, full=True)
    assert (batch.raw_events, batch.complete, batch.next_sync_token) == ([], True, "sync-token-1")


# ---- an incremental call ----


def test_incremental_call_sends_sync_token_and_no_time_bounds(google, source):
    google.pages = [[stub("synthmeet0001")]]
    batch = source.sync(connection(sync_token="stored-token"), now=NOW, full=False)
    (request,) = google.list_requests()
    q = google.query(request)
    assert q["syncToken"] == "stored-token"
    assert "timeMin" not in q and "timeMax" not in q
    # Google wants the other parameters to match the initial list
    assert q["singleEvents"] == "true"
    assert batch.full is False and batch.complete is True
    assert batch.raw_events == [stub("synthmeet0001")]
    assert batch.next_sync_token == "sync-token-1"


def test_incremental_pages_keep_the_sync_token_and_never_add_time_bounds(google, source):
    google.pages = [[stub("a")], [stub("b")]]
    batch = source.sync(connection(sync_token="stored-token"), now=NOW, full=False)
    assert [e["id"] for e in batch.raw_events] == ["a", "b"]
    for q in map(google.query, google.list_requests()):
        assert q["syncToken"] == "stored-token"
        assert "timeMin" not in q and "timeMax" not in q


def test_incremental_without_a_stored_token_does_a_full_list(google, source):
    batch = source.sync(connection(sync_token=None), now=NOW, full=False)
    assert batch.full is True
    assert "timeMin" in google.query(google.list_requests()[0])


# ---- 410, revoked grants, transient failures ----


def test_410_raises_sync_token_expired(google, source):
    google.override("list", 0, gone())
    with pytest.raises(SyncTokenExpired):
        source.sync(connection(sync_token="stored-token"), now=NOW, full=False)


def test_410_on_a_later_incremental_page_also_raises_sync_token_expired(google, source):
    google.pages = [[stub("a")], [stub("b")]]
    google.override("list", 1, gone())
    with pytest.raises(SyncTokenExpired):
        source.sync(connection(sync_token="stored-token"), now=NOW, full=False)


def test_410_on_a_full_list_is_transient_not_sync_token_expired(google, source):
    # a full list carries no sync token, so there is nothing to expire and falling back to a full
    # list would loop
    google.override("list", 0, gone())
    with pytest.raises(TransientError):
        source.sync(connection(), now=NOW, full=True)


def test_refresh_invalid_grant_raises_auth_revoked(google, source):
    google.valid_refresh_tokens.clear()
    with pytest.raises(AuthRevoked):
        source.sync(connection(), now=NOW, full=True)
    assert google.list_calls == 0


@pytest.mark.parametrize(
    "response",
    [
        httpx.Response(500, json={"error": "backend_error"}),
        httpx.Response(503, content=b"unavailable"),
        httpx.Response(429, json={"error": "rate_limit"}),
        httpx.Response(400, json={"error": "invalid_client"}),  # our client misconfigured
        httpx.Response(401, json={"error": "invalid_client"}),
        not_json(200),
        httpx.Response(200, json=["not", "an", "object"]),
        httpx.Response(200, json={"expires_in": 3599}),  # no access_token
        httpx.ReadTimeout("slow"),
        httpx.ConnectError("down"),
    ],
    ids=[
        "500",
        "503",
        "429",
        "400-invalid_client",
        "401",
        "html",
        "list",
        "no-token",
        "timeout",
        "connect",
    ],
)
def test_refresh_failures_other_than_invalid_grant_are_transient(google, source, response):
    google.override_refresh(response)
    with pytest.raises(TransientError):
        source.sync(connection(), now=NOW, full=True)


@pytest.mark.parametrize(
    "response",
    [
        httpx.Response(500, json={"error": {"code": 500}}),
        httpx.Response(502, content=b"bad gateway"),
        httpx.Response(503, content=b""),
        httpx.Response(429, json={"error": {"code": 429}}),
        FakeGoogleCalendar.error(403, "rateLimitExceeded"),
        FakeGoogleCalendar.error(403, "userRateLimitExceeded"),
        FakeGoogleCalendar.error(403, "quotaExceeded"),
        FakeGoogleCalendar.error(403, "accessNotConfigured"),  # our project, not the user's grant
        FakeGoogleCalendar.error(400, "badRequest"),
        httpx.Response(302, headers={"location": "https://evil.test/"}),
        not_json(200),
        httpx.Response(200, json=["not", "an", "object"]),
        httpx.ReadTimeout("slow"),
        httpx.ConnectTimeout("slow"),
        httpx.ConnectError("down"),
        httpx.RemoteProtocolError("closed"),
    ],
    ids=[
        "500",
        "502",
        "503",
        "429",
        "rate",
        "user-rate",
        "quota",
        "not-configured",
        "400",
        "redirect",
        "html",
        "list-body",
        "read-timeout",
        "connect-timeout",
        "connect",
        "protocol",
    ],
)
def test_5xx_timeouts_and_odd_answers_raise_transient_on_the_first_page(google, source, response):
    google.override("list", 0, response)
    with pytest.raises(TransientError):
        source.sync(connection(), now=NOW, full=True)


def test_403_insufficient_permissions_is_auth_revoked(google, source):
    # the user's grant no longer covers the calendar: only a reconnect helps
    google.override("list", 0, FakeGoogleCalendar.error(403, "insufficientPermissions"))
    with pytest.raises(AuthRevoked):
        source.sync(connection(), now=NOW, full=True)


def test_401_mints_a_fresh_token_once_and_carries_on(google, source):
    google.pages = [[event()]]
    google.override("list", 0, FakeGoogleCalendar.error(401, "authError", "Invalid Credentials"))
    batch = source.sync(connection(), now=NOW, full=True)
    assert batch.complete is True and len(batch.raw_events) == 1
    assert google.token_calls == 2  # the first token was thrown away


def test_a_401_that_survives_a_fresh_token_is_auth_revoked(google, source):
    google.override("list", 0, FakeGoogleCalendar.error(401, "authError"))
    google.override("list", 1, FakeGoogleCalendar.error(401, "authError"))
    with pytest.raises(AuthRevoked):
        source.sync(connection(), now=NOW, full=True)


def test_a_redirect_is_never_followed(google, source):
    google.override("list", 0, httpx.Response(302, headers={"location": "https://evil.test/x"}))
    with pytest.raises(TransientError):
        source.sync(connection(), now=NOW, full=True)
    assert {r.url.host for r in google.requests} <= {TOKEN_HOST, CALENDAR_HOST}


# ---- a chain that breaks halfway (Review Focus 1) ----


@pytest.mark.parametrize(
    "response",
    [
        httpx.Response(503, content=b"unavailable"),
        httpx.Response(500, json={"error": {"code": 500}}),
        FakeGoogleCalendar.error(403, "rateLimitExceeded"),
        httpx.ReadTimeout("slow"),
        httpx.ConnectError("down"),
    ],
    ids=["503", "500", "rate-limit", "timeout", "connect"],
)
def test_incomplete_page_chain_returns_complete_false(google, source, response):
    google.pages = [[event()], [event("zoom_event")], [event("teams_event")]]
    google.override("list", 1, response)
    batch = source.sync(connection(), now=NOW, full=True)
    assert batch.complete is False
    assert batch.next_sync_token is None
    assert batch.full is True
    # what was read is returned (it is valid); the caller must not treat it as the whole window
    assert [e["id"] for e in batch.raw_events] == ["synthmeet0001"]


def test_incomplete_incremental_chain_does_not_advance_the_token(google, source):
    google.pages = [[stub("a")], [stub("b")]]
    google.override("list", 1, httpx.Response(503, content=b""))
    batch = source.sync(connection(sync_token="stored-token"), now=NOW, full=False)
    assert (batch.complete, batch.next_sync_token, batch.full) == (False, None, False)


def test_a_401_on_a_later_page_mints_again_and_the_chain_completes(google, source):
    google.pages = [[event()], [event("zoom_event")]]
    google.override("list", 1, FakeGoogleCalendar.error(401, "authError"))
    batch = source.sync(connection(), now=NOW, full=True)
    assert batch.complete is True and len(batch.raw_events) == 2
    assert batch.next_sync_token == "sync-token-1"


def test_a_401_that_persists_on_a_later_page_raises_rather_than_returning_a_partial_batch(
    google, source
):
    google.pages = [[event()], [event("zoom_event")]]
    google.override("list", 1, FakeGoogleCalendar.error(401, "authError"))
    google.override("list", 2, FakeGoogleCalendar.error(401, "authError"))
    with pytest.raises(AuthRevoked):
        source.sync(connection(), now=NOW, full=True)


def test_an_endless_page_chain_stops_at_the_page_cap_and_is_incomplete(google, source, monkeypatch):
    monkeypatch.setattr(gcal_client, "MAX_PAGES", 2)
    google.pages = [[event()], [event()], [event()], [event()]]
    batch = source.sync(connection(), now=NOW, full=True)
    assert google.list_calls == 2
    assert (batch.complete, batch.next_sync_token) == (False, None)


def test_a_last_page_without_a_sync_token_is_incomplete(google, source):
    google.sync_token = None
    batch = source.sync(connection(), now=NOW, full=True)
    assert (batch.complete, batch.next_sync_token) == (False, None)


def test_a_page_token_from_google_cannot_redirect_the_call_to_another_host(google, source):
    # a hostile or odd nextPageToken is only ever a query value
    google.pages = [[event()], [event("zoom_event")]]
    google.override(
        "list",
        0,
        httpx.Response(
            200,
            json={"items": [event()], "nextPageToken": "https://evil.test/?x=1&pageToken=y"},
        ),
    )
    batch = source.sync(connection(), now=NOW, full=True)
    assert {r.url.host for r in google.requests} <= {TOKEN_HOST, CALENDAR_HOST}
    assert (
        google.query(google.list_requests()[1])["pageToken"] == "https://evil.test/?x=1&pageToken=y"
    )
    assert batch.complete is False  # the fake refuses that token (400): transient, incomplete


# ---- events.get ----


def test_get_event_returns_the_raw_event(google, source):
    google.events["synthmeet0001"] = event()
    assert source.get_event(connection(), "synthmeet0001") == event()
    (request,) = google.calls(CALENDAR_HOST, EVENTS_PATH + "/")
    assert request.method == "GET"


@pytest.mark.parametrize("status", [404, 410])
def test_get_event_404_or_410_returns_none(google, source, status):
    google.override("get", 0, FakeGoogleCalendar.error(status, "notFound"))
    assert source.get_event(connection(), "gone") is None


def test_get_event_unknown_id_is_none(google, source):
    assert source.get_event(connection(), "never-existed") is None


def test_get_event_returns_a_cancelled_event_so_the_caller_can_cancel_the_join(google, source):
    google.events["x"] = {**stub("x"), "summary": "Weekly planning"}
    assert source.get_event(connection(), "x")["status"] == "cancelled"


def test_get_event_5xx_and_timeout_are_transient(google, source):
    google.override("get", 0, httpx.Response(503, content=b""))
    google.override("get", 1, httpx.ReadTimeout("slow"))
    with pytest.raises(TransientError):
        source.get_event(connection(), "a")
    with pytest.raises(TransientError):
        source.get_event(connection(), "a")


def test_get_event_refresh_revoked_is_auth_revoked(google, source):
    google.valid_refresh_tokens.clear()
    with pytest.raises(AuthRevoked):
        source.get_event(connection(), "a")


def test_get_event_401_mints_once_more(google, source):
    google.events["a"] = event()
    google.override("get", 0, FakeGoogleCalendar.error(401, "authError"))
    assert source.get_event(connection(), "a") == event()


@pytest.mark.parametrize("event_id", ["", ".", ".."])
def test_an_empty_or_dot_event_id_is_refused(source, event_id):
    with pytest.raises(ValueError):
        source.get_event(connection(), event_id)


@pytest.mark.parametrize(
    "event_id",
    ["../calendars/other%40example.test/events/x", "https://evil.test/x", "a/b", "a?b=c#d", "a b"],
)
def test_the_event_id_is_one_path_segment_and_never_changes_host(google, source, event_id):
    source.get_event(connection(), event_id)
    (request,) = google.calls(CALENDAR_HOST, EVENTS_PATH)
    path = request.url.raw_path.decode()
    assert path.startswith(EVENTS_PATH + "/")
    tail = path[len(EVENTS_PATH) + 1 :]
    assert "/" not in tail and "?" not in tail and "#" not in tail
    assert request.url.host == CALENDAR_HOST


# ---- only Google hosts, a timeout on every call ----


def test_only_google_api_hosts_over_https_are_ever_called(google, source):
    google.pages = [[event()], [event("zoom_event")]]
    google.events["a"] = event()
    source.sync(connection(), now=NOW, full=True)
    source.sync(connection(sync_token="t"), now=NOW, full=False)
    source.get_event(connection(), "a")
    assert google.requests
    assert {(r.url.scheme, r.url.host) for r in google.requests} <= {
        ("https", CALENDAR_HOST),
        ("https", TOKEN_HOST),
    }


def test_every_call_has_a_bounded_timeout_on_all_four_stages(google, source):
    google.events["a"] = event()
    source.sync(connection(), now=NOW, full=True)
    source.get_event(connection(), "a")
    assert len(google.requests) == 3  # refresh, list, get
    for request in google.requests:
        timeout = request.extensions["timeout"]
        assert set(timeout) == {"connect", "read", "write", "pool"}
        assert all(value is not None and 0 < value <= 30 for value in timeout.values())


def test_no_environment_proxy_and_no_redirects(monkeypatch, google):
    seen = {}
    real = httpx.Client

    def spy(*args, **kwargs):
        seen.update(kwargs)
        return real(*args, **kwargs)

    monkeypatch.setattr(gcal_client.httpx, "Client", spy)
    cache = AccessTokenCache(Clock(), settings=SETTINGS, transport=google.transport)
    GoogleSource(SETTINGS, cache=cache, transport=google.transport).sync(
        connection(), now=NOW, full=True
    )
    assert seen["trust_env"] is False
    assert seen.get("follow_redirects", False) is False


# ---- the refresh request ----


def test_refresh_request_is_a_refresh_token_grant_with_the_decrypted_token(google, source):
    source.sync(connection(), now=NOW, full=True)
    (request,) = google.calls(TOKEN_HOST, "/token")
    assert request.method == "POST"
    assert str(request.url) == "https://oauth2.googleapis.com/token"
    form = dict(httpx.QueryParams(request.content.decode()))
    assert form == {
        "grant_type": "refresh_token",
        "refresh_token": REFRESH,
        "client_id": FakeGoogleCalendar.CLIENT_ID,
        "client_secret": FakeGoogleCalendar.CLIENT_SECRET,
    }
    assert REFRESH not in str(request.url)


def test_calendar_calls_carry_the_access_token_in_a_header_never_in_the_url(google, source):
    source.sync(connection(), now=NOW, full=True)
    (request,) = google.list_requests()
    assert request.headers["authorization"] == "Bearer access-token-placeholder-1"
    assert "access-token" not in str(request.url)


def test_a_refresh_token_encrypted_for_another_user_is_auth_revoked(google, source):
    conn = connection(user_id=7)
    conn.user_id = 8  # the blob was made for user 7: it does not open for 8
    with pytest.raises(AuthRevoked):
        source.sync(conn, now=NOW, full=True)
    assert google.token_calls == 0


def test_a_connection_with_no_refresh_token_is_auth_revoked(source, google):
    conn = connection()
    conn.refresh_token_enc = None
    with pytest.raises(AuthRevoked):
        source.sync(conn, now=NOW, full=True)
    assert google.token_calls == 0


def test_a_missing_vault_key_is_transient_not_a_reason_to_make_the_user_reconnect(
    google, source, monkeypatch
):
    conn = connection()
    monkeypatch.setattr(vault, "get_settings", lambda: make_settings())  # no TOKEN_ENC_KEY
    with pytest.raises(TransientError):
        source.sync(conn, now=NOW, full=True)


def test_an_unconfigured_google_client_is_transient(google, clock):
    bare = make_settings(token_enc_key=KEY)  # no client id or secret
    cache = AccessTokenCache(clock, settings=bare, transport=google.transport)
    with pytest.raises(TransientError):
        cache.get(connection())
    assert google.requests == []


def test_a_transient_refresh_failure_reuses_a_token_that_has_not_expired_yet(google, clock):
    cache = AccessTokenCache(clock, settings=SETTINGS, transport=google.transport)
    conn = connection()
    google.expires_in = 3600
    first = cache.get(conn)
    clock.advance(seconds=3400)  # inside the five-minute early window, 200 s before expiry
    google.override_refresh(httpx.Response(503, content=b""))
    assert cache.get(conn) == first
    clock.advance(seconds=250)  # past real expiry: the failure now surfaces
    with pytest.raises(TransientError):
        cache.get(conn)


def test_a_revoked_grant_drops_the_cached_token(google, source, clock):
    conn = connection()
    source.sync(conn, now=NOW, full=True)
    google.expire_access_tokens()
    google.valid_refresh_tokens.clear()
    clock.advance(hours=2)
    with pytest.raises(AuthRevoked):
        source.sync(conn, now=NOW, full=True)
    google.valid_refresh_tokens.add(REFRESH)  # the user reconnects
    batch = source.sync(conn, now=NOW, full=True)
    assert batch.complete is True


# ---- the access token cache ----


def test_access_token_cached_until_five_minutes_before_expiry(google, clock):
    cache = AccessTokenCache(clock, settings=SETTINGS, transport=google.transport)
    conn = connection()
    google.expires_in = 3600
    first = cache.get(conn)
    assert first == "access-token-placeholder-1"
    clock.advance(seconds=3599 - 300)  # 3299 s in, 301 s left
    assert cache.get(conn) == first and google.token_calls == 1
    clock.advance(seconds=1)  # 3300 s in: five minutes left, refresh now
    second = cache.get(conn)
    assert second == "access-token-placeholder-2" and google.token_calls == 2


def test_each_connection_has_its_own_cached_token(google, clock):
    cache = AccessTokenCache(clock, settings=SETTINGS, transport=google.transport)
    a, b = connection(conn_id=1), connection(conn_id=2, user_id=9)
    assert cache.get(a) != cache.get(b)
    assert cache.get(a) == "access-token-placeholder-1"
    assert google.token_calls == 2


def test_a_token_that_lives_under_five_minutes_is_used_once_and_not_looped_on(google, clock):
    google.expires_in = 120
    cache = AccessTokenCache(clock, settings=SETTINGS, transport=google.transport)
    conn = connection()
    assert cache.get(conn) == "access-token-placeholder-1"
    assert cache.get(conn) == "access-token-placeholder-2"  # already inside the early window


def test_a_missing_expires_in_defaults_to_an_hour(google, clock):
    google.override_refresh(
        httpx.Response(200, json={"access_token": "access-token-placeholder-x"})
    )
    cache = AccessTokenCache(clock, settings=SETTINGS, transport=google.transport)
    conn = connection()
    assert cache.get(conn) == "access-token-placeholder-x"
    clock.advance(minutes=54)
    cache.get(conn)
    assert google.token_calls == 1


def test_invalidate_and_forget_drop_a_cached_token(google, clock):
    cache = AccessTokenCache(clock, settings=SETTINGS, transport=google.transport)
    conn = connection()
    cache.get(conn)
    cache.invalidate(conn.id)
    cache.get(conn)
    assert google.token_calls == 2
    cache.forget(conn.id)
    cache.forget(999)  # unknown ids are fine
    cache.get(conn)
    assert google.token_calls == 3


def test_cache_repr_has_no_token(google, clock):
    cache = AccessTokenCache(clock, settings=SETTINGS, transport=google.transport)
    token = cache.get(connection())
    for text in (repr(cache), str(cache), repr(cache._tokens)):  # a debug dump too
        assert token not in text
        assert REFRESH not in text
    assert "redacted" in repr(cache)
    with pytest.raises(TypeError):
        pickle.dumps(cache)


def test_the_cache_works_from_several_threads_with_one_mint_per_connection(google, clock):
    from concurrent.futures import ThreadPoolExecutor

    cache = AccessTokenCache(clock, settings=SETTINGS, transport=google.transport)
    conn = connection()
    with ThreadPoolExecutor(8) as pool:
        tokens = list(pool.map(lambda _: cache.get(conn), range(16)))
    assert set(tokens) == {"access-token-placeholder-1"}
    assert google.token_calls == 1


# ---- secrets never leak (logs, exception text, repr) ----


def _chain_text(exc: BaseException) -> str:
    return "".join(traceback.format_exception(exc)) + repr(exc) + str(exc)


FAILURES = [
    ("list", httpx.Response(503, content=b"unavailable")),
    ("list", httpx.ReadTimeout("slow")),
    ("list", FakeGoogleCalendar.error(403, "insufficientPermissions")),
    ("list", FakeGoogleCalendar.error(401, "authError")),
    ("refresh", httpx.Response(400, json={"error": "invalid_grant"})),
    ("refresh", httpx.ConnectError("down")),
    ("refresh", httpx.Response(500, content=b"boom")),
]


@pytest.mark.parametrize("kind,response", FAILURES)
def test_no_token_reaches_logs_or_exception_text(google, source, caplog, kind, response):
    caplog.set_level(logging.DEBUG)
    if kind == "refresh":
        google.override_refresh(response)
    else:
        google.override("list", 0, response)
        google.override("list", 1, response)
    seen = ""
    try:
        source.sync(connection(), now=NOW, full=True)
    except Exception as exc:  # noqa: BLE001 - the point is what the text holds
        seen = _chain_text(exc)
    secrets = (REFRESH, FakeGoogleCalendar.CLIENT_SECRET, "access-token-placeholder")
    for secret in secrets:
        assert secret not in caplog.text
        assert secret not in seen


def test_no_token_reaches_logs_on_success(google, source, caplog):
    caplog.set_level(logging.DEBUG)
    google.events["a"] = event()
    source.sync(connection(), now=NOW, full=True)
    source.get_event(connection(), "a")
    for secret in (REFRESH, FakeGoogleCalendar.CLIENT_SECRET, "access-token-placeholder"):
        assert secret not in caplog.text


def test_errors_carry_no_response_body_url_or_event_data(google, source):
    leaky = httpx.Response(
        500, json={"error": {"message": "Weekly planning at https://meet.google.com/abc-defg-hij"}}
    )
    google.override("list", 0, leaky)
    with pytest.raises(TransientError) as caught:
        source.sync(connection(), now=NOW, full=True)
    text = _chain_text(caught.value)
    assert "abc-defg-hij" not in text and "Weekly planning" not in text


def test_a_failed_google_call_logs_the_status_and_not_the_body(google, source, caplog):
    caplog.set_level(logging.DEBUG)
    google.override("list", 0, httpx.Response(503, json={"error": {"message": "secret detail"}}))
    with pytest.raises(TransientError):
        source.sync(connection(), now=NOW, full=True)
    assert "secret detail" not in caplog.text
