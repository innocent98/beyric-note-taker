"""Connecting Google Calendar: incremental consent, PKCE, the single-use state, and the refresh
token stored only through the vault (spec 7.2, 12.1; plan task C3).

Google's side is `tests/fakes/google.py`: the real `GoogleOAuth` client over a fake transport, so
the consent URL, the PKCE check and the token parsing are all production code."""

import base64
import hashlib
import logging
from datetime import UTC, datetime, timedelta
from urllib.parse import parse_qs, quote, urlparse

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app import auth, calendar_connect, google_oauth, tokens, vault
from app.db import get_session
from app.main import create_app
from app.models import (
    JOIN_STATES,
    CalendarConnection,
    CalendarEvent,
    Meeting,
    Notification,
    OAuthFlow,
    ScheduledJoin,
    Template,
    UsageEntry,
    User,
    UserSettings,
)
from app.services import get_openai_client, get_vexa
from tests.fakes.google import REVOKE_URL, FakeGoogle
from tests.test_api import Api, error_of
from tests.test_web import (  # noqa: F401
    OTHER_EMAIL,
    OWNER_EMAIL,
    ZOOM,
    add_meeting,
    fake_openai,
    fake_vexa,
    make_settings,
    make_user,
    owner,
    stranger,
    template,
)

CLIENT_ID = "test-client-id.apps.example.test"
CALENDAR_SCOPE = "https://www.googleapis.com/auth/calendar.events.readonly"
MEET_SCOPE = "https://www.googleapis.com/auth/meetings.space.readonly"
KEY = base64.b64encode(b"K" * 32).decode()
CONNECT = "/api/calendar/google/connect"
CALLBACK = "/api/calendar/google/callback"
DISCONNECT = "/api/calendar/google/disconnect"
DEFAULT_LANDING = "/settings/calendar"
NOW = datetime(2026, 11, 3, 12, 0, tzinfo=UTC)


@pytest.fixture
def settings():
    return make_settings(
        google_client_id=CLIENT_ID,
        google_client_secret="s3cret",
        calendar_enabled=True,
        token_enc_key=KEY,
    )


@pytest.fixture(autouse=True)
def vault_key(monkeypatch, settings):
    monkeypatch.setattr(vault, "get_settings", lambda: settings)


@pytest.fixture
def google(settings):
    return FakeGoogle(settings)


def build_app(engine, settings, google, fake_vexa, fake_openai):
    app = create_app(settings)

    def session_override():
        with Session(engine, expire_on_commit=False) as s:
            yield s

    app.dependency_overrides[get_session] = session_override
    app.dependency_overrides[get_vexa] = lambda: fake_vexa
    app.dependency_overrides[get_openai_client] = lambda: fake_openai
    app.dependency_overrides[google_oauth.get_google] = lambda: google.oauth
    return app


@pytest.fixture
def capp(engine, settings, google, fake_vexa, fake_openai):
    return build_app(engine, settings, google, fake_vexa, fake_openai)


def browser(app, email=OWNER_EMAIL, *, log_in=True) -> Api:
    web = Api(TestClient(app, follow_redirects=False, client=("198.51.100.1", 50000)))
    if log_in:
        assert web.log_in(email=email).status_code == 200
    return web


@pytest.fixture
def web(capp, owner):
    return browser(capp)


def connect(web: Api, features=None, return_to=None):
    """POST the connect call; returns (consent URL, state, response)."""
    body = {}
    if features is not None:
        body["features"] = features
    if return_to is not None:
        body["return_to"] = return_to
    response = web.post(CONNECT, body)
    assert response.status_code == 200, response.text
    url = response.json()["authorize_url"]
    return url, parse_qs(urlparse(url).query)["state"][0], response


def callback(web: Api, state: str, code: str, extra: str = ""):
    return web.client.get(f"{CALLBACK}?code={quote(code)}&state={quote(state)}{extra}")


def callback_raw(web: Api, query: str):
    return web.client.get(f"{CALLBACK}?{query}")


def connect_google(web: Api, google: FakeGoogle, *, features=None, return_to=None, **consent):
    url, state, _ = connect(web, features, return_to)
    code = google.consent(url, **consent)
    return callback(web, state, code), state, code


def landing(response) -> tuple[str, dict[str, str]]:
    assert response.status_code == 302, response.text
    parsed = urlparse(response.headers["location"])
    return parsed.path, {k: v[0] for k, v in parse_qs(parsed.query).items()}


def connection_of(session, user: User | None = None) -> CalendarConnection | None:
    session.expire_all()
    query = select(CalendarConnection)
    if user is not None:
        query = query.where(CalendarConnection.user_id == user.id)
    return session.scalars(query).first()


def flows(session) -> list[OAuthFlow]:
    session.expire_all()
    return list(session.scalars(select(OAuthFlow)).all())


# ---- starting ----


def test_connect_url_has_offline_consent_pkce_and_login_hint(web, session, owner):
    url, state, response = connect(web)
    parsed = urlparse(url)
    query = {k: v[0] for k, v in parse_qs(parsed.query).items()}
    assert f"{parsed.scheme}://{parsed.netloc}{parsed.path}" == (
        "https://accounts.google.com/o/oauth2/v2/auth"
    )
    assert query["client_id"] == CLIENT_ID
    assert query["response_type"] == "code"
    assert query["access_type"] == "offline"
    assert query["prompt"] == "consent"
    assert query["include_granted_scopes"] == "true"
    assert query["login_hint"] == OWNER_EMAIL
    assert query["code_challenge_method"] == "S256"
    assert query["redirect_uri"] == "https://notes.beyrictech.com/api/calendar/google/callback"
    scopes = query["scope"].split()
    assert CALENDAR_SCOPE == "https://www.googleapis.com/auth/calendar.events.readonly"
    assert CALENDAR_SCOPE in scopes and MEET_SCOPE not in scopes
    assert {"openid", "email"} <= set(
        scopes
    )  # an identity to record, even without a Google sign-in
    assert "profile" not in scopes
    assert query["state"] == state and len(state) >= 40


def test_the_flow_is_stored_hashed_for_this_user_and_expires_in_ten_minutes(web, session, owner):
    before = datetime.now(UTC)
    url, state, _ = connect(web, return_to="/settings/calendar?tab=rules")
    [flow] = flows(session)
    assert flow.state_hash == tokens.digest(state) and state not in flow.state_hash
    assert flow.purpose == "calendar" and flow.user_id == owner.id
    assert flow.return_to == "/settings/calendar?tab=rules"
    assert 43 <= len(flow.code_verifier) <= 128 and flow.nonce
    assert timedelta(minutes=9) < flow.expires_at - before <= timedelta(minutes=10, seconds=5)
    # The PKCE challenge in the URL is the S256 of the stored verifier.
    challenge = parse_qs(urlparse(url).query)["code_challenge"][0]
    digest = hashlib.sha256(flow.code_verifier.encode()).digest()
    assert challenge == base64.urlsafe_b64encode(digest).decode().rstrip("=")


def test_the_response_sets_a_flow_cookie_that_binds_the_browser(web):
    _, state, response = connect(web)
    header = next(h for h in response.headers.get_list("set-cookie") if "calendar_flow" in h)
    assert f"calendar_flow={state}" in header
    assert "HttpOnly" in header and "SameSite=lax" in header
    assert "Path=/api/calendar/google" in header


def test_features_choose_the_scopes_and_the_flow_purpose(web, session):
    _, _, _ = connect(web, ["meet"])
    _, _, _ = connect(web, ["calendar", "meet"])
    both, meet_only = sorted(flows(session), key=lambda f: f.purpose)
    assert (both.purpose, meet_only.purpose) == ("calendar", "meet_scope")


def test_meet_only_asks_for_the_meet_scope_and_not_the_calendar_one(web):
    url, _, _ = connect(web, ["meet"])
    scopes = parse_qs(urlparse(url).query)["scope"][0].split()
    assert MEET_SCOPE in scopes and CALENDAR_SCOPE not in scopes
    url, _, _ = connect(web, ["calendar", "meet"])
    scopes = parse_qs(urlparse(url).query)["scope"][0].split()
    assert MEET_SCOPE in scopes and CALENDAR_SCOPE in scopes


@pytest.mark.parametrize(
    "features", [[], ["calendars"], ["calendar", "calendar", "meet"], "calendar"]
)
def test_unknown_or_empty_features_are_a_validation_error(web, features):
    error_of(web.post(CONNECT, {"features": features}), 422, "validation_error")


def test_connect_needs_a_login_and_the_csrf_token(capp, owner):
    anon = browser(capp, log_in=False)
    error_of(anon.post(CONNECT, {}), 401, "not_authenticated")
    web = browser(capp)
    error_of(web.post(CONNECT, {}, csrf=False), 403, "csrf_rejected")
    error_of(web.post(CONNECT, {}, csrf="wrong"), 403, "csrf_rejected")


@pytest.mark.parametrize(
    "target",
    ["//evil.test", "https://evil.test/", "/\\evil.test", "javascript:alert(1)", "evil", "/a b"],
)
def test_return_to_must_start_with_single_slash(web, session, target):
    connect(web, return_to=target)
    [flow] = flows(session)
    assert flow.return_to == DEFAULT_LANDING  # never stored, so never redirected to


def test_a_trailing_newline_does_not_pass_the_return_to_check(web, session):
    from app import google_signin

    connect(web, return_to="/settings\n")
    assert flows(session)[0].return_to == DEFAULT_LANDING
    for pattern_user in (google_signin.safe_return_to, calendar_connect._safe_return_to):
        assert pattern_user("/settings\n") != "/settings\n"
        assert pattern_user("/settings") == "/settings"


def test_a_missing_return_to_lands_on_the_calendar_settings(web, session):
    connect(web)
    assert flows(session)[0].return_to == DEFAULT_LANDING


def test_the_feature_is_dark_unless_the_calendar_flag_and_a_google_client_are_set(
    engine, settings, google, fake_vexa, fake_openai, owner
):
    off = settings.model_copy(update={"calendar_enabled": False})
    web = browser(build_app(engine, off, google, fake_vexa, fake_openai))
    error_of(web.post(CONNECT, {}), 404, "calendar_unavailable")
    error_of(web.get(CALLBACK + "?code=x&state=y"), 404, "calendar_unavailable")

    app = build_app(engine, settings, google, fake_vexa, fake_openai)
    app.dependency_overrides[google_oauth.get_google] = lambda: None  # no client configured
    error_of(browser(app).post(CONNECT, {}), 404, "calendar_unavailable")


def test_without_an_encryption_key_connecting_is_refused_before_google_is_involved(
    monkeypatch, web, session
):
    monkeypatch.setattr(vault, "get_settings", lambda: make_settings())  # no TOKEN_ENC_KEY
    error_of(web.post(CONNECT, {}), 503, "calendar_unavailable")
    assert flows(session) == []


def test_the_redirect_uri_comes_from_a_short_allow_list(settings):
    assert google_oauth.redirect_uri(settings) == (
        "https://notes.beyrictech.com/api/auth/google/callback"
    )
    assert google_oauth.redirect_uri(settings, google_oauth.CALENDAR_CALLBACK_PATH) == (
        "https://notes.beyrictech.com/api/calendar/google/callback"
    )
    assert set(google_oauth.ALLOWED_CALLBACK_PATHS) == {
        "/api/auth/google/callback",
        "/api/calendar/google/callback",
    }
    for path in ("/api/session", "/evil", "//evil.test/api/calendar/google/callback", ""):
        with pytest.raises(ValueError):
            google_oauth.redirect_uri(settings, path)


def test_the_unverified_app_notice_is_shown_until_google_has_verified_the_app(
    engine, settings, google, fake_vexa, fake_openai, owner
):
    for verified, expected in ((False, True), (True, False)):
        app = build_app(
            engine,
            settings.model_copy(update={"google_app_verified": verified}),
            google,
            fake_vexa,
            fake_openai,
        )
        body = browser(app).post(CONNECT, {}).json()
        assert set(body) == {"authorize_url", "show_unverified_notice"}
        assert body["show_unverified_notice"] is expected


# ---- the callback ----


def test_a_good_callback_stores_the_connection_and_goes_back_to_the_page(
    web, session, owner, google
):
    response, state, code = connect_google(web, google, return_to="/settings/calendar")
    assert landing(response) == ("/settings/calendar", {"calendar": "connected"})
    conn = connection_of(session, owner)
    assert conn.provider == "google" and conn.status == "connected"
    assert conn.google_account_email == "grace@example.test" and conn.google_sub == "g-grace-1"
    assert conn.google_hosted_domain is None
    assert conn.needs_reconnect_reason is None and conn.last_error is None
    assert (
        conn.last_sync_at is None and conn.sync_token is None
    )  # asks the sync loop for a first sync
    assert calendar_connect.granted_features(conn) == {"calendar"}
    assert flows(session) == []  # the flow is used up
    cleared = response.headers.get_list("set-cookie")
    assert any(h.startswith("calendar_flow=") and "Max-Age=0" in h for h in cleared)
    # The exchange carried the code, the secret, the calendar redirect URI and a PKCE verifier.
    [sent] = google.token_requests
    assert sent["code"] == code and sent["client_secret"] == "s3cret"
    assert sent["redirect_uri"] == "https://notes.beyrictech.com/api/calendar/google/callback"
    assert len(sent["code_verifier"]) >= 43


def test_the_refresh_token_is_stored_encrypted_and_decrypts_with_the_user_id_as_owner(
    web, session, owner, stranger, google
):
    connect_google(web, google)
    conn = connection_of(session, owner)
    [token] = google.refresh_tokens
    assert (
        isinstance(conn.refresh_token_enc, bytes) and token.encode() not in conn.refresh_token_enc
    )
    assert conn.key_version == 1
    secret = vault.decrypt(
        conn.refresh_token_enc, conn.key_version, purpose="refresh_token", owner=owner.id
    )
    assert secret.reveal() == token
    with pytest.raises(vault.VaultError):  # a copy in another user's row opens for nobody
        vault.decrypt(
            conn.refresh_token_enc, conn.key_version, purpose="refresh_token", owner=stranger.id
        )
    with pytest.raises(vault.VaultError):
        vault.decrypt(conn.refresh_token_enc, conn.key_version, purpose="ics_url", owner=owner.id)


def test_nothing_in_the_row_or_the_response_holds_the_token_or_the_code(
    web, session, owner, google
):
    response, state, code = connect_google(web, google)
    [token] = google.refresh_tokens
    conn = connection_of(session, owner)
    row = " ".join(
        str(getattr(conn, column.name))
        for column in CalendarConnection.__table__.columns
        if column.name != "refresh_token_enc"
    )
    assert token not in row and code not in row
    assert token not in str(response.headers) and token not in response.text
    assert code not in response.headers["location"] and state not in response.headers["location"]


def test_granted_scopes_are_what_google_granted_not_what_we_asked(web, session, owner, google):
    connect_google(
        web, google, features=["calendar", "meet"], grant={CALENDAR_SCOPE, "email"}
    )  # the person unticked the Meet box
    conn = connection_of(session, owner)
    assert MEET_SCOPE not in conn.granted_scopes.split()
    assert CALENDAR_SCOPE in conn.granted_scopes.split()
    assert calendar_connect.granted_features(conn) == {"calendar"}


def test_scopes_granted_earlier_come_back_and_count(web, session, owner, google):
    connect_google(web, google)
    response, _, _ = connect_google(
        web, google, features=["meet"], previously_granted=frozenset({CALENDAR_SCOPE})
    )
    assert landing(response)[1] == {"calendar": "connected"}
    assert calendar_connect.granted_features(connection_of(session, owner)) == {"calendar", "meet"}


def test_a_meet_only_connection_has_no_calendar_feature(web, session, owner, google):
    connect_google(web, google, features=["meet"])
    conn = connection_of(session, owner)
    assert calendar_connect.granted_features(conn) == {"meet"}


def test_granted_features_reads_the_scope_list(session, owner):
    def conn(scopes):
        return CalendarConnection(user_id=owner.id, granted_scopes=scopes)

    assert calendar_connect.granted_features(conn(None)) == set()
    assert calendar_connect.granted_features(conn("")) == set()
    assert calendar_connect.granted_features(conn("openid email")) == set()
    assert calendar_connect.granted_features(conn(f"openid {CALENDAR_SCOPE}")) == {"calendar"}
    assert calendar_connect.granted_features(conn(f"{MEET_SCOPE} {CALENDAR_SCOPE}")) == {
        "calendar",
        "meet",
    }
    # Broader or look-alike scopes are not ours: only the exact strings count.
    assert (
        calendar_connect.granted_features(
            conn("https://www.googleapis.com/auth/calendar " + CALENDAR_SCOPE + "x")
        )
        == set()
    )


def test_callback_refuses_when_calendar_scope_unticked(web, session, owner, google):
    response, _, _ = connect_google(web, google, grant={"email"})
    path, query = landing(response)
    assert path == DEFAULT_LANDING and query == {"calendar_error": "scope_missing"}
    assert connection_of(session) is None
    assert flows(session) == []


def test_meet_upgrade_refused_when_the_meet_box_is_unticked_keeps_the_connection(
    web, session, owner, google
):
    connect_google(web, google)
    before = connection_of(session, owner)
    blob, connected_at = before.refresh_token_enc, before.connected_at
    response, _, _ = connect_google(
        web,
        google,
        features=["meet"],
        grant={"email"},
        previously_granted=frozenset({CALENDAR_SCOPE}),
    )
    assert landing(response)[1] == {"calendar_error": "scope_missing"}
    after = connection_of(session, owner)
    assert after.refresh_token_enc == blob and after.connected_at == connected_at
    assert calendar_connect.granted_features(after) == {"calendar"}


@pytest.mark.parametrize("how", ["unknown", "reused", "expired", "garbage", "missing"])
def test_callback_rejects_unknown_expired_or_reused_state_and_stores_nothing(
    web, session, google, how
):
    url, state, _ = connect(web)
    code = google.consent(url)
    if how == "reused":
        assert landing(callback(web, state, google.consent(url)))[1] == {"calendar": "connected"}
        session.execute(CalendarConnection.__table__.delete())
        session.commit()
        response = callback(web, state, code)
    elif how == "expired":
        session.execute(
            OAuthFlow.__table__.update().values(expires_at=datetime.now(UTC) - timedelta(seconds=1))
        )
        session.commit()
        response = callback(web, state, code)
    elif how == "unknown":
        response = callback(web, "x" * 43, code)
    elif how == "garbage":
        response = callback(web, "x" * 1500, code)
    else:
        response = web.client.get(f"{CALLBACK}?code={code}")
    assert landing(response) == (DEFAULT_LANDING, {"calendar_error": "google_failed"})
    assert connection_of(session) is None
    if how != "unknown" and how != "garbage" and how != "missing":
        assert flows(session) == []  # a state, once seen, is gone whatever happened
    assert len(google.token_requests) == (1 if how == "reused" else 0)  # Google was never asked


def test_a_sign_in_flow_state_cannot_finish_a_calendar_connect(web, session, google, owner):
    from app import google_signin

    url, state = google_signin.start_flow(
        session, google.oauth, "/", datetime.now(UTC)
    )  # a purpose "signin" flow
    web.client.cookies.set("calendar_flow", state, domain="testserver", path="/api/calendar/google")
    code = google.consent(url)
    assert landing(callback(web, state, code))[1] == {"calendar_error": "google_failed"}
    assert connection_of(session) is None


def test_callback_rejects_a_different_signed_in_user(capp, web, session, owner, stranger, google):
    url, state, _ = connect(web)  # the owner starts
    code = google.consent(url)
    other = browser(capp, OTHER_EMAIL)
    # The other user's browser even holds the flow cookie (a planted or copied one).
    other.client.cookies.set(
        "calendar_flow", state, domain="testserver", path="/api/calendar/google"
    )
    response = callback(other, state, code)
    assert landing(response) == (DEFAULT_LANDING, {"calendar_error": "wrong_account"})
    assert connection_of(session) is None and flows(session) == []
    assert google.token_requests == []


def test_callback_needs_the_flow_cookie_of_the_browser_that_started_it(
    capp, web, session, owner, google
):
    url, state, _ = connect(web)
    code = google.consent(url)
    same_user_elsewhere = browser(capp)  # signed in as the owner but without the cookie
    response = callback(same_user_elsewhere, state, code)
    assert landing(response)[1] == {"calendar_error": "google_failed"}
    assert connection_of(session) is None and flows(session) == []


def test_callback_needs_a_signed_in_session_and_uses_the_state_up(capp, web, session, google):
    url, state, _ = connect(web)
    code = google.consent(url)
    signed_out = browser(capp, log_in=False)
    signed_out.client.cookies.set(
        "calendar_flow", state, domain="testserver", path="/api/calendar/google"
    )
    response = callback(signed_out, state, code)
    assert landing(response) == ("/login", {"error": "google_failed"})
    assert connection_of(session) is None and flows(session) == []
    assert google.token_requests == []


def test_callback_is_public_but_state_checked_and_cookie_required(capp):
    assert ("GET", CALLBACK) in auth.PUBLIC_API
    assert ("POST", CONNECT) not in auth.PUBLIC_API
    response = browser(capp, log_in=False).client.get(CALLBACK)  # no state at all
    assert landing(response) == ("/login", {"error": "google_failed"})


def test_the_person_cancelling_on_the_consent_screen(web, session, google):
    url, state, _ = connect(web, return_to="/settings/calendar")
    response = callback_raw(web, f"error=access_denied&state={state}")
    assert landing(response) == ("/settings/calendar", {"calendar_error": "google_denied"})
    assert connection_of(session) is None and flows(session) == []


def test_a_token_endpoint_failure_stores_nothing(web, session, google):
    url, state, _ = connect(web)
    code = google.consent(url)
    google.fail_token_endpoint = True
    assert landing(callback(web, state, code))[1] == {"calendar_error": "google_failed"}
    assert connection_of(session) is None


def test_an_id_token_for_another_nonce_is_refused(web, session, google):
    url, state, _ = connect(web)
    code = google.consent(url, nonce="a-nonce-this-flow-never-issued")
    assert landing(callback(web, state, code))[1] == {"calendar_error": "google_failed"}
    assert connection_of(session) is None


def test_a_wrong_code_is_refused_by_google_and_stores_nothing(web, session, google):
    url, state, _ = connect(web)
    google.consent(url)
    assert landing(callback(web, state, "4/not-a-real-code"))[1] == {
        "calendar_error": "google_failed"
    }
    assert connection_of(session) is None


def test_the_pkce_verifier_is_checked_by_google(web, session, google):
    url, state, _ = connect(web)
    code = google.consent(url)
    session.execute(OAuthFlow.__table__.update().values(code_verifier="w" * 64))
    session.commit()
    assert landing(callback(web, state, code))[1] == {"calendar_error": "google_failed"}
    assert connection_of(session) is None


def test_connected_google_email_may_differ_from_signin_email(web, session, owner, google):
    connect_google(
        web, google, email="Work.Person@Example.Test", sub="g-other-9", hosted_domain="Example.Test"
    )
    conn = connection_of(session, owner)
    assert owner.email == OWNER_EMAIL
    assert conn.google_account_email == "work.person@example.test"
    assert conn.google_sub == "g-other-9" and conn.google_hosted_domain == "example.test"
    session.refresh(owner)
    assert owner.google_sub is None  # connecting a calendar is not a sign-in identity


def test_without_a_refresh_token_a_first_connection_is_refused(web, session, google):
    google.issue_refresh_token = False
    response, _, _ = connect_google(web, google)
    assert landing(response)[1] == {"calendar_error": "no_refresh_token"}
    assert connection_of(session) is None


def test_consent_without_refresh_token_keeps_the_old_one(web, session, owner, google):
    connect_google(web, google)
    before = connection_of(session, owner)
    blob, version, connected_at = before.refresh_token_enc, before.key_version, before.connected_at
    google.issue_refresh_token = False
    response, _, _ = connect_google(web, google)  # the same Google account again
    assert landing(response)[1] == {"calendar": "connected"}
    after = connection_of(session, owner)
    assert (after.refresh_token_enc, after.key_version) == (blob, version)
    assert after.connected_at == connected_at  # nothing was replaced
    assert (
        vault.decrypt(
            after.refresh_token_enc, after.key_version, purpose="refresh_token", owner=owner.id
        ).reveal()
        == google.refresh_tokens[0]
    )


def test_another_google_account_without_a_refresh_token_is_refused(web, session, owner, google):
    connect_google(web, google)
    google.issue_refresh_token = False
    response, _, _ = connect_google(web, google, email="someone.else@example.test", sub="g-x")
    assert landing(response)[1] == {"calendar_error": "different_account"}
    assert connection_of(session, owner).google_sub == "g-grace-1"


def kill(session, owner, reason="revoked"):
    conn = connection_of(session, owner)
    conn.status, conn.needs_reconnect_reason = "needs_reconnect", reason
    session.commit()
    return conn


def test_another_account_without_a_refresh_token_on_a_dead_connection_needs_one(
    web, session, owner, google
):
    connect_google(web, google)
    kill(session, owner)
    google.issue_refresh_token = False
    response, _, _ = connect_google(web, google, email="someone.else@example.test", sub="g-x")
    assert landing(response)[1] == {"calendar_error": "no_refresh_token"}
    assert connection_of(session, owner).google_sub == "g-grace-1"


def test_a_new_scope_without_a_new_refresh_token_is_refused_not_faked(web, session, owner, google):
    connect_google(web, google)  # calendar only
    google.issue_refresh_token = False
    response, _, _ = connect_google(
        web, google, features=["meet"], previously_granted=frozenset({CALENDAR_SCOPE})
    )
    assert landing(response)[1] == {"calendar_error": "no_refresh_token"}
    assert calendar_connect.granted_features(connection_of(session, owner)) == {"calendar"}


def test_a_reply_without_a_token_records_the_new_scopes_and_heals_a_degraded_connection(
    web, session, owner, google
):
    connect_google(web, google)  # calendar
    conn = connection_of(session, owner)
    conn.status, conn.last_error, conn.last_error_at = "degraded", "boom", NOW
    session.commit()
    blob = conn.refresh_token_enc
    google.issue_refresh_token = False
    # A calendar reconnect with Meet riding along; the person ticked Meet this time.
    response, _, _ = connect_google(web, google, features=["calendar", "meet"])
    assert landing(response)[1] == {"calendar": "connected"}
    after = connection_of(session, owner)
    assert after.refresh_token_enc == blob  # the old token stands in
    assert calendar_connect.granted_features(after) == {"calendar", "meet"}
    assert after.status == "connected" and after.last_error is None and after.last_error_at is None


def test_a_reply_without_a_token_keeps_old_scopes_google_did_not_repeat(
    web, session, owner, google
):
    connect_google(web, google)
    google.issue_refresh_token = False
    connect_google(web, google, grant={CALENDAR_SCOPE})  # Google answers with a shorter list
    assert CALENDAR_SCOPE in connection_of(session, owner).granted_scopes.split()


def test_a_reply_without_a_token_follows_the_testing_flag_for_the_expiry_hint(
    engine, settings, google, fake_vexa, fake_openai, session, owner
):
    connect_google(browser(build_app(engine, settings, google, fake_vexa, fake_openai)), google)
    google.issue_refresh_token = False

    def again(testing: bool):
        app = build_app(
            engine,
            settings.model_copy(update={"google_oauth_testing": testing}),
            google,
            fake_vexa,
            fake_openai,
        )
        connect_google(browser(app), google)
        return connection_of(session, owner)

    on = again(True)
    assert on.expected_expiry_at == on.connected_at + timedelta(days=7)  # the old token's age
    assert again(False).expected_expiry_at is None


def test_the_row_lock_is_released_after_a_reply_without_a_token(
    web, session, owner, google, engine
):
    connect_google(web, google)
    google.issue_refresh_token = False
    connect_google(web, google)
    with Session(engine) as other:  # would block (and time out) if the first session kept its lock
        other.execute(text("set lock_timeout = '2s'"))
        conn = other.scalars(select(CalendarConnection).with_for_update()).one()
        conn.last_error = "x"
        other.commit()


def test_a_dead_connection_is_not_kept_alive_by_a_reply_without_a_token(
    web, session, owner, google
):
    connect_google(web, google)
    conn = connection_of(session, owner)
    conn.status, conn.needs_reconnect_reason = "needs_reconnect", "revoked"
    session.commit()
    google.issue_refresh_token = False
    response, _, _ = connect_google(web, google)
    assert landing(response)[1] == {"calendar_error": "no_refresh_token"}
    assert connection_of(session, owner).status == "needs_reconnect"


def test_reconnect_replaces_rather_than_duplicates(web, session, owner, google):
    connect_google(web, google)
    conn = connection_of(session, owner)
    first_id, first_blob = conn.id, conn.refresh_token_enc
    conn.status, conn.needs_reconnect_reason = "needs_reconnect", "revoked"
    conn.last_error, conn.last_error_at = "invalid_grant", NOW
    conn.sync_token, conn.last_sync_at = "sync-token-1", NOW
    session.commit()
    response, _, _ = connect_google(web, google)
    assert landing(response)[1] == {"calendar": "connected"}
    session.expire_all()
    [row] = session.scalars(select(CalendarConnection)).all()
    assert row.id == first_id  # the same row, updated
    assert row.status == "connected" and row.needs_reconnect_reason is None
    assert row.last_error is None and row.last_error_at is None
    assert row.sync_token is None and row.last_sync_at is None  # a full first sync again
    assert row.refresh_token_enc != first_blob
    assert (
        vault.decrypt(
            row.refresh_token_enc, row.key_version, purpose="refresh_token", owner=owner.id
        ).reveal()
        == google.refresh_tokens[-1]
    )


def add_event_and_join(session, conn, user, *, state="scheduled"):
    template = Template(owner_user_id=user.id, name="General", instructions="x")
    session.add(template)
    event = CalendarEvent(
        user_id=user.id,
        connection_id=conn.id,
        source_event_id="evt-1",
        title="Standup",
        starts_at=NOW,
        ends_at=NOW + timedelta(hours=1),
        platform="google_meet",
        meeting_url="https://meet.google.com/abc-defg-hij",
        native_id="abc-defg-hij",
        status="confirmed",
    )
    session.add(event)
    session.flush()
    join = ScheduledJoin(
        user_id=user.id,
        calendar_event_id=event.id,
        platform="google_meet",
        native_id="abc-defg-hij",
        meeting_url=event.meeting_url,
        starts_at=NOW,
        ends_at=NOW + timedelta(hours=1),
        run_at=NOW,
        deadline_at=NOW + timedelta(minutes=15),
        state=state,
    )
    session.add(join)
    session.commit()
    return event, join


@pytest.mark.parametrize("features", [["calendar"], ["meet"], ["calendar", "meet"]])
@pytest.mark.parametrize("status", ["connected", "degraded"])
def test_a_different_google_account_never_replaces_a_working_connection(
    web, session, owner, google, features, status
):
    connect_google(web, google)
    conn = connection_of(session, owner)
    event, join = add_event_and_join(session, conn, owner)
    conn.status = status
    conn.meet_subscription_name, conn.meet_subscription_state = "subscriptions/s1", "active"
    session.commit()
    blob, scopes = conn.refresh_token_enc, conn.granted_scopes
    response, _, _ = connect_google(
        web, google, features=features, email="other@example.test", sub="g-other-2"
    )
    assert landing(response)[1] == {"calendar_error": "different_account"}
    session.expire_all()
    after = connection_of(session, owner)
    assert after.google_sub == "g-grace-1" and after.status == status
    assert after.refresh_token_enc == blob and after.granted_scopes == scopes
    assert after.meet_subscription_state == "active"
    assert len(session.scalars(select(CalendarEvent)).all()) == 1  # the events survive
    assert len(session.scalars(select(ScheduledJoin)).all()) == 1
    assert flows(session) == []


def test_replacing_a_dead_connection_with_another_account_drops_the_old_accounts_events(
    web, session, owner, google
):
    connect_google(web, google)
    conn = connection_of(session, owner)
    add_event_and_join(session, conn, owner)
    conn.meet_subscription_name = "subscriptions/s1"
    conn.meet_subscription_expires_at = NOW
    conn.meet_subscription_state = "active"
    session.commit()
    kill(session, owner)
    response, _, _ = connect_google(web, google, email="other@example.test", sub="g-other-2")
    assert landing(response)[1] == {"calendar": "connected"}
    session.expire_all()
    assert session.scalars(select(CalendarEvent)).all() == []
    assert session.scalars(select(ScheduledJoin)).all() == []
    after = connection_of(session, owner)
    assert after.google_sub == "g-other-2" and after.status == "connected"
    # The old account's Meet subscription is forgotten, not carried over.
    assert after.meet_subscription_name is None
    assert after.meet_subscription_expires_at is None
    assert after.meet_subscription_state == "none"


def test_reconnecting_the_same_google_account_keeps_events_and_joins(web, session, owner, google):
    connect_google(web, google)
    add_event_and_join(session, connection_of(session, owner), owner)
    connect_google(web, google)
    session.expire_all()
    assert len(session.scalars(select(CalendarEvent)).all()) == 1
    assert len(session.scalars(select(ScheduledJoin)).all()) == 1


def test_a_join_already_running_survives_replacing_a_dead_connection(web, session, owner, google):
    connect_google(web, google)
    add_event_and_join(session, connection_of(session, owner), owner, state="joined")
    kill(session, owner)
    connect_google(web, google, email="other@example.test", sub="g-other-2")
    session.expire_all()
    assert [j.state for j in session.scalars(select(ScheduledJoin)).all()] == ["joined"]


def test_two_users_connect_separately_and_only_see_their_own_row(capp, web, session, owner, google):
    stranger = make_user(session, OTHER_EMAIL)
    connect_google(web, google, email="a@example.test", sub="g-a")
    other = browser(capp, OTHER_EMAIL)
    connect_google(other, google, email="b@example.test", sub="g-b")
    assert connection_of(session, owner).google_account_email == "a@example.test"
    assert connection_of(session, stranger).google_account_email == "b@example.test"


def test_expected_expiry_set_to_seven_days_only_while_testing_flag_is_on_and_clear_when_off(
    engine, settings, google, fake_vexa, fake_openai, session, owner
):
    def run(testing: bool):
        app = build_app(
            engine,
            settings.model_copy(update={"google_oauth_testing": testing}),
            google,
            fake_vexa,
            fake_openai,
        )
        connect_google(browser(app), google)
        return connection_of(session, owner)

    on = run(True)
    assert on.expected_expiry_at is not None
    assert abs((on.expected_expiry_at - on.connected_at) - timedelta(days=7)) < timedelta(seconds=2)
    off = run(False)
    assert off.expected_expiry_at is None  # a reconnect clears the old hint


def test_the_return_path_keeps_its_own_query_when_the_result_is_added(web, session, google):
    response, _, _ = connect_google(web, google, return_to="/settings/calendar?tab=rules")
    path, query = landing(response)
    assert path == "/settings/calendar"
    assert query == {"tab": "rules", "calendar": "connected"}


def test_no_code_or_token_in_captured_logs(web, session, owner, google, caplog):
    caplog.set_level(logging.DEBUG)
    secrets_seen: list[str] = []
    for outcome in ("good", "denied", "refused", "exchange-fails"):
        url, state, _ = connect(web)
        secrets_seen.append(state)
        query = parse_qs(urlparse(url).query)
        secrets_seen.append(query["code_challenge"][0])
        if outcome == "denied":
            callback_raw(web, f"error=access_denied&state={state}")
            continue
        if outcome == "exchange-fails":
            google.fail_token_endpoint = True
        code = google.consent(url, grant={"email"} if outcome == "refused" else None)
        secrets_seen.append(code)
        callback(web, state, code)
        google.fail_token_endpoint = False
    secrets_seen += google.refresh_tokens + ["s3cret"]
    secrets_seen += [request["code_verifier"] for request in google.token_requests]
    secrets_seen += [f"ya29.fake-access-token-{n}" for n in range(1, 10)]
    # The test client's own request log (httpx) records the callback URL it fetched: that URL
    # carries the code by OAuth's design, and in production the proxy's access log does the same
    # (a single-use code that is useless without the PKCE verifier). Everything the app itself
    # logs must be clean, and so must the app's calls out to Google.
    lines = [r.getMessage() for r in caplog.records if "testserver" not in r.getMessage()]
    assert lines, "the flow logged nothing at all: the filter is wrong"
    text = "\n".join(lines)
    for secret in secrets_seen:
        assert secret not in text
    assert "1//fake-refresh-token" not in text and "ya29." not in text


# ---- disconnecting (C4) ----


def add_join(session, conn, user, state: str, n: int):
    """One event and its scheduled join in `state`; `n` keeps the unique keys apart."""
    event = CalendarEvent(
        user_id=user.id,
        connection_id=conn.id,
        source_event_id=f"evt-{user.id}-{n}",
        title=f"Standup {n}",
        starts_at=NOW,
        ends_at=NOW + timedelta(hours=1),
        platform="google_meet",
        meeting_url=f"https://meet.google.com/abc-defg-{n:03d}",
        native_id=f"abc-defg-{n:03d}",
        status="confirmed",
    )
    session.add(event)
    session.flush()
    join = ScheduledJoin(
        user_id=user.id,
        calendar_event_id=event.id,
        platform="google_meet",
        native_id=event.native_id,
        meeting_url=event.meeting_url,
        starts_at=NOW,
        ends_at=NOW + timedelta(hours=1),
        run_at=NOW,
        deadline_at=NOW + timedelta(minutes=15),
        state=state,
    )
    session.add(join)
    session.commit()
    return event, join


def add_notification(session, user, kind: str, key: str) -> Notification:
    row = Notification(user_id=user.id, kind=kind, title="t", body="b", dedupe_key=key)
    session.add(row)
    session.commit()
    return row


def count(session, model, **where) -> int:
    session.expire_all()
    return len(session.scalars(select(model).filter_by(**where)).all())


def test_disconnect_revokes_at_google_and_deletes_everything_listed_in_spec_12_2(
    web, session, owner, google
):
    connect_google(web, google, features=["calendar", "meet"])
    conn = connection_of(session, owner)
    for n, state in enumerate(JOIN_STATES):  # a join in every state
        add_join(session, conn, owner, state, n)
    conn.meet_subscription_name = "subscriptions/s1"
    conn.meet_subscription_expires_at = NOW
    conn.meet_subscription_state = "active"
    conn.sync_token, conn.last_sync_at, conn.last_error = "sync-1", NOW, "boom"
    conn.expected_expiry_at = NOW
    session.commit()
    add_notification(session, owner, "calendar.disconnected", "cal_disc:1")
    add_notification(session, owner, "calendar.not_recorded", "cal_nr:1")
    add_notification(session, owner, "usage.hours_low", "usage:1")
    token = google.refresh_tokens[-1]

    response = web.post(DISCONNECT)

    assert response.status_code == 200, response.text
    assert response.json() == {"status": "disconnected"}
    assert google.revoked == [token]  # the refresh token, revoked at Google
    assert count(session, CalendarEvent) == 0
    assert count(session, ScheduledJoin) == 0  # every state, including live ones
    kinds = sorted(n.kind for n in session.scalars(select(Notification)).all())
    assert kinds == ["usage.hours_low"]  # only the calendar-kind ones went
    after = connection_of(session, owner)
    assert after.status == "disconnected" and after.needs_reconnect_reason is None
    assert after.refresh_token_enc is None and after.key_version is None
    assert after.granted_scopes is None and granted_features(after) == set()
    assert after.google_account_email is None and after.google_sub is None
    assert after.google_hosted_domain is None
    assert after.sync_token is None and after.last_sync_at is None
    assert after.last_error is None and after.last_error_at is None
    assert after.expected_expiry_at is None
    assert after.meet_subscription_name is None
    assert after.meet_subscription_expires_at is None
    assert after.meet_subscription_state == "none"


def granted_features(conn):
    return calendar_connect.granted_features(conn)


def test_the_revoke_call_goes_to_the_fixed_url_with_only_the_token(web, session, owner, google):
    connect_google(web, google)
    web.post(DISCONNECT)
    [request] = google.revoke_requests
    assert str(request.url) == REVOKE_URL
    assert request.method == "POST"
    assert parse_qs(request.content.decode()) == {"token": [google.refresh_tokens[-1]]}


def test_disconnect_keeps_meetings_usage_and_settings(web, session, owner, google, template):
    connect_google(web, google)
    conn = connection_of(session, owner)
    _, join = add_join(session, conn, owner, "joined", 1)
    meeting = add_meeting(
        session, template, title="Standup", url=ZOOM, source="calendar", status="ready"
    )
    join.meeting_id = meeting.id
    session.add(
        UsageEntry(user_id=owner.id, email_hash="h" * 64, meeting_id=meeting.id, seconds=60)
    )
    settings_row = session.get(UserSettings, owner.id)
    if settings_row is None:
        settings_row = UserSettings(user_id=owner.id)
        session.add(settings_row)
    settings_row.auto_join_rule = "organizer"
    settings_row.calendar_template_id = template.id
    session.commit()

    assert web.post(DISCONNECT).status_code == 200

    session.expire_all()
    kept = session.get(Meeting, meeting.id)
    assert kept.title == "Standup" and kept.url == ZOOM and kept.status == "ready"
    assert [u.seconds for u in session.scalars(select(UsageEntry)).all()] == [60]
    after = session.get(UserSettings, owner.id)
    assert after.auto_join_rule == "organizer" and after.calendar_template_id == template.id


def test_running_calendar_meeting_keeps_going(web, session, owner, google, template, fake_vexa):
    connect_google(web, google)
    conn = connection_of(session, owner)
    _, join = add_join(session, conn, owner, "joined", 1)
    meeting = add_meeting(session, template, source="calendar", title="Standup", status="bot_sent")
    join.meeting_id = meeting.id
    session.commit()

    assert web.post(DISCONNECT).status_code == 200

    session.expire_all()
    running = session.get(Meeting, meeting.id)
    assert running.status == "bot_sent" and running.source == "calendar"
    assert not getattr(fake_vexa, "stopped", [])  # nobody told the bot to leave


def test_join_in_dispatching_state_is_cancelled_not_sent(
    web, session, owner, google, fake_vexa, template
):
    connect_google(web, google)
    conn = connection_of(session, owner)
    for n, state in enumerate(("scheduled", "dispatching", "no_capacity")):
        add_join(session, conn, owner, state, n)
    assert web.post(DISCONNECT).status_code == 200
    # Nothing is left for a runner to pick up: no row in any state, so no bot can be sent and
    # no meeting is created for it.
    assert count(session, ScheduledJoin) == 0
    assert count(session, Meeting) == 0


@pytest.mark.parametrize(
    "answer", [503, 500, 403, httpx.ConnectError("down"), httpx.ReadTimeout("slow")]
)
def test_revoke_failure_still_deletes_locally_and_logs_warning(
    web, session, owner, google, caplog, answer
):
    connect_google(web, google)
    add_join(session, connection_of(session, owner), owner, "scheduled", 1)
    google.revoke_override = answer
    caplog.set_level(logging.DEBUG)

    response = web.post(DISCONNECT)

    assert response.status_code == 200
    assert count(session, CalendarEvent) == 0 and count(session, ScheduledJoin) == 0
    after = connection_of(session, owner)
    assert after.status == "disconnected" and after.refresh_token_enc is None
    warnings = [r.getMessage() for r in caplog.records if r.levelno == logging.WARNING]
    assert any("revoke" in line for line in warnings)
    assert google.refresh_tokens[-1] not in caplog.text


def test_a_token_google_already_dropped_is_not_a_failure(web, session, owner, google, caplog):
    connect_google(web, google)
    google.revoked.append(google.refresh_tokens[-1])  # revoked at myaccount.google.com already
    caplog.set_level(logging.WARNING)
    assert web.post(DISCONNECT).status_code == 200
    assert "revoke" not in caplog.text
    assert connection_of(session, owner).refresh_token_enc is None


def test_a_token_that_cannot_be_decrypted_is_still_deleted(
    monkeypatch, web, session, owner, google, caplog
):
    connect_google(web, google)
    caplog.set_level(logging.WARNING)
    other_key = make_settings(token_enc_key=base64.b64encode(b"Z" * 32).decode())
    monkeypatch.setattr(vault, "get_settings", lambda: other_key)  # the key was lost or rotated
    assert web.post(DISCONNECT).status_code == 200
    assert google.revoke_requests == []  # nothing readable to revoke
    assert connection_of(session, owner).refresh_token_enc is None
    assert any("revoke" in r.getMessage() for r in caplog.records)


def test_a_second_connection_can_be_created_again_after_disconnect(web, session, owner, google):
    connect_google(web, google)
    first = connection_of(session, owner)
    first_id = first.id
    web.post(DISCONNECT)
    response, _, _ = connect_google(web, google)
    assert landing(response)[1] == {"calendar": "connected"}
    session.expire_all()
    [row] = session.scalars(select(CalendarConnection)).all()
    assert row.id == first_id and row.status == "connected"
    assert row.google_sub == "g-grace-1" and row.refresh_token_enc is not None
    assert row.last_sync_at is None  # a full first sync again
    assert granted_features(row) == {"calendar"}


def test_after_disconnect_another_google_account_can_connect(web, session, owner, google):
    connect_google(web, google)
    web.post(DISCONNECT)
    response, _, _ = connect_google(web, google, email="other@example.test", sub="g-other-2")
    assert landing(response)[1] == {"calendar": "connected"}
    assert connection_of(session, owner).google_sub == "g-other-2"


def test_disconnect_is_idempotent_and_safe_to_double_submit(web, session, owner, google):
    connect_google(web, google)
    add_join(session, connection_of(session, owner), owner, "scheduled", 1)
    first = web.post(DISCONNECT)
    second = web.post(DISCONNECT)
    assert first.status_code == second.status_code == 200
    assert first.json() == second.json() == {"status": "disconnected"}
    assert len(google.revoke_requests) == 1  # the second one has no token left to revoke
    assert connection_of(session, owner).status == "disconnected"


def test_disconnect_with_no_connection_is_a_no_op(web, session, owner, google):
    assert web.post(DISCONNECT).json() == {"status": "disconnected"}
    assert google.revoke_requests == []
    assert count(session, CalendarConnection) == 0  # nothing is created by disconnecting


def test_disconnect_only_touches_the_signed_in_users_rows(capp, web, session, owner, google):
    stranger = make_user(session, OTHER_EMAIL)
    other = browser(capp, OTHER_EMAIL)
    connect_google(web, google, email="a@example.test", sub="g-a")
    connect_google(other, google, email="b@example.test", sub="g-b")
    mine, theirs = connection_of(session, owner), connection_of(session, stranger)
    add_join(session, mine, owner, "scheduled", 1)
    add_join(session, theirs, stranger, "scheduled", 2)
    add_notification(session, owner, "calendar.x", "k1")
    add_notification(session, stranger, "calendar.x", "k1")
    their_token = vault.decrypt(
        theirs.refresh_token_enc, theirs.key_version, purpose="refresh_token", owner=stranger.id
    ).reveal()
    their_blob = theirs.refresh_token_enc

    assert web.post(DISCONNECT).status_code == 200

    assert connection_of(session, owner).status == "disconnected"
    survivor = connection_of(session, stranger)
    assert survivor.status == "connected" and survivor.refresh_token_enc == their_blob
    assert survivor.google_sub == "g-b"
    assert count(session, CalendarEvent, user_id=stranger.id) == 1
    assert count(session, ScheduledJoin, user_id=stranger.id) == 1
    assert count(session, Notification, user_id=stranger.id) == 1
    assert count(session, ScheduledJoin, user_id=owner.id) == 0
    assert their_token not in google.revoked  # only the caller's own token is revoked


def test_disconnect_needs_a_login_and_the_csrf_token(capp, owner):
    anon = browser(capp, log_in=False)
    error_of(anon.post(DISCONNECT), 401, "not_authenticated")
    web = browser(capp)
    error_of(web.post(DISCONNECT, csrf=False), 403, "csrf_rejected")
    error_of(web.post(DISCONNECT, csrf="wrong"), 403, "csrf_rejected")


def test_disconnect_still_works_while_the_calendar_features_are_switched_off(
    engine, settings, google, fake_vexa, fake_openai, session, owner, web
):
    """The privacy promise (delete on disconnect) must not depend on the feature flag."""
    connect_google(web, google)
    add_join(session, connection_of(session, owner), owner, "scheduled", 1)
    off = settings.model_copy(update={"calendar_enabled": False})
    app = build_app(engine, off, google, fake_vexa, fake_openai)
    assert browser(app).post(DISCONNECT).status_code == 200
    assert count(session, ScheduledJoin) == 0
    assert connection_of(session, owner).status == "disconnected"
    assert google.revoked  # and the revoke still went out


def test_disconnect_deletes_locally_even_with_no_google_client_configured(
    engine, settings, google, fake_vexa, fake_openai, session, owner, web
):
    connect_google(web, google)
    add_join(session, connection_of(session, owner), owner, "scheduled", 1)
    app = build_app(engine, settings, google, fake_vexa, fake_openai)
    app.dependency_overrides[google_oauth.get_google] = lambda: None
    assert browser(app).post(DISCONNECT).status_code == 200
    assert count(session, ScheduledJoin) == 0
    assert connection_of(session, owner).refresh_token_enc is None
    assert google.revoke_requests == []


def test_on_disconnect_hooks_run_with_the_connection_before_it_is_cleared(
    monkeypatch, web, session, owner, google
):
    connect_google(web, google)
    conn = connection_of(session, owner)
    conn.meet_subscription_name = "subscriptions/s1"
    session.commit()
    seen = []

    def hook(hook_session, hook_conn, hook_google):
        seen.append((hook_conn.user_id, hook_conn.meet_subscription_name, hook_google))

    monkeypatch.setattr(calendar_connect, "ON_DISCONNECT", [hook])
    web.post(DISCONNECT)
    assert seen == [(owner.id, "subscriptions/s1", google.oauth)]
    assert connection_of(session, owner).meet_subscription_name is None


def test_a_failing_hook_does_not_stop_the_deletion_and_logs_no_detail(
    monkeypatch, web, session, owner, google, caplog
):
    connect_google(web, google)
    add_join(session, connection_of(session, owner), owner, "scheduled", 1)

    def broken(hook_session, hook_conn, hook_google):
        raise RuntimeError("secret detail 1//fake-refresh-token")

    monkeypatch.setattr(calendar_connect, "ON_DISCONNECT", [broken])
    caplog.set_level(logging.DEBUG)
    assert web.post(DISCONNECT).status_code == 200
    assert count(session, ScheduledJoin) == 0
    assert connection_of(session, owner).status == "disconnected"
    assert "RuntimeError" in caplog.text and "secret detail" not in caplog.text


def test_replacing_a_dead_connection_with_another_account_revokes_the_old_token(
    monkeypatch, web, session, owner, google
):
    connect_google(web, google)
    old_token = google.refresh_tokens[-1]
    kill(session, owner)
    hooked = []
    monkeypatch.setattr(calendar_connect, "ON_DISCONNECT", [lambda s, c, g: hooked.append(c.id)])
    response, _, _ = connect_google(web, google, email="other@example.test", sub="g-other-2")
    assert landing(response)[1] == {"calendar": "connected"}
    assert google.revoked == [old_token]
    assert hooked == [connection_of(session, owner).id]
    new_token = google.refresh_tokens[-1]
    assert new_token != old_token and new_token not in google.revoked


def test_reconnecting_the_same_account_does_not_revoke_anything(web, session, owner, google):
    connect_google(web, google)
    kill(session, owner)
    connect_google(web, google)
    assert google.revoke_requests == []


def test_no_token_or_code_in_the_logs_of_a_disconnect(web, session, owner, google, caplog):
    connect_google(web, google)
    caplog.set_level(logging.DEBUG)
    google.revoke_override = 503
    web.post(DISCONNECT)
    lines = [r.getMessage() for r in caplog.records if "testserver" not in r.getMessage()]
    assert lines
    text = "\n".join(lines)
    assert "1//fake-refresh-token" not in text and "ya29." not in text and "s3cret" not in text
