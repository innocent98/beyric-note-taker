"""Invoices: one per payment that was used (billing spec sections 6 and 7, stage S9).

**When.** `on_payment_succeeded` is a grant handler: `settle` runs it, once, inside the transaction
that marks a payment `succeeded`, right AFTER the subscription and pack handler (`subscriptions.
register` puts it second), so it sees what that handler did. It is best effort and **never stops
the hours a person paid for**: it runs in a savepoint, a failure is logged by type and the admins
are told, and the sweeper (`issue_missing`) issues it again on its next pass.

**Which payments.** A payment that succeeded but that nothing could use (`payments.
needs_refund_reason` is set: a second payment for a period already paid, money for a cancelled plan)
is NOT invoiced: no supply happened, and a person refunds it in full. Every other succeeded payment
gets one invoice, exactly one (`invoices.payment_id` is unique).

**The number.** `BN-<year>-<six digits>`, sequential within the year (invoices made while
`BILLING_LIVE` is false are counted apart and stored as `BN-T-<year>-...`, so purchases with the
providers' test keys never use up the live numbers; that `T-` is internal and never shown, see
`invoice_number.shown`), the year in Lagos time (the seller's). It comes from `invoice_counters`
with one atomic upsert (`next_number`): the row lock lasts to the end of the issuing transaction,
so two payments settled at the same moment queue on it and never get the same number, and a
rollback gives its number back, so there are no gaps. The unique index on `invoices.number` is
the second line behind it.

**The money.** `total_minor` is what the person paid (the payment's amount). `BILLING_VAT_RATE_BP`
is the VAT that price INCLUDES (default 0: no VAT line): `vat = round_half_up(total * rate /
(10000 + rate))`, `subtotal = total - vat`, so the parts always add up to the total. Added-on VAT is
not built (spec section 6: it would change what every provider is asked to charge, and the
accountant has not answered). The rate is stored on the invoice, so a later change of the setting
never changes an old one.

**Snapshots.** The seller (from settings: nothing here is invented, an empty registration or tax
number is simply left off) and the buyer (from the billing profile, else the account) are copied
onto the invoice, with a one-line description of what was bought, so an invoice never changes.
"""

import logging
from datetime import datetime

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app import mail
from app.billing import notify
from app.billing.invoice_pdf import SELLER_ZONE
from app.config import Settings, get_settings
from app.models import (
    BillingProfile,
    HourGrant,
    Invoice,
    Payment,
    Plan,
    PlanPrice,
    Product,
    Subscription,
    User,
)

logger = logging.getLogger(__name__)

# The seller's year is read in Lagos time (`invoice_pdf.SELLER_ZONE`): a payment at 23:30 UTC on
# 31 December is already the next year's first invoice in Nigeria.
RECEIPT_TEMPLATE = "billing_receipt"
BATCH = 20


def vat_split(total_minor: int, rate_bp: int) -> tuple[int, int]:
    """`(subtotal, vat)` of a price that INCLUDES VAT at `rate_bp` basis points (750 = 7.5%), in
    whole minor units, with `subtotal + vat == total_minor`. The VAT is rounded half up in integers
    (no floats): `total * rate / (10000 + rate)`. A rate of 0 gives no VAT."""
    if type(total_minor) is not int or total_minor <= 0:
        raise ValueError("the total is a positive whole number of minor units")
    if type(rate_bp) is not int or not 0 <= rate_bp <= 10000:
        raise ValueError("the VAT rate is 0 to 10000 basis points")
    divisor = 10000 + rate_bp
    vat = (total_minor * rate_bp * 2 + divisor) // (divisor * 2)
    return total_minor - vat, vat


def invoice_year(at: datetime) -> int:
    return at.astimezone(SELLER_ZONE).year


def number_text(year: int, n: int, *, test: bool = False) -> str:
    """The stored number: `BN-2026-000123`; internal series (`test`): `BN-T-2026-000123`."""
    return f"BN-{'T-' if test else ''}{year}-{n:06d}"


def next_number(db: Session, year: int, *, test: bool = False) -> str:
    """The next stored invoice number of `year` in the live series, or in the internal series
    (`test`: invoices made while `BILLING_LIVE` is false, `BN-T-...`, counted under the negative
    year) so those never use up the live numbers: the first live invoice is `BN-<year>-000001`.
    The `T-` is never shown to anyone (`invoice_number.shown`). One atomic upsert: the row
    stays locked until the caller commits (or rolls back and gives the number back), so concurrent
    payments queue here."""
    n = db.execute(
        text(
            "insert into invoice_counters (year, last_number) values (:year, 1) "
            "on conflict (year) do update set last_number = invoice_counters.last_number + 1 "
            "returning last_number"
        ),
        {"year": -year if test else year},
    ).scalar_one()
    return number_text(year, n, test=test)


def seller_snapshot(settings: Settings) -> dict:
    """Who sells, from settings. Empty values are kept as empty strings and left off the invoice."""
    return {
        "name": settings.billing_seller_name.strip(),
        "address": settings.billing_seller_address.strip(),
        "registration_no": settings.billing_seller_registration_no.strip(),
        "tax_id": settings.billing_seller_tax_id.strip(),
        "email": settings.billing_seller_email.strip(),
    }


def buyer_snapshot(db: Session, user: User | None) -> dict:
    """Who pays: the billing profile's fields, falling back to the account's name and email."""
    if user is None:
        return {"name": "", "address": "", "country": "", "tax_id": "", "email": ""}
    profile = db.scalars(select(BillingProfile).where(BillingProfile.user_id == user.id)).first()
    return {
        "name": ((profile.name_or_company if profile else None) or user.name or "").strip(),
        "address": ((profile.address if profile else None) or "").strip(),
        "country": ((profile.billing_country if profile else None) or "").strip(),
        "tax_id": ((profile.tax_id if profile else None) or "").strip(),
        "email": ((profile.billing_email if profile else None) or user.email or "").strip(),
    }


def _hours(seconds: int) -> str:
    return f"{seconds / 3600:.1f}".removesuffix(".0")


def _line(db: Session, payment: Payment) -> dict:
    """One line saying what was bought, from the rows the grant handler just wrote."""
    sub = db.get(Subscription, payment.subscription_id) if payment.subscription_id else None
    grant = db.scalars(select(HourGrant).where(HourGrant.source_payment_id == payment.id)).first()
    price = db.get(PlanPrice, payment.plan_price_id) if payment.plan_price_id else None
    plan = db.get(Plan, price.plan_code) if price else None
    if payment.kind == "pack":
        product = db.get(Product, payment.product_id) if payment.product_id else None
        seconds = grant.seconds if grant else (product.seconds if product else 0)
        return {
            "kind": "pack",
            "description": product.name if product else "Extra hours",
            "seconds": seconds,
            "period_start": grant.starts_at.isoformat() if grant else None,
            "period_end": grant.expires_at.isoformat() if grant and grant.expires_at else None,
        }
    name = plan.name if plan else "Plan"
    if payment.kind == "upgrade":
        seconds = grant.seconds if grant else 0
        return {
            "kind": "upgrade",
            "description": f"{name}: change of plan, {_hours(seconds)} extra hours for the "
            "rest of the period",
            "seconds": seconds,
            "period_start": payment.paid_at.isoformat() if payment.paid_at else None,
            "period_end": sub.current_period_end.isoformat() if sub else None,
        }
    seconds = grant.seconds if grant else (plan.monthly_seconds if plan else 0)
    return {
        "kind": payment.kind,
        "description": f"Monthly renewal: {name}" if payment.kind == "renewal" else name,
        "seconds": seconds,
        "period_start": sub.current_period_start.isoformat() if sub else None,
        "period_end": sub.current_period_end.isoformat() if sub else None,
    }


def issue(
    db: Session, payment: Payment, now: datetime, settings: Settings | None = None
) -> Invoice | None:
    """The invoice of a succeeded payment, made now; the existing one when there is one. None for
    a payment that was not used (it needs a refund, not an invoice). Writes in the caller's
    transaction; the caller commits."""
    settings = settings or get_settings()
    existing = db.scalars(select(Invoice).where(Invoice.payment_id == payment.id)).first()
    if existing is not None:
        return existing
    if payment.status != "succeeded" or payment.needs_refund_reason is not None:
        return None
    paid_at = payment.paid_at or now
    subtotal, vat = vat_split(payment.amount_minor, settings.billing_vat_rate_bp)
    payer = db.get(User, payment.payer_user_id) if payment.payer_user_id else None
    invoice = Invoice(
        number=next_number(db, invoice_year(paid_at), test=not settings.billing_live),
        payment_id=payment.id,
        payer_user_id=payment.payer_user_id,
        issued_at=paid_at,  # one date for the invoice: the number's year comes from it too
        currency=payment.currency,
        subtotal_minor=subtotal,
        vat_rate_bp=settings.billing_vat_rate_bp,
        vat_minor=vat,
        total_minor=payment.amount_minor,
        seller=seller_snapshot(settings),
        buyer=buyer_snapshot(db, payer),
        line=_line(db, payment),
        test_mode=not settings.billing_live,
    )
    db.add(invoice)
    db.flush()
    return invoice


def queue_receipt(db: Session, invoice: Invoice) -> None:
    """Queue the receipt mail (ids only; the PDF is built when the mail is sent). A receipt is the
    record of a payment the person made: an unsubscribe does not stop it (a bounce or complaint
    still does). Best effort, in its own savepoint."""
    address = (invoice.buyer or {}).get("email", "")
    if not address or not mail.is_configured(get_settings()):
        return
    if mail.suppression_reason(db, address) in mail.HARD_REASONS:
        return
    try:
        with db.begin_nested():
            mail.enqueue(db, address, RECEIPT_TEMPLATE, {"invoice_id": invoice.id})
    except Exception as exc:
        logger.warning("receipt mail not queued (%s)", type(exc).__name__)


def on_payment_succeeded(db: Session, payment: Payment) -> None:
    """The grant handler: invoice the payment and queue its receipt. Never raises: what this does
    must not stop the hours that were paid for (the sweeper tries again)."""
    try:
        with db.begin_nested():
            invoice = issue(db, payment, payment.paid_at)  # `_mark_paid` set it just before
            if invoice is not None:
                queue_receipt(db, invoice)
    except Exception as exc:
        logger.error("invoice for payment %s not issued: %s", payment.id, type(exc).__name__)
        notify.tell_admins(
            db,
            f"admin.billing_invoice_failed:{payment.id}",
            "An invoice could not be issued",
            f"Payment {payment.id} succeeded and its hours were granted, but its invoice could "
            f"not be made ({type(exc).__name__}). It will be tried again.",
        )


def issue_missing(session: Session, now: datetime) -> int:
    """The sweeper's pass: invoice every succeeded, used payment that has none (a handler that
    failed, a payment settled before this stage). Oldest first, `BATCH` a pass, each in its own
    transaction. Returns how many were issued."""
    rows = session.scalars(
        select(Payment.id)
        .where(
            Payment.status == "succeeded",
            Payment.needs_refund_reason.is_(None),
            ~select(Invoice.id).where(Invoice.payment_id == Payment.id).exists(),
        )
        .order_by(Payment.id)
        .limit(BATCH)
    ).all()
    issued = 0
    for payment_id in rows:
        try:
            payment = session.scalars(
                select(Payment).where(Payment.id == payment_id).with_for_update(skip_locked=True)
            ).first()
            if payment is None:
                session.rollback()
                continue
            invoice = issue(session, payment, now)
            if invoice is not None:
                queue_receipt(session, invoice)
                issued += 1
            session.commit()
        except Exception as exc:
            session.rollback()
            logger.error("invoice for payment %s not issued: %s", payment_id, type(exc).__name__)
    return issued


def register() -> None:
    """Put the handler where `settle` runs it, after the subscription handler. Idempotent."""
    if on_payment_succeeded not in _handlers():
        _handlers().append(on_payment_succeeded)


def _handlers() -> list:
    from app.billing import settle

    return settle.GRANT_HANDLERS
