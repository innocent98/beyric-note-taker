"""The Microsoft OAuth client (Outlook phase O, M2): authorize URL, code redemption, ID token
checks, refresh with rotation, error mapping, scopes, settings and the start-up checks.

**Fixture rule (the M0 run has not happened yet).** Every body here comes from
`tests/fixtures/microsoft/documented_*__replace_with_m0_capture.json`: the shapes Microsoft
DOCUMENTS (Outlook spec section 4), not captures. Each test says so in its docstring where a
fixture decides the outcome; when Adebayo's M0 run produces the redacted captures they replace
these files and every assumption listed in the SOP under "to confirm with the M0 run" is settled.
"""

import base64
import json
import logging
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest

from app import auth, calendar_states, microsoft_oauth, vault
from app.calendar_source import AuthRevoked, RefreshTokenUnreadable, TransientError
from app.microsoft_oauth import (
    CONSUMER_TENANT,
    ClientMisconfigured,
    ClientRejected,
    MicrosoftError,
    MicrosoftOAuth,
    TenantNotAllowed,
)
from app.vault import Secret
from tests.test_web import make_settings

FIXTURES = Path(__file__).parent / "fixtures" / "microsoft"
NOW = datetime(2026, 11, 9, 12, 0, tzinfo=UTC)
CLIENT_ID = "CLIENT-ID-PLACEHOLDER"
CLIENT_SECRET = "CLIENT-SECRET-PLACEHOLDER"
WORK_TENANT = "00000000-0000-0000-0000-0000000000aa"
OTHER_TENANT = "00000000-0000-0000-0000-0000000000bb"
CODE = "AUTH-CODE-PLACEHOLDER"
VERIFIER = "VERIFIER-PLACEHOLDER"
NONCE = "NONCE-PLACEHOLDER"
SENTINELS = (
    CLIENT_SECRET,
    CODE,
    VERIFIER,
    "ACCESS-TOKEN-PLACEHOLDER",
    "REFRESH-TOKEN-PLACEHOLDER",
    "ERROR-DESCRIPTION-SENTINEL",
    "TRACE-SENTINEL",
    "CORR-SENTINEL",
)


def fixture(name: str) -> dict:
    return json.loads((FIXTURES / f"{name}__replace_with_m0_capture.json").read_text())


def jwt(claims: dict) -> str:
    def part(value: dict) -> str:
        return base64.urlsafe_b64encode(json.dumps(value).encode()).decode().rstrip("=")

    return f"{part({'alg': 'RS256'})}.{part(claims)}.signature-not-checked"


def claims_for(kind: str = "personal", **changes) -> dict:
    claims = fixture(f"documented_id_token_claims_{kind}")
    claims.update(
        aud=CLIENT_ID, nonce=NONCE, iat=int(NOW.timestamp()) - 5, exp=int(NOW.timestamp()) + 3600
    )
    claims.update(changes)
    return {k: v for k, v in claims.items() if v is not None}


def settings_for(**overrides):
    values = {
        "microsoft_client_id": CLIENT_ID,
        "microsoft_client_secret": CLIENT_SECRET,
        "microsoft_authority": "consumers",
        "public_base_url": "https://notes.example.test",
    }
    return make_settings(**{**values, **overrides})


class Recorder:
    """A MockTransport handler that records every request and answers from a list."""

    def __init__(self, *answers: httpx.Response | Exception) -> None:
        self.answers = list(answers)
        self.requests: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        answer = self.answers.pop(0) if len(self.answers) > 1 else self.answers[0]
        if isinstance(answer, Exception):
            raise answer
        return answer

    @property
    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self)

    def form(self, index: int = 0) -> dict[str, str]:
        return {k: v[0] for k, v in parse_qs(self.requests[index].content.decode()).items()}


def reply(body: dict | str, status: int = 200) -> httpx.Response:
    if isinstance(body, str):
        return httpx.Response(status, text=body)
    return httpx.Response(status, json=body)


def code_answer(kind: str = "personal", claims: dict | None = None, **extra) -> httpx.Response:
    body = fixture(f"documented_token_response_{'personal' if kind == 'personal' else 'work'}")
    body["id_token"] = jwt(claims if claims is not None else claims_for(kind))
    body.update(extra)
    return reply({k: v for k, v in body.items() if v is not None})


def exchange(recorder: Recorder, settings=None, **kwargs):
    client = MicrosoftOAuth(settings or settings_for(), transport=recorder.transport)
    return client.exchange_connect(code=CODE, code_verifier=VERIFIER, nonce=NONCE, now=NOW)


# ---- the consent URL ----


def test_authorize_url_asks_for_exactly_the_documented_parameters():
    """Spec 4.1: the `consumers` endpoint, code flow, PKCE S256, query response, the account
    chooser, our one registered redirect, the five scopes, no login hint."""
    verifier, challenge = auth_pkce()
    url = MicrosoftOAuth(settings_for()).authorize_url(
        state="STATE-1", nonce="NONCE-1", code_challenge=challenge
    )
    parts = urlsplit(url)
    assert f"{parts.scheme}://{parts.netloc}{parts.path}" == (
        "https://login.microsoftonline.com/consumers/oauth2/v2.0/authorize"
    )
    query = {k: v[0] for k, v in parse_qs(parts.query).items()}
    assert query == {
        "client_id": CLIENT_ID,
        "response_type": "code",
        "redirect_uri": "https://notes.example.test/api/calendar/microsoft/callback",
        "response_mode": "query",
        "scope": (
            "openid profile offline_access https://graph.microsoft.com/User.Read "
            "https://graph.microsoft.com/Calendars.Read"
        ),
        "state": "STATE-1",
        "nonce": "NONCE-1",
        "code_challenge": challenge,
        "code_challenge_method": "S256",
        "prompt": "select_account",
    }
    assert "login_hint" not in url and CLIENT_SECRET not in url and verifier not in url


def test_the_authority_setting_picks_the_endpoint():
    url = MicrosoftOAuth(settings_for(microsoft_authority="common")).authorize_url(
        state="s", nonce="n", code_challenge="c"
    )
    assert urlsplit(url).path == "/common/oauth2/v2.0/authorize"


def test_the_redirect_uri_is_the_registered_string_without_a_trailing_slash():
    settings = settings_for(public_base_url="https://notes.example.test/")
    assert microsoft_oauth.redirect_uri(settings) == (
        "https://notes.example.test/api/calendar/microsoft/callback"
    )


def test_the_only_scopes_asked_for_are_the_five_of_spec_4_2():
    assert microsoft_oauth.SCOPES == (
        "openid",
        "profile",
        "offline_access",
        "https://graph.microsoft.com/User.Read",
        "https://graph.microsoft.com/Calendars.Read",
    )


def auth_pkce():
    from app.google_oauth import new_pkce

    return new_pkce()


# ---- redeeming the code ----


def test_a_personal_account_connects(caplog):
    """Documented shape: the personal token answer with a short scope list and the ID token claims
    of a `consumers` sign-in (`tid` is the consumer tenant)."""
    recorder = Recorder(code_answer("personal"))
    grant = exchange(recorder)
    assert grant.identity.tenant_id == CONSUMER_TENANT
    assert grant.identity.subject == "00000000-0000-0000-0000-000000000001"
    assert grant.identity.account_kind == "personal"
    assert grant.identity.name == "Person One"
    assert grant.scopes == frozenset({"Calendars.Read", "User.Read", "profile", "openid", "email"})
    assert microsoft_oauth.has_calendar_scope(grant.scopes)
    assert grant.refresh_token.reveal() == "REFRESH-TOKEN-PLACEHOLDER-one"
    assert grant.access_token.reveal() == "ACCESS-TOKEN-PLACEHOLDER-one"
    assert grant.access_lifetime == timedelta(seconds=3599)


def test_the_redemption_request_is_a_post_with_everything_in_the_form_body():
    recorder = Recorder(code_answer("personal"))
    exchange(recorder)
    (request,) = recorder.requests
    assert request.method == "POST"
    assert str(request.url) == "https://login.microsoftonline.com/consumers/oauth2/v2.0/token"
    assert request.url.query == b""  # nothing secret in the URL
    assert recorder.form() == {
        "client_id": CLIENT_ID,
        "client_secret": CLIENT_SECRET,
        "scope": " ".join(microsoft_oauth.SCOPES),
        "grant_type": "authorization_code",
        "code": CODE,
        "redirect_uri": "https://notes.example.test/api/calendar/microsoft/callback",
        "code_verifier": VERIFIER,
    }


def test_a_work_account_connects_when_the_authority_allows_it():
    """Documented shape: the work token answer lists scopes as full Graph URLs."""
    recorder = Recorder(code_answer("work"))
    grant = exchange(recorder, settings_for(microsoft_authority="common"))
    assert grant.identity.tenant_id == WORK_TENANT
    assert grant.identity.account_kind == "work"
    assert microsoft_oauth.has_calendar_scope(grant.scopes)
    assert str(recorder.requests[0].url).startswith(
        "https://login.microsoftonline.com/common/oauth2/v2.0/token"
    )


def test_the_subject_is_the_oid_and_falls_back_to_sub():
    """ASSUMED: a personal-account token may lack `oid`. The M0 run settles it."""
    recorder = Recorder(code_answer("personal", claims_for("personal", oid=None)))
    assert exchange(recorder).identity.subject == "SUB-PLACEHOLDER-personal"


def test_the_email_style_claims_are_labels_and_never_the_identity():
    recorder = Recorder(
        code_answer("personal", claims_for("personal", preferred_username="attacker@example.test"))
    )
    identity = exchange(recorder).identity
    assert identity.subject == "00000000-0000-0000-0000-000000000001"
    assert identity.preferred_username == "attacker@example.test"  # shown at most, decides nothing


def test_the_refresh_token_may_be_absent():
    recorder = Recorder(code_answer("personal", refresh_token=None))
    assert exchange(recorder).refresh_token is None


def test_an_oversize_id_subject_is_refused():
    recorder = Recorder(code_answer("personal", claims_for("personal", oid="x" * 65, sub=None)))
    with pytest.raises(MicrosoftError):
        exchange(recorder)


ID_TOKEN_REFUSALS = {
    "audience of another app": {"aud": "another-client-id"},
    "expired": {"exp": int(NOW.timestamp()) - 3600},
    "issued in the future": {"iat": int(NOW.timestamp()) + 600},
    "wrong nonce": {"nonce": "somebody-elses-nonce"},
    "no nonce": {"nonce": None},
    "tid is not a GUID": {"tid": "consumers"},
    "no tid": {"tid": None},
    "issuer of another tenant": {"iss": f"https://login.microsoftonline.com/{OTHER_TENANT}/v2.0"},
    "issuer on another host": {"iss": f"https://login.evil.example/{CONSUMER_TENANT}/v2.0"},
    "v1 issuer": {"iss": f"https://sts.windows.net/{CONSUMER_TENANT}/"},
    "no oid and no sub": {"oid": None, "sub": None},
    "integer timestamps are strings": {"exp": "9999999999"},
    "boolean timestamp": {"iat": True},
}


@pytest.mark.parametrize("change", list(ID_TOKEN_REFUSALS.values()), ids=list(ID_TOKEN_REFUSALS))
def test_an_id_token_that_fails_a_check_is_refused_and_nothing_is_returned(change, caplog):
    claims = claims_for("personal", **change)
    caplog.set_level(logging.WARNING)
    with pytest.raises(MicrosoftError) as raised:
        exchange(Recorder(code_answer("personal", claims)))
    assert not isinstance(raised.value, TenantNotAllowed)
    assert str(raised.value) == ""  # no detail to leak
    assert "microsoft id token refused" in caplog.text


def test_an_expired_token_inside_the_clock_skew_still_passes():
    claims = claims_for("personal", exp=int(NOW.timestamp()) - 30)
    assert exchange(Recorder(code_answer("personal", claims))).identity.account_kind == "personal"


@pytest.mark.parametrize("token", ["not-a-jwt", "a.b", "a.%%%.c", f"a.{'e30'}.c"])
def test_a_malformed_id_token_is_refused(token):
    body = fixture("documented_token_response_personal")
    body["id_token"] = token
    with pytest.raises(MicrosoftError):
        exchange(Recorder(reply(body)))


def test_an_id_token_that_is_not_an_object_is_refused():
    body = fixture("documented_token_response_personal")
    body["id_token"] = "a." + base64.urlsafe_b64encode(b"[1,2]").decode() + ".c"
    with pytest.raises(MicrosoftError):
        exchange(Recorder(reply(body)))


def test_the_pilot_authority_refuses_a_work_tenant_even_with_a_valid_token():
    """Defence in depth: `consumers` can only ever return the personal tenant, so a work `tid`
    here means something is wrong and nothing is stored."""
    recorder = Recorder(code_answer("work"))
    with pytest.raises(TenantNotAllowed):
        exchange(recorder, settings_for(microsoft_authority="consumers"))


def test_a_tenant_outside_the_allow_list_is_refused():
    allowed = settings_for(microsoft_authority="common", microsoft_allowed_tenants=OTHER_TENANT)
    with pytest.raises(TenantNotAllowed):
        exchange(Recorder(code_answer("work")), allowed)


def test_a_tenant_on_the_allow_list_is_accepted():
    allowed = settings_for(
        microsoft_authority="common",
        microsoft_allowed_tenants=f"{OTHER_TENANT.upper()}, {WORK_TENANT}",
    )
    assert exchange(Recorder(code_answer("work")), allowed).identity.tenant_id == WORK_TENANT


def test_the_allow_list_applies_to_personal_accounts_too():
    allowed = settings_for(microsoft_authority="common", microsoft_allowed_tenants=WORK_TENANT)
    with pytest.raises(TenantNotAllowed):
        exchange(Recorder(code_answer("personal")), allowed)


def test_the_tenant_refusal_never_logs_the_tenant(caplog):
    caplog.set_level(logging.WARNING)
    allowed = settings_for(microsoft_authority="common", microsoft_allowed_tenants=OTHER_TENANT)
    with pytest.raises(TenantNotAllowed):
        exchange(Recorder(code_answer("work")), allowed)
    assert WORK_TENANT not in caplog.text and "tenant not allowed" in caplog.text


@pytest.mark.parametrize(
    "answer",
    [
        reply(fixture("documented_error_invalid_grant"), 400),
        reply({"error": "invalid_request"}, 400),
        reply("<html>gateway</html>", 502),
        reply("not json"),
        reply([1, 2]),
        httpx.ConnectTimeout("boom: https://login.microsoftonline.com/?code=" + CODE),
    ],
    ids=[
        "invalid_grant",
        "invalid_request",
        "gateway html",
        "200 not json",
        "200 array",
        "timeout",
    ],
)
def test_a_failed_redemption_is_a_plain_microsoft_error(answer, caplog):
    caplog.set_level(logging.DEBUG)
    with pytest.raises(MicrosoftError) as raised:
        exchange(Recorder(answer))
    assert type(raised.value) is MicrosoftError
    assert str(raised.value) == ""
    for sentinel in SENTINELS:
        assert sentinel not in caplog.text, sentinel


def test_an_expired_client_secret_at_redemption_is_named_for_the_alert():
    answer = reply(fixture("documented_error_secret_expired_7000222"), 401)
    with pytest.raises(ClientMisconfigured) as raised:
        exchange(Recorder(answer))
    assert raised.value.secret_expired is True
    assert isinstance(raised.value, MicrosoftError)


def test_another_invalid_client_is_not_called_an_expiry():
    answer = reply({"error": "invalid_client", "error_codes": [7000215]}, 401)
    with pytest.raises(ClientMisconfigured) as raised:
        exchange(Recorder(answer))
    assert raised.value.secret_expired is False


TOKEN_ANSWER_REFUSALS = {
    "no id token": {"id_token": None},
    "no scope": {"scope": None},
    "not a bearer token": {"token_type": "mac"},
    "no access token": {"access_token": None},
    "access token not a string": {"access_token": 123},
    "empty access token": {"access_token": ""},
    "huge access token": {"access_token": "a" * 20000},
    "huge refresh token": {"refresh_token": "r" * 9000},
    "refresh token not a string": {"refresh_token": ["x"]},
}


@pytest.mark.parametrize(
    "change", list(TOKEN_ANSWER_REFUSALS.values()), ids=list(TOKEN_ANSWER_REFUSALS)
)
def test_a_malformed_token_answer_is_refused(change):
    body = fixture("documented_token_response_personal")
    body["id_token"] = jwt(claims_for("personal"))
    body.update(change)
    with pytest.raises(MicrosoftError):
        exchange(Recorder(reply({k: v for k, v in body.items() if v is not None})))


def test_a_string_lifetime_is_read_and_a_missing_one_defaults_to_an_hour():
    recorder = Recorder(code_answer("personal", expires_in="1800"))
    assert exchange(recorder).access_lifetime == timedelta(minutes=30)
    recorder = Recorder(code_answer("personal", expires_in=None))
    assert exchange(recorder).access_lifetime == timedelta(hours=1)
    recorder = Recorder(code_answer("personal", expires_in=-5))
    assert exchange(recorder).access_lifetime == timedelta(hours=1)


def test_nothing_secret_reaches_a_log_a_repr_or_an_error_on_the_success_path(caplog):
    caplog.set_level(logging.DEBUG)
    recorder = Recorder(code_answer("personal"))
    oauth = MicrosoftOAuth(settings_for(), transport=recorder.transport)
    grant = oauth.exchange_connect(code=CODE, code_verifier=VERIFIER, nonce=NONCE, now=NOW)
    shown = repr(grant) + str(grant) + repr(oauth) + repr(grant.refresh_token) + caplog.text
    for sentinel in SENTINELS:
        assert sentinel not in shown, sentinel
    assert isinstance(grant.refresh_token, Secret) and isinstance(grant.access_token, Secret)


# ---- refreshing, with rotation ----


def refresh(recorder: Recorder, tenant: str = CONSUMER_TENANT, token: str = "OLD-REFRESH-TOKEN"):
    client = MicrosoftOAuth(settings_for(), transport=recorder.transport)
    return client.refresh(Secret(token), tenant_id=tenant)


def test_a_refresh_returns_the_new_refresh_token_to_be_saved():
    """Documented shape: every refresh returns a NEW refresh token (spec 4.3). The caller saves it
    and discards the old one; this method touches no database."""
    recorder = Recorder(reply(fixture("documented_refresh_response")))
    result = refresh(recorder)
    assert result.access_token.reveal() == "ACCESS-TOKEN-PLACEHOLDER-two"
    assert result.refresh_token.reveal() == "REFRESH-TOKEN-PLACEHOLDER-two"
    assert result.refresh_token.reveal() != "OLD-REFRESH-TOKEN"
    assert result.lifetime == timedelta(seconds=3599)
    assert microsoft_oauth.has_calendar_scope(result.scopes)


def test_the_refresh_request_uses_the_connections_own_tenant_and_the_form_body():
    recorder = Recorder(reply(fixture("documented_refresh_response")))
    refresh(recorder, tenant=WORK_TENANT.upper())
    (request,) = recorder.requests
    assert str(request.url) == f"https://login.microsoftonline.com/{WORK_TENANT}/oauth2/v2.0/token"
    assert request.url.query == b""
    form = recorder.form()
    assert form["grant_type"] == "refresh_token"
    assert form["refresh_token"] == "OLD-REFRESH-TOKEN"
    assert form["client_secret"] == CLIENT_SECRET and form["client_id"] == CLIENT_ID
    assert "code" not in form and "code_verifier" not in form


@pytest.mark.parametrize("tenant", ["common", "consumers", "organizations", "", "../x", "a" * 36])
def test_a_refresh_never_goes_to_common_or_to_a_made_up_tenant(tenant):
    recorder = Recorder(reply(fixture("documented_refresh_response")))
    with pytest.raises(ValueError):
        refresh(recorder, tenant=tenant)
    assert recorder.requests == []


def test_the_id_token_in_a_refresh_answer_is_never_read():
    """Microsoft calls it unsigned and not for security decisions: even a garbage one is ignored."""
    body = fixture("documented_refresh_response")
    body["id_token"] = "garbage"
    assert refresh(Recorder(reply(body))).access_token.reveal().startswith("ACCESS-TOKEN")


def test_a_refresh_with_no_new_refresh_token_says_so():
    body = fixture("documented_refresh_response")
    del body["refresh_token"]
    assert refresh(Recorder(reply(body))).refresh_token is None


def test_a_refresh_answer_without_a_scope_gives_an_empty_set():
    body = fixture("documented_refresh_response")
    del body["scope"]
    assert refresh(Recorder(reply(body))).scopes == frozenset()


REFRESH_FAILURES = [
    ("invalid_grant", 400, "documented_error_invalid_grant", AuthRevoked, False),
    ("idle expiry 700082", 400, "documented_error_refresh_idle_700082", AuthRevoked, True),
    ("interaction_required", 400, "documented_error_interaction_required", AuthRevoked, False),
    ("consent_required", 400, None, AuthRevoked, False),
    ("invalid_grant as 401", 401, "documented_error_invalid_grant", AuthRevoked, False),
]


@pytest.mark.parametrize(
    "status,fixture_name,expected,expired",
    [(s, f, e, x) for _, s, f, e, x in REFRESH_FAILURES],
    ids=[r[0] for r in REFRESH_FAILURES],
)
def test_a_dead_grant_is_auth_revoked_and_only_700082_is_expired(
    status, fixture_name, expected, expired
):
    body = fixture(fixture_name) if fixture_name else {"error": "consent_required"}
    with pytest.raises(expected) as raised:
        refresh(Recorder(reply(body, status)))
    assert raised.value.expired is expired
    for sentinel in SENTINELS:
        assert sentinel not in str(raised.value), sentinel


def test_the_reason_shown_for_a_dead_grant_follows_the_expired_flag():
    conn = type("Conn", (), {"expected_expiry_at": None})()
    assert calendar_states.reason_for(conn, AuthRevoked("x"), NOW, testing=False) == "revoked"
    assert (
        calendar_states.reason_for(conn, AuthRevoked("x", expired=True), NOW, testing=False)
        == "expired"
    )
    assert (
        calendar_states.reason_for(conn, RefreshTokenUnreadable("x"), NOW, testing=False) == "error"
    )


def test_an_expired_client_secret_is_transient_for_the_user_and_named_for_the_alert():
    answer = reply(fixture("documented_error_secret_expired_7000222"), 401)
    with pytest.raises(ClientRejected) as raised:
        refresh(Recorder(answer))
    assert isinstance(raised.value, TransientError) and not isinstance(raised.value, AuthRevoked)
    assert raised.value.secret_expired is True
    assert "ERROR-DESCRIPTION-SENTINEL" not in str(raised.value)
    assert "7000222" in str(raised.value)  # the numeric code is safe and useful


def test_a_wrong_client_secret_is_rejected_without_claiming_an_expiry():
    with pytest.raises(ClientRejected) as raised:
        refresh(Recorder(reply({"error": "invalid_client", "error_codes": [7000215]}, 401)))
    assert raised.value.secret_expired is False


@pytest.mark.parametrize(
    "answer",
    [
        reply({"error": "temporarily_unavailable", "error_codes": [90033]}, 503),
        reply({"error": "server_error"}, 500),
        reply("slow down", 429),
        reply("<html>bad gateway</html>", 502),
        reply({"error": "invalid_request"}, 400),
        reply({"error": "unauthorized_client"}, 400),
        reply({"error": "invalid_grant"}, 500),  # a 500 is never a verdict on the grant
        reply({"token_type": "Bearer"}),  # 200 without an access token
        reply({"access_token": 5}),
        reply({"access_token": "x" * 20000}),
        reply({"access_token": "ok", "refresh_token": ["list"]}),
        reply("not json"),
        reply([1]),
        httpx.ReadTimeout("slow"),
    ],
    ids=[
        "temporarily_unavailable",
        "server_error",
        "429",
        "502 html",
        "invalid_request",
        "unauthorized_client",
        "invalid_grant on 500",
        "no access token",
        "access token number",
        "access token huge",
        "refresh token list",
        "not json",
        "array",
        "timeout",
    ],
)
def test_everything_else_is_transient_and_the_connection_keeps_its_state(answer):
    with pytest.raises(TransientError) as raised:
        refresh(Recorder(answer))
    assert not isinstance(raised.value, AuthRevoked)
    for sentinel in SENTINELS:
        assert sentinel not in str(raised.value), sentinel


def test_error_strings_that_are_not_plain_names_are_not_echoed():
    body = {
        "error": "Bad Thing: <script>https://evil.example/?code=1</script>",
        "error_codes": [1, "x", True, 2.5],
    }
    with pytest.raises(TransientError) as raised:
        refresh(Recorder(reply(body, 400)))
    assert "evil" not in str(raised.value) and "script" not in str(raised.value)
    assert "codes [1]" in str(raised.value)  # only real integers survive


def test_the_http_client_follows_no_redirects_and_reads_no_proxy(monkeypatch):
    """A redirect from the token endpoint must not carry the form (with the secret) elsewhere."""
    monkeypatch.setenv("HTTPS_PROXY", "http://proxy.invalid:3128")
    recorder = Recorder(httpx.Response(302, headers={"Location": "https://evil.example/"}))
    with pytest.raises(TransientError):
        refresh(recorder)
    assert len(recorder.requests) == 1


# ---- scopes ----


@pytest.mark.parametrize(
    "scopes,expected",
    [
        ("Calendars.Read User.Read profile openid email", True),
        ("https://graph.microsoft.com/Calendars.Read https://graph.microsoft.com/User.Read", True),
        ("https://graph.microsoft.com/calendars.read", True),
        ("HTTPS://GRAPH.MICROSOFT.COM/CALENDARS.READ", True),
        ("calendars.read", True),
        ({"Calendars.Read"}, True),
        (frozenset({"User.Read", "https://graph.microsoft.com/Calendars.Read"}), True),
        ("Calendars.ReadBasic User.Read", False),
        ("Calendars.ReadWrite", False),
        ("Calendars.Read.Shared", False),
        ("https://graph.example.test/Calendars.Read", False),
        ("MyCalendars.Read", False),
        ("User.Read openid", False),
        ("", False),
        (None, False),
        (set(), False),
    ],
)
def test_the_calendar_scope_is_recognised_exactly(scopes, expected):
    assert microsoft_oauth.has_calendar_scope(scopes) is expected


# ---- settings and start-up ----


def test_the_authority_must_be_consumers_or_common():
    assert make_settings(microsoft_authority=" Common ").microsoft_authority == "common"
    assert make_settings(microsoft_authority="").microsoft_authority == "consumers"
    for bad in ("organizations", "contoso.onmicrosoft.com", WORK_TENANT):
        with pytest.raises(ValueError, match="consumers or common"):
            make_settings(microsoft_authority=bad)


def test_allowed_tenants_are_normalised_and_must_be_guids():
    settings = make_settings(
        microsoft_allowed_tenants=f" {WORK_TENANT.upper()},{OTHER_TENANT},,{WORK_TENANT}"
    )
    assert settings.microsoft_allowed_tenant_ids == {WORK_TENANT, OTHER_TENANT}
    assert make_settings().microsoft_allowed_tenant_ids == frozenset()
    with pytest.raises(ValueError, match="tenant ids"):
        make_settings(microsoft_allowed_tenants="contoso.com")


def test_the_secret_expiry_is_a_date_and_the_reminder_window_is_thirty_days():
    assert microsoft_oauth.secret_expiry(make_settings()) is None
    assert not microsoft_oauth.secret_expiry_due(make_settings(), date(2030, 1, 1))
    settings = make_settings(microsoft_secret_expires_on="2026-12-09")
    assert microsoft_oauth.secret_expiry(settings) == date(2026, 12, 9)
    assert not microsoft_oauth.secret_expiry_due(settings, date(2026, 11, 8))
    assert microsoft_oauth.secret_expiry_due(settings, date(2026, 11, 9))  # 30 days to go
    assert microsoft_oauth.secret_expiry_due(settings, date(2026, 12, 20))  # already expired
    for bad in ("12/09/2026", "2026-13-01", "soon"):
        with pytest.raises(ValueError, match="YYYY-MM-DD"):
            make_settings(microsoft_secret_expires_on=bad)


def test_the_client_exists_only_with_both_halves():
    assert microsoft_oauth.enabled(settings_for())
    assert not microsoft_oauth.enabled(settings_for(microsoft_client_secret=""))
    assert not microsoft_oauth.enabled(settings_for(microsoft_client_id=""))
    assert not microsoft_oauth.enabled(make_settings())


def production(**overrides):
    return make_settings(app_env="production", cookie_secure=True, token_enc_key=KEY, **overrides)


KEY = base64.b64encode(b"K" * 32).decode()


def test_production_refuses_a_client_id_without_a_secret():
    with pytest.raises(RuntimeError, match="MICROSOFT_CLIENT_SECRET"):
        auth.check_start(production(microsoft_client_id=CLIENT_ID))


def test_production_refuses_the_flag_without_a_client():
    with pytest.raises(RuntimeError, match="MICROSOFT_CALENDAR_ENABLED"):
        auth.check_start(production(microsoft_calendar_enabled=True))
    with pytest.raises(RuntimeError, match="MICROSOFT_CLIENT_SECRET"):  # half a client
        auth.check_start(production(microsoft_calendar_enabled=True, microsoft_client_id=CLIENT_ID))


def test_production_refuses_a_client_without_the_token_key():
    with pytest.raises(RuntimeError, match="TOKEN_ENC_KEY"):
        auth.check_start(
            make_settings(
                app_env="production",
                cookie_secure=True,
                microsoft_client_id=CLIENT_ID,
                microsoft_client_secret=CLIENT_SECRET,
            )
        )


def test_production_starts_with_a_full_client_and_with_nothing_at_all():
    auth.check_start(
        production(microsoft_client_id=CLIENT_ID, microsoft_client_secret=CLIENT_SECRET)
    )
    auth.check_start(
        production(
            microsoft_client_id=CLIENT_ID,
            microsoft_client_secret=CLIENT_SECRET,
            microsoft_calendar_enabled=True,
        )
    )
    auth.check_start(production())  # all Microsoft settings are optional while the flag is off


def test_development_and_test_start_without_the_client():
    auth.check_start(make_settings(microsoft_client_id=CLIENT_ID))
    auth.check_start(make_settings(microsoft_calendar_enabled=True))


# ---- the vault purpose ----


@pytest.fixture
def vault_key(monkeypatch):
    settings = make_settings(token_enc_key=KEY)
    monkeypatch.setattr(vault, "get_settings", lambda: settings)


def test_a_microsoft_token_round_trips_under_its_own_purpose(vault_key):
    blob, version = vault.encrypt("REFRESH-TOKEN-PLACEHOLDER", purpose="ms_refresh_token", owner=7)
    secret = vault.decrypt(blob, version, purpose="ms_refresh_token", owner=7)
    assert secret.reveal() == "REFRESH-TOKEN-PLACEHOLDER"


def test_a_google_blob_in_a_microsoft_row_fails_to_open_and_the_reverse(vault_key):
    google_blob, version = vault.encrypt("a-google-token", purpose="refresh_token", owner=7)
    with pytest.raises(vault.VaultError):
        vault.decrypt(google_blob, version, purpose="ms_refresh_token", owner=7)
    ms_blob, version = vault.encrypt("a-microsoft-token", purpose="ms_refresh_token", owner=7)
    with pytest.raises(vault.VaultError):
        vault.decrypt(ms_blob, version, purpose="refresh_token", owner=7)


def test_a_microsoft_blob_copied_to_another_user_fails_to_open(vault_key):
    blob, version = vault.encrypt("a-microsoft-token", purpose="ms_refresh_token", owner=7)
    with pytest.raises(vault.VaultError):
        vault.decrypt(blob, version, purpose="ms_refresh_token", owner=8)


def test_the_dependency_gives_a_client_only_when_configured():
    class State:
        pass

    def request_with(settings):
        app = type("App", (), {"state": State()})()
        app.state.settings = settings
        return type("Request", (), {"app": app})()

    assert microsoft_oauth.get_microsoft(request_with(make_settings())) is None
    assert isinstance(microsoft_oauth.get_microsoft(request_with(settings_for())), MicrosoftOAuth)
