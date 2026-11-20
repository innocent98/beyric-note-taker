"""Deciding what a stored webhook means: the rules that decide whether a payment is paid.

A webhook is a claim. Nothing is believed from it except which payment and which transaction it is
about. For a `charge_succeeded` event the payment is re-read from the provider
(`fetch_transaction`) and is marked `succeeded` only if ALL of these hold, judged on what the
provider reported and never on what the webhook said (billing spec section 5.4):

1. the payment row exists (we made it, `payments.new_payment`) and belongs to this provider;
2. the provider reports the transaction `successful` (still `pending`: try again later);
3. its reference is the payment's own reference;
4. its currency is the currency we asked for;
5. its amount is at least the amount we asked for.

The provider's report is checked for shape first (`_malformed`): an amount that is not a whole
number, an unknown currency or status, a reference or id that is not text. A malformed report is
refused and recorded like any other refusal, so the provider is not asked to resend it for ever.
The card's country is a note, not a rule (`mismatch.py`): its two letters are kept (a vendor's
`NIGERIA NG` gives `NG`), anything else is stored as nothing, and it is compared with the payer's
billing country without ever refusing the payment.

Anything else is refused: the payment stays as it was, and the event is marked decided with the
reason in `billing_events.error`. A refusal never changes a payment.

**The grant point.** The first (and only) time a payment moves to `succeeded`, every function in
`GRANT_HANDLERS` runs inside the same database transaction, with the payment row locked. The hour
grants of the later stages register there. If a handler raises, the whole step is undone, the event
stays waiting and the webhook answers 503 so the provider sends it again. A payment already
`succeeded` runs no handler, so the same event twice, or two different events for one payment, grant
once. The grants' own unique key on (kind, source payment id) is the second line behind this.

**A grant that keeps failing is never silent.** When the provider says a payment is paid and a grant
handler (or the write around it) fails, the step is undone and the payment stays open, so a person
who paid has nothing. The first failure is only logged (a lock timeout or a deadlock that the next
pass clears should not page anyone); once the failure has gone on for `GRANT_ALERT_AFTER` the admins
are told once per payment (`notify.tell_grant_failed`, the key the cancel button shares). The
clock is the event's `received_at` here, the payment's `created_at` in the sweeper
(`alert_grant_failed`). `billing_events.error` keeps only the exception TYPE for such a failure,
never its text: the text can hold an email, an amount or the provider's own words.

A `charge_failed` event moves a `pending` payment to `failed` only if the provider, asked, says it
failed; a `failed` payment can still succeed later (a payer retries the same checkout).

**Stage S9 adds three things a person must hear about**, each an admin alert by ids only:
a successful charge under a DIFFERENT transaction id for a payment that is already `succeeded` (the
payer paid twice: the provider is asked first, so a stray event is not an alert), a successful
charge the rules REFUSED (a wrong amount, currency or reference: money may have moved), and a
`dispute` (a chargeback). A `refund` or `dispute` event is stored and marked decided: refunds are
started by an admin (`refunds.py`), so the provider's later notice changes nothing here.
"""

import hashlib
import logging
import os
import traceback
from collections.abc import Callable
from datetime import datetime, timedelta

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.billing import notify
from app.billing.mismatch import flag_country_mismatch, normalize_card_country
from app.billing.provider import PaymentProvider
from app.billing.types import (
    TRANSACTION_STATUSES,
    ProviderEvent,
    ProviderTransaction,
    RetryLater,
)
from app.models import BILLING_CURRENCIES, BillingEvent, Payment

logger = logging.getLogger(__name__)

# Called as `handler(db, payment)` once, when a payment first becomes `succeeded`. Stage S8
# registers the subscription and pack grants here (`subscriptions.py`).
GrantHandler = Callable[[Session, Payment], None]
GRANT_HANDLERS: list[GrantHandler] = []
# Called as `handler(db, payment, tx)` just before the grant handlers, with what the provider
# reported: the one place a handler can see the reusable card token (it is never stored in the
# inbox). The saved-card handler of stage S8 registers here. Same transaction, same single run.
TransactionHandler = Callable[[Session, Payment, ProviderTransaction], None]
TRANSACTION_HANDLERS: list[TransactionHandler] = []

OPEN_STATUSES = ("pending", "failed")  # a payment in one of these can still be paid

# How long a paid payment's grant must have been failing before the admins are told. The sweeper
# asks again every two minutes, so thirty minutes is about fifteen failed passes (or several of the
# provider's own webhook retries): long enough that a one-off lock timeout or deadlock which the
# next pass clears never pages anyone, short enough that a person who paid and got nothing is
# looked at within the hour.
GRANT_ALERT_AFTER = timedelta(minutes=30)


class _PaidButNotGranted(Exception):
    """Internal to `process`: the provider reported the payment `successful`, the rules accepted it,
    and then granting failed. The original exception is `__cause__`. Wrapped only so `process` can
    tell this failure (a person who paid has nothing) from a provider or parse failure."""

    def __init__(self, payment_id: int) -> None:
        super().__init__(payment_id)
        self.payment_id = payment_id


def process(
    db: Session, provider: PaymentProvider, row: BillingEvent, event: ProviderEvent, now
) -> None:
    """Decide one stored event. Marks `row` decided, or raises `RetryLater` leaving it waiting."""
    row_id, received_at = row.id, row.received_at
    try:
        refusal = _apply(db, provider, row, event, now)
    except RetryLater as exc:
        _park(db, row, str(exc))  # our own words (`_refuse_transaction`), never a provider's
        raise
    except _PaidButNotGranted as failed:  # a paid payment whose grant failed: nothing is kept
        cause = failed.__cause__ or failed
        _log_failure(f"billing event {row_id} was paid but could not be granted", cause)
        _park(db, row, type(cause).__name__)
        alert_grant_failed(db, failed.payment_id, received_at, cause, now)
        raise RetryLater("the event could not be applied") from cause
    except Exception as exc:  # a provider failure, or a step that failed: nothing is kept
        _log_failure(f"billing event {row_id} could not be applied", exc)
        _park(db, row, type(exc).__name__)
        raise RetryLater("the event could not be applied") from exc
    if refusal:
        logger.warning("billing event %s refused: %s", row.id, refusal)
        _alert_refusal(db, row, event, refusal)
    db.commit()


def _log_failure(what: str, exc: BaseException) -> None:
    """Log a failure by exception TYPE and where it was raised, never its text (an email, an amount
    or a provider's words can be in it, and these lines are kept)."""
    frames = traceback.extract_tb(exc.__traceback__)
    where = f"{os.path.basename(frames[-1].filename)}:{frames[-1].lineno}" if frames else "?"
    logger.error("%s: %s at %s", what, type(exc).__name__, where)


def alert_grant_failed(
    db: Session, payment_id: int, first_seen: datetime, exc: BaseException, now: datetime
) -> None:
    """The provider says `payment_id` is paid and granting it failed (the step is already undone).
    Tell the admins ONCE, but only when the failure is persistent: `first_seen` (the moment the
    system first had a reason to settle it: the event's `received_at`, or the payment's
    `created_at` when no event is involved) is at least `GRANT_ALERT_AFTER` old. Called only after
    the provider reported `successful`: a failed or pending payment, or a provider that could not
    be asked, never reaches here. The caller has rolled back; this commits only the alert."""
    if now - first_seen < GRANT_ALERT_AFTER:
        return
    notify.tell_grant_failed(db, payment_id, exc)


def _park(db: Session, row: BillingEvent, error: str) -> None:
    """Undo whatever the step did, and leave the event waiting with the reason."""
    db.rollback()
    row.error = error
    db.commit()


def _apply(
    db: Session, provider: PaymentProvider, row: BillingEvent, event: ProviderEvent, now
) -> str | None:
    """Apply the event inside the caller's transaction. Returns a refusal reason, or None."""
    if event.kind in ("refund", "dispute"):
        return _record_refund_notice(db, row, event, now)
    payment = db.scalars(
        select(Payment).where(Payment.reference == event.reference).with_for_update()
    ).one_or_none()
    refusal = _refuse_payment(payment, provider)
    if refusal is None and payment.status in OPEN_STATUSES:
        payment_id = payment.id
        tx = provider.fetch_transaction(event.provider_tx_id)
        try:
            refusal = _malformed(tx) or _decide(db, payment, event, tx, now)
        except RetryLater:
            raise
        except Exception as exc:
            # Only a `charge_succeeded` event whose transaction the provider reports `successful`
            # reaches the grants (`_malformed` returned nothing, so `tx` is a usable report).
            if event.kind == "charge_succeeded" and tx.status == "successful":
                raise _PaidButNotGranted(payment_id) from exc
            raise
    elif refusal is None and event.kind == "charge_succeeded":
        _check_second_payment(db, provider, payment, event)
    row.processed_at = now
    row.error = refusal
    return refusal


def _record_refund_notice(db: Session, row: BillingEvent, event: ProviderEvent, now) -> None:
    """A refund or a dispute notice: marked decided (it is stored in the inbox, which is the
    record). Refunds are started by an admin and their state is read from the provider's own
    dashboard; a dispute (a chargeback: a bank pulling a payment back) tells the admins."""
    row.processed_at = now
    row.error = f"recorded: a {event.kind} notice"
    if event.kind == "refund" and event.refund_failed:
        # The person was told "Your payment was refunded", their plan ended and their card was
        # deleted when the provider accepted the refund. A refund that then FAILS pays nobody:
        # a person must pay it by hand. Nothing else changes here.
        payment = db.scalars(select(Payment).where(Payment.reference == event.reference)).first()
        about = f"payment {payment.id}" if payment is not None else "a payment we cannot match"
        notify.tell_admins(
            db,
            f"admin.billing_refund_failed:{row.id}",
            "A refund failed at the provider",
            f"{row.provider} reports that the refund of {about} FAILED, after Beyric recorded it "
            "as accepted: the person was told they were refunded, their plan ended, and they have "
            "not been paid. Look at the refund in the provider's dashboard and pay it by hand "
            "there (do not start another refund in Beyric: the payment already shows refunded).",
        )
    if event.kind == "dispute":
        payment = db.scalars(select(Payment).where(Payment.reference == event.reference)).first()
        about = f"payment {payment.id}" if payment is not None else "a payment we cannot match"
        notify.tell_admins(
            db,
            f"admin.billing_dispute:{row.id}",
            "A payment was disputed",
            f"{row.provider} reports a dispute (a chargeback) on {about}. Look at it in the "
            "provider's dashboard and answer it there; refunds and disputes are not the same.",
        )
    return None


def _check_second_payment(
    db: Session, provider: PaymentProvider, payment: Payment, event: ProviderEvent
) -> None:
    """A `charge_succeeded` event for a payment that is already paid. The same transaction again
    (a retry, a late webhook after the sweeper settled it) is nothing. A DIFFERENT transaction id
    is possibly a second charge: ask the provider, and only if it reports that transaction
    `successful` under this reference tell the admins. Nothing is granted, nothing changes."""
    if event.provider_tx_id == payment.provider_tx_id:
        return
    tx = provider.fetch_transaction(event.provider_tx_id)  # an error parks the event and retries
    if (
        _malformed(tx) is None
        and tx.status == "successful"
        and tx.reference == payment.reference
        and tx.provider_tx_id != payment.provider_tx_id
    ):
        digest = hashlib.sha256(tx.provider_tx_id.encode()).hexdigest()[:12]
        notify.tell_admins(
            db,
            f"admin.billing_second_payment:{payment.id}:{digest}",
            "A payment was charged twice",
            f"Payment {payment.id} was already paid, and {provider.name} reports a second "
            f"successful charge under the same reference (provider transaction id "
            f"{tx.provider_tx_id}). Nothing was granted for it. Refund the second one in the "
            "provider's dashboard.",
        )


# What a refusal that is only a person's own failed payment starts with: no alert for those.
_QUIET_REFUSALS = ("not successful", "unknown reference", "the provider does not report")


def _alert_refusal(db: Session, row: BillingEvent, event: ProviderEvent, refusal: str) -> None:
    """A successful charge the rules refused (a wrong amount, currency or reference, a transaction
    another payment holds): money may have moved for something we will not give. A declined or
    unknown payment is not an alert."""
    if event.kind != "charge_succeeded" or refusal.startswith(_QUIET_REFUSALS):
        return
    notify.tell_admins(
        db,
        f"admin.billing_charge_refused:{row.id}",
        "A payment was refused by the settlement rules",
        f"{row.provider} reports a successful charge (event {row.id}) that our rules refused: "
        f"{refusal}. Money may have moved. Look at it in the provider's dashboard.",
    )


def _decide(
    db: Session, payment: Payment, event: ProviderEvent, tx: ProviderTransaction, now
) -> str | None:
    """Act on the provider's report for an open payment. Returns a refusal reason, or None."""
    if event.kind == "charge_succeeded":
        return _refuse_transaction(payment, tx) or _mark_paid(db, payment, tx, now)
    if tx.reference == payment.reference and tx.status == "failed":
        if payment.status == "pending":
            payment.status = "failed"
        return None
    return "the provider does not report this payment as failed"


def _refuse_payment(payment: Payment | None, provider: PaymentProvider) -> str | None:
    if payment is None:
        return "unknown reference: we made no such payment"
    if payment.provider != provider.name:
        return f"wrong provider: the payment is with {payment.provider}"
    return None


def _malformed(tx: object) -> str | None:
    """Why the provider's answer is not a usable report, or None. Checked before any comparison, so
    a text amount or a missing field is a recorded refusal, never an exception that makes the
    provider resend the same event for ever."""
    if not isinstance(tx, ProviderTransaction):
        return "malformed provider report: not a transaction"
    if not isinstance(tx.provider_tx_id, str) or not 0 < len(tx.provider_tx_id) <= 128:
        return "malformed provider report: no usable transaction id"
    if not isinstance(tx.reference, str):
        return "malformed provider report: reference is not text"
    if type(tx.amount_minor) is not int:
        return "malformed provider report: amount is not a whole number of minor units"
    if tx.currency not in BILLING_CURRENCIES:
        return "malformed provider report: unknown currency"
    if tx.status not in TRANSACTION_STATUSES:
        return "malformed provider report: unknown status"
    return None


def _refuse_transaction(payment: Payment, tx: ProviderTransaction) -> str | None:
    """Why the provider's own report does not match what we asked for, or None when it does."""
    if tx.status == "pending":
        raise RetryLater("the provider still reports the payment as pending")
    if tx.status != "successful":
        return f"not successful: the provider reports {tx.status}"
    if tx.reference != payment.reference:
        return "reference mismatch: the provider reports a different payment"
    if tx.currency != payment.currency:
        return f"currency mismatch: asked {payment.currency}, provider reports {tx.currency}"
    if tx.amount_minor < payment.amount_minor:
        return (
            f"amount too low: asked {payment.amount_minor}, provider reports {tx.amount_minor} "
            f"{payment.currency}"
        )
    return None


def _mark_paid(db: Session, payment: Payment, tx: ProviderTransaction, now) -> str | None:
    """The one place a payment becomes `succeeded`; runs the grant handlers once."""
    try:
        # The assignments are INSIDE the savepoint: `begin_nested` flushes pending changes before
        # it opens, so a unique violation on the provider transaction id raised from a flush
        # outside it would poison the whole transaction (stage S6 found this: a 500 instead of
        # the refusal below).
        with db.begin_nested():
            payment.status = "succeeded"
            payment.provider_tx_id = tx.provider_tx_id
            payment.paid_at = now
            payment.card_country = normalize_card_country(tx.card_country)
            db.flush()
    except IntegrityError:  # another payment already carries this provider transaction id
        db.refresh(payment)
        return "transaction already used by another payment"
    # A note for a human look, never a rule: it cannot raise and cannot stop the grants below.
    flag_country_mismatch(db, payment)
    for transaction_handler in TRANSACTION_HANDLERS:
        transaction_handler(db, payment, tx)
    for handler in GRANT_HANDLERS:
        handler(db, payment)
    return None


def settle_transaction(
    db: Session, provider: PaymentProvider, payment_id: int, tx: ProviderTransaction, now
) -> str | None:
    """Decide one payment from a transaction the provider reported for it, with the same rules a
    webhook gets (the module note), for callers that did not come from a webhook: the sweeper that
    looks a payment up by its reference, and a saved-card renewal. `tx` must be a report the
    provider itself just gave about this payment; what a webhook or a charge call CLAIMED is never
    passed here. Locks the payment row, applies the rules, and flushes; the caller commits (or
    rolls back after an exception). Returns a refusal reason, or None: None means the payment is
    `succeeded` (now or already), still open without a verdict (`pending`, `failed`), or a failed
    report was recorded. Raises `RetryLater` while the provider still says `pending`."""
    payment = db.scalars(select(Payment).where(Payment.id == payment_id).with_for_update()).one()
    refusal = _refuse_payment(payment, provider)
    if refusal is None and payment.status in OPEN_STATUSES:
        refusal = _malformed(tx)
        if refusal is None and tx.reference != payment.reference:
            refusal = "reference mismatch: the provider reports a different payment"
        if refusal is None and tx.status == "failed":
            payment.status = "failed"
        elif refusal is None:
            refusal = _refuse_transaction(payment, tx) or _mark_paid(db, payment, tx, now)
    if refusal:
        logger.warning("payment %s refused: %s", payment.id, refusal)
    return refusal
