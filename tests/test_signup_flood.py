"""The sign-up flood defence: unconfirmed accounts are capped per client address, and when the
whole table is full the busiest address's rows go first, never a quiet one's. Attack cases:
about 20 addresses x 5 sign-ups an hour, an attacker's fresh rows next to a real person's old one,
an attacker re-submitting a victim's address, and parallel requests."""

import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import func, select

from app import accounts, passwords, ratelimit, signup
from app.models import MailOutbox, User
from tests.test_accounts import visitor
from tests.test_api import anon, error_of  # noqa: F401  (fixtures used by name)
from tests.test_signup import joined_unconfirmed, open_pilot, queued, sign_up, user_of
from tests.test_web import (  # noqa: F401
    anon_client,
    app,
    fake_openai,
    fake_vexa,
    make_user,
    owner,
)


@pytest.fixture(autouse=True)
def cheap_hashes(monkeypatch):
    """Hundreds of sign-ups: the password hash is slow on purpose and is not under test here."""
    monkeypatch.setattr(passwords, "hash_password", lambda password: "x" * 20)


def pending(session) -> list[User]:
    session.expire_all()
    return list(
        session.scalars(
            select(User).where(User.email_verified_at.is_(None), User.is_admin.is_(False))
        )
    )


def origin_of(app, address):
    return ratelimit.origin_key(ratelimit.store_secret(app.state.settings), address)


def aged(session, user, hours, origin=None):
    """A pending account from `origin`, last submitted `hours` ago, with its default rows."""
    user.terms_accepted_at = user.created_at = datetime.now(UTC) - timedelta(hours=hours)
    user.signup_origin = origin
    accounts.add_user_defaults(session, user)
    session.commit()
    return user


# ---- per address: a concurrent cap ----


def test_one_address_can_hold_only_a_few_unconfirmed_accounts_at_once(app, session):
    open_pilot(session, max_accounts=None)
    api = visitor(app, "192.0.2.10")
    cap = signup.MAX_UNVERIFIED_PER_ORIGIN
    for n in range(cap):
        assert sign_up(api, email=f"a{n}@example.test").status_code == 202
    error_of(sign_up(api, email="one-too-many@example.test"), 429, "too_many_requests")
    assert user_of(session, "one-too-many@example.test") is None
    assert len(pending(session)) == cap


def test_the_per_address_cap_does_not_say_whether_an_email_has_an_account(app, session):
    """At its cap the address gets one answer for a new email and for one it already holds."""
    open_pilot(session, max_accounts=None)
    api = visitor(app, "192.0.2.10")
    for n in range(signup.MAX_UNVERIFIED_PER_ORIGIN):
        sign_up(api, email=f"a{n}@example.test")
    new = error_of(sign_up(api, email="new@example.test"), 429, "too_many_requests")
    held = error_of(sign_up(api, email="a0@example.test"), 429, "too_many_requests")
    assert new == held


def test_other_addresses_are_not_held_back_by_one_address_at_its_cap(app, session):
    open_pilot(session, max_accounts=None)
    noisy = visitor(app, "192.0.2.10")
    for n in range(signup.MAX_UNVERIFIED_PER_ORIGIN + 1):
        sign_up(noisy, email=f"a{n}@example.test")
    assert sign_up(visitor(app, "192.0.2.11"), email="real@example.test").status_code == 202
    assert user_of(session, "real@example.test") is not None


def test_confirming_frees_a_place_under_the_per_address_cap(app, session):
    open_pilot(session, max_accounts=None)
    api = visitor(app, "192.0.2.10")
    for n in range(signup.MAX_UNVERIFIED_PER_ORIGIN):
        sign_up(api, email=f"a{n}@example.test")
    user_of(session, "a0@example.test").email_verified_at = datetime.now(UTC)
    session.commit()
    assert sign_up(api, email="b@example.test").status_code == 202


def test_the_origin_is_a_hash_of_the_address_and_never_the_address(app, session):
    open_pilot(session, max_accounts=None)
    sign_up(visitor(app, "192.0.2.10"), email="a@example.test")
    stored = user_of(session, "a@example.test").signup_origin
    assert stored == origin_of(app, "192.0.2.10")
    assert "192.0.2" not in stored and len(stored) == 32


def test_a_later_submission_from_another_address_does_not_move_a_row_to_it(app, session):
    """Otherwise an attacker could re-submit a victim's pending address from the address they are
    flooding with, to make the victim's row look like part of the flood."""
    open_pilot(session, max_accounts=None)
    sign_up(visitor(app, "192.0.2.10"), email="victim@example.test")
    first = user_of(session, "victim@example.test").signup_origin
    app.state.public_limits.reset()
    sign_up(
        visitor(app, "198.51.100.66"),
        email="victim@example.test",
        password="-".join(["x", "y", "9", "zzz"]),
    )
    assert user_of(session, "victim@example.test").signup_origin == first


# ---- about 20 addresses x 5 an hour ----


def test_twenty_addresses_five_sign_ups_each_cannot_fill_the_table_or_the_mail(app, session):
    open_pilot(session, max_accounts=None)
    codes = []
    for a in range(20):
        api = visitor(app, f"203.0.113.{a + 1}")
        for n in range(5):
            codes.append(sign_up(api, email=f"bot{a}-{n}@example.test").status_code)
    cap = signup.MAX_UNVERIFIED_PER_ORIGIN
    assert codes.count(202) == 20 * cap and codes.count(429) == 20 * (5 - cap)
    assert len(pending(session)) == 20 * cap < signup.MAX_UNVERIFIED
    assert len(queued(session, "verify_email")) == 20 * cap  # no mail for a refused sign-up
    # a real person at a 21st address is not turned away
    real = visitor(app, "198.51.100.20")
    assert sign_up(real, email="real@example.test").status_code == 202
    assert user_of(session, "real@example.test") is not None


# ---- the whole table is full: the busiest address goes first ----


def test_at_the_cap_the_attackers_rows_go_before_a_real_persons_older_one(
    app, session, monkeypatch
):
    open_pilot(session, max_accounts=None)
    monkeypatch.setattr(signup, "MAX_UNVERIFIED", 8)
    a, b, c = (origin_of(app, f"203.0.113.{n}") for n in (1, 2, 3))
    aged(session, make_user(session, "real-old@example.test", email_verified_at=None), 3, "r1")
    aged(session, make_user(session, "real-older@example.test", email_verified_at=None), 5, "r2")
    for i in range(3):
        aged(
            session,
            make_user(session, f"a{i}@example.test", email_verified_at=None),
            0.1 * (3 - i),
            a,
        )
    for i in range(2):
        aged(session, make_user(session, f"b{i}@example.test", email_verified_at=None), 0.2, b)
    aged(session, make_user(session, "c0@example.test", email_verified_at=None), 0.1, c)
    assert len(pending(session)) == 8
    api = visitor(app, "198.51.100.30")
    assert sign_up(api, email="newcomer@example.test").status_code == 202
    assert user_of(session, "newcomer@example.test") is not None
    assert user_of(session, "real-old@example.test") and user_of(session, "real-older@example.test")
    assert user_of(session, "a0@example.test") is None  # the busiest address's oldest row went
    assert user_of(session, "a1@example.test") and user_of(session, "b0@example.test")
    assert len(pending(session)) == 8


def test_an_evicted_row_takes_its_own_data_with_it_and_nothing_else(app, session, monkeypatch):
    open_pilot(session, max_accounts=None)
    monkeypatch.setattr(signup, "MAX_UNVERIFIED", 3)
    a = origin_of(app, "203.0.113.1")
    for i in range(3):
        aged(
            session,
            make_user(session, f"a{i}@example.test", email_verified_at=None),
            0.1 * (3 - i),
            a,
        )
    member = make_user(session, "member@example.test")  # confirmed: never counted, never evicted
    assert sign_up(visitor(app, "198.51.100.30"), email="newcomer@example.test").status_code == 202
    assert user_of(session, "a0@example.test") is None
    assert user_of(session, "member@example.test").id == member.id


def test_when_every_address_has_one_row_a_fresh_row_is_never_evicted(app, session, monkeypatch):
    """Nothing says which of them is an attacker's, so none that is under an hour old is touched
    and the newcomer is turned away."""
    open_pilot(session, max_accounts=None)
    monkeypatch.setattr(signup, "MAX_UNVERIFIED", 3)
    for i in range(3):
        aged(
            session, make_user(session, f"s{i}@example.test", email_verified_at=None), 0.2, f"o{i}"
        )
    error_of(
        sign_up(visitor(app, "198.51.100.30"), email="newcomer@example.test"),
        429,
        "too_many_requests",
    )
    assert len(pending(session)) == 3 and user_of(session, "newcomer@example.test") is None


def test_when_every_address_has_one_row_the_oldest_abandoned_one_makes_room(
    app, session, monkeypatch
):
    open_pilot(session, max_accounts=None)
    monkeypatch.setattr(signup, "MAX_UNVERIFIED", 3)
    aged(session, make_user(session, "old@example.test", email_verified_at=None), 5, "o1")
    aged(session, make_user(session, "older@example.test", email_verified_at=None), 9, "o2")
    aged(session, make_user(session, "fresh@example.test", email_verified_at=None), 0.1, "o3")
    assert sign_up(visitor(app, "198.51.100.30"), email="newcomer@example.test").status_code == 202
    assert user_of(session, "older@example.test") is None
    assert user_of(session, "old@example.test") and user_of(session, "fresh@example.test")


def test_rows_with_no_recorded_origin_are_each_their_own_address(app, session, monkeypatch):
    """Rows from before the origin was recorded are never lumped together as one flooder."""
    open_pilot(session, max_accounts=None)
    monkeypatch.setattr(signup, "MAX_UNVERIFIED", 3)
    for i in range(3):
        aged(session, make_user(session, f"n{i}@example.test", email_verified_at=None), 0.2, None)
    error_of(
        sign_up(visitor(app, "198.51.100.30"), email="newcomer@example.test"),
        429,
        "too_many_requests",
    )
    assert len(pending(session)) == 3


def test_an_invited_persons_unconfirmed_row_is_never_evicted_and_never_refused(
    app, session, monkeypatch
):
    open_pilot(session, max_accounts=None)
    monkeypatch.setattr(signup, "MAX_UNVERIFIED", 2)
    invited, _ = joined_unconfirmed(session, "invited@example.test", hours_old=9)
    invited.signup_origin = "i1"
    aged(session, make_user(session, "x@example.test", email_verified_at=None), 9, "x1")
    session.commit()
    assert sign_up(visitor(app, "198.51.100.30"), email="newcomer@example.test").status_code == 202
    assert user_of(session, "invited@example.test") is not None  # its place was promised
    assert user_of(session, "x@example.test") is None


# ---- parallel requests ----


def test_parallel_sign_ups_from_one_address_make_at_most_the_per_address_cap(app, session):
    """The hourly counter lives in the database, so ten requests at once see one budget: five get
    through it, and the concurrent cap lets only three of those create a row."""
    open_pilot(session, max_accounts=None)
    codes: list[int] = []
    lock = threading.Lock()

    def attempt(n):
        status = sign_up(visitor(app, "192.0.2.77"), email=f"p{n}@example.test").status_code
        with lock:
            codes.append(status)

    with ThreadPoolExecutor(10) as pool:
        list(pool.map(attempt, range(10)))
    assert codes.count(202) == signup.MAX_UNVERIFIED_PER_ORIGIN
    assert codes.count(429) == 10 - signup.MAX_UNVERIFIED_PER_ORIGIN
    assert len(pending(session)) == signup.MAX_UNVERIFIED_PER_ORIGIN


def test_parallel_sign_ups_from_many_addresses_never_pass_the_global_cap(app, session, monkeypatch):
    open_pilot(session, max_accounts=None)
    monkeypatch.setattr(signup, "MAX_UNVERIFIED", 6)

    def attempt(n):
        return sign_up(visitor(app, f"192.0.2.{n + 100}"), email=f"p{n}@example.test").status_code

    with ThreadPoolExecutor(12) as pool:
        codes = list(pool.map(attempt, range(12)))
    assert codes.count(202) == 6 and codes.count(429) == 6
    assert len(pending(session)) == 6


def test_the_mail_for_a_refused_sign_up_is_never_queued(app, session):
    open_pilot(session, max_accounts=None)
    api = visitor(app, "192.0.2.10")
    for n in range(signup.MAX_UNVERIFIED_PER_ORIGIN + 3):
        sign_up(api, email=f"a{n}@example.test")
    assert (
        session.scalar(select(func.count()).select_from(MailOutbox))
        == signup.MAX_UNVERIFIED_PER_ORIGIN
    )
