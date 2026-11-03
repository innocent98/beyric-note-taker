"""Sign-up, email verification, resend, the 48-hour sweep and password reset (spec 4.3, 4.4)."""

import re
import threading
import time
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import func, select

from app import accounts, mail, passwords, signup, tokens
from app.models import (
    AppState,
    AuthToken,
    MailOutbox,
    Template,
    User,
    UserSettings,
    WaitlistEntry,
)
from tests.test_accounts import visitor
from tests.test_api import anon, error_of  # noqa: F401  (fixtures used by name)
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

GOOD = "-".join(["correct", "horse", "battery", "9"])
ATTACKER = "-".join(["attackers", "own", "pass", "1"])
NEW_EMAIL = "ada@example.test"


def open_pilot(session, *, max_accounts=10, password_signup=True, signups_open=True):
    state = session.get(AppState, 1) or AppState(id=1)
    state.signups_open = signups_open
    state.max_accounts = max_accounts
    state.password_signup_enabled = password_signup
    session.add(state)
    session.commit()


def form(**over) -> dict:
    return {
        "name": "Ada Lovelace",
        "email": NEW_EMAIL,
        "password": GOOD,
        "accept_terms": True,
    } | over


def queued(session, template=None, to=None) -> list[MailOutbox]:
    query = select(MailOutbox).order_by(MailOutbox.id)
    if template:
        query = query.where(MailOutbox.template == template)
    if to:
        query = query.where(MailOutbox.to_email == to)
    return list(session.scalars(query))


def token_of(row: MailOutbox) -> str:
    match = re.search(r"token=([\w-]+)", row.payload["link"])
    assert match, row.payload
    return match[1]


def sign_up(api, **over):
    return api.post("/api/signup", form(**over))


def a_minute_later(api):
    """Let the same address sign up again: the per-email limit is one a minute."""
    api.client.app.state.public_limits.reset()


def verify(api, token, password=GOOD):
    return api.post("/api/verify-email", {"token": token, "password": password})


def user_of(session, email=NEW_EMAIL) -> User | None:
    session.expire_all()
    return session.scalars(select(User).where(User.email == email)).first()


# ---- the pilot state ----


def test_the_pilot_is_closed_until_the_admin_opens_it(anon, session):
    assert anon.get("/api/pilot").json() == {
        "state": "closed",
        "prefill_email": None,
        "google_signin": False,
    }
    open_pilot(session)
    assert anon.get("/api/pilot").json() == {
        "state": "open",
        "prefill_email": None,
        "google_signin": False,
    }


def test_the_pilot_is_full_at_the_cap_and_the_admin_does_not_count(anon, session):
    open_pilot(session, max_accounts=2)
    make_user(session, "admin@example.test", admin=True)
    make_user(session, "one@example.test")
    assert anon.get("/api/pilot").json()["state"] == "open"
    make_user(session, "two@example.test", email_verified_at=None)  # unconfirmed: no place held
    assert anon.get("/api/pilot").json()["state"] == "open"
    make_user(session, "three@example.test")
    assert anon.get("/api/pilot").json()["state"] == "full"


def joined_unconfirmed(session, email=NEW_EMAIL, hours_old=1):
    """An account made from an invitation that is not confirmed yet: it still holds its place."""
    user = make_user(session, email, email_verified_at=None)
    session.add(WaitlistEntry(email=email, status="joined"))
    raw = tokens.issue(
        session,
        purpose="verify_email",
        email=email,
        user_id=user.id,
        now=datetime.now(UTC) - timedelta(hours=hours_old),
        pending_password_hash=user.password_hash,
    )
    session.commit()
    return user, raw


def test_an_unconfirmed_account_holds_no_place_but_one_made_from_an_invitation_does(anon, session):
    open_pilot(session, max_accounts=1)
    make_user(session, "plain@example.test", email_verified_at=None)
    assert anon.get("/api/pilot").json()["state"] == "open"
    joined_unconfirmed(session)
    assert anon.get("/api/pilot").json()["state"] == "full"
    assert signup.slots_taken(session, datetime.now(UTC)) == 1


def test_confirming_after_the_pilot_filled_up_is_refused_and_keeps_the_link(app, session):
    open_pilot(session, max_accounts=1)
    web = visitor(app, "198.51.100.1")
    sign_up(web)
    link = token_of(queued(session)[0])
    make_user(session, "member@example.test")  # takes the only place meanwhile
    error = error_of(verify(web, link), 409, "pilot_full")
    assert "filled up" in error["message"]
    assert user_of(session).email_verified_at is None
    open_pilot(session, max_accounts=2)  # room appears: the same link still works
    assert verify(web, link).status_code == 200


def test_an_invited_account_confirms_even_when_the_pilot_is_full(app, session):
    open_pilot(session, max_accounts=1)
    user, raw = joined_unconfirmed(session)
    assert signup.pilot_state(session, datetime.now(UTC)) == "full"
    web = visitor(app, "198.51.100.1")
    assert verify(web, raw, PASSWORD).status_code == 200


def test_ten_parallel_confirmations_at_nine_accounts_confirm_exactly_one(app, session, monkeypatch):
    open_pilot(session, max_accounts=10)
    for i in range(9):
        make_user(session, f"member{i}@example.test")
    racers = []
    for i in range(10):
        web = visitor(app, f"198.51.100.{i + 1}")
        assert sign_up(web, email=f"racer{i}@example.test").status_code == 202
        racers.append((web, token_of(queued(session, to=f"racer{i}@example.test")[0])))
    real = signup.has_room

    def slow(
        db, now
    ):  # widen the gap between "is there room" and "confirm" so only the lock decides
        answer = real(db, now)
        time.sleep(0.15)
        return answer

    monkeypatch.setattr(signup, "has_room", slow)
    results: list[int] = []
    threads = [
        threading.Thread(target=lambda w=w, t=t: results.append(verify(w, t).status_code))
        for w, t in racers
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert sorted(results) == [200] + [409] * 9
    session.expire_all()
    confirmed = session.scalars(select(User).where(User.email_verified_at.is_not(None))).all()
    assert len(confirmed) == 10


def test_at_most_a_hundred_unconfirmed_password_accounts_exist_at_once(app, session, monkeypatch):
    open_pilot(session, max_accounts=None)
    monkeypatch.setattr(signup, "MAX_UNVERIFIED", 2)
    for i in range(2):
        make_user(session, f"u{i}@example.test", email_verified_at=None)
    new = visitor(app, "198.51.100.1")
    existing = visitor(app, "198.51.100.2")
    # new and existing addresses get the same answer, so the cap tells nobody who has an account
    answers = [
        error_of(
            new.post("/api/signup", form(email="fresh@example.test")), 429, "too_many_requests"
        ),
        error_of(
            existing.post("/api/signup", form(email="u0@example.test")), 429, "too_many_requests"
        ),
    ]
    assert answers[0] == answers[1]
    assert user_of(session, "fresh@example.test") is None
    make_user(session, "member@example.test")  # confirmed accounts do not count toward it
    assert signup.MAX_UNVERIFIED == 2


def test_at_the_cap_abandoned_unconfirmed_accounts_are_cleared_to_make_room(
    app, session, monkeypatch
):
    """The cap must not be a way to lock everyone out: rows older than an hour are cleared."""
    open_pilot(session, max_accounts=None)
    monkeypatch.setattr(signup, "MAX_UNVERIFIED", 2)
    old = [make_user(session, f"old{i}@example.test", email_verified_at=None) for i in range(2)]
    for user in old:
        user.created_at = datetime.now(UTC) - timedelta(hours=2)
        accounts.add_user_defaults(session, user)
    invited, _ = joined_unconfirmed(session, "invited@example.test")
    invited.created_at = datetime.now(UTC) - timedelta(hours=2)
    session.commit()
    assert visitor(app, "198.51.100.1").post("/api/signup", form()).status_code == 202
    assert user_of(session) is not None
    assert (
        user_of(session, "old0@example.test") is None
        and user_of(session, "old1@example.test") is None
    )
    assert user_of(session, "invited@example.test") is not None  # its place was promised


def test_the_unconfirmed_cap_does_not_stop_an_invited_sign_up(app, session, monkeypatch):
    open_pilot(session, max_accounts=5)
    monkeypatch.setattr(signup, "MAX_UNVERIFIED", 1)
    make_user(session, "u0@example.test", email_verified_at=None)
    session.add(WaitlistEntry(email=NEW_EMAIL, status="invited"))
    raw = tokens.issue(
        session, purpose="waitlist_invite", email=NEW_EMAIL, user_id=None, now=datetime.now(UTC)
    )
    session.commit()
    assert (
        visitor(app, "198.51.100.1").post("/api/signup", form(invite_token=raw)).status_code == 202
    )
    assert user_of(session) is not None


def test_suspended_accounts_keep_their_slot_and_deleted_ones_free_it(anon, session):
    open_pilot(session, max_accounts=1)
    gone = make_user(session, "one@example.test", status="suspended")
    assert anon.get("/api/pilot").json()["state"] == "full"
    gone.status = "deleted"
    session.commit()
    assert anon.get("/api/pilot").json()["state"] == "open"


def test_no_cap_means_never_full(anon, session):
    open_pilot(session, max_accounts=None)
    for i in range(3):
        make_user(session, f"u{i}@example.test")
    assert anon.get("/api/pilot").json()["state"] == "open"


def test_the_pilot_endpoint_is_public_and_reports_no_counts(anon, session):
    open_pilot(session)
    body = anon.get("/api/pilot").json()
    assert set(body) == {"state", "prefill_email", "google_signin"}


# ---- sign-up ----


def test_signing_up_answers_202_and_creates_an_unverified_account(anon, session):
    open_pilot(session)
    response = sign_up(anon)
    assert response.status_code == 202
    assert response.json() == {"status": "check_your_email"}
    user = user_of(session)
    assert user.email_verified_at is None and user.status == "active" and not user.is_admin
    assert user.name == "Ada Lovelace"
    assert passwords.verify_password(user.password_hash, GOOD)
    assert user.terms_accepted_at is not None and user.terms_version == signup.TERMS_VERSION
    assert (
        len(session.scalars(select(Template).where(Template.owner_user_id == user.id)).all()) == 4
    )
    assert session.get(UserSettings, user.id) is not None


def test_sign_up_queues_one_verification_mail_with_a_token_that_expires_in_24_hours(anon, session):
    open_pilot(session)
    sign_up(anon)
    [row] = queued(session)
    assert (row.template, row.to_email) == ("verify_email", NEW_EMAIL)
    assert row.payload["link"].startswith("https://notes.beyrictech.com/verify-email?token=")
    assert row.payload["name"] == "Ada Lovelace"
    [token] = session.scalars(select(AuthToken)).all()
    assert token.purpose == "verify_email" and token.user_id == user_of(session).id
    assert token.token_hash != token_of(row) and len(token.token_hash) == 64  # only the hash
    age = token.expires_at - datetime.now(UTC)
    assert timedelta(hours=23, minutes=55) < age <= timedelta(hours=24)


def test_a_new_account_gets_no_session(anon, session):
    open_pilot(session)
    sign_up(anon)
    assert anon.get("/api/session").json()["authenticated"] is False
    error_of(anon.log_in(GOOD, NEW_EMAIL), 401, "invalid_credentials")


def test_sign_up_is_refused_while_the_pilot_is_closed(anon, session):
    open_pilot(session, signups_open=False)
    error_of(sign_up(anon), 403, "pilot_closed")
    assert user_of(session) is None and queued(session) == []


def test_sign_up_is_refused_when_the_pilot_is_full(anon, session):
    open_pilot(session, max_accounts=1)
    make_user(session, "one@example.test")
    error_of(sign_up(anon), 409, "pilot_full")
    assert user_of(session) is None


def test_full_wins_over_an_existing_email_so_the_answer_never_depends_on_who_has_an_account(
    anon, session
):
    open_pilot(session, max_accounts=1)
    make_user(session, NEW_EMAIL)
    error_of(sign_up(anon), 409, "pilot_full")


def test_password_sign_up_can_be_switched_off(anon, session):
    open_pilot(session, password_signup=False)
    error_of(sign_up(anon), 403, "password_signup_disabled")
    assert user_of(session) is None


@pytest.mark.parametrize(
    "over, status, code",
    [
        ({"password": "short"}, 422, "password_too_weak"),
        ({"password": "password"}, 422, "password_too_weak"),
        ({"password": "x" * 129}, 422, "password_too_weak"),
        ({"accept_terms": False}, 422, "terms_required"),
        ({"email": "not-an-email"}, 422, "invalid_email"),
        ({"email": "a@b"}, 422, "invalid_email"),
        ({"email": "ädä@example.test"}, 422, "invalid_email"),
        ({"email": "a" * 250 + "@example.test"}, 422, "invalid_email"),
        ({"name": ""}, 422, "invalid_name"),
        ({"name": "n" * 201}, 422, "invalid_name"),
    ],
)
def test_sign_up_validates_its_input(anon, session, over, status, code):
    open_pilot(session)
    error_of(sign_up(anon, **over), status, code)
    assert session.scalars(select(User)).all() == [] and queued(session) == []


def test_the_body_is_strict(anon, session):
    open_pilot(session)
    error_of(anon.post("/api/signup", {**form(), "is_admin": True}), 422, "validation_error")
    error_of(anon.post("/api/signup", {**form(), "accept_terms": "yes"}), 422, "validation_error")


def test_the_email_is_lower_cased_and_trimmed(anon, session):
    open_pilot(session)
    sign_up(anon, email="  Ada@Example.TEST ")
    assert user_of(session, NEW_EMAIL) is not None


def test_an_existing_verified_email_gets_the_same_answer_and_an_already_registered_mail(
    anon, session
):
    open_pilot(session)
    existing = make_user(session, NEW_EMAIL)
    before = existing.password_hash
    response = sign_up(anon, password=GOOD)
    assert (response.status_code, response.json()) == (202, {"status": "check_your_email"})
    session.refresh(existing)
    assert existing.password_hash == before  # the submitted password is ignored
    assert session.scalar(select(func.count()).select_from(User)) == 1
    [row] = queued(session)
    assert (row.template, row.to_email) == ("already_registered", NEW_EMAIL)
    assert row.payload["link"] == "https://notes.beyrictech.com/login"
    assert session.scalars(select(AuthToken)).all() == []


def test_an_unverified_email_gets_a_fresh_mail_and_the_newest_password_and_name(anon, session):
    open_pilot(session)
    sign_up(anon, name="Planted", password=ATTACKER)
    a_minute_later(anon)
    sign_up(anon)  # the real owner of the address signs up
    user = user_of(session)
    assert user.name == "Ada Lovelace" and passwords.verify_password(user.password_hash, GOOD)
    assert session.scalar(select(func.count()).select_from(User)) == 1
    assert (
        len(session.scalars(select(Template).where(Template.owner_user_id == user.id)).all()) == 4
    )
    assert len(queued(session, "verify_email")) == 2


def test_each_link_confirms_only_the_password_of_its_own_submission(anon, session):
    """Pre-registering a victim's address with a password you know must not work: the victim
    types the password they chose, and a link made by someone else's submission refuses it."""
    open_pilot(session)
    sign_up(anon, name="Planted", password=ATTACKER)
    a_minute_later(anon)
    sign_up(anon)  # the victim, later
    attackers_link, victims_link = (token_of(row) for row in queued(session, "verify_email"))
    error_of(verify(anon, attackers_link, GOOD), 400, "password_mismatch")  # victim clicked it
    assert user_of(session).email_verified_at is None
    assert verify(anon, victims_link, GOOD).status_code == 200
    user = user_of(session)
    assert passwords.verify_password(user.password_hash, GOOD)
    assert not passwords.verify_password(user.password_hash, ATTACKER)
    error_of(verify(anon, attackers_link, ATTACKER), 400, "token_invalid_or_expired")


def test_the_attackers_password_never_becomes_the_accounts_even_if_the_attacker_signed_up_last(
    anon, session
):
    open_pilot(session)
    sign_up(anon)  # the victim
    a_minute_later(anon)
    sign_up(anon, password=ATTACKER)  # the attacker re-submits afterwards
    victims_link, attackers_link = (token_of(row) for row in queued(session, "verify_email"))
    session.expire_all()
    a_minute_later(anon)
    assert verify(anon, victims_link, GOOD).status_code == 200
    user = user_of(session)
    assert passwords.verify_password(user.password_hash, GOOD)
    assert not passwords.verify_password(user.password_hash, ATTACKER)


def test_five_wrong_passwords_use_up_a_link_and_the_right_one_no_longer_works(anon, session):
    """A link's password cannot be guessed by trying many: after 5 misses the link is dead."""
    token = make_unverified(anon, session)
    for _ in range(5):
        error_of(verify(anon, token, ATTACKER), 400, "password_mismatch")
    error_of(verify(anon, token, GOOD), 400, "token_invalid_or_expired")
    assert user_of(session).email_verified_at is None


def test_wrong_guesses_on_one_link_do_not_count_against_another(anon, session):
    first = make_unverified(anon, session)
    a_minute_later(anon)
    sign_up(anon, password=ATTACKER)
    second = token_of(queued(session, "verify_email")[1])
    for _ in range(5):
        verify(anon, first, "-".join(["not", "it", "at", "all"]))
    error_of(verify(anon, first, GOOD), 400, "token_invalid_or_expired")
    assert verify(anon, second, ATTACKER).status_code == 200


def test_a_sign_up_cannot_overwrite_the_password_of_an_account_confirmed_a_moment_ago(
    app, session, monkeypatch
):
    """The sign-up reads an unverified account, and while it works the real owner confirms it. The
    row lock makes the confirmation wait, so the confirmed password is the owner's, not the
    attacker's. Without the lock the attacker's write lands after the confirmation."""
    import threading

    open_pilot(session)
    victim = visitor(app, "198.51.100.1")
    assert sign_up(victim).status_code == 202
    link = token_of(queued(session)[0])
    attacker = visitor(app, "198.51.100.2")
    a_minute_later(attacker)
    real = signup._send_verification

    def slow(*args, **kwargs):  # the gap between reading the account and writing to it
        time.sleep(0.6)
        return real(*args, **kwargs)

    monkeypatch.setattr(signup, "_send_verification", slow)
    results = {}
    thread = threading.Thread(
        target=lambda: results.update(attacker=sign_up(attacker, password=ATTACKER).status_code)
    )
    thread.start()
    time.sleep(0.2)  # the attacker's request now holds the account
    results["verify"] = verify(victim, link, GOOD).status_code
    thread.join()
    assert results == {"attacker": 202, "verify": 200}
    user = user_of(session)
    assert user.email_verified_at is not None
    assert passwords.verify_password(user.password_hash, GOOD)
    assert not passwords.verify_password(user.password_hash, ATTACKER)


def test_a_sign_up_that_finds_the_account_confirmed_by_then_only_mails_the_owner(app, session):
    open_pilot(session)
    make_user(session, NEW_EMAIL)  # confirmed already
    before = user_of(session).password_hash
    assert sign_up(visitor(app, "198.51.100.3"), password=ATTACKER).status_code == 202
    assert user_of(session).password_hash == before
    assert [r.template for r in queued(session)] == ["already_registered"]


def test_the_wrong_password_does_not_use_up_the_link(anon, session):
    token = make_unverified(anon, session)
    error_of(verify(anon, token, "-".join(["not", "it", "at", "all"])), 400, "password_mismatch")
    assert verify(anon, token).status_code == 200


def test_a_suspended_account_is_told_nothing_new(anon, session):
    open_pilot(session)
    make_user(session, NEW_EMAIL, status="suspended")
    assert sign_up(anon).status_code == 202


def test_every_sign_up_outcome_has_the_same_body_and_status(app, session):
    open_pilot(session)
    make_user(session, "taken@example.test")
    outcomes = {
        (r.status_code, str(r.json()))
        for r in (
            visitor(app, "198.51.100.1").post("/api/signup", form(email="fresh@example.test")),
            visitor(app, "198.51.100.2").post("/api/signup", form(email="taken@example.test")),
            visitor(app, "198.51.100.3").post("/api/signup", form(email="fresh@example.test")),
        )
    }
    assert outcomes == {(202, str({"status": "check_your_email"}))}


def test_sign_up_is_limited_per_address(app, session):
    open_pilot(session, max_accounts=None)
    client = visitor(app, "203.0.113.9")
    codes = [
        client.post("/api/signup", form(email=f"u{i}@example.test")).status_code for i in range(7)
    ]
    assert codes[:5] == [202] * 5 and codes[5:] == [429, 429]


def test_a_second_sign_up_for_one_address_within_a_minute_sends_no_second_mail(app, session):
    """A third party's inbox cannot be flooded through the form. The answer stays the same 202."""
    open_pilot(session, max_accounts=None)
    for i in range(4):
        response = visitor(app, f"198.51.100.{i + 1}").post("/api/signup", form())
        assert (response.status_code, response.json()) == (202, {"status": "check_your_email"})
    assert len(queued(session)) == 1


def test_a_failed_attempt_does_not_use_up_the_minute(anon, session):
    open_pilot(session)
    error_of(sign_up(anon, password="short"), 422, "password_too_weak")
    assert sign_up(anon).status_code == 202
    assert len(queued(session)) == 1


def test_a_busy_hasher_is_a_429_and_creates_nothing(anon, session, monkeypatch):
    open_pilot(session)

    def busy(_):
        raise passwords.HashBusy

    monkeypatch.setattr(passwords, "hash_password", busy)
    error_of(sign_up(anon), 429, "server_busy")
    assert user_of(session) is None


def test_sign_up_needs_the_csrf_token(anon, session):
    open_pilot(session)
    error_of(anon.post("/api/signup", form(), csrf=False), 403, "csrf_rejected")


# ---- verification ----


def make_unverified(anon, session):
    open_pilot(session)
    sign_up(anon)
    return token_of(queued(session)[0])


def test_the_token_verifies_the_account_and_then_the_user_can_log_in(anon, session):
    token = make_unverified(anon, session)
    response = verify(anon, token)
    assert (response.status_code, response.json()) == (200, {"status": "verified"})
    assert user_of(session).email_verified_at is not None
    assert anon.log_in(GOOD, NEW_EMAIL).status_code == 200


def test_verifying_does_not_log_anyone_in(anon, session):
    token = make_unverified(anon, session)
    verify(anon, token)
    assert anon.get("/api/session").json()["authenticated"] is False


def test_a_token_works_once(anon, session):
    token = make_unverified(anon, session)
    assert verify(anon, token).status_code == 200
    error_of(verify(anon, token), 400, "token_invalid_or_expired")


def test_an_expired_token_is_refused(anon, session):
    token = make_unverified(anon, session)
    row = session.scalars(select(AuthToken)).one()
    row.expires_at = datetime.now(UTC) - timedelta(seconds=1)
    session.commit()
    error_of(verify(anon, token), 400, "token_invalid_or_expired")
    assert user_of(session).email_verified_at is None


@pytest.mark.parametrize("token", ["", "nonsense", "A" * 43, "x" * 500])
def test_a_wrong_token_is_refused(anon, session, token):
    make_unverified(anon, session)
    error_of(verify(anon, token), 400, "token_invalid_or_expired")


def test_a_reset_token_does_not_verify_an_email(anon, session):
    make_unverified(anon, session)
    user = user_of(session)
    raw = tokens.issue(
        session,
        purpose="reset_password",
        email=user.email,
        user_id=user.id,
        ttl=timedelta(minutes=30),
        now=datetime.now(UTC),
    )
    session.commit()
    error_of(verify(anon, raw), 400, "token_invalid_or_expired")


def test_a_deleted_user_cannot_be_verified(anon, session):
    token = make_unverified(anon, session)
    user = user_of(session)
    user.status = "deleted"
    session.commit()
    error_of(verify(anon, token), 400, "token_invalid_or_expired")


def test_verify_needs_the_csrf_token_and_a_token_field(anon, session):
    error_of(
        anon.post("/api/verify-email", {"token": "x", "password": GOOD}, csrf=False),
        403,
        "csrf_rejected",
    )
    error_of(anon.post("/api/verify-email", {"token": "x"}), 422, "validation_error")
    error_of(anon.post("/api/verify-email", {"password": GOOD}), 422, "validation_error")


# ---- resend ----


def test_resend_mails_a_new_link_that_confirms_the_same_password_as_the_last_one(anon, session):
    old = make_unverified(anon, session)
    response = anon.post("/api/signup/resend", {"email": NEW_EMAIL})
    assert (response.status_code, response.json()) == (202, {"status": "check_your_email"})
    rows = queued(session, "verify_email")
    assert len(rows) == 2
    fresh = token_of(rows[1])
    assert fresh != old
    error_of(verify(anon, fresh, ATTACKER), 400, "password_mismatch")
    assert verify(anon, fresh).status_code == 200
    error_of(verify(anon, old), 400, "token_invalid_or_expired")  # a confirmed account used them up


def test_resend_never_takes_a_password_and_sends_nothing_without_one_to_copy(anon, session):
    make_user(session, NEW_EMAIL, email_verified_at=None)  # no sign-up behind it, so no link
    assert anon.post("/api/signup/resend", {"email": NEW_EMAIL}).status_code == 202
    assert queued(session) == []


@pytest.mark.parametrize("email", ["nobody@example.test", "not-an-email", ""])
def test_resend_for_an_unknown_address_is_the_same_202_with_no_mail(anon, session, email):
    open_pilot(session)
    response = anon.post("/api/signup/resend", {"email": email})
    assert (response.status_code, response.json()) == (202, {"status": "check_your_email"})
    assert queued(session) == []


def test_resend_for_a_verified_account_sends_nothing(anon, session):
    make_user(session, NEW_EMAIL)
    assert anon.post("/api/signup/resend", {"email": NEW_EMAIL}).status_code == 202
    assert queued(session) == []


def test_resend_is_one_a_minute_per_email_and_still_202(anon, session):
    make_unverified(anon, session)
    for _ in range(3):
        assert anon.post("/api/signup/resend", {"email": NEW_EMAIL}).status_code == 202
    assert len(queued(session, "verify_email")) == 2  # the sign-up's, and one resend


def test_resend_is_five_an_hour_per_address(app, session):
    open_pilot(session, max_accounts=None)
    for i in range(7):
        user = make_user(session, f"u{i}@example.test", email_verified_at=None)
        tokens.issue(
            session,
            purpose="verify_email",
            email=user.email,
            user_id=user.id,
            now=datetime.now(UTC),
            pending_password_hash=user.password_hash,
        )
    session.commit()
    client = visitor(app, "203.0.113.50")
    for i in range(7):
        assert client.post("/api/signup/resend", {"email": f"u{i}@example.test"}).status_code == 202
    assert len(queued(session, "verify_email")) == 5


# ---- the 48-hour sweep ----


def test_unverified_accounts_older_than_48_hours_are_deleted_and_free_their_slot(session):
    now = datetime(2026, 11, 3, 12, 0, tzinfo=UTC)
    old = make_user(session, "old@example.test", email_verified_at=None)
    young = make_user(session, "young@example.test", email_verified_at=None)
    verified = make_user(session, "verified@example.test")
    admin = make_user(session, "admin@example.test", admin=True, email_verified_at=None)
    for user, age in ((old, 49), (young, 47), (verified, 500), (admin, 500)):
        user.created_at = now - timedelta(hours=age)
    session.commit()
    for user in (old, young):
        accounts.create_user_defaults(session, user)
    ids = old.id, young.id
    assert accounts.sweep_unverified(session, now) == 1
    session.expire_all()
    assert session.get(User, ids[0]) is None
    assert session.get(User, ids[1]) is not None
    assert session.scalars(select(Template).where(Template.owner_user_id == ids[0])).all() == []
    assert user_of(session, "verified@example.test") and user_of(session, "admin@example.test")


def production_without_mail(app):
    from tests.test_web import make_settings

    app.state.settings = make_settings(app_env="production")


def test_without_a_mail_provider_password_sign_up_is_off_and_no_token_is_made(app, session):
    open_pilot(session)
    production_without_mail(app)
    web = visitor(app, "198.51.100.1")
    error_of(sign_up(web), 403, "password_signup_disabled")
    assert user_of(session) is None
    assert session.scalars(select(AuthToken)).all() == [] and queued(session) == []


def test_without_a_mail_provider_forgot_and_resend_make_no_token_and_still_answer_202(
    app, session, owner
):
    production_without_mail(app)
    web = visitor(app, "198.51.100.1")
    assert web.post("/api/password/forgot", {"email": OWNER_EMAIL}).status_code == 202
    assert web.post("/api/signup/resend", {"email": OWNER_EMAIL}).status_code == 202
    assert session.scalars(select(AuthToken)).all() == [] and queued(session) == []


def test_the_sweep_counts_from_the_last_sign_up_submission_and_a_resend_does_not_extend_it(
    session,
):
    now = datetime(2026, 11, 3, 12, 0, tzinfo=UTC)
    resubmitted, _ = joined_unconfirmed(session, "again@example.test")
    stale, _ = joined_unconfirmed(session, "stale@example.test")
    resubmitted.created_at = stale.created_at = now - timedelta(hours=60)
    resubmitted.terms_accepted_at = now - timedelta(hours=1)  # signed up again an hour ago
    stale.terms_accepted_at = now - timedelta(hours=49)
    for row in session.scalars(select(AuthToken)).all():
        row.created_at = now - timedelta(minutes=5)  # both were just sent a resend
    session.commit()
    assert accounts.sweep_unverified(session, now) == 1
    assert user_of(session, "again@example.test") and user_of(session, "stale@example.test") is None


def test_sweeping_an_invited_account_returns_the_person_to_the_waitlist(session):
    now = datetime(2026, 11, 3, 12, 0, tzinfo=UTC)
    user, _ = joined_unconfirmed(session, "invitee@example.test")
    user.created_at = user.terms_accepted_at = now - timedelta(hours=60)
    session.commit()
    accounts.sweep_unverified(session, now)
    session.expire_all()
    entry = session.scalars(select(WaitlistEntry)).one()
    assert (entry.status, entry.invite_token_id) == ("waiting", None)


# ---- password reset ----


def forgot(api, email=NEW_EMAIL):
    return api.post("/api/password/forgot", {"email": email})


def test_forgot_answers_the_same_for_a_known_and_an_unknown_email(anon, session, owner):
    known = forgot(anon, OWNER_EMAIL)
    unknown = forgot(anon, "nobody@example.test")
    assert (known.status_code, known.json()) == (unknown.status_code, unknown.json())
    assert known.status_code == 202 and known.json() == {"status": "check_your_email"}


def test_forgot_queues_a_30_minute_link_for_a_verified_account(anon, session, owner):
    forgot(anon, OWNER_EMAIL)
    [row] = queued(session)
    assert (row.template, row.to_email) == ("reset_password", OWNER_EMAIL)
    assert row.payload["link"].startswith("https://notes.beyrictech.com/reset-password?token=")
    [token] = session.scalars(select(AuthToken)).all()
    assert token.purpose == "reset_password" and token.user_id == owner.id
    assert token.token_hash != token_of(row)
    age = token.expires_at - datetime.now(UTC)
    assert timedelta(minutes=29) < age <= timedelta(minutes=30)


def test_forgot_sends_nothing_for_unknown_unverified_suspended_or_malformed(anon, session):
    make_user(session, "unverified@example.test", email_verified_at=None)
    make_user(session, "suspended@example.test", status="suspended")
    for email in ("nobody@example.test", "unverified@example.test", "suspended@example.test", "x"):
        assert forgot(anon, email).status_code == 202
    assert queued(session) == []


def test_forgot_is_limited_per_email_and_per_address_and_still_202(anon, session, owner):
    for _ in range(3):
        assert forgot(anon, OWNER_EMAIL).status_code == 202
    assert len(queued(session, "reset_password")) == 1


def test_a_second_request_kills_the_first_link(anon, session, owner, monkeypatch):
    forgot(anon, OWNER_EMAIL)
    first = token_of(queued(session)[0])
    anon.client.app.state.public_limits.reset()
    forgot(anon, OWNER_EMAIL)
    error_of(
        anon.post("/api/password/reset", {"token": first, "password": GOOD}),
        400,
        "token_invalid_or_expired",
    )


def reset_token(anon, session, email=OWNER_EMAIL):
    forgot(anon, email)
    return token_of(queued(session, "reset_password")[-1])


def test_reset_sets_the_password_and_signs_that_user_out_everywhere(app, session, owner):
    signed_in = visitor(app, "198.51.100.1")
    other = visitor(app, "198.51.100.2")
    make_user(session, "other@example.test")
    signed_in.log_in()
    other.log_in(email="other@example.test")
    token = reset_token(visitor(app, "198.51.100.3"), session)
    new = "-".join(["a", "brand", "new", "pass", "7"])
    response = visitor(app, "198.51.100.3").post(
        "/api/password/reset", {"token": token, "password": new}
    )
    assert (response.status_code, response.json()) == (200, {"status": "password_changed"})
    error_of(signed_in.get("/api/templates"), 401, "not_authenticated")  # epoch bumped
    assert other.get("/api/templates").status_code == 200  # nobody else is touched
    fresh = visitor(app, "198.51.100.4")
    error_of(fresh.log_in(PASSWORD), 401, "invalid_credentials")
    assert fresh.log_in(new).status_code == 200


def test_reset_does_not_log_anyone_in(anon, session, owner):
    token = reset_token(anon, session)
    anon.post("/api/password/reset", {"token": token, "password": GOOD})
    assert anon.get("/api/session").json()["authenticated"] is False


def test_a_reset_token_works_once(anon, session, owner):
    token = reset_token(anon, session)
    assert anon.post("/api/password/reset", {"token": token, "password": GOOD}).status_code == 200
    error_of(
        anon.post("/api/password/reset", {"token": token, "password": GOOD + "x"}),
        400,
        "token_invalid_or_expired",
    )


def test_a_reset_token_expires_after_30_minutes(anon, session, owner):
    token = reset_token(anon, session)
    row = session.scalars(select(AuthToken)).one()
    row.expires_at = datetime.now(UTC) - timedelta(seconds=1)
    session.commit()
    error_of(
        anon.post("/api/password/reset", {"token": token, "password": GOOD}),
        400,
        "token_invalid_or_expired",
    )


def test_a_weak_new_password_is_refused_without_using_up_the_token(anon, session, owner):
    token = reset_token(anon, session)
    error_of(
        anon.post("/api/password/reset", {"token": token, "password": "password"}),
        422,
        "password_too_weak",
    )
    assert anon.post("/api/password/reset", {"token": token, "password": GOOD}).status_code == 200


def test_a_verification_token_cannot_reset_a_password(anon, session):
    token = make_unverified(anon, session)
    error_of(
        anon.post("/api/password/reset", {"token": token, "password": GOOD}),
        400,
        "token_invalid_or_expired",
    )


def test_a_suspended_user_cannot_use_a_link_issued_earlier(anon, session, owner):
    token = reset_token(anon, session)
    owner.status = "suspended"
    session.commit()
    error_of(
        anon.post("/api/password/reset", {"token": token, "password": GOOD}),
        400,
        "token_invalid_or_expired",
    )


def test_a_google_only_account_can_set_a_password_by_reset(anon, session):
    user = make_user(session, NEW_EMAIL, password=None)
    assert user.password_hash is None
    token = reset_token(anon, session, NEW_EMAIL)
    assert anon.post("/api/password/reset", {"token": token, "password": GOOD}).status_code == 200
    assert anon.log_in(GOOD, NEW_EMAIL).status_code == 200


def test_the_public_routes_need_the_csrf_header(anon):
    for url, body in (
        ("/api/signup/resend", {"email": "a@b.test"}),
        ("/api/password/forgot", {"email": "a@b.test"}),
        ("/api/password/reset", {"token": "x", "password": GOOD}),
    ):
        error_of(anon.post(url, body, csrf=False), 403, "csrf_rejected")


def test_mail_templates_used_exist():
    assert {"verify_email", "reset_password", "already_registered"} <= set(mail.TEMPLATES)
