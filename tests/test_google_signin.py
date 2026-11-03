"""Sign in with Google: the OAuth client (PKCE, ID-token checks) and the redirect flow with its
matching order (spec 4.3, 4.4, 12.1)."""

import base64
import hashlib
import json
import logging
import time
from datetime import UTC, datetime, timedelta
from urllib.parse import parse_qs, urlparse

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app import auth, google_oauth, google_signin, signup, tokens
from app.db import get_session
from app.google_oauth import GoogleError, Identity
from app.main import create_app
from app.models import (
    AuthToken,
    MailOutbox,
    OAuthFlow,
    Template,
    User,
    UserSettings,
    WaitlistEntry,
)
from app.services import get_openai_client, get_vexa
from tests.test_api import Api, error_of
from tests.test_signup import ATTACKER, GOOD, open_pilot
from tests.test_signup import form as signup_form
from tests.test_web import (  # noqa: F401
    app,
    fake_openai,
    fake_vexa,
    make_settings,
    make_user,
)

NOW = datetime(2026, 11, 3, 12, 0, tzinfo=UTC)
CLIENT_ID = "test-client-id.apps.example.test"
ADA = Identity(sub="g-ada-1", email="ada@example.test", name="Ada Lovelace")


# ---- the OAuth client ----


def id_token(**over) -> str:
    claims = {
        "iss": "https://accounts.google.com",
        "aud": CLIENT_ID,
        "sub": "g-ada-1",
        "email": "ada@example.test",
        "email_verified": True,
        "name": "Ada Lovelace",
        "nonce": "the-nonce",
        "iat": int(NOW.timestamp()) - 10,
        "exp": int(NOW.timestamp()) + 3000,
    } | over
    claims = {k: v for k, v in claims.items() if v is not ...}  # `...` removes a claim

    def part(data) -> str:
        return base64.urlsafe_b64encode(json.dumps(data).encode()).decode().rstrip("=")

    return f"{part({'alg': 'RS256'})}.{part(claims)}.c2ln"


def oauth(handler, **over) -> google_oauth.GoogleOAuth:
    settings = make_settings(google_client_id=CLIENT_ID, google_client_secret="s3cret", **over)
    return google_oauth.GoogleOAuth(settings, transport=httpx.MockTransport(handler))


def exchange(client, **over):
    args = {"code": "the-code", "code_verifier": "v" * 64, "nonce": "the-nonce", "now": NOW}
    return client.exchange(**args | over)


def answer(token, status=200):
    return lambda request: httpx.Response(status, json={"id_token": token, "access_token": "x"})


def test_google_sign_in_exists_only_with_both_halves_of_the_client():
    assert not google_oauth.enabled(make_settings())
    assert not google_oauth.enabled(make_settings(google_client_id="a"))
    assert not google_oauth.enabled(make_settings(google_client_secret="b"))
    assert google_oauth.enabled(make_settings(google_client_id="a", google_client_secret="b"))


def test_the_consent_url_asks_for_identity_only_with_pkce():
    url = oauth(answer(id_token())).authorize_url(state="st", nonce="no", code_challenge="ch")
    parsed = urlparse(url)
    query = {k: v[0] for k, v in parse_qs(parsed.query).items()}
    assert f"{parsed.scheme}://{parsed.netloc}{parsed.path}" == (
        "https://accounts.google.com/o/oauth2/v2/auth"
    )
    assert query == {
        "client_id": CLIENT_ID,
        "redirect_uri": "https://notes.beyrictech.com/api/auth/google/callback",
        "response_type": "code",
        "scope": "openid email profile",
        "state": "st",
        "nonce": "no",
        "code_challenge": "ch",
        "code_challenge_method": "S256",
        "prompt": "select_account",
    }


def test_pkce_challenge_is_the_s256_of_a_long_verifier():
    verifier, challenge = google_oauth.new_pkce()
    assert 43 <= len(verifier) <= 128
    expected = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest())
    assert challenge == expected.decode().rstrip("=")
    assert google_oauth.new_pkce()[0] != verifier


def test_the_code_is_exchanged_with_the_verifier_and_the_identity_comes_back():
    seen = {}

    def handler(request):
        seen["url"] = str(request.url)
        seen["form"] = {k: v[0] for k, v in parse_qs(request.content.decode()).items()}
        return httpx.Response(200, json={"id_token": id_token()})

    identity = exchange(oauth(handler))
    assert identity == Identity("g-ada-1", "ada@example.test", "Ada Lovelace")
    assert seen["url"] == "https://oauth2.googleapis.com/token"
    assert seen["form"] == {
        "code": "the-code",
        "client_id": CLIENT_ID,
        "client_secret": "s3cret",
        "redirect_uri": "https://notes.beyrictech.com/api/auth/google/callback",
        "grant_type": "authorization_code",
        "code_verifier": "v" * 64,
    }


def test_a_missing_name_is_none():
    assert exchange(oauth(answer(id_token(name=...)))).name is None


@pytest.mark.parametrize(
    "claims",
    [
        {"iss": "https://evil.example"},
        {"aud": "someone-elses-client"},
        {"aud": ["a", CLIENT_ID]},
        {"exp": int(NOW.timestamp()) - 120},
        {"exp": "soon"},
        {"iat": int(NOW.timestamp()) + 600},
        {"iat": ...},
        {"nonce": "other"},
        {"nonce": ...},
        {"sub": ...},
        {"sub": ""},
        {"sub": "x" * 65},
        {"email": ...},
        {"email_verified": False},
        {"email_verified": "true"},
        {"email_verified": ...},
    ],
)
def test_an_id_token_that_fails_a_check_is_refused(claims):
    with pytest.raises(GoogleError):
        exchange(oauth(answer(id_token(**claims))))


def test_both_google_issuer_spellings_are_accepted():
    assert exchange(oauth(answer(id_token(iss="accounts.google.com")))).sub == "g-ada-1"


@pytest.mark.parametrize("token", ["", "a.b", "a.b.c.d", "a.!!!.c", "a.bnVsbA.c", "a.W10.c"])
def test_a_malformed_token_is_refused(token):
    with pytest.raises(GoogleError):
        exchange(oauth(answer(token)))


def test_a_refused_code_or_a_broken_reply_is_an_error_with_no_detail(caplog):
    caplog.set_level(logging.DEBUG)
    for handler in (
        lambda r: httpx.Response(400, json={"error": "invalid_grant"}),
        lambda r: httpx.Response(200, text="not json the-code"),
        lambda r: httpx.Response(200, json={"access_token": "only"}),
        lambda r: (_ for _ in ()).throw(httpx.ConnectError("boom the-code")),
    ):
        with pytest.raises(GoogleError) as caught:
            exchange(oauth(handler))
        assert str(caught.value) == ""
    assert "the-code" not in caplog.text and "s3cret" not in caplog.text


def test_each_exchange_closes_its_http_client(monkeypatch):
    """A client per request that is never closed leaks sockets; each call closes its own."""
    made = []
    real = httpx.Client

    class Recording(real):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            made.append(self)

    monkeypatch.setattr(google_oauth.httpx, "Client", Recording)
    client = oauth(answer(id_token()))
    exchange(client)
    with pytest.raises(GoogleError):
        exchange(oauth(lambda r: httpx.Response(400, json={})))
    assert made and all(c.is_closed for c in made)


# ---- the flow ----


class FakeGoogle:
    def __init__(self, identity: Identity | None = ADA):
        self.identity = identity
        self.exchanges: list[dict] = []

    def authorize_url(self, *, state, nonce, code_challenge):
        return f"https://accounts.google.test/auth?state={state}&nonce={nonce}&cc={code_challenge}"

    def exchange(self, *, code, code_verifier, nonce, now):
        self.exchanges.append({"code": code, "verifier": code_verifier, "nonce": nonce})
        if self.identity is None:
            raise GoogleError
        return self.identity


@pytest.fixture
def google():
    return FakeGoogle()


@pytest.fixture
def gapp(engine, fake_vexa, fake_openai, google):
    app = create_app(make_settings(google_client_id=CLIENT_ID, google_client_secret="s3cret"))

    def session_override():
        with Session(engine, expire_on_commit=False) as s:
            yield s

    app.dependency_overrides[get_session] = session_override
    app.dependency_overrides[get_vexa] = lambda: fake_vexa
    app.dependency_overrides[get_openai_client] = lambda: fake_openai
    app.dependency_overrides[google_oauth.get_google] = lambda: google
    return app


def browser(app, address="198.51.100.1") -> Api:
    return Api(TestClient(app, follow_redirects=False, client=(address, 50000)))


def start(web: Api, query="?accept_terms=true"):
    response = web.client.get(f"/api/auth/google/start{query}")
    assert response.status_code == 302, response.text
    location = response.headers["location"]
    return response, parse_qs(urlparse(location).query)["state"][0]


def callback(web: Api, state: str, code="the-code", extra=""):
    return web.client.get(f"/api/auth/google/callback?code={code}&state={state}{extra}")


def sign_in(web: Api, query="?accept_terms=true"):
    _, state = start(web, query)
    return callback(web, state)


def logged_in(web: Api) -> dict:
    return web.get("/api/session").json()


def user_by(session, **where) -> User | None:
    session.expire_all()
    return session.scalars(select(User).filter_by(**where)).first()


def test_the_feature_is_off_without_a_client_id(app):
    web = browser(app)
    error_of(web.get("/api/auth/google/start"), 404, "google_signin_unavailable")
    error_of(web.get("/api/auth/google/callback?code=x&state=y"), 404, "google_signin_unavailable")
    assert web.get("/api/pilot").json()["google_signin"] is False


def test_the_pilot_says_when_google_sign_in_is_available(gapp):
    assert browser(gapp).get("/api/pilot").json()["google_signin"] is True


def test_start_redirects_to_google_and_keeps_a_hashed_single_use_flow(gapp, session, google):
    response, state = start(browser(gapp), "?accept_terms=true&return_to=/templates")
    assert response.headers["location"].startswith("https://accounts.google.test/auth?")
    [flow] = session.scalars(select(OAuthFlow)).all()
    assert flow.state_hash == tokens.digest(state) and state not in flow.state_hash
    assert flow.purpose == "signin" and flow.user_id is None
    assert flow.return_to == "/templates"
    assert 43 <= len(flow.code_verifier) <= 128 and flow.nonce
    challenge = parse_qs(urlparse(response.headers["location"]).query)["cc"][0]
    digest = hashlib.sha256(flow.code_verifier.encode()).digest()
    assert challenge == base64.urlsafe_b64encode(digest).decode().rstrip("=")
    assert (flow.expires_at - datetime.now(UTC)) <= timedelta(minutes=10)
    assert (flow.expires_at - datetime.now(UTC)) > timedelta(minutes=9)


@pytest.mark.parametrize(
    "target",
    [
        "//evil.test",
        "https://evil.test/",
        "/\\evil.test",
        "javascript:alert(1)",
        "evil",
        "/a b",
        "",
    ],
)
def test_an_unsafe_return_to_falls_back_to_the_home_page(gapp, session, target):
    from urllib.parse import quote

    start(browser(gapp), f"?accept_terms=true&return_to={quote(target)}")
    assert session.scalars(select(OAuthFlow)).one().return_to == "/"


def test_the_flow_cookie_is_http_only_lax_and_scoped_to_the_google_routes(gapp):
    response, _ = start(browser(gapp))
    header = next(h for h in response.headers.get_list("set-cookie") if "google_flow" in h)
    assert "HttpOnly" in header and "SameSite=lax" in header and "Path=/api/auth/google" in header


def test_a_full_sign_up_with_google_creates_a_verified_user_and_a_session(gapp, session, google):
    open_pilot(session)
    web = browser(gapp)
    before = logged_in(web)["csrf_token"]
    _, state = start(web)
    response = callback(web, state)
    assert (response.status_code, response.headers["location"]) == (302, "/")
    after = logged_in(web)
    assert after["authenticated"] and after["user"]["email"] == "ada@example.test"
    assert after["user"]["email_verified"] is True and after["csrf_token"] != before
    user = user_by(session, email="ada@example.test")
    assert (user.google_sub, user.name, user.password_hash) == ("g-ada-1", "Ada Lovelace", None)
    assert user.email_verified_at is not None and user.last_login_at is not None
    assert user.terms_accepted_at is not None and user.terms_version == signup.TERMS_VERSION
    assert (
        len(session.scalars(select(Template).where(Template.owner_user_id == user.id)).all()) == 4
    )
    assert session.get(UserSettings, user.id) is not None
    [call] = google.exchanges
    assert call["code"] == "the-code"


def test_the_code_verifier_and_nonce_sent_to_google_are_the_stored_ones(gapp, session, google):
    open_pilot(session)
    web = browser(gapp)
    sign_in(web)
    # the flow was consumed, so compare with what the fake recorded against what start stored
    [call] = google.exchanges
    assert len(call["verifier"]) >= 43 and len(call["nonce"]) >= 16


def test_the_return_to_is_where_the_person_lands(gapp, session):
    open_pilot(session)
    web = browser(gapp)
    _, state = start(web, "?accept_terms=true&return_to=/templates")
    assert callback(web, state).headers["location"] == "/templates"


def test_a_new_account_needs_the_terms_accepted_at_the_consent_text(gapp, session):
    open_pilot(session)
    web = browser(gapp)
    response = sign_in(web, "")
    assert response.headers["location"] == "/login?error=terms_required"
    assert user_by(session, email="ada@example.test") is None
    assert logged_in(web)["authenticated"] is False


def test_an_existing_account_signs_in_without_accepting_terms_again(gapp, session):
    make_user(session, "ada@example.test", password=None, google_sub="g-ada-1")
    web = browser(gapp)
    assert sign_in(web, "").headers["location"] == "/"
    assert logged_in(web)["authenticated"]


# -- the state is single use, short and tied to this browser --


def test_an_unknown_state_is_refused_and_nothing_is_created(gapp, session, google):
    open_pilot(session)
    web = browser(gapp)
    start(web)
    response = callback(web, "not-a-state")
    assert response.headers["location"] == "/login?error=google_failed"
    assert google.exchanges == [] and user_by(session, email="ada@example.test") is None


def test_a_state_works_once(gapp, session):
    open_pilot(session)
    web = browser(gapp)
    _, state = start(web)
    assert callback(web, state).headers["location"] == "/"
    again = browser(gapp, "198.51.100.2")  # same state, replayed, with the cookie it had
    again.client.cookies.set("google_flow", state, path="/api/auth/google")
    assert callback(again, state).headers["location"] == "/login?error=google_failed"
    assert session.scalars(select(OAuthFlow)).all() == []


def test_an_expired_flow_is_refused(gapp, session, google):
    open_pilot(session)
    web = browser(gapp)
    _, state = start(web)
    flow = session.scalars(select(OAuthFlow)).one()
    flow.expires_at = datetime.now(UTC) - timedelta(seconds=1)
    session.commit()
    assert callback(web, state).headers["location"] == "/login?error=google_failed"
    assert google.exchanges == []


def test_a_state_started_in_another_browser_cannot_be_completed_here(gapp, session, google):
    """Login CSRF: an attacker starts a flow, then makes the victim's browser finish it."""
    open_pilot(session)
    attacker, victim = browser(gapp, "198.51.100.1"), browser(gapp, "198.51.100.2")
    _, state = start(attacker)
    response = callback(victim, state)  # the victim's browser has no flow cookie
    assert response.headers["location"] == "/login?error=google_failed"
    assert logged_in(victim)["authenticated"] is False and google.exchanges == []


def test_a_wrong_flow_cookie_is_refused_too(gapp, session, google):
    open_pilot(session)
    web = browser(gapp)
    _, state = start(web)
    [flow_cookie] = [c for c in web.client.cookies.jar if c.name == "google_flow"]
    flow_cookie.value = "x" * 40
    assert callback(web, state).headers["location"] == "/login?error=google_failed"
    assert google.exchanges == []


def test_the_person_saying_no_at_google_is_a_clean_redirect(gapp, session, google):
    web = browser(gapp)
    _, state = start(web)
    response = web.client.get(f"/api/auth/google/callback?error=access_denied&state={state}")
    assert response.headers["location"] == "/login?error=google_denied"
    assert session.scalars(select(OAuthFlow)).all() == [] and google.exchanges == []


def test_a_failed_exchange_or_token_is_a_clean_redirect(gapp, session, google):
    open_pilot(session)
    google.identity = None
    web = browser(gapp)
    assert sign_in(web).headers["location"] == "/login?error=google_failed"
    assert user_by(session, email="ada@example.test") is None


def test_no_code_or_state_is_logged(gapp, session, caplog):
    open_pilot(session)
    caplog.set_level(logging.DEBUG)
    web = browser(gapp)
    _, state = start(web)
    callback(web, state, code="SUPERSECRETCODE")
    ours = " ".join(r.getMessage() for r in caplog.records if r.name.startswith("app"))
    assert state not in ours and "SUPERSECRETCODE" not in ours
    assert "ada@example.test" not in ours


# -- the matching order --


def test_one_a_known_google_account_signs_in_by_its_sub(gapp, session):
    make_user(session, "old-address@example.test", password=None, google_sub="g-ada-1", name="Ada")
    web = browser(gapp)
    sign_in(web)
    assert logged_in(web)["user"]["email"] == "ada@example.test"  # the stored email followed
    assert session.scalar(select(func.count()).select_from(User)) == 1


def test_a_changed_google_email_is_not_stored_when_another_user_has_it(gapp, session):
    make_user(session, "old-address@example.test", password=None, google_sub="g-ada-1")
    make_user(session, "ada@example.test")
    web = browser(gapp)
    sign_in(web)
    assert logged_in(web)["user"]["email"] == "old-address@example.test"
    assert user_by(session, email="ada@example.test").google_sub is None


def test_two_a_verified_password_account_gets_the_google_identity_linked(gapp, session):
    mine = make_user(session, "ada@example.test")
    before = mine.password_hash
    web = browser(gapp)
    sign_in(web)
    user = user_by(session, email="ada@example.test")
    assert user.id == mine.id and user.google_sub == "g-ada-1"
    assert user.password_hash == before  # the password still works
    assert logged_in(web)["user"]["email"] == "ada@example.test"
    assert session.scalar(select(func.count()).select_from(User)) == 1


def test_two_b_an_unverified_account_is_verified_linked_and_loses_its_password(gapp, session):
    """Someone may have pre-registered the address with a password they know."""
    open_pilot(session)
    planted = make_user(
        session, "ada@example.test", password=ATTACKER, email_verified_at=None, name="Planted"
    )
    tokens.issue(
        session,
        purpose="verify_email",
        email="ada@example.test",
        user_id=planted.id,
        now=datetime.now(UTC),
        pending_password_hash=planted.password_hash,
    )
    session.commit()
    web = browser(gapp)
    sign_in(web)
    user = user_by(session, id=planted.id)
    assert user.email_verified_at is not None and user.google_sub == "g-ada-1"
    assert user.password_hash is None
    assert session.scalars(select(AuthToken).where(AuthToken.used_at.is_(None))).all() == []
    fresh = browser(gapp, "198.51.100.9")
    error_of(fresh.log_in(ATTACKER, "ada@example.test"), 401, "invalid_credentials")
    assert logged_in(web)["authenticated"] is True


def test_google_cannot_confirm_an_unconfirmed_account_when_the_pilot_has_filled_up(gapp, session):
    open_pilot(session, max_accounts=1)
    make_user(session, "member@example.test")
    planted = make_user(session, "ada@example.test", password=ATTACKER, email_verified_at=None)
    web = browser(gapp)
    assert sign_in(web).headers["location"] == "/pilot-full"
    user = user_by(session, id=planted.id)
    assert user.email_verified_at is None and user.google_sub is None
    assert logged_in(web)["authenticated"] is False


def test_google_redeems_the_invite_of_an_unconfirmed_account_when_the_pilot_is_full(gapp, session):
    open_pilot(session, max_accounts=2)
    make_user(session, "a@example.test")
    make_user(session, "b@example.test")
    planted = make_user(session, "ada@example.test", password=ATTACKER, email_verified_at=None)
    open_pilot(session, max_accounts=3)
    session.add(WaitlistEntry(email="ada@example.test", status="invited"))
    tokens.issue(
        session,
        purpose="waitlist_invite",
        email="ada@example.test",
        user_id=None,
        now=datetime.now(UTC),
    )
    session.commit()
    web = browser(gapp)
    assert sign_in(web).headers["location"] == "/"
    user = user_by(session, id=planted.id)
    assert user.email_verified_at is not None and user.google_sub == "g-ada-1"
    assert session.scalars(select(WaitlistEntry)).one().status == "joined"
    assert session.scalars(select(AuthToken).where(AuthToken.used_at.is_(None))).all() == []


def test_a_confirmation_and_a_google_link_for_one_account_at_once_do_not_deadlock(
    gapp, session, monkeypatch
):
    """Both take the sign-up lock and touch the account's verification tokens. If the confirmation
    took the token row before the lock, each would wait for the other and one would get a 500."""
    import threading

    from tests.test_signup import token_of, verify

    open_pilot(session)
    owner_browser = browser(gapp, "198.51.100.1")
    assert owner_browser.post("/api/signup", signup_form()).status_code == 202
    link = token_of(session.scalars(select(MailOutbox)).first())
    real = tokens.revoke

    def slow(*args, **kwargs):  # Google holds the lock while it revokes: a wide gap
        time.sleep(0.6)
        return real(*args, **kwargs)

    monkeypatch.setattr(tokens, "revoke", slow)
    results, errors = {}, []

    def google():
        try:
            results["google"] = sign_in(browser(gapp, "198.51.100.2")).headers["location"]
        except Exception as exc:  # a deadlock surfaces as a server error
            errors.append(exc)

    def confirm():
        try:
            time.sleep(0.2)  # the Google flow already holds the lock
            results["verify"] = verify(owner_browser, link, GOOD).status_code
        except Exception as exc:
            errors.append(exc)

    threads = [threading.Thread(target=google), threading.Thread(target=confirm)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert errors == []
    assert results["google"] == "/" and results["verify"] in (200, 400)
    assert user_by(session, email="ada@example.test").email_verified_at is not None


def test_a_password_account_already_linked_to_another_google_account_is_not_relinked(gapp, session):
    make_user(session, "ada@example.test", google_sub="g-someone-else")
    web = browser(gapp)
    assert sign_in(web).headers["location"] == "/login?error=google_failed"
    assert user_by(session, email="ada@example.test").google_sub == "g-someone-else"


def test_three_a_new_google_user_is_created_with_no_password(gapp, session):
    open_pilot(session)
    web = browser(gapp)
    sign_in(web)
    assert user_by(session, email="ada@example.test").password_hash is None
    error_of(
        browser(gapp, "198.51.100.8").log_in(GOOD, "ada@example.test"), 401, "invalid_credentials"
    )


def test_the_name_falls_back_to_the_start_of_the_email(gapp, session, google):
    open_pilot(session)
    google.identity = Identity("g-9", "grace@example.test", None)
    sign_in(browser(gapp))
    assert user_by(session, email="grace@example.test").name == "grace"


def test_the_email_is_lower_cased_before_matching(gapp, session, google):
    mine = make_user(session, "ada@example.test")
    google.identity = Identity("g-ada-1", "Ada@Example.TEST", "Ada")
    sign_in(browser(gapp))
    assert user_by(session, id=mine.id).google_sub == "g-ada-1"


def test_an_international_address_is_not_supported_yet(gapp, session, google):
    open_pilot(session)
    google.identity = Identity("g-9", "ädä@example.test", "A")
    response = sign_in(browser(gapp))
    assert response.headers["location"] == "/login?error=email_unsupported"
    assert session.scalars(select(User)).all() == []


@pytest.mark.parametrize("by", ["sub", "email"])
def test_a_suspended_account_cannot_sign_in_with_google(gapp, session, by):
    fields = {"google_sub": "g-ada-1"} if by == "sub" else {}
    make_user(session, "ada@example.test", status="suspended", **fields)
    web = browser(gapp)
    assert sign_in(web).headers["location"] == "/login?error=account_suspended"
    assert logged_in(web)["authenticated"] is False
    assert user_by(session, email="ada@example.test").google_sub == fields.get("google_sub")


def test_a_google_sign_in_does_not_set_the_password_login_device_cookie(gapp, session):
    open_pilot(session)
    web = browser(gapp)
    response = sign_in(web)
    assert not any("device" in h for h in response.headers.get_list("set-cookie"))


# -- the pilot's cap --


def test_a_new_google_user_is_refused_while_the_pilot_is_closed(gapp, session):
    open_pilot(session, signups_open=False)
    web = browser(gapp)
    assert sign_in(web).headers["location"] == "/login?error=pilot_closed"
    assert session.scalars(select(User)).all() == []


def test_existing_users_still_sign_in_when_the_pilot_is_closed_or_full(gapp, session):
    open_pilot(session, max_accounts=1, signups_open=False)
    make_user(session, "ada@example.test", password=None, google_sub="g-ada-1")
    web = browser(gapp)
    assert sign_in(web).headers["location"] == "/"


def test_a_full_pilot_sets_a_short_cookie_and_leaves_no_half_made_user(gapp, session):
    open_pilot(session, max_accounts=1)
    make_user(session, "one@example.test")
    web = browser(gapp)
    response = sign_in(web)
    assert response.headers["location"] == "/pilot-full"
    assert "ada" not in response.headers["location"]  # the email is never put in a URL
    assert session.scalars(select(User).where(User.email == "ada@example.test")).all() == []
    assert logged_in(web)["authenticated"] is False
    header = next(h for h in response.headers.get_list("set-cookie") if h.startswith("pilot_email"))
    assert "HttpOnly" in header and "Max-Age=900" in header
    assert web.get("/api/pilot").json() == {
        "state": "full",
        "prefill_email": "ada@example.test",
        "google_signin": True,
    }


def test_the_prefill_cookie_must_be_ours_and_fresh(gapp):
    web = browser(gapp)
    forged = google_signin.sign_prefill(b"wrong-key" * 4, "mallory@example.test", datetime.now(UTC))
    web.client.cookies.set("pilot_email", forged)
    assert web.get("/api/pilot").json()["prefill_email"] is None
    key = google_signin.prefill_key(make_settings())
    old = google_signin.sign_prefill(
        key, "ada@example.test", datetime.now(UTC) - timedelta(minutes=16)
    )
    web.client.cookies.set("pilot_email", old)
    assert web.get("/api/pilot").json()["prefill_email"] is None
    assert key != auth.signing_key(make_settings())  # its own label: a session cookie is not one


def test_the_prefill_round_trips_and_expires_after_15_minutes():
    key = google_signin.prefill_key(make_settings())
    cookie = google_signin.sign_prefill(key, "ada@example.test", NOW)
    assert (
        google_signin.read_prefill(key, cookie, NOW + timedelta(minutes=14)) == "ada@example.test"
    )
    assert google_signin.read_prefill(key, cookie, NOW + timedelta(minutes=16)) is None
    assert google_signin.read_prefill(key, cookie + "x", NOW) is None
    assert google_signin.read_prefill(key, None, NOW) is None


def test_a_waitlist_invite_for_the_google_address_gets_in_when_full_and_is_used_up(gapp, session):
    open_pilot(session, max_accounts=1)
    make_user(session, "one@example.test")
    session.add(WaitlistEntry(email="ada@example.test", status="invited"))
    tokens.issue(
        session,
        purpose="waitlist_invite",
        email="ada@example.test",
        user_id=None,
        now=datetime.now(UTC),
    )
    session.commit()
    web = browser(gapp)
    assert sign_in(web).headers["location"] == "/"
    assert user_by(session, email="ada@example.test") is not None
    assert session.scalars(select(AuthToken).where(AuthToken.used_at.is_(None))).all() == []
    session.expire_all()
    assert session.scalars(select(WaitlistEntry)).one().status == "joined"


def test_an_invite_for_another_address_does_not_help(gapp, session):
    open_pilot(session, max_accounts=1)
    make_user(session, "one@example.test")
    tokens.issue(
        session,
        purpose="waitlist_invite",
        email="bob@example.test",
        user_id=None,
        now=datetime.now(UTC),
    )
    session.commit()
    assert sign_in(browser(gapp)).headers["location"] == "/pilot-full"


def test_a_waitlist_invite_reserves_the_slot_against_others(gapp, session, google):
    open_pilot(session, max_accounts=1)
    tokens.issue(
        session,
        purpose="waitlist_invite",
        email="bob@example.test",
        user_id=None,
        now=datetime.now(UTC),
    )
    session.commit()
    assert sign_in(browser(gapp)).headers["location"] == "/pilot-full"  # the slot is Bob's


def test_ten_parallel_google_sign_ups_at_nine_accounts_make_exactly_one(
    gapp, session, google, monkeypatch
):
    import threading

    real = signup.pilot_state

    def slow(db, now):
        state = real(db, now)
        time.sleep(0.15)
        return state

    monkeypatch.setattr(signup, "pilot_state", slow)
    open_pilot(session, max_accounts=10)
    for i in range(9):
        make_user(session, f"member{i}@example.test")
    flows = []
    for i in range(10):
        web = browser(gapp, f"198.51.100.{i + 1}")
        flows.append((web, start(web)[1], i))
    outcomes: list[str] = []

    class Many(FakeGoogle):
        def exchange(self, *, code, **kw):
            return Identity(f"g-{code}", f"racer{code}@example.test", "R")

    gapp.dependency_overrides[google_oauth.get_google] = lambda: Many()

    def go(web, state, i):
        outcomes.append(callback(web, state, code=str(i)).headers["location"])

    threads = [threading.Thread(target=go, args=flow) for flow in flows]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert sorted(outcomes) == ["/"] + ["/pilot-full"] * 9
    session.expire_all()
    assert session.scalar(select(func.count()).select_from(User)) == 10


def test_the_public_routes_in_the_guard_match_the_ones_the_tests_know():
    from tests.test_api import PUBLIC

    assert auth.PUBLIC_API == PUBLIC
