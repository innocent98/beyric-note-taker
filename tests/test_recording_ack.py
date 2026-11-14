"""The recording-consent box: required at sign-up on both paths (password and Google), stored the
way the terms are, and a one-time, never-blocking card for accounts older than the box."""

from datetime import UTC, datetime, timedelta

from fastapi.testclient import TestClient
from sqlalchemy import select

from app import google_signin, signup
from app.models import Meeting, User
from tests.test_accounts import visitor  # noqa: F401  (fixture used by name)
from tests.test_api import Api, anon, api, create, error_of  # noqa: F401  (fixtures used by name)
from tests.test_google_signin import (  # noqa: F401
    browser,
    callback,
    gapp,
    google,
    logged_in,
    sign_in,
    start,
    user_by,
)
from tests.test_signup import NEW_EMAIL, form, open_pilot, sign_up, user_of
from tests.test_web import (  # noqa: F401
    OWNER_EMAIL,
    PASSWORD,
    anon_client,
    app,
    fake_openai,
    fake_vexa,
    make_user,
    owner,
    template,
)

# ---- password sign-up ----


def test_password_sign_up_without_the_box_is_refused_and_creates_nothing(anon, session):
    open_pilot(session)
    response = sign_up(anon, accept_recording=False)
    error = error_of(response, 422, "recording_ack_required")
    assert "tell the people in your meetings" in error["message"]
    assert user_of(session, NEW_EMAIL) is None


def test_password_sign_up_with_the_field_missing_is_the_same_plain_refusal(anon, session):
    open_pilot(session)
    body = form()
    del body["accept_recording"]
    error_of(anon.post("/api/signup", body), 422, "recording_ack_required")
    assert user_of(session, NEW_EMAIL) is None


def test_the_terms_are_asked_for_before_the_box(anon, session):
    open_pilot(session)
    error_of(sign_up(anon, accept_terms=False, accept_recording=False), 422, "terms_required")


def test_the_refusal_is_the_same_whether_or_not_the_address_has_an_account(anon, session, owner):
    open_pilot(session)
    new = error_of(sign_up(anon, accept_recording=False), 422, "recording_ack_required")
    known = error_of(
        sign_up(anon, email=OWNER_EMAIL, accept_recording=False), 422, "recording_ack_required"
    )
    assert new == known


def test_password_sign_up_with_the_box_stores_the_time_and_the_version(anon, session):
    open_pilot(session)
    before = datetime.now(UTC)
    assert sign_up(anon).status_code == 202
    user = user_of(session, NEW_EMAIL)
    assert user.recording_ack_version == signup.RECORDING_ACK_VERSION == "2026-11-14"
    assert before - timedelta(seconds=5) <= user.recording_ack_at <= datetime.now(UTC)


def test_the_box_is_never_pre_ticked_by_the_server_a_default_means_refused():
    from app.api_schemas import SignUp

    assert SignUp.model_fields["accept_recording"].default is False


def test_a_sign_up_for_an_account_that_exists_does_not_touch_its_acknowledgement(anon, session):
    open_pilot(session)
    make_user(session, NEW_EMAIL, password=PASSWORD)
    assert sign_up(anon).status_code == 202
    assert user_of(session, NEW_EMAIL).recording_ack_at is None  # nothing about it changed


# ---- Google ----


def test_google_new_account_without_the_box_is_turned_back_and_nothing_is_created(gapp, session):
    open_pilot(session)
    web = browser(gapp)
    response = sign_in(web, "?accept_terms=true")
    assert response.headers["location"] == "/login?error=recording_ack_required"
    assert user_by(session, email="ada@example.test") is None
    assert logged_in(web)["authenticated"] is False


def test_google_without_either_box_still_says_terms_first(gapp, session):
    open_pilot(session)
    response = sign_in(browser(gapp), "")
    assert response.headers["location"] == "/login?error=terms_required"


def test_google_new_account_with_the_box_stores_the_time_and_the_version(gapp, session):
    open_pilot(session)
    web = browser(gapp)
    assert sign_in(web).headers["location"] == "/"
    user = user_by(session, email="ada@example.test")
    assert user.recording_ack_version == signup.RECORDING_ACK_VERSION
    assert user.recording_ack_at is not None
    assert logged_in(web)["user"]["recording_acknowledged"] is True


def test_an_existing_google_account_signs_in_without_the_box_and_is_not_marked(gapp, session):
    make_user(session, "ada@example.test", password=None, google_sub="g-ada-1")
    web = browser(gapp)
    assert sign_in(web, "").headers["location"] == "/"
    assert user_by(session, email="ada@example.test").recording_ack_at is None
    assert logged_in(web)["user"]["recording_acknowledged"] is False


def test_the_flow_cookie_carries_each_box_and_still_names_the_state():
    state = "abc_DEF-123"
    for terms, recording, value in [
        (False, False, state),
        (True, False, state + ".t"),
        (False, True, state + ".r"),
        (True, True, state + ".t.r"),
    ]:
        cookie = google_signin.flow_cookie_value(state, terms, recording)
        assert cookie == value
        assert google_signin.cookie_accepted_terms(cookie) is terms
        assert google_signin.cookie_accepted_recording(cookie) is recording
        assert google_signin.flow_cookie_matches(cookie, state)
        assert not google_signin.flow_cookie_matches(cookie, "another-state")


def test_a_flow_started_before_the_box_existed_keeps_its_terms_but_has_no_box():
    old = "abc_DEF-123.t"  # the cookie the previous release set
    assert google_signin.cookie_accepted_terms(old)
    assert not google_signin.cookie_accepted_recording(old)
    assert google_signin.flow_cookie_matches(old, "abc_DEF-123")


# ---- accounts older than the box: a card, never a gate ----


def test_an_old_account_is_told_the_box_is_not_confirmed_yet(api, session, owner):
    assert owner.recording_ack_at is None
    assert api.get("/api/session").json()["user"]["recording_acknowledged"] is False


def test_the_card_button_stores_the_time_and_the_version_once(api, session, owner):
    first = api.post("/api/settings/recording-ack")
    assert (first.status_code, first.json()) == (200, {"recording_acknowledged": True})
    session.expire_all()
    stored = session.get(User, owner.id)
    when, version = stored.recording_ack_at, stored.recording_ack_version
    assert when is not None and version == signup.RECORDING_ACK_VERSION
    assert api.get("/api/session").json()["user"]["recording_acknowledged"] is True
    assert api.post("/api/settings/recording-ack").json() == {"recording_acknowledged": True}
    session.expire_all()
    assert session.get(User, owner.id).recording_ack_at == when  # the first click is the evidence


def test_the_acknowledgement_is_the_callers_own_and_needs_login_and_the_header(
    api, app, session, owner
):
    other = make_user(session, "other@example.test")
    stranger = Api(TestClient(app))  # `api` is logged in (it shares the `anon` client)
    error_of(stranger.post("/api/settings/recording-ack"), 401, "not_authenticated")
    error_of(api.post("/api/settings/recording-ack", csrf=False), 403, "csrf_rejected")
    assert api.post("/api/settings/recording-ack").status_code == 200
    session.expire_all()
    assert session.get(User, other.id).recording_ack_at is None


def test_an_account_that_never_acknowledged_can_still_send_the_bot(
    api, template, fake_vexa, session, owner
):
    assert owner.recording_ack_at is None
    response = create(api, template)
    assert response.status_code == 201 and fake_vexa.sent
    assert session.scalars(select(Meeting).where(Meeting.owner_user_id == owner.id)).first()
