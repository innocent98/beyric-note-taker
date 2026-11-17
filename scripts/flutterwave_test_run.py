"""The Flutterwave TEST-MODE run (billing stage S7): make a test payment, send yourself to the
hosted checkout, then look at what the app and Flutterwave recorded. Steps and what each proves:
`docs/sop/2026-11-17-billing-flutterwave-adapter.md`, "The test-mode run".

    python scripts/flutterwave_test_run.py checkout --email you@example.com [--amount-cents 1800]
    python scripts/flutterwave_test_run.py status --reference bn-...
    python scripts/flutterwave_test_run.py raw --transaction-id 123456 [--show-token]
    python scripts/flutterwave_test_run.py charge --email you@example.com --token flw-t... \\
        [--amount-cents 1800]
    python scripts/flutterwave_test_run.py refund --transaction-id 123456 --amount-cents 500

**It refuses to run unless BILLING_LIVE is true AND the secret key is a Flutterwave TEST key** (its
text contains `_TEST`, authentication page), so a live key can never start a payment from here.

- `checkout`: finds the account by email (it must exist), makes a `pending` USD payment row
  (`payments.new_payment`), asks Flutterwave for a hosted checkout link and prints the link and the
  reference. Pay on that page with a test card (SOP). The payment is settled by the running app
  when Flutterwave's webhook arrives; this command never marks anything paid.
- `status`: prints the payment (status, transaction id, card country, mismatch flag) and its webhook
  events (type, decided or waiting, the refusal reason). No payload, email or token is printed.
- `raw`: asks Flutterwave to verify one transaction and prints only these fields: status, currency,
  amount, charged_amount, amount_settled, app_fee, merchant_fee, payment_type, card country. This is
  the evidence for "dollar pricing and settlement" (spec 5.6): what currency Flutterwave charged and
  settled, and at what amounts. `--show-token` also prints the card token (a TEST token), which
  `charge` needs; the app saves no token in this stage.
- `charge`: makes a `pending` USD renewal payment and charges the saved TEST card token under the
  account's email (Flutterwave ties a token to the email it was saved under). Expect `pending`
  (3D Secure) unless Flutterwave support enabled NOAUTH on the account.
- `refund`: refunds part of a test transaction and prints the refund id.
"""

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import select  # noqa: E402

from app.billing.flutterwave import FlutterwaveProvider  # noqa: E402
from app.billing.payments import new_payment  # noqa: E402
from app.billing.types import ProviderError  # noqa: E402
from app.config import Settings, get_settings  # noqa: E402
from app.models import BillingEvent, Payment, User  # noqa: E402

RAW_FIELDS = (
    "status",
    "currency",
    "amount",
    "charged_amount",
    "amount_settled",
    "app_fee",
    "merchant_fee",
    "payment_type",
)


def guard(settings: Settings) -> None:
    """Stop unless this is a test-mode run: calls on, and a TEST key."""
    if not settings.billing_live:
        raise SystemExit("Refusing: BILLING_LIVE is not true, so no call may leave the process.")
    if "_TEST" not in settings.flutterwave_secret_key:
        raise SystemExit("Refusing: FLUTTERWAVE_SECRET_KEY is not a Flutterwave TEST key.")


def checkout(db, provider: FlutterwaveProvider, email: str, amount_cents: int, return_url: str):
    user = db.scalar(select(User).where(User.email == email.strip().lower()))
    if user is None:
        raise SystemExit("No account has that email: create it in the app first.")
    payment = new_payment(
        db,
        payer=user,
        provider="flutterwave",
        kind="plan",
        currency="USD",
        amount_minor=amount_cents,
    )
    db.commit()  # the row must exist before the payer can reach the provider (S4 follow-up)
    link = provider.start_checkout(payment, user, return_url)
    print(f"reference: {link.reference}")
    print(f"pay here (a TEST card, see the SOP): {link.url}")
    return payment


def status(db, reference: str) -> None:
    payment = db.scalar(select(Payment).where(Payment.reference == reference))
    if payment is None:
        raise SystemExit("No payment has that reference.")
    print(
        f"payment: status={payment.status} tx={payment.provider_tx_id} "
        f"asked={payment.amount_minor} {payment.currency} card_country={payment.card_country} "
        f"mismatch={payment.country_mismatch} paid_at={payment.paid_at}"
    )
    events = db.scalars(
        select(BillingEvent)
        .where(BillingEvent.provider == "flutterwave")
        .order_by(BillingEvent.id.desc())
        .limit(50)
    )
    # Events carry no payment column; show the ones whose stored body names this reference.
    for event in events:
        if reference in str(event.payload):
            state = "decided" if event.processed_at else "WAITING"
            print(f"event: {event.dedupe_key} {state} error={event.error}")


def raw(provider: FlutterwaveProvider, transaction_id: str, show_token: bool = False) -> None:
    data = provider.read_verify_data(transaction_id)
    card = data.get("card") if isinstance(data.get("card"), dict) else {}
    for name in RAW_FIELDS:
        print(f"{name}: {data.get(name)}")
    print(f"card.country: {card.get('country')}")
    if show_token:
        print(f"card.token: {card.get('token')}")


def charge(db, provider: FlutterwaveProvider, email: str, token: str, amount_cents: int) -> None:
    user = db.scalar(select(User).where(User.email == email.strip().lower()))
    if user is None:
        raise SystemExit("No account has that email: create it in the app first.")
    payment = new_payment(
        db,
        payer=user,
        provider="flutterwave",
        kind="renewal",
        currency="USD",
        amount_minor=amount_cents,
    )
    db.commit()
    tx = provider.charge_saved_method(
        token, amount_cents, "USD", payment.reference, payer_email=user.email
    )
    print(f"reference: {payment.reference}")
    print(f"flutterwave says: status={tx.status} amount={tx.amount_minor} {tx.currency}")
    print("pending means the payer must complete 3D Secure: that is the pay-link case.")


def main(argv=None) -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = parser.add_subparsers(dest="command", required=True)
    one = sub.add_parser("checkout")
    one.add_argument("--email", required=True)
    one.add_argument("--amount-cents", type=int, default=1800)
    one.add_argument("--return-url", default="http://localhost:3000/billing/return")
    two = sub.add_parser("status")
    two.add_argument("--reference", required=True)
    three = sub.add_parser("raw")
    three.add_argument("--transaction-id", required=True)
    three.add_argument("--show-token", action="store_true")
    four = sub.add_parser("charge")
    four.add_argument("--email", required=True)
    four.add_argument("--token", required=True)
    four.add_argument("--amount-cents", type=int, default=1800)
    five = sub.add_parser("refund")
    five.add_argument("--transaction-id", required=True)
    five.add_argument("--amount-cents", type=int, required=True)
    args = parser.parse_args(argv)

    settings = get_settings()
    guard(settings)
    provider = FlutterwaveProvider(settings)
    from app.db import SessionLocal

    try:
        with SessionLocal() as db:
            if args.command == "checkout":
                checkout(db, provider, args.email, args.amount_cents, args.return_url)
            elif args.command == "status":
                status(db, args.reference)
            elif args.command == "raw":
                raw(provider, args.transaction_id, args.show_token)
            elif args.command == "charge":
                charge(db, provider, args.email, args.token, args.amount_cents)
            else:
                print(f"refund id: {provider.refund(args.transaction_id, args.amount_cents)}")
    except ProviderError as exc:
        raise SystemExit(f"Flutterwave call failed: {exc}") from None


if __name__ == "__main__":
    main()
