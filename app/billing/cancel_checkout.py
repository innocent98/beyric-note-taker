"""Cancelling a checkout the person started and did not finish.

`POST /api/billing/payments/{reference}/cancel`.

A person who closes the provider's page without paying leaves a `pending` payment: Paystack reports
an abandoned page as `pending` and Flutterwave keeps it open, so the "you started a payment and have
not finished it" notice would stay for ever. This is how they put it away.

**The provider is asked first, always.** A person's click is never believed over the provider's own
word about OUR reference (`lookup_by_reference`), because the page may have been paid in another tab
a second ago:

- paid (`successful`): settled with the webhook's own rules (`settle.settle_transaction`), so the
  hours are granted and nothing is cancelled. `result` is `paid`.
- `failed`: the payment is marked `failed`, by the same rules. `result` is `failed`.
- still unpaid (`pending`: an abandoned or open page): the payment is marked `failed`. `result` is
  `cancelled`.
- no record under our reference (`NoRecord`: Flutterwave makes a transaction only when the payer
  tries to pay, so a page nobody opened answers "no transaction"): treated as unpaid, as above.
- nothing else (it cannot be asked, no provider is set, or its answer is unusable): 502
  `provider_error`, and **nothing changes**.
- paid, but our rules refuse it (a wrong amount, say): 409 `payment_in_progress`, an admin is told,
  nothing changes.
- paid, but granting the hours fails (a grant handler raises): the transaction is undone, 409
  `payment_in_progress`, an admin is told (`admin.billing_cancel_grant_failed:<id>`), nothing
  changes.

A payment that is already paid or refunded answers `paid` and changes nothing; asking again after a
cancel asks the provider again (a `failed` payment can still be paid, below).

**"Cancelled" is `failed`, not a new state.** No migration. A `failed` payment is still open to
settlement (`settle.OPEN_STATUSES`): if the page is paid later by mistake, or a payer was mid-way
through it, the provider's webhook finds the row, asks the provider again and grants the hours as it
would for a retried checkout. Nothing here can lose money the provider took. A payment whose
webhook never arrives is found by the sweeper (`renewals._sweep_payments`), which also asks about
recent `failed` plan and pack payments that have a page (`renewals.FAILED_LOOKBACK`) and settles one
the provider says is paid. The person is also told not to pay the old page.

Only a plan or a pack checkout can be cancelled here (a renewal belongs to the clock and an upgrade
to the plan change). The payment row is locked only to write (the order everywhere is the payment,
then the refund, then the subscription: this takes the payment and reads the refund), never across
the call to the provider. While a refund of the plan is being settled nothing about the plan's
payments moves (`subscriptions._refuse_while_refunding`).
"""

import logging
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.billing import notify, settle, subscriptions
from app.billing.provider import lookup_by_reference, provider_named
from app.billing.types import NoRecord, ProviderError, ProviderTransaction, RetryLater
from app.config import Settings
from app.models import Payment, Subscription, User

logger = logging.getLogger(__name__)

CANCELLABLE_KINDS = ("plan", "pack")
# A payment in one of these has been paid (and maybe refunded): there is nothing to cancel.
PAID_STATUSES = ("succeeded", "refunded", "partly_refunded")


@dataclass(frozen=True)
class Cancelled:
    result: str  # `paid`, `failed` or `cancelled`: see the module note
    payment: Payment


def _refuse(status: int, code: str, message: str) -> subscriptions.Refused:
    return subscriptions.Refused(status, code, message)


def _provider_error() -> subscriptions.Refused:
    return _refuse(
        502,
        "provider_error",
        "We could not check this payment with the payment provider just now. Nothing was "
        "changed. Try again in a moment.",
    )


def _refuse_while_refunding(db: Session, payment: Payment) -> None:
    """409 `refund_pending` while a refund of this payment's plan is being settled."""
    if payment.subscription_id is not None:
        plan_row = db.get(Subscription, payment.subscription_id)
        if plan_row is not None:
            subscriptions._refuse_while_refunding(db, plan_row)


def _locked(db: Session, payment_id: int) -> Payment:
    return db.scalars(
        select(Payment)
        .where(Payment.id == payment_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    ).one()


def cancel_checkout(db: Session, user: User, reference: str, settings: Settings, now) -> Cancelled:
    """Put away the caller's own unfinished plan or pack checkout, after asking the provider. The
    caller commits on a result and rolls back on a `subscriptions.Refused`. (Two exceptions in
    `_settle_reported`: its refusal path and its grant-failure path each commit only the admin alert
    they raised, then refuse.)"""
    payment = db.scalars(
        select(Payment).where(Payment.reference == reference, Payment.payer_user_id == user.id)
    ).one_or_none()
    if payment is None:  # someone else's reference is the same plain 404 as an unknown one
        raise _refuse(404, "not_found", "Not found.")
    if payment.kind not in CANCELLABLE_KINDS:
        raise _refuse(409, "cannot_cancel", "This payment cannot be cancelled here.")
    if payment.status in PAID_STATUSES:
        return Cancelled("paid", payment)
    _refuse_while_refunding(db, payment)
    payment_id, provider_name = payment.id, payment.provider
    adapter = provider_named(provider_name, settings)
    db.rollback()  # a read only so far: nothing is held while the provider is asked
    if adapter is None:
        raise _provider_error()
    try:
        tx = lookup_by_reference(adapter, reference)
    except NoRecord:
        # The provider is reachable and has nothing under our reference: nobody paid it. Safe to
        # close, because a `failed` payment is still settled if the page is paid later.
        return _close_unpaid(db, payment_id)
    except ProviderError:
        logger.warning("cancel of payment %s: the provider could not be asked", payment_id)
        raise _provider_error() from None
    # A report that is not about our payment, or not a usable report, is no answer at all.
    if settle._malformed(tx) is not None or tx.reference != reference:
        logger.warning("cancel of payment %s: the provider's report is unusable", payment_id)
        raise _provider_error()
    if tx.status == "pending":
        return _close_unpaid(db, payment_id)
    return _settle_reported(db, adapter, payment_id, tx, now)


def _close_unpaid(db: Session, payment_id: int) -> Cancelled:
    """The provider says the page is not paid: lock the payment and look again, since the webhook
    may have settled it while the provider was asked, and only then mark it `failed`."""
    payment = _locked(db, payment_id)
    if payment.status in PAID_STATUSES:
        return Cancelled("paid", payment)
    _refuse_while_refunding(db, payment)  # a refund may have started while the provider answered
    payment.status = "failed"
    db.flush()
    return Cancelled("cancelled", payment)


def _settle_reported(
    db: Session, adapter, payment_id: int, tx: ProviderTransaction, now
) -> Cancelled:
    """The provider reported the payment paid or failed: decide it with the webhook's own rules, so
    a paid page grants its hours and a wrong amount is refused and told to the admins."""
    try:
        refusal = settle.settle_transaction(db, adapter, payment_id, tx, now)
    except RetryLater:
        db.rollback()
        raise _refuse(
            409,
            "payment_in_progress",
            "We could not confirm your payment yet. We are looking into it.",
        ) from None
    except Exception as exc:  # a grant handler failed: the transaction is undone, nothing was kept
        db.rollback()
        logger.error("cancel of payment %s could not settle it: %s", payment_id, type(exc).__name__)
        if tx.status == "successful":
            _tell_grant_failed(db, payment_id, exc)
        raise _refuse(
            409,
            "payment_in_progress",
            "We could not confirm your payment yet. We are looking into it.",
        ) from None
    payment = db.get(Payment, payment_id)
    if refusal is not None and tx.status != "successful":
        db.rollback()  # a refused report of a failure: no answer worth acting on
        raise _provider_error()
    if refusal is not None:
        subscriptions._tell_admins(
            db,
            f"admin.billing_payment_refused:{payment_id}",
            "A payment was refused by the settlement rules",
            f"Payment {payment_id}: {refusal}. The provider reports it paid; look at it.",
        )
        db.commit()
        raise _refuse(
            409,
            "payment_in_progress",
            "We could not confirm your payment yet. We are looking into it.",
        )
    if payment.status in PAID_STATUSES:
        return Cancelled("paid", payment)
    return Cancelled("failed", payment)


def _tell_grant_failed(db: Session, payment_id: int, exc: Exception) -> None:
    """The provider reports the page paid and granting its hours failed: a person who paid may be
    left without hours, so the admins are told (once per payment). Ids and the exception TYPE only,
    never its text or anything the provider said. Its own transaction, after the rollback, and
    best effort: the person still gets the 409, never a 500 from the alert."""
    notify.tell_admins(
        db,
        notify.grant_failed_key(payment_id),
        "A paid payment could not be granted",
        f"The provider reports payment {payment_id} paid, but it could not be settled "
        f"({type(exc).__name__}). Look at it.",
    )
    try:
        db.commit()
    except Exception as commit_exc:
        logger.error(
            "admin alert for payment %s not saved: %s", payment_id, type(commit_exc).__name__
        )
        try:
            db.rollback()
        except Exception as rollback_exc:  # a dead connection: still the person's 409, not a 500
            logger.error(
                "rollback after the alert for payment %s failed: %s",
                payment_id,
                type(rollback_exc).__name__,
            )
