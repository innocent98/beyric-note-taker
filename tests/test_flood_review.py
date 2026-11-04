"""Fixes from the Opus review of the flood defence and mail budget (PR #75): the reserve under a
backlog of more than a batch, a cap on reset requests, unlocking a locked account, the per-address
cap without a trusted address, and the cheaper findings."""

import logging
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select

from app import accounts, limits, maintenance, ratelimit, signup
from app.main import create_app
from app.models import MailOutbox, RateHit, User
from tests.test_accounts import visitor
from tests.test_api import anon, error_of  # noqa: F401  (fixtures used by name)
from tests.test_mail import queue
from tests.test_mail_budget import go, links, rows
from tests.test_mail_upgrade import NOW, plain  # noqa: F401
from tests.test_signup import forgot, open_pilot, queued, sign_up, token_of
from tests.test_web import (  # noqa: F401
    OWNER_EMAIL,
    PASSWORD,
    anon_client,
    app,
    fake_openai,
    fake_vexa,
    make_user,
    owner,
)

NEW_PASSWORD = "-".join(["a", "brand", "new", "pass", "7"])


# ---- H1: a backlog of held mail cannot hide a reset link, an invitation or an alert ----


def backlog(session, n=130):
    """More held verification mails than one pass loads, then the budget used by a first pass."""
    links(session, n)
    assert go(session, budget=10, reserve=4)[0] == 6
    go(session, now=NOW + timedelta(minutes=1), budget=10, reserve=4)  # every row has been held
    assert len(rows(session, "pending")) == n - 6
    assert {r.last_error for r in rows(session, "pending")} == {"over_budget"}


@pytest.mark.parametrize("template", ["reset_password", "waitlist_invite"])
def test_a_protected_mail_behind_a_backlog_of_held_mail_still_goes(session, template):
    backlog(session)
    queue(session, template, {"link": "https://x.test/l"}, to="real@example.test")
    sent, mailer = go(session, budget=10, reserve=4)
    assert sent == 1 and mailer.sent[0][0].to == "real@example.test"


def test_an_admin_alert_behind_a_backlog_of_held_mail_still_goes(session):
    make_user(session, "boss@example.test", admin=True)
    backlog(session)
    queue(session, "alert", {"title": "T", "body": "B"}, to="boss@example.test")
    sent, mailer = go(session, budget=10, reserve=4)
    assert sent == 1 and mailer.sent[0][0].to == "boss@example.test"


def test_new_verification_mail_is_not_stuck_behind_the_held_backlog(session):
    """Mail that was never held is looked at before the held backlog, so a real person's
    verification mail goes as soon as there is room, not after the attacker's."""
    backlog(session)
    queue(
        session, "verify_email", {"link": "https://x.test/v", "name": "R"}, to="fresh@example.test"
    )
    sent, mailer = go(session, now=NOW + timedelta(minutes=61), budget=10, reserve=4)
    assert sent == 6 and mailer.sent[0][0].to == "fresh@example.test"


# ---- M1: reset requests are capped per email, so a flood cannot spend the budget or the inbox ----


def test_reset_requests_for_one_email_are_capped_per_hour(anon, session, owner):
    store = anon.client.app.state.rate_store
    for _ in range(6):
        assert forgot(anon, OWNER_EMAIL).status_code == 202
        store.clear("forgot_email", limits.email_key(OWNER_EMAIL))  # the minute passes
    assert len(queued(session, "reset_password")) == 3  # the rest answered 202 and did nothing


def test_the_reset_cap_is_per_email_not_for_everyone(anon, session, owner):
    make_user(session, "other@example.test")
    store = anon.client.app.state.rate_store
    for _ in range(4):
        forgot(anon, OWNER_EMAIL)
        store.clear("forgot_email", limits.email_key(OWNER_EMAIL))
    forgot(anon, "other@example.test")
    assert len(queued(session, "reset_password", to="other@example.test")) == 1


# ---- M2: a locked account is unlocked by a password reset, and by the operator's script ----


def lock_out(app, email=OWNER_EMAIL):
    for i in range(10):
        error_of(
            visitor(app, f"203.0.113.{i}").log_in("guess", email=email), 401, "invalid_credentials"
        )
    error_of(visitor(app, "203.0.113.200").log_in(email=email), 429, "too_many_attempts")


def test_a_password_reset_unlocks_the_account_for_a_new_device(app, session, owner):
    lock_out(app)
    other = visitor(app, "198.51.100.77")
    forgot(other, OWNER_EMAIL)
    token = token_of(queued(session, "reset_password")[-1])
    assert (
        other.post("/api/password/reset", {"token": token, "password": NEW_PASSWORD}).status_code
        == 200
    )
    fresh = visitor(app, "198.51.100.78")
    assert fresh.log_in(NEW_PASSWORD).status_code == 200  # no longer refused


def test_a_reset_unlocks_only_that_account(app, session, owner):
    make_user(session, "other@example.test")
    lock_out(app, "other@example.test")
    forgot(visitor(app, "198.51.100.77"), OWNER_EMAIL)
    token = token_of(queued(session, "reset_password")[-1])
    visitor(app, "198.51.100.77").post(
        "/api/password/reset", {"token": token, "password": NEW_PASSWORD}
    )
    error_of(
        visitor(app, "198.51.100.79").log_in(email="other@example.test"), 429, "too_many_attempts"
    )


def test_the_unlock_script_clears_the_accounts_lockout_and_nothing_else(app, session, owner):
    from scripts import unlock_login

    lock_out(app)
    make_user(session, "other@example.test")
    lock_out(app, "other@example.test")
    store = app.state.rate_store
    secret_key_rows = len(session.scalars(select(RateHit)).all())
    assert unlock_login.unlock(store, OWNER_EMAIL) > 0
    assert unlock_login.unlock(store, OWNER_EMAIL) == 0  # nothing left to clear
    assert visitor(app, "198.51.100.80").log_in().status_code == 200
    error_of(
        visitor(app, "198.51.100.81").log_in(email="other@example.test"), 429, "too_many_attempts"
    )
    assert len(session.scalars(select(RateHit)).all()) < secret_key_rows


def test_the_unlock_script_refuses_what_is_not_an_email(capsys):
    from scripts import unlock_login

    assert unlock_login.run(["not an email"]) == 1
    assert "not an email address" in capsys.readouterr().err


# ---- M3: no per-address cap where the address cannot be told apart ----


def production_app(app, secret=""):
    fresh = create_app(
        _settings(
            app_env="production",
            session_secret="p" * 40,
            resend_api_key="re_x",
            edge_shared_secret=secret,
        )
    )
    fresh.dependency_overrides = app.dependency_overrides
    return fresh


def _settings(**kw):
    from tests.test_web import make_settings

    return make_settings(**kw)


def test_without_the_edge_secret_in_production_the_48_hour_cap_is_skipped(app, session, caplog):
    """Every visitor would share one address (the proxy's): three sign-ups would close the form to
    everyone for two days. The cap is skipped and the log says why, once."""
    open_pilot(session, max_accounts=None)
    shop = production_app(app)
    api = visitor(shop, "172.28.1.10")
    with caplog.at_level(logging.WARNING):
        codes = [sign_up(api, email=f"n{n}@example.test").status_code for n in range(4)]
    assert codes == [202] * 4  # the cap of 3 did not apply
    warnings = [r for r in caplog.records if "per-address" in r.getMessage()]
    assert len(warnings) == 1
    session.expire_all()
    assert {u.signup_origin for u in session.scalars(select(User)) if u.email.startswith("n")} == {
        None
    }


def test_a_request_without_the_right_edge_secret_has_no_origin_either(app, session):
    open_pilot(session, max_accounts=None)
    shop = production_app(app, secret="s" * 40)
    api = visitor(shop, "172.28.1.10")
    api.client.headers.update({"X-Edge-Secret": "wrong" * 8, "X-Forwarded-For": "203.0.113.5"})
    assert [sign_up(api, email=f"w{n}@example.test").status_code for n in range(4)] == [202] * 4


def test_with_the_edge_secret_the_cap_applies_to_the_real_client_address(app, session):
    open_pilot(session, max_accounts=None)
    shop = production_app(app, secret="s" * 40)
    api = visitor(shop, "172.28.1.10")
    api.client.headers.update({"X-Edge-Secret": "s" * 40, "X-Forwarded-For": "203.0.113.5"})
    codes = [sign_up(api, email=f"e{n}@example.test").status_code for n in range(4)]
    assert codes == [202, 202, 202, 429]
    other = visitor(shop, "172.28.1.10")
    other.client.headers.update({"X-Edge-Secret": "s" * 40, "X-Forwarded-For": "203.0.113.6"})
    assert sign_up(other, email="elsewhere@example.test").status_code == 202


# ---- L1 and L2: the sweep keeps each bucket for its own time ----


def test_the_sweep_keeps_login_hits_two_hours_and_sign_up_hits_two_days(session):
    now = datetime.now(UTC)
    at = lambda hours: (now - timedelta(hours=hours)).timestamp()  # noqa: E731
    for bucket, hours in (
        ("login_client", 1), ("login_client", 3),
        ("signup_origin", 30), ("signup_origin", 50),
        ("login_account", 1.5), ("verify_wrong", 3),
    ):  # fmt: skip
        session.add(RateHit(bucket=bucket, key="k", at=at(hours)))
    session.commit()
    maintenance.sweep_rate_hits(session, now)
    left = sorted(
        (r.bucket, round((now.timestamp() - r.at) / 3600)) for r in session.scalars(select(RateHit))
    )
    assert left == [("login_account", 2), ("login_client", 1), ("signup_origin", 30)]


# ---- L7: only an admin's alert skips the budget ----


def test_an_alert_to_someone_who_is_not_an_admin_is_held_like_other_mail(session):
    make_user(session, "boss@example.test", admin=True)
    links(session, 2)
    assert go(session, budget=1, reserve=0)[0] == 1  # the budget is used up
    queue(session, "alert", {"title": "T", "body": "B"}, to="random@example.test")
    queue(session, "alert", {"title": "T", "body": "B"}, to="boss@example.test")
    sent, mailer = go(session, budget=1, reserve=0)
    assert [m.to for m, _ in mailer.sent] == ["boss@example.test"] and sent == 1


def test_an_alert_to_the_alert_mailbox_in_the_settings_is_an_admins(session):
    from tests.test_mail_upgrade import deliver

    links(session, 2)
    assert go(session, budget=1, reserve=0)[0] == 1
    queue(session, "alert", {"title": "T", "body": "B"}, to="ops@example.test")
    sent, mailer = deliver(
        session, mail_hourly_budget=1, mail_hourly_reserve=0, admin_alert_email="ops@example.test"
    )
    assert sent == 1 and mailer.sent[0][0].to == "ops@example.test"


# ---- L10: an evicted account's verification mail is not sent ----


def test_clearing_an_unconfirmed_account_drops_its_waiting_verification_mail(app, session):
    user = make_user(session, "gone@example.test", email_verified_at=None)
    accounts.add_user_defaults(session, user)
    body = {"link": "https://x.test/v"}
    waiting = queue(session, "verify_email", body, to="gone@example.test").id
    keep = queue(session, "verify_email", body, to="other@example.test").id
    done = queue(session, "verify_email", body, to="gone@example.test")
    done.status = "sent"
    session.commit()
    done = done.id
    accounts.delete_unverified(session, [user.id], [user.email])
    session.commit()
    left = {r.id for r in session.scalars(select(MailOutbox))}
    assert waiting not in left and keep in left and done in left


def test_the_mail_of_an_evicted_account_never_goes_out(app, session, monkeypatch):
    open_pilot(session, max_accounts=None)
    monkeypatch.setattr(signup, "MAX_UNVERIFIED", 3)
    a = ratelimit.origin_key(ratelimit.store_secret(app.state.settings), "203.0.113.1")
    for i in range(3):
        user = make_user(session, f"a{i}@example.test", email_verified_at=None)
        user.signup_origin = a
        user.terms_accepted_at = datetime.now(UTC) - timedelta(minutes=30 - i)
        accounts.add_user_defaults(session, user)
        queue(session, "verify_email", {"link": "https://x.test/v"}, to=user.email)
    assert sign_up(visitor(app, "198.51.100.30"), email="new@example.test").status_code == 202
    session.expire_all()
    assert [
        r.to_email for r in session.scalars(select(MailOutbox)) if r.to_email == "a0@example.test"
    ] == []
