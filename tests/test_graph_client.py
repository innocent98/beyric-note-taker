"""The Microsoft Graph calendar client (Outlook phase O, M4): `MicrosoftSource` (a full window
every sync, the `nextLink` rule, error mapping, `get_event`) and `MicrosoftTokenCache` (minting
from a refresh token that ROTATES on every use, saved with a compare-and-swap), plus the admin
alerts about our client secret.

Graph's and the token endpoint's side is a `httpx.MockTransport`. **The bodies are DOCUMENTED
shapes, not captures** (the M0 run has not happened): `tests/fixtures/microsoft/` and
`tests/fixtures/graph/`, files named `documented_*__replace_with_m0_capture.json`. The token store
is real: the tests run against Postgres.
"""

import base64
import json
import logging
import pickle
import threading
from datetime import UTC, datetime, timedelta
from pathlib import Path
from urllib.parse import parse_qs

import httpx
import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app import db, gcal_sync, graph_http, microsoft_alerts, vault
from app.calendar_source import (
    AuthRevoked,
    CalendarSource,
    RefreshTokenUnreadable,
    SyncBatch,
    TransientError,
)
from app.graph_client import (
    GRANT_CODES,
    MAX_PAGES,
    PREFER,
    SELECT,
    ConnectionTokenStore,
    MicrosoftSource,
    MicrosoftTokenCache,
)
from app.models import (
    CalendarConnection,
    CalendarEvent,
    Notification,
    ScheduledJoin,
    UserSettings,
)
from tests.test_web import make_settings, make_user

KEY = base64.b64encode(b"M" * 32).decode()
KEY_2 = base64.b64encode(b"N" * 32).decode()
NOW = datetime(2026, 11, 9, 9, 0, tzinfo=UTC)
TENANT = "9188040d-6c67-4c5b-b112-36a304b66dad"
CLIENT_SECRET = "ms-client-secret-placeholder"
FIXTURES = Path(__file__).parent / "fixtures"
SETTINGS = make_settings(
    microsoft_client_id="ms-client-id-placeholder",
    microsoft_client_secret=CLIENT_SECRET,
    microsoft_calendar_enabled=True,
    calendar_enabled=True,
    token_enc_key=KEY,
)
SENTINELS = (
    CLIENT_SECRET,
    "ACCESS-TOKEN",
    "REFRESH-TOKEN",
    "ERROR-DESCRIPTION-SENTINEL",
    "OLD-REFRESH",
)


def doc(folder: str, name: str) -> dict:
    return json.loads(
        (FIXTURES / folder / f"documented_{name}__replace_with_m0_capture.json").read_text()
    )


@pytest.fixture(autouse=True)
def vault_key(monkeypatch):
    monkeypatch.setattr(vault, "get_settings", lambda: SETTINGS)


class Clock:
    def __init__(self, start: datetime = NOW) -> None:
        self.now = start

    def __call__(self) -> datetime:
        return self.now

    def advance(self, **delta) -> None:
        self.now += timedelta(**delta)


class FakeGraphWorld:
    """The token endpoint and Graph, as one transport. `pages` is the list of answers Graph gives
    to successive calendar requests (the last one repeats); `refresh_answers` likewise for the
    token endpoint."""

    def __init__(self) -> None:
        self.requests: list[httpx.Request] = []
        self.pages: list[httpx.Response | Exception] = [httpx.Response(200, json={"value": []})]
        self.refresh_answers: list[httpx.Response | Exception] = [self.refresh_ok(1)]
        self.on_refresh = None  # called with no arguments as a refresh request arrives
        self.minted = 0

    @staticmethod
    def refresh_ok(n: int, *, with_refresh=True) -> httpx.Response:
        body = doc("microsoft", "refresh_response")
        body["access_token"] = f"ACCESS-TOKEN-{n}"
        body["refresh_token"] = f"REFRESH-TOKEN-ROTATED-{n}"
        if not with_refresh:
            del body["refresh_token"]
        return httpx.Response(200, json=body)

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if request.url.host == "login.microsoftonline.com":
            if self.on_refresh:
                self.on_refresh()
            self.minted += 1
            answer = (
                self.refresh_answers.pop(0)
                if len(self.refresh_answers) > 1
                else self.refresh_answers[0]
            )
        else:
            answer = self.pages.pop(0) if len(self.pages) > 1 else self.pages[0]
        if isinstance(answer, Exception):
            raise answer
        return answer

    @property
    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self)

    def graph(self) -> list[httpx.Request]:
        return [r for r in self.requests if r.url.host != "login.microsoftonline.com"]

    def refreshes(self) -> list[dict[str, str]]:
        return [
            {k: v[0] for k, v in parse_qs(r.content.decode()).items()}
            for r in self.requests
            if r.url.host == "login.microsoftonline.com"
        ]


@pytest.fixture
def world():
    return FakeGraphWorld()


@pytest.fixture
def conn(session, engine):
    user = make_user(session, "ada@example.test")
    session.add(UserSettings(user_id=user.id))
    blob, version = vault.encrypt("OLD-REFRESH-TOKEN", purpose="ms_refresh_token", owner=user.id)
    row = CalendarConnection(
        user_id=user.id,
        provider="microsoft",
        status="connected",
        granted_scopes="Calendars.Read User.Read",
        account_email="ada@outlook.example.test",
        account_subject="oid-1",
        tenant_id=TENANT,
        account_kind="personal",
        refresh_token_enc=blob,
        key_version=version,
    )
    session.add(row)
    session.commit()
    return row


@pytest.fixture
def store(engine):
    return ConnectionTokenStore(lambda: Session(engine))


def make_cache(world, store, clock=None, settings=SETTINGS):
    return MicrosoftTokenCache(
        clock or Clock(), settings=settings, transport=world.transport, store=store
    )


def make_source(world, store, clock=None):
    return MicrosoftSource(
        SETTINGS, cache=make_cache(world, store, clock), transport=world.transport
    )


def stored_token(session, conn) -> str:
    session.expire_all()
    row = session.get(CalendarConnection, conn.id)
    return vault.decrypt(
        row.refresh_token_enc, row.key_version, purpose="ms_refresh_token", owner=row.user_id
    ).reveal()


def event(event_id="evt-1", **fields) -> dict:
    return {
        "id": event_id,
        "subject": "Standup",
        "start": {"dateTime": "2026-11-09T10:00:00.0000000", "timeZone": "UTC"},
        "end": {"dateTime": "2026-11-09T10:30:00.0000000", "timeZone": "UTC"},
        **fields,
    }


def page(*events: dict, next_link: str | None = None) -> httpx.Response:
    body = {"value": list(events)}
    if next_link is not None:
        body["@odata.nextLink"] = next_link
    return httpx.Response(200, json=body)


NEXT = "https://graph.microsoft.com/v1.0/me/calendarView?$skiptoken=SKIP-1&startDateTime=x"


# ---- the source satisfies the contract ----


def test_the_source_is_a_calendar_source(world, store):
    source = make_source(world, store)
    assert isinstance(source, CalendarSource) and source.provider == "microsoft"
    assert isinstance(source.cache, MicrosoftTokenCache)


# ---- sync: the window, the headers, the paging ----


def test_a_sync_reads_the_whole_window_with_both_prefer_lines(world, store, conn):
    world.pages = [page(event("a"), event("b"))]
    batch = make_source(world, store).sync(conn, now=NOW, full=False)  # `full` changes nothing
    assert [e["id"] for e in batch.raw_events] == ["a", "b"]
    assert batch.complete is True and batch.full is True and batch.next_sync_token is None
    [request] = world.graph()
    assert request.method == "GET"
    assert f"{request.url.scheme}://{request.url.host}{request.url.path}" == (
        "https://graph.microsoft.com/v1.0/me/calendarView"
    )
    query = {k: v[0] for k, v in parse_qs(request.url.query.decode()).items()}
    assert query == {
        "startDateTime": "2026-11-09T08:00:00Z",  # one hour back
        "endDateTime": "2026-11-23T09:00:00Z",  # fourteen days on
        "$select": SELECT,
        "$top": "250",
    }
    assert request.headers.get_list("prefer") == ['outlook.timezone="UTC"', 'IdType="ImmutableId"']
    assert request.headers["authorization"] == "Bearer ACCESS-TOKEN-1"
    assert "ACCESS-TOKEN" not in str(request.url)


def test_the_select_list_names_every_field_the_normaliser_reads():
    fields = set(SELECT.split(","))
    assert {
        "id",
        "subject",
        "start",
        "end",
        "isAllDay",
        "isCancelled",
        "isOrganizer",
        "organizer",
        "responseStatus",
        "attendees",
        "onlineMeeting",
        "isOnlineMeeting",
        "location",
        "locations",
        "body",
        "seriesMasterId",
    } <= fields
    assert not fields & {"createdDateTime", "lastModifiedDateTime"}  # do not support $select


def test_the_prefer_pair_is_two_header_lines():
    assert PREFER == (("Prefer", 'outlook.timezone="UTC"'), ("Prefer", 'IdType="ImmutableId"'))


def test_a_naive_clock_is_refused(world, store, conn):
    with pytest.raises(ValueError):
        make_source(world, store).sync(conn, now=datetime(2026, 11, 9, 9, 0), full=True)


def test_next_links_are_followed_to_the_last_page(world, store, conn):
    world.pages = [
        page(event("a"), next_link=NEXT),
        page(event("b"), next_link=NEXT.replace("SKIP-1", "SKIP-2")),
        page(event("c")),
    ]
    batch = make_source(world, store).sync(conn, now=NOW, full=True)
    assert [e["id"] for e in batch.raw_events] == ["a", "b", "c"] and batch.complete
    second, third = world.graph()[1:]
    assert str(second.url) == NEXT  # exactly the link Microsoft gave, no parameters added
    assert "SKIP-2" in str(third.url)
    for later in (second, third):  # the preferences and the token go on every page
        assert later.headers.get_list("prefer") == [
            'outlook.timezone="UTC"',
            'IdType="ImmutableId"',
        ]
        assert later.headers["authorization"] == "Bearer ACCESS-TOKEN-1"


EVIL_LINKS = [
    "https://graph.microsoft.com/v1.0/../beta/me/calendarView",
    "https://graph.microsoft.com/v1.0/%2e%2e/beta/me",
    "https://graph.microsoft.com/v1.0/%2E%2E/beta/me",
    "https://graph.microsoft.com/v1.0/%252e%252e/beta/me",
    "https://graph.microsoft.com/v1.0/./me/calendarView",
    "https://graph.microsoft.com/v1.0/me/..",
    "https://graph.microsoft.com/v1.0/me/%2e./x",
    "https://evil.example/v1.0/me/calendarView?x=1",
    "http://graph.microsoft.com/v1.0/me/calendarView",
    "https://graph.microsoft.com@evil.example/v1.0/me/calendarView",
    "https://graph.microsoft.com.evil.example/v1.0/me/calendarView",
    "https://graph.microsoft.com/v1.0evil/me",
    "https://graph.microsoft.com/v2.0/me/calendarView",
    "https://graph.microsoft.com/v1.0",
    "https://graph.microsoft.com\\@evil.example/v1.0/x",
    "https://graph.microsoft.com/v1.0/me/calendarView\n?x=1",
    "//graph.microsoft.com/v1.0/me",
    "/v1.0/me/calendarView?skip=1",
    "graph.microsoft.com/v1.0/me/calendarView",
    "file:///etc/passwd",
    "",
]


@pytest.mark.parametrize("link", EVIL_LINKS)
def test_a_next_link_outside_the_graph_base_is_never_followed(world, store, conn, link, caplog):
    caplog.set_level(logging.DEBUG)
    world.pages = [page(event("a"), next_link=link), page(event("never"))]
    batch = make_source(world, store).sync(conn, now=NOW, full=True)
    assert batch.complete is False and batch.next_sync_token is None and batch.full is True
    assert [e["id"] for e in batch.raw_events] == ["a"]
    assert len(world.graph()) == 1  # the bad link was not requested, and no token went anywhere
    assert "unusable next link" in caplog.text


@pytest.mark.parametrize("link", [123, ["https://graph.microsoft.com/v1.0/x"], {"u": 1}, True])
def test_a_next_link_that_is_not_a_string_ends_the_read_as_incomplete(world, store, conn, link):
    world.pages = [page(event("a"), next_link=link)]
    batch = make_source(world, store).sync(conn, now=NOW, full=True)
    assert batch.complete is False and len(world.graph()) == 1


def test_a_runaway_chain_stops_at_the_page_cap_and_is_incomplete(world, store, conn, caplog):
    world.pages = [page(event("x"), next_link=NEXT)]
    batch = make_source(world, store).sync(conn, now=NOW, full=True)
    assert batch.complete is False and batch.next_sync_token is None
    assert len(world.graph()) == MAX_PAGES
    assert f"over {MAX_PAGES} pages" in caplog.text


def test_a_page_that_fails_after_the_first_returns_what_was_read_as_incomplete(world, store, conn):
    world.pages = [page(event("a"), next_link=NEXT), httpx.Response(503)]
    batch = make_source(world, store).sync(conn, now=NOW, full=True)
    assert [e["id"] for e in batch.raw_events] == ["a"]
    assert batch.complete is False and batch.next_sync_token is None  # no cancellations follow


def test_a_first_page_that_fails_raises(world, store, conn):
    world.pages = [httpx.Response(503)]
    with pytest.raises(TransientError):
        make_source(world, store).sync(conn, now=NOW, full=True)


def test_a_transport_failure_is_transient_and_names_only_its_type(world, store, conn):
    world.pages = [httpx.ConnectTimeout("boom https://graph.microsoft.com/?code=SECRET")]
    with pytest.raises(TransientError) as raised:
        make_source(world, store).sync(conn, now=NOW, full=True)
    assert "ConnectTimeout" in str(raised.value) and "SECRET" not in str(raised.value)


# ---- sync: errors ----


@pytest.mark.parametrize("status", [429, 500, 502, 503, 504, 400, 404, 410, 418])
def test_throttling_and_server_errors_are_transient_with_only_the_status(
    world, store, conn, status
):
    world.pages = [
        httpx.Response(
            status, json={"error": {"code": "X", "message": "ERROR-DESCRIPTION-SENTINEL"}}
        )
    ]
    with pytest.raises(TransientError) as raised:
        make_source(world, store).sync(conn, now=NOW, full=True)
    assert not isinstance(raised.value, AuthRevoked)
    assert str(status) in str(raised.value) and "SENTINEL" not in str(raised.value)


def test_a_401_mints_a_fresh_token_and_tries_once_more(world, store, conn):
    world.pages = [httpx.Response(401), page(event("a"))]
    world.refresh_answers = [world.refresh_ok(1), world.refresh_ok(2)]
    batch = make_source(world, store).sync(conn, now=NOW, full=True)
    assert [e["id"] for e in batch.raw_events] == ["a"]
    first, second = world.graph()
    assert first.headers["authorization"] == "Bearer ACCESS-TOKEN-1"
    assert second.headers["authorization"] == "Bearer ACCESS-TOKEN-2"


def test_a_second_401_means_the_grant_is_gone(world, store, conn):
    world.pages = [httpx.Response(401)]
    world.refresh_answers = [world.refresh_ok(1), world.refresh_ok(2)]
    with pytest.raises(AuthRevoked):
        make_source(world, store).sync(conn, now=NOW, full=True)
    assert len(world.graph()) == 2


@pytest.mark.parametrize("code", sorted(GRANT_CODES))
def test_a_403_that_says_the_grant_is_too_narrow_needs_a_reconnect(world, store, conn, code):
    world.pages = [httpx.Response(403, json={"error": {"code": code}})]
    with pytest.raises(AuthRevoked):
        make_source(world, store).sync(conn, now=NOW, full=True)


@pytest.mark.parametrize(
    "answer",
    [
        httpx.Response(403, json={"error": {"code": "ErrorTooManyRequests"}}),
        httpx.Response(403),
        httpx.Response(403, text="<html>"),
    ],
)
def test_any_other_403_is_not_something_reconnecting_would_fix(world, store, conn, answer):
    world.pages = [answer]
    with pytest.raises(TransientError) as raised:
        make_source(world, store).sync(conn, now=NOW, full=True)
    assert not isinstance(raised.value, AuthRevoked)


@pytest.mark.parametrize(
    "answer",
    [
        httpx.Response(200, text="not json"),
        httpx.Response(200, json=[1]),
        httpx.Response(200, json={"value": "x"}),
        httpx.Response(200, json={"value": [1, 2]}),
        httpx.Response(200, json={}),
    ],
    ids=["not json", "array", "value not list", "items not objects", "no value"],
)
def test_a_malformed_page_is_transient(world, store, conn, answer):
    world.pages = [answer]
    with pytest.raises(TransientError):
        make_source(world, store).sync(conn, now=NOW, full=True)


def test_a_revoked_grant_at_the_token_endpoint_is_auth_revoked_and_nothing_is_called(
    world, store, conn
):
    world.refresh_answers = [httpx.Response(400, json=doc("microsoft", "error_invalid_grant"))]
    with pytest.raises(AuthRevoked) as raised:
        make_source(world, store).sync(conn, now=NOW, full=True)
    assert raised.value.expired is False and world.graph() == []


def test_an_idle_expired_grant_says_expired(world, store, conn):
    world.refresh_answers = [
        httpx.Response(400, json=doc("microsoft", "error_refresh_idle_700082"))
    ]
    with pytest.raises(AuthRevoked) as raised:
        make_source(world, store).sync(conn, now=NOW, full=True)
    assert raised.value.expired is True


# ---- get_event ----


def test_get_event_asks_for_one_event_with_the_same_preferences(world, store, conn):
    world.pages = [httpx.Response(200, json=event("AAMk="))]
    got = make_source(world, store).get_event(conn, "AAMk=")
    assert got["id"] == "AAMk="
    [request] = world.graph()
    assert request.url.raw_path.split(b"?")[0] == b"/v1.0/me/events/AAMk%3D"
    assert request.headers.get_list("prefer") == ['outlook.timezone="UTC"', 'IdType="ImmutableId"']
    assert parse_qs(request.url.query.decode()) == {"$select": [SELECT]}


@pytest.mark.parametrize("event_id", ["a/b", "a?b=c", "a#frag", "a%2Fb", "a b", "a\\b", "é"])
def test_an_event_id_is_quoted_into_one_path_segment(world, store, conn, event_id):
    world.pages = [httpx.Response(200, json=event(event_id))]
    make_source(world, store).get_event(conn, event_id)
    [request] = world.graph()
    path = request.url.raw_path.split(b"?")[0].decode()
    assert path.startswith("/v1.0/me/events/") and path.count("/") == 4
    assert request.url.host == "graph.microsoft.com"
    assert request.url.params.get("$select") == SELECT  # and nothing else in the query
    assert list(request.url.params) == ["$select"]


@pytest.mark.parametrize("event_id", ["", ".", ".."])
def test_an_event_id_that_could_reach_another_resource_is_refused_before_any_request(
    world, store, conn, event_id
):
    with pytest.raises(ValueError):
        make_source(world, store).get_event(conn, event_id)
    assert world.requests == []


@pytest.mark.parametrize("event_id", [None, 5, b"x", ["x"]])
def test_an_event_id_that_is_not_a_string_is_refused(world, store, conn, event_id):
    with pytest.raises(ValueError):
        make_source(world, store).get_event(conn, event_id)


@pytest.mark.parametrize("status", [404, 410])
def test_a_missing_event_is_none(world, store, conn, status):
    world.pages = [httpx.Response(status, json={"error": {"code": "ErrorItemNotFound"}})]
    assert make_source(world, store).get_event(conn, "gone") is None


def test_get_event_maps_errors_like_the_list(world, store, conn):
    world.pages = [httpx.Response(503)]
    with pytest.raises(TransientError):
        make_source(world, store).get_event(conn, "x")
    world.pages = [httpx.Response(403, json={"error": {"code": "ErrorAccessDenied"}})]
    with pytest.raises(AuthRevoked):
        make_source(world, store).get_event(conn, "x")
    world.pages = [httpx.Response(200, text="nope")]
    with pytest.raises(TransientError):
        make_source(world, store).get_event(conn, "x")


# ---- the token cache: minting ----


def test_the_first_get_mints_from_the_stored_token_and_saves_the_rotated_one(
    world, store, conn, session
):
    token = make_cache(world, store).get(conn)
    assert token == "ACCESS-TOKEN-1"
    [form] = world.refreshes()
    assert form["grant_type"] == "refresh_token" and form["refresh_token"] == "OLD-REFRESH-TOKEN"
    assert form["client_secret"] == CLIENT_SECRET
    request = [r for r in world.requests if r.url.host == "login.microsoftonline.com"][0]
    assert request.url.path == f"/{TENANT}/oauth2/v2.0/token" and request.url.query == b""
    assert stored_token(session, conn) == "REFRESH-TOKEN-ROTATED-1"  # the old one is replaced


def test_the_next_mint_uses_the_rotated_token_not_the_old_one(world, store, conn, session):
    clock = Clock()
    cache = make_cache(world, store, clock)
    world.refresh_answers = [world.refresh_ok(1), world.refresh_ok(2)]
    cache.get(conn)
    clock.advance(minutes=56)  # inside the five minutes before the hour is up
    assert cache.get(conn) == "ACCESS-TOKEN-2"
    assert [f["refresh_token"] for f in world.refreshes()] == [
        "OLD-REFRESH-TOKEN",
        "REFRESH-TOKEN-ROTATED-1",
    ]
    assert stored_token(session, conn) == "REFRESH-TOKEN-ROTATED-2"


def test_a_cached_token_is_reused_until_five_minutes_before_it_expires(world, store, conn):
    clock = Clock()
    cache = make_cache(world, store, clock)
    cache.get(conn)
    clock.advance(minutes=50)
    cache.get(conn)
    assert world.minted == 1
    clock.advance(minutes=6)
    world.refresh_answers = [world.refresh_ok(2)]
    cache.get(conn)
    assert world.minted == 2


def test_the_rotated_token_is_written_under_the_current_key_version(
    world, store, conn, session, monkeypatch
):
    rotated = make_settings(
        microsoft_client_id="ms-client-id-placeholder",
        microsoft_client_secret=CLIENT_SECRET,
        token_enc_key=KEY_2,
        token_enc_key_previous=KEY,
        token_enc_key_version=2,
    )
    monkeypatch.setattr(vault, "get_settings", lambda: rotated)
    assert conn.key_version == 1
    cache = make_cache(world, store, settings=rotated)
    cache.get(conn)
    session.expire_all()
    row = session.get(CalendarConnection, conn.id)
    assert row.key_version == 2  # Microsoft rows move to a new key by refreshing
    assert (
        vault.decrypt(
            row.refresh_token_enc, 2, purpose="ms_refresh_token", owner=row.user_id
        ).reveal()
        == "REFRESH-TOKEN-ROTATED-1"
    )


def test_a_reply_without_a_new_refresh_token_leaves_the_stored_one(world, store, conn, session):
    world.refresh_answers = [world.refresh_ok(1, with_refresh=False)]
    before = conn.refresh_token_enc
    assert make_cache(world, store).get(conn) == "ACCESS-TOKEN-1"
    session.expire_all()
    assert bytes(session.get(CalendarConnection, conn.id).refresh_token_enc) == bytes(before)


# ---- the token cache: the compare-and-swap (review focus 1) ----


def test_the_swap_replaces_only_the_blob_it_read(store, conn, session):
    blob = bytes(conn.refresh_token_enc)
    new, version = vault.encrypt("NEW-ONE", purpose="ms_refresh_token", owner=conn.user_id)
    assert store.swap(conn.id, blob, new, version) is True
    newer, _ = vault.encrypt("NEWER-ONE", purpose="ms_refresh_token", owner=conn.user_id)
    assert store.swap(conn.id, blob, newer, version) is False  # a stale writer
    assert stored_token(session, conn) == "NEW-ONE"


def test_a_stale_writer_never_overwrites_a_newer_token(world, store, conn, session):
    """Two refreshes race. Both read the same stored token; the first to finish rotates it. The
    second's rotated token loses the compare-and-swap, the row keeps the first's, and the second
    still gets its access token (the old refresh token works until its own expiry)."""
    cache = make_cache(world, store)
    winner, _ = vault.encrypt("WINNER-TOKEN", purpose="ms_refresh_token", owner=conn.user_id)

    def another_process_rotates():
        assert store.swap(conn.id, bytes(conn.refresh_token_enc), winner, 1)

    world.on_refresh = another_process_rotates
    assert cache.get(conn) == "ACCESS-TOKEN-1"  # no exception, the sync carries on
    assert stored_token(session, conn) == "WINNER-TOKEN"


def test_a_lost_race_logs_no_token(world, store, conn, caplog):
    caplog.set_level(logging.DEBUG)
    blob, _ = vault.encrypt("WINNER-TOKEN", purpose="ms_refresh_token", owner=conn.user_id)
    world.on_refresh = lambda: store.swap(conn.id, bytes(conn.refresh_token_enc), blob, 1)
    make_cache(world, store).get(conn)
    assert "another writer won" in caplog.text
    for sentinel in SENTINELS:
        assert sentinel not in caplog.text


class BrokenStore(ConnectionTokenStore):
    def __init__(self, inner: ConnectionTokenStore, *, fail: str) -> None:
        self.inner, self.fail = inner, fail

    def read(self, conn_id):
        return self.inner.read(conn_id)

    def swap(self, conn_id, previous, new, key_version):
        if self.fail == "swap":
            raise RuntimeError("the database is down: REFRESH-TOKEN-ROTATED-1")
        return self.inner.swap(conn_id, previous, new, key_version)


def test_a_failed_write_does_not_lose_the_connection_or_leak_the_token(
    world, store, conn, session, caplog
):
    caplog.set_level(logging.DEBUG)
    cache = make_cache(world, BrokenStore(store, fail="swap"))
    assert cache.get(conn) == "ACCESS-TOKEN-1"  # the sync carries on
    assert (
        stored_token(session, conn) == "OLD-REFRESH-TOKEN"
    )  # the old token is still stored and valid
    assert "RuntimeError" in caplog.text and "database is down" not in caplog.text
    for sentinel in SENTINELS:
        assert sentinel not in caplog.text
    # the next mint (after a clock jump) still works from the old token
    clock = Clock()
    cache2 = make_cache(world, store, clock)
    world.refresh_answers = [world.refresh_ok(2)]
    assert cache2.get(conn) == "ACCESS-TOKEN-2"


def test_a_vault_failure_while_saving_does_not_fail_the_sync(
    world, store, conn, monkeypatch, session
):
    real = vault.encrypt

    def encrypt(plaintext, *, purpose, owner):
        raise vault.VaultError("no key")

    cache = make_cache(world, store)
    monkeypatch.setattr(vault, "encrypt", encrypt)
    assert cache.get(conn) == "ACCESS-TOKEN-1"
    monkeypatch.setattr(vault, "encrypt", real)
    assert stored_token(session, conn) == "OLD-REFRESH-TOKEN"


def test_the_old_refresh_token_is_not_kept_after_a_successful_swap(world, store, conn, session):
    old_blob = bytes(conn.refresh_token_enc)
    make_cache(world, store).get(conn)
    session.expire_all()
    assert bytes(session.get(CalendarConnection, conn.id).refresh_token_enc) != old_blob
    assert b"OLD-REFRESH-TOKEN" not in bytes(
        session.get(CalendarConnection, conn.id).refresh_token_enc
    )


# ---- the token cache: failures and housekeeping ----


def test_a_transient_refresh_failure_rides_a_still_valid_cached_token(world, store, conn):
    clock = Clock()
    cache = make_cache(world, store, clock)
    cache.get(conn)
    clock.advance(minutes=56)  # inside the early window, the token still has 4 minutes
    world.refresh_answers = [httpx.Response(503)]
    assert cache.get(conn) == "ACCESS-TOKEN-1"
    clock.advance(minutes=4)
    with pytest.raises(TransientError):
        cache.get(conn)


def test_a_dead_grant_drops_the_cached_token_and_raises(world, store, conn):
    clock = Clock()
    cache = make_cache(world, store, clock)
    cache.get(conn)
    clock.advance(minutes=56)
    world.refresh_answers = [httpx.Response(400, json=doc("microsoft", "error_invalid_grant"))]
    with pytest.raises(AuthRevoked):
        cache.get(conn)
    clock.advance(minutes=1)
    world.refresh_answers = [world.refresh_ok(2)]
    assert cache.get(conn) == "ACCESS-TOKEN-2"  # the dead token was not reused


def test_invalidate_and_forget_drop_tokens(world, store, conn):
    cache = make_cache(world, store)
    world.refresh_answers = [world.refresh_ok(1), world.refresh_ok(2), world.refresh_ok(3)]
    cache.get(conn)
    cache.invalidate(conn.id)
    assert cache.get(conn) == "ACCESS-TOKEN-2"
    cache.forget(conn.id)
    assert cache.get(conn) == "ACCESS-TOKEN-3"


def test_a_missing_token_or_tenant_is_a_dead_grant(world, store, conn, session):
    cache = make_cache(world, store)
    row = session.get(CalendarConnection, conn.id)
    row.tenant_id = None
    session.commit()
    with pytest.raises(AuthRevoked):
        cache.get(conn)
    row.tenant_id, row.refresh_token_enc, row.key_version = TENANT, None, None
    session.commit()
    with pytest.raises(AuthRevoked):
        cache.get(conn)
    assert world.requests == []


def test_an_unreadable_token_is_not_a_withdrawn_grant(world, store, conn, session):
    row = session.get(CalendarConnection, conn.id)
    row.user_id, other = row.user_id, make_user(session, "other@example.test")
    blob, version = vault.encrypt("x", purpose="ms_refresh_token", owner=other.id)
    row.refresh_token_enc, row.key_version = blob, version  # sealed for somebody else
    session.commit()
    with pytest.raises(RefreshTokenUnreadable):
        make_cache(world, store).get(conn)
    assert world.requests == []


def test_a_missing_vault_key_is_transient_not_a_dead_grant(world, store, conn, monkeypatch):
    monkeypatch.setattr(vault, "get_settings", lambda: make_settings())
    with pytest.raises(TransientError) as raised:
        make_cache(world, store).get(conn)
    assert not isinstance(raised.value, AuthRevoked)


def test_an_unconfigured_client_is_transient(world, store, conn):
    cache = make_cache(world, store, settings=make_settings())
    with pytest.raises(TransientError):
        cache.get(conn)
    assert world.requests == []


def test_concurrent_callers_for_one_connection_mint_once(world, store, conn):
    cache = make_cache(world, store)
    results = []
    threads = [threading.Thread(target=lambda: results.append(cache.get(conn))) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert results == ["ACCESS-TOKEN-1"] * 8 and world.minted == 1


def test_the_cache_is_redacted_and_cannot_be_pickled(world, store, conn):
    cache = make_cache(world, store)
    cache.get(conn)
    assert "ACCESS-TOKEN" not in repr(cache) + str(cache) and "redacted" in repr(cache)
    with pytest.raises(TypeError):
        pickle.dumps(cache)


def test_no_token_secret_or_description_reaches_a_log_on_any_path(world, store, conn, caplog):
    caplog.set_level(logging.DEBUG)
    source = make_source(world, store)
    world.pages = [
        page(event("a"), next_link=NEXT),
        httpx.Response(503, json={"error": {"message": "ERROR-DESCRIPTION-SENTINEL"}}),
    ]
    source.sync(conn, now=NOW, full=True)
    world.pages = [page(event("a"), next_link="https://evil.example/")]
    source.sync(conn, now=NOW, full=True)
    world.pages = [httpx.Response(429, text="ERROR-DESCRIPTION-SENTINEL")]
    with pytest.raises(TransientError):
        source.sync(conn, now=NOW, full=True)
    app_lines = "\n".join(r.getMessage() for r in caplog.records if r.name.startswith("app"))
    for sentinel in SENTINELS:
        assert sentinel not in app_lines, sentinel


# ---- the client secret ----


def test_an_expired_client_secret_is_transient_and_alerts_the_admins(
    world, store, conn, monkeypatch
):
    seen = []
    monkeypatch.setattr(microsoft_alerts, "secret_expired", lambda now: seen.append(now))
    world.refresh_answers = [
        httpx.Response(401, json=doc("microsoft", "error_secret_expired_7000222"))
    ]
    with pytest.raises(TransientError) as raised:
        make_cache(world, store, Clock()).get(conn)
    assert not isinstance(raised.value, AuthRevoked) and seen == [NOW]


def test_a_wrong_client_secret_is_transient_without_the_expiry_alert(
    world, store, conn, monkeypatch
):
    seen = []
    monkeypatch.setattr(microsoft_alerts, "secret_expired", lambda now: seen.append(now))
    world.refresh_answers = [
        httpx.Response(401, json={"error": "invalid_client", "error_codes": [7000215]})
    ]
    with pytest.raises(TransientError):
        make_cache(world, store).get(conn)
    assert seen == []


@pytest.fixture
def admin(session):
    user = make_user(session, "root@example.test")
    user.is_admin = True
    session.commit()
    return user


@pytest.fixture
def alert_db(engine, monkeypatch):
    monkeypatch.setattr(db, "SessionLocal", lambda: Session(engine))


def notes(session, kind: str) -> list[Notification]:
    session.expire_all()
    return list(session.scalars(select(Notification).where(Notification.kind == kind)))


def test_the_expiry_alert_is_written_once_a_day(admin, session, alert_db):
    microsoft_alerts.secret_expired(NOW)
    microsoft_alerts.secret_expired(NOW + timedelta(hours=3))
    [note] = notes(session, "admin.microsoft_secret_expired")
    assert "MICROSOFT_CLIENT_SECRET" in note.body and "http" not in note.body
    microsoft_alerts.secret_expired(NOW + timedelta(days=1))
    assert len(notes(session, "admin.microsoft_secret_expired")) == 2


def test_an_alert_that_cannot_be_written_never_raises(monkeypatch, caplog):
    def broken():
        raise RuntimeError("connection string with a password")

    monkeypatch.setattr(db, "SessionLocal", broken)
    microsoft_alerts.secret_expired(NOW)
    microsoft_alerts.secret_expiring(
        SETTINGS.model_copy(update={"microsoft_secret_expires_on": "2026-12-01"}), NOW
    )
    assert "password" not in caplog.text and "RuntimeError" in caplog.text


def test_the_reminder_names_the_date_and_the_steps(admin, session, alert_db):
    settings = SETTINGS.model_copy(update={"microsoft_secret_expires_on": "2026-12-01"})
    microsoft_alerts.secret_expiring(settings, NOW)
    [note] = notes(session, "admin.microsoft_secret_expiring")
    assert (
        "2026-12-01" in note.body
        and "22 days" in note.body
        and "delete the old secret" in note.body
    )
    microsoft_alerts.secret_expiring(
        settings.model_copy(update={"microsoft_secret_expires_on": ""}), NOW + timedelta(days=2)
    )
    assert len(notes(session, "admin.microsoft_secret_expiring")) == 1  # nothing to remind about


def test_an_already_expired_secret_says_so(admin, session, alert_db):
    settings = SETTINGS.model_copy(update={"microsoft_secret_expires_on": "2026-11-01"})
    microsoft_alerts.secret_expiring(settings, NOW)
    [note] = notes(session, "admin.microsoft_secret_expiring")
    assert "has expired" in note.body


def test_the_sync_reminds_once_a_day_only_inside_the_thirty_day_window(
    world, store, conn, monkeypatch
):
    calls = []
    monkeypatch.setattr(
        microsoft_alerts, "secret_expiring", lambda settings, now: calls.append(now)
    )
    near = SETTINGS.model_copy(update={"microsoft_secret_expires_on": "2026-12-01"})
    source = MicrosoftSource(
        near, cache=make_cache(world, store, settings=near), transport=world.transport
    )
    source.sync(conn, now=NOW, full=True)
    source.sync(conn, now=NOW + timedelta(minutes=5), full=True)
    assert len(calls) == 1
    source.sync(conn, now=NOW + timedelta(days=1), full=True)
    assert len(calls) == 2
    far = SETTINGS.model_copy(update={"microsoft_secret_expires_on": "2027-06-01"})
    calls.clear()
    MicrosoftSource(
        far, cache=make_cache(world, store, settings=far), transport=world.transport
    ).sync(conn, now=NOW, full=True)
    MicrosoftSource(SETTINGS, cache=make_cache(world, store), transport=world.transport).sync(
        conn, now=NOW, full=True
    )
    assert calls == []


# ---- end to end through the sync ----


def fixture_event(name: str, event_id: str, start: datetime) -> dict:
    raw = doc("graph", name)
    raw["id"] = event_id
    raw["start"] = {"dateTime": start.strftime("%Y-%m-%dT%H:%M:%S.0000000"), "timeZone": "UTC"}
    raw["end"] = {
        "dateTime": (start + timedelta(minutes=30)).strftime("%Y-%m-%dT%H:%M:%S.0000000"),
        "timeZone": "UTC",
    }
    return raw


def test_a_graph_calendar_syncs_into_rows_and_joins_and_a_removed_event_is_cancelled(
    world, store, conn, session, monkeypatch
):
    """Through the sync's own `sync_connection`: MicrosoftSource, the registry's Graph parser, the
    rules and the materialiser, then a second full listing without one event."""

    start = NOW + timedelta(hours=2)
    world.pages = [
        page(
            fixture_event("zoom_in_body", "e-zoom", start),
            fixture_event("meet_in_location", "e-meet", start + timedelta(hours=1)),
            fixture_event("teams_work", "e-teams", start),
        )
    ]
    source = make_source(world, store)
    gcal_sync.sync_connection(
        session, conn, source, now=NOW, settings=make_settings(autojoin_lead_seconds=60)
    )
    session.expire_all()
    rows = {e.source_event_id: e for e in session.scalars(select(CalendarEvent))}
    assert set(rows) == {"e-zoom", "e-meet", "e-teams"}
    assert rows["e-teams"].platform == "teams"
    joins = {j.calendar_event_id for j in session.scalars(select(ScheduledJoin))}
    assert joins == {rows["e-zoom"].id, rows["e-meet"].id}  # Teams cannot be joined yet
    assert conn.last_sync_at is not None and conn.sync_token is None  # no delta token, ever
    assert conn.last_full_sync_at == NOW

    world.pages = [page(fixture_event("meet_in_location", "e-meet", start + timedelta(hours=1)))]
    gcal_sync.sync_connection(
        session,
        conn,
        source,
        now=NOW + timedelta(minutes=5),
        settings=make_settings(autojoin_lead_seconds=60),
    )
    session.expire_all()
    left = {j.calendar_event_id: j.state for j in session.scalars(select(ScheduledJoin))}
    assert left[rows["e-zoom"].id] == "cancelled" and left[rows["e-meet"].id] == "scheduled"


def test_an_incomplete_listing_never_cancels_anything(world, store, conn, session):
    start = NOW + timedelta(hours=2)
    world.pages = [
        page(
            fixture_event("zoom_in_body", "e-zoom", start),
            fixture_event("meet_in_location", "e-meet", start),
        )
    ]
    source = make_source(world, store)
    settings = make_settings(autojoin_lead_seconds=60)
    gcal_sync.sync_connection(session, conn, source, now=NOW, settings=settings)
    world.pages = [
        page(fixture_event("meet_in_location", "e-meet", start), next_link=NEXT),
        httpx.Response(503),
    ]
    gcal_sync.sync_connection(
        session, conn, source, now=NOW + timedelta(minutes=5), settings=settings
    )
    session.expire_all()
    assert {j.state for j in session.scalars(select(ScheduledJoin))} == {
        "scheduled"
    }  # nothing cancelled


def test_the_registry_builds_this_source_for_microsoft(world):
    from app import calendar_providers

    source = calendar_providers.PROVIDERS["microsoft"].make_source(SETTINGS)
    assert isinstance(source, MicrosoftSource) and isinstance(source.cache, MicrosoftTokenCache)
    sources = calendar_providers.build_sources(SETTINGS)
    assert sorted(s.provider for s in sources) == ["google", "microsoft"]
    cache = {s.provider: s.cache for s in sources}["microsoft"]
    assert isinstance(cache, MicrosoftTokenCache)


def test_the_scheduler_gets_one_sync_job_per_enabled_provider_and_the_runner_shares_the_sources():
    from app import gcal_sync
    from app import scheduler as sched

    class FakeScheduler:
        def __init__(self) -> None:
            self.jobs: list[tuple[str, str]] = []

        def register(self, name, seconds, fn, loop="dispatch"):
            self.jobs.append((name, loop))

    fake = FakeScheduler()
    gcal_sync.register_calendar_sync(fake, SETTINGS)
    assert fake.jobs == [
        ("calendar_sync", "sync"),
        ("calendar_sync_microsoft", "sync"),
        ("microsoft_secret_reminder", "sync"),
    ]
    off = SETTINGS.model_copy(update={"microsoft_calendar_enabled": False})
    fake = FakeScheduler()
    gcal_sync.register_calendar_sync(fake, off)
    assert fake.jobs == [("calendar_sync", "sync")]  # nothing Microsoft while the flag is off
    assert callable(sched.register_jobs)


# ---- review fixes (Opus review of #153) ----


def test_dot_segments_are_refused_but_a_dotted_query_is_fine():
    assert graph_http.under_graph_base("https://graph.microsoft.com/v1.0/me/calendarView?a=../b")
    assert graph_http.under_graph_base(
        "https://graph.microsoft.com/v1.0/me/calendarView?$skiptoken=a%2E%2Eb&x=1#frag"
    )
    assert not graph_http.under_graph_base("https://graph.microsoft.com/v1.0/../beta/me")
    with pytest.raises(ValueError):  # `get` refuses it before a request
        graph_http.get("t", "https://graph.microsoft.com/v1.0/%2e%2e/beta/me")


def runner_world(session, conn):
    """A Zoom event materialised for the Outlook connection, starting at 10:00, with its join."""
    from app.models import AppState, Template

    session.add(AppState(id=1, autojoin_started_at=datetime(2026, 1, 1, tzinfo=UTC)))
    template = Template(owner_user_id=conn.user_id, name="General", instructions="Notes.")
    session.add(template)
    session.flush()
    prefs = session.get(UserSettings, conn.user_id)
    prefs.calendar_template_id = template.id
    session.commit()
    start = datetime(2026, 11, 9, 10, 0, tzinfo=UTC)
    raw = fixture_event("zoom_in_body", "e-zoom", start)
    gcal_sync.apply_batch(
        session,
        conn,
        SyncBatch([raw], None, complete=True, full=True),
        prefs,
        now=start - timedelta(minutes=10),
        settings=RUNNER_SETTINGS,
    )
    session.expire_all()
    return start, session.scalars(select(ScheduledJoin)).one()


RUNNER_SETTINGS = make_settings(
    autojoin_lead_seconds=60, bot_max_local=1, user_bot_limit=2, teams_join_enabled=False
)


@pytest.fixture
def host():
    from tests.fakes.vexa_host import FakeVexaHost

    return FakeVexaHost()


@pytest.fixture(autouse=True)
def alert_settings(monkeypatch):
    from app import alerts

    monkeypatch.setattr(
        alerts, "get_settings", lambda: make_settings(admin_alert_email="o@example.test")
    )


def test_an_outlook_event_deleted_before_it_starts_is_cancelled_by_the_runner_and_no_bot_goes(
    world, store, conn, session, host
):
    """The re-check asks Graph, Graph says ErrorItemNotFound, and the join is cancelled: the
    cancelled stub must be one the Graph parser can read (Google's shape would be skipped)."""
    from app.join_runner import run_due_joins

    start, join = runner_world(session, conn)
    assert join.state == "scheduled" and join.platform == "zoom"
    world.pages = [httpx.Response(404, json={"error": {"code": "ErrorItemNotFound"}})]
    source = make_source(world, store)
    run_due_joins(session, host, source, now=start - timedelta(minutes=2), settings=RUNNER_SETTINGS)
    run_due_joins(
        session, host, source, now=start - timedelta(seconds=30), settings=RUNNER_SETTINGS
    )
    session.expire_all()
    fresh = session.get(ScheduledJoin, join.id)
    assert (fresh.state, fresh.skip_reason) == ("cancelled", "cancelled")
    assert host.bots_sent == 0
    [asked] = world.graph()
    assert asked.url.raw_path.startswith(b"/v1.0/me/events/")


def test_a_mailbox_level_404_at_the_runner_never_cancels_a_real_join(
    world, store, conn, session, host
):
    from app.join_runner import run_due_joins

    start, join = runner_world(session, conn)
    world.pages = [httpx.Response(404, json={"error": {"code": "MailboxNotEnabledForRESTAPI"}})]
    source = make_source(world, store)
    run_due_joins(session, host, source, now=start - timedelta(minutes=2), settings=RUNNER_SETTINGS)
    session.expire_all()
    assert session.get(ScheduledJoin, join.id).state == "scheduled"  # not cancelled
    assert host.bots_sent == 0  # and time is not up yet


def test_the_cancelled_stub_is_read_as_cancelled_by_each_provider():
    from app import calendar_providers
    from app.graph_events import parse_event

    google = calendar_providers.cancelled_stub("google", "x")
    assert google == {"id": "x", "status": "cancelled"}
    graph = calendar_providers.cancelled_stub("microsoft", "x")
    assert parse_event(graph).status == "cancelled" and parse_event(graph).source_event_id == "x"
    assert calendar_providers.cancelled_stub("ics", "x") == google  # no row: Google's shape


@pytest.mark.parametrize(
    "answer,gone",
    [
        (httpx.Response(404, json={"error": {"code": "ErrorItemNotFound"}}), True),
        (httpx.Response(410), True),
        (httpx.Response(404, json={"error": {"code": "MailboxNotEnabledForRESTAPI"}}), False),
        (httpx.Response(404, json={"error": {"code": "MailboxNotSupportedForRESTAPI"}}), False),
        (httpx.Response(404), False),
        (httpx.Response(404, text="<html>not json</html>"), False),
    ],
    ids=[
        "item not found",
        "410",
        "mailbox not enabled",
        "mailbox not supported",
        "bare 404",
        "html 404",
    ],
)
def test_only_an_item_not_found_or_a_410_means_the_event_is_gone(world, store, conn, answer, gone):
    world.pages = [answer]
    source = make_source(world, store)
    if gone:
        assert source.get_event(conn, "evt") is None
    else:
        with pytest.raises(TransientError) as raised:
            source.get_event(conn, "evt")
        assert not isinstance(raised.value, AuthRevoked) and "404" in str(raised.value)


def test_a_refused_grant_drops_the_cached_token_so_the_next_call_mints_again(world, store, conn):
    source = make_source(world, store)
    world.pages = [page(event("a"))]
    source.sync(conn, now=NOW, full=True)
    assert world.minted == 1
    world.pages = [httpx.Response(403, json={"error": {"code": "ErrorAccessDenied"}})]
    with pytest.raises(AuthRevoked):
        source.sync(conn, now=NOW, full=True)
    world.pages = [page(event("a"))]
    world.refresh_answers = [world.refresh_ok(2)]
    source.sync(conn, now=NOW, full=True)
    assert world.minted == 2  # not read with the token of the refused grant


def test_a_second_401_also_drops_the_cached_token(world, store, conn):
    source = make_source(world, store)
    world.refresh_answers = [world.refresh_ok(1), world.refresh_ok(2), world.refresh_ok(3)]
    world.pages = [httpx.Response(401)]
    with pytest.raises(AuthRevoked):
        source.sync(conn, now=NOW, full=True)
    minted_after_failure = world.minted
    world.pages = [page(event("a"))]
    source.sync(conn, now=NOW, full=True)
    assert world.minted == minted_after_failure + 1


def test_a_token_minted_for_one_identity_is_never_used_for_the_next(world, store, conn, session):
    """A reconnect over the same row (another Microsoft identity, or just a fresh connect) changes
    who the connection is: the cached access token of the old identity must not be read."""
    cache = make_cache(world, store)
    world.refresh_answers = [world.refresh_ok(1), world.refresh_ok(2)]
    assert cache.get(conn) == "ACCESS-TOKEN-1"
    assert cache.get(conn) == "ACCESS-TOKEN-1"  # same identity: cached
    row = session.get(CalendarConnection, conn.id)
    row.account_subject = "oid-of-another-person"
    row.connected_at = NOW
    session.commit()
    session.refresh(conn)
    assert cache.get(conn) == "ACCESS-TOKEN-2"
    assert world.minted == 2


def test_graph_calls_for_one_connection_never_overlap(conn, store):
    """The sync thread and the runner's re-check share a source: one call at a time."""
    import time

    state = {"now": 0, "max": 0}
    guard = threading.Lock()

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "login.microsoftonline.com":
            return FakeGraphWorld.refresh_ok(1)
        with guard:
            state["now"] += 1
            state["max"] = max(state["max"], state["now"])
        time.sleep(0.05)
        with guard:
            state["now"] -= 1
        return httpx.Response(200, json=event("x"))

    transport = httpx.MockTransport(handler)
    cache = MicrosoftTokenCache(settings=SETTINGS, transport=transport, store=store)
    source = MicrosoftSource(SETTINGS, cache=cache, transport=transport)
    threads = [threading.Thread(target=lambda: source.get_event(conn, "x")) for _ in range(6)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert state["max"] == 1


def test_the_secret_reminder_has_its_own_hourly_job_that_needs_no_due_connection(monkeypatch):
    calls = []
    monkeypatch.setattr(
        microsoft_alerts, "secret_expiring", lambda settings, now: calls.append(now)
    )

    class FakeScheduler:
        def __init__(self) -> None:
            self.jobs: dict[str, tuple] = {}

        def register(self, name, seconds, fn, loop="dispatch"):
            self.jobs[name] = (seconds, fn, loop)

    near = SETTINGS.model_copy(update={"microsoft_secret_expires_on": "2026-12-01"})
    fake = FakeScheduler()
    gcal_sync.register_calendar_sync(fake, near)
    seconds, job, loop = fake.jobs["microsoft_secret_reminder"]
    assert (seconds, loop) == (3600, "sync")
    job(None, NOW)  # no session, no connection: still reminds
    assert calls == [NOW]
    far = SETTINGS.model_copy(update={"microsoft_secret_expires_on": "2027-06-01"})
    calls.clear()
    microsoft_alerts.remind_if_due(far, NOW)
    microsoft_alerts.remind_if_due(SETTINGS, NOW)
    assert calls == []
    off = near.model_copy(update={"microsoft_calendar_enabled": False})
    fake = FakeScheduler()
    gcal_sync.register_calendar_sync(fake, off)
    assert "microsoft_secret_reminder" not in fake.jobs  # nothing Microsoft while the flag is off


def test_a_tiny_stated_lifetime_does_not_cause_a_refresh_on_every_call(world, store, conn):
    body = doc("microsoft", "refresh_response")
    body["expires_in"] = 30
    world.refresh_answers = [httpx.Response(200, json=body)]
    clock = Clock()
    cache = make_cache(world, store, clock)
    cache.get(conn)
    for _ in range(5):
        clock.advance(seconds=20)
        cache.get(conn)
    assert world.minted == 1  # a stated 30 s is treated as at least ten minutes


def test_a_removed_connection_is_logged_as_removed_not_as_a_lost_race(
    world, store, conn, engine, caplog
):
    caplog.set_level(logging.DEBUG)

    def disconnect_meanwhile():
        with Session(engine) as other:
            row = other.get(CalendarConnection, conn.id)
            row.refresh_token_enc, row.key_version = None, None
            other.commit()

    world.on_refresh = disconnect_meanwhile
    assert make_cache(world, store).get(conn) == "ACCESS-TOKEN-1"
    assert "the connection was removed" in caplog.text and "another writer won" not in caplog.text


def test_an_event_id_that_decodes_to_a_dot_segment_is_refused_before_any_request(
    world, store, conn
):
    with pytest.raises(ValueError):
        make_source(world, store).get_event(conn, "..%2f")
    assert world.requests == []
