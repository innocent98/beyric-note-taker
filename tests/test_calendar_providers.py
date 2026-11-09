"""The provider registry, the `{provider}` routes and the one-calendar rule (Outlook phase O, M1).

Google is the only provider with a row, so Microsoft is exercised through a fake row that the tests
register themselves: what is proven here is the shape every later provider plugs into.
"""

import pytest
from sqlalchemy import select

from app import calendar_connect, calendar_providers
from app.calendar_providers import PROVIDERS, Provider, SourceRegistry, as_registry
from app.models import CalendarConnection, OAuthFlow
from tests import test_calendar_connect as cc
from tests import test_web as tw
from tests.test_api import error_of
from tests.test_calendar_connect import (
    CONNECT,
    DISCONNECT,
    browser,
    callback,
    connect,
    connect_google,
    connection_of,
    landing,
)

capp, google, settings, vault_key, web = cc.capp, cc.google, cc.settings, cc.vault_key, cc.web
fake_openai, fake_vexa, owner = tw.fake_openai, tw.fake_vexa, tw.owner


class FakeSource:
    def __init__(self, provider: str) -> None:
        self.provider = provider
        self.calls: list[str] = []

    def sync(self, conn, *, now, full):  # pragma: no cover - not used here
        raise AssertionError("not called")

    def get_event(self, conn, source_event_id):
        self.calls.append(source_event_id)
        return None


@pytest.fixture
def microsoft_row(monkeypatch):
    """A `microsoft` registry row whose calendar feature is the scope string `Calendars.Read`."""
    row = Provider(
        name="microsoft",
        parse_event=lambda raw, conn: pytest.fail("not parsed here"),
        granted_features=lambda conn: (
            {"calendar"} if "Calendars.Read" in (conn.granted_scopes or "").split() else set()
        ),
        enabled=lambda settings: True,
        make_source=lambda settings: FakeSource("microsoft"),
        flow_cookie="calendar_flow_ms",
        flow_cookie_path="/api/calendar/microsoft",
    )
    monkeypatch.setitem(PROVIDERS, "microsoft", row)
    return row


def add_microsoft(session, user, status="connected", scopes="Calendars.Read User.Read"):
    conn = CalendarConnection(
        user_id=user.id,
        provider="microsoft",
        status=status,
        granted_scopes=scopes,
        account_email="ada@outlook.example.test",
        account_subject="oid-1",
        tenant_id="9188040d-6c67-4c5b-b112-36a304b66dad",
        account_kind="personal",
    )
    session.add(conn)
    session.commit()
    return conn


# ---- the registry ----


def test_google_and_microsoft_are_the_providers_with_a_row():
    assert list(PROVIDERS) == ["google", "microsoft"]
    assert calendar_providers.get("ics") is None
    assert PROVIDERS["microsoft"].flow_cookie == "calendar_flow_ms"
    assert PROVIDERS["microsoft"].flow_cookie_path == "/api/calendar/microsoft"
    assert PROVIDERS["google"].flow_cookie == calendar_connect.FLOW_COOKIE
    assert PROVIDERS["google"].flow_cookie_path == calendar_connect.FLOW_COOKIE_PATH


def test_features_come_from_the_connections_own_provider(microsoft_row):
    google_conn = CalendarConnection(
        provider="google", granted_scopes="https://www.googleapis.com/auth/calendar.events.readonly"
    )
    ms_conn = CalendarConnection(provider="microsoft", granted_scopes="Calendars.Read")
    assert calendar_providers.features_of(google_conn) == {"calendar"}
    assert calendar_providers.features_of(ms_conn) == {"calendar"}
    # a Google scope string means nothing to Microsoft, and a provider with no row gives nothing
    ms_conn.granted_scopes = google_conn.granted_scopes
    assert calendar_providers.features_of(ms_conn) == set()
    assert calendar_providers.features_of(CalendarConnection(provider="ics")) == set()


def test_a_source_registry_finds_each_source_by_provider():
    google_source, ms_source = FakeSource("google"), FakeSource("microsoft")
    registry = SourceRegistry([google_source, ms_source])
    assert registry.get("google") is google_source and registry.get("microsoft") is ms_source
    assert registry.get("ics") is None and len(registry) == 2
    assert as_registry(registry) is registry
    assert as_registry(google_source).get("google") is google_source
    assert len(as_registry(None)) == 0
    with pytest.raises(ValueError):
        SourceRegistry([google_source, FakeSource("google")])


def test_build_sources_makes_one_source_per_enabled_provider(settings, microsoft_row):
    sources = calendar_providers.build_sources(settings)
    assert sorted(s.provider for s in sources) == ["google", "microsoft"]
    assert (
        len(
            calendar_providers.build_sources(
                settings.model_copy(update={"calendar_enabled": False})
            )
        )
        == 1
    )


def test_parsing_needs_a_provider_with_a_row():
    with pytest.raises(ValueError):
        calendar_providers.parse({"id": "x"}, CalendarConnection(provider="ics"))


# ---- the routes ----


def test_the_microsoft_connect_and_callback_answer_404_while_the_flag_is_off(web):
    """The flag is off by default, so the routes do not exist for anyone. Disconnect still works
    (a person must always be able to delete their data); with nothing connected it is a no-op."""
    error_of(web.post("/api/calendar/microsoft/connect", {}), 404, "calendar_unavailable")
    response = web.client.get("/api/calendar/microsoft/callback?code=x&state=y")
    error_of(response, 404, "calendar_unavailable")
    assert web.post("/api/calendar/microsoft/disconnect").status_code == 200


def test_an_unknown_provider_is_a_validation_error_not_a_route(web):
    assert web.post("/api/calendar/yahoo/connect", {}).status_code == 422


def test_the_microsoft_callback_is_public_like_googles(capp, owner):
    anon = browser(capp, log_in=False)
    # not a 401: the guard lets it through and the route answers for itself
    error_of(
        anon.client.get("/api/calendar/microsoft/callback?code=x&state=y"),
        404,
        "calendar_unavailable",
    )


def test_a_microsoft_state_never_finishes_at_the_google_callback(web, session, owner, google):
    url, state, _ = connect(web)
    code = google.consent(url)
    flow = session.scalars(select(OAuthFlow)).one()
    assert flow.provider == "google"
    flow.provider = "microsoft"
    session.commit()
    path, query = landing(callback(web, state, code))
    assert (path, query) == ("/settings", {"calendar_error": "google_failed"})
    assert connection_of(session, owner) is None
    assert session.scalars(select(OAuthFlow)).all() == []  # used up whatever the answer


def test_a_google_flow_stores_its_provider(web, session):
    connect(web)
    assert session.scalars(select(OAuthFlow)).one().provider == "google"


# ---- one live calendar at a time ----


def test_connect_is_refused_while_another_providers_calendar_is_live(
    web, session, owner, microsoft_row
):
    add_microsoft(session, owner)
    response = web.post(CONNECT, {})
    error_of(response, 409, "calendar_already_connected")
    assert session.scalars(select(OAuthFlow)).all() == []


@pytest.mark.parametrize("status", ["degraded", "needs_reconnect"])
def test_a_degraded_or_dead_microsoft_calendar_still_blocks(
    web, session, owner, microsoft_row, status
):
    add_microsoft(session, owner, status=status)
    error_of(web.post(CONNECT, {}), 409, "calendar_already_connected")


def test_a_disconnected_microsoft_row_never_blocks(web, session, owner, microsoft_row):
    add_microsoft(session, owner, status="disconnected")
    assert web.post(CONNECT, {}).status_code == 200


def test_a_microsoft_row_without_the_calendar_feature_never_blocks(
    web, session, owner, microsoft_row
):
    add_microsoft(session, owner, scopes="User.Read")
    assert web.post(CONNECT, {}).status_code == 200


def test_asking_only_for_meet_is_not_blocked(web, session, owner, microsoft_row):
    add_microsoft(session, owner)
    assert web.post(CONNECT, {"features": ["meet"]}).status_code == 200


def test_the_callback_refuses_when_a_calendar_appeared_in_the_meantime(
    web, session, owner, google, microsoft_row
):
    url, state, _ = connect(web)  # allowed: nothing was connected yet
    code = google.consent(url)
    add_microsoft(session, owner)  # a second tab finished Microsoft meanwhile
    path, query = landing(callback(web, state, code))
    assert (path, query) == ("/settings", {"calendar_error": "already_connected"})
    assert connection_of(session, owner).provider == "microsoft"  # nothing of Google's stored
    assert (
        session.scalars(
            select(CalendarConnection).where(CalendarConnection.provider == "google")
        ).all()
        == []
    )


def test_the_state_reads_the_live_connection_of_whichever_provider(
    web, session, owner, microsoft_row
):
    add_microsoft(session, owner)
    body = web.get("/api/calendar").json()
    assert body["provider"] == "microsoft" and body["account_email"] == "ada@outlook.example.test"
    assert body["connected"] is True


def test_disconnect_takes_the_named_providers_row(web, session, owner, google, microsoft_row):
    connect_google(web, google)
    assert web.post(DISCONNECT).status_code == 200
    assert connection_of(session, owner).status == "disconnected"
    assert connection_of(session, owner).account_email is None
