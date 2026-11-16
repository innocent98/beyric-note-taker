"""Making a payment row, before anyone is sent to a provider (billing spec section 5.4)."""

import secrets

from sqlalchemy.orm import Session

from app.models import Payment, User


def new_payment(
    db: Session,
    *,
    payer: User,
    provider: str,
    kind: str,
    currency: str,
    amount_minor: int,
    subscription_id: int | None = None,
) -> Payment:
    """A `pending` payment with its reference, `bn-<payment id>-<random>`, flushed but not
    committed (the caller commits). The reference is ours: every webhook is matched to a row we
    made, so a webhook for a payment we never asked for finds nothing."""
    payment = Payment(
        payer_user_id=payer.id,
        subscription_id=subscription_id,
        provider=provider,
        kind=kind,
        currency=currency,
        amount_minor=amount_minor,
        reference=f"bn-new-{secrets.token_hex(8)}",  # replaced below, once the id exists
    )
    db.add(payment)
    db.flush()
    payment.reference = f"bn-{payment.id}-{secrets.token_hex(8)}"
    db.flush()
    return payment
