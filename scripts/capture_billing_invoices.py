"""Drive the invoice, receipt and refund routes over real HTTP and save every exchange for the FE
guide (billing stage S9).

    python scripts/capture_billing_invoices.py postgresql+psycopg://USER:PASS@localhost:PORT/postgres

Same setup as `scripts/capture_billing_subscriptions.py` (which this script imports for its
helpers): real migrations (so the seeded plan and pack are the real rows), the real app under
uvicorn, real HTTP, two copies of the app (billing on and billing off). Files go to
`docs/fe-captures/billing-invoices/NN-name.json`; the real PDFs go beside them as `.pdf`, with the
text a PDF reader (`pdftotext`) extracts from each as `.txt`, so every claim about a PDF can be
checked by opening it.

**What is real:** the app, the guard (login and CSRF), the routes, the settlement rules, the
invoice numbers and PDFs (the real child process and WeasyPrint), the receipt mail built by the real
outbox sender (captured by a mailer that records instead of sending), the refund rules, the locks,
the sweeper pass, every error shape.

**What is NOT, and each capture says so where it matters:**
- **No real payment provider.** The provider is `app.billing.fake.FakeProvider` registered under
  the names `flutterwave` and `paystack`. Nothing here proves a real Paystack or Flutterwave accepts
  these refund calls: **not verified live**. A provider's refund that "got no answer" is scripted
  (`refund_error`), not a real timeout.
- **The seller's registration and tax numbers are PLACEHOLDERS** (`RC-PLACEHOLDER-NOT-REAL`,
  `TIN-PLACEHOLDER-NOT-REAL`), set only here so the PDF shows what they look like. The app reads them
  from the environment and invents nothing: Adebayo supplies the real ones (spec section 6).
- **VAT is shown at 7.5% for the second account only**, to show the arithmetic. The default rate is
  0 (no VAT line); whether Beyrichq charges VAT is an open fact (spec section 6).
- **The clock is moved by SQL** where a window matters (a payment aged 8 days for the 7-day rule).
- Every person and address is a placeholder (`@example.test`); passwords are placeholders.
"""

import hashlib
import json
import shutil
import subprocess
import sys
from datetime import UTC, datetime, timedelta

import capture_billing_subscriptions as subcap

base = subcap.base
OUT = base.ROOT / "docs" / "fe-captures" / "billing-invoices"
base.OUT = OUT
subcap.OUT = OUT

import httpx  # noqa: E402
from sqlalchemy import create_engine, select, text, update  # noqa: E402
from sqlalchemy.orm import Session  # noqa: E402

from app import hours, mail  # noqa: E402
from app.billing import invoices, renewals  # noqa: E402
from app.billing.invoice_number import shown as shown_number  # noqa: E402
from app.billing.payments import new_payment  # noqa: E402
from app.billing.types import OutcomeUnknown, ProviderError, ProviderTransaction  # noqa: E402
from app.config import Settings  # noqa: E402
from app.main import create_app  # noqa: E402
from app.models import (  # noqa: E402
    Invoice,
    MailOutbox,
    Payment,
    PlanPrice,
    UsageEntry,
    User,
)
from app.passwords import hash_password  # noqa: E402
from app.services import get_bot_provider  # noqa: E402
from app.usage import email_hash  # noqa: E402

Recorder = base.Recorder
PASSWORD = subcap.PASSWORD
USD, NGN = subcap.USD, subcap.NGN
ADA, BEN, ROOT = "ada@example.test", "ben@example.test", "root@example.test"
PEOPLE = ("cy", "dee", "eve", "flo", "gus", "hal", "ivy")


def email(name: str) -> str:
    return f"{name}@example.test"


def process_settings(vat_bp: int = 0, **extra) -> Settings:
    """What the app reads from the process (`get_settings`): the seller's placeholders, the VAT rate,
    and mail to the console provider so receipts are queued."""
    return Settings(
        _env_file=None,
        app_env="test",
        session_secret="capture-secret-" + "x" * 30,
        cookie_secure=False,
        database_url=base.SCRATCH_URL,
        public_base_url="https://notes.example.test",
        token_enc_key=base.KEY,
        mail_provider="console",
        billing_enabled=True,
        billing_allowed_emails="*",
        billing_seller_name="Beyrichq LTD",
        billing_seller_address="PLACEHOLDER ADDRESS (not real), Lagos, Nigeria",
        billing_seller_registration_no="RC-PLACEHOLDER-NOT-REAL",
        billing_seller_tax_id="TIN-PLACEHOLDER-NOT-REAL",
        billing_seller_email="billing-placeholder@example.test",
        billing_vat_rate_bp=vat_bp,
        **extra,
    )


def use_vat(vat_bp: int) -> None:
    cfg = process_settings(vat_bp)
    invoices.get_settings = lambda: cfg


def person(engine, address: str, *, admin: bool = False) -> None:
    with Session(engine) as session:
        session.add(
            User(
                email=address,
                password_hash=hash_password(PASSWORD),
                email_verified_at=datetime.now(UTC),
                is_admin=admin,
            )
        )
        session.commit()


def quiet_webhook(server_base: str, provider, tx, kind: str = "charge_succeeded") -> int:
    """The provider's webhook to the real route, not saved (a setup step, not a capture)."""
    body = provider.event_body(provider.event_for(tx, kind=kind))
    return httpx.post(
        f"{server_base}/api/billing/webhooks/{provider.name}",
        content=body,
        headers={"Content-Type": "application/json", "X-Fake-Signature": provider.sign(body)},
    ).status_code


def buy_quietly(engine, on: str, name: str, *, country="US", plan="plan_20h") -> Payment:
    """A person buys the plan through the real checkout route and the real webhook (not saved)."""
    client = httpx.Client(base_url=on)
    token = client.get("/api/session").json()["csrf_token"]
    login = client.post(
        "/api/session",
        json={"email": email(name), "password": PASSWORD},
        headers={"X-CSRF-Token": token},
    ).json()
    headers = {"X-CSRF-Token": login["csrf_token"]}
    client.put("/api/billing/profile/country", json={"billing_country": country}, headers=headers)
    out = client.post("/api/billing/checkout", json={"kind": "plan", "code": plan}, headers=headers)
    reference = out.json()["payment"]["reference"]
    provider = USD if country != "NG" else NGN
    payment = subcap.payment_by_reference(engine, reference)
    tx = provider.complete(payment, card_token=f"tok-{name}", payer_email=email(name))
    quiet_webhook(on, provider, tx)
    return subcap.payment_by_reference(engine, reference)


def buy_pack_quietly(engine, on: str, name: str) -> Payment:
    client = httpx.Client(base_url=on)
    token = client.get("/api/session").json()["csrf_token"]
    login = client.post(
        "/api/session",
        json={"email": email(name), "password": PASSWORD},
        headers={"X-CSRF-Token": token},
    ).json()
    out = client.post(
        "/api/billing/checkout",
        json={"kind": "pack", "code": "pack_10h"},
        headers={"X-CSRF-Token": login["csrf_token"]},
    )
    payment = subcap.payment_by_reference(engine, out.json()["payment"]["reference"])
    quiet_webhook(on, USD, USD.complete(payment))
    return subcap.payment_by_reference(engine, payment.reference)


def save_pdf(ada: Recorder, name: str, number: str, note: str) -> bytes:
    """Download an invoice PDF through the route, save the exchange, the file and the text that a
    PDF reader extracts from it."""
    response = ada.http.get(f"/api/billing/invoices/{number}/pdf")
    Recorder.count += 1
    pdf = response.content
    stem = f"{Recorder.count:02d}-{name}"
    (OUT / f"{stem}.pdf").write_bytes(pdf)
    extracted = subprocess.run(
        ["pdftotext", "-layout", str(OUT / f"{stem}.pdf"), "-"], capture_output=True, check=True
    ).stdout.decode()
    (OUT / f"{stem}.txt").write_text(extracted)
    record = {
        "note": note,
        "client": ada.who,
        "request": {"method": "GET", "path": f"/api/billing/invoices/{number}/pdf"},
        "response": {
            "status": response.status_code,
            "headers": {
                k: response.headers[k]
                for k in ("content-type", "content-disposition", "cache-control")
                if k in response.headers
            },
            "body": {
                "file": f"{stem}.pdf",
                "bytes": len(pdf),
                "sha256": hashlib.sha256(pdf).hexdigest(),
                "starts_with": pdf[:8].decode("latin-1"),
                "text_extracted_by_pdftotext": f"{stem}.txt",
            },
        },
    }
    (OUT / f"{stem}.json").write_text(json.dumps(record, indent=2, ensure_ascii=False) + "\n")
    print(
        f"{Recorder.count:02d}-{name:44} GET   invoice pdf -> {response.status_code}, {len(pdf)} bytes"
    )
    return pdf


class CapturingMailer:
    """Records what the outbox sender would send, instead of sending it."""

    def __init__(self) -> None:
        self.sent = []

    def send(self, message, key: str) -> None:
        self.sent.append(message)


def run(engine, on: str, off: str, settings: Settings) -> None:
    for address in (ADA, BEN):
        person(engine, address)
    person(engine, ROOT, admin=True)
    for name in PEOPLE:
        person(engine, email(name))

    # ---- billing off ----
    off_root = subcap.signed_in(off, "root flag-off", ROOT)
    off_root.call(
        "off-admin-payments",
        "GET",
        "/api/admin/billing/payments",
        note="Billing is off: 404 `not_found`, even for the administrator.",
    )
    off_ada = subcap.signed_in(off, "ada flag-off", ADA)
    off_ada.call(
        "off-invoices", "GET", "/api/billing/invoices", note="Billing is off: 404 `not_found`."
    )

    # ---- the person's invoices (VAT off: the default) ----
    use_vat(0)
    Recorder(on, "visitor").call(
        "visitor-invoices", "GET", "/api/billing/invoices", note="Not signed in: 401."
    )
    ada = subcap.signed_in(on, "ada billing-on", ADA)
    ada.call(
        "invoices-empty",
        "GET",
        "/api/billing/invoices",
        note="Nothing paid yet: an empty list, not an error.",
    )
    ada.call(
        "country-us",
        "PUT",
        "/api/billing/profile/country",
        body={"billing_country": "US"},
        note="Dollars through the route named `flutterwave`.",
    )
    ada.call(
        "profile-for-the-invoice",
        "PUT",
        "/api/billing/profile",
        body={
            "name_or_company": "Ada Works Ltd (placeholder)",
            "address": "1 Placeholder Road, Lagos",
            "tax_id": "BUYER-TAX-PLACEHOLDER",
            "billing_email": ADA,
        },
        note="The billing profile is the BUYER on the invoice: its name, address and tax number are copied onto each invoice when it is issued (a snapshot: a later edit never changes an old invoice).",
    )
    started = ada.call(
        "checkout-plan",
        "POST",
        "/api/billing/checkout",
        body={"kind": "plan", "code": "plan_20h"},
        note="Starts the hosted checkout; the payment is `pending`.",
    ).json()
    reference = started["payment"]["reference"]
    payment = subcap.payment_by_reference(engine, reference)
    ada.call(
        "payments-before-paid",
        "GET",
        "/api/billing/payments",
        note="`invoice_number` is null while the payment waits: the invoice is made when the payment succeeds.",
    )
    tx = USD.complete(payment, card_token="tok-capture-ada", payer_email=ADA)
    subcap.webhook(
        on,
        "webhook-paid",
        USD,
        tx,
        "The provider's signed webhook; the payment is re-read and verified, the hours are granted and the invoice is issued in the same step.",
    )
    ada.call(
        "payments-after-paid",
        "GET",
        "/api/billing/payments",
        note="`invoice_number` now names the invoice.",
    )
    listed = ada.call(
        "invoices-list",
        "GET",
        "/api/billing/invoices",
        note="`subtotal_minor + vat_minor = total_minor`; `vat_rate_bp` 0 means no VAT line; the number is a plain `BN-<year>-<digits>`.",
    ).json()
    number = listed["invoices"][0]["number"]
    save_pdf(
        ada,
        "invoice-pdf",
        number,
        "The invoice PDF: built now, never stored. `Content-Disposition` carries the file name; `Cache-Control: no-store`. The .txt beside it is what `pdftotext` extracts from these exact bytes.",
    )
    ada.call(
        "invoice-bad-number",
        "GET",
        "/api/billing/invoices/not-a-number/pdf",
        note="A number that is not shaped like one: 404 `not_found`, never read from the database.",
    )
    ada.call(
        "invoice-unknown-number",
        "GET",
        "/api/billing/invoices/BN-2026-999999/pdf",
        note="Unknown: 404 `not_found`.",
    )
    ben = subcap.signed_in(on, "ben billing-on", BEN)
    ben.call(
        "invoices-other-person",
        "GET",
        "/api/billing/invoices",
        note="Another account sees none of Ada's invoices.",
    )
    ben.call(
        "invoice-pdf-other-person",
        "GET",
        f"/api/billing/invoices/{number}/pdf",
        note="Someone else's invoice number is a plain 404, the same as an unknown one.",
    )

    # a pack: a second invoice, numbered after the first
    pack = ada.call(
        "checkout-pack",
        "POST",
        "/api/billing/checkout",
        body={"kind": "pack", "code": "pack_10h"},
        note="An extra-hours pack on the plan's route.",
    ).json()
    pack_payment = subcap.payment_by_reference(engine, pack["payment"]["reference"])
    subcap.webhook(
        on,
        "webhook-pack-paid",
        USD,
        USD.complete(pack_payment),
        "The pack verified: 10 hours granted, a second invoice issued.",
    )
    ada.call(
        "invoices-two",
        "GET",
        "/api/billing/invoices",
        note="Newest first; the number is sequential within the year.",
    )

    # naira, with VAT 7.5% INCLUDED in the price to show the arithmetic
    use_vat(750)
    ben.call(
        "country-ng",
        "PUT",
        "/api/billing/profile/country",
        body={"billing_country": "NG"},
        note="Naira through the route named `paystack`.",
    )
    ng = ben.call(
        "checkout-ng",
        "POST",
        "/api/billing/checkout",
        body={"kind": "plan", "code": "plan_20h"},
        note="NGN 25,000.00 (2,500,000 kobo), decided 2026-11-17.",
    ).json()
    ng_payment = subcap.payment_by_reference(engine, ng["payment"]["reference"])
    subcap.webhook(
        on,
        "webhook-ng-paid",
        NGN,
        NGN.complete(ng_payment, card_token="tok-ng", payer_email=BEN),
        "Paid in naira with VAT 7.5% configured as INCLUDED in the price.",
    )
    ng_list = ben.call(
        "invoices-list-ng-vat",
        "GET",
        "/api/billing/invoices",
        note="VAT INCLUDED at 750 basis points: `vat_minor` 174419 and `subtotal_minor` 2325581 add up to `total_minor` 2500000, rounded half up in whole kobo. The default rate is 0: no VAT line.",
    ).json()
    save_pdf(
        ben,
        "invoice-pdf-ng-vat",
        ng_list["invoices"][0]["number"],
        "The same invoice as a PDF, with the VAT lines.",
    )
    use_vat(0)

    # ---- the receipt mail ----
    with Session(engine) as session:
        rows = session.scalars(
            select(MailOutbox).where(MailOutbox.template == "billing_receipt")
        ).all()
        base.note_file(
            "receipt-mail-outbox-rows",
            "What is queued when a payment succeeds: the template and the invoice id only. No amount, no name, no number: the text and the PDF are built when the mail is SENT.",
            {
                "rows": [
                    {
                        "template": r.template,
                        "payload": r.payload,
                        "status": r.status,
                        "to_domain": r.to_email.split("@")[1],
                    }
                    for r in rows
                ]
            },
        )
        mailer = CapturingMailer()
        mail.deliver_pending(session, mailer, settings, datetime.now(UTC))
        receipts = [m for m in mailer.sent if m.subject.startswith("Your receipt")]
        base.note_file(
            "receipt-mail-delivered",
            "What the outbox sender built for each receipt (captured, not sent): the subject, the plain text, and the attached PDF's name, size and first bytes.",
            {
                "messages": [
                    {
                        "subject": m.subject,
                        "text": m.text,
                        "attachments": [
                            {
                                "filename": a.filename,
                                "content_type": a.content_type,
                                "bytes": len(a.content),
                                "starts_with": a.content[:8].decode("latin-1"),
                            }
                            for a in m.attachments
                        ],
                    }
                    for m in receipts
                ]
            },
        )

    # ---- the administrator: refunds ----
    root = subcap.signed_in(on, "root billing-on", ROOT)
    cy = buy_quietly(engine, on, "cy")
    dee = buy_quietly(engine, on, "dee")
    dee_pack = buy_pack_quietly(engine, on, "dee")
    eve = buy_quietly(engine, on, "eve")
    flo = buy_quietly(engine, on, "flo")
    gus = buy_quietly(engine, on, "gus")
    hal = buy_quietly(engine, on, "hal")
    ivy = buy_quietly(engine, on, "ivy")
    with Session(engine) as session:
        eve_user = session.scalars(select(User).where(User.email == email("eve"))).one()
        entry = UsageEntry(user_id=eve_user.id, email_hash=email_hash(eve_user.email), seconds=3600)
        session.add(entry)
        session.flush()
        hours.allocate(session, eve_user, entry, datetime.now(UTC))
        # a double charge: Flo pays a second plan checkout while her first plan is live
        flo_user = session.scalars(select(User).where(User.email == email("flo"))).one()
        second = new_payment(
            session,
            payer=flo_user,
            provider="flutterwave",
            kind="plan",
            currency="USD",
            amount_minor=1800,
        )
        second.plan_price_id = session.scalar(
            select(PlanPrice.id).where(
                PlanPrice.plan_code == "plan_20h", PlanPrice.currency == "USD"
            )
        )
        session.commit()
        second_id = second.id
        # Ivy's payment is 8 days old: outside the 7-day window
        session.execute(
            update(Payment)
            .where(Payment.id == ivy.id)
            .values(paid_at=datetime.now(UTC) - timedelta(days=8))
        )
        session.commit()
    second_payment = subcap.payment_by_reference(engine, _reference(engine, second_id))
    quiet_webhook(on, USD, USD.complete(second_payment, payer_email=email("flo")))

    root.call(
        "admin-payments",
        "GET",
        "/api/admin/billing/payments",
        note="Each payment with the ONE refund case that fits it and whether the rules allow it now (`refund_option`), and why not when not. The server decides; the screen only shows its answer.",
    )
    root.call(
        "admin-payments-needs-refund",
        "GET",
        "/api/admin/billing/payments?only=needs_refund",
        note="`only=needs_refund`: payments that succeeded and nothing could use (`needs_refund_reason`). Flo's second plan payment.",
    )
    ada.call(
        "admin-payments-as-a-person",
        "GET",
        "/api/admin/billing/payments",
        note="A signed-in person who is not an administrator: 403 `forbidden`.",
    )
    root.call(
        "admin-refund-no-csrf",
        "POST",
        f"/api/admin/billing/payments/{cy.id}/refund",
        body={"reason": "first_plan_7d"},
        csrf=None,
        note="No CSRF header: 403 `csrf_rejected`.",
    )
    root.call(
        "admin-refund-bad-reason",
        "POST",
        f"/api/admin/billing/payments/{cy.id}/refund",
        body={"reason": "because"},
        note="422 `validation_error`: the case is one of three.",
    )
    root.call(
        "admin-refund-an-amount",
        "POST",
        f"/api/admin/billing/payments/{cy.id}/refund",
        body={"reason": "first_plan_7d", "amount_minor": 1},
        note="422: an amount is never taken from the request; the whole payment is refunded.",
    )
    root.call(
        "admin-refund-unknown-payment",
        "POST",
        "/api/admin/billing/payments/987654/refund",
        body={"reason": "double_charge"},
        note="404 `not_found`.",
    )

    root.call(
        "admin-refund-window-over",
        "POST",
        f"/api/admin/billing/payments/{ivy.id}/refund",
        body={"reason": "first_plan_7d"},
        note="Eight days after the payment: 409 `window_over`. Nothing was sent to the provider.",
    )
    root.call(
        "admin-refund-hours-used",
        "POST",
        f"/api/admin/billing/payments/{eve.id}/refund",
        body={"reason": "first_plan_7d"},
        note="One hour of the plan was used: 409 `hours_used`.",
    )
    root.call(
        "admin-refund-wrong-kind",
        "POST",
        f"/api/admin/billing/payments/{dee.id}/refund",
        body={"reason": "pack_14d"},
        note="The case must fit the payment: 409 `wrong_kind`.",
    )
    root.call(
        "admin-refund-not-a-double-charge",
        "POST",
        f"/api/admin/billing/payments/{flo.id}/refund",
        body={"reason": "double_charge"},
        note="Flo's FIRST payment was used (it bought her plan): not a double charge, 409 `not_a_double_charge`.",
    )

    root.call(
        "admin-refund-first-plan",
        "POST",
        f"/api/admin/billing/payments/{cy.id}/refund",
        body={"reason": "first_plan_7d", "note": "asked by email (placeholder)"},
        note="A first plan payment within 7 days, no hours used: refunded in full. The provider was asked ONCE, with our own reference. `status` `submitted`: the provider accepted it (a card refund takes days to arrive). The plan ended and its hours were removed in the same step.",
    )
    root.call(
        "admin-refund-again",
        "POST",
        f"/api/admin/billing/payments/{cy.id}/refund",
        body={"reason": "first_plan_7d"},
        note="Asking again: 409 `already_refunded`. The provider is not asked.",
    )
    cy_client = subcap.signed_in(on, "cy billing-on", email("cy"))
    cy_client.call(
        "refunded-payments",
        "GET",
        "/api/billing/payments",
        note="The person's own history: `status` `refunded`; the invoice number stays.",
    )
    cy_list = cy_client.call(
        "refunded-invoices",
        "GET",
        "/api/billing/invoices",
        note="The invoice shows the payment as `refunded` with `refunded_at`.",
    ).json()
    cy_client.call(
        "refunded-subscription",
        "GET",
        "/api/billing/subscription",
        note="The plan ended: `state` `none`, no hours, the subscription is `ended`.",
    )
    save_pdf(
        cy_client,
        "invoice-pdf-refunded",
        cy_list["invoices"][0]["number"],
        "The refunded invoice's PDF says so on its face (there is no credit note yet: a follow-up for the accountant).",
    )
    root.call(
        "admin-refund-pack",
        "POST",
        f"/api/admin/billing/payments/{dee_pack.id}/refund",
        body={"reason": "pack_14d"},
        note="An unused pack within 14 days: refunded; only the pack's hours go, the plan is untouched.",
    )
    root.call(
        "admin-refund-double-charge",
        "POST",
        f"/api/admin/billing/payments/{second_id}/refund",
        body={"reason": "double_charge"},
        note="A double charge: always refunded in full, any time; it granted nothing, so there is nothing else to remove.",
    )

    USD.refund_error = OutcomeUnknown("scripted: the provider did not answer")
    USD.refund_acts_on_error = True
    unknown = root.call(
        "admin-refund-unknown-outcome",
        "POST",
        f"/api/admin/billing/payments/{gus.id}/refund",
        body={"reason": "first_plan_7d"},
        note="The provider gave NO answer (scripted here; a real timeout in production). `status` `unknown`, `needs_resolution` true: it may or may not exist, so NOTHING asks again. The hours stay removed meanwhile.",
    ).json()
    USD.refund_error = None
    USD.refund_acts_on_error = False
    root.call(
        "admin-refund-while-unknown",
        "POST",
        f"/api/admin/billing/payments/{gus.id}/refund",
        body={"reason": "first_plan_7d"},
        note="Asking again while it is unresolved: 409 `refund_in_progress`. The provider is not asked.",
    )
    root.call(
        "admin-refunds-list",
        "GET",
        "/api/admin/billing/refunds",
        note="Newest first. `needs_resolution` true: a person must look at the provider's dashboard for `reference` and say what is there.",
    )
    root.call(
        "admin-resolve-bad-outcome",
        "POST",
        f"/api/admin/billing/refunds/{unknown['id']}/resolve",
        body={"outcome": "maybe"},
        note="422 `validation_error`.",
    )
    root.call(
        "admin-resolve-made",
        "POST",
        f"/api/admin/billing/refunds/{unknown['id']}/resolve",
        body={"outcome": "made"},
        note="The person looked and the refund exists: the payment becomes `refunded` and the plan ends. Nothing calls the provider.",
    )
    root.call(
        "admin-resolve-again",
        "POST",
        f"/api/admin/billing/refunds/{unknown['id']}/resolve",
        body={"outcome": "not_made"},
        note="Already resolved: 409 `not_unresolved`.",
    )

    USD.refund_error = ProviderError("scripted: refused")
    refused = root.call(
        "admin-refund-provider-refuses",
        "POST",
        f"/api/admin/billing/payments/{hal.id}/refund",
        body={"reason": "first_plan_7d"},
        note="The provider REFUSED the request (nothing moved): `status` `failed`, `error` `provider_refused`. The hours are back and the plan stands.",
    ).json()
    USD.refund_error = None
    root.call(
        "admin-payments-after-refusal",
        "GET",
        "/api/admin/billing/payments",
        note="Hal's payment is refundable again (a refused refund is closed); the refund list shows the failed one.",
    )
    assert refused["status"] == "failed"

    # ---- what the administrators are told ----
    flo_tx = ProviderTransaction(
        provider_tx_id="fx-second-charge",
        reference=flo.reference,
        status="successful",
        amount_minor=flo.amount_minor,
        currency="USD",
    )
    USD.add_transaction(flo_tx)
    subcap.webhook(
        on,
        "webhook-second-charge",
        USD,
        flo_tx,
        "A SECOND successful charge under a different transaction id for a payment that is already paid: 200 (decided); the provider is asked first, nothing is granted, and the administrators are told by id.",
    )
    with Session(engine) as session:
        pending = new_payment(
            session,
            payer=session.scalars(select(User).where(User.email == ADA)).one(),
            provider="flutterwave",
            kind="pack",
            currency="USD",
            amount_minor=1000,
        )
        session.commit()
        pending_ref = pending.reference
        pending_id = pending.id
    refused_tx = USD.complete(
        subcap.payment_by_reference(engine, pending_ref), amount_minor=500, tx_id="fx-half"
    )
    subcap.webhook(
        on,
        "webhook-refused-amount",
        USD,
        refused_tx,
        "A successful charge for HALF the amount we asked: the rules refuse it (nothing granted) and the administrators are told: money may have moved.",
    )
    with Session(engine) as session:  # the old pending payment the sweeper names once a day
        session.execute(
            update(Payment)
            .where(Payment.id == pending_id)
            .values(created_at=datetime.now(UTC) - timedelta(hours=30))
        )
        session.commit()
    with Session(engine) as session:
        renewals.sweep(session, datetime.now(UTC), settings)
    root.call(
        "admin-notifications",
        "GET",
        "/api/notifications",
        note="The administrator's bell after the events above: the second charge, the refused charge, the refund with no answer, the payments waiting more than a day. By ids only: no email address, no provider text.",
    )

    with Session(engine) as session:
        base.note_file(
            "invoice-rows-at-the-end",
            "The invoices table at the end of the run (numbers are sequential within the year; the seller and buyer are snapshots).",
            {
                "invoices": [
                    {
                        "number": shown_number(i.number),
                        "payment_id": i.payment_id,
                        "currency": i.currency,
                        "subtotal_minor": i.subtotal_minor,
                        "vat_rate_bp": i.vat_rate_bp,
                        "vat_minor": i.vat_minor,
                        "total_minor": i.total_minor,
                        "kind": i.line.get("kind"),
                        "seller": i.seller,
                    }
                    for i in session.scalars(select(Invoice).order_by(Invoice.id))
                ]
            },
        )
    subcap.run_script(
        engine,
        "operator-preflight",
        "The runbook's preflight for step 1, read-only, now with a note about the seller's details.",
        lambda s: subcap.runbook.preflight(s, settings, 1),
    )


def _reference(engine, payment_id: int) -> str:
    with Session(engine) as session:
        return session.get(Payment, payment_id).reference


def main() -> None:
    if not sys.argv[1:]:
        sys.exit(__doc__)
    if not shutil.which("pdftotext"):
        sys.exit("pdftotext (poppler) is needed to read the PDFs this script saves.")
    base.prepare_database()
    shutil.rmtree(OUT, ignore_errors=True)
    OUT.mkdir(parents=True)
    use_vat(0)

    def settings_for(**extra) -> Settings:
        return Settings(
            _env_file=None,
            app_env="test",
            session_secret="capture-secret-" + "x" * 30,
            cookie_secure=False,
            database_url=base.SCRATCH_URL,
            public_base_url="https://notes.example.test",
            token_enc_key=base.KEY,
            **extra,
        )

    on_app_settings = settings_for(billing_enabled=True, billing_allowed_emails="*")
    apps = [create_app(on_app_settings), create_app(settings_for(billing_enabled=False))]
    for each in apps:
        each.dependency_overrides[get_bot_provider] = lambda: base.FakeBot()
    started = [base.start_server(each) for each in apps]
    engine = create_engine(base.SCRATCH_URL)
    try:
        run(engine, started[0][1], started[1][1], on_app_settings)
        for path in OUT.glob("*.json"):  # no password, even a placeholder, stays in a file
            body = path.read_text()
            for secret in (PASSWORD, base.ADMIN_PASSWORD):
                body = body.replace(secret, "<password placeholder>")
            path.write_text(body)
        with engine.connect() as conn:
            conn.execute(text("select 1"))
    finally:
        for server, _ in started:
            server.should_exit = True
        engine.dispose()


if __name__ == "__main__":
    main()
