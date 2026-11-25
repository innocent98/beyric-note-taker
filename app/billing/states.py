"""The subscription status machine, as pure functions and one table (billing spec 3.3 and 3.4).

No database, no clock of its own, no provider: every function takes the facts and the time and
answers. `subscriptions.py` and `renewals.py` apply the answers to rows;
`tests/test_billing_states.py`
runs EVERY row of the table and a fake-clock walk through the dunning timeline.

**The statuses.**

| status | meaning |
|---|---|
| `active` | inside a paid period (a cancel flag may be set: access until the period ends) |
| `past_due` | the renewal failed: the 3 days of grace, no new hours, change plan refused |
| `lapsed` | the grace ended unpaid (history) |
| `canceled` | the person stopped it and the period (or the grace) ended (history) |
| `ended` | closed for another reason, such as a deleted account (history) |

**The timeline** (counted from the due moment `D`, the end of the paid period; spec 3.3):

| when | what happens |
|---|---|
| `D` | attempt 0: the saved card is charged. Fails: `past_due`, grace until `D + 72 h`, mail |
| `D + 24 h` | attempt 1 (day 1): charged again. Fails: nothing else yet |
| `D + 48 h` | attempt 2 (day 2): charged again. Fails: the last-day mail, no more attempts |
| `D + 72 h` | grace ends unpaid: `lapsed` |
| a payment succeeds, at any moment or later | `active`, a NEW period from the payment date |

A person who cancelled is never charged: at `D` the subscription becomes `canceled`; cancelling
inside the grace stops the attempts and the grace's end makes it `canceled` instead of `lapsed`.

**No new hours until a payment succeeds** is the rule that nothing but the `start_period` effect
grants a period, and the only row that has it is a verified successful payment.
"""

from dataclasses import dataclass
from datetime import datetime, timedelta

ACTIVE, PAST_DUE, LAPSED, CANCELED, ENDED = "active", "past_due", "lapsed", "canceled", "ended"
LIVE = (ACTIVE, PAST_DUE)
HISTORY = (LAPSED, CANCELED, ENDED)
STATUSES = LIVE + HISTORY

GRACE = timedelta(hours=72)
# When each attempt is made, counted from the due moment: attempt 0 at the due moment itself, then
# day 1 and day 2.
ATTEMPT_OFFSETS = (timedelta(0), timedelta(hours=24), timedelta(hours=48))
LAST_ATTEMPT = len(ATTEMPT_OFFSETS) - 1

# ---- events (what can happen to a subscription) ----
CHARGE_FAILED_0 = "charge_failed_0"  # the attempt at the due moment did not pay
CHARGE_FAILED_1 = "charge_failed_1"  # the day-1 retry did not pay
CHARGE_FAILED_2 = "charge_failed_2"  # the day-2 retry did not pay
PAYMENT_SUCCEEDED = "payment_succeeded"  # a verified payment for this subscription's period
PAYMENT_STALE = "payment_stale"  # a verified payment for a period that was already settled
GRACE_ENDED = "grace_ended"  # 72 hours after the due moment, unpaid
GRACE_ENDED_CANCELED = "grace_ended_canceled"  # the same, but the person had cancelled
PERIOD_ENDED_CANCELED = "period_ended_canceled"  # the period ended and the person had cancelled
CANCEL = "cancel"  # the person cancels at period end
RESUME = "resume"  # the person takes the cancel back
UPGRADE = "upgrade"  # the person asks for a bigger plan now
DOWNGRADE = "downgrade"  # the person asks for a smaller plan at the next renewal
ACCOUNT_DELETED = "account_deleted"
REFUNDED = "refunded"  # the first payment of the plan was refunded (stage S9): the plan ends

# ---- effects (what the caller must do for the new status) ----
START_PERIOD = "start_period"  # new period from the payment date, grant its hours, clear dunning
EXPIRE_LEFTOVER = "expire_leftover"  # the old period's leftover hours end now
OPEN_GRACE = "open_grace"  # grace until D + 72 h; the old period's leftover stays usable till then
SCHEDULE_NEXT_ATTEMPT = "schedule_next_attempt"  # next_charge_at = the next attempt's moment
STOP_ATTEMPTS = "stop_attempts"  # no more charges in this cycle
NOTIFY_FAILED = "notify_failed"  # day-0 mail: the payment failed
NOTIFY_LAST_DAY = "notify_last_day"  # day-2 mail: the last day
NOTIFY_LAPSED = "notify_lapsed"  # the grace ended unpaid
SET_CANCEL = "set_cancel"
CLEAR_CANCEL = "clear_cancel"
CHARGE_DIFFERENCE = "charge_difference"  # an upgrade: a checkout for the prorated difference
SCHEDULE_DOWNGRADE = "schedule_downgrade"  # pending_plan_code; applied at the next renewal
NEEDS_REFUND = "needs_refund"  # money arrived that no period can use: tell a human

# What `due_action` can answer.
NONE, CHARGE, LAPSE, END_CANCELED = "none", "charge", "lapse", "end_canceled"


@dataclass(frozen=True)
class Transition:
    status: str
    effects: frozenset[str]


def _t(status: str, *effects: str) -> Transition:
    return Transition(status, frozenset(effects))


# (status, event) -> what happens. A pair that is not here is REFUSED: `apply` returns None.
TABLE: dict[tuple[str, str], Transition] = {
    # --- active
    (ACTIVE, CHARGE_FAILED_0): _t(PAST_DUE, OPEN_GRACE, SCHEDULE_NEXT_ATTEMPT, NOTIFY_FAILED),
    (ACTIVE, PAYMENT_SUCCEEDED): _t(ACTIVE, START_PERIOD),
    (ACTIVE, PAYMENT_STALE): _t(ACTIVE, NEEDS_REFUND),
    (ACTIVE, PERIOD_ENDED_CANCELED): _t(CANCELED),
    (ACTIVE, CANCEL): _t(ACTIVE, SET_CANCEL),
    (ACTIVE, RESUME): _t(ACTIVE, CLEAR_CANCEL),
    (ACTIVE, UPGRADE): _t(ACTIVE, CHARGE_DIFFERENCE),
    (ACTIVE, DOWNGRADE): _t(ACTIVE, SCHEDULE_DOWNGRADE),
    (ACTIVE, ACCOUNT_DELETED): _t(ENDED),
    (ACTIVE, REFUNDED): _t(ENDED),
    # --- past_due: the 3 days of grace. Change plan and resume are refused (not in the table).
    (PAST_DUE, CHARGE_FAILED_1): _t(PAST_DUE, SCHEDULE_NEXT_ATTEMPT),
    (PAST_DUE, CHARGE_FAILED_2): _t(PAST_DUE, STOP_ATTEMPTS, NOTIFY_LAST_DAY),
    (PAST_DUE, PAYMENT_SUCCEEDED): _t(ACTIVE, START_PERIOD, EXPIRE_LEFTOVER),
    (PAST_DUE, PAYMENT_STALE): _t(PAST_DUE, NEEDS_REFUND),
    (PAST_DUE, GRACE_ENDED): _t(LAPSED, EXPIRE_LEFTOVER, NOTIFY_LAPSED),
    (PAST_DUE, GRACE_ENDED_CANCELED): _t(CANCELED, EXPIRE_LEFTOVER),
    (PAST_DUE, CANCEL): _t(PAST_DUE, SET_CANCEL, STOP_ATTEMPTS),
    (PAST_DUE, ACCOUNT_DELETED): _t(ENDED),
    # --- lapsed: a late payment brings the subscription back, from the payment date
    (LAPSED, PAYMENT_SUCCEEDED): _t(ACTIVE, START_PERIOD),
    (LAPSED, PAYMENT_STALE): _t(LAPSED, NEEDS_REFUND),
    # --- history: money that arrives has nothing to pay for
    (CANCELED, PAYMENT_SUCCEEDED): _t(CANCELED, NEEDS_REFUND),
    (CANCELED, PAYMENT_STALE): _t(CANCELED, NEEDS_REFUND),
    (ENDED, PAYMENT_SUCCEEDED): _t(ENDED, NEEDS_REFUND),
    (ENDED, PAYMENT_STALE): _t(ENDED, NEEDS_REFUND),
}


def apply(status: str, event: str) -> Transition | None:
    """What `event` does to a subscription in `status`, or None when the rules refuse it."""
    return TABLE.get((status, event))


@dataclass(frozen=True)
class Snapshot:
    """The facts `due_action` needs, copied from a subscription row."""

    status: str
    current_period_end: datetime
    cancel_at_period_end: bool = False
    grace_ends_at: datetime | None = None
    next_charge_at: datetime | None = None
    retry_count: int = 0  # attempts made so far in this cycle


@dataclass(frozen=True)
class Due:
    kind: str  # NONE, CHARGE, LAPSE or END_CANCELED
    attempt: int = 0  # for CHARGE: which attempt, 0 to LAST_ATTEMPT


def grace_end(due: datetime) -> datetime:
    return due + GRACE


def attempt_time(due: datetime, attempt: int) -> datetime:
    return due + ATTEMPT_OFFSETS[attempt]


def due_action(s: Snapshot, now: datetime) -> Due:
    """What time alone asks of this subscription at `now`: nothing, an attempt to charge, the end
    of the grace (lapse), or the end of a period the person cancelled."""
    if s.status == ACTIVE:
        if now < s.current_period_end:
            return Due(NONE)
        if s.cancel_at_period_end:
            return Due(END_CANCELED)
        if s.next_charge_at is not None and now < s.next_charge_at:
            return Due(NONE)  # held back: a charge refused for a reason that is not the person's
        return Due(CHARGE, 0)
    if s.status == PAST_DUE:
        ends = s.grace_ends_at or grace_end(s.current_period_end)
        if now >= ends:
            return Due(END_CANCELED if s.cancel_at_period_end else LAPSE)
        if s.cancel_at_period_end or s.next_charge_at is None or now < s.next_charge_at:
            return Due(NONE)
        return Due(CHARGE, min(s.retry_count, LAST_ATTEMPT))
    return Due(NONE)


def effective_status(s: Snapshot, now: datetime) -> str:
    """What the person is at `now` when only time is considered: a subscription whose scheduler
    pass has not run yet (the scheduler was down) is still treated as what it is. `active` past its
    period end is `past_due` (or `lapsed` once 72 hours have gone), never quietly `active`."""
    if s.status == ACTIVE:
        if now < s.current_period_end:
            return ACTIVE
        if s.cancel_at_period_end:
            return CANCELED
        return LAPSED if now >= grace_end(s.current_period_end) else PAST_DUE
    if s.status == PAST_DUE:
        ends = s.grace_ends_at or grace_end(s.current_period_end)
        if now >= ends:
            return CANCELED if s.cancel_at_period_end else LAPSED
        return PAST_DUE
    return s.status


def failed_event(attempt: int) -> str:
    """The event for attempt number `attempt` not paying."""
    return (CHARGE_FAILED_0, CHARGE_FAILED_1, CHARGE_FAILED_2)[attempt]
