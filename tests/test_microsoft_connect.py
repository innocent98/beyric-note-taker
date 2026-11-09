"""Connecting an Outlook (Microsoft) calendar: the consent URL, the seven callback checks, the
tenant rules, the mailbox probe, the one-calendar rule, reconnecting, and disconnecting with no
revoke (Outlook phase O, M3; spec 4.1, 4.4 to 4.6, 6.3, 8.1).

Microsoft's side is `tests/fakes/microsoft.py`: the real `MicrosoftOAuth` client over a fake
transport, with bodies built from DOCUMENTED shapes (the M0 run has not happened). Google's side is
`tests/fakes/google.py`, used where a test needs both providers at once.
"""

import base64
import hashlib
import logging
import threading
from datetime import UTC, datetime, timedelta
from urllib.parse import parse_qs, quote, urlparse

import httpx
import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app import (
    calendar_connect,
    calendar_providers,
    google_oauth,
    microsoft_alerts,
    microsoft_oauth,
    tokens,
    vault,
)
from app.db import get_session
from app.main import create_app
from app.microsoft_oauth import CONSUMER_TENANT
from app.models import (
    JOIN_STATES,
    CalendarConnection,
    CalendarEvent,
    Meeting,
    Notification,
    OAuthFlow,
    ScheduledJoin,
    UsageEntry,
    User,
    UserSettings,
)
from app.services import get_openai_client, get_vexa
from tests import test_calendar_connect as cc
from tests.fakes.google import FakeGoogle
from tests.fakes.microsoft import FakeMicrosoft
from tests.test_api import Api, error_of
from tests.test_calendar_connect import (
    add_join,
    add_notification,
    browser,
    count,
    kill,
    landing,
)
from tests.test_web import (  # noqa: F401
    OTHER_EMAIL,
    OWNER_EMAIL,
    fake_openai,
    fake_vexa,
    make_settings,
    make_user,
    owner,
    stranger,
    template,
)

CLIENT_ID = "ms-client-id-placeholder"
CLIENT_SECRET = "ms-client-secret-placeholder"
WORK_TENANT = "00000000-0000-0000-0000-0000000000aa"
OTHER_TENANT = "00000000-0000-0000-0000-0000000000bb"
KEY = base64.b64encode(b"K" * 32).decode()
CONNECT = "/api/calendar/microsoft/connect"
CALLBACK = "/api/calendar/microsoft/callback"
DISCONNECT = "/api/calendar/microsoft/disconnect"
COOKIE = "calendar_flow_ms"
NOW = datetime(2026, 11, 9, 12, 0, tzinfo=UTC)
SECRET_SENTINELS = (
    CLIENT_SECRET,
    "ERROR-DESCRIPTION-SENTINEL",
    "ACCESS-PLACEHOLDER",
    "REFRESH-PLACEHOLDER",
    "CODE-PLACEHOLDER",
)


@pytest.fixture
def settings():
    return make_settings(
        google_client_id="g-client",
        google_client_secret="g-secret",
        microsoft_client_id=CLIENT_ID,
        microsoft_client_secret=CLIENT_SECRET,
        microsoft_calendar_enabled=True,
        calendar_enabled=True,
        token_enc_key=KEY,
    )


@pytest.fixture(autouse=True)
def vault_key(monkeypatch, settings):
    monkeypatch.setattr(vault, "get_settings", lambda: settings)


@pytest.fixture
def ms(settings):
    return FakeMicrosoft(settings)


@pytest.fixture
def google(settings):
    return FakeGoogle(settings)


def build_app(engine, settings, ms, google, fake_vexa, fake_openai):
    app = create_app(settings)

    def session_override():
        with Session(engine, expire_on_commit=False) as s:
            yield s

    app.dependency_overrides[get_session] = session_override
    app.dependency_overrides[get_vexa] = lambda: fake_vexa
    app.dependency_overrides[get_openai_client] = lambda: fake_openai
    app.dependency_overrides[google_oauth.get_google] = lambda: google.oauth
    app.dependency_overrides[microsoft_oauth.get_microsoft] = lambda: ms.oauth
    return app


@pytest.fixture
def capp(engine, settings, ms, google, fake_vexa, fake_openai):
    return build_app(engine, settings, ms, google, fake_vexa, fake_openai)


@pytest.fixture
def web(capp, owner):
    return browser(capp)


def connect(web: Api, body=None):
    response = web.post(CONNECT, {} if body is None else body)
    assert response.status_code == 200, response.text
    url = response.json()["authorize_url"]
    return url, parse_qs(urlparse(url).query)["state"][0], response


def callback(web: Api, state: str, code: str, extra: str = ""):
    return web.client.get(f"{CALLBACK}?code={quote(code)}&state={quote(state)}{extra}")


def connect_outlook(web: Api, ms: FakeMicrosoft, *, return_to=None, **consent):
    body = {"return_to": return_to} if return_to else None
    url, state, _ = connect(web, body)
    code = ms.consent(url, **consent)
    return callback(web, state, code), state, code


def row(session, user=None, provider="microsoft") -> CalendarConnection | None:
    session.expire_all()
    query = select(CalendarConnection).where(CalendarConnection.provider == provider)
    if user is not None:
        query = query.where(CalendarConnection.user_id == user.id)
    return session.scalars(query).first()


def flows(session) -> list[OAuthFlow]:
    session.expire_all()
    return list(session.scalars(select(OAuthFlow)).all())


def app_log(caplog) -> str:
    """What the application logged (not the test client's own request lines)."""
    return "\n".join(
        r.getMessage() for r in caplog.records if r.name == "app" or r.name.startswith("app.")
    )


def error_code(response) -> str:
    path, query = landing(response)
    assert path == "/settings" and set(query) == {"calendar_error"}, (path, query)
    return query["calendar_error"]


# ---- starting ----


def test_the_connect_url_is_the_consumers_code_flow_with_pkce(web, session, owner):
    url, state, response = connect(web)
    parsed = urlparse(url)
    query = {k: v[0] for k, v in parse_qs(parsed.query).items()}
    assert f"{parsed.scheme}://{parsed.netloc}{parsed.path}" == (
        "https://login.microsoftonline.com/consumers/oauth2/v2.0/authorize"
    )
    assert query["client_id"] == CLIENT_ID and query["response_type"] == "code"
    assert query["redirect_uri"] == "https://notes.beyrictech.com/api/calendar/microsoft/callback"
    assert query["code_challenge_method"] == "S256" and query["prompt"] == "select_account"
    assert "login_hint" not in query and query["state"] == state and len(state) >= 40
    assert "https://graph.microsoft.com/Calendars.Read" in query["scope"].split()
    # a personal-account consent screen is not documented to say "unverified" (M0 S2): no promise
    assert response.json()["show_unverified_notice"] is False
    [flow] = flows(session)
    assert flow.provider == "microsoft" and flow.purpose == "calendar" and flow.user_id == owner.id
    assert flow.state_hash == tokens.digest(state) and state not in flow.state_hash
    digest = hashlib.sha256(flow.code_verifier.encode()).digest()
    assert query["code_challenge"] == base64.urlsafe_b64encode(digest).decode().rstrip("=")
    assert query["nonce"] == flow.nonce
    assert (
        timedelta(minutes=9)
        < flow.expires_at - datetime.now(UTC)
        <= timedelta(minutes=10, seconds=5)
    )


@pytest.mark.parametrize(
    "authority,verified,expected",
    [("consumers", False, False), ("common", False, True), ("common", True, False)],
)
def test_the_notice_flag_is_for_work_accounts_until_the_publisher_is_verified(
    engine, settings, ms, google, fake_vexa, fake_openai, owner, authority, verified, expected
):
    changed = settings.model_copy(
        update={"microsoft_authority": authority, "microsoft_publisher_verified": verified}
    )
    app = build_app(engine, changed, FakeMicrosoft(changed), google, fake_vexa, fake_openai)
    app.dependency_overrides[microsoft_oauth.get_microsoft] = lambda: FakeMicrosoft(changed).oauth
    assert connect(browser(app))[2].json()["show_unverified_notice"] is expected


def test_the_response_sets_the_microsoft_flow_cookie(web):
    _, state, response = connect(web)
    header = response.headers["set-cookie"]
    assert header.startswith(f"{COOKIE}={state}")
    assert (
        "HttpOnly" in header
        and "SameSite=lax" in header
        and "Path=/api/calendar/microsoft" in header
    )
    assert "Max-Age=600" in header


def test_microsoft_connects_the_calendar_only(web, session):
    error_of(web.post(CONNECT, {"features": ["meet"]}), 422, "invalid_features")
    error_of(web.post(CONNECT, {"features": ["calendar", "meet"]}), 422, "invalid_features")
    assert web.post(CONNECT, {"features": ["calendar"]}).status_code == 200
    assert web.post(CONNECT, {"features": ["teams"]}).status_code == 422


@pytest.mark.parametrize(
    "target",
    ["//evil.test", "https://evil.test/", "/\\evil.test", "javascript:alert(1)", "evil", "/a b"],
)
def test_return_to_must_start_with_one_slash(web, session, target):
    connect(web, {"return_to": target})
    [flow] = flows(session)
    assert flow.return_to == "/settings"


def test_connect_needs_a_login_and_the_csrf_token(capp, owner):
    anon = browser(capp, log_in=False)
    assert anon.post(CONNECT, {}).status_code in (401, 403)
    signed_in = browser(capp)
    assert signed_in.client.post(CONNECT, json={}).status_code == 403  # no CSRF header


@pytest.mark.parametrize(
    "changes",
    [
        {"microsoft_calendar_enabled": False},
        {"calendar_enabled": False},
        {"microsoft_client_id": ""},
        {"microsoft_client_secret": ""},
    ],
    ids=["flag off", "calendar features off", "no client id", "no client secret"],
)
def test_every_route_but_disconnect_answers_404_while_microsoft_is_not_switched_on(
    changes, engine, settings, google, fake_vexa, fake_openai, owner
):
    off = settings.model_copy(update=changes)
    ms_off = FakeMicrosoft(off)
    app = build_app(engine, off, ms_off, google, fake_vexa, fake_openai)
    if "microsoft_client_id" in changes or "microsoft_client_secret" in changes:
        app.dependency_overrides[microsoft_oauth.get_microsoft] = lambda: None
    web = browser(app)
    error_of(web.post(CONNECT, {}), 404, "calendar_unavailable")
    error_of(web.client.get(f"{CALLBACK}?code=x&state=y"), 404, "calendar_unavailable")
    assert web.post(DISCONNECT).status_code == 200  # deleting your data never depends on the flag
    assert ms_off.requests == []


def test_a_server_without_a_token_key_refuses_before_microsoft_is_involved(web, ms, monkeypatch):
    monkeypatch.setattr(vault, "get_settings", lambda: make_settings())
    error_of(web.post(CONNECT, {}), 503, "calendar_unavailable")
    assert ms.requests == []


# ---- the callback: a connect that works ----


def test_a_personal_account_connects(web, session, owner, ms):
    response, state, _ = connect_outlook(web, ms)
    assert landing(response) == ("/settings", {"calendar": "connected"})
    conn = row(session, owner)
    assert conn.status == "connected" and conn.provider == "microsoft"
    assert conn.account_email == "ada@outlook.example.test"  # from GET /me, a label
    assert conn.account_subject == "00000000-0000-0000-0000-000000000001"  # the oid, not the email
    assert conn.tenant_id == CONSUMER_TENANT and conn.account_kind == "personal"
    assert conn.tenant_domains is None and conn.account_domain is None
    assert set(conn.granted_scopes.split()) >= {"Calendars.Read", "User.Read"}
    assert calendar_connect.granted_features(conn) == {"calendar"}
    assert conn.last_sync_at is None and conn.sync_token is None and conn.expected_expiry_at is None
    assert conn.connected_at is not None
    assert flows(session) == []  # the state is used up
    assert (
        f"{COOKIE}=" in response.headers["set-cookie"]
        and "Max-Age=0" in response.headers["set-cookie"]
    )


def test_the_refresh_token_is_stored_only_under_the_microsoft_vault_purpose(
    web, session, owner, ms
):
    connect_outlook(web, ms)
    conn = row(session, owner)
    issued = "REFRESH-PLACEHOLDER-1"
    secret = vault.decrypt(
        conn.refresh_token_enc, conn.key_version, purpose="ms_refresh_token", owner=owner.id
    )
    assert secret.reveal() == issued
    assert issued.encode() not in bytes(conn.refresh_token_enc)
    with pytest.raises(vault.VaultError):  # a Google-purpose reader cannot open it
        vault.decrypt(
            conn.refresh_token_enc, conn.key_version, purpose="refresh_token", owner=owner.id
        )
    with pytest.raises(vault.VaultError):  # nor another user
        vault.decrypt(
            conn.refresh_token_enc, conn.key_version, purpose="ms_refresh_token", owner=owner.id + 1
        )


def test_the_state_endpoint_shows_the_microsoft_connection(web, session, owner, ms):
    connect_outlook(web, ms)
    state = web.get("/api/calendar").json()
    assert state["provider"] == "microsoft" and state["account_email"] == "ada@outlook.example.test"
    assert state["connected"] is True and state["state"] == "connected"


def test_the_redemption_and_the_probe_are_exactly_the_documented_requests(web, ms):
    connect_outlook(web, ms)
    [token_request] = ms.token_requests
    assert token_request["grant_type"] == "authorization_code"
    assert token_request["client_secret"] == CLIENT_SECRET  # in the body, never a URL
    assert ms.token_urls == ["https://login.microsoftonline.com/consumers/oauth2/v2.0/token"]
    probe, me = ms.graph_requests[:2]
    assert probe.method == "GET" and probe.url.host == "graph.microsoft.com"
    assert probe.url.path == "/v1.0/me/calendarView"
    query = {k: v[0] for k, v in parse_qs(probe.url.query.decode()).items()}
    start = datetime.strptime(query["startDateTime"], "%Y-%m-%dT%H:%M:%SZ")
    end = datetime.strptime(query["endDateTime"], "%Y-%m-%dT%H:%M:%SZ")
    assert end - start == timedelta(hours=1) and query["$top"] == "1"
    assert me.url.path == "/v1.0/me"
    for request in ms.graph_requests:
        assert request.headers["authorization"] == "Bearer ACCESS-PLACEHOLDER-1"
        assert "ACCESS-PLACEHOLDER" not in str(request.url)
    # only the two Microsoft hosts were ever contacted, and no organisation call by default
    assert {r.url.host for r in ms.requests} == {"login.microsoftonline.com", "graph.microsoft.com"}
    assert all(r.url.path != "/v1.0/organization" for r in ms.graph_requests)


@pytest.mark.parametrize(
    "consent,expected",
    [
        (
            {"mail": None, "user_principal_name": "Ada.Upn@Outlook.Example.Test"},
            "ada.upn@outlook.example.test",
        ),
        ({"mail": "Ada@Outlook.Example.Test"}, "ada@outlook.example.test"),
        (
            {
                "mail": "not an address",
                "user_principal_name": None,
                "preferred_username": "pu@example.test",
            },
            "pu@example.test",
        ),
        ({"mail": None, "preferred_username": "no-at-sign", "name": "Ada Example"}, "Ada Example"),
    ],
    ids=["upn when no mail", "mail lower-cased", "preferred_username fallback", "name fallback"],
)
def test_the_label_is_mail_then_upn_then_the_id_token_labels(
    web, session, owner, ms, consent, expected
):
    connect_outlook(web, ms, **consent)
    assert row(session, owner).account_email == expected


@pytest.mark.parametrize(
    "answer",
    [
        httpx.Response(500),
        httpx.Response(401),
        httpx.Response(200, text="x"),
        httpx.ConnectTimeout("t"),
    ],
    ids=["500", "401", "not json", "timeout"],
)
def test_a_failing_me_call_only_loses_the_nice_label(web, session, owner, ms, answer):
    ms.me = answer
    response, _, _ = connect_outlook(web, ms)
    assert landing(response)[1] == {"calendar": "connected"}
    assert row(session, owner).account_email == "ada@outlook.example.test"  # the ID token's label


def test_the_account_label_never_decides_identity(web, session, owner, ms):
    connect_outlook(web, ms, mail="someone.else@example.test")
    conn = row(session, owner)
    assert conn.account_email == "someone.else@example.test"
    assert conn.account_subject == "00000000-0000-0000-0000-000000000001"


def test_an_oid_less_token_falls_back_to_sub(web, session, owner, ms):
    connect_outlook(web, ms, oid=None, sub="SUB-OF-THE-ACCOUNT")
    assert row(session, owner).account_subject == "SUB-OF-THE-ACCOUNT"


# ---- work accounts and the organisation's domains ----


@pytest.fixture
def work_settings(settings):
    return settings.model_copy(update={"microsoft_authority": "common"})


def work_app(engine, work_settings, google, fake_vexa, fake_openai):
    work_ms = FakeMicrosoft(work_settings)
    return build_app(engine, work_settings, work_ms, google, fake_vexa, fake_openai), work_ms


def test_a_work_account_connects_but_its_domains_are_not_read_by_default(
    engine, work_settings, google, fake_vexa, fake_openai, session, owner
):
    app, work_ms = work_app(engine, work_settings, google, fake_vexa, fake_openai)
    response, _, _ = connect_outlook(browser(app), work_ms, tid=WORK_TENANT, scope_style="full")
    assert landing(response)[1] == {"calendar": "connected"}
    conn = row(session, owner)
    assert conn.tenant_id == WORK_TENANT and conn.account_kind == "work"
    assert conn.tenant_domains is None  # spec 8.1 #8: the organiser-domain trust is opt-in
    assert all(r.url.path != "/v1.0/organization" for r in work_ms.graph_requests)
    assert work_ms.token_urls == ["https://login.microsoftonline.com/common/oauth2/v2.0/token"]


def test_with_the_trust_switch_on_a_work_connection_stores_the_verified_domains(
    engine, work_settings, google, fake_vexa, fake_openai, session, owner
):
    on = work_settings.model_copy(update={"microsoft_organizer_domain_trust": True})
    app, work_ms = work_app(engine, on, google, fake_vexa, fake_openai)
    connect_outlook(browser(app), work_ms, tid=WORK_TENANT)
    assert row(session, owner).tenant_domains == "company.onmicrosoft.com company.test"


@pytest.mark.parametrize(
    "answer",
    [
        httpx.Response(403),
        httpx.Response(200, json={"value": []}),
        httpx.Response(200, json={"value": [{"verifiedDomains": "x"}]}),
        httpx.Response(
            200, json={"value": [{"verifiedDomains": [{"name": "bad domain!"}, {"name": 5}, "x"]}]}
        ),
        httpx.Response(200, text="nope"),
        httpx.ReadTimeout("t"),
    ],
)
def test_domains_that_cannot_be_read_leave_the_field_empty_and_the_connect_working(
    engine, work_settings, google, fake_vexa, fake_openai, session, owner, answer
):
    on = work_settings.model_copy(update={"microsoft_organizer_domain_trust": True})
    app, work_ms = work_app(engine, on, google, fake_vexa, fake_openai)
    work_ms.organization = answer
    response, _, _ = connect_outlook(browser(app), work_ms, tid=WORK_TENANT)
    assert landing(response)[1] == {"calendar": "connected"}
    assert row(session, owner).tenant_domains is None


def test_a_personal_account_never_reads_the_organisation(
    engine, settings, google, fake_vexa, fake_openai, session, owner, ms
):
    on = settings.model_copy(update={"microsoft_organizer_domain_trust": True})
    app = build_app(engine, on, ms, google, fake_vexa, fake_openai)
    connect_outlook(browser(app), ms)
    assert all(r.url.path != "/v1.0/organization" for r in ms.graph_requests)


def test_a_tenant_outside_the_allow_list_is_refused_and_nothing_is_stored(
    engine, work_settings, google, fake_vexa, fake_openai, session, owner
):
    limited = work_settings.model_copy(update={"microsoft_allowed_tenants": OTHER_TENANT})
    app, work_ms = work_app(engine, limited, google, fake_vexa, fake_openai)
    response, _, _ = connect_outlook(browser(app), work_ms, tid=WORK_TENANT)
    assert error_code(response) == "tenant_not_allowed"
    assert row(session, owner) is None and work_ms.graph_requests == []


def test_a_work_tenant_at_the_consumers_endpoint_is_refused(web, session, owner, ms):
    response, _, _ = connect_outlook(web, ms, tid=WORK_TENANT)
    assert error_code(response) == "tenant_not_allowed"
    assert row(session, owner) is None


# ---- the callback checks ----


def test_an_unknown_state_is_refused(web, session, owner, ms):
    assert error_code(callback(web, "no-such-state", "CODE")) == "microsoft_failed"
    assert row(session, owner) is None and ms.requests == []


def test_a_replayed_state_is_refused_and_nothing_changes(web, session, owner, ms):
    url, state, _ = connect(web)
    code = ms.consent(url)
    assert landing(callback(web, state, code))[1] == {"calendar": "connected"}
    before = row(session, owner).refresh_token_enc
    web.client.cookies.set(COOKIE, state, path="/api/calendar/microsoft")
    assert error_code(callback(web, state, code)) == "microsoft_failed"
    assert row(session, owner).refresh_token_enc == before


def test_an_expired_state_is_refused_and_used_up(web, session, owner, ms):
    url, state, _ = connect(web)
    flow = flows(session)[0]
    flow.expires_at = datetime.now(UTC) - timedelta(seconds=1)
    session.commit()
    assert error_code(callback(web, state, ms.consent(url))) == "microsoft_failed"
    assert flows(session) == [] and row(session, owner) is None
    assert ms.token_requests == []  # the code was never redeemed


def test_the_state_is_checked_for_length_and_presence(web, ms):
    assert error_code(web.client.get(f"{CALLBACK}?code=x")) == "microsoft_failed"
    assert error_code(callback(web, "s" * 300, "x")) == "microsoft_failed"


def test_a_google_state_never_finishes_at_the_microsoft_callback(web, session, owner, ms, google):
    g_url, g_state, _ = google_connect(web)
    web.client.cookies.set(COOKIE, g_state, path="/api/calendar/microsoft")
    assert error_code(callback(web, g_state, google.consent(g_url))) == "microsoft_failed"
    assert flows(session) == [] and row(session, owner) is None and ms.token_requests == []


def test_a_microsoft_state_never_finishes_at_the_google_callback(web, session, owner, ms, google):
    url, state, _ = connect(web)
    web.client.cookies.set("calendar_flow", state, path="/api/calendar/google")
    response = web.client.get(f"/api/calendar/google/callback?code=x&state={quote(state)}")
    path, query = landing(response)
    assert query == {"calendar_error": "google_failed"}
    assert flows(session) == [] and row(session, owner, "google") is None


def google_connect(web: Api):
    response = web.post("/api/calendar/google/connect", {})
    assert response.status_code == 200, response.text
    url = response.json()["authorize_url"]
    return url, parse_qs(urlparse(url).query)["state"][0], response


def test_a_signed_out_visitor_goes_to_login_and_the_state_is_used_up(capp, web, session, owner, ms):
    url, state, _ = connect(web)
    code = ms.consent(url)
    anon = browser(capp, log_in=False)
    anon.client.cookies.set(COOKIE, state, path="/api/calendar/microsoft")
    response = callback(anon, state, code)
    assert landing(response) == ("/login", {"error": "microsoft_failed"})
    assert flows(session) == [] and row(session, owner) is None and ms.token_requests == []


def test_another_signed_in_user_gets_wrong_account_and_nothing_is_stored(
    capp, web, session, owner, stranger, ms
):
    """Login CSRF: a victim lured to the callback with an attacker's code."""
    url, state, _ = connect(web)
    code = ms.consent(url)
    victim = browser(capp, OTHER_EMAIL)
    victim.client.cookies.set(COOKIE, state, path="/api/calendar/microsoft")
    assert error_code(callback(victim, state, code)) == "wrong_account"
    assert row(session) is None and ms.token_requests == []


def test_the_flow_cookie_of_the_browser_that_started_it_is_required(capp, web, session, owner, ms):
    url, state, _ = connect(web)
    code = ms.consent(url)
    same_user_elsewhere = browser(capp)  # signed in as the owner but without the cookie
    assert error_code(callback(same_user_elsewhere, state, code)) == "microsoft_failed"
    assert row(session, owner) is None and ms.token_requests == []


def test_a_google_flow_cookie_does_not_stand_in_for_the_microsoft_one(
    capp, web, session, owner, ms
):
    url, state, _ = connect(web)
    code = ms.consent(url)
    elsewhere = browser(capp)
    elsewhere.client.cookies.set(
        "calendar_flow", state, domain="testserver", path="/api/calendar/microsoft"
    )
    assert error_code(callback(elsewhere, state, code)) == "microsoft_failed"
    assert row(session, owner) is None and ms.token_requests == []


@pytest.mark.parametrize(
    "extra,expected",
    [
        ("&error=access_denied&error_description=ERROR-DESCRIPTION-SENTINEL", "microsoft_denied"),
        ("&error=consent_required", "microsoft_failed"),
        ("&error=server_error&error_subcode=x", "microsoft_failed"),
    ],
)
def test_an_error_from_microsoft_stores_nothing(web, session, owner, ms, extra, expected, caplog):
    caplog.set_level(logging.DEBUG)
    url, state, _ = connect(web)
    assert error_code(callback(web, state, "unused", extra)) == expected
    assert row(session, owner) is None and ms.token_requests == []
    assert "ERROR-DESCRIPTION-SENTINEL" not in app_log(caplog)


def test_a_callback_with_no_code_stores_nothing(web, session, owner, ms):
    url, state, _ = connect(web)
    assert error_code(web.client.get(f"{CALLBACK}?state={quote(state)}")) == "microsoft_failed"
    assert row(session, owner) is None


def test_a_wrong_nonce_or_audience_in_the_id_token_stores_nothing(web, session, owner, ms):
    assert error_code(connect_outlook(web, ms, nonce="another")[0]) == "microsoft_failed"
    assert (
        error_code(connect_outlook(web, ms, claims={"aud": "other-app"})[0]) == "microsoft_failed"
    )
    assert (
        error_code(
            connect_outlook(
                web, ms, claims={"iss": f"https://login.microsoftonline.com/{OTHER_TENANT}/v2.0"}
            )[0]
        )
        == "microsoft_failed"
    )
    assert row(session, owner) is None and ms.graph_requests == []


def test_a_failing_token_endpoint_stores_nothing(web, session, owner, ms):
    ms.fail_token_endpoint = True
    assert error_code(connect_outlook(web, ms)[0]) == "microsoft_failed"
    assert row(session, owner) is None and ms.graph_requests == []


def test_a_code_works_once_at_microsoft(web, session, owner, ms):
    url, state, _ = connect(web)
    code = ms.consent(url)
    ms.codes.pop(code)  # Microsoft already spent it
    assert error_code(callback(web, state, code)) == "microsoft_failed"
    assert row(session, owner) is None


@pytest.mark.parametrize(
    "grant",
    [
        {"openid", "profile", "offline_access", "https://graph.microsoft.com/User.Read"},
        {"openid", "profile", "offline_access", "https://graph.microsoft.com/Calendars.ReadBasic"},
        {
            "openid",
            "profile",
            "https://graph.microsoft.com/User.Read",
            "https://graph.microsoft.com/Calendars.Read",
        },
    ],
    ids=["no calendar scope", "ReadBasic only", "no offline_access, so no refresh token"],
)
def test_a_grant_without_the_calendar_scope_or_a_refresh_token_stores_nothing(
    web, session, owner, ms, grant
):
    response, _, _ = connect_outlook(web, ms, grant=grant)
    assert error_code(response) == "scope_missing"
    assert row(session, owner) is None and ms.graph_requests == []


def test_a_reply_without_a_refresh_token_stores_nothing(web, session, owner, ms):
    assert error_code(connect_outlook(web, ms, issue_refresh_token=False)[0]) == "scope_missing"
    assert row(session, owner) is None


# ---- the probe ----


@pytest.mark.parametrize(
    "answer,expected",
    [
        (httpx.Response(404, json={"error": {"code": "ResourceNotFound"}}), "no_mailbox"),
        (httpx.Response(404), "no_mailbox"),
        (
            httpx.Response(400, json={"error": {"code": "MailboxNotEnabledForRESTAPI"}}),
            "no_mailbox",
        ),
        (
            httpx.Response(401, json={"error": {"code": "InvalidAuthenticationToken"}}),
            "scope_missing",
        ),
        (httpx.Response(403, json={"error": {"code": "ErrorAccessDenied"}}), "scope_missing"),
        (httpx.Response(429), "microsoft_failed"),
        (httpx.Response(500), "microsoft_failed"),
        (httpx.Response(400, json={"error": {"code": "SomethingElse"}}), "microsoft_failed"),
        (httpx.Response(400, text="<html>"), "microsoft_failed"),
        (httpx.ConnectTimeout("t"), "microsoft_failed"),
    ],
    ids=[
        "404 json",
        "404",
        "mailbox code",
        "401",
        "403",
        "429",
        "500",
        "other 400",
        "html",
        "timeout",
    ],
)
def test_a_probe_that_cannot_read_a_calendar_stores_nothing(
    web, session, owner, ms, answer, expected
):
    ms.probe = answer
    response, _, _ = connect_outlook(web, ms)
    assert error_code(response) == expected
    assert row(session, owner) is None
    assert flows(session) == []


# ---- one calendar at a time ----


def connect_google_for(web, google, **consent):
    url, state, _ = google_connect(web)
    return cc.callback(web, state, google.consent(url, **consent))


def test_connecting_outlook_while_google_is_connected_is_refused_with_409(
    web, session, owner, google, ms
):
    assert landing(connect_google_for(web, google))[1] == {"calendar": "connected"}
    error_of(web.post(CONNECT, {}), 409, "calendar_already_connected")
    assert flows(session) == []  # nothing was started


@pytest.mark.parametrize("status", ["connected", "degraded", "needs_reconnect"])
def test_a_google_calendar_in_any_live_state_blocks_it(web, session, owner, google, status):
    connect_google_for(web, google)
    conn = row(session, owner, "google")
    conn.status = status
    session.commit()
    error_of(web.post(CONNECT, {}), 409, "calendar_already_connected")


def test_a_disconnected_or_meet_only_google_row_does_not_block_it(web, session, owner, google, ms):
    connect_google_for(web, google)
    web.post("/api/calendar/google/disconnect")
    assert web.post(CONNECT, {}).status_code == 200
    conn = row(session, owner, "google")
    conn.status, conn.granted_scopes = "connected", google_oauth.MEET_SCOPE
    session.commit()
    assert web.post(CONNECT, {}).status_code == 200


def test_google_cannot_start_while_outlook_is_connected(web, session, owner, ms):
    connect_outlook(web, ms)
    error_of(web.post("/api/calendar/google/connect", {}), 409, "calendar_already_connected")


def test_a_google_connection_that_wins_the_race_makes_the_callback_say_already_connected(
    web, session, owner, google, ms
):
    url, state, _ = connect(web)
    code = ms.consent(url)
    # Google finishes first (the second tab), then the Outlook callback arrives.
    g_url, g_state, _ = google_connect(web)
    web.client.cookies.set(COOKIE, state, path="/api/calendar/microsoft")
    assert landing(cc.callback(web, g_state, google.consent(g_url)))[1] == {"calendar": "connected"}
    web.client.cookies.set(COOKIE, state, path="/api/calendar/microsoft")
    assert error_code(callback(web, state, code)) == "already_connected"
    assert row(session, owner) is None  # no Microsoft row, nothing of it stored


def test_the_one_calendar_check_runs_under_the_users_row_lock(web, session, owner, ms, engine):
    """Two tabs finishing two providers at once queue on the user's own row: the second sees the
    first's committed connection. Session A holds the lock, B (the Outlook store) must wait, A
    commits a Google connection, and B then refuses."""
    url, state, _ = connect(web)
    code = ms.consent(url)
    web.client.cookies.set(COOKIE, state, path="/api/calendar/microsoft")
    holder = Session(engine)
    holder.execute(select(User.id).where(User.id == owner.id).with_for_update())
    result = {}

    def finish():
        result["response"] = callback(web, state, code)

    thread = threading.Thread(target=finish)
    thread.start()
    thread.join(timeout=1.5)
    assert thread.is_alive()  # blocked on the row lock, nothing decided yet
    holder.add(
        CalendarConnection(
            user_id=owner.id,
            provider="google",
            status="connected",
            granted_scopes=google_oauth.CALENDAR_SCOPE,
        )
    )
    holder.commit()
    holder.close()
    thread.join(timeout=10)
    assert not thread.is_alive()
    assert error_code(result["response"]) == "already_connected"
    assert row(session, owner) is None


# ---- reconnecting ----


def test_reconnecting_the_same_account_updates_the_one_row(web, session, owner, ms):
    connect_outlook(web, ms)
    conn = row(session, owner)
    first_id, first_blob = conn.id, conn.refresh_token_enc
    conn.status, conn.needs_reconnect_reason = "needs_reconnect", "revoked"
    conn.last_error, conn.last_error_at, conn.sync_token, conn.last_sync_at = "x", NOW, "t", NOW
    session.commit()
    response, _, _ = connect_outlook(web, ms)
    assert landing(response)[1] == {"calendar": "connected"}
    session.expire_all()
    [only] = session.scalars(select(CalendarConnection)).all()
    assert (
        only.id == first_id and only.status == "connected" and only.needs_reconnect_reason is None
    )
    assert only.last_error is None and only.sync_token is None and only.last_sync_at is None
    assert only.refresh_token_enc != first_blob


def test_a_different_identity_over_a_working_connection_is_refused(web, session, owner, ms):
    connect_outlook(web, ms)
    before = row(session, owner).refresh_token_enc
    response, _, _ = connect_outlook(web, ms, oid="00000000-0000-0000-0000-0000000000cc")
    assert error_code(response) == "different_account"
    conn = row(session, owner)
    assert conn.account_subject == "00000000-0000-0000-0000-000000000001"
    assert conn.refresh_token_enc == before


def test_the_same_email_with_another_object_id_is_another_identity(web, session, owner, ms):
    """Identity is tenant plus object id, never the address."""
    connect_outlook(web, ms, preferred_username="same@example.test", mail="same@example.test")
    response, _, _ = connect_outlook(
        web,
        ms,
        oid="00000000-0000-0000-0000-0000000000dd",
        preferred_username="same@example.test",
        mail="same@example.test",
    )
    assert error_code(response) == "different_account"


def test_a_different_identity_over_a_dead_connection_drops_the_old_events_and_unstarted_joins(
    web, session, owner, ms, monkeypatch
):
    connect_outlook(web, ms)
    conn = row(session, owner)
    _, scheduled = add_join(session, conn, owner, "scheduled", 1)
    _, live = add_join(session, conn, owner, "joined", 2)
    scheduled_id, live_id = scheduled.id, live.id
    kill(session, owner)
    seen = []
    monkeypatch.setattr(
        calendar_connect, "ON_DISCONNECT", [lambda s, r, g: seen.append((r.user_id, g))]
    )
    requests_before = len(ms.requests)
    response, _, _ = connect_outlook(web, ms, oid="00000000-0000-0000-0000-0000000000ee")
    assert landing(response)[1] == {"calendar": "connected"}
    session.expire_all()
    assert row(session, owner).account_subject == "00000000-0000-0000-0000-0000000000ee"
    assert session.get(ScheduledJoin, scheduled_id) is None  # not started: gone with its event
    assert session.get(ScheduledJoin, live_id) is not None  # a join already live keeps going
    assert seen == [(owner.id, None)]  # hooks ran, and there was nothing to revoke at Microsoft
    assert all(r.url.host != "oauth2.googleapis.com" for r in ms.requests[requests_before:])


# ---- disconnecting ----


def test_disconnect_deletes_what_c4_deletes_and_keeps_what_c4_keeps(
    web, session, owner, ms, template
):
    connect_outlook(web, ms)
    conn = row(session, owner)
    for n, state in enumerate(JOIN_STATES):
        add_join(session, conn, owner, state, n)
    conn.sync_token, conn.last_sync_at, conn.last_error = "t", NOW, "boom"
    session.commit()
    add_notification(session, owner, "calendar.disconnected", "cal_disc:1")
    add_notification(session, owner, "usage.hours_low", "usage:1")
    settings_row = session.get(UserSettings, owner.id) or UserSettings(user_id=owner.id)
    settings_row.auto_join_rule = "organizer"
    session.add(settings_row)
    session.add(UsageEntry(user_id=owner.id, email_hash="h" * 64, meeting_id=None, seconds=60))
    session.commit()
    requests_before = len(ms.requests)

    response = web.post(DISCONNECT)

    assert response.status_code == 200, response.text
    assert response.json() == {
        "status": "disconnected",
        "revoked": False,
        "reason": "provider_has_no_revoke",
    }
    assert len(ms.requests) == requests_before  # nothing was sent to Microsoft
    assert count(session, CalendarEvent) == 0 and count(session, ScheduledJoin) == 0
    assert sorted(n.kind for n in session.scalars(select(Notification)).all()) == [
        "usage.hours_low"
    ]
    after = row(session, owner)
    assert (
        after.status == "disconnected"
        and after.refresh_token_enc is None
        and after.key_version is None
    )
    assert after.granted_scopes is None and calendar_connect.granted_features(after) == set()
    for column in (
        "account_email",
        "account_subject",
        "account_domain",
        "tenant_id",
        "account_kind",
        "tenant_domains",
        "sync_token",
        "last_sync_at",
        "last_error",
        "last_error_at",
    ):
        assert getattr(after, column) is None, column
    session.expire_all()
    assert session.get(UserSettings, owner.id).auto_join_rule == "organizer"
    assert [u.seconds for u in session.scalars(select(UsageEntry)).all()] == [60]


def test_disconnect_keeps_meetings(web, session, owner, ms, template):
    from tests.test_web import ZOOM, add_meeting

    connect_outlook(web, ms)
    _, join = add_join(session, row(session, owner), owner, "joined", 1)
    meeting = add_meeting(
        session, template, title="Standup", url=ZOOM, source="calendar", status="ready"
    )
    join.meeting_id = meeting.id
    session.commit()
    assert web.post(DISCONNECT).status_code == 200
    session.expire_all()
    assert session.get(Meeting, meeting.id).title == "Standup"


def test_disconnect_is_idempotent_and_the_repeat_has_nothing_to_report(web, session, owner, ms):
    connect_outlook(web, ms)
    first = web.post(DISCONNECT)
    assert first.json()["reason"] == "provider_has_no_revoke"
    again = web.post(DISCONNECT)
    assert again.status_code == 200 and again.json() == {"status": "disconnected", "revoked": True}
    assert row(session, owner).status == "disconnected"


def test_disconnect_with_nothing_connected_creates_nothing(web, session, owner):
    assert web.post(DISCONNECT).json() == {"status": "disconnected", "revoked": True}
    assert row(session, owner) is None


def test_disconnect_runs_the_hooks_after_the_commit_with_no_token_revoked(
    web, session, owner, ms, monkeypatch, engine
):
    connect_outlook(web, ms)
    conn = row(session, owner)
    seen = []

    def hook(hook_session, released, hook_google):
        seen.append((released.id, released.token.reveal(), hook_google, cc.locked_or_stale(engine)))

    monkeypatch.setattr(calendar_connect, "ON_DISCONNECT", [hook])
    web.post(DISCONNECT)
    assert seen == [(conn.id, "REFRESH-PLACEHOLDER-1", None, None)]


def test_disconnecting_never_touches_another_users_rows(capp, web, session, owner, stranger, ms):
    connect_outlook(web, ms)
    other = row(session, owner)
    theirs = CalendarConnection(
        user_id=stranger.id,
        provider="microsoft",
        status="connected",
        granted_scopes="Calendars.Read",
        account_email="them@example.test",
        account_subject="their-oid",
        tenant_id=CONSUMER_TENANT,
        account_kind="personal",
    )
    session.add(theirs)
    session.commit()
    add_join(session, theirs, stranger, "scheduled", 9)
    web.post(DISCONNECT)
    session.expire_all()
    assert session.get(CalendarConnection, theirs.id).status == "connected"
    assert count(session, ScheduledJoin, user_id=stranger.id) == 1
    assert other is not None


def test_connect_works_again_after_a_disconnect_for_another_identity(web, session, owner, ms):
    connect_outlook(web, ms)
    web.post(DISCONNECT)
    response, _, _ = connect_outlook(web, ms, oid="00000000-0000-0000-0000-0000000000ff")
    assert landing(response)[1] == {"calendar": "connected"}
    assert row(session, owner).account_subject == "00000000-0000-0000-0000-0000000000ff"


# ---- the registry row ----


def test_the_microsoft_row_reads_scopes_exactly_and_parses_with_domains_for_work_only(settings):
    row_ = calendar_providers.PROVIDERS["microsoft"]
    full = CalendarConnection(
        provider="microsoft", granted_scopes="https://graph.microsoft.com/Calendars.Read"
    )
    basic = CalendarConnection(provider="microsoft", granted_scopes="Calendars.ReadBasic User.Read")
    assert row_.granted_features(full) == {"calendar"} and row_.granted_features(basic) == set()
    assert (
        calendar_providers.features_of(
            CalendarConnection(provider="microsoft", granted_scopes=None)
        )
        == set()
    )
    assert row_.enabled(settings) is True
    assert row_.enabled(settings.model_copy(update={"microsoft_calendar_enabled": False})) is False
    assert row_.make_source(settings).provider == "microsoft"  # M4's Graph source
    assert [
        s.provider
        for s in calendar_providers.build_sources(
            settings.model_copy(update={"microsoft_calendar_enabled": False})
        )
    ] == ["google"]


# ---- nothing secret in a log ----


def test_no_code_token_secret_or_description_reaches_a_log_in_any_outcome(
    web, session, owner, ms, caplog
):
    caplog.set_level(logging.DEBUG)
    connect_outlook(web, ms)  # success
    web.post(DISCONNECT)
    connect_outlook(web, ms, nonce="wrong")  # id token refused
    ms.fail_token_endpoint = True
    connect_outlook(web, ms)  # token endpoint down
    ms.fail_token_endpoint = False
    ms.probe = httpx.Response(404)
    connect_outlook(web, ms)  # no mailbox
    ms.probe = httpx.Response(200, json={"value": []})
    url, state, _ = connect(web)
    callback(
        web, state, "unused", "&error=access_denied&error_description=ERROR-DESCRIPTION-SENTINEL"
    )
    logged = app_log(caplog)
    for sentinel in SECRET_SENTINELS:
        assert sentinel not in logged, sentinel
    assert "microsoft" in logged  # the useful, non-secret lines are there


# ---- our own client secret at the callback ----


def test_an_expired_client_secret_at_the_callback_alerts_the_admins_not_the_person(
    web, session, owner, ms, monkeypatch
):
    seen = []
    monkeypatch.setattr(microsoft_alerts, "secret_expired", lambda now: seen.append(now))
    ms.token_error = httpx.Response(
        401,
        json={"error": "invalid_client", "error_codes": [7000222], "error_description": "X"},
    )
    response, _, _ = connect_outlook(web, ms)
    assert error_code(response) == "microsoft_failed"  # the person is told nothing of our secret
    assert len(seen) == 1
    assert row(session, owner) is None


def test_a_wrong_client_secret_at_the_callback_does_not_claim_an_expiry(
    web, session, owner, ms, monkeypatch
):
    seen = []
    monkeypatch.setattr(microsoft_alerts, "secret_expired", lambda now: seen.append(now))
    ms.token_error = httpx.Response(401, json={"error": "invalid_client", "error_codes": [7000215]})
    assert error_code(connect_outlook(web, ms)[0]) == "microsoft_failed"
    assert seen == []


# ---- review fixes (Opus review of #152) ----


def seed_calendar(session, user, conn, n: int, *, state="scheduled"):
    """One event and its join for `conn`, as the sync would leave them."""
    return add_join(session, conn, user, state, n)


def test_a_microsoft_disconnect_with_only_google_connected_deletes_nothing_of_googles(
    web, session, owner, google
):
    """The route works with the flag off, so it can be POSTed while only Google is connected."""
    connect_google_for(web, google)
    g = row(session, owner, "google")
    g.sync_token = "google-sync-token"
    session.commit()
    seed_calendar(session, owner, g, 1)
    add_notification(session, owner, "calendar.not_recorded", "cal-nr:1")
    response = web.post(DISCONNECT)
    assert response.status_code == 200 and response.json() == {
        "status": "disconnected",
        "revoked": True,
    }
    assert count(session, CalendarEvent) == 1 and count(session, ScheduledJoin) == 1
    assert count(session, Notification, kind="calendar.not_recorded") == 1
    after = row(session, owner, "google")
    assert after.status == "connected" and after.sync_token == "google-sync-token"


def test_disconnecting_a_meet_only_google_never_touches_the_outlook_calendar(
    web, session, owner, google, ms
):
    connect_outlook(web, ms)
    outlook_conn = row(session, owner)
    seed_calendar(session, owner, outlook_conn, 1)
    outlook_conn.sync_token = "outlook-marker"
    session.commit()
    # Google connected for Meet only is allowed beside a live Outlook calendar
    g_url, g_state, _ = google_connect_features(web, ["meet"])
    assert landing(cc.callback(web, g_state, google.consent(g_url)))[1] == {"calendar": "connected"}
    assert web.post("/api/calendar/google/disconnect").status_code == 200
    assert count(session, CalendarEvent) == 1 and count(session, ScheduledJoin) == 1
    after = row(session, owner)
    assert after.status == "connected" and after.sync_token == "outlook-marker"
    assert row(session, owner, "google").status == "disconnected"


def google_connect_features(web: Api, features):
    response = web.post("/api/calendar/google/connect", {"features": features})
    assert response.status_code == 200, response.text
    url = response.json()["authorize_url"]
    return url, parse_qs(urlparse(url).query)["state"][0], response


def test_replacing_a_dead_connection_only_drops_that_connections_unstarted_joins(
    web, session, owner, google, ms
):
    from app.models import CalendarConnection

    google_conn = CalendarConnection(
        user_id=owner.id,
        provider="google",
        status="connected",
        granted_scopes=google_oauth.CALENDAR_SCOPE,
        account_subject="g-1",
    )
    dead = CalendarConnection(
        user_id=owner.id,
        provider="microsoft",
        status="needs_reconnect",
        granted_scopes="Calendars.Read",
        account_subject="oid-old",
        tenant_id=CONSUMER_TENANT,
        account_kind="personal",
    )
    session.add_all([google_conn, dead])
    session.commit()
    _, google_join = seed_calendar(session, owner, google_conn, 1)
    _, dead_join = seed_calendar(session, owner, dead, 2)
    google_join_id, dead_join_id = google_join.id, dead_join.id
    calendar_connect._drop_old_account(session, dead)
    session.commit()
    session.expire_all()
    assert session.get(ScheduledJoin, google_join_id) is not None
    assert session.get(ScheduledJoin, dead_join_id) is None
    assert count(session, CalendarEvent, connection_id=google_conn.id) == 1
    assert count(session, CalendarEvent, connection_id=dead.id) == 0


def test_the_same_object_id_under_a_different_tenant_is_another_identity(
    engine, work_settings, google, fake_vexa, fake_openai, session, owner
):
    """Identity is tenant plus object id: `oid` alone is not enough."""
    app, work_ms = work_app(engine, work_settings, google, fake_vexa, fake_openai)
    web_ = browser(app)
    connect_outlook(web_, work_ms, oid=OID_SAME)  # personal tenant
    response, _, _ = connect_outlook(web_, work_ms, oid=OID_SAME, tid=WORK_TENANT)
    assert error_code(response) == "different_account"
    conn = row(session, owner)
    assert conn.tenant_id == CONSUMER_TENANT and conn.account_subject == OID_SAME


OID_SAME = "00000000-0000-0000-0000-0000000000a1"


def test_a_switched_on_provider_with_no_source_yet_never_takes_the_scheduler_down(
    settings, monkeypatch, caplog
):
    row_ = calendar_providers.PROVIDERS["microsoft"]

    def not_built(_settings):
        raise NotImplementedError("later")

    monkeypatch.setattr(
        calendar_providers,
        "PROVIDERS",
        {
            **calendar_providers.PROVIDERS,
            "microsoft": row_.__class__(**{**row_.__dict__, "make_source": not_built}),
        },
    )
    sources = calendar_providers.build_sources(settings)
    assert [s.provider for s in sources] == ["google"]
    assert "microsoft is switched on but has no source yet" in caplog.text


def test_a_commit_collision_names_the_callers_own_error():
    from sqlalchemy.exc import IntegrityError

    class Colliding:
        def commit(self):
            raise IntegrityError("x", {}, Exception("y"))

    assert calendar_connect._commit(Colliding()) == "google_failed"
    assert calendar_connect._commit(Colliding(), "microsoft_failed") == "microsoft_failed"
