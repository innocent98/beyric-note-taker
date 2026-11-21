"""Invoices (billing stage S9, `app/billing/invoices.py`): the numbering, the VAT arithmetic, which
payments are invoiced, the snapshots, the receipt mail and the PDF.

Real Postgres; the payer "pays" through the REAL webhook inbox and settlement rules with only the
provider scripted. The number's uniqueness under two payments settled at the same moment is proved
with two REAL connections (threads, each its own session), not with a mock.
"""

import shutil
import subprocess
import threading
import time
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.billing import invoice_pdf, invoices
from app.billing import subscriptions as subs
from app.models import (
    BillingProfile,
    HourGrant,
    Invoice,
    InvoiceCounter,
    MailOutbox,
    Notification,
    Payment,
)
from tests.billing_support import (  # noqa: F401  (fixtures used by name)
    HOUR,
    P20,
    T0,
    assert_no_test_wording,
    buy_plan,
    choose_country,
    grants,
    make_catalog,
    pay,
    settings,
)
from tests.test_web import make_user


@pytest.fixture(autouse=True)
def catalog(session, vault_key):
    make_catalog(session)


@pytest.fixture(autouse=True)
def process_settings(monkeypatch):
    """What the app reads from the process (`get_settings`): test settings, with mail going to the
    console provider so receipts are queued (production with no provider queues none)."""
    monkeypatch.setattr(invoices, "get_settings", lambda: settings(mail_provider="console"))


@pytest.fixture
def ada(session):
    return make_user(session, "ada@example.test")


@pytest.fixture
def admin(session):
    return make_user(session, "admin@example.test", admin=True)


def all_invoices(session) -> list[Invoice]:
    session.expire_all()
    return list(session.scalars(select(Invoice).order_by(Invoice.id)))


# ---- the VAT arithmetic ------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("total", "rate", "expected"),
    [
        (1800, 0, (1800, 0)),  # no VAT line at the default rate
        (
            2_500_000,
            750,
            (2_325_581, 174_419),
        ),  # NGN 25,000.00 at 7.5% included: 174,418.6 rounds up
        (1800, 750, (1674, 126)),  # USD 18.00: 125.58 rounds to 126
        (1, 750, (1, 0)),  # one kobo: 0.07 rounds to 0
        (7, 750, (7, 0)),  # 7 * 750 / 10750 = 0.49: rounds down to 0
        (10_750, 750, (10_000, 750)),  # exact
        (100, 5000, (67, 33)),  # 33.33 -> 33
        (3, 5000, (2, 1)),  # 1.0 exactly
    ],
)
def test_vat_split_rounds_half_up_in_whole_minor_units(total, rate, expected):
    assert invoices.vat_split(total, rate) == expected


def test_vat_split_parts_always_add_up_and_never_go_negative():
    for rate in (0, 1, 150, 750, 1000, 5000, 10000):
        for total in list(range(1, 400)) + [999_999, 2_500_000, 1_500_000, 123_456_789]:
            subtotal, vat = invoices.vat_split(total, rate)
            assert subtotal + vat == total
            assert subtotal >= 0 and vat >= 0
            # the VAT is what the rate says of the subtotal, to within one minor unit
            assert abs(subtotal * rate / 10000 - vat) <= 1


@pytest.mark.parametrize(("total", "rate"), [(0, 0), (-5, 0), (100, -1), (100, 10001), (1.5, 0)])
def test_vat_split_refuses_nonsense(total, rate):
    with pytest.raises(ValueError):
        invoices.vat_split(total, rate)


def test_the_year_is_read_in_lagos_time():
    assert invoices.invoice_year(datetime(2026, 12, 31, 22, 59, tzinfo=UTC)) == 2026
    assert invoices.invoice_year(datetime(2026, 12, 31, 23, 0, tzinfo=UTC)) == 2027  # midnight WAT
    assert invoices.number_text(2026, 123) == "BN-2026-000123"
    assert invoices.number_text(2026, 123, test=True) == "BN-T-2026-000123"


# ---- which payments get an invoice, and what is on it ----------------------------------------


def test_a_first_plan_is_invoiced_once_with_a_number_and_snapshots(session, ada, providers):
    session.add(
        BillingProfile(
            user_id=ada.id,
            name_or_company="Ada Works Ltd",
            address="1 Marina Road, Lagos",
            billing_country="US",
            tax_id="TAX-PLACEHOLDER-1",
            billing_email="billing@example.test",
        )
    )
    session.commit()
    buy_plan(session, ada, providers.usd)

    [invoice] = all_invoices(session)
    payment = session.scalars(select(Payment)).one()
    assert invoice.number == "BN-T-2026-000001"
    assert invoice.payment_id == payment.id and invoice.payer_user_id == ada.id
    assert (invoice.currency, invoice.total_minor) == ("USD", 1800)
    assert (invoice.vat_rate_bp, invoice.vat_minor, invoice.subtotal_minor) == (0, 0, 1800)
    assert invoice.buyer == {
        "name": "Ada Works Ltd",
        "address": "1 Marina Road, Lagos",
        "country": "US",
        "tax_id": "TAX-PLACEHOLDER-1",
        "email": "billing@example.test",
    }
    assert invoice.seller["name"] == "Beyrichq LTD"
    assert invoice.line["kind"] == "plan"
    assert invoice.line["description"] == "Twenty"
    assert invoice.line["seconds"] == P20
    assert invoice.test_mode is True  # BILLING_LIVE is off in tests: not a tax document


def test_the_seller_comes_from_settings_and_an_empty_number_is_kept_empty(session, ada, providers):
    cfg = settings(
        billing_seller_name="Seller Placeholder Ltd",
        billing_seller_registration_no="RC-PLACEHOLDER-0",
    )
    choose_country(session, ada, "US")
    started = subs.start_plan_checkout(session, ada, "p20", cfg, T0)
    pay(session, providers.usd, started.payment, T0)
    # the handler reads the process settings (`get_settings`), so issue explicitly with `cfg`
    payment = session.scalars(select(Payment)).one()
    session.query(Invoice).delete()
    session.flush()
    invoice = invoices.issue(session, payment, T0, cfg)
    assert invoice.seller == {
        "name": "Seller Placeholder Ltd",
        "address": "",
        "registration_no": "RC-PLACEHOLDER-0",
        "tax_id": "",  # never invented
        "email": "",
    }


def test_vat_included_at_a_configured_rate_is_split_on_the_invoice(session, ada, providers):
    cfg = settings(billing_vat_rate_bp=750)
    choose_country(session, ada, "NG")
    started = subs.start_plan_checkout(session, ada, "p20", cfg, T0)
    pay(session, providers.ngn, started.payment, T0)
    payment = session.scalars(select(Payment)).one()
    session.query(Invoice).delete()
    session.flush()
    invoice = invoices.issue(session, payment, T0, cfg)
    assert (invoice.currency, invoice.total_minor) == ("NGN", 2_500_000)
    assert (invoice.subtotal_minor, invoice.vat_minor, invoice.vat_rate_bp) == (
        2_325_581,
        174_419,
        750,
    )
    assert invoice.subtotal_minor + invoice.vat_minor == invoice.total_minor == payment.amount_minor


def test_every_kind_of_used_payment_is_invoiced_in_order(session, ada, providers):
    from app.billing import renewals

    sub = buy_plan(session, ada, providers.usd)
    # a renewal: make the period due and let the clock charge the saved card
    from sqlalchemy import update

    from app.models import Subscription

    session.execute(
        update(Subscription)
        .where(Subscription.id == sub.id)
        .values(current_period_end=T0 + timedelta(hours=1))
    )
    session.commit()
    providers.usd.charge_token = "tok-2"
    renewals.run_due(session, T0 + timedelta(hours=1, minutes=1), settings())
    # a pack on the same route
    session.expire_all()
    started = subs.start_pack_checkout(session, ada, "pack10", settings(), T0 + timedelta(days=1))
    pay(session, providers.usd, started.payment, T0 + timedelta(days=1))

    rows = all_invoices(session)
    assert [r.number for r in rows] == ["BN-T-2026-000001", "BN-T-2026-000002", "BN-T-2026-000003"]
    assert [r.line["kind"] for r in rows] == ["plan", "renewal", "pack"]
    assert rows[2].line["description"] == "Ten hours"
    paid = [p.amount_minor for p in session.scalars(select(Payment).order_by(Payment.id))]
    assert [r.total_minor for r in rows] == paid


def test_a_replayed_webhook_makes_no_second_invoice(session, ada, providers):
    from tests.billing_support import deliver

    choose_country(session, ada, "US")
    started = subs.start_plan_checkout(session, ada, "p20", settings(), T0)
    tx = providers.usd.complete(started.payment, card_token="t", tx_id="fx-1")
    body = providers.usd.event_body(providers.usd.event_for(tx))
    deliver(session, providers.usd, body, T0)
    deliver(session, providers.usd, body, T0 + timedelta(minutes=5))
    assert len(all_invoices(session)) == 1


def test_a_payment_nothing_could_use_gets_no_invoice_and_a_reason(session, ada, providers):
    buy_plan(session, ada, providers.usd)
    # a second checkout paid by someone who already has a live plan: money, but no supply
    from app.billing.payments import new_payment

    second = new_payment(
        session,
        payer=ada,
        provider="flutterwave",
        kind="plan",
        currency="USD",
        amount_minor=1800,
    )
    from app.models import PlanPrice

    second.plan_price_id = session.scalar(
        select(PlanPrice.id).where(PlanPrice.plan_code == "p20", PlanPrice.currency == "USD")
    )
    session.commit()
    pay(session, providers.usd, second, T0 + timedelta(hours=1))

    session.expire_all()
    second = session.get(Payment, second.id)
    assert second.status == "succeeded"
    assert second.needs_refund_reason == "the person already has a live plan"
    assert len(all_invoices(session)) == 1  # only the first payment
    receipts = session.scalars(select(MailOutbox).where(MailOutbox.template == "billing_receipt"))
    assert len(list(receipts)) == 1  # and no receipt for money that will be refunded


def test_the_invoice_handler_never_stops_the_hours_and_the_sweeper_issues_it_later(
    session, ada, providers, admin, monkeypatch
):
    real = invoices.issue

    def broken(*args, **kwargs):
        raise RuntimeError("the counter is down")

    monkeypatch.setattr(invoices, "issue", broken)
    buy_plan(session, ada, providers.usd)
    # the hours were granted anyway, the payment is paid, the admins were told, there is no invoice
    assert [g.seconds for g in grants(session, ada, "plan_period")] == [P20]
    assert session.scalars(select(Payment)).one().status == "succeeded"
    assert all_invoices(session) == []
    session.expire_all()
    alerts = list(
        session.scalars(
            select(Notification).where(Notification.kind == "admin.billing_invoice_failed")
        )
    )
    assert len(alerts) == 1

    monkeypatch.setattr(invoices, "issue", real)
    assert invoices.issue_missing(session, T0 + timedelta(minutes=3), settings()) == 1
    assert (
        invoices.issue_missing(session, T0 + timedelta(minutes=4), settings()) == 0
    )  # nothing left to do
    assert [i.number for i in all_invoices(session)] == ["BN-T-2026-000001"]


def test_issue_missing_skips_a_payment_nothing_could_use(session, ada, providers):
    buy_plan(session, ada, providers.usd)
    session.query(Invoice).delete()
    payment = session.scalars(select(Payment)).one()
    payment.needs_refund_reason = "test"
    session.commit()
    assert invoices.issue_missing(session, T0, settings()) == 0


# ---- the number under concurrency -------------------------------------------------------------


def _two_paid_payments(session, engine, ada, providers):
    """Two succeeded payments (committed) with no invoice, ready for two connections to invoice."""
    buy_plan(session, ada, providers.usd)
    bob = make_user(session, "bob@example.test")
    buy_plan(session, bob, providers.usd, country="US")
    session.query(Invoice).delete()
    session.query(InvoiceCounter).delete()
    session.commit()
    return [p.id for p in session.scalars(select(Payment).order_by(Payment.id))]


def test_two_payments_settled_at_the_same_moment_get_two_different_numbers(
    session, engine, ada, providers
):
    ids = _two_paid_payments(session, engine, ada, providers)
    first_holds, second_started = threading.Event(), threading.Event()
    waited = {}
    numbers = {}

    def first():
        with Session(engine) as db:  # connection one
            payment = db.get(Payment, ids[0])
            invoice = invoices.issue(db, payment, T0)
            numbers["first"] = invoice.number
            first_holds.set()  # the counter row is now locked by this open transaction
            second_started.wait(5)
            time.sleep(0.4)  # hold it while the second connection tries
            db.commit()

    def second():
        with Session(engine) as db:  # connection two
            first_holds.wait(5)
            payment = db.get(Payment, ids[1])
            second_started.set()
            started = time.monotonic()
            invoice = invoices.issue(db, payment, T0)  # must queue behind the first
            waited["seconds"] = time.monotonic() - started
            numbers["second"] = invoice.number
            db.commit()

    threads = [threading.Thread(target=first), threading.Thread(target=second)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(15)
    assert numbers == {"first": "BN-T-2026-000001", "second": "BN-T-2026-000002"}
    assert waited["seconds"] >= 0.3  # it really waited for the other connection's lock
    assert [i.number for i in all_invoices(session)] == ["BN-T-2026-000001", "BN-T-2026-000002"]


def test_eight_connections_issuing_at_once_never_share_a_number(session, engine, ada, providers):
    buy_plan(session, ada, providers.usd)
    for n in range(7):
        who = make_user(session, f"p{n}@example.test")
        buy_plan(session, who, providers.usd, country="US")
    session.query(Invoice).delete()
    session.query(InvoiceCounter).delete()
    session.commit()
    ids = [p.id for p in session.scalars(select(Payment).order_by(Payment.id))]
    assert len(ids) == 8
    barrier, errors = threading.Barrier(8), []

    def work(payment_id):
        try:
            with Session(engine) as db:
                payment = db.get(Payment, payment_id)
                barrier.wait(5)
                invoices.issue(db, payment, T0)
                db.commit()
        except Exception as exc:  # pragma: no cover
            errors.append(repr(exc))

    threads = [threading.Thread(target=work, args=(i,)) for i in ids]
    for t in threads:
        t.start()
    for t in threads:
        t.join(20)
    assert errors == []
    numbers = sorted(i.number for i in all_invoices(session))
    assert numbers == [f"BN-T-2026-{n:06d}" for n in range(1, 9)]  # 8 distinct, no gap


def test_a_rolled_back_invoice_gives_its_number_back(session, engine, ada, providers):
    ids = _two_paid_payments(session, engine, ada, providers)
    with Session(engine) as db:
        invoices.issue(db, db.get(Payment, ids[0]), T0)
        db.rollback()  # the settlement step failed: the number goes back
    with Session(engine) as db:
        number = invoices.issue(db, db.get(Payment, ids[1]), T0).number
        db.commit()
    assert number == "BN-T-2026-000001"  # no gap


def test_each_year_counts_from_one_and_the_unique_index_is_the_second_line(session, ada, providers):
    buy_plan(session, ada, providers.usd)
    [first] = all_invoices(session)
    payment = session.scalars(select(Payment)).one()
    # a payment in the next Lagos year starts that year's count
    assert invoices.next_number(session, 2027) == "BN-2027-000001"
    assert invoices.next_number(session, 2027) == "BN-2027-000002"
    assert session.get(InvoiceCounter, -2026).last_number == 1  # the test series, by negative year
    # a duplicate number or a second invoice for one payment is refused by the database itself
    clone = Invoice(
        number=first.number,
        payment_id=payment.id,
        issued_at=T0,
        currency="USD",
        subtotal_minor=1800,
        total_minor=1800,
        seller={},
        buyer={},
        line={},
    )
    session.add(clone)
    with pytest.raises(IntegrityError):
        session.flush()
    session.rollback()


def test_the_database_refuses_parts_that_do_not_add_up(session, ada, providers):
    buy_plan(session, ada, providers.usd)
    payment = session.scalars(select(Payment)).one()
    session.query(Invoice).delete()
    session.commit()
    session.add(
        Invoice(
            number="BN-T-2026-000099",
            payment_id=payment.id,
            issued_at=T0,
            currency="USD",
            subtotal_minor=1000,
            vat_minor=1,
            total_minor=1800,  # 1000 + 1 != 1800
            seller={},
            buyer={},
            line={},
        )
    )
    with pytest.raises(IntegrityError):
        session.flush()
    session.rollback()


# ---- the receipt mail -------------------------------------------------------------------------


def test_a_receipt_mail_is_queued_with_the_invoice_id_only(session, ada, providers):
    buy_plan(session, ada, providers.usd)
    session.expire_all()
    [row] = session.scalars(select(MailOutbox).where(MailOutbox.template == "billing_receipt"))
    invoice = all_invoices(session)[0]
    assert row.to_email == "ada@example.test"
    assert row.payload == {"invoice_id": invoice.id}  # ids only: no amount, no name, no number


def test_one_mail_per_payment_the_old_alert_mail_is_gone(session, ada, providers):
    buy_plan(session, ada, providers.usd)
    session.expire_all()
    templates = [m.template for m in session.scalars(select(MailOutbox))]
    assert templates.count("billing_receipt") == 1
    assert "alert" not in templates  # the bell entry stays, its mail does not
    bell = session.scalars(select(Notification).where(Notification.kind == "billing.receipt")).all()
    assert len(bell) == 1


def test_the_receipt_is_built_when_sent_with_the_pdf_attached(session, ada, providers):
    from app import mail_templates
    from app.billing import receipt_mail  # noqa: F401  (registers the template)

    buy_plan(session, ada, providers.usd)
    invoice = all_invoices(session)[0]
    message = mail_templates.prepare(
        session, "billing_receipt", {"invoice_id": invoice.id}, "ada@example.test", settings(), T0
    )
    # stored `BN-T-2026-000001` (the internal series), shown as an ordinary number
    assert message.subject == "Your receipt BN-2026-900001"
    assert "BN-2026-900001" in message.text and "USD 18.00" in message.text
    [attachment] = message.attachments
    assert attachment.filename == "invoice-BN-2026-900001.pdf"
    assert_no_test_wording(message.subject, message.text, message.html, attachment.filename)
    assert attachment.content.startswith(b"%PDF-") and len(attachment.content) > 3000


def test_a_renderer_that_fails_still_sends_the_receipt_without_the_file(
    session, ada, providers, monkeypatch
):
    from app import mail_templates

    buy_plan(session, ada, providers.usd)
    invoice = all_invoices(session)[0]

    def boom(doc):
        raise invoice_pdf.InvoiceFailed("down")

    monkeypatch.setattr(invoice_pdf, "build_pdf", boom)
    message = mail_templates.prepare(
        session, "billing_receipt", {"invoice_id": invoice.id}, "ada@example.test", settings(), T0
    )
    assert message.attachments == ()
    assert "download your invoice from your billing page" in message.text


def test_a_receipt_for_a_refunded_or_missing_payment_is_skipped(session, ada, providers):
    from app import mail_templates

    buy_plan(session, ada, providers.usd)
    invoice = all_invoices(session)[0]
    payment = session.get(Payment, invoice.payment_id)
    payment.status = "refunded"
    session.commit()
    args = ("ada@example.test", settings(), T0)
    skip = mail_templates.prepare(session, "billing_receipt", {"invoice_id": invoice.id}, *args)
    assert skip.reason == "payment_not_paid"
    assert mail_templates.prepare(
        session, "billing_receipt", {"invoice_id": 99999}, *args
    ).reason == ("invoice_gone")
    assert mail_templates.prepare(
        session, "billing_receipt", {"invoice_id": "x"}, *args
    ).reason == ("invoice_gone")


def test_an_unsubscribed_person_still_gets_the_receipt_a_bounce_does_not(session, ada, providers):
    from app import mail

    mail.suppress(session, "ada@example.test", "unsubscribed")
    session.commit()
    buy_plan(session, ada, providers.usd)
    session.expire_all()
    assert session.scalars(select(MailOutbox).where(MailOutbox.template == "billing_receipt")).one()
    row = session.scalars(select(MailOutbox).where(MailOutbox.template == "billing_receipt")).one()
    assert mail._is_suppressed(session, row) is False  # an unsubscribe does not stop a receipt
    mail.suppress(session, "ada@example.test", "bounce")
    session.commit()
    assert mail._is_suppressed(session, row) is True  # a bounce does


# ---- the PDF ----------------------------------------------------------------------------------


def _pdf_text(pdf: bytes) -> str | None:
    if not shutil.which("pdftotext"):
        return None
    done = subprocess.run(
        ["pdftotext", "-layout", "-", "-"], input=pdf, capture_output=True, check=True
    )
    return done.stdout.decode()


def _layout_text(doc: dict) -> str:
    """The text WeasyPrint lays out for the invoice page, read from its own box tree: what a reader
    sees, without needing a PDF text extractor in CI."""
    from weasyprint import HTML

    from app.invoice_render import render_html

    document = HTML(string=render_html(doc)).render()
    out = []

    def walk(box):
        text = getattr(box, "text", None)
        if text:
            out.append(text)
        for child in getattr(box, "children", ()) or ():
            walk(child)

    for page in document.pages:
        walk(page._page_box)
    return " ".join(" ".join(out).split())


def test_the_pdf_is_a_real_pdf_with_what_the_invoice_says(session, ada, providers):
    session.add(
        BillingProfile(
            user_id=ada.id,
            name_or_company="Ada Works Ltd",
            address="1 Marina Road, Lagos",
            billing_country="NG",
            tax_id="TAX-PLACEHOLDER-1",
        )
    )
    session.commit()
    choose_country(session, ada, "NG")
    started = subs.start_plan_checkout(session, ada, "p20", settings(), T0)
    pay(session, providers.ngn, started.payment, T0)
    invoice = all_invoices(session)[0]
    payment = session.get(Payment, invoice.payment_id)
    doc = invoice_pdf.document(invoice, payment)

    pdf = invoice_pdf.build_pdf(doc)  # the real child process, the real WeasyPrint
    assert pdf.startswith(b"%PDF-") and pdf.rstrip().endswith(b"%%EOF")
    assert len(pdf) > 5000  # fonts embedded: a real page, not an empty file
    wanted = [
        "BN-2026-900001",
        "Ada Works Ltd",
        "1 Marina Road, Lagos",
        "TAX-PLACEHOLDER-1",
        "Beyrichq LTD",
        "Twenty",
        "NGN 25,000.00",
        "1 December 2026 to 1 January 2027",
        payment.reference,
        "No VAT is charged on this invoice.",
    ]
    layout = _layout_text(doc)
    for text in wanted:
        assert text in layout, text
    assert_no_test_wording(layout, " ".join(doc["banners"]), doc["number"], doc["footer"])
    extracted = _pdf_text(pdf)  # a second opinion from a real PDF reader, where there is one
    if extracted is not None:
        flat = " ".join(extracted.split())
        for text in wanted:
            assert text in flat, text
        assert_no_test_wording(flat)


def test_the_pdf_shows_vat_and_a_refund_notice(session, ada, providers):
    cfg = settings(billing_vat_rate_bp=750)
    choose_country(session, ada, "NG")
    started = subs.start_plan_checkout(session, ada, "p20", cfg, T0)
    pay(session, providers.ngn, started.payment, T0)
    payment = session.scalars(select(Payment)).one()
    session.query(Invoice).delete()
    session.flush()
    invoice = invoices.issue(session, payment, T0, cfg)
    doc = invoice_pdf.document(invoice, payment, refunded_at=T0 + timedelta(days=2))
    layout = _layout_text(doc)
    assert "Subtotal NGN 23,255.81" in layout
    assert "VAT 7.5% (included in the price) NGN 1,744.19" in layout
    assert "Total paid (NGN) NGN 25,000.00" in layout
    assert "REFUNDED on 3 December 2026" in layout


def test_text_in_a_name_cannot_change_the_page(session, ada, providers):
    buy_plan(session, ada, providers.usd)
    invoice = all_invoices(session)[0]
    payment = session.get(Payment, invoice.payment_id)
    invoice.buyer = {
        "name": "<script>alert(1)</script> & <b>x</b>",
        "address": "a\nb <i>",
        "email": "",
    }
    doc = invoice_pdf.document(invoice, payment)
    from app.invoice_render import render_html

    html = render_html(doc)
    assert "<script>" not in html and "<b>x</b>" not in html
    assert "&lt;script&gt;" in html
    assert invoice_pdf.build_pdf(doc).startswith(b"%PDF-")


def test_a_grant_of_the_payment_is_unchanged_by_the_invoice(session, ada, providers):
    buy_plan(session, ada, providers.usd)
    grant = session.scalars(select(HourGrant).where(HourGrant.kind == "plan_period")).one()
    assert grant.seconds == P20 and grant.expires_at > grant.starts_at
    assert HOUR == 3600


# ---- the test series, one date, integer money --------------------------------------------------


def test_test_purchases_never_use_up_the_live_numbers(session, ada, providers):
    """Payments made with test keys have their own series (`BN-T-`): the first LIVE invoice of a
    year is `BN-<year>-000001` however many test purchases came before it."""
    from app.models import Payment

    buy_plan(session, ada, providers.usd)  # test mode: BILLING_LIVE is off
    bob = make_user(session, "bob@example.test")
    buy_plan(session, bob, providers.usd, country="US")
    assert [i.number for i in all_invoices(session)] == ["BN-T-2026-000001", "BN-T-2026-000002"]
    assert all(i.test_mode for i in all_invoices(session))

    # the same payments issued as LIVE invoices start the live series at one
    payments = list(session.scalars(select(Payment).order_by(Payment.id)))
    session.query(Invoice).delete()
    session.flush()
    live = settings(billing_live=True)
    first = invoices.issue(session, payments[0], T0, live)
    second = invoices.issue(session, payments[1], T0, live)
    assert (first.number, second.number) == ("BN-2026-000001", "BN-2026-000002")
    assert not first.test_mode and not second.test_mode
    assert session.get(InvoiceCounter, 2026).last_number == 2  # the live counter
    assert session.get(InvoiceCounter, -2026).last_number == 2  # the test counter is untouched


def test_a_late_invoice_has_one_date_the_payments(session, ada, providers):
    """The sweeper may issue an invoice days later. Its number's year and its issue date both come
    from the payment, so a 31 December payment is never a 2026 number with a 2027 date."""
    from app.models import Payment

    buy_plan(session, ada, providers.usd)
    payment = session.scalars(select(Payment)).one()
    session.query(Invoice).delete()
    session.flush()
    late = payment.paid_at + timedelta(days=40)
    invoice = invoices.issue(session, payment, late)
    assert invoice.issued_at == payment.paid_at
    assert invoices.invoice_year(invoice.issued_at) == int(invoice.number.split("-")[-2])


def test_the_pdf_writes_money_with_integers_only():
    assert invoice_pdf.money(2_500_000, "NGN") == "NGN 25,000.00"
    assert invoice_pdf.money(99, "USD") == "USD 0.99"
    assert invoice_pdf.money(1, "USD") == "USD 0.01"
    assert invoice_pdf.money(0, "USD") == "USD 0.00"
    # a float cannot hold this exactly (it would print ...00.00 or ...00.02)
    assert invoice_pdf.money(10**17 + 1, "NGN") == "NGN 1,000,000,000,000,000.01"


def test_the_number_pattern_survives_the_millionth_invoice_of_a_year():
    from app.billing.invoices_api import NUMBER

    for good in ("BN-2026-000001", "BN-2026-900001", "BN-2026-1000000", "BN-2026-123456789"):
        assert NUMBER.fullmatch(good), good
    for bad in (
        "BN-2026-00001",
        "bn-2026-000001",
        "BN-2026-1234567890",
        "BN-X-2026-000001",
        "BN-T-2026-000001",  # the internal series is never a number a person is given
        "BN-2026-",
    ):
        assert not NUMBER.fullmatch(bad), bad
    assert invoices.number_text(2026, 1_000_000) == "BN-2026-1000000"


# ---- no test-mode wording, one display number per row ----------------------------------------


def test_the_internal_series_is_shown_as_an_ordinary_number():
    from app.billing import invoice_number

    assert invoice_number.shown("BN-2026-000123") == "BN-2026-000123"  # live: as stored
    assert invoice_number.shown("BN-T-2026-000001") == "BN-2026-900001"
    assert invoice_number.shown("BN-T-2026-000011") == "BN-2026-900011"
    assert invoice_number.shown("BN-T-2027-100000") == "BN-2027-1000000"  # past six digits
    assert invoice_number.shown("not a number") == "not a number"


def test_a_shown_number_maps_back_to_the_one_row_it_came_from():
    from app.billing import invoice_number

    stored = [f"BN-T-{year}-{n:06d}" for year in (2026, 2027) for n in (1, 2, 99, 999_999)]
    shown = [invoice_number.shown(s) for s in stored]
    assert len(set(shown)) == len(stored)  # one-to-one: two rows never show the same number
    for s, number in zip(stored, shown, strict=True):
        assert "T" not in number
        assert s in invoice_number.stored_candidates(number)  # and back again
    # a live number is only itself: nothing in the live range reaches into the internal one
    assert invoice_number.stored_candidates("BN-2026-000123") == ["BN-2026-000123"]
    assert invoice_number.stored_candidates("BN-2026-899999") == ["BN-2026-899999"]


def test_a_test_era_invoice_never_shows_the_same_number_as_a_live_one(session, ada, providers):
    """Both series start at one in the database, but what is shown does not collide: the internal
    series is shown from 900001 up, the live one from 000001."""
    from app.billing import invoice_number

    buy_plan(session, ada, providers.usd)
    bob = make_user(session, "bob@example.test")
    buy_plan(session, bob, providers.usd, country="US")
    internal = [invoice_number.shown(i.number) for i in all_invoices(session)]
    assert internal == ["BN-2026-900001", "BN-2026-900002"]

    payments = list(session.scalars(select(Payment).order_by(Payment.id)))
    session.query(Invoice).delete()
    session.flush()
    live = [invoices.issue(session, p, T0, settings(billing_live=True)) for p in payments]
    shown_live = [invoice_number.shown(i.number) for i in live]
    assert shown_live == ["BN-2026-000001", "BN-2026-000002"]
    assert not set(internal) & set(shown_live)


def test_a_test_mode_invoice_reads_exactly_like_a_live_one(session, ada, providers):
    """The mail and the PDF of an invoice made in test mode and of the same payment made live say
    the same things: no banner, no extra line, no mention of test keys. Only the number differs."""
    from app import mail_templates
    from app.billing import receipt_mail  # noqa: F401  (registers the template)

    buy_plan(session, ada, providers.usd)
    test_invoice = all_invoices(session)[0]
    assert test_invoice.test_mode is True  # kept for the server only
    payment = session.get(Payment, test_invoice.payment_id)
    test_doc = invoice_pdf.document(test_invoice, payment)
    args = ("ada@example.test", settings(), T0)
    test_mail = mail_templates.prepare(
        session, "billing_receipt", {"invoice_id": test_invoice.id}, *args
    )
    assert test_doc["banners"] == []
    assert_no_test_wording(test_mail.subject, test_mail.text, test_mail.html)
    assert "not a tax document" not in test_mail.text.lower()

    session.query(Invoice).delete()
    session.flush()
    live_invoice = invoices.issue(session, payment, T0, settings(billing_live=True))
    live_doc = invoice_pdf.document(live_invoice, payment)
    live_mail = mail_templates.prepare(
        session, "billing_receipt", {"invoice_id": live_invoice.id}, *args
    )
    assert live_invoice.test_mode is False
    assert set(live_doc) == set(test_doc)
    assert {k: v for k, v in live_doc.items() if k not in ("number", "footer")} == {
        k: v for k, v in test_doc.items() if k not in ("number", "footer")
    }
    # the mails differ by the number alone
    shown = {"test": "BN-2026-900001", "live": "BN-2026-000001"}
    assert test_mail.text.replace(shown["test"], shown["live"]) == live_mail.text


def test_the_live_series_numbers_exactly_as_before(session, ada, providers):
    """Go-live: with BILLING_LIVE true the stored number, the shown number and the live counter are
    the live series with no offset, and the internal counter is not touched."""
    from app.billing import invoice_number

    buy_plan(session, ada, providers.usd)
    payment = session.scalars(select(Payment)).one()
    session.query(Invoice).delete()
    session.flush()
    live = settings(billing_live=True)
    first = invoices.issue(session, payment, T0, live)
    assert first.number == "BN-2026-000001" and invoice_number.shown(first.number) == first.number
    assert invoices.next_number(session, 2026) == "BN-2026-000002"
    assert session.get(InvoiceCounter, 2026).last_number == 2
    assert session.get(InvoiceCounter, -2026).last_number == 1  # the internal series stayed at 1
