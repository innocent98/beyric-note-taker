"""Accounts: the session cookie, the guard that loads the user, email plus password login, and
that one user never sees another's meetings, templates or vocabulary."""

import json
import re
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app import accounts, auth, google_oauth, passwords, teams
from app.models import (
    CalendarConnection,
    CalendarEvent,
    Decision,
    Meeting,
    Notification,
    ReviewItem,
    Task,
    TeamInvite,
    Template,
    UserSettings,
    VocabularyTerm,
)
from tests.test_api import Api, anon, api, error_of, ready  # noqa: F401  (fixtures used by name)
from tests.test_web import (  # noqa: F401
    MEET,
    OTHER_EMAIL,
    OWNER_EMAIL,
    PASSWORD,
    add_meeting,
    anon_client,
    app,
    client,
    fake_openai,
    fake_vexa,
    login_data,
    make_user,
    owner,
    stranger,
    template,
    token_from,
)

KEY = b"k" * 32
GUESS = "-".join(["wrong", "guess", "17"])
OTHER_SECRET = "-".join(["not", "a", "login", "value"])  # only proves the key ignores it


def visitor(app, address="198.51.100.1") -> Api:
    """A separate browser at its own address (the app limits logins per address)."""
    return Api(TestClient(app, follow_redirects=False, client=(address, 50000)))


# ---- the cookie ----


def test_the_cookie_holds_uid_csrf_iat_epoch_and_gepoch():
    session = auth.new_session(uid=7, epoch=2, gepoch=3, now=1_000)
    assert set(session) == {"uid", "csrf", "iat", "epoch", "gepoch"}
    assert (session["uid"], session["epoch"], session["gepoch"]) == (7, 2, 3)
    assert auth.read_session(KEY, auth.sign_session(KEY, session), now=1_001) == session


def test_a_visitor_has_no_uid():
    assert auth.new_session()["uid"] is None


def test_the_old_cookie_shape_is_not_a_session():
    old = {"auth": True, "csrf": "x" * 43, "iat": 1_000, "epoch": 0}
    assert auth.read_session(KEY, auth.sign_session(KEY, old), now=1_001) is None


@pytest.mark.parametrize("uid", [True, "7", 1.5, -1, 0, [7]])
def test_a_malformed_uid_makes_the_cookie_worthless(uid):
    session = auth.new_session(uid=7, now=1_000) | {"uid": uid}
    assert auth.read_session(KEY, auth.sign_session(KEY, session), now=1_001) is None


@pytest.mark.parametrize("field", ["epoch", "gepoch", "iat"])
def test_booleans_are_not_numbers_in_the_cookie(field):
    session = auth.new_session(uid=7, now=1_000) | {field: True}
    assert auth.read_session(KEY, auth.sign_session(KEY, session), now=1_001) is None


def test_a_cookie_expires_after_thirty_days():
    session = auth.new_session(uid=7, now=1_000)
    cookie = auth.sign_session(KEY, session)
    assert auth.read_session(KEY, cookie, now=1_000 + auth.SESSION_SECONDS)
    assert auth.read_session(KEY, cookie, now=1_001 + auth.SESSION_SECONDS) is None


def test_the_signing_key_comes_from_the_session_secret_alone():
    from tests.test_web import make_settings

    base = auth.signing_key(make_settings())
    assert auth.signing_key(make_settings(owner_password=OTHER_SECRET)) == base
    assert auth.signing_key(make_settings(session_secret="z" * 40)) != base


# ---- the guard loads the user ----


def test_the_session_state_names_the_user(api, owner):
    body = api.get("/api/session").json()
    assert body["authenticated"] is True
    assert body["user"] == {
        "email": OWNER_EMAIL,
        "name": None,
        "is_admin": False,
        "email_verified": True,
        "time_zone": None,
    }


def test_a_visitor_sees_no_user_and_no_usage(anon):
    body = anon.get("/api/session").json()
    assert body["authenticated"] is False and body["user"] is None and body["usage"] is None


def test_logging_out_signs_out_only_that_user(app, owner, stranger):
    first, second = visitor(app, "198.51.100.1"), visitor(app, "198.51.100.2")
    assert first.log_in().status_code == 200
    assert second.log_in(email=OTHER_EMAIL).status_code == 200
    assert first.delete("/api/session").status_code == 200
    error_of(first.get("/api/templates"), 401, "not_authenticated")
    assert second.get("/api/templates").status_code == 200  # the other user is untouched
    assert second.get("/api/session").json()["user"]["email"] == OTHER_EMAIL


def test_a_per_user_epoch_bump_revokes_that_users_cookies_only(app, session, owner, stranger):
    first, second = visitor(app, "198.51.100.1"), visitor(app, "198.51.100.2")
    first.log_in()
    second.log_in(email=OTHER_EMAIL)
    auth.bump_user_epoch(session, owner.id)  # what a password change does
    error_of(first.get("/api/templates"), 401, "not_authenticated")
    assert second.get("/api/templates").status_code == 200


def test_a_global_epoch_bump_signs_everyone_out(app, session, owner, stranger):
    first, second = visitor(app, "198.51.100.1"), visitor(app, "198.51.100.2")
    first.log_in()
    second.log_in(email=OTHER_EMAIL)
    auth.bump_epoch(session)
    error_of(first.get("/api/templates"), 401, "not_authenticated")
    error_of(second.get("/api/templates"), 401, "not_authenticated")


@pytest.mark.parametrize("status", ["suspended", "deleted"])
def test_a_suspended_or_deleted_user_is_logged_out_at_once(api, session, owner, status):
    assert api.get("/api/templates").status_code == 200
    owner.status = status
    session.commit()
    error_of(api.get("/api/templates"), 401, "not_authenticated")
    assert api.get("/api/session").json()["authenticated"] is False  # the cookie was dropped


def test_a_cookie_for_a_user_that_no_longer_exists_is_just_a_visitor(app, api, session, owner):
    session.delete(owner)
    session.commit()
    error_of(api.get("/api/meetings"), 401, "not_authenticated")
    assert api.get("/api/session").json()["authenticated"] is False


def test_a_cookie_cannot_be_moved_to_another_user_by_editing_it(app, api, owner, stranger):
    cookie = api.client.cookies.get("session")
    body, signature = cookie.split(".")
    forged_body = auth._b64(
        json.dumps({**json.loads(auth._unb64(body)), "uid": stranger.id}).encode()
    )
    with TestClient(
        app, follow_redirects=False, cookies={"session": f"{forged_body}.{signature}"}
    ) as c:
        error_of(c.get("/api/templates"), 401, "not_authenticated")


# ---- login with email and password ----


def test_login_names_the_user_and_the_usage(anon, owner):
    body = anon.log_in().json()
    assert body["authenticated"] is True
    assert body["user"]["email"] == OWNER_EMAIL
    assert body["usage"] == {"used_seconds": 0, "cap_seconds": 18000}


def test_the_email_is_not_case_or_space_sensitive(anon, owner):
    assert anon.log_in(email="  Owner@EXAMPLE.test ").status_code == 200


def test_a_wrong_password_and_an_unknown_email_look_the_same(anon, owner):
    wrong = anon.log_in("not the password")
    unknown = anon.log_in(email="nobody@example.test")
    assert (wrong.status_code, wrong.json()) == (unknown.status_code, unknown.json())
    assert wrong.json()["error"]["code"] == "invalid_credentials"


def test_an_unknown_email_still_costs_one_hash_check(anon, owner, monkeypatch):
    seen = []
    real = passwords._hasher

    class Spy:
        def verify(self, stored, given):
            seen.append(stored)
            return real.verify(stored, given)

    monkeypatch.setattr(passwords, "_hasher", Spy())
    anon.log_in(email="nobody@example.test")
    assert seen == [passwords.DUMMY_HASH]
    seen.clear()
    anon.log_in("wrong password")  # a real account: its own hash is checked
    assert len(seen) == 1 and seen[0] != passwords.DUMMY_HASH


def test_an_account_without_a_password_cannot_log_in_that_way(anon, session):
    make_user(session, "google@example.test", password=None)
    error_of(anon.log_in("", email="google@example.test"), 401, "invalid_credentials")
    error_of(anon.log_in("anything", email="google@example.test"), 401, "invalid_credentials")


def test_an_unverified_account_gets_no_session_even_with_the_right_password(anon, session):
    make_user(session, "new@example.test", email_verified_at=None)
    error_of(anon.log_in(email="new@example.test"), 401, "invalid_credentials")
    assert anon.get("/api/session").json()["authenticated"] is False


def test_login_answers_alike_for_no_account_an_unverified_one_and_a_wrong_password(
    app, session, owner
):
    """Otherwise sign-up plus login would tell whether an address has a confirmed account: sign
    up with a password, then log in with it. The answer must not depend on which case it is."""
    make_user(session, "new@example.test", email_verified_at=None)
    answers = []
    for i, (email, password) in enumerate(
        [
            ("nobody@example.test", PASSWORD),  # no account
            ("new@example.test", PASSWORD),  # unverified, right password
            (OWNER_EMAIL, "-".join(["a", "wrong", "one", "7"])),  # verified, wrong password
        ]
    ):
        response = visitor(app, f"198.51.100.{i + 1}").log_in(password, email=email)
        answers.append((response.status_code, response.json()))
    assert answers[0] == answers[1] == answers[2]
    assert answers[0][1]["error"]["code"] == "invalid_credentials"


def test_an_unverified_login_costs_one_hash_check_like_every_other_case(anon, session, monkeypatch):
    make_user(session, "new@example.test", email_verified_at=None)
    calls = []
    real = passwords.verify_password

    def counting(stored, password):
        calls.append(stored is not None)
        return real(stored, password)

    monkeypatch.setattr(passwords, "verify_password", counting)
    anon.log_in(email="new@example.test")
    anon.log_in(email="nobody@example.test")
    assert calls == [True, False]  # one check each: against its hash, against the dummy


def test_sign_up_then_login_gives_the_same_answer_for_a_new_and_an_existing_address(
    app, session, owner
):
    from tests.test_signup import open_pilot, sign_up

    open_pilot(session)
    new = visitor(app, "198.51.100.10")
    sign_up(new, email="fresh@example.test", password=PASSWORD)
    existing = visitor(app, "198.51.100.11")
    sign_up(existing, email=OWNER_EMAIL, password=PASSWORD)  # the owner's address, a known password
    seen = []
    for web, email in ((new, "fresh@example.test"), (existing, OWNER_EMAIL)):
        response = web.log_in(
            PASSWORD + "x", email=email
        )  # the password they just submitted, wrong
        seen.append((response.status_code, response.json()))
    right_new = new.log_in(PASSWORD, email="fresh@example.test")
    assert right_new.status_code == 401  # an unconfirmed account does not sign in
    assert seen[0] == seen[1]


def test_a_suspended_account_cannot_log_in(anon, session):
    make_user(session, "gone@example.test", status="suspended")
    error_of(anon.log_in(email="gone@example.test"), 403, "account_suspended")
    assert anon.get("/api/session").json()["authenticated"] is False


def test_a_deleted_account_looks_unknown(anon, session):
    make_user(session, "deleted@example.test", status="deleted")
    error_of(anon.log_in(email="deleted@example.test"), 401, "invalid_credentials")


def test_a_login_stamps_the_time_and_issues_a_fresh_cookie(anon, session, owner):
    before = anon.csrf
    assert owner.last_login_at is None
    assert anon.log_in().status_code == 200
    session.refresh(owner)
    assert owner.last_login_at is not None
    assert anon.csrf != before


def test_an_unverified_login_counts_against_the_limit_like_any_failure(anon, session):
    make_user(session, "new@example.test", email_verified_at=None)
    codes = [anon.log_in(email="new@example.test").status_code for _ in range(6)]
    assert codes == [401] * 5 + [429]


def test_failures_on_one_email_from_many_addresses_lock_that_email(app, owner):
    for i in range(10):
        error_of(visitor(app, f"203.0.113.{i}").log_in("guess"), 401, "invalid_credentials")
    fresh = visitor(app, "203.0.113.200")
    error_of(fresh.log_in(), 429, "too_many_attempts")  # even the right password waits
    # another account is not affected
    assert fresh.log_in("guess", email="other@example.test").status_code == 401


def test_the_pages_login_form_has_an_email_field(anon_client):
    page = anon_client.get("/login").text
    assert 'name="email"' in page and 'type="email"' in page and 'name="password"' in page
    assert page.index('name="email"') < page.index('name="password"')


def test_the_pages_login_logs_in_with_email_and_password(anon_client, owner):
    token = token_from(anon_client.get("/login").text)
    response = anon_client.post("/login", data=login_data(token))
    assert response.status_code == 303 and response.headers["location"] == "/"
    assert anon_client.get("/templates").status_code == 200


def test_the_pages_login_error_keeps_the_email_but_never_the_password(anon_client, owner):
    token = token_from(anon_client.get("/login").text)
    response = anon_client.post("/login", data=login_data(token, password=GUESS))
    assert response.status_code == 401
    assert "That email or password is not right." in response.text
    assert f'value="{OWNER_EMAIL}"' in response.text
    assert GUESS not in response.text


def test_the_pages_login_without_an_email_fails(anon_client, owner):
    token = token_from(anon_client.get("/login").text)
    response = anon_client.post(
        "/login", data={"password": PASSWORD, "csrf_token": token}
    )  # the old, password-only form
    assert response.status_code == 401


# ---- each user sees only their own data ----


@pytest.fixture
def strangers_data(session, stranger):
    template = Template(owner_user_id=stranger.id, name="Theirs", instructions="Their notes.")
    session.add(template)
    session.flush()
    term = VocabularyTerm(owner_user_id=stranger.id, term="TheirTerm")
    session.add(term)
    session.commit()
    meeting = add_meeting(session, template, status="ready", step="done", notes="Their notes")
    return {"template": template, "term": term, "meeting": meeting}


def test_lists_show_only_my_own_rows(api, session, template, strangers_data):
    mine = add_meeting(session, template)
    session.add(VocabularyTerm(owner_user_id=template.owner_user_id, term="MyTerm"))
    session.commit()
    meetings = api.get("/api/meetings").json()["meetings"]
    assert [m["id"] for m in meetings] == [mine.id]
    assert [t["name"] for t in api.get("/api/templates").json()["templates"]] == ["General"]
    assert [t["term"] for t in api.get("/api/vocabulary").json()["terms"]] == ["MyTerm"]


@pytest.mark.parametrize(
    "method, path, body",
    [
        ("GET", "/api/meetings/{meeting}", None),
        ("POST", "/api/meetings/{meeting}/cancel", None),
        ("POST", "/api/meetings/{meeting}/retry", None),
        ("POST", "/api/meetings/{meeting}/regenerate", {"template_id": 0}),
        ("PUT", "/api/templates/{template}", {"name": "Hijack", "instructions": "x"}),
        ("DELETE", "/api/vocabulary/{term}", None),
    ],
)
def test_someone_elses_id_is_a_404_and_changes_nothing(
    api, session, template, strangers_data, method, path, body
):
    ids = {key: row.id for key, row in strangers_data.items()}
    if body and "template_id" in body:
        body = {"template_id": template.id}
    response = api.call(method, path.format(**ids), body)
    error_of(response, 404, "not_found")
    session.expire_all()
    assert session.get(Template, ids["template"]).name == "Theirs"
    assert session.get(VocabularyTerm, ids["term"]) is not None
    assert session.get(Meeting, ids["meeting"]).status == "ready"


def test_the_pages_hide_foreign_meetings_and_templates_too(client, template, strangers_data):
    meeting_id = strangers_data["meeting"].id
    assert client.get(f"/meetings/{meeting_id}").status_code == 404
    page = client.get("/templates").text
    assert "General" in page and "Theirs" not in page
    assert "TheirTerm" not in client.get("/vocabulary").text
    response = client.post(
        f"/meetings/{meeting_id}/retry", data={"csrf_token": token_from(client.get("/").text)}
    )
    assert response.status_code == 404
    edit = client.post(
        f"/templates/{strangers_data['template'].id}",
        data={"name": "x", "instructions": "y", "csrf_token": token_from(client.get("/").text)},
    )
    assert edit.status_code == 404


def test_the_pages_refuse_regenerate_cancel_and_term_delete_on_foreign_rows(
    client, session, template, strangers_data, fake_vexa, fake_openai
):
    """The Jinja routes are scoped by the same service calls as the API; this proves it for the
    three that act on a row (retry and template edit are above)."""
    token = token_from(client.get("/").text)
    meeting = strangers_data["meeting"]
    notes_before = meeting.notes
    regenerate = client.post(
        f"/meetings/{meeting.id}/regenerate",
        data={"template_id": str(template.id), "csrf_token": token},
    )
    assert regenerate.status_code == 404
    waiting = add_meeting(
        session,
        strangers_data["template"],
        status="bot_sent",
        native_id="zzz-zzzz-zzz",
    )
    cancel = client.post(f"/meetings/{waiting.id}/cancel", data={"csrf_token": token})
    assert cancel.status_code == 404
    delete = client.post(
        f"/vocabulary/{strangers_data['term'].id}/delete", data={"csrf_token": token}
    )
    assert delete.status_code == 303  # the page never says whether the row exists
    session.expire_all()
    assert session.get(Meeting, meeting.id).notes == notes_before
    assert session.get(Meeting, waiting.id).status == "bot_sent"
    assert session.get(VocabularyTerm, strangers_data["term"].id) is not None
    assert getattr(fake_vexa, "stopped", []) == [] and fake_openai.chat_calls == []


def test_every_page_route_with_an_id_returns_404_for_another_users_row(
    anon_client, app, session, owner, template, stranger, strangers_data
):
    """Page routes that act on an id: the stranger calls each with the owner's row. A new one
    that is not classified here fails, so nobody adds one unscoped."""
    from fastapi.routing import APIRoute

    from app import web

    mine = add_meeting(session, template)
    term = VocabularyTerm(owner_user_id=owner.id, term="MyTerm")
    session.add(term)
    session.commit()
    ids = {"meeting_id": mine.id, "template_id": template.id, "term_id": term.id}
    token = token_from(anon_client.get("/login").text)
    login = anon_client.post("/login", data=login_data(token, email=OTHER_EMAIL))
    assert login.status_code == 303
    token = token_from(anon_client.get("/").text)
    checked = 0
    for route in web.router.routes:
        assert isinstance(route, APIRoute)
        names = re.findall(r"\{(\w+)(?::\w+)?\}", route.path)
        if not names:
            continue
        assert set(names) <= set(ids), f"classify the id in {route.path} and scope it"
        url = re.sub(r"\{(\w+)(?::\w+)?\}", lambda m: str(ids[m[1]]), route.path)
        for method in route.methods:
            data = {"template_id": str(strangers_data["template"].id), "csrf_token": token}
            data |= {"name": "x", "instructions": "y"}
            response = anon_client.request(method, url, data=data if method == "POST" else None)
            if route.path.endswith("/delete"):
                assert response.status_code == 303  # a term delete never says "no such term"
            else:
                assert response.status_code == 404, f"{method} {route.path}"
            checked += 1
    assert checked >= 6
    session.expire_all()
    assert session.get(VocabularyTerm, term.id) is not None


def test_a_meeting_cannot_be_started_with_someone_elses_template(
    api, fake_vexa, session, strangers_data
):
    body = {"url": MEET, "template_id": strangers_data["template"].id}
    error_of(api.post("/api/meetings", body), 400, "template_required")
    assert fake_vexa.sent == []
    assert session.scalars(select(Meeting).where(Meeting.status == "bot_sent")).all() == []


def test_notes_cannot_be_rewritten_with_someone_elses_template(
    api, session, template, strangers_data, fake_openai
):
    meeting = ready(session, template)
    notes_before, template_before = meeting.notes, meeting.template_id
    body = {"template_id": strangers_data["template"].id}
    error_of(api.post(f"/api/meetings/{meeting.id}/regenerate", body), 400, "template_required")
    assert fake_openai.chat_calls == []
    session.refresh(meeting)
    assert (meeting.notes, meeting.template_id) == (notes_before, template_before)


def test_the_page_cannot_rewrite_notes_with_someone_elses_template(
    client, session, template, strangers_data, fake_openai
):
    meeting = ready(session, template)
    notes_before, template_before = meeting.notes, meeting.template_id
    response = client.post(
        f"/meetings/{meeting.id}/regenerate",
        data={
            "template_id": str(strangers_data["template"].id),
            "csrf_token": token_from(client.get("/").text),
        },
    )
    assert response.status_code == 400
    assert "Choose a template." in response.text
    assert fake_openai.chat_calls == []
    session.refresh(meeting)
    assert (meeting.notes, meeting.template_id) == (notes_before, template_before)


def test_two_users_may_use_the_same_template_name_and_term(api, session, template, strangers_data):
    session.add(
        Template(
            owner_user_id=strangers_data["template"].owner_user_id, name="General", instructions="x"
        )
    )
    session.add(VocabularyTerm(owner_user_id=strangers_data["term"].owner_user_id, term="Beyric"))
    session.commit()
    assert api.post("/api/templates", {"name": "Theirs", "instructions": "mine"}).status_code == 201
    assert api.post("/api/vocabulary", {"term": "TheirTerm"}).status_code == 201
    # but not twice for one user
    error_of(
        api.post("/api/templates", {"name": "Theirs", "instructions": "again"}),
        400,
        "duplicate_name",
    )
    error_of(api.post("/api/vocabulary", {"term": "TheirTerm"}), 400, "duplicate_term")


def test_new_rows_belong_to_the_caller(api, session, owner):
    api.post("/api/templates", {"name": "Mine", "instructions": "x"})
    api.post("/api/vocabulary", {"term": "Mine"})
    assert session.scalars(select(Template.owner_user_id)).all() == [owner.id]
    assert session.scalars(select(VocabularyTerm.owner_user_id)).all() == [owner.id]


def test_a_started_meeting_belongs_to_the_caller(api, session, owner, template):
    response = api.post("/api/meetings", {"url": MEET, "template_id": template.id})
    assert response.status_code == 201
    assert session.scalars(select(Meeting.owner_user_id)).all() == [owner.id]


def test_every_api_route_with_an_id_returns_404_for_another_users_row(
    anon, app, session, owner, stranger, template, strangers_data
):
    """The IDOR guard (spec 5.3): sign in as the stranger and call every route that has an id in
    its path with the OWNER's row. A new id route that is not listed here fails the test, so
    nobody can add one and forget to scope it."""
    from tests.test_api import api_routes

    mine = add_meeting(session, template)
    term = VocabularyTerm(owner_user_id=owner.id, term="MyTerm")
    session.add(term)
    session.commit()
    note = Notification(user_id=owner.id, kind="k", title="t", dedupe_key="d")
    decision = Decision(meeting_id=mine.id, text="d", position=0, fingerprint="d")
    review = ReviewItem(
        meeting_id=mine.id, owner_user_id=owner.id, text="r", position=0, fingerprint="r"
    )
    task = Task(
        team_id=teams.ensure_team(session, owner).id,
        meeting_id=mine.id,
        title="t",
        source="ai",
        ai_fingerprint="t",
        assignee_user_id=owner.id,
    )
    pending_invite = TeamInvite(
        team_id=teams.ensure_team(session, owner).id,
        email="invited@example.test",
        token_hash="f" * 64,
        invited_by=owner.id,
        expires_at=datetime.now(UTC) + timedelta(days=7),
    )
    session.add_all([term, note, decision, review, task, pending_invite])
    session.commit()
    # The calendar routes answer 404 `calendar_unavailable` while the feature is off, which would
    # hide an unscoped id: switch it on, and give BOTH people a live connection so that only the
    # owner filter on the event row can refuse the stranger.
    app.state.settings = app.state.settings.model_copy(
        update={"calendar_enabled": True, "google_client_id": "id", "google_client_secret": "s"}
    )
    app.dependency_overrides[google_oauth.get_google] = lambda: object()  # only "is there one"
    for user in (owner, stranger):
        session.add(CalendarConnection(user_id=user.id, granted_scopes=google_oauth.CALENDAR_SCOPE))
    session.commit()
    start = datetime.now(UTC) + timedelta(hours=2)
    event = CalendarEvent(
        user_id=owner.id,
        connection_id=session.scalars(
            select(CalendarConnection.id).where(CalendarConnection.user_id == owner.id)
        ).one(),
        source_event_id="evt-1",
        title="Owner meeting",
        starts_at=start,
        ends_at=start + timedelta(hours=1),
        platform="google_meet",
        meeting_url="https://meet.google.com/abc-defg-hij",
        native_id="abc-defg-hij",
        organizer_self=True,
        self_response="accepted",
    )
    session.add(event)
    session.commit()
    ids = {
        "event_id": event.id,
        "meeting_id": mine.id,
        "template_id": template.id,
        "term_id": term.id,
        "notification_id": note.id,
        "decision_id": decision.id,
        "item_id": review.id,
        "task_id": task.id,
        "invite_id": pending_invite.id,
        "user_id": owner.id,  # a member of the owner's team, reached through the team routes
        "language": "fr",  # with the owner's meeting id: a translation is reached through it
    }
    anon.log_in(email=OTHER_EMAIL)
    checked = 0
    for method, path in api_routes(app):
        names = [n for n in re.findall(r"\{(\w+)\}", path) if n != "provider"]  # a name, not an id
        if not names or path.startswith("/api/admin/"):
            continue  # admin routes answer 403 to any non-admin: tests/test_admin.py
        assert set(names) <= set(ids), f"classify the id in {method} {path} and scope it"
        url = re.sub(r"\{(\w+)\}", lambda m: str(ids.get(m[1], "google")), path)
        bodies = {  # a valid body for each route that takes one, so only the id can be wrong
            "/regenerate": {"template_id": strangers_data["template"].id},
            "/vocabulary": {"term": "Metrika"},
            "/translations": {"language": "fr"},
            "/override": {"override": False},
            "/api/tasks/{task_id}": {"status": "done"},
            "/api/team/members/{user_id}": {"aliases": ["x"]},
            "/api/templates/{template_id}": {"name": "x", "instructions": "y"},
        }
        body = next((b for key, b in bodies.items() if path.endswith(key)), None)
        if method == "DELETE":
            body = None  # a delete takes no body: the template delete shares the PUT's path
        error_of(anon.call(method, url, body), 404, "not_found")
        checked += 1
    assert checked >= 7
    assert ("DELETE", "/api/templates/{template_id}") in set(api_routes(app))
    # The 404 above is the owner filter, not a dead route: the owner's own call works, and the
    # stranger's attempt changed nothing.
    session.expire_all()
    assert session.get(CalendarEvent, event.id).override is None
    # the stranger's DELETE of the owner's template was refused, not carried out
    assert session.get(Template, template.id).deleted_at is None
    anon.log_in(email=OWNER_EMAIL)
    assert (
        anon.put(f"/api/calendar/events/{event.id}/override", {"override": False}).status_code
        == 200
    )
    session.expire_all()
    assert session.get(CalendarEvent, event.id).override is False


# ---- defaults for a new user ----


def test_a_new_user_gets_their_own_copy_of_the_four_default_templates(session, stranger, owner):
    for user in (owner, stranger):
        accounts.create_user_defaults(session, user)
    for user in (owner, stranger):
        names = session.scalars(
            select(Template.name).where(Template.owner_user_id == user.id).order_by(Template.id)
        ).all()
        assert names == ["General", "Client call", "Team update", "Interview"]
    assert len(session.scalars(select(Template.id)).all()) == 8  # copies, not shared rows
    general = session.scalars(
        select(Template).where(Template.owner_user_id == owner.id, Template.name == "General")
    ).one()
    assert "summary" in general.instructions.lower()


def test_the_defaults_create_the_settings_row_pointing_at_their_general_copy(session, owner):
    accounts.create_user_defaults(session, owner)
    settings = session.get(UserSettings, owner.id)
    assert (settings.auto_join_rule, settings.join_instant_meets, settings.notify_email) == (
        "all",
        False,
        True,
    )
    general = session.get(Template, settings.calendar_template_id)
    assert (general.name, general.owner_user_id) == ("General", owner.id)


def test_the_default_templates_match_the_ones_migration_0001_seeds():
    import importlib.util
    from pathlib import Path

    path = Path(__file__).parent.parent / "alembic" / "versions" / "0001_initial.py"
    spec = importlib.util.spec_from_file_location("initial", path)
    initial = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(initial)
    assert accounts.DEFAULT_TEMPLATES == initial.SEED_TEMPLATES


def test_a_busy_server_answers_429_without_costing_a_login_attempt(app, owner, monkeypatch):
    import threading

    from tests.test_edge import SECRET  # noqa: F401

    held = threading.BoundedSemaphore(1)
    held.acquire()  # every hash slot is taken
    monkeypatch.setattr(passwords, "_slots", held)
    monkeypatch.setattr(passwords, "SLOT_WAIT_SECONDS", 0.05)
    client = visitor(app)
    for _ in range(8):  # more than the 5 failures an address may have
        error = error_of(client.log_in(), 429, "server_busy")
        assert error["message"] == "The server is busy. Try again in a moment."
    held.release()  # a slot frees up: the very same address is not locked out
    assert client.log_in().status_code == 200
