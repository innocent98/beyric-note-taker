"""Refunds, by an admin, for exactly the three cases the spec allows (billing spec 3.5, stage S9).

- `double_charge`: the payment succeeded and NOTHING could use it (`payments.needs_refund_reason`:
  a second payment for a period already paid, money for a cancelled plan, a gone account). It
  granted no hours, so there is nothing to remove.
- `first_plan_7d`: the payment is the person's FIRST plan payment ever, within 7 days of it, and
  none of its hours were used. The refund ends the grant it bought, ends the subscription and
  deletes the saved card.
- `pack_14d`: a pack payment within 14 days of it, and none of its hours were used. The refund ends
  the grant it bought.

Any other payment is refused: no refund outside the three cases (a real service failure on our side
is refunded by a person in the provider's dashboard, by hand). **Only whole payments** are refunded:
the amount is read from the payment, never from the request, so nobody can type another one.

**"None of its hours were used"** is read from the person's hours as `entitlements` reads them: the
grant the payment bought must hold all its seconds after the usage of the allocations AND the
backlog (usage counted before allocations existed) is taken off in the spending order, and the
person must have no recording running or being prepared. The check and the removal happen under the
payment's row lock and the person's hours lock (the one `hours.allocate` takes), so a recording
counted at that moment cannot spend the hours in between.

**The double-refund risk, and how it is closed.** A refund moves real money and cannot be undone.

1. One refund per payment at a time: a partial unique index on `refunds` (`status in pending,
   submitted, unknown`) and the payment's row lock. Two admins pressing the button make one.
2. The refund row, with our own `reference`, is COMMITTED before the provider is called, so a crash
   leaves a trace, never an unrecorded call.
3. The provider is called once. Paystack takes our `reference` as an idempotency key (a repeat is a
   409); Flutterwave v3 has none, so we never repeat the call at all.
4. A call that got no answer (`OutcomeUnknown`) is NEVER retried, by code or by a button: the refund
   becomes `unknown` and the admins are told to look at the provider's dashboard for its reference
   and then say what they saw (`resolve`: `made` or `not_made`). Only `not_made` lets a new refund
   start, so a refund that exists can never be asked for again. The same goes for a `pending` row
   whose process died before the provider answered, once it is `STUCK_AFTER` old.
5. Once a refund is `submitted`, the payment is `refunded` and no new refund can start.

The hours covered are removed when the refund STARTS (the grant is ended under the locks, so the
person cannot spend them while the provider decides) and given back if the provider refuses it or an
admin confirms none was made. A refund `unknown` keeps them removed.

This module never commits the provider's call inside a transaction that holds a lock: three short
transactions (start, call with no lock held, apply the outcome).
"""

import logging
import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from app import alerts, entitlements, hours, usage
from app.billing import notify, states
from app.billing import subscriptions as subs
from app.billing.provider import provider_named
from app.billing.types import OutcomeUnknown, ProviderError
from app.config import Settings
from app.models import (
    REFUND_LIVE_STATUSES,
    REFUND_REASONS,
    HourGrant,
    Meeting,
    Payment,
    PaymentMethod,
    Refund,
    Subscription,
    UsageEntry,
    User,
)

logger = logging.getLogger(__name__)

FIRST_PLAN_WINDOW = timedelta(days=7)
PACK_WINDOW = timedelta(days=14)
# A refund still `pending` this long after it started was left by a process that died before the
# provider answered: nobody knows whether the call went out, so it is handled as `unknown`.
STUCK_AFTER = timedelta(minutes=10)
NOTE_MAX = 500

Refused = subs.Refused


@dataclass(frozen=True)
class Assessment:
    reason: str
    grant: HourGrant | None  # the grant the refund ends (None for a double charge)
    subscription: Subscription | None  # the plan the refund ends (first plan payment only)


def _refuse(code: str, message: str, status: int = 409) -> Refused:
    return Refused(status, code, message)


def _own_grant(db: Session, payment: Payment) -> HourGrant | None:
    return db.scalars(
        select(HourGrant).where(HourGrant.source_payment_id == payment.id).order_by(HourGrant.id)
    ).first()


def _recording_running(db: Session, user: User) -> bool:
    counted = select(UsageEntry.meeting_id).where(UsageEntry.meeting_id.is_not(None))
    return (
        db.scalar(
            select(func.count())
            .select_from(Meeting)
            .where(
                Meeting.owner_user_id == user.id,
                Meeting.status.in_(
                    usage.RUNNING
                ),  # the bot is in the call, or it left and is not counted yet
                Meeting.id.not_in(counted),
            )
        )
        > 0
    )


def _is_first_plan_payment(db: Session, payment: Payment) -> bool:
    """The earliest `plan` payment the person ever made that succeeded and was used, refunded ones
    included: a person who bought, was refunded and bought again has no second first payment. A
    payment nothing could use (a double charge) is skipped, so it cannot hide the real first one."""
    earliest = db.scalar(
        select(Payment.id)
        .where(
            Payment.payer_user_id == payment.payer_user_id,
            Payment.kind == "plan",
            Payment.status.in_(("succeeded", "refunded", "partly_refunded")),
            # a double-charged payment bought nothing: it must not be what "first" means
            Payment.needs_refund_reason.is_(None),
        )
        .order_by(Payment.paid_at.asc(), Payment.id.asc())
        .limit(1)
    )
    return earliest == payment.id


def _hours_untouched(db: Session, user: User, grant: HourGrant, now: datetime) -> bool:
    """True when the grant still holds every second it was made with, after the allocations and the
    backlog are taken off in the spending order."""
    for held, left in entitlements.held_after_backlog(db, user, now):
        if held.id == grant.id:
            return left >= grant.seconds
    return False  # not in force any more: its hours are gone


def assess(
    db: Session, payment: Payment, reason: str, now: datetime, *, lock: bool = True
) -> Assessment:
    """Whether `payment` may be refunded for `reason` now, judged on the facts. Raises `Refused`
    (409) naming what is wrong. Call it under the payment's row lock and the person's hours lock."""
    if payment.status == "refunded":
        raise _refuse("already_refunded", "This payment was refunded already.")
    if payment.status != "succeeded":
        raise _refuse("payment_not_paid", "Only a payment that succeeded can be refunded.")
    if not payment.provider_tx_id:
        raise _refuse("no_provider_transaction", "The provider's transaction for it is unknown.")
    if db.scalar(
        select(Refund.id).where(
            Refund.payment_id == payment.id, Refund.status.in_(REFUND_LIVE_STATUSES)
        )
    ):
        raise _refuse(
            "refund_in_progress",
            "A refund of this payment is already under way. Look at it in the refunds list.",
        )
    grant = _own_grant(db, payment)
    if reason == "double_charge":
        if payment.needs_refund_reason is None or grant is not None:
            raise _refuse(
                "not_a_double_charge",
                "This payment was used (it bought a plan or hours): it is not a double charge.",
            )
        return Assessment(reason, None, None)
    window = FIRST_PLAN_WINDOW if reason == "first_plan_7d" else PACK_WINDOW
    wanted_kind = "plan" if reason == "first_plan_7d" else "pack"
    if payment.kind != wanted_kind:
        raise _refuse(
            "wrong_kind",
            "This is not a first plan payment."
            if wanted_kind == "plan"
            else "This is not a pack payment.",
        )
    if payment.needs_refund_reason is not None or grant is None:
        raise _refuse(
            "not_used_for_hours",
            "Nothing was granted for this payment: refund it as a double charge.",
        )
    if wanted_kind == "plan" and not _is_first_plan_payment(db, payment):
        raise _refuse(
            "not_first_payment",
            "Only a person's first plan payment can be refunded this way.",
        )
    if payment.paid_at is None or now - payment.paid_at > window:
        raise _refuse(
            "window_over",
            f"The refund window ({window.days} days from the payment) is over.",
        )
    user = db.get(User, payment.payer_user_id) if payment.payer_user_id else None
    if user is None or user.status == "deleted":
        raise _refuse("payer_gone", "The person's account is gone: refund it by hand if you must.")
    if _recording_running(db, user):
        raise _refuse(
            "recording_in_progress",
            "A recording is running or being prepared. Try again when it is done.",
        )
    if not _hours_untouched(db, user, grant, now):
        raise _refuse("hours_used", "Hours from this payment were used: it cannot be refunded.")
    sub = None
    if wanted_kind == "plan":
        later = db.scalar(
            select(func.count())
            .select_from(Payment)
            .where(
                Payment.subscription_id == payment.subscription_id,
                Payment.id != payment.id,
                Payment.kind.in_(("upgrade", "renewal")),
                Payment.status == "succeeded",
            )
        )
        if later:
            raise _refuse(
                "plan_changed",
                "The plan was changed or renewed since this payment: refund it by hand.",
            )
        query = select(Subscription).where(Subscription.id == payment.subscription_id)
        sub = db.scalars(query.with_for_update() if lock else query).first()
    return Assessment(reason, grant, sub)


# ---- the three steps ----


def new_reference(payment_id: int) -> str:
    """Our key for one refund: `bnrf-<payment id>-<random>`, 8 to 64 characters of letters, digits
    and `-`, the form Paystack accepts as a refund reference."""
    return f"bnrf-{payment_id}-{secrets.token_hex(8)}"


def _start(
    db: Session, payment_id: int, reason: str, admin: User, note: str | None, now: datetime
) -> Refund:
    """Step one: check, write the refund row, end the grant it covers. Commits."""
    payment = db.scalars(select(Payment).where(Payment.id == payment_id).with_for_update()).first()
    if payment is None:
        raise Refused(404, "not_found", "No such payment.")
    payer = db.get(User, payment.payer_user_id) if payment.payer_user_id else None
    if payer is not None:
        hours.lock_person(db, payer)
    found = assess(db, payment, reason, now)
    refund = Refund(
        payment_id=payment.id,
        reference=new_reference(payment.id),
        reason=reason,
        status="pending",
        amount_minor=payment.amount_minor,
        currency=payment.currency,
        note=(note or "").strip()[:NOTE_MAX] or None,
        created_by=admin.id,
        created_at=now,  # the caller's clock: STUCK_AFTER counts from it
    )
    if found.grant is not None:
        refund.grant_id = found.grant.id
        refund.grant_expires_before = found.grant.expires_at
        refund.seconds_removed = found.grant.seconds
        found.grant.expires_at = now  # the hours leave NOW: nobody spends them while we decide
    db.add(refund)
    db.flush()
    db.commit()
    return refund


def _restore_hours(db: Session, refund: Refund) -> None:
    """The refund did not happen: the grant it ended is in force again."""
    grant = db.get(HourGrant, refund.grant_id) if refund.grant_id else None
    if grant is not None:
        grant.expires_at = refund.grant_expires_before


def _accepted(
    db: Session, refund: Refund, payment: Payment, provider_refund_id: str | None, now: datetime
) -> None:
    """The refund exists at the provider: the payment is refunded, a plan ends, the person is told.
    In the caller's transaction."""
    refund.status = "submitted"
    refund.provider_refund_id = provider_refund_id
    refund.resolved_at = refund.resolved_at or now
    refund.error = None
    payment.status = "refunded"
    if refund.reason == "first_plan_7d" and payment.subscription_id is not None:
        sub = db.scalars(
            select(Subscription).where(Subscription.id == payment.subscription_id).with_for_update()
        ).first()
        if sub is not None:
            transition = states.apply(sub.status, states.REFUNDED)
            if transition is not None:
                sub.status = transition.status
                sub.next_charge_at = sub.grace_ends_at = None
                sub.updated_at = now
            else:  # not a live plan any more: the refund stands, nothing more to end
                logger.warning("refund %s: subscription %s is %s", refund.id, sub.id, sub.status)
        # the card was saved for renewals of a plan that no longer exists
        if payment.payer_user_id is not None:
            db.execute(
                delete(PaymentMethod).where(
                    PaymentMethod.user_id == payment.payer_user_id,
                    PaymentMethod.provider == payment.provider,
                )
            )
    if payment.payer_user_id is not None:
        paid = subs.money(payment.amount_minor, payment.currency)
        alerts.notify_user(
            db,
            payment.payer_user_id,
            kind="billing.refund",
            severity="info",
            title="Your payment was refunded",
            body=f"We refunded your payment of {paid}. "
            "Card refunds can take several days to reach your account.",
            dedupe_key=f"billing.refund:{refund.id}",
            email_template=alerts.ALERT_TEMPLATE,
        )


def _failed(db: Session, refund: Refund, error: str, now: datetime, by: User | None = None) -> None:
    """Nothing moved: the refund is closed and the hours it ended are back. In the caller's
    transaction."""
    refund.status = "failed"
    refund.error = error
    refund.resolved_at = now
    if by is not None:
        refund.resolved_by = by.id
    _restore_hours(db, refund)


def request(
    db: Session,
    payment_id: int,
    reason: str,
    admin: User,
    note: str | None,
    settings: Settings,
    now: datetime,
) -> Refund:
    """Refund payment `payment_id` for `reason`. Returns the refund as it stands after the
    provider's answer: `submitted`, `failed` (the provider refused: nothing moved) or `unknown` (no
    answer: look at the dashboard; nothing retries). Raises `Refused` before anything is written
    when the rules refuse it."""
    if reason not in REFUND_REASONS:
        raise Refused(422, "validation_error", "That is not a refund reason.")
    refund = _start(db, payment_id, reason, admin, note, now)
    refund_id, amount, reference = refund.id, refund.amount_minor, refund.reference
    payment = db.get(Payment, payment_id)
    provider_name, tx_id = payment.provider, payment.provider_tx_id
    db.rollback()  # no transaction stays open while the provider is asked
    adapter = provider_named(provider_name, settings)
    answer: str | None = None
    error: str | None = None
    unknown = False
    if adapter is None:
        error = "provider_unavailable"
    else:
        try:
            answer = adapter.refund(tx_id, amount, reference=reference)
        except OutcomeUnknown:
            unknown = True
        except ProviderError:
            error = "provider_refused"
        except Exception as exc:  # whatever else: the call may or may not have gone out
            logger.error(
                "refund %s: unexpected %s from the provider", refund_id, type(exc).__name__
            )
            unknown = True
    # step three: a fresh transaction, no lock held while the provider was asked
    payment = db.scalars(select(Payment).where(Payment.id == payment_id).with_for_update()).one()
    refund = db.scalars(select(Refund).where(Refund.id == refund_id).with_for_update()).one()
    amount = subs.money(refund.amount_minor, refund.currency)
    if refund.status != "pending":
        # A person closed this refund (`resolve`, after STUCK_AFTER) while the provider's answer was
        # still on its way. The answer is NOT applied: the row, the hours and the payment stay as
        # the person left them, and the admins are told what the provider said, because money may
        # have moved after a refund that was closed as "not made".
        _late_answer(db, refund, payment, answer, error, unknown, amount)
        db.commit()
        return refund
    if unknown:
        refund.status = "unknown"
        refund.error = "no_answer"
        notify.tell_admins(
            db,
            f"admin.billing_refund_unknown:{refund.id}",
            "A refund got no answer from the provider",
            f"Refund {refund.id} of payment {payment.id} ({amount}, {payment.provider}) got no "
            "answer. It may or may not exist. Look in the provider's "
            f"dashboard for the reference {refund.reference}, then resolve it in the refunds list: "
            "do NOT ask for it again until you have looked.",
        )
    elif error is not None:
        _failed(db, refund, error, now)
    else:
        _accepted(db, refund, payment, answer, now)
    db.commit()
    return refund


def _late_answer(
    db: Session,
    refund: Refund,
    payment: Payment,
    answer: str | None,
    error: str | None,
    unknown: bool,
    amount: str,
) -> None:
    said = "accepted it" if error is None and not unknown else "gave no answer or refused it"
    if refund.status == "submitted" and error is None and not unknown:
        return  # the person said "made", and the provider now agrees: nothing to tell
    notify.tell_admins(
        db,
        f"admin.billing_refund_late:{refund.id}",
        "A refund answer came after it was closed",
        f"Refund {refund.id} of payment {payment.id} ({amount}, {payment.provider}) was already "
        f"{refund.status} when the provider's answer arrived: it {said}"
        + (f" (provider refund id {answer})" if answer else "")
        + f". Nothing was changed. Look in the provider's dashboard for the reference "
        f"{refund.reference}: if the money really went back and the refund was closed as not made, "
        "the hours are back with the person and the payment is not marked refunded.",
    )


def resolve(db: Session, refund_id: int, outcome: str, admin: User, now: datetime) -> Refund:
    """An admin says what the provider's dashboard shows for an `unknown` refund (or a `pending`
    one left by a dead process): `made` (it exists: the payment is refunded) or `not_made` (it does
    not: the refund is closed and the hours come back, so a new refund can be started). The only
    way out of `unknown`; nothing here calls the provider."""
    if outcome not in ("made", "not_made"):
        raise Refused(422, "validation_error", "The outcome is `made` or `not_made`.")
    refund = db.scalars(select(Refund).where(Refund.id == refund_id).with_for_update()).first()
    if refund is None:
        raise Refused(404, "not_found", "No such refund.")
    waiting_too_long = refund.status == "pending" and now - refund.created_at >= STUCK_AFTER
    if refund.status != "unknown" and not waiting_too_long:
        raise _refuse("not_unresolved", "This refund is not waiting for a person to resolve it.")
    payment = db.scalars(
        select(Payment).where(Payment.id == refund.payment_id).with_for_update()
    ).one()
    refund.resolved_by = admin.id
    if outcome == "made":
        _accepted(db, refund, payment, refund.provider_refund_id, now)
    else:
        _failed(db, refund, "confirmed_not_made", now, admin)
    db.commit()
    return refund


def alert_stuck(db: Session, now: datetime) -> None:
    """The sweeper's check: a refund `pending` for `STUCK_AFTER` is a dead process's. Tell the
    admins once per refund; resolving it is the same as for `unknown`."""
    rows = db.scalars(
        select(Refund).where(Refund.status == "pending", Refund.created_at <= now - STUCK_AFTER)
    ).all()
    for refund in rows:
        notify.tell_admins(
            db,
            f"admin.billing_refund_stuck:{refund.id}",
            "A refund was left half-done",
            f"Refund {refund.id} of payment {refund.payment_id} was started and never answered "
            f"(its process died). Look in the provider's dashboard for the reference "
            f"{refund.reference}, then resolve it in the refunds list. Do not ask for it again "
            "until you have looked.",
        )
    if rows:
        db.commit()
