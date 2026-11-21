"""Drive the go-live cutover (`BILLING_LIVE_SINCE`) over real HTTP and save every exchange for the
FE guide (`docs/fe-integration-guide-billing.md`, "The go-live cutover").

    python scripts/capture_billing_cutover.py postgresql+psycopg://USER:PASS@localhost:PORT/postgres

Same setup as `scripts/capture_billing_invoices.py` (whose helpers it imports): real migrations, the
real app under uvicorn, real HTTP. The app runs with `BILLING_LIVE=true` and `BILLING_LIVE_SINCE`
set to one hour ago. One person pays for a plan BEFORE that moment (the payment's date is moved
back by SQL, as a payment made with test keys would be) and for a pack AFTER it. Files go to
`docs/fe-captures/billing-cutover/NN-name.json`.

**What is real:** the app, the guard, the routes, the settlement rules, the invoice numbers, every
status code and error body below.

**What is NOT:** no real payment provider (`app.billing.fake.FakeProvider` under the names
`flutterwave` and `paystack`): **not verified live**; with `BILLING_LIVE=true` and no provider keys
set the fake stands in for the live adapters; the "before the cutover" date of the first payment is
set by SQL; every person is a placeholder (`@example.test`).
"""

import shutil
import sys
from datetime import UTC, datetime, timedelta

import capture_billing_invoices as inv

subcap = inv.subcap
base = inv.base
OUT = base.ROOT / "docs" / "fe-captures" / "billing-cutover"
base.OUT = OUT
subcap.OUT = OUT

from sqlalchemy import create_engine, select, text  # noqa: E402
from sqlalchemy.orm import Session  # noqa: E402

from app.billing.invoice_number import shown as shown_number  # noqa: E402
from app.config import Settings  # noqa: E402
from app.main import create_app  # noqa: E402
from app.models import Invoice  # noqa: E402
from app.services import get_bot_provider  # noqa: E402

ADA, ROOT = inv.ADA, inv.ROOT


def main() -> None:
    if not sys.argv[1:]:
        sys.exit(__doc__)
    base.prepare_database()
    shutil.rmtree(OUT, ignore_errors=True)
    OUT.mkdir(parents=True)
    inv.use_vat(0)
    cutover = datetime.now(UTC) - timedelta(hours=1)
    cfg = Settings(
        _env_file=None,
        app_env="test",
        session_secret="capture-secret-" + "x" * 30,
        cookie_secure=False,
        database_url=base.SCRATCH_URL,
        public_base_url="https://notes.example.test",
        token_enc_key=base.KEY,
        billing_enabled=True,
        billing_allowed_emails="*",
        billing_live=True,
        billing_live_since=cutover.isoformat(),
    )
    app = create_app(cfg)
    app.dependency_overrides[get_bot_provider] = lambda: base.FakeBot()
    server, on = base.start_server(app)
    engine = create_engine(base.SCRATCH_URL)
    try:
        inv.person(engine, ADA)
        inv.person(engine, ROOT, admin=True)
        test_payment = inv.buy_quietly(engine, on, "ada")
        with Session(engine) as session:  # made BEFORE the cutover: a test-era payment
            session.execute(
                text("update payments set created_at = :t where id = :i"),
                {"t": cutover - timedelta(days=1), "i": test_payment.id},
            )
            session.commit()
        live_payment = inv.buy_pack_quietly(engine, on, "ada")  # made AFTER it
        with Session(engine) as session:
            numbers = {
                payment_id: shown_number(number)
                for payment_id, number in session.execute(
                    select(Invoice.payment_id, Invoice.number)
                )
            }
        test_number, live_number = numbers[test_payment.id], numbers[live_payment.id]
        base.note_file(
            "setup",
            "What was set up: BILLING_LIVE=true, BILLING_LIVE_SINCE one hour ago (a placeholder "
            "moment). Ada paid for a plan BEFORE it (the payment's date moved back by SQL) and for "
            "a pack AFTER it. Both payments have an invoice in the database.",
            {
                "test_era_payment_reference": test_payment.reference,
                "test_era_invoice_number": test_number,
                "live_payment_reference": live_payment.reference,
                "live_invoice_number": live_number,
            },
        )
        ada = subcap.signed_in(on, "ada", ADA)
        ada.call(
            "invoices-list",
            "GET",
            "/api/billing/invoices",
            note="Only the invoice of the payment made AFTER the cutover is listed.",
        )
        ada.call(
            "invoice-pdf-test-era",
            "GET",
            f"/api/billing/invoices/{test_number}/pdf",
            note="The invoice of a payment made BEFORE the cutover: 404 `not_found`, the same body "
            "as an unknown number or another person's invoice.",
        )
        ada.call(
            "payments-list",
            "GET",
            "/api/billing/payments",
            note="Only the payment made after the cutover; the earlier one is not in the history.",
        )
        ada.call(
            "payment-read-test-era",
            "GET",
            f"/api/billing/payments/{test_payment.reference}",
            note="Reading the earlier payment by its reference: 404 `not_found`.",
        )
        ada.call(
            "payment-cancel-test-era",
            "POST",
            f"/api/billing/payments/{test_payment.reference}/cancel",
            body={},
            note="Cancelling it: 404 `not_found`; the provider is never asked.",
        )
        root = subcap.signed_in(on, "root", ROOT)
        root.call(
            "admin-payments",
            "GET",
            "/api/admin/billing/payments",
            note="The administrator still sees BOTH payments and their invoice numbers; the "
            "earlier one offers no refund (`refund_option.allowed` false, with the reason).",
        )
        root.call(
            "admin-refund-test-era",
            "POST",
            f"/api/admin/billing/payments/{test_payment.id}/refund",
            body={"reason": "first_plan_7d"},
            note="Asking for a refund of the earlier payment: 409 `before_go_live`, before any "
            "provider is called.",
        )
    finally:
        server.should_exit = True
        engine.dispose()


if __name__ == "__main__":
    main()
