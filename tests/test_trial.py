"""The one-time trial (billing stage S10): a `trial` grant given once to a person at their first
confirmed sign-in, ending at the hours used or 14 days, whichever comes first.

Real Postgres, like the other hours tests. The clock is injected everywhere (`now=`): the trial's
start is the moment the caller says, so the 14th day and the budget window are exact."""

import logging
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select

from app import deletion, entitlements, google_signin, signup, tokens, trial
from app.google_oauth import Identity
from app.models import HourGrant, UsageAllocation, UsageEntry, User
from app.usage import email_hash
from tests.test_web import make_settings, make_user

NOW = datetime(2027, 3, 1, 9, 0, tzinfo=UTC)
HOUR = 3600
TRIAL = 3 * HOUR
DAY = timedelta(days=1)


def on(**over):
    """Settings with the trial on and the hours rule in force."""
    return make_settings(**{"hours_mode": "enforce", "trial_seconds": TRIAL, **over})


def grants_of(session, user=None, kind=None):
    query = select(HourGrant).order_by(HourGrant.id)
    if user is not None:
        query = query.where(HourGrant.user_id == user.id)
    if kind is not None:
        query = query.where(HourGrant.kind == kind)
    return list(session.scalars(query))


def person(session, name, **fields):
    return make_user(session, f"{name}@example.test", **fields)


def spend(session, user, seconds):
    """Count a recording of `seconds` against the user's trial grant, the way `hours.allocate`
    leaves it: a ledger entry and an allocation on the grant."""
    entry = UsageEntry(user_id=user.id, email_hash=email_hash(user.email), seconds=seconds)
    session.add(entry)
    session.flush()
    grant = grants_of(session, user, "trial")[0]
    session.add(UsageAllocation(usage_entry_id=entry.id, grant_id=grant.id, seconds=seconds))
    session.commit()


# ---- the flags: with them off nothing is written ----


def test_trial_seconds_zero_gives_nothing(session):
    ada = person(session, "ada")
    assert trial.give(session, ada, NOW, make_settings(hours_mode="enforce")) is None
    assert grants_of(session) == []


@pytest.mark.parametrize("mode", ["off", "shadow", "enforce"])
def test_the_trial_is_given_in_every_mode(session, mode):
    ada = person(session, "ada")
    settings = make_settings(hours_mode=mode, trial_seconds=TRIAL)
    assert trial.enabled(settings) is True
    assert trial.give(session, ada, NOW, settings) is not None


def test_what_a_trial_holder_recorded_before_enforce_comes_off_their_three_hours(session):
    """Recordings are only allocated to grants under `enforce`. A person who recorded 2 h while the
    flag was off or shadow must hold 1 h when it comes on, not a second 3 h."""
    ada = person(session, "ada")
    trial.give(session, ada, NOW, make_settings(hours_mode="off", trial_seconds=TRIAL))
    entry = UsageEntry(user_id=ada.id, email_hash=email_hash(ada.email), seconds=2 * HOUR)
    entry.counted_at = NOW + timedelta(hours=1)
    session.add(entry)
    session.commit()
    later = NOW + DAY
    assert entitlements.status(session, ada, later).seconds_left == HOUR
    # settling it into allocations changes nothing about what is left
    from app import hours

    assert hours.reconcile_backlog(session, ada, later) == 2 * HOUR
    session.commit()
    assert entitlements.status(session, ada, later).seconds_left == HOUR
    assert hours.reconcile_backlog(session, ada, later) == 0  # idempotent


def test_usage_from_before_the_trial_was_given_never_comes_off_it(session):
    ada = person(session, "ada")
    old = UsageEntry(user_id=ada.id, email_hash=email_hash(ada.email), seconds=5 * HOUR)
    old.counted_at = NOW - 3 * DAY
    session.add(old)
    session.commit()
    trial.give(session, ada, NOW, on())
    session.commit()
    assert entitlements.status(session, ada, NOW + timedelta(hours=1)).seconds_left == TRIAL


# ---- what a trial is ----


def test_the_trial_is_three_hours_for_fourteen_days_from_the_moment_it_is_given(session):
    ada = person(session, "ada")
    grant = trial.give(session, ada, NOW, on())
    session.commit()
    assert (grant.kind, grant.seconds, grant.user_id) == ("trial", TRIAL, ada.id)
    assert grant.starts_at == NOW
    assert grant.expires_at == NOW + timedelta(days=14)
    assert grant.email_hash == email_hash(ada.email)


def test_the_length_follows_the_settings(session):
    ada = person(session, "ada")
    grant = trial.give(session, ada, NOW, on(trial_days=7, trial_seconds=HOUR))
    assert (grant.seconds, grant.expires_at) == (HOUR, NOW + timedelta(days=7))


def test_a_person_with_a_trial_is_in_the_trial_state_with_three_hours_left(session):
    ada = person(session, "ada")
    trial.give(session, ada, NOW, on())
    session.commit()
    s = entitlements.status(session, ada, NOW + timedelta(minutes=1))
    assert (s.state, s.seconds_left) == ("trial", TRIAL)
    assert s.trial_ends_at == NOW + timedelta(days=14)
    assert entitlements.check_join(session, ada, NOW + timedelta(minutes=1)).allowed
    assert entitlements.can(session, ada, entitlements.ASK, NOW + timedelta(minutes=1))


# ---- the end: hours used or fourteen days, whichever comes first ----


def test_the_fourteenth_day_is_the_last_second_and_then_the_trial_is_over(session):
    ada = person(session, "ada")
    trial.give(session, ada, NOW, on())
    session.commit()
    last = NOW + timedelta(days=14) - timedelta(seconds=1)
    assert entitlements.check_join(session, ada, last).allowed
    assert entitlements.status(session, ada, last).state == "trial"
    end = NOW + timedelta(days=14)
    decision = entitlements.check_join(session, ada, end)
    assert (decision.allowed, decision.reason) == (False, entitlements.NO_PLAN)
    after = entitlements.status(session, ada, end)
    assert (after.state, after.seconds_left, after.has_grant) == ("none", 0, False)
    assert entitlements.can(session, ada, entitlements.ASK, end) is False


def test_exactly_three_hours_used_ends_it_one_second_less_does_not(session):
    ada = person(session, "ada")
    trial.give(session, ada, NOW, on())
    session.commit()
    spend(session, ada, TRIAL - 1)
    later = NOW + DAY
    assert entitlements.check_join(session, ada, later).allowed
    assert entitlements.status(session, ada, later).seconds_left == 1
    spend(session, ada, 1)
    decision = entitlements.check_join(session, ada, later)
    assert (decision.allowed, decision.reason) == (False, entitlements.NO_HOURS)


def test_the_trial_state_is_none_once_the_hours_are_gone(session):
    ada = person(session, "ada")
    trial.give(session, ada, NOW, on())
    session.commit()
    spend(session, ada, TRIAL)
    assert entitlements.status(session, ada, NOW + DAY).state == "none"
    assert entitlements.can(session, ada, entitlements.ASK, NOW + DAY) is False


# ---- once per person, and per email fingerprint ----


def test_a_second_attempt_gives_nothing(session):
    ada = person(session, "ada")
    assert trial.give(session, ada, NOW, on()) is not None
    session.commit()
    assert trial.give(session, ada, NOW + timedelta(minutes=5), on()) is None
    assert len(grants_of(session, ada)) == 1


def test_an_ended_trial_is_never_given_again(session):
    ada = person(session, "ada")
    trial.give(session, ada, NOW, on())
    session.commit()
    assert trial.give(session, ada, NOW + timedelta(days=400), on()) is None
    assert trial.sweep(session, NOW + timedelta(days=400), on()) == 0
    assert len(grants_of(session, ada)) == 1


def test_deleting_the_account_and_signing_up_again_gives_nothing(session):
    ada = person(session, "ada")
    trial.give(session, ada, NOW, on())
    session.commit()
    deletion.erase_account_row(session, ada, NOW + DAY)
    session.commit()
    assert grants_of(session, kind="trial")[0].user_id is None  # the grant outlives the account
    again = person(session, "ada")  # the same address, a new row
    assert trial.give(session, again, NOW + 2 * DAY, on()) is None
    assert trial.sweep(session, NOW + 2 * DAY, on()) == 0
    assert len(grants_of(session, kind="trial")) == 1


def test_the_sweep_matches_a_grant_by_fingerprint_alone(session):
    """The sweep's SQL fingerprint is the same as `usage.email_hash`: a grant whose account is gone
    (no user id) still counts, for any address, even one with dots and a plus."""
    ada = make_user(session, "ada.q+tag@example.test")
    session.add(
        HourGrant(
            user_id=None,
            email_hash=email_hash("  Ada.Q+Tag@Example.TEST "),
            kind="trial",
            seconds=TRIAL,
        )
    )
    session.commit()
    assert trial.waiting(session) == []
    assert trial.give(session, ada, NOW, on()) is None


def test_another_person_still_gets_theirs(session):
    ada, ben = person(session, "ada"), person(session, "ben")
    assert trial.give(session, ada, NOW, on()) is not None
    assert trial.give(session, ben, NOW, on()) is not None
    session.commit()
    assert {g.user_id for g in grants_of(session, kind="trial")} == {ada.id, ben.id}


# ---- who is not owed one ----


def test_a_pilot_account_gets_none_even_when_its_pilot_hours_are_all_used(session):
    ada = person(session, "ada")
    session.add(
        HourGrant(user_id=ada.id, email_hash=email_hash(ada.email), kind="pilot", seconds=0)
    )
    session.commit()
    assert trial.give(session, ada, NOW, on()) is None
    assert trial.sweep(session, NOW, on()) == 0
    assert grants_of(session, kind="trial") == []


def test_a_pilot_account_re_registered_under_the_same_address_gets_none(session):
    ada = person(session, "ada")
    session.add(
        HourGrant(user_id=ada.id, email_hash=email_hash(ada.email), kind="pilot", seconds=HOUR)
    )
    session.commit()
    deletion.erase_account_row(session, ada, NOW)
    session.commit()
    again = person(session, "ada")
    assert trial.give(session, again, NOW + DAY, on()) is None


def test_an_admin_gets_none(session):
    boss = person(session, "boss", admin=True)
    assert trial.give(session, boss, NOW, on()) is None


def test_an_unconfirmed_suspended_or_deleted_account_gets_none(session):
    fresh = person(session, "fresh", email_verified_at=None)
    held = person(session, "held", status="suspended")
    gone = person(session, "gone", status="deleted")
    for user in (fresh, held, gone):
        assert trial.give(session, user, NOW, on()) is None
    assert trial.sweep(session, NOW, on()) == 0


def test_a_person_with_only_a_pack_or_a_gift_still_gets_the_trial(session):
    ada = person(session, "ada")
    session.add(
        HourGrant(user_id=ada.id, email_hash=email_hash(ada.email), kind="admin_gift", seconds=HOUR)
    )
    session.commit()
    assert trial.give(session, ada, NOW, on()) is not None


# ---- the daily budget ----


def test_the_budget_stops_the_trial_that_would_pass_it_and_allows_one_that_just_fits(session):
    settings = on(trial_daily_budget_seconds=2 * TRIAL)
    people = [person(session, n) for n in ("ada", "ben", "cy")]
    given = [trial.give(session, p, NOW, settings) for p in people]
    session.commit()
    assert [g is not None for g in given] == [True, True, False]  # 2 x 3 h fits exactly, a 3rd not
    assert trial.given_in_window(session, NOW) == 2 * TRIAL


def test_the_window_frees_exactly_24_hours_after_a_grant(session):
    settings = on(trial_daily_budget_seconds=TRIAL)
    ada, ben = person(session, "ada"), person(session, "ben")
    trial.give(session, ada, NOW, settings)
    session.commit()
    just_before = NOW + DAY - timedelta(seconds=1)
    assert trial.give(session, ben, just_before, settings) is None
    assert trial.give(session, ben, NOW + DAY, settings) is not None  # 24 h old: out of the window


def test_no_budget_means_no_cap(session):
    settings = on(trial_daily_budget_seconds=0)
    for name in ("a", "b", "c", "d", "e"):
        assert trial.give(session, person(session, name), NOW, settings) is not None


def test_a_budget_smaller_than_one_trial_stops_the_process_at_startup():
    with pytest.raises(ValueError, match="TRIAL_DAILY_BUDGET_SECONDS"):
        make_settings(trial_seconds=TRIAL, trial_daily_budget_seconds=TRIAL - 1)


def test_blank_lines_in_the_env_file_mean_not_set():
    settings = make_settings(trial_seconds="", trial_days="", trial_daily_budget_seconds="")
    assert (settings.trial_seconds, settings.trial_days, settings.trial_daily_budget_seconds) == (
        0,
        14,
        0,
    )


# ---- the sweep: late, delayed and missing trials ----


def test_the_sweep_gives_every_waiting_account_a_trial_once_and_closes_the_sign_up_gap(session):
    """Accounts made after migration 0049 hold no hours; the sweep is what gives them theirs."""
    old = [person(session, n, email_verified_at=NOW - 10 * DAY) for n in ("old1", "old2")]
    pilot = person(session, "pilot")
    session.add(
        HourGrant(user_id=pilot.id, email_hash=email_hash(pilot.email), kind="pilot", seconds=HOUR)
    )
    person(session, "boss", admin=True)
    session.commit()
    assert trial.sweep(session, NOW, on()) == 2
    assert trial.sweep(session, NOW, on()) == 0  # idempotent
    got = grants_of(session, kind="trial")
    assert sorted(g.user_id for g in got) == sorted(u.id for u in old)
    assert all(g.starts_at == NOW and g.expires_at == NOW + timedelta(days=14) for g in got)


def test_the_sweep_serves_the_oldest_confirmation_first_and_waits_out_the_budget(session):
    settings = on(trial_daily_budget_seconds=TRIAL)
    late = person(session, "late", email_verified_at=NOW - DAY)
    early = person(session, "early", email_verified_at=NOW - 5 * DAY)
    assert trial.sweep(session, NOW, settings) == 1
    assert [g.user_id for g in grants_of(session, kind="trial")] == [early.id]
    assert trial.sweep(session, NOW + timedelta(hours=23), settings) == 0
    assert trial.sweep(session, NOW + DAY, settings) == 1  # the window freed
    assert {g.user_id for g in grants_of(session, kind="trial")} == {early.id, late.id}
    # a trial that waited starts when it is given: its 14 days were not spent waiting
    assert grants_of(session, late, "trial")[0].expires_at == NOW + DAY + timedelta(days=14)


# ---- the three places an account becomes confirmed ----


def google(email="ada@example.test", sub="g-ada-1"):
    return Identity(sub=sub, email=email, name="Ada Lovelace")


def open_pilot(session, max_accounts=10):
    from tests.test_signup import open_pilot as open_it

    open_it(session, max_accounts=max_accounts)


def test_a_new_google_account_gets_the_trial_at_sign_in(session):
    open_pilot(session)
    outcome = google_signin.complete(
        session, google(), accepted_terms=True, accepted_recording=True, now=NOW, settings=on()
    )
    grant = grants_of(session, outcome.user, "trial")[0]
    assert (grant.seconds, grant.starts_at) == (TRIAL, NOW)
    # the account is owed nothing a second time at its next sign-in
    again = google_signin.complete(
        session,
        google(),
        accepted_terms=True,
        accepted_recording=True,
        now=NOW + DAY,
        settings=on(),
    )
    assert again.user.id == outcome.user.id
    assert len(grants_of(session, outcome.user, "trial")) == 1


def test_google_confirming_an_unconfirmed_password_account_gives_the_trial(session):
    open_pilot(session)
    unconfirmed = person(session, "ada", email_verified_at=None)
    outcome = google_signin.complete(
        session, google(), accepted_terms=True, accepted_recording=True, now=NOW, settings=on()
    )
    assert outcome.user.id == unconfirmed.id
    assert len(grants_of(session, unconfirmed, "trial")) == 1


def test_a_confirmed_password_account_linked_to_google_is_given_no_second_trial(session):
    open_pilot(session)
    ada = person(session, "ada")  # already confirmed: this is not its first confirmed sign-in
    google_signin.complete(
        session, google(), accepted_terms=True, accepted_recording=True, now=NOW, settings=on()
    )
    assert grants_of(session, ada) == []


def test_the_password_link_gives_the_trial_when_the_email_is_confirmed(session, monkeypatch):
    fresh = User(email="ada@example.test", name="Ada")
    session.add(fresh)
    session.flush()
    raw = tokens.issue(
        session,
        purpose="verify_email",
        email=fresh.email,
        user_id=fresh.id,
        now=NOW,
        pending_password_hash="x",
    )
    session.commit()
    open_pilot(session)
    monkeypatch.setattr(signup.passwords, "verify_password", lambda *_: True)  # argon2 is slow
    signup.verify_email(session, raw, "whatever", NOW + timedelta(minutes=2), settings=on())
    session.refresh(fresh)
    assert fresh.email_verified_at is not None
    assert [g.seconds for g in grants_of(session, fresh, "trial")] == [TRIAL]


def test_with_the_trial_off_confirming_writes_no_grant(session):
    open_pilot(session)
    outcome = google_signin.complete(
        session,
        google(),
        accepted_terms=True,
        accepted_recording=True,
        now=NOW,
        settings=make_settings(hours_mode="enforce"),
    )
    assert outcome.user is not None
    assert grants_of(session) == []


def test_a_full_pilot_still_refuses_and_gives_no_trial(session):
    """`max_accounts` stays as the brake: a sign-up past it never becomes an account."""
    open_pilot(session, max_accounts=1)
    person(session, "taken")
    outcome = google_signin.complete(
        session, google(), accepted_terms=True, accepted_recording=True, now=NOW, settings=on()
    )
    assert outcome.full is True
    assert grants_of(session, kind="trial") == []


def test_a_failure_giving_the_trial_never_fails_the_confirmation(session, monkeypatch, caplog):
    open_pilot(session)

    def boom(*_a, **_k):
        raise RuntimeError("secret detail")

    monkeypatch.setattr(trial, "give", boom)
    with caplog.at_level(logging.WARNING, logger="app.trial"):
        outcome = google_signin.complete(
            session, google(), accepted_terms=True, accepted_recording=True, now=NOW, settings=on()
        )
    assert outcome.user is not None and outcome.user.email_verified_at is not None
    assert "RuntimeError" in caplog.text and "secret detail" not in caplog.text
    monkeypatch.undo()
    assert trial.sweep(session, NOW + timedelta(minutes=1), on()) == 1  # the sweep makes it up


# ---- the lock: two workers at once ----


def _in_two_connections(engine, first_work, second_work):
    """Run `first_work(session)` and `second_work(session)` in two sessions on two connections.
    The first gives its trial and keeps its transaction open until the second is either blocked on
    the first's lock (an ungranted lock shows in `pg_locks`) or has finished: so the second really
    meets the first's uncommitted row, however slow the machine is."""
    import threading
    import time

    from sqlalchemy import text as sql
    from sqlalchemy.orm import Session

    first_gave, second_done = threading.Event(), threading.Event()
    results: dict[str, object] = {}

    def first():
        with Session(engine) as s:
            results["first"] = first_work(s)
            first_gave.set()
            deadline = time.time() + 10
            while time.time() < deadline and not second_done.is_set():
                if s.scalar(sql("select count(*) from pg_locks where not granted")):
                    break  # the second is waiting on us
                time.sleep(0.05)
            s.commit()

    def second():
        first_gave.wait(10)
        with Session(engine) as s:
            results["second"] = second_work(s)
            s.commit()
        second_done.set()

    threads = [threading.Thread(target=first), threading.Thread(target=second)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(30)
    return results


def test_two_workers_confirming_one_person_at_once_give_one_trial(engine, session):
    ada = person(session, "ada")
    ada_id = ada.id
    settings = on()

    def give_to_ada(s):
        user = s.get(User, ada_id)
        return trial.give(s, user, NOW, settings) is not None

    results = _in_two_connections(engine, give_to_ada, give_to_ada)
    assert (results["first"], results["second"]) == (True, False)
    session.expire_all()
    assert len(grants_of(session, kind="trial")) == 1


def test_two_workers_cannot_spend_one_budget_twice(engine, session):
    ada, ben = person(session, "ada"), person(session, "ben")
    ids = {"ada": ada.id, "ben": ben.id}
    settings = on(trial_daily_budget_seconds=TRIAL)  # room for exactly one

    def give_to(name):
        return lambda s: trial.give(s, s.get(User, ids[name]), NOW, settings) is not None

    results = _in_two_connections(engine, give_to("ada"), give_to("ben"))
    assert sorted([results["first"], results["second"]]) == [False, True]
    session.expire_all()
    assert len(grants_of(session, kind="trial")) == 1
