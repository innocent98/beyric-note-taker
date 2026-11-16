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

Anything else is refused: the payment stays as it was, and the event is marked decided with the
reason in `billing_events.error`. A refusal never changes a payment.

**The grant point.** The first (and only) time a payment moves to `succeeded`, every function in
`GRANT_HANDLERS` runs inside the same database transaction, with the payment row locked. The hour
grants of the later stages register there. If a handler raises, the whole step is undone, the event
stays waiting and the webhook answers 503 so the provider sends it again. A payment already
`succeeded` runs no handler, so the same event twice, or two different events for one payment, grant
once. The grants' own unique key on (kind, source payment id) is the second line behind this.

A `charge_failed` event moves a `pending` payment to `failed` only if the provider, asked, says it
failed; a `failed` payment can still succeed later (a payer retries the same checkout). `refund` and
`dispute` events are stored and left waiting for the refund stage (S9).
"""

import logging
from collections.abc import Callable

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.billing.provider import PaymentProvider
from app.billing.types import ProviderEvent, ProviderTransaction, RetryLater
from app.models import BillingEvent, Payment

logger = logging.getLogger(__name__)

# Called as `handler(db, payment)` once, when a payment first becomes `succeeded`. Empty in S4.
GrantHandler = Callable[[Session, Payment], None]
GRANT_HANDLERS: list[GrantHandler] = []

OPEN_STATUSES = ("pending", "failed")  # a payment in one of these can still be paid


def process(
    db: Session, provider: PaymentProvider, row: BillingEvent, event: ProviderEvent, now
) -> None:
    """Decide one stored event. Marks `row` decided, or raises `RetryLater` leaving it waiting."""
    try:
        refusal = _apply(db, provider, row, event, now)
    except RetryLater as exc:
        _park(db, row, str(exc))
        raise
    except Exception as exc:  # a provider failure, or a handler that failed: nothing is kept
        logger.exception("billing event %s could not be applied", row.id)
        _park(db, row, f"{type(exc).__name__}: {str(exc)[:200]}")
        raise RetryLater("the event could not be applied") from exc
    if refusal:
        logger.warning("billing event %s refused: %s", row.id, refusal)
    db.commit()


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
        row.error = "waiting for the refund stage (S9)"
        return None
    payment = db.scalars(
        select(Payment).where(Payment.reference == event.reference).with_for_update()
    ).one_or_none()
    refusal = _refuse_payment(payment, provider)
    if refusal is None and payment.status in OPEN_STATUSES:
        tx = provider.fetch_transaction(event.provider_tx_id)
        if event.kind == "charge_succeeded":
            refusal = _refuse_transaction(payment, tx)
            if refusal is None:
                refusal = _mark_paid(db, payment, tx, now)
        elif tx.reference == payment.reference and tx.status == "failed":
            if payment.status == "pending":
                payment.status = "failed"
        else:
            refusal = "the provider does not report this payment as failed"
    row.processed_at = now
    row.error = refusal
    return refusal


def _refuse_payment(payment: Payment | None, provider: PaymentProvider) -> str | None:
    if payment is None:
        return "unknown reference: we made no such payment"
    if payment.provider != provider.name:
        return f"wrong provider: the payment is with {payment.provider}"
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
    payment.status = "succeeded"
    payment.provider_tx_id = tx.provider_tx_id
    payment.paid_at = now
    payment.card_country = tx.card_country
    try:
        with db.begin_nested():
            db.flush()
    except IntegrityError:  # another payment already carries this provider transaction id
        db.refresh(payment)
        return "transaction already used by another payment"
    for handler in GRANT_HANDLERS:
        handler(db, payment)
    return None
