"""The waitlist, the admin API, deleting an account and the notifications API (spec 4.4 to 13)."""

import re
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import func, select

from app import deletion, mail, signup, tokens
from app.models import (
    AppState,
    AuthToken,
    MailOutbox,
    Meeting,
    Notification,
    TeamMember,
    Template,
    UsageEntry,
    User,
    UserSettings,
    VocabularyTerm,
    WaitlistEntry,
)
from app.vexa import VexaError, VexaMeeting
from tests.test_accounts import visitor
from tests.test_api import anon, api, error_of  # noqa: F401  (fixtures used by name)
from tests.test_signup import GOOD, open_pilot, queued
from tests.test_web import (  # noqa: F401
    OTHER_EMAIL,
    OWNER_EMAIL,
    PASSWORD,
    add_meeting,
    anon_client,
    app,
    fake_openai,
    fake_vexa,
    make_settings,
    make_user,
    owner,
    stranger,
    template,
)

ADMIN = "admin@example.test"


@pytest.fixture
def admin(session):
    return make_user(session, ADMIN, admin=True)


@pytest.fixture
def boss(app, admin):
    """A browser signed in as the admin."""
    web = visitor(app, "198.51.100.77")
    assert web.log_in(email=ADMIN).status_code == 200
    return web


def entry(session, email="wait@example.test", **fields) -> WaitlistEntry:
    row = WaitlistEntry(email=email, **fields)
    session.add(row)
    session.commit()
    return row


# ---- the public waitlist ----


def test_joining_the_waitlist_answers_202_and_adds_a_row(anon, session):
    response = anon.post("/api/waitlist", {"email": " Wait@Example.test ", "name": "Wendy"})
    assert (response.status_code, response.json()) == (202, {"status": "on_the_list"})
    [row] = session.scalars(select(WaitlistEntry)).all()
    assert (row.email, row.name, row.status) == ("wait@example.test", "Wendy", "waiting")


def test_the_name_is_optional(anon, session):
    assert anon.post("/api/waitlist", {"email": "wait@example.test"}).status_code == 202
    assert session.scalars(select(WaitlistEntry)).one().name is None


def test_the_same_answer_whether_or_not_the_address_is_listed_or_has_an_account(app, session):
    entry(session, "listed@example.test", name="First")
    make_user(session, "member@example.test")
    answers = {
        (r.status_code, str(r.json()))
        for r in (
            visitor(app, f"198.51.100.{i}").post("/api/waitlist", {"email": email})
            for i, email in enumerate(
                ["new@example.test", "listed@example.test", "member@example.test"], start=1
            )
        )
    }
    assert answers == {(202, str({"status": "on_the_list"}))}
    names = {r.email: r.name for r in session.scalars(select(WaitlistEntry))}
    assert names["listed@example.test"] == "First"  # not overwritten
    assert session.scalar(select(func.count()).select_from(WaitlistEntry)) == 3


def test_a_removed_entry_stays_removed_when_the_person_asks_again(anon, session):
    entry(session, "gone@example.test", status="removed")
    anon.post("/api/waitlist", {"email": "gone@example.test"})
    assert session.scalars(select(WaitlistEntry)).one().status == "removed"


@pytest.mark.parametrize("email", ["", "nope", "a@b", "ädä@example.test"])
def test_the_waitlist_needs_a_plain_email(anon, session, email):
    error_of(anon.post("/api/waitlist", {"email": email}), 422, "invalid_email")
    assert session.scalars(select(WaitlistEntry)).all() == []


def test_the_waitlist_body_is_strict(anon):
    error_of(anon.post("/api/waitlist", {"email": "a@b.test", "x": 1}), 422, "validation_error")
    error_of(anon.post("/api/waitlist", {"email": "a@b.test"}, csrf=False), 403, "csrf_rejected")


def test_the_waitlist_is_limited_per_address_and_still_202(app, session):
    client = visitor(app, "203.0.113.5")
    codes = [
        client.post("/api/waitlist", {"email": f"w{i}@example.test"}).status_code for i in range(7)
    ]
    assert codes == [202] * 7
    assert session.scalar(select(func.count()).select_from(WaitlistEntry)) == 5


def test_one_email_is_added_once_a_minute(anon, session):
    anon.post("/api/waitlist", {"email": "w@example.test", "name": "A"})
    anon.post("/api/waitlist", {"email": "w@example.test", "name": "B"})
    assert session.scalars(select(WaitlistEntry)).one().name == "A"


# ---- who is an admin ----


ADMIN_ROUTES = [
    ("GET", "/api/admin/users"),
    ("PATCH", "/api/admin/users/1"),
    ("DELETE", "/api/admin/users/1"),
    ("POST", "/api/admin/users/1/reset-link"),
    ("GET", "/api/admin/waitlist"),
    ("POST", "/api/admin/waitlist/1/invite"),
    ("DELETE", "/api/admin/waitlist/1"),
    ("GET", "/api/admin/pilot"),
    ("PUT", "/api/admin/pilot"),
    ("GET", "/api/admin/mail-failures"),
]


def test_every_admin_route_is_in_this_list(app):
    from tests.test_api import api_routes

    routes = {
        (m, p.replace("{user_id}", "1").replace("{waitlist_id}", "1")) for m, p in api_routes(app)
    }
    assert {r for r in routes if r[1].startswith("/api/admin/")} == set(ADMIN_ROUTES)


@pytest.mark.parametrize("method, path", ADMIN_ROUTES)
def test_an_admin_route_refuses_an_ordinary_user_with_403(api, method, path):
    body = {"status": "suspended"} if method in ("PATCH", "PUT") else None
    error_of(api.call(method, path, body), 403, "forbidden")


@pytest.mark.parametrize("method, path", ADMIN_ROUTES)
def test_an_admin_route_refuses_a_visitor_with_401(anon, method, path):
    error_of(anon.call(method, path, {"x": 1}), 401, "not_authenticated")


def test_the_admin_flag_cannot_be_set_through_any_route(boss, session, owner):
    error_of(
        boss.call("PATCH", f"/api/admin/users/{owner.id}", {"is_admin": True}),
        422,
        "validation_error",
    )
    assert session.get(User, owner.id).is_admin is False


# ---- users ----


def test_the_user_list_shows_users_and_usage_but_never_a_hash_or_a_deleted_user(
    boss, session, owner
):
    make_user(session, "gone@example.test", status="deleted")
    session.add(UsageEntry(user_id=owner.id, email_hash="h" * 64, seconds=3600))
    session.commit()
    body = boss.get("/api/admin/users").json()
    emails = [u["email"] for u in body["users"]]
    assert emails == [OWNER_EMAIL, ADMIN]  # newest first
    mine = next(u for u in body["users"] if u["email"] == OWNER_EMAIL)
    assert mine["used_seconds"] == 3600 and mine["cap_seconds"] == 18000
    assert mine["hours_cap_override"] is None and mine["has_password"] is True
    assert mine["has_google"] is False and mine["email_verified"] is True
    assert set(mine) == {
        "id", "email", "name", "is_admin", "status", "email_verified", "has_password",
        "has_google", "created_at", "last_login_at", "used_seconds", "cap_seconds",
        "hours_cap_override",
    }  # fmt: skip
    assert "hash" not in str(body) and "google_sub" not in str(body)
    admin_row = next(u for u in body["users"] if u["email"] == ADMIN)
    assert admin_row["cap_seconds"] is None and admin_row["is_admin"] is True


def test_the_user_list_pages(boss, session):
    for i in range(3):
        make_user(session, f"u{i}@example.test")
    first = boss.get("/api/admin/users?limit=2").json()
    assert len(first["users"]) == 2 and first["has_more"] is True
    second = boss.get(f"/api/admin/users?limit=2&before_id={first['next_before_id']}").json()
    assert {u["id"] for u in first["users"]}.isdisjoint({u["id"] for u in second["users"]})
    assert first["users"][0]["id"] > second["users"][0]["id"]


def test_the_admin_can_suspend_and_reactivate_and_the_user_is_out_at_once(
    app, boss, session, owner
):
    web = visitor(app, "198.51.100.20")
    web.log_in()
    assert web.get("/api/templates").status_code == 200
    response = boss.call("PATCH", f"/api/admin/users/{owner.id}", {"status": "suspended"})
    assert response.status_code == 200 and response.json()["status"] == "suspended"
    error_of(web.get("/api/templates"), 401, "not_authenticated")
    error_of(visitor(app, "198.51.100.21").log_in(), 403, "account_suspended")
    assert (
        boss.call("PATCH", f"/api/admin/users/{owner.id}", {"status": "active"}).json()["status"]
        == "active"
    )
    assert visitor(app, "198.51.100.22").log_in().status_code == 200


def test_the_admin_can_raise_one_users_hours_and_clear_the_override(boss, session, owner):
    body = boss.call("PATCH", f"/api/admin/users/{owner.id}", {"hours_cap_seconds": 36000}).json()
    assert (body["cap_seconds"], body["hours_cap_override"]) == (36000, 36000)
    body = boss.call("PATCH", f"/api/admin/users/{owner.id}", {"hours_cap_seconds": None}).json()
    assert (body["cap_seconds"], body["hours_cap_override"]) == (18000, None)


def test_a_patch_changes_only_the_fields_it_names(boss, session, owner):
    owner.hours_cap_seconds = 7200
    session.commit()
    boss.call("PATCH", f"/api/admin/users/{owner.id}", {"status": "suspended"})
    session.refresh(owner)
    assert (owner.status, owner.hours_cap_seconds) == ("suspended", 7200)


@pytest.mark.parametrize(
    "body",
    [{}, {"status": "deleted"}, {"status": "deleting"}, {"status": "nope"},
     {"hours_cap_seconds": -1}, {"hours_cap_seconds": "5"}, {"hours_cap_seconds": 10**9},
     {"email": "x@y.test"}],
)  # fmt: skip
def test_a_patch_is_strict(boss, owner, body):
    error_of(boss.call("PATCH", f"/api/admin/users/{owner.id}", body), 422, "validation_error")


def test_an_unknown_user_is_404(boss):
    error_of(
        boss.call("PATCH", "/api/admin/users/99999", {"status": "suspended"}), 404, "not_found"
    )
    error_of(boss.call("DELETE", "/api/admin/users/99999"), 404, "not_found")
    error_of(boss.call("POST", "/api/admin/users/99999/reset-link"), 404, "not_found")


def test_a_deleted_user_looks_unknown_to_the_admin_too(boss, session):
    gone = make_user(session, "gone@example.test", status="deleted")
    error_of(
        boss.call("PATCH", f"/api/admin/users/{gone.id}", {"status": "active"}), 404, "not_found"
    )


def test_the_last_admin_can_be_neither_suspended_nor_deleted(boss, admin):
    error_of(
        boss.call("PATCH", f"/api/admin/users/{admin.id}", {"status": "suspended"}),
        409,
        "last_admin",
    )
    error_of(boss.call("DELETE", f"/api/admin/users/{admin.id}"), 409, "last_admin")


def test_with_a_second_admin_the_first_may_be_suspended(boss, admin, session):
    make_user(session, "second@example.test", admin=True)
    response = boss.call("PATCH", f"/api/admin/users/{admin.id}", {"status": "suspended"})
    assert response.status_code == 200


def test_suspending_a_suspended_admin_does_not_count_as_a_second_admin(boss, admin, session):
    make_user(session, "second@example.test", admin=True, status="suspended")
    error_of(
        boss.call("PATCH", f"/api/admin/users/{admin.id}", {"status": "suspended"}),
        409,
        "last_admin",
    )


def test_suspending_ends_old_sessions_for_good_even_after_reactivation(app, boss, session, owner):
    web = visitor(app, "198.51.100.50")
    web.log_in()
    boss.call("PATCH", f"/api/admin/users/{owner.id}", {"status": "suspended"})
    boss.call("PATCH", f"/api/admin/users/{owner.id}", {"status": "active"})
    error_of(web.get("/api/templates"), 401, "not_authenticated")  # the old cookie stays dead
    assert visitor(app, "198.51.100.51").log_in().status_code == 200


def test_two_admins_cannot_remove_each_other_at_the_same_moment(app, session, monkeypatch):
    """The last-admin check and the change happen under one lock, so exactly one succeeds."""
    import threading
    import time

    from app import admin as admin_module

    a = make_user(session, "a@example.test", admin=True)
    b = make_user(session, "b@example.test", admin=True)
    real = admin_module._is_last_admin

    def slow(db, user):
        answer = real(db, user)
        time.sleep(0.2)  # widen the gap between the check and the change
        return answer

    monkeypatch.setattr(admin_module, "_is_last_admin", slow)
    outcomes: dict[str, int] = {}
    ready = threading.Barrier(2)

    def go(email, victim):
        web = visitor(app, f"198.51.100.{60 + len(outcomes)}")
        web.log_in(email=email)
        ready.wait()
        outcomes[email] = web.call("DELETE", f"/api/admin/users/{victim.id}").status_code

    threads = [
        threading.Thread(target=go, args=("a@example.test", b)),
        threading.Thread(target=go, args=("b@example.test", a)),
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert sorted(outcomes.values()) == [202, 401] or sorted(outcomes.values()) == [202, 409]
    session.expire_all()
    active = session.scalars(select(User).where(User.is_admin, User.status == "active")).all()
    assert len(active) == 1


def test_deleting_marks_the_user_deleted_at_once_and_signs_them_out(app, boss, session, owner):
    web = visitor(app, "198.51.100.30")
    web.log_in()
    response = boss.call("DELETE", f"/api/admin/users/{owner.id}")
    assert (response.status_code, response.json()) == (202, {"status": "deleting"})
    session.refresh(owner)
    assert owner.status == "deleted" and owner.deleted_at is not None
    error_of(web.get("/api/templates"), 401, "not_authenticated")
    error_of(visitor(app, "198.51.100.31").log_in(), 401, "invalid_credentials")
    assert session.get(User, owner.id) is not None  # the worker purges it


def test_the_reset_link_is_returned_once_and_works(app, boss, session, owner):
    response = boss.call("POST", f"/api/admin/users/{owner.id}/reset-link")
    assert response.status_code == 200
    url = response.json()["reset_url"]
    assert url.startswith("https://notes.beyrictech.com/reset-password?token=")
    token = re.search(r"token=([\w-]+)", url)[1]
    assert queued(session) == []  # nothing is mailed: the admin hands it over
    new = "-".join(["admin", "issued", "pass", "5"])
    anon = visitor(app, "198.51.100.40")
    assert anon.post("/api/password/reset", {"token": token, "password": new}).status_code == 200
    assert visitor(app, "198.51.100.41").log_in(new).status_code == 200
    stored = session.scalars(select(AuthToken)).all()
    assert all(t.token_hash != token for t in stored)  # only the hash is kept


def test_a_reset_link_replaces_the_one_before_it(boss, session, owner):
    first = re.search(
        r"token=([\w-]+)",
        boss.call("POST", f"/api/admin/users/{owner.id}/reset-link").json()["reset_url"],
    )[1]
    boss.call("POST", f"/api/admin/users/{owner.id}/reset-link")
    live = session.scalars(select(AuthToken).where(AuthToken.used_at.is_(None))).all()
    assert len(live) == 1 and live[0].token_hash != tokens.digest(first)


def test_a_reset_link_tells_the_user_and_logs_no_token(boss, session, owner, caplog):
    caplog.set_level("INFO")
    url = boss.call("POST", f"/api/admin/users/{owner.id}/reset-link").json()["reset_url"]
    token = re.search(r"token=([\w-]+)", url)[1]
    [note] = session.scalars(select(Notification).where(Notification.user_id == owner.id)).all()
    assert note.kind == "account.reset_link_issued" and note.severity == "warning"
    assert "administrator" in note.body
    ours = " ".join(r.getMessage() for r in caplog.records if r.name.startswith("app"))
    assert token not in ours and OWNER_EMAIL not in ours
    assert f"user {owner.id}" in ours


def test_no_reset_link_for_an_admin_account(boss, admin, session):
    second = make_user(session, "second@example.test", admin=True)
    error_of(boss.call("POST", f"/api/admin/users/{second.id}/reset-link"), 409, "admin_account")
    error_of(boss.call("POST", f"/api/admin/users/{admin.id}/reset-link"), 409, "admin_account")
    assert session.scalars(select(AuthToken)).all() == []


def test_no_reset_link_for_an_unverified_or_suspended_account(boss, session):
    unverified = make_user(session, "u@example.test", email_verified_at=None)
    suspended = make_user(session, "s@example.test", status="suspended")
    error_of(
        boss.call("POST", f"/api/admin/users/{unverified.id}/reset-link"), 409, "user_not_verified"
    )
    error_of(
        boss.call("POST", f"/api/admin/users/{suspended.id}/reset-link"), 409, "account_suspended"
    )


# ---- pilot settings ----


def test_the_pilot_settings_default_to_closed(boss):
    body = boss.get("/api/admin/pilot").json()
    assert body == {
        "signups_open": False,
        "max_accounts": 10,
        "default_hours_cap_seconds": 18000,
        "password_signup_enabled": False,
        "slots_taken": 0,
        "state": "closed",
    }


def test_the_admin_opens_the_pilot_and_the_public_endpoint_follows(boss, anon, session, owner):
    body = {
        "signups_open": True,
        "max_accounts": 2,
        "default_hours_cap_seconds": 7200,
        "password_signup_enabled": True,
    }
    response = boss.call("PUT", "/api/admin/pilot", body)
    assert response.status_code == 200
    assert response.json() == body | {"slots_taken": 1, "state": "open"}
    assert anon.get("/api/pilot").json()["state"] == "open"
    make_user(session, "two@example.test")
    assert anon.get("/api/pilot").json()["state"] == "full"
    assert session.get(AppState, 1).default_hours_cap_seconds == 7200


def test_the_caps_can_be_removed(boss, session):
    body = {
        "signups_open": True,
        "max_accounts": None,
        "default_hours_cap_seconds": None,
        "password_signup_enabled": False,
    }
    assert boss.call("PUT", "/api/admin/pilot", body).json()["max_accounts"] is None
    session.expire_all()
    state = session.get(AppState, 1)
    assert (state.max_accounts, state.default_hours_cap_seconds) == (None, None)


@pytest.mark.parametrize(
    "over",
    [{"max_accounts": -1}, {"max_accounts": 10**6}, {"default_hours_cap_seconds": -5},
     {"signups_open": "yes"}, {"extra": 1}],
)  # fmt: skip
def test_the_pilot_body_is_strict(boss, over):
    body = {
        "signups_open": True,
        "max_accounts": 5,
        "default_hours_cap_seconds": 100,
        "password_signup_enabled": True,
    } | over
    error_of(boss.call("PUT", "/api/admin/pilot", body), 422, "validation_error")


def test_the_pilot_body_needs_every_field(boss):
    error_of(boss.call("PUT", "/api/admin/pilot", {"signups_open": True}), 422, "validation_error")


# ---- the waitlist, admin side ----


def test_the_admin_sees_the_waitlist_oldest_first(boss, session):
    entry(session, "a@example.test", name="A")
    entry(session, "b@example.test")
    entry(session, "gone@example.test", status="removed")
    body = boss.get("/api/admin/waitlist").json()
    assert [(e["email"], e["name"], e["status"]) for e in body["entries"]] == [
        ("a@example.test", "A", "waiting"),
        ("b@example.test", None, "waiting"),
    ]
    assert set(body["entries"][0]) == {"id", "email", "name", "status", "created_at", "invited_at"}


def test_an_invite_reserves_a_slot_and_is_mailed_when_mail_works(boss, session):
    open_pilot(session, max_accounts=1)
    wait = entry(session)
    response = boss.call("POST", f"/api/admin/waitlist/{wait.id}/invite")
    assert response.status_code == 200
    body = response.json()
    assert body["emailed"] is True and body["invite_url"] is None
    session.refresh(wait)
    assert wait.status == "invited" and wait.invited_at is not None and wait.invite_token_id
    [row] = queued(session, "waitlist_invite")
    assert row.to_email == "wait@example.test"
    assert row.payload["link"].startswith("https://notes.beyrictech.com/signup?invite=")
    token = session.get(AuthToken, wait.invite_token_id)
    assert token.purpose == "waitlist_invite" and token.email == "wait@example.test"
    assert timedelta(days=6, hours=23) < token.expires_at - datetime.now(UTC) <= timedelta(days=7)
    assert signup.pilot_state(session, datetime.now(UTC)) == "full"  # the slot is held


def test_with_no_mail_provider_the_invite_link_is_shown_to_the_admin(app, boss, session):
    app.state.settings = make_settings(app_env="production", session_secret="x" * 40)
    open_pilot(session)
    wait = entry(session)
    body = boss.call("POST", f"/api/admin/waitlist/{wait.id}/invite").json()
    assert body["emailed"] is False
    assert body["invite_url"].startswith("https://notes.beyrictech.com/signup?invite=")
    assert queued(session) == []


def test_the_invited_person_signs_up_with_the_link_when_the_pilot_is_full_or_closed(
    boss, anon, session
):
    open_pilot(session, max_accounts=1)
    wait = entry(session)
    boss.call("POST", f"/api/admin/waitlist/{wait.id}/invite")
    token = re.search(r"invite=([\w-]+)", queued(session, "waitlist_invite")[0].payload["link"])[1]
    error_of(anon.post("/api/signup", signup_body()), 409, "pilot_full")  # without the token
    assert anon.post("/api/signup", signup_body(invite_token=token)).status_code == 202
    session.expire_all()
    assert session.scalars(select(User).where(User.email == "wait@example.test")).one()
    assert session.get(WaitlistEntry, wait.id).status == "joined"
    error_of(
        anon.post("/api/signup", signup_body(invite_token=token)), 400, "invite_invalid"
    )  # once


def test_an_invite_token_is_for_its_own_address_only(boss, anon, session):
    open_pilot(session, max_accounts=1)
    wait = entry(session)
    boss.call("POST", f"/api/admin/waitlist/{wait.id}/invite")
    token = re.search(r"invite=([\w-]+)", queued(session, "waitlist_invite")[0].payload["link"])[1]
    error_of(
        anon.post(
            "/api/signup", signup_body(email="someone-else@example.test", invite_token=token)
        ),
        400,
        "invite_invalid",
    )


def test_an_invite_works_while_the_pilot_is_closed(boss, anon, session):
    open_pilot(session, signups_open=False)
    wait = entry(session)
    boss.call("POST", f"/api/admin/waitlist/{wait.id}/invite")
    token = re.search(r"invite=([\w-]+)", queued(session, "waitlist_invite")[0].payload["link"])[1]
    assert anon.post("/api/signup", signup_body(invite_token=token)).status_code == 202


def test_an_expired_or_garbage_invite_is_invalid(boss, anon, session):
    open_pilot(session)
    wait = entry(session)
    boss.call("POST", f"/api/admin/waitlist/{wait.id}/invite")
    token = re.search(r"invite=([\w-]+)", queued(session, "waitlist_invite")[0].payload["link"])[1]
    session.scalars(select(AuthToken)).one().expires_at = datetime.now(UTC) - timedelta(seconds=1)
    session.commit()
    error_of(anon.post("/api/signup", signup_body(invite_token=token)), 400, "invite_invalid")
    error_of(anon.post("/api/signup", signup_body(invite_token="nonsense")), 400, "invite_invalid")


def test_no_invite_when_the_pilot_has_no_free_slot(boss, session):
    open_pilot(session, max_accounts=1)
    make_user(session, "member@example.test")
    wait = entry(session)
    error_of(boss.call("POST", f"/api/admin/waitlist/{wait.id}/invite"), 409, "pilot_full")


def test_inviting_again_replaces_the_old_link_and_holds_one_slot(boss, session):
    open_pilot(session, max_accounts=1)
    wait = entry(session)
    boss.call("POST", f"/api/admin/waitlist/{wait.id}/invite")
    boss.call("POST", f"/api/admin/waitlist/{wait.id}/invite")
    live = session.scalars(select(AuthToken).where(AuthToken.used_at.is_(None))).all()
    assert len(live) == 1
    assert signup.slots_taken(session, datetime.now(UTC)) == 1


@pytest.mark.parametrize("status", ["joined", "removed"])
def test_a_closed_entry_cannot_be_invited(boss, session, status):
    open_pilot(session)
    wait = entry(session, status=status)
    error_of(
        boss.call("POST", f"/api/admin/waitlist/{wait.id}/invite"), 409, "waitlist_entry_closed"
    )


def test_removing_an_entry_frees_its_reserved_slot(boss, session):
    open_pilot(session, max_accounts=1)
    wait = entry(session)
    boss.call("POST", f"/api/admin/waitlist/{wait.id}/invite")
    assert boss.call("DELETE", f"/api/admin/waitlist/{wait.id}").status_code == 204
    session.refresh(wait)
    assert wait.status == "removed"
    assert signup.pilot_state(session, datetime.now(UTC)) == "open"


def test_an_unknown_waitlist_entry_is_404(boss):
    error_of(boss.call("POST", "/api/admin/waitlist/9999/invite"), 404, "not_found")
    error_of(boss.call("DELETE", "/api/admin/waitlist/9999"), 404, "not_found")


def signup_body(**over) -> dict:
    return {
        "name": "Wendy",
        "email": "wait@example.test",
        "password": GOOD,
        "accept_terms": True,
    } | over


# ---- mail failures ----


def test_the_admin_sees_failed_mail_without_links(boss, session):
    row = mail.enqueue(session, "x@example.test", "verify_email", {"link": "https://x/?token=T"})
    row.status, row.attempts, row.last_error, row.payload = "failed", 4, "HTTP 500", {}
    mail.enqueue(session, "ok@example.test", "verify_email", {"link": "https://x/?token=U"})
    session.commit()
    body = boss.get("/api/admin/mail-failures").json()
    [failure] = body["failures"]
    assert failure["to_email"] == "x@example.test" and failure["last_error"] == "HTTP 500"
    assert set(failure) == {"id", "to_email", "template", "attempts", "last_error", "created_at"}
    assert "token" not in str(body)


# ---- deleting an account for good ----


class FakeVexa:
    def __init__(self, fail_stop=False, fail_delete=False, lookups=None):
        self.stopped, self.deleted = [], []
        self.fail_stop, self.fail_delete = fail_stop, fail_delete
        self.lookups = lookups or {}  # Vexa meeting id -> VexaMeeting (or an exception)
        self.looked_up = []

    def stop_bot(self, platform, native_id):
        if self.fail_stop:
            raise VexaError("down")
        self.stopped.append((platform, native_id))

    def get_meeting_by_id(self, vexa_meeting_id):
        self.looked_up.append(str(vexa_meeting_id))
        found = self.lookups[str(vexa_meeting_id)]
        if isinstance(found, Exception):
            raise found
        return found

    def delete_recording(self, recording_id):
        if self.fail_delete:
            raise VexaError("down")
        self.deleted.append(recording_id)


def full_account(session, tmp_path, email="doomed@example.test"):
    user = make_user(session, email)
    tpl = Template(owner_user_id=user.id, name="General", instructions="x")
    session.add_all([tpl, VocabularyTerm(owner_user_id=user.id, term="Beyric")])
    session.flush()
    session.add(UserSettings(user_id=user.id, calendar_template_id=tpl.id))
    running = add_meeting(session, tpl, status="bot_sent", native_id="run-ning-bot")
    done = add_meeting(
        session, tpl, status="ready", step="done", native_id="don-e", vexa_recording_id=42
    )
    session.add(
        UsageEntry(user_id=user.id, email_hash=signup_hash(email), meeting_id=done.id, seconds=900)
    )
    session.add(
        Notification(user_id=user.id, kind="k", title="t", dedupe_key="d", meeting_id=done.id)
    )
    session.add(MailOutbox(to_email=email, template="verify_email", payload={"link": "x"}))
    invite = AuthToken(
        email=email,
        purpose="waitlist_invite",
        token_hash=signup_hash("t" + email),
        expires_at=datetime.now(UTC) + timedelta(days=1),
    )
    session.add(invite)
    session.add(WaitlistEntry(email=email, status="joined"))
    session.commit()
    for meeting in (running, done):
        folder = tmp_path / str(meeting.id)
        folder.mkdir()
        (folder / "source.webm").write_bytes(b"audio")
    return user, running, done


def signup_hash(email):
    from app import usage

    return usage.email_hash(email)


@pytest.fixture
def audio_dir(tmp_path, monkeypatch):
    from app import pipeline
    from app.config import Settings

    monkeypatch.setattr(pipeline, "get_settings", lambda: Settings(audio_dir=str(tmp_path)))
    return tmp_path


def test_purging_removes_everything_of_the_user_and_keeps_only_the_ledger(session, audio_dir):
    user, running, done = full_account(session, audio_dir)
    other = make_user(session, "other@example.test")
    vexa = FakeVexa()
    deletion.purge_user(session, user, vexa)
    session.expire_all()
    assert session.get(User, user.id) is None
    for model in (
        Meeting,
        Template,
        VocabularyTerm,
        UserSettings,
        Notification,
        MailOutbox,
        WaitlistEntry,
    ):
        assert session.scalar(select(func.count()).select_from(model)) == 0, model
    assert session.scalar(select(func.count()).select_from(AuthToken)) == 0
    assert vexa.stopped == [("google_meet", "run-ning-bot")]
    assert vexa.deleted == [42]
    assert not (audio_dir / str(running.id)).exists() and not (audio_dir / str(done.id)).exists()
    [ledger] = session.scalars(select(UsageEntry)).all()
    assert (ledger.user_id, ledger.meeting_id, ledger.seconds) == (None, None, 900)
    assert ledger.email_hash == signup_hash("doomed@example.test")
    assert session.get(User, other.id) is not None


def test_signing_up_again_does_not_reset_the_hours(session, audio_dir):
    from app import usage

    user, *_ = full_account(session, audio_dir)
    deletion.purge_user(session, user, FakeVexa())
    again = make_user(session, "doomed@example.test")
    assert usage.hours_used(session, again, datetime.now(UTC)) == 900


def test_purging_leaves_other_users_data_alone(session, audio_dir):
    user, *_ = full_account(session, audio_dir)
    other, other_running, _ = full_account(session, audio_dir, "keeper@example.test")
    deletion.purge_user(session, user, FakeVexa())
    session.expire_all()
    assert session.get(User, other.id) is not None
    assert (
        session.scalar(
            select(func.count()).select_from(Meeting).where(Meeting.owner_user_id == other.id)
        )
        == 2
    )
    assert (audio_dir / str(other_running.id)).exists()


def live_meeting_account(session, audio_dir, vexa_id="77"):
    user = make_user(session, "live@example.test")
    tpl = Template(owner_user_id=user.id, name="General", instructions="x")
    session.add(tpl)
    session.flush()
    running = add_meeting(
        session, tpl, status="bot_sent", native_id="liv-eliv-eli", vexa_meeting_id=vexa_id
    )
    (audio_dir / str(running.id)).mkdir()
    return user, running


def test_a_recording_vexa_has_not_told_us_about_yet_is_found_and_deleted(session, audio_dir):
    """The pipeline learns the recording id later, so a live meeting may not have it stored."""
    user, running = live_meeting_account(session, audio_dir)
    assert running.vexa_recording_id is None
    done = VexaMeeting(status="completed", recording_id=55, recording_status="completed")
    vexa = FakeVexa(lookups={"77": done})
    deletion.purge_user(session, user, vexa)
    assert vexa.stopped == [("google_meet", "liv-eliv-eli")]
    assert vexa.looked_up == ["77"] and vexa.deleted == [55]
    session.expire_all()
    assert session.get(User, user.id) is None


def test_a_meeting_vexa_is_still_finishing_keeps_the_account_until_the_copy_can_be_deleted(
    session, audio_dir
):
    user, _ = live_meeting_account(session, audio_dir)
    vexa = FakeVexa(lookups={"77": VexaMeeting(status="active")})
    with pytest.raises(VexaError):
        deletion.purge_user(session, user, vexa)
    session.expire_all()
    assert session.get(User, user.id) is not None
    assert session.scalar(select(func.count()).select_from(Meeting)) == 1


def test_a_finished_meeting_with_no_recording_yet_waits_out_the_recording_grace(session, audio_dir):
    """`completed` with no recording id is not proof that none will appear: the pipeline itself
    waits RECORDING_GRACE after first seeing the meeting over. The purge measures it from the
    moment IT first saw Vexa report the meeting over (stamped on the row), never from creation."""
    from app import pipeline

    user, running = live_meeting_account(session, audio_dir)
    running.created_at = datetime.now(UTC) - timedelta(days=2)  # old, and it must not matter
    session.commit()
    vexa = FakeVexa(lookups={"77": VexaMeeting(status="completed")})
    with pytest.raises(VexaError):  # first sight: stamp it and wait
        deletion.purge_user(session, user, vexa)
    session.refresh(running)
    assert running.terminal_seen_at is not None  # kept although the attempt raised
    running.terminal_seen_at = datetime.now(UTC) - pipeline.RECORDING_GRACE + timedelta(minutes=1)
    session.commit()
    with pytest.raises(VexaError):  # seen over, but not long enough ago
        deletion.purge_user(session, user, vexa)
    running.terminal_seen_at = datetime.now(UTC) - pipeline.RECORDING_GRACE - timedelta(minutes=1)
    session.commit()
    deletion.purge_user(session, user, vexa)  # the grace has passed with no recording
    assert vexa.deleted == []
    session.expire_all()
    assert session.get(User, user.id) is None


def test_a_recording_that_exists_but_has_no_id_yet_is_waited_for(session, audio_dir):
    user, _ = live_meeting_account(session, audio_dir)
    vexa = FakeVexa(lookups={"77": VexaMeeting(status="completed", recording_status="in_progress")})
    with pytest.raises(VexaError):
        deletion.purge_user(session, user, vexa)
    session.expire_all()
    assert session.get(User, user.id) is not None


def test_a_recording_vexa_says_failed_is_nothing_to_delete_at_once(session, audio_dir):
    user, _ = live_meeting_account(session, audio_dir)
    vexa = FakeVexa(lookups={"77": VexaMeeting(status="completed", recording_status="failed")})
    deletion.purge_user(session, user, vexa)
    assert vexa.deleted == []
    session.expire_all()
    assert session.get(User, user.id) is None


def test_a_recording_still_in_progress_keeps_the_purge_waiting_for_the_give_up(session, audio_dir):
    """Audio may still be written: only the 24-hour give-up (with its admin alert) ends the wait."""
    from app import pipeline

    user, running = live_meeting_account(session, audio_dir)
    running.terminal_seen_at = datetime.now(UTC) - pipeline.RECORDING_GRACE - timedelta(hours=1)
    session.commit()
    vexa = FakeVexa(lookups={"77": VexaMeeting(status="completed", recording_status="in_progress")})
    with pytest.raises(VexaError):
        deletion.purge_user(session, user, vexa)
    session.expire_all()
    assert session.get(User, user.id) is not None


def test_a_completed_recording_without_audio_is_nothing_to_delete_after_the_grace(
    session, audio_dir
):
    from app import pipeline

    user, running = live_meeting_account(session, audio_dir)
    running.terminal_seen_at = datetime.now(UTC) - pipeline.RECORDING_GRACE - timedelta(minutes=1)
    session.commit()
    vexa = FakeVexa(lookups={"77": VexaMeeting(status="completed", recording_status="completed")})
    deletion.purge_user(session, user, vexa)  # a recording exists but holds no audio media
    assert vexa.deleted == []
    session.expire_all()
    assert session.get(User, user.id) is None


def test_a_bot_is_stopped_whatever_our_own_status_says_and_the_purge_waits_for_it(
    session, audio_dir
):
    """A meeting given up after 6 hours is `failed` here but its bot may still be in the call."""
    user, running = live_meeting_account(session, audio_dir)
    running.status, running.failure_reason = "failed", "gave up"
    running.created_at = datetime.now(UTC) - timedelta(hours=7)
    session.commit()
    vexa = FakeVexa(lookups={"77": VexaMeeting(status="active")})
    with pytest.raises(VexaError):
        deletion.purge_user(session, user, vexa)
    assert vexa.stopped == [("google_meet", "liv-eliv-eli")]
    vexa.lookups["77"] = VexaMeeting(status="completed")  # the bot has left, nothing recorded
    with pytest.raises(VexaError):  # the purge sees it over for the first time: the grace starts
        deletion.purge_user(session, user, vexa)
    running.terminal_seen_at = datetime.now(UTC) - timedelta(hours=1)
    session.commit()
    deletion.purge_user(session, user, vexa)
    session.expire_all()
    assert session.get(User, user.id) is None


def test_a_meeting_vexa_does_not_know_is_settled(session, audio_dir):
    from app.vexa import VexaNotFound

    user, running = live_meeting_account(session, audio_dir)
    running.vexa_recording_id = 31
    session.commit()
    vexa = FakeVexa(lookups={"77": VexaNotFound("gone")})
    deletion.purge_user(session, user, vexa)
    assert vexa.deleted == [31]  # a stored copy is still asked to be removed (Vexa 404 is fine)
    session.expire_all()
    assert session.get(User, user.id) is None


def test_a_404_with_no_stored_recording_is_settled_but_logged_with_the_meeting_id(
    session, audio_dir, caplog
):
    from app.vexa import VexaNotFound

    user, running = live_meeting_account(session, audio_dir)
    meeting_id = running.id
    caplog.set_level("ERROR")
    vexa = FakeVexa(lookups={"77": VexaNotFound("gone")})
    deletion.purge_user(session, user, vexa)
    errors = [r.getMessage() for r in caplog.records if r.levelname == "ERROR"]
    assert any(f"meeting {meeting_id}" in e and "404" in e for e in errors)
    assert "live@example.test" not in " ".join(errors)


def test_deleting_an_account_stamps_when_and_the_purge_stamps_it_if_it_was_missing(
    session, audio_dir
):
    user, _ = live_meeting_account(session, audio_dir)
    user.status = "deleted"
    session.commit()
    assert user.deleted_at is None  # marked by SQL, not by the admin API
    vexa = FakeVexa(lookups={"77": VexaMeeting(status="active")})
    assert deletion.purge_deleted(session, vexa) == 0
    session.refresh(user)
    assert user.deleted_at is not None  # the give-up clock starts at the first failed pass


def test_after_a_day_the_purge_gives_up_alerts_the_admin_and_deletes_our_rows(
    session, audio_dir, caplog
):
    admin = make_user(session, "root@example.test", admin=True)
    user, running = live_meeting_account(session, audio_dir)
    user.status = "deleted"
    user.deleted_at = datetime.now(UTC) - timedelta(hours=25)
    session.commit()
    ids = (user.id, running.id)

    class Never(FakeVexa):
        def get_meeting_by_id(self, vexa_meeting_id):
            raise VexaError("down")

    caplog.set_level("ERROR")
    vexa = Never()
    assert deletion.purge_deleted(session, vexa) == 1
    session.expire_all()
    assert session.get(User, ids[0]) is None and session.get(Meeting, ids[1]) is None
    # the attempt before giving up stopped the bot once; the forced pass calls Vexa no more
    assert vexa.deleted == [] and len(vexa.stopped) == 1
    [note] = session.scalars(select(Notification).where(Notification.user_id == admin.id)).all()
    assert (note.kind, note.severity) == ("admin.purge_forced", "error")
    assert str(ids[1]) in note.body and "live@example.test" not in note.body
    assert any(str(ids[1]) in r.getMessage() for r in caplog.records if r.levelname == "ERROR")
    assert "live@example.test" not in " ".join(r.getMessage() for r in caplog.records)


def test_before_a_day_is_up_the_purge_keeps_waiting(session, audio_dir):
    admin = make_user(session, "root@example.test", admin=True)
    user, _ = live_meeting_account(session, audio_dir)
    user.status = "deleted"
    user.deleted_at = datetime.now(UTC) - timedelta(hours=23)
    session.commit()
    vexa = FakeVexa(lookups={"77": VexaMeeting(status="active")})
    assert deletion.purge_deleted(session, vexa) == 0
    session.expire_all()
    assert session.get(User, user.id) is not None
    assert session.scalars(select(Notification).where(Notification.user_id == admin.id)).all() == []


def test_deleting_through_the_api_while_a_meeting_runs_ends_in_a_full_purge_and_no_openai(
    app, boss, session, owner, template, audio_dir
):
    """The admin deletes a user mid-meeting: the worker leaves the meeting alone, the bot is
    stopped, Vexa's copy is deleted and every row goes."""
    from tests.test_pipeline import DONE, FakeOpenAI
    from tests.test_pipeline import FakeVexa as PipelineVexa

    meeting = add_meeting(
        session, template, status="bot_sent", native_id="run-ning-bot", vexa_meeting_id="9"
    )
    (audio_dir / str(meeting.id)).mkdir()
    assert boss.call("DELETE", f"/api/admin/users/{owner.id}").status_code == 202
    openai, pipe_vexa = FakeOpenAI(), PipelineVexa(DONE)
    from app import pipeline

    pipeline.advance_meeting(session, meeting.id, vexa=pipe_vexa, openai_client=openai)
    assert pipe_vexa.get_calls == 0 and openai.transcriptions == 0 and openai.chats == 0
    done = VexaMeeting(status="completed", recording_id=8, recording_status="completed")
    vexa = FakeVexa(lookups={"9": done})
    assert deletion.purge_deleted(session, vexa) == 1
    assert vexa.stopped == [("google_meet", "run-ning-bot")] and vexa.deleted == [8]
    session.expire_all()
    assert session.scalar(select(func.count()).select_from(Meeting)) == 0
    assert session.get(User, owner.id) is None


def test_a_bot_that_never_recorded_leaves_nothing_at_vexa_to_delete(session, audio_dir):
    user, _ = live_meeting_account(session, audio_dir)
    vexa = FakeVexa(lookups={"77": VexaMeeting(status="failed")})
    deletion.purge_user(session, user, vexa)
    assert vexa.deleted == []
    session.expire_all()
    assert session.get(User, user.id) is None


def test_a_vexa_lookup_that_fails_keeps_the_account(session, audio_dir):
    user, _ = live_meeting_account(session, audio_dir)
    vexa = FakeVexa(lookups={"77": VexaError("down")})
    with pytest.raises(VexaError):
        deletion.purge_user(session, user, vexa)
    session.expire_all()
    assert session.get(User, user.id) is not None


def test_one_users_unexpected_error_does_not_stop_the_others(session, audio_dir):
    first, *_ = full_account(session, audio_dir, "first@example.test")
    second, *_ = full_account(session, audio_dir, "second@example.test")
    first.status = second.status = "deleted"
    session.commit()

    class Explodes(FakeVexa):
        def stop_bot(self, platform, native_id):
            if not hasattr(self, "tripped"):
                self.tripped = True
                raise ValueError("unsupported platform or meeting code")  # not a VexaError
            super().stop_bot(platform, native_id)

    assert deletion.purge_deleted(session, Explodes()) == 1
    session.expire_all()
    assert session.scalar(select(func.count()).select_from(User)) == 1


def test_background_jobs_survive_a_failing_job_and_a_failing_purge(session, monkeypatch, caplog):
    from app import worker

    calls = []

    def boom_mail(*args, **kwargs):
        calls.append("mail")
        raise RuntimeError("secret detail ada@example.test")

    def boom_purge(*args, **kwargs):
        calls.append("purge")
        raise ValueError("secret detail")

    monkeypatch.setattr(worker, "send_mail", boom_mail)
    monkeypatch.setattr(worker, "purge_deleted", boom_purge)
    caplog.set_level("WARNING")
    worker.background_jobs(session, None, datetime.now(UTC))  # must not raise
    assert calls == ["mail", "purge"]
    ours = " ".join(r.getMessage() for r in caplog.records)
    assert "RuntimeError" in ours and "ValueError" in ours
    assert "secret detail" not in ours and "ada@example.test" not in ours


def test_a_bot_that_cannot_be_stopped_keeps_the_account_to_try_again(session, audio_dir):
    user, *_ = full_account(session, audio_dir)
    with pytest.raises(VexaError):
        deletion.purge_user(session, user, FakeVexa(fail_stop=True))
    session.expire_all()
    assert session.get(User, user.id) is not None
    assert session.scalar(select(func.count()).select_from(Meeting)) == 2


def test_a_recording_that_cannot_be_deleted_at_vexa_keeps_the_account_to_try_again(
    session, audio_dir
):
    user, *_ = full_account(session, audio_dir)
    with pytest.raises(VexaError):
        deletion.purge_user(session, user, FakeVexa(fail_delete=True))
    session.expire_all()
    assert session.get(User, user.id) is not None


def test_the_worker_purges_users_marked_deleted_and_survives_a_failure(session, audio_dir):
    first, *_ = full_account(session, audio_dir, "first@example.test")
    second, *_ = full_account(session, audio_dir, "second@example.test")
    first.status = second.status = "deleted"
    keeper = make_user(session, "keeper@example.test")
    session.commit()

    class FailsOnFirst(FakeVexa):
        def stop_bot(self, platform, native_id):
            if not self.stopped and not hasattr(self, "tried"):
                self.tried = True
                raise VexaError("blip")
            super().stop_bot(platform, native_id)

    assert deletion.purge_deleted(session, FailsOnFirst()) == 1
    session.expire_all()
    remaining = {u.email for u in session.scalars(select(User))}
    assert "keeper@example.test" in remaining and len(remaining) == 2
    assert deletion.purge_deleted(session, FakeVexa()) == 1
    session.expire_all()
    assert {u.email for u in session.scalars(select(User))} == {keeper.email}


def test_purging_with_nobody_marked_deleted_does_nothing(session):
    assert deletion.purge_deleted(session, FakeVexa()) == 0


def test_the_script_refuses_to_delete_the_last_admin_and_asks_first(
    session, audio_dir, monkeypatch, capsys
):
    from scripts import delete_user

    make_user(session, ADMIN, admin=True)
    user, *_ = full_account(session, audio_dir)
    monkeypatch.setattr(delete_user, "SessionLocal", lambda: _Same(session))
    monkeypatch.setattr(delete_user, "build_provider", lambda: FakeVexa())
    assert delete_user.run([ADMIN, "--yes"]) == 1
    assert "last admin" in capsys.readouterr().err
    monkeypatch.setattr("builtins.input", lambda prompt="": "no")
    assert delete_user.run([user.email]) == 1
    session.expire_all()
    assert session.get(User, user.id) is not None
    assert delete_user.run([user.email, "--yes"]) == 0
    session.expire_all()
    assert session.get(User, user.id) is None
    assert delete_user.run(["nobody@example.test", "--yes"]) == 1


class _Same:
    """A session the script may `with` without closing the test's own."""

    def __init__(self, session):
        self.session = session

    def __enter__(self):
        return self.session

    def __exit__(self, *exc):
        return False


# ---- notifications ----


def note(session, user, n, **fields):
    row = Notification(
        user_id=user.id, kind="usage.hours_low", title=f"Note {n}", body="b",
        dedupe_key=f"k{n}", **fields,
    )  # fmt: skip
    session.add(row)
    session.commit()
    return row


def test_the_session_counts_unread_notifications(api, session, owner):
    assert api.get("/api/session").json()["unread_notifications"] == 0
    note(session, owner, 1)
    note(session, owner, 2, read_at=datetime.now(UTC))
    assert api.get("/api/session").json()["unread_notifications"] == 1


def test_a_visitor_has_no_unread_count(anon):
    assert anon.get("/api/session").json()["unread_notifications"] is None


def test_the_list_is_mine_only_newest_first_and_pages(api, session, owner, stranger):
    for n in range(3):
        note(session, owner, n)
    note(session, stranger, 9)
    body = api.get("/api/notifications?limit=2").json()
    assert [n["title"] for n in body["notifications"]] == ["Note 2", "Note 1"]
    assert body["has_more"] is True
    rest = api.get(f"/api/notifications?limit=2&before_id={body['next_before_id']}").json()
    assert [n["title"] for n in rest["notifications"]] == ["Note 0"] and rest["has_more"] is False
    assert set(body["notifications"][0]) == {
        "id", "kind", "severity", "title", "body", "meeting_id", "created_at", "read_at",
    }  # fmt: skip


def test_reading_one_marks_it_and_is_repeatable(api, session, owner):
    first = note(session, owner, 1)
    response = api.post(f"/api/notifications/{first.id}/read")
    assert response.status_code == 200 and response.json()["read_at"] is not None
    stamp = response.json()["read_at"]
    assert api.post(f"/api/notifications/{first.id}/read").json()["read_at"] == stamp
    assert api.get("/api/session").json()["unread_notifications"] == 0


def test_a_foreign_notification_is_404_and_stays_unread(api, session, stranger):
    theirs = note(session, stranger, 1)
    error_of(api.post(f"/api/notifications/{theirs.id}/read"), 404, "not_found")
    session.refresh(theirs)
    assert theirs.read_at is None


def test_read_all_marks_only_mine(api, session, owner, stranger):
    note(session, owner, 1)
    note(session, owner, 2)
    theirs = note(session, stranger, 3)
    body = api.post("/api/notifications/read-all").json()
    assert body == {"unread_notifications": 0}
    session.refresh(theirs)
    assert theirs.read_at is None


def test_the_script_checks_for_the_last_admin_under_the_admin_lock_after_confirming(
    session, audio_dir, monkeypatch
):
    from app import admin as admin_module
    from scripts import delete_user

    keeper = make_user(session, ADMIN, admin=True)
    second = make_user(session, "second@example.test", admin=True)
    seen = []
    real_execute = session.execute

    def spy(statement, *args, **kwargs):
        text_ = str(statement)
        if "pg_advisory_xact_lock" in text_:
            seen.append(("lock", args[0]["key"] if args else None))
        return real_execute(statement, *args, **kwargs)

    monkeypatch.setattr(session, "execute", spy)
    real_check = admin_module._is_last_admin
    monkeypatch.setattr(
        admin_module,
        "_is_last_admin",
        lambda db, user: seen.append(("check", user.id)) or real_check(db, user),
    )
    monkeypatch.setattr(delete_user, "SessionLocal", lambda: _Same(session))
    monkeypatch.setattr(delete_user, "build_provider", lambda: FakeVexa())
    assert delete_user.run([second.email, "--yes"]) == 0
    assert ("lock", admin_module.ADMIN_LOCK) in seen
    assert seen.index(("lock", admin_module.ADMIN_LOCK)) < max(
        i for i, item in enumerate(seen) if item[0] == "check"
    )  # the decisive check comes after the lock
    session.expire_all()
    assert session.get(User, keeper.id) is not None


def test_purging_an_account_removes_the_proposals_from_its_meetings_but_keeps_a_confirmed_one(
    session, audio_dir
):
    """Opus review: a proposal must not outlive the private meeting it came from."""
    from app import teams
    from app.models import Milestone

    user, running, done = full_account(session, audio_dir)
    team = teams.ensure_team(session, user)
    colleague = make_user(session, "colleague@example.test")
    session.add(TeamMember(team_id=team.id, user_id=colleague.id, role="admin"))
    stated = datetime(2026, 12, 8).date()
    session.add_all(
        [
            Milestone(team_id=team.id, name="Proposed", due_date=stated, source_meeting_id=done.id),
            Milestone(
                team_id=team.id,
                name="Confirmed",
                due_date=stated,
                source_meeting_id=done.id,
                confirmed_at=datetime(2026, 11, 3, tzinfo=UTC),
            ),
        ]
    )
    session.commit()
    deletion.purge_user(session, user, FakeVexa())
    session.expire_all()
    assert [m.name for m in session.scalars(select(Milestone))] == ["Confirmed"]
