"""The Paystack TEST-mode round trip, by hand (billing stage S6 runbook).

Run it where the app's `.env` is, with Paystack TEST keys set and `BILLING_LIVE` unset or false
(runbook: `docs/sop/2026-11-17-billing-paystack-adapter.md`, "Runbook"):

    docker compose exec -T web python scripts/paystack_test_run.py checkout EMAIL [--naira 50]
    docker compose exec -T web python scripts/paystack_test_run.py status REFERENCE
    docker compose exec -T web python scripts/paystack_test_run.py verify REFERENCE

`checkout` makes a pending payment for an existing account, asks Paystack for a hosted checkout and
prints the link and our reference. Open the link, pay with a Paystack test card, and Paystack calls
the webhook. `status` prints what the app wrote (the payment and its webhook events). `verify` asks
Paystack about the payment and prints what it says (read-only: it changes nothing here).

It sends nothing without a TEST secret key: with no key, or a live key, or `BILLING_LIVE=true`, it
stops before any call (and before it touches the database). A failed run leaves no payment row.
It prints no key, no token and no email. No hours are granted (the grant handlers arrive with
stage S8): a paid payment only becomes `succeeded`.
"""

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import func, select  # noqa: E402
from sqlalchemy.orm import Session  # noqa: E402

from app.billing.payments import new_payment  # noqa: E402
from app.billing.paystack import PaystackProvider  # noqa: E402
from app.billing.types import ProviderError  # noqa: E402
from app.models import BillingEvent, Payment, User  # noqa: E402

MIN_NAIRA = 50  # Paystack's documented minimum for NGN (docs: "Supported currency")


def make_checkout(db: Session, provider: PaystackProvider, email: str, naira: int, return_url: str):
    """A pending payment of `naira` for the account `email`, committed BEFORE the payer is sent to
    Paystack (a fast webhook must find it). Returns (payment, checkout)."""
    if not isinstance(naira, int) or naira < MIN_NAIRA:
        raise SystemExit(f"the amount must be a whole number of naira, at least {MIN_NAIRA}")
    payer = db.scalar(select(User).where(User.email == email.strip().lower()))
    if payer is None:
        raise SystemExit("no account has that email")
    payment = new_payment(
        db,
        payer=payer,
        provider="paystack",
        kind="plan",
        currency="NGN",
        amount_minor=naira * 100,
    )
    # The payment is flushed (it has its reference) but NOT committed until Paystack has given a
    # checkout: a failed run rolls it back and leaves no orphan row. The payer cannot pay before the
    # link is printed, which is after the commit, so a webhook cannot arrive first.
    try:
        checkout = provider.start_checkout(payment, payer, return_url)
    except ProviderError as exc:
        db.rollback()
        raise SystemExit(f"Paystack was not asked or did not give a checkout: {exc}") from None
    db.commit()
    return payment, checkout


def status_report(db: Session, reference: str) -> dict:
    """What the app wrote about one payment: numbers and words, no email, no token."""
    payment = db.scalar(select(Payment).where(Payment.reference == reference))
    if payment is None:
        raise SystemExit("no payment has that reference")
    events = db.scalars(
        select(BillingEvent)
        .where(BillingEvent.provider == "paystack")
        .where(BillingEvent.payload["data"]["reference"].astext == reference)
        .order_by(BillingEvent.id)
    )
    return {
        "payment": {
            "reference": payment.reference,
            "status": payment.status,
            "amount_kobo": payment.amount_minor,
            "currency": payment.currency,
            "provider_tx_id": payment.provider_tx_id,
            "card_country": payment.card_country,
            "country_mismatch": payment.country_mismatch,
        },
        "events": [
            {
                "dedupe_key": e.dedupe_key,
                "type": e.type,
                "decided": e.processed_at is not None,
                "error": e.error,
            }
            for e in events
        ],
        "events_total_for_paystack": db.scalar(
            select(func.count())
            .select_from(BillingEvent)
            .where(BillingEvent.provider == "paystack")
        ),
    }


def verify_report(provider: PaystackProvider, reference: str) -> dict:
    tx = provider.verify_reference(reference)
    return {
        "reference": tx.reference,
        "status": tx.status,
        "amount_kobo": tx.amount_minor,
        "currency": tx.currency,
        "card_country": tx.card_country,
        "card_can_be_charged_again": tx.card_token is not None,
        "provider_tx_id": tx.provider_tx_id,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)
    checkout = sub.add_parser("checkout")
    checkout.add_argument("email")
    checkout.add_argument("--naira", type=int, default=MIN_NAIRA)
    sub.add_parser("status").add_argument("reference")
    sub.add_parser("verify").add_argument("reference")
    args = parser.parse_args(argv)

    from app.billing.paystack import from_settings
    from app.config import get_settings

    settings = get_settings()
    if settings.billing_live:
        raise SystemExit("test mode only: BILLING_LIVE is true, so this script does nothing")
    from app.db import SessionLocal

    provider = from_settings(settings)
    with SessionLocal() as db:
        if args.command == "checkout":
            payment, result = make_checkout(
                db, provider, args.email, args.naira, settings.public_base_url
            )
            print(f"reference: {payment.reference}")
            print(f"amount:    {payment.amount_minor} kobo (NGN {payment.amount_minor // 100})")
            print(f"open this to pay: {result.url}")
        elif args.command == "status":
            report = status_report(db, args.reference)
            for section, value in report.items():
                print(f"{section}: {value}")
        else:
            try:
                report = verify_report(provider, args.reference)
            except ProviderError as exc:
                raise SystemExit(f"Paystack did not answer usefully: {exc}") from None
            for key, value in report.items():
                print(f"{key}: {value}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
