"""The subscription status machine, every row (billing stage S8a, `app/billing/states.py`).

Pure: no database, no provider. Three parts: the table (every (status, event) pair, each either
answered or refused), a fake-clock walk through the dunning timeline of spec 3.3 including
"payment succeeds on day 2" and "grace ends", and `effective_status`, which judges a subscription
by the clock alone when the worker has not run.
"""

import itertools
from datetime import UTC, datetime, timedelta

import pytest

from app.billing import states as s

D = datetime(2026, 12, 1, 9, 0, tzinfo=UTC)  # the due moment: the end of a paid period
EVENTS = (
    s.CHARGE_FAILED_0,
    s.CHARGE_FAILED_1,
    s.CHARGE_FAILED_2,
    s.CHARGE_BLOCKED,
    s.PAYMENT_SUCCEEDED,
    s.PAYMENT_STALE,
    s.GRACE_ENDED,
    s.GRACE_ENDED_CANCELED,
    s.PERIOD_ENDED_CANCELED,
    s.CANCEL,
    s.RESUME,
    s.UPGRADE,
    s.DOWNGRADE,
    s.ACCOUNT_DELETED,
    s.REFUNDED,
)


# ---- part one: the table ----------------------------------------------------------------------

# Every row the machine answers, written out a second time on purpose: the table in the module is
# the code under test, this is the specification (spec 3.3 and 3.4).
EXPECTED = {
    (s.ACTIVE, s.CHARGE_FAILED_0): (
        s.PAST_DUE,
        {s.OPEN_GRACE, s.SCHEDULE_NEXT_ATTEMPT, s.NOTIFY_FAILED},
    ),
    (s.ACTIVE, s.PAYMENT_SUCCEEDED): (s.ACTIVE, {s.START_PERIOD}),
    (s.ACTIVE, s.PAYMENT_STALE): (s.ACTIVE, {s.NEEDS_REFUND}),
    (s.ACTIVE, s.PERIOD_ENDED_CANCELED): (s.CANCELED, set()),
    (s.ACTIVE, s.CANCEL): (s.ACTIVE, {s.SET_CANCEL}),
    (s.ACTIVE, s.RESUME): (s.ACTIVE, {s.CLEAR_CANCEL}),
    (s.ACTIVE, s.UPGRADE): (s.ACTIVE, {s.CHARGE_DIFFERENCE}),
    (s.ACTIVE, s.DOWNGRADE): (s.ACTIVE, {s.SCHEDULE_DOWNGRADE}),
    (s.ACTIVE, s.ACCOUNT_DELETED): (s.ENDED, set()),
    (s.ACTIVE, s.REFUNDED): (s.ENDED, set()),
    (s.ACTIVE, s.CHARGE_BLOCKED): (s.PAST_DUE, {s.OPEN_GRACE}),  # our key, not the person's failure
    (s.PAST_DUE, s.CHARGE_BLOCKED): (s.PAST_DUE, set()),
    (s.PAST_DUE, s.CHARGE_FAILED_1): (s.PAST_DUE, {s.SCHEDULE_NEXT_ATTEMPT}),
    (s.PAST_DUE, s.CHARGE_FAILED_2): (s.PAST_DUE, {s.STOP_ATTEMPTS, s.NOTIFY_LAST_DAY}),
    (s.PAST_DUE, s.PAYMENT_SUCCEEDED): (s.ACTIVE, {s.START_PERIOD, s.EXPIRE_LEFTOVER}),
    (s.PAST_DUE, s.PAYMENT_STALE): (s.PAST_DUE, {s.NEEDS_REFUND}),
    (s.PAST_DUE, s.GRACE_ENDED): (s.LAPSED, {s.EXPIRE_LEFTOVER, s.NOTIFY_LAPSED}),
    (s.PAST_DUE, s.GRACE_ENDED_CANCELED): (s.CANCELED, {s.EXPIRE_LEFTOVER}),
    (s.PAST_DUE, s.CANCEL): (s.PAST_DUE, {s.SET_CANCEL, s.STOP_ATTEMPTS}),
    (s.PAST_DUE, s.ACCOUNT_DELETED): (s.ENDED, set()),
    (s.LAPSED, s.PAYMENT_SUCCEEDED): (s.ACTIVE, {s.START_PERIOD}),
    (s.LAPSED, s.PAYMENT_STALE): (s.LAPSED, {s.NEEDS_REFUND}),
    (s.CANCELED, s.PAYMENT_SUCCEEDED): (s.CANCELED, {s.NEEDS_REFUND}),
    (s.CANCELED, s.PAYMENT_STALE): (s.CANCELED, {s.NEEDS_REFUND}),
    (s.ENDED, s.PAYMENT_SUCCEEDED): (s.ENDED, {s.NEEDS_REFUND}),
    (s.ENDED, s.PAYMENT_STALE): (s.ENDED, {s.NEEDS_REFUND}),
}

ALL_PAIRS = list(itertools.product(s.STATUSES, EVENTS))


@pytest.mark.parametrize(("status", "event"), ALL_PAIRS, ids=[f"{a}-{b}" for a, b in ALL_PAIRS])
def test_every_status_and_event_pair_is_answered_or_refused_as_specified(status, event):
    got = s.apply(status, event)
    if (status, event) in EXPECTED:
        new_status, effects = EXPECTED[(status, event)]
        assert got is not None, f"{status} + {event} must be allowed"
        assert (got.status, set(got.effects)) == (new_status, effects)
    else:
        assert got is None, f"{status} + {event} must be refused"


def test_the_table_and_the_specification_list_the_same_rows():
    assert set(s.TABLE) == set(EXPECTED)  # nothing added or dropped without this file knowing


def test_the_table_only_uses_known_statuses_and_events():
    for (status, event), transition in s.TABLE.items():
        assert status in s.STATUSES and event in EVENTS
        assert transition.status in s.STATUSES


def test_only_a_successful_payment_can_start_a_period():
    """The rule behind 'no new hours until a payment succeeds': the one effect that grants a period
    is on `payment_succeeded` rows and nowhere else, so no clock event, cancel or plan change can
    ever grant hours."""
    for (_, event), transition in s.TABLE.items():
        if s.START_PERIOD in transition.effects:
            assert event == s.PAYMENT_SUCCEEDED
    started_from = {st for (st, ev), t in s.TABLE.items() if s.START_PERIOD in t.effects}
    assert started_from == {s.ACTIVE, s.PAST_DUE, s.LAPSED}


def test_a_stale_or_late_payment_never_starts_a_period():
    for (_, event), transition in s.TABLE.items():
        if event == s.PAYMENT_STALE:
            assert s.START_PERIOD not in transition.effects
            assert s.NEEDS_REFUND in transition.effects


def test_in_the_grace_upgrade_downgrade_and_resume_are_refused_and_cancel_stops_the_retries():
    for event in (s.UPGRADE, s.DOWNGRADE, s.RESUME):
        assert s.apply(s.PAST_DUE, event) is None
    cancel = s.apply(s.PAST_DUE, s.CANCEL)
    assert cancel.status == s.PAST_DUE and s.STOP_ATTEMPTS in cancel.effects


def test_history_stays_history_except_a_lapsed_one_a_late_payment_revives():
    for status in s.HISTORY:
        for event in EVENTS:
            got = s.apply(status, event)
            if got is not None and got.status in s.LIVE:
                assert (status, event) == (s.LAPSED, s.PAYMENT_SUCCEEDED)


# ---- part two: the timeline with a fake clock -------------------------------------------------


def snap(status=s.ACTIVE, **over) -> s.Snapshot:
    return s.Snapshot(status=status, current_period_end=D, **over)


def fail(snapshot: s.Snapshot, attempt: int, now: datetime) -> s.Snapshot:
    """The attempt did not pay: what `renewals._fail` does to the row (the effects, applied)."""
    t = s.apply(snapshot.status, s.failed_event(attempt))
    next_charge, grace = snapshot.next_charge_at, snapshot.grace_ends_at
    if s.OPEN_GRACE in t.effects:
        grace = s.grace_end(D)
    if s.SCHEDULE_NEXT_ATTEMPT in t.effects:
        next_charge = s.attempt_time(D, attempt + 1)
    if s.STOP_ATTEMPTS in t.effects:
        next_charge = None
    return s.Snapshot(t.status, D, snapshot.cancel_at_period_end, grace, next_charge, attempt + 1)


def test_nothing_is_due_before_the_period_ends():
    assert s.due_action(snap(), D - timedelta(seconds=1)).kind == s.NONE


def test_all_attempts_fail_walk_through_the_whole_timeline():
    row = snap()
    assert s.due_action(row, D) == s.Due(s.CHARGE, 0)  # the due moment: attempt 0
    row = fail(row, 0, D)
    assert (row.status, row.grace_ends_at, row.next_charge_at) == (
        s.PAST_DUE,
        D + timedelta(hours=72),
        D + timedelta(hours=24),
    )
    assert s.due_action(row, D + timedelta(hours=24) - timedelta(seconds=1)).kind == s.NONE
    assert s.due_action(row, D + timedelta(hours=24)) == s.Due(s.CHARGE, 1)  # day 1
    row = fail(row, 1, D + timedelta(hours=24))
    assert row.next_charge_at == D + timedelta(hours=48)
    assert s.due_action(row, D + timedelta(hours=47)).kind == s.NONE
    assert s.due_action(row, D + timedelta(hours=48)) == s.Due(s.CHARGE, 2)  # day 2
    row = fail(row, 2, D + timedelta(hours=48))
    assert row.next_charge_at is None  # no more attempts, the last-day mail went with this failure
    assert s.apply(s.PAST_DUE, s.CHARGE_FAILED_2).effects >= {s.NOTIFY_LAST_DAY}
    assert s.due_action(row, D + timedelta(hours=71, minutes=59)).kind == s.NONE
    assert s.due_action(row, D + timedelta(hours=72)).kind == s.LAPSE  # grace ends
    lapsed = s.apply(s.PAST_DUE, s.GRACE_ENDED)
    assert lapsed.status == s.LAPSED and s.NOTIFY_LAPSED in lapsed.effects
    # and nothing more is ever due for a lapsed subscription
    assert s.due_action(s.Snapshot(s.LAPSED, D), D + timedelta(days=30)).kind == s.NONE


def test_the_day_one_mail_is_not_sent_only_the_due_day_and_the_last_day_ones():
    day0 = s.apply(s.ACTIVE, s.CHARGE_FAILED_0).effects
    day1 = s.apply(s.PAST_DUE, s.CHARGE_FAILED_1).effects
    day2 = s.apply(s.PAST_DUE, s.CHARGE_FAILED_2).effects
    assert s.NOTIFY_FAILED in day0 and s.NOTIFY_LAST_DAY in day2
    assert not day1 & {s.NOTIFY_FAILED, s.NOTIFY_LAST_DAY, s.NOTIFY_LAPSED}


def test_payment_succeeds_on_day_2_ends_the_dunning_and_starts_a_new_period():
    row = fail(fail(fail(snap(), 0, D), 1, D + timedelta(hours=24)), 2, D + timedelta(hours=48))
    # the person pays through the link on day 2, after the last attempt failed
    paid_at = D + timedelta(hours=50)
    t = s.apply(row.status, s.PAYMENT_SUCCEEDED)
    assert t.status == s.ACTIVE
    assert {s.START_PERIOD, s.EXPIRE_LEFTOVER} <= t.effects  # a NEW period; the old leftover ends
    renewed = s.Snapshot(t.status, paid_at + timedelta(days=30))  # a clean dunning clock
    assert s.due_action(renewed, paid_at + timedelta(days=29)).kind == s.NONE


def test_payment_succeeds_at_each_attempt_in_the_grace_ends_it_the_same_way():
    for attempt in (1, 2):
        row = snap(s.PAST_DUE, grace_ends_at=D + s.GRACE, next_charge_at=D, retry_count=attempt)
        assert s.due_action(row, D + timedelta(hours=24 * attempt)).attempt == attempt
        assert s.apply(row.status, s.PAYMENT_SUCCEEDED).status == s.ACTIVE


def test_a_cancelled_subscription_is_not_charged_and_ends_at_the_period_end():
    row = snap(cancel_at_period_end=True)
    assert s.due_action(row, D - timedelta(seconds=1)).kind == s.NONE
    assert s.due_action(row, D).kind == s.END_CANCELED
    assert s.apply(s.ACTIVE, s.PERIOD_ENDED_CANCELED).status == s.CANCELED


def test_cancelling_in_the_grace_stops_the_attempts_and_the_grace_end_cancels_it():
    row = fail(snap(), 0, D)
    assert s.due_action(row, D + timedelta(hours=24)).kind == s.CHARGE
    stopped = s.Snapshot(s.PAST_DUE, D, True, row.grace_ends_at, None, row.retry_count)
    assert s.due_action(stopped, D + timedelta(hours=24)).kind == s.NONE  # no attempt
    assert s.due_action(stopped, D + timedelta(hours=72)).kind == s.END_CANCELED
    assert s.apply(s.PAST_DUE, s.GRACE_ENDED_CANCELED).status == s.CANCELED


def test_a_worker_that_was_down_still_charges_once_and_never_skips_the_lapse():
    row = snap()  # nobody looked for two days
    assert s.due_action(row, D + timedelta(hours=40)) == s.Due(s.CHARGE, 0)
    assert s.due_action(row, D + timedelta(days=9)) == s.Due(s.CHARGE, 0)
    grace = snap(s.PAST_DUE, grace_ends_at=D + s.GRACE, next_charge_at=D, retry_count=1)
    assert s.due_action(grace, D + timedelta(days=9)).kind == s.LAPSE  # the lapse wins over a retry


def test_the_retry_count_never_points_past_the_last_attempt():
    row = snap(s.PAST_DUE, grace_ends_at=D + s.GRACE, next_charge_at=D, retry_count=9)
    assert s.due_action(row, D + timedelta(hours=1)).attempt == s.LAST_ATTEMPT


# ---- part three: effective status by the clock ------------------------------------------------


@pytest.mark.parametrize(
    ("row", "at", "expected"),
    [
        (snap(), D - timedelta(days=5), s.ACTIVE),
        (snap(), D - timedelta(seconds=1), s.ACTIVE),
        (snap(), D, s.PAST_DUE),  # worker has not run: still judged by the clock
        (snap(), D + timedelta(hours=71, minutes=59), s.PAST_DUE),
        (snap(), D + timedelta(hours=72), s.LAPSED),
        (snap(cancel_at_period_end=True), D - timedelta(seconds=1), s.ACTIVE),
        (snap(cancel_at_period_end=True), D, s.CANCELED),
        (snap(s.PAST_DUE, grace_ends_at=D + s.GRACE), D + timedelta(hours=10), s.PAST_DUE),
        (snap(s.PAST_DUE, grace_ends_at=D + s.GRACE), D + timedelta(hours=72), s.LAPSED),
        (
            snap(s.PAST_DUE, cancel_at_period_end=True, grace_ends_at=D + s.GRACE),
            D + timedelta(hours=72),
            s.CANCELED,
        ),
        (snap(s.PAST_DUE), D + timedelta(hours=72), s.LAPSED),  # no stored grace: D + 72 h
        (snap(s.LAPSED), D + timedelta(days=1), s.LAPSED),
        (snap(s.CANCELED), D + timedelta(days=1), s.CANCELED),
        (snap(s.ENDED), D + timedelta(days=1), s.ENDED),
    ],
)
def test_effective_status_follows_the_clock(row, at, expected):
    assert s.effective_status(row, at) == expected


def test_an_active_subscription_past_its_end_waits_for_a_charge_held_back_until_next_charge_at():
    """A charge refused for our key or a rate limit (not the person's failure) holds the attempt
    back with `next_charge_at` while the status stays `active`: nothing is due until then."""
    held = snap(next_charge_at=D + timedelta(hours=1))
    assert s.due_action(held, D + timedelta(minutes=1)).kind == s.NONE
    assert s.due_action(held, D + timedelta(hours=1)) == s.Due(s.CHARGE, 0)
    assert s.due_action(snap(), D + timedelta(minutes=1)) == s.Due(s.CHARGE, 0)  # nothing held
