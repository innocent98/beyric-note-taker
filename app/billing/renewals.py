"""Our own billing clock: renewals, retries, grace and lapse, the sweeper and the pay link
(billing spec 3.3 and 5.3; stage S8a).

**One pass** (`run`, a job of the leader-elected scheduler process, on its sync loop every 60
seconds while `BILLING_ENABLED` is on) does two things, each on its own so one failing never
stops the other:

1. `run_due`: for every live subscription with something due, ask `states.due_action` what time
   asks of it and do exactly that, one subscription per transaction, under its row lock
   (`FOR UPDATE SKIP LOCKED`, so two workers never take the same one).
2. `sweep`: settle what the providers' webhooks did not (below).

**An attempt** (`_attempt`) is the saved-card charge of the due moment, then day 1 and day 2. The
order matters, and money is only ever moved by a row we made first:

1. An earlier renewal payment for this period that is still `pending` is settled first, by asking
   the provider about OUR reference (`provider.lookup_by_reference`). A charge whose answer was
   lost is therefore resolved before another is made, never repeated blind. One that is open with
   the payer's own link (a 3-D Secure charge) is NOT charged again: the slot is spent and the
   person is sent to that link.
2. The attempt payment row (kind `renewal`, `attempt` n, `cycle_end` = this period's end) is made
   and committed: the unique index on (subscription, cycle_end, attempt) lets exactly one worker
   hold the slot.
3. `charge_saved_method` is called with no lock held. **Its answer grants nothing**: a
   `successful` answer is re-read from the provider (`fetch_transaction`) and judged by the same
   rules a webhook gets (`settle.settle_transaction`: our reference, our currency, at least our
   amount). Only then do the hours come, through the grant handler.
4. A failure moves the status machine (`states.TABLE`) under the lock: the first one opens the 72
   hours of grace (the period's leftover hours stay usable until its end, no new hours), each
   failure schedules the next attempt, the last one sends the last-day notice.

**An attempt whose outcome cannot be found never freezes the plan, and never doubles a charge**
(review S1 and its delta review). Four cases:

- the provider says the call made no charge for a reason about the card or the request
  (`NothingMoved`: a 4xx, an input check) and holds nothing under our reference: the attempt FAILED
  at once (`_charge_refused`: payment `failed`, the clock moves);
- the call was refused for a reason that is ours or the provider's, not the person's
  (`ChargeBlocked`: our key missing or refused, a rate limit): NOT their failure. The payment is
  closed (`failed`, with no slot), the status machine does not move (no grace, no mail, no retry
  spent), the admins are told, and the charge is held back `BLOCKED_RETRY` (`next_charge_at`)
  until a hold would run past the end of the grace: then it counts like any other failed attempt
  and the ordinary lapse follows (a broken key never holds a plan `active` for ever);
- the call got no answer and the lookup by our reference cannot find it, or the provider keeps
  saying `pending` with no page for the payer: the attempt waits `UNRESOLVED_AFTER`, then COUNTS as
  a failed attempt for the clock only (`_note_unresolved` tells the admins; grace, mails, the retry
  slots and the lapse run);
- **its payment stays `pending`**: the charge may exist, and a second charge for the same period
  could debit the person twice. So every later pass asks the provider about it FIRST
  (`_settle_open_payments`): found paid, it is settled and nothing more is charged; still unknown,
  the slot is counted but NOT charged, and the pay link makes no second page (502, retry), until
  the provider can say. A late webhook settles it too. The plan then runs out its grace and lapses
  at the normal moment; the admins were told at six hours to look the reference up.

A subscription with no usable saved card, or whose billing country now routes elsewhere, makes no
charge: its attempt is a recorded failure, and the person pays through `pay_link` (the provider's
own page, on the route they are on now). The grace and the lapse run on the clock either way.

**The sweeper** covers the providers' retries running out: webhook events stored but never decided
(`billing_events.processed_at` null) are decided again, and a `pending` payment older than two
minutes is looked up by its reference and settled if the provider says it succeeded. It is also
what lets a test-mode purchase finish when the provider cannot reach this server.

**The pay link** (`pay_link`): for a subscription in its grace, the provider's page to pay what is
due. If a charge is already open for this period with its own link (3-D Secure), that same link;
otherwise a fresh hosted checkout for the renewal amount.
"""

import json
import logging
from datetime import datetime, timedelta

from sqlalchemy import and_, func, or_, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app import alerts
from app.billing import cutover, invoices, plans, refunds, routing, settle, states, subscriptions
from app.billing import profile as profiles
from app.billing.payments import new_payment
from app.billing.provider import lookup_by_reference, provider_named
from app.billing.types import ChargeBlocked, NothingMoved, OutcomeUnknown, ProviderError, RetryLater
from app.config import Settings
from app.models import (
    BillingEvent,
    HourGrant,
    Payment,
    Refund,
    Subscription,
    User,
)

logger = logging.getLogger(__name__)

BATCH = 50
SWEEP_AFTER = timedelta(
    minutes=2
)  # a payment or event younger than this is the webhook's to settle
SWEEP_LOOKBACK = timedelta(days=3)  # older unpaid checkouts are left alone
# A plan, pack or upgrade checkout the person put away (`cancel_checkout`: `failed`, with its page)
# is still asked about for this long, so a page paid later by mistake whose webhook was lost is
# settled, not lost.
FAILED_LOOKBACK = timedelta(days=7)
MIN_RETRY_GAP = timedelta(hours=1)  # a late pass never fires two attempts back to back
NO_LINK_GRACE = timedelta(minutes=10)  # a pay-link row still waiting for its page is not abandoned
UNRESOLVED_AFTER = timedelta(hours=6)
BLOCKED_RETRY = timedelta(minutes=30)  # a charge our key or a rate limit refused is tried again
# Our outage must not cost the person their plan: after the LAST block they keep at least this long
# to be charged again, and the grace is never pushed further than the cap past its normal end.
BLOCKED_GRACE = timedelta(hours=24)
BLOCKED_GRACE_CAP = timedelta(days=7)
PAGE_HOLDS_CHARGE = timedelta(hours=2)  # a pay-link page this young holds the saved-card retry


def run(session: Session, now: datetime, settings: Settings) -> None:
    """One pass of the clock and the sweeper. Each part is caught on its own: whatever it throws is
    logged by type (an exception can hold an address) and never reaches the scheduler's loop."""
    for name, job in (
        ("due", lambda: run_due(session, now, settings)),
        ("sweep", lambda: sweep(session, now, settings)),
    ):
        try:
            job()
        except Exception as exc:
            session.rollback()
            logger.error("billing %s pass failed: %s", name, type(exc).__name__)


# ---- the clock ----


def run_due(session: Session, now: datetime, settings: Settings) -> int:
    """Handle every live subscription with something due. Returns how many were looked at."""
    due = or_(
        and_(Subscription.status == states.ACTIVE, Subscription.current_period_end <= now),
        and_(
            Subscription.status == states.PAST_DUE,
            or_(
                Subscription.grace_ends_at <= now,
                Subscription.grace_ends_at.is_(None),
                and_(Subscription.next_charge_at.is_not(None), Subscription.next_charge_at <= now),
            ),
        ),
    )
    # Stage S9: a plan whose first payment has a refund still `pending` or `unknown` is not charged,
    # retried or lapsed until a person settles the refund (`made` ends the plan, `not_made` lets
    # the clock catch up and charge once, as after any pause).
    waiting_refund = (
        select(Refund.id)
        .join(Payment, Payment.id == Refund.payment_id)
        .where(
            Payment.subscription_id == Subscription.id, Refund.status.in_(("pending", "unknown"))
        )
        .exists()
    )
    ids = session.scalars(
        select(Subscription.id)
        .where(due, ~waiting_refund)
        .order_by(Subscription.current_period_end)
        .limit(BATCH)
    ).all()
    for sub_id in ids:
        try:
            process_one(session, sub_id, now, settings)
        except Exception as exc:
            session.rollback()
            logger.error("subscription %s pass failed: %s", sub_id, type(exc).__name__)
    return len(ids)


def process_one(session: Session, sub_id: int, now: datetime, settings: Settings) -> None:
    """What time asks of one subscription now. Takes its row lock (skipping one another worker
    holds) and ends the transaction either way."""
    sub = session.scalars(
        select(Subscription)
        .where(Subscription.id == sub_id)
        .with_for_update(skip_locked=True)
        .execution_options(populate_existing=True)
    ).one_or_none()
    if sub is None:
        session.rollback()
        return
    due = states.due_action(subscriptions.snapshot(sub), now)
    if due.kind == states.NONE:
        session.rollback()
    elif due.kind in (states.LAPSE, states.END_CANCELED):
        _close(session, sub, due.kind, now)
    else:
        _attempt(session, sub, due.attempt, now, settings)


def _notify(
    session: Session, sub: Subscription, kind: str, title: str, body: str, cycle: datetime
) -> None:
    """A bell entry and a plain mail, once per subscription and period. The mail carries no link
    (the alert mail never does): the body sends the person to Billing, which has a pay button."""
    if sub.user_id is None:
        return
    alerts.notify_user(
        session,
        sub.user_id,
        kind=kind,
        severity="warning",
        title=title,
        body=body,
        dedupe_key=f"{kind}:{sub.id}:{int(cycle.timestamp())}",
        email_template=alerts.ALERT_TEMPLATE,
    )


FAILED_BODY = (
    "We could not renew your plan. We will try your card again tomorrow and the day after. "
    "Your hours from this period stay usable for 3 days, but no new hours are added until a "
    "payment goes through. Open Billing in Beyric to pay now. Your notes, transcripts and "
    "exports are not affected."
)
LAST_DAY_BODY = (
    "Today is the last day to pay for your plan. If the payment does not go through, new "
    "recordings, Ask Beyric, translation and rewriting notes stop. Open Billing in Beyric to pay "
    "now. Your notes, transcripts and exports stay as they are."
)
LAPSED_BODY = (
    "Your plan has ended because the payment did not go through. Recordings, Ask Beyric, "
    "translation and rewriting notes have stopped; any pack hours you bought still work for "
    "recordings. Your notes, transcripts and exports stay as they are. You can subscribe again "
    "in Billing."
)
ENDED_BODY = (
    "Your plan has ended, as you asked. Your notes, transcripts and exports stay as they are. "
    "You can subscribe again in Billing."
)


def _close(session: Session, sub: Subscription, kind: str, now: datetime) -> None:
    """The grace ended unpaid (`lapsed`), or a period the person cancelled ended (`canceled`)."""
    if kind == states.LAPSE:
        event = states.GRACE_ENDED
    elif sub.status == states.PAST_DUE:
        event = states.GRACE_ENDED_CANCELED
    else:
        event = states.PERIOD_ENDED_CANCELED
    transition = states.apply(sub.status, event)
    cycle = sub.current_period_end
    sub.status = transition.status
    sub.next_charge_at = None
    sub.updated_at = now
    if states.EXPIRE_LEFTOVER in transition.effects:
        grace_end = sub.grace_ends_at or states.grace_end(cycle)
        subscriptions.end_leftover(session, sub, min(now, grace_end))
    if states.NOTIFY_LAPSED in transition.effects:
        _notify(session, sub, "billing.lapsed", "Your plan has ended", LAPSED_BODY, cycle)
    elif transition.status == states.CANCELED:
        _notify(session, sub, "billing.ended", "Your plan has ended", ENDED_BODY, cycle)
    session.commit()


# ---- an attempt ----


def _attempt(
    session: Session, sub: Subscription, n: int, now: datetime, settings: Settings
) -> None:
    """Attempt number `n` of this period's renewal (see the module note for the order)."""
    cycle = sub.current_period_end
    sub_id = sub.id
    user = session.get(User, sub.user_id) if sub.user_id else None
    outcome = _settle_open_payments(session, sub, now, settings)
    if outcome == "paid":
        return
    if outcome == "wait":
        session.commit()  # nothing changed, except perhaps an admin alert that must be kept
        return
    if outcome in ("open_link", "gave_up"):
        # open_link: a charge is waiting for the payer, its slot counts, no new charge.
        # gave_up: the charge of this slot could not be found for `UNRESOLVED_AFTER`: the slot
        # counts as a failed attempt for the clock, and NOTHING is charged (the charge may exist).
        return _fail(session, sub.id, cycle, n, now)

    price = plans.renewal_price(session, sub, now)
    adapter = provider_named(sub.provider, settings)
    card = subscriptions.saved_card(session, user.id, sub.provider) if user else None
    if user is None or user.status != "active":
        card = None
    route_moved = _route_moved(session, sub, user, settings, now) if user else False
    if price is None or adapter is None or card is None or route_moved:
        _record_failed_attempt(session, sub, user, price, n, cycle, now)
        return _fail(session, sub.id, cycle, n, now)

    try:
        with session.begin_nested():
            payment = new_payment(
                session,
                payer=user,
                provider=sub.provider,
                kind="renewal",
                currency=sub.currency,
                amount_minor=price.amount_minor,
                subscription_id=sub.id,
            )
            payment.plan_price_id = price.id
            payment.cycle_end = cycle
            payment.attempt = n
            payment.created_at = now
            session.flush()
    except IntegrityError:  # the slot has its row already
        session.rollback()
        slot = session.scalars(
            select(Payment).where(
                Payment.subscription_id == sub_id,
                Payment.kind == "renewal",
                Payment.cycle_end == cycle,
                Payment.attempt == n,
            )
        ).first()
        if slot is not None and slot.status == "failed":
            # a pass recorded this attempt as failed and died before the status moved: finish it
            return _fail(session, sub_id, cycle, n, now)
        return  # another worker holds it
    payment_id, reference, amount = payment.id, payment.reference, price.amount_minor
    token, email = card
    sub_id, currency = sub.id, sub.currency
    session.commit()  # the payment row exists before the provider is asked, and no lock is held

    try:
        tx = adapter.charge_saved_method(token, amount, currency, reference, payer_email=email)
    except ChargeBlocked as blocked:
        # Our key or a rate limit refused it: nothing was charged and it says nothing about the
        # person, so it is not their failed attempt (see the module note).
        return _charge_blocked(session, payment_id, sub_id, cycle, now, blocked)
    except NothingMoved as refused:
        # The provider refused the call (or it was never made): no charge exists, unless an
        # earlier call under this reference made one, so it is asked once. Nothing found means
        # the attempt failed: there is nothing for a later pass to wait for.
        try:
            tx = lookup_by_reference(adapter, reference)
        except ProviderError:
            return _charge_refused(session, payment_id, sub_id, cycle, n, now, refused)
    except (OutcomeUnknown, ProviderError):
        # Unknown outcome: the money may have moved (Flutterwave says so with `OutcomeUnknown`;
        # Paystack's adapter says it with a plain `ProviderError`, so both are treated alike). Ask
        # by our reference; if even that fails the payment stays pending, the next pass settles it
        # before anything is charged again, and `_settle_open_payments` gives it up after
        # `UNRESOLVED_AFTER`, so the plan is never frozen on it.
        try:
            tx = lookup_by_reference(adapter, reference)
        except ProviderError:
            logger.warning("renewal payment %s: outcome unknown, left pending", payment_id)
            return

    if tx.status == "successful":
        verdict = _settle_charge(session, adapter, payment_id, tx, now)
        if verdict in ("paid", "unknown"):
            # paid: the grant handler renewed the subscription. Unknown: the provider could not
            # confirm it just now, the money may have moved, so this is NOT a failed attempt: the
            # payment stays pending and the next pass settles it by reference.
            return
    elif tx.status == "failed":
        settle.settle_transaction(session, adapter, payment_id, tx, now)
        session.commit()
    elif tx.status == "pending":
        if not tx.payer_action_url:
            # queued or processing, and nothing for the payer to do: not a failure. Wait; the next
            # pass settles it by reference (an unknown outcome is handled the same way).
            return
        row = session.get(Payment, payment_id)
        row.pay_url = tx.payer_action_url  # THIS charge's own link: never a second checkout
        session.commit()
    _fail(session, sub_id, cycle, n, now)


def _settle_charge(session: Session, adapter, payment_id: int, tx, now: datetime) -> str:
    """The charge answered `successful`. The answer is a claim: re-read the payment from the
    provider and apply the settlement rules. Returns `paid` (the payment is now `succeeded`),
    `refused` (the rules refused what the provider reports: a failed attempt, and the admins are
    told) or `unknown` (the provider could not confirm it just now)."""
    try:
        seen = adapter.fetch_transaction(tx.provider_tx_id)
    except ProviderError:
        return "unknown"
    try:
        refusal = settle.settle_transaction(session, adapter, payment_id, seen, now)
        session.commit()
    except RetryLater:
        session.rollback()
        return "unknown"
    except Exception:
        session.rollback()
        raise
    if refusal:
        subscriptions._tell_admins(
            session,
            f"admin.billing_payment_refused:{payment_id}",
            "A renewal charge was refused by the settlement rules",
            f"Renewal payment {payment_id}: {refusal}. The provider may have taken money; "
            "look at it.",
        )
        session.commit()
        return "refused"
    return "paid" if session.get(Payment, payment_id).status == "succeeded" else "unknown"


def _charge_refused(
    session: Session,
    payment_id: int,
    sub_id: int,
    cycle: datetime,
    n: int,
    now: datetime,
    refused: NothingMoved,
) -> None:
    """The saved-card charge call was refused and the provider holds nothing under our reference:
    the attempt failed. The payment is closed (`failed`: a webhook for it would still settle it),
    the admins are told once (a refusal that repeats for everyone is a key or an adapter problem),
    and the status machine moves, so the person gets the grace, the mail and the pay link."""
    closed = session.execute(
        update(Payment)
        .where(Payment.id == payment_id, Payment.status == "pending")
        .values(status="failed")
    ).rowcount
    session.commit()
    if not closed:  # a webhook decided this payment meanwhile: it is not ours to fail
        return
    logger.warning("renewal payment %s: charge refused (%s)", payment_id, type(refused).__name__)
    subscriptions._tell_admins(
        session,
        f"admin.billing_charge_refused:{payment_id}",
        "A renewal charge was refused by the provider",
        f"Renewal payment {payment_id}: the provider refused the saved-card charge, so nothing was "
        "charged. The attempt counts as failed and the person has the 3 days of grace. If this "
        "repeats for other people, look at the provider keys and the provider's dashboard.",
    )
    session.commit()
    _fail(session, sub_id, cycle, n, now)


def _charge_blocked(
    session: Session,
    payment_id: int,
    sub_id: int,
    cycle: datetime,
    now: datetime,
    blocked: ChargeBlocked,
) -> None:
    """The charge call was refused for a reason that is ours or the provider's (our key, a rate
    limit): not the person's failed attempt. The payment row is kept as history, `failed`, told
    apart from a pay-link page by its `provider_tx_id` (`blocked-<reference>`, like the `refused-`
    ids of Flutterwave) and with no slot (`attempt` null) so the retry can use it.

    The subscription is not failed (no mail, no retry spent) but is protected: an `active` row
    opens its grace (the leftover hours stay usable and Pay now works), and the grace is extended
    so the person always has `BLOCKED_GRACE` after the last block, up to `BLOCKED_GRACE_CAP` past
    its normal end; the charge is held back `BLOCKED_RETRY`. Past the cap the ordinary lapse
    comes, as the admin alert says. The alert is sent once every six hours per provider."""
    closed = session.execute(
        update(Payment)
        .where(Payment.id == payment_id, Payment.status == "pending")
        .values(
            status="failed",
            attempt=None,
            provider_tx_id=func.substr(func.concat("blocked-", Payment.reference), 1, 128),
        )
    ).rowcount
    if closed:
        sub = session.scalars(
            select(Subscription)
            .where(Subscription.id == sub_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        ).one_or_none()
        if sub is not None and sub.status in states.LIVE and sub.current_period_end == cycle:
            _hold_for_block(session, sub, cycle, now)
        provider = session.scalar(select(Payment.provider).where(Payment.id == payment_id))
        logger.warning(
            "renewal payment %s: charge blocked (%s)", payment_id, type(blocked).__name__
        )
        subscriptions._tell_admins(
            session,
            f"admin.billing_charge_blocked:{provider}:{now:%Y-%m-%d}-{now.hour // 6}",
            "Renewal charges are being refused by the provider",
            f"The {provider} provider refused a saved-card renewal charge (payment {payment_id}) "
            "for a reason that is ours or theirs, not the customer's: a missing, refused or "
            "restricted key, or a rate limit. Nothing was charged and the customer was not told "
            "of a failure. The charge is tried again every 30 minutes, and the customer's grace "
            "is extended while this lasts, for up to 7 days past its normal end; after that the "
            "plan lapses. Check the provider keys, their permissions and the provider's dashboard.",
        )
    session.commit()


def _hold_for_block(session: Session, sub: Subscription, cycle: datetime, now: datetime) -> None:
    """Protect a person whose charge OUR side blocked (see `_charge_blocked`): open the grace if
    it is not open, extend it by the blocked time within the cap, and hold the next charge."""
    transition = states.apply(sub.status, states.CHARGE_BLOCKED)
    if transition is None:
        return
    sub.status = transition.status
    sub.updated_at = now
    if states.OPEN_GRACE in transition.effects:
        _open_grace(session, sub, cycle)
    ceiling = states.grace_end(cycle) + BLOCKED_GRACE_CAP
    wanted = min(now + BLOCKED_GRACE, ceiling)
    if sub.grace_ends_at is None or wanted > sub.grace_ends_at:
        _set_grace_end(session, sub, wanted)
    sub.next_charge_at = now + BLOCKED_RETRY


def _note_unresolved(session: Session, payment: Payment, why: str, now: datetime) -> bool:
    """A renewal charge nobody could find for `UNRESOLVED_AFTER`: tell the admins (once) and say
    whether the attempt now counts as a failed one for the clock. The payment is left `pending`:
    the charge may exist, so nothing is charged again for the period while it is unresolved, and
    it is asked about at every pass (`_settle_open_payments`). Returns False when the payment is
    no longer pending (a webhook settled it while we waited on the provider): not ours to count."""
    session.refresh(payment)  # read it as it is now, not as it was before the provider was asked
    if payment.status != "pending":
        session.commit()
        return False
    subscriptions._tell_admins(
        session,
        f"admin.billing_unresolved_charge:{payment.id}:{now:%Y-%m-%d}",  # again each day
        "A renewal charge has no known outcome",
        f"Renewal payment {payment.id} was sent to the provider more than six hours ago and {why}. "
        "The attempt now counts as failed for the clock: the person has the 3 days of grace. "
        "Nothing is charged again for this period while it is unresolved, and it is asked about "
        "again at every pass. Look it up by reference in the provider's dashboard: if it was paid "
        "it is settled when the provider reports it.",
    )
    session.commit()
    return True


def _settle_open_payments(
    session: Session, sub: Subscription, now: datetime, settings: Settings
) -> str:
    """Resolve renewal payments of this period that are still `pending`. Returns `paid` (one
    settled: the subscription moved on), `open_link` (a charge waits for the payer's own link),
    `gave_up` (a charge in flight that nobody could find, or that stayed `pending` with nothing for
    the payer, for `UNRESOLVED_AFTER`: still `pending`, never charged again, but counted as a
    failed attempt for the clock), `wait` (the provider could not be asked, or a charge is in
    flight: try again next pass) or `none`."""
    pending = session.scalars(
        select(Payment)
        .where(
            Payment.subscription_id == sub.id,
            Payment.kind == "renewal",
            Payment.cycle_end == sub.current_period_end,
            Payment.status == "pending",
        )
        .order_by(Payment.id)
    ).all()
    result = "none"
    for payment in pending:
        # A hosted checkout made for the payer (`attempt` is null: the pay link) is not a charge in
        # flight: it is settled if the provider says it was paid, and otherwise ignored.
        in_flight = payment.attempt is not None
        # A young hosted page: the person is probably paying it right now (see below).
        holds_charge = (
            not in_flight and bool(payment.pay_url) and payment.created_at > now - PAGE_HOLDS_CHARGE
        )
        adapter = provider_named(payment.provider, settings)
        if adapter is None:
            result = "wait" if in_flight else result
            continue
        try:
            tx = lookup_by_reference(adapter, payment.reference)
        except ProviderError:
            # No answer. "The provider is down" and "the charge call never left, so the provider
            # has nothing under our reference" look the same, so we cannot tell and we wait, but
            # not for ever: after six hours it is given up (the admins are told).
            if not in_flight:
                if holds_charge and result == "none":
                    result = "wait"  # a page with no record yet (nobody has tried to pay it)
                continue
            if payment.created_at <= now - UNRESOLVED_AFTER and _note_unresolved(
                session, payment, "the provider cannot say what became of it", now
            ):
                result = "gave_up" if result == "none" else result
            else:
                result = "wait"
            continue
        if tx.status == "successful":
            try:
                refusal = settle.settle_transaction(session, adapter, payment.id, tx, now)
                session.commit()
            except RetryLater:
                session.rollback()
                result = "wait"
                continue
            if refusal is None and session.get(Payment, payment.id).status == "succeeded":
                return "paid"
            if refusal:
                subscriptions._tell_admins(
                    session,
                    f"admin.billing_payment_refused:{payment.id}",
                    "A renewal payment was refused by the settlement rules",
                    f"Renewal payment {payment.id}: {refusal}. The provider reports it paid; "
                    "look at it.",
                )
                session.commit()
            result = "wait"
        elif tx.status == "failed":
            settle.settle_transaction(session, adapter, payment.id, tx, now)
            session.commit()
        elif not in_flight:
            # A hosted page nobody has paid (yet): normally ignored, but a young one means the
            # person is probably paying it right now, so the saved card is not charged beside it
            # (both succeeding is a refund by hand). Two hours at most: an abandoned page holds
            # nothing. The slot is not spent while waiting.
            if holds_charge and result == "none":
                result = "wait"
            continue
        elif payment.pay_url and result != "wait":
            result = "open_link"
        elif (
            not payment.pay_url
            and payment.created_at <= now - UNRESOLVED_AFTER
            and _note_unresolved(
                session, payment, "has stayed pending with no page for the payer", now
            )
        ):
            # a charge the provider keeps reporting as pending with nothing for the payer to do
            result = "gave_up" if result in ("none", "open_link") else result
        else:
            result = "wait"
    return result


def _route_moved(
    session: Session, sub: Subscription, user: User, settings: Settings, now: datetime
) -> bool:
    """A billing-country change that crossed routes has taken effect: the saved card belongs to
    the old provider and cannot be moved, so this renewal is a fresh checkout (spec 5.2)."""
    try:
        view = profiles.settle_due_country(session, user, now)
        current = routing.provider_for(view.billing_country, settings.billing_country_routes)
    except routing.RoutingError:
        return False
    return current != subscriptions.sold_route(sub)


def _record_failed_attempt(
    session: Session, sub: Subscription, user, price, n: int, cycle: datetime, now: datetime
) -> None:
    """An attempt that made no charge (no card, no price, the route moved): still written to the
    payments table as a failed attempt, so the history shows it (spec 3.3)."""
    if user is None or price is None:
        return
    try:
        with session.begin_nested():
            payment = new_payment(
                session,
                payer=user,
                provider=sub.provider,
                kind="renewal",
                currency=sub.currency,
                amount_minor=price.amount_minor,
                subscription_id=sub.id,
            )
            payment.plan_price_id = price.id
            payment.cycle_end = cycle
            payment.attempt = n
            payment.status = "failed"
            payment.created_at = now
            session.flush()
    except IntegrityError:
        pass  # the slot already has its row


def _open_grace(session: Session, sub: Subscription, cycle: datetime) -> None:
    """The grace opens: until the due moment + 72 h. The period's leftover hours stay usable until
    it ends (spec 3.3): they ended at the due moment, the grace gives them back, and nothing new is
    ever added here."""
    sub.grace_ends_at = states.grace_end(cycle)
    session.execute(
        update(HourGrant)
        .where(
            HourGrant.subscription_id == sub.id,
            HourGrant.kind == "plan_period",
            HourGrant.expires_at == cycle,
        )
        .values(expires_at=sub.grace_ends_at)
    )


def _set_grace_end(session: Session, sub: Subscription, new_end: datetime) -> None:
    """Move the end of an open grace (an outage of ours extends it) and its leftover hours."""
    old_end = sub.grace_ends_at
    sub.grace_ends_at = new_end
    session.execute(
        update(HourGrant)
        .where(
            HourGrant.subscription_id == sub.id,
            HourGrant.kind == "plan_period",
            HourGrant.expires_at == old_end,
        )
        .values(expires_at=new_end)
    )


def _fail(session: Session, sub_id: int, cycle: datetime, n: int, now: datetime) -> None:
    """Attempt `n` did not pay: move the status machine under the lock. Applied once (a second
    worker, or a second pass, finds the attempt already counted), and not at all when the
    subscription has moved on in the meantime (a webhook settled the period)."""
    sub = session.scalars(
        select(Subscription)
        .where(Subscription.id == sub_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    ).one_or_none()
    if (
        sub is None
        or sub.status not in states.LIVE
        or sub.current_period_end != cycle
        or sub.retry_count != n
    ):
        session.rollback()
        return
    transition = states.apply(sub.status, states.failed_event(n))
    if transition is None:
        session.rollback()
        return
    sub.retry_count = n + 1
    sub.status = transition.status
    sub.updated_at = now
    if states.OPEN_GRACE in transition.effects:
        _open_grace(session, sub, cycle)
    if states.SCHEDULE_NEXT_ATTEMPT in transition.effects:
        sub.next_charge_at = max(states.attempt_time(cycle, n + 1), now + MIN_RETRY_GAP)
    if states.STOP_ATTEMPTS in transition.effects:
        sub.next_charge_at = None
    if states.NOTIFY_FAILED in transition.effects:
        _notify(
            session, sub, "billing.failed", "Your payment did not go through", FAILED_BODY, cycle
        )
    if states.NOTIFY_LAST_DAY in transition.effects:
        _notify(
            session, sub, "billing.last_day", "Last day to pay for your plan", LAST_DAY_BODY, cycle
        )
    session.commit()


# ---- the sweeper ----


def sweep(session: Session, now: datetime, settings: Settings) -> None:
    _sweep_events(session, now, settings)
    _sweep_payments(session, now, settings)
    steps = (
        (_issue_missing_invoices.__name__, lambda: _issue_missing_invoices(session, now, settings)),
        (_alert_stuck_refunds.__name__, lambda: _alert_stuck_refunds(session, now)),
        (_alert_old_pending.__name__, lambda: _alert_old_pending(session, now)),
    )
    for name, after_money in steps:
        try:  # one failing check must not stop the others (or the next pass)
            after_money()
        except Exception as exc:
            session.rollback()
            logger.error("billing sweep step %s failed: %s", name, type(exc).__name__)


def _issue_missing_invoices(session: Session, now: datetime, settings: Settings) -> None:
    """Stage S9: a used payment with no invoice (its handler failed, or it was settled before the
    stage) gets one now, with its receipt mail."""
    invoices.issue_missing(session, now, settings)


def _alert_stuck_refunds(session: Session, now: datetime) -> None:
    """Stage S9: a refund left `pending` by a process that died: the admins look at the provider."""
    refunds.alert_stuck(session, now)


STUCK_BAND = (timedelta(hours=24), timedelta(hours=48))
STUCK_IDS_SHOWN = 15


def _alert_old_pending(session: Session, now: datetime) -> None:
    """Stage S9: payments that waited more than a day for a result (an abandoned or refused
    checkout, a webhook that never came and a provider that says nothing). One digest a day, ids
    only: each payment is in the band of 24 to 48 hours for a day, so it is named once or twice,
    never for ever. The sweeper already settled any the provider says succeeded."""
    young, old = STUCK_BAND
    rows = session.scalars(
        select(Payment.id)
        .where(
            Payment.status == "pending",
            Payment.created_at <= now - young,
            Payment.created_at > now - old,
        )
        .order_by(Payment.id)
    ).all()
    if not rows:
        return
    shown = ", ".join(str(i) for i in rows[:STUCK_IDS_SHOWN])
    more = f" and {len(rows) - STUCK_IDS_SHOWN} more" if len(rows) > STUCK_IDS_SHOWN else ""
    subscriptions._tell_admins(
        session,
        f"admin.billing_pending_old:{now:%Y-%m-%d}",
        "Payments have waited more than a day",
        f"{len(rows)} payment(s) were started more than a day ago and have no result yet: "
        f"{shown}{more}. Most are checkouts nobody finished; a refused or lost payment looks the "
        "same, so look at the provider's dashboard for any you expect.",
    )
    session.commit()


def _sweep_events(session: Session, now: datetime, settings: Settings) -> None:
    """Stored webhook events nobody decided (the provider's retries ran out, or a step failed):
    decide them again. The signature was checked when they arrived; the body is the stored one
    with tokens removed, which is all the decision needs (it re-reads the payment itself)."""
    rows = session.scalars(
        select(BillingEvent)
        .where(
            BillingEvent.processed_at.is_(None),
            BillingEvent.received_at <= now - SWEEP_AFTER,
            BillingEvent.received_at > now - SWEEP_LOOKBACK,
            BillingEvent.type.in_(("charge_succeeded", "charge_failed")),
            # an event received before the go-live cutover is about a test payment: the live
            # provider has no record of it, so asking again can only fail (`cutover`)
            cutover.live_only(BillingEvent.received_at, settings),
        )
        .order_by(BillingEvent.received_at.desc())  # newest first: a stuck pile never starves it
        .limit(BATCH)
    ).all()
    for row in rows:
        adapter = provider_named(row.provider, settings)
        if adapter is None:
            continue
        try:
            event = adapter.parse_event(json.dumps(row.payload).encode())
            settle.process(session, adapter, row, event, now)
        except RetryLater:
            continue  # parked by `process`, which also tells the admins of a paid grant that fails
        except Exception as exc:  # the stored body would not parse, or the database is gone
            session.rollback()
            logger.error("sweeping event %s failed: %s", row.id, type(exc).__name__)


def _sweep_payments(session: Session, now: datetime, settings: Settings) -> None:
    """A payment still `pending` after two minutes (or a recent `failed` plan, pack or upgrade
    checkout, see below): ask the provider by our reference, and settle it only if the provider says
    it succeeded (an unpaid or abandoned checkout is left alone: the payer may still finish it)."""
    hour, day = timedelta(hours=1), timedelta(days=1)
    # Each payment is asked about on a back-off that grows with its age (every 2 minutes for the
    # first hour, every 30 minutes for the first day, every 3 hours after), and the ones asked about
    # least recently (never first) go first: a pile of abandoned checkouts cannot hold back a
    # payment that was just paid, and costs a handful of lookups a day instead of one a minute.
    due = or_(
        Payment.last_checked_at.is_(None),
        and_(Payment.created_at > now - hour, Payment.last_checked_at <= now - SWEEP_AFTER),
        and_(
            Payment.created_at <= now - hour,
            Payment.created_at > now - day,
            Payment.last_checked_at <= now - timedelta(minutes=30),
        ),
        and_(Payment.created_at <= now - day, Payment.last_checked_at <= now - timedelta(hours=3)),
    )
    # A `failed` plan, pack or upgrade checkout that has its page (one the person cancelled, or one
    # the provider reported failed: a failed payment can still be paid) is asked about too, less
    # often: every 30 minutes the first day, every 3 hours after. Nothing else that failed is
    # touched.
    failed_due = or_(
        Payment.last_checked_at.is_(None),
        and_(
            Payment.created_at > now - day,
            Payment.last_checked_at <= now - timedelta(minutes=30),
        ),
        and_(Payment.created_at <= now - day, Payment.last_checked_at <= now - timedelta(hours=3)),
    )
    rows = session.scalars(
        select(Payment)
        .where(
            Payment.created_at <= now - SWEEP_AFTER,
            # a payment made before the go-live cutover is a test payment (`cutover`): never asked
            # about on the live API
            cutover.live_only(Payment.created_at, settings),
            or_(
                and_(
                    Payment.status == "pending",
                    Payment.created_at > now - SWEEP_LOOKBACK,
                    due,
                ),
                and_(
                    Payment.status == "failed",
                    Payment.kind.in_(("plan", "pack", "upgrade")),
                    Payment.pay_url.is_not(None),
                    Payment.created_at > now - FAILED_LOOKBACK,
                    failed_due,
                ),
            ),
        )
        .order_by(Payment.last_checked_at.asc().nullsfirst(), Payment.id.desc())
        .limit(BATCH)
    ).all()
    for payment in rows:
        adapter = provider_named(payment.provider, settings)
        if adapter is None:
            continue
        payment_id, reference, created_at = payment.id, payment.reference, payment.created_at
        session.execute(update(Payment).where(Payment.id == payment_id).values(last_checked_at=now))
        session.commit()
        paid = False  # the provider itself has said `successful`: only then is a failure an alert
        try:
            tx = lookup_by_reference(adapter, reference)
            if tx.status != "successful":
                continue
            paid = True
            refusal = settle.settle_transaction(session, adapter, payment_id, tx, now)
            if refusal:  # the provider says it is paid, our rules say no: a human looks
                subscriptions._tell_admins(
                    session,
                    f"admin.billing_payment_refused:{payment_id}",
                    "A payment was refused by the settlement rules",
                    f"Payment {payment_id}: {refusal}. The provider reports it paid; look at it.",
                )
            session.commit()
        except (ProviderError, RetryLater) as exc:
            session.rollback()
            if paid:  # not expected after `successful`; never silent (type only, no text)
                logger.warning(
                    "sweeping payment %s: %s after the provider reported it paid",
                    payment_id,
                    type(exc).__name__,
                )
        except Exception as exc:
            session.rollback()
            logger.error("sweeping payment %s failed: %s", payment_id, type(exc).__name__)
            if paid:  # a person who paid has nothing: told once, once it has gone on a while
                first_seen = _first_seen(session, adapter, reference, created_at, now)
                settle.alert_grant_failed(session, payment_id, first_seen, exc, now)


def _first_seen(session: Session, adapter, reference: str, created_at: datetime, now: datetime):
    """When the system first had a reason to settle this payment: the `received_at` of its oldest
    undecided `charge_succeeded` event (the webhook that said it was paid), else its `created_at`.
    An old checkout paid just now would otherwise look old at its very first failure. Reads the
    stored events of this provider that are still waiting (a handful, within the sweep lookback)
    and matches the payment's reference from each body, as `_sweep_events` reads them; any
    failure falls back to `created_at`, since this must never break the sweep."""
    try:
        waiting = session.scalars(
            select(BillingEvent)
            .where(
                BillingEvent.provider == adapter.name,
                BillingEvent.type == "charge_succeeded",
                BillingEvent.processed_at.is_(None),
                BillingEvent.received_at > now - SWEEP_LOOKBACK,
            )
            .order_by(BillingEvent.received_at.asc())
            .limit(BATCH * 4)
        ).all()
        for row in waiting:
            if adapter.parse_event(json.dumps(row.payload).encode()).reference == reference:
                return row.received_at
    except Exception as exc:
        session.rollback()
        logger.warning("first sight of a paid payment not read: %s", type(exc).__name__)
    return created_at


# ---- the pay link ----


def _open_page(
    session: Session, sub: Subscription, now: datetime, settings: Settings
) -> Payment | None:
    """The payment of this period whose page the person can still pay, or None when a fresh one may
    be made. Asks the provider about every unpaid renewal payment of the period (newest first):

    - `successful`: it is settled now (the plan is renewed) and the caller is told nothing is due;
    - `failed`: marked failed, and does not count;
    - still open with a link: that is the page (an open charge keeps its own link for ever);
    - open with no link (a charge in flight): refused, 409 `payment_in_progress`;
    - the provider has no record of a pay-link checkout nobody paid (or cannot be asked): its page
      is reused WHATEVER ITS AGE, since handing back the same page can never double-charge; only an
      open CHARGE (`attempt` set) that cannot be checked is refused, 502 `provider_error`, rather
      than guessed;
    - a pay-link row with no page: younger than ten minutes it is another request opening the page
      right now (409 `payment_in_progress`); older (the process died between making the row and
      storing the page) it is abandoned: marked failed.
    """
    pending = session.scalars(
        select(Payment)
        .where(
            Payment.subscription_id == sub.id,
            Payment.kind == "renewal",
            Payment.cycle_end == sub.current_period_end,
            Payment.status == "pending",
        )
        .order_by(Payment.id.desc())
    ).all()
    chosen = None
    for payment in pending:
        if payment.attempt is None and not payment.pay_url:
            if payment.created_at <= now - NO_LINK_GRACE:
                payment.status = "failed"  # abandoned half-made page: never a reason to block
                continue
            # Young: another request is opening this page right now (the row is committed before
            # the provider is called, which frees the subscription lock). A second click must wait,
            # not make a second page for the same period.
            session.rollback()
            raise subscriptions.Refused(
                409,
                "payment_in_progress",
                "A payment page is being opened. Wait a moment and try again.",
            )
        adapter = provider_named(payment.provider, settings)
        try:
            if adapter is None:
                raise ProviderError("no provider is configured for this payment")
            tx = lookup_by_reference(adapter, payment.reference)
        except ProviderError:
            if payment.attempt is None:  # an unpaid page: the same page, however old
                chosen = chosen or payment
                continue
            session.rollback()
            _tell_payer_blocked(session, payment, now)
            raise subscriptions.Refused(
                502,
                "provider_error",
                "We could not check your open payment just now. Try again in a moment.",
            ) from None
        if tx.status == "successful":
            try:
                refusal = settle.settle_transaction(session, adapter, payment.id, tx, now)
            except RetryLater:
                refusal = "the provider is not final yet"
            if refusal is None:
                session.commit()
                raise subscriptions.Refused(
                    409, "nothing_due", "Your payment has just been received."
                )
            subscriptions._tell_admins(
                session,
                f"admin.billing_payment_refused:{payment.id}",
                "A renewal payment was refused by the settlement rules",
                f"Renewal payment {payment.id}: {refusal}. The provider reports it paid; "
                "look at it.",
            )
            session.commit()
            raise subscriptions.Refused(
                409,
                "payment_in_progress",
                "We could not confirm your payment yet. We are looking into it.",
            )
        if tx.status == "failed":
            settle.settle_transaction(session, adapter, payment.id, tx, now)
            continue
        if payment.pay_url:
            chosen = chosen or payment
        else:
            session.rollback()
            _tell_payer_blocked(session, payment, now)
            raise subscriptions.Refused(
                409,
                "payment_in_progress",
                "A payment is already being processed. Wait a moment and check again.",
            )
    return chosen


def _tell_payer_blocked(session: Session, payment: Payment, now: datetime) -> None:
    """A person pressed Pay now and cannot be given a page because a charge of this period may
    exist and cannot be checked (the dead end the reminder mails lead into: they say open Billing
    and pay). Silent for a charge younger than `UNRESOLVED_AFTER` (ordinary waiting); older, the
    admins are told, once a day per payment, so the dead end is never left to the person alone."""
    if payment.created_at > now - UNRESOLVED_AFTER:
        return
    subscriptions._tell_admins(
        session,
        f"admin.billing_payer_blocked:{payment.id}:{now:%Y-%m-%d}",
        "A person cannot pay: an earlier charge cannot be checked",
        f"Someone pressed Pay now on their overdue plan and was refused, because renewal payment "
        f"{payment.id} may have been charged and the provider cannot say. Look it up by reference "
        "in the provider's dashboard: if it was paid it is settled when the provider reports it; "
        "if it was not, nothing else can open a page for them until it is closed.",
    )
    session.commit()


def pay_link(
    session: Session, user: User, settings: Settings, now: datetime
) -> subscriptions.Started:
    """The page to pay what is overdue (a plan in its grace). The same link as a charge that is
    already open for this period (3-D Secure), else a fresh hosted checkout for the renewal amount
    on the route the person is on now. Commits."""
    sub = subscriptions.live_row(session, user.id, lock=True)
    if sub is None:
        raise subscriptions.Refused(409, "no_subscription", "You do not have a plan.")
    if states.effective_status(subscriptions.snapshot(sub), now) != states.PAST_DUE:
        session.rollback()
        raise subscriptions.Refused(409, "nothing_due", "There is nothing to pay right now.")
    # Stage S9: while a refund of the plan's first payment is `pending` or `unknown` the plan may
    # be about to end: nothing new is charged on it, a pay link included.
    try:
        subscriptions._refuse_while_refunding(session, sub)
    except subscriptions.Refused:
        session.rollback()
        raise
    # Anything already open for this period is asked about FIRST, by our reference, at the
    # provider: a person must never be given a second page while a first one can still be paid
    # (that is a double charge). Nothing new is made until the provider says every open one is
    # failed, and an open charge keeps its OWN link however old it is.
    chosen = _open_page(session, sub, now, settings)
    if chosen is not None:
        started = subscriptions.Started(chosen, chosen.pay_url)
        session.commit()  # keeps anything `_open_page` marked failed
        return started
    sub = subscriptions.live_row(session, user.id, lock=True)  # the lock may have been released
    if sub is None or states.effective_status(subscriptions.snapshot(sub), now) != states.PAST_DUE:
        session.rollback()
        raise subscriptions.Refused(409, "nothing_due", "There is nothing to pay right now.")
    try:  # asked again under THIS lock: a refund may have started while the first was released
        subscriptions._refuse_while_refunding(session, sub)
    except subscriptions.Refused:
        session.rollback()
        raise

    sold = subscriptions.sold_route(sub)
    route = sold
    try:
        view = profiles.settle_due_country(session, user, now)
        current = routing.provider_for(view.billing_country, settings.billing_country_routes)
        route = current
    except routing.RoutingError:
        pass
    price = (
        plans.renewal_price(session, sub, now)
        if route == sold
        else plans.current_price(
            session, sub.pending_plan_code or sub.plan_code, route.currency, now
        )
    )
    if price is None:
        session.rollback()
        raise subscriptions.Refused(
            422, "price_not_available", f"Your plan is not on sale in {route.currency} yet."
        )
    adapter = subscriptions._adapter(route.provider, settings)
    payment = new_payment(
        session,
        payer=user,
        provider=route.provider,
        kind="renewal",
        currency=route.currency,
        amount_minor=price.amount_minor,
        subscription_id=sub.id,
    )
    payment.plan_price_id = price.id
    payment.cycle_end = sub.current_period_end
    payment.created_at = now
    return subscriptions.send_to_provider(session, adapter, payment, user, settings)
