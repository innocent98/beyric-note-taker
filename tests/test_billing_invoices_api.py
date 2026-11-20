"""The invoice routes and the admin refund routes over HTTP (billing stage S9,
`app/billing/invoices_api.py`, `app/billing/admin_api.py`).

The app's own routes with billing on, real Postgres, a scripted provider; a person pays through the
real webhook inbox and settlement rules. The PDF is the real one (the real child process)."""

import json
import shutil
import subprocess
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.billing import invoice_pdf, invoices
from app.models import Invoice, Payment, Refund, User
from tests.billing_support import assert_no_test_wording, make_catalog, pay
from tests.test_billing_profile import API, api_client, error, rows
from tests.test_billing_subscriptions_api import Buyer
from tests.test_web import make_user

ADMIN_API = "/api/admin/billing"


@pytest.fixture(autouse=True)
def catalog(session, vault_key):
    make_catalog(session)


@pytest.fixture(autouse=True)
def process_settings(monkeypatch):
    from tests.billing_support import settings

    monkeypatch.setattr(invoices, "get_settings", lambda: settings(mail_provider="console"))


@pytest.fixture
def web(engine):
    with api_client(engine) as client:
        yield client


@pytest.fixture
def ada(session, web, providers) -> Buyer:
    make_user(session, "ada@example.test")
    return Buyer(web, "ada@example.test")


def now() -> datetime:
    return datetime.now(UTC)


def buy(session, ada, provider, *, country="US", plan="p20"):
    assert ada.put(f"{API}/profile/country", {"billing_country": country}).status_code == 200
    out = ada.post(f"{API}/checkout", {"kind": "plan", "code": plan})
    assert out.status_code == 200, out.text
    payment = session.scalars(
        select(Payment).where(Payment.reference == out.json()["payment"]["reference"])
    ).one()
    pay(session, provider, payment, now(), card_token="tok-api")
    session.expire_all()
    return payment


def second_browser(session, engine, email="ben@example.test", *, admin=False):
    make_user(session, email, admin=admin)
    client = api_client(engine).__enter__()
    return Buyer(client, email)


# ---- the person's invoices ----------------------------------------------------------------------


def test_with_billing_off_the_invoice_routes_are_a_404(engine, session, providers):
    make_user(session, "ada@example.test")
    with api_client(engine, enabled=False) as off:
        ada = Buyer(off, "ada@example.test")
        error(ada.get(f"{API}/invoices"), 404, "not_found")
        error(ada.get(f"{API}/invoices/BN-2026-000001/pdf"), 404, "not_found")


def test_an_account_not_on_the_allow_list_gets_the_same_404(engine, session, providers):
    make_user(session, "ada@example.test")
    with api_client(engine, billing_allowed_emails="someone-else@example.test") as closed:
        ada = Buyer(closed, "ada@example.test")
        error(ada.get(f"{API}/invoices"), 404, "not_found")
        error(ada.get(f"{API}/invoices/BN-2026-000001/pdf"), 404, "not_found")


def test_a_visitor_is_refused_first(web, ada):
    with TestClient(web.app, follow_redirects=False) as visitor:
        error(visitor.get(f"{API}/invoices"), 401, "not_authenticated")
        error(visitor.get(f"{API}/invoices/BN-2026-000001/pdf"), 401, "not_authenticated")


def test_a_person_lists_their_own_invoices_newest_first(session, ada, providers):
    assert ada.get(f"{API}/invoices").json() == {"invoices": []}
    payment = buy(session, ada, providers.usd)
    body = ada.get(f"{API}/invoices").json()
    [invoice] = body["invoices"]
    assert invoice["number"] == "BN-2026-900001"  # stored `BN-T-2026-000001`, shown without the T
    assert invoice["currency"] == "USD" and invoice["total_minor"] == 1800
    assert (invoice["subtotal_minor"], invoice["vat_minor"], invoice["vat_rate_bp"]) == (1800, 0, 0)
    assert invoice["description"] == "Twenty"
    assert invoice["payment_reference"] == payment.reference
    assert invoice["payment_status"] == "succeeded" and invoice["refunded_at"] is None
    assert "test_mode" not in invoice  # internal: never returned
    assert_no_test_wording(json.dumps(body))
    # the billing history points at it
    [row] = ada.get(f"{API}/payments").json()["payments"]
    assert row["invoice_number"] == invoice["number"]
    assert (
        ada.get(f"{API}/payments/{payment.reference}").json()["invoice_number"]
        == (invoice["number"])
    )


def test_nobody_sees_or_downloads_another_persons_invoice(session, engine, ada, providers):
    buy(session, ada, providers.usd)
    number = ada.get(f"{API}/invoices").json()["invoices"][0]["number"]
    ben = second_browser(session, engine)
    assert ben.get(f"{API}/invoices").json() == {"invoices": []}
    error(ben.get(f"{API}/invoices/{number}/pdf"), 404, "not_found")  # exists, but not his
    error(ben.get(f"{API}/invoices/BN-2026-999999/pdf"), 404, "not_found")  # does not exist
    # a number that is not even shaped like one never reaches the database
    for bad in ("1", "bn-2026-000001", "BN-2026-1", "BN-2026-000001%20", "..%2F..%2Fetc"):
        error(ben.get(f"{API}/invoices/{bad}/pdf"), 404, "not_found")


def test_the_pdf_downloads_as_a_real_file_with_what_the_invoice_says(session, ada, providers):
    payment = buy(session, ada, providers.usd)
    number = ada.get(f"{API}/invoices").json()["invoices"][0]["number"]

    response = ada.get(f"{API}/invoices/{number}/pdf")

    assert response.status_code == 200
    assert response.headers["content-type"] == "application/pdf"
    assert response.headers["content-disposition"].startswith(
        f'attachment; filename="invoice-{number}.pdf"'
    )
    assert response.headers["cache-control"] == "no-store"
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.content.startswith(b"%PDF-") and len(response.content) > 5000
    if shutil.which("pdftotext"):
        text = subprocess.run(
            ["pdftotext", "-layout", "-", "-"], input=response.content, capture_output=True
        ).stdout.decode()
        flat = " ".join(text.split())
        for wanted in (number, "USD 18.00", payment.reference, "Twenty"):
            assert wanted in flat, wanted


def test_a_busy_or_failing_renderer_answers_with_an_error_not_a_broken_file(
    session, ada, providers, monkeypatch
):
    buy(session, ada, providers.usd)
    number = ada.get(f"{API}/invoices").json()["invoices"][0]["number"]

    def busy(doc):
        raise invoice_pdf.InvoiceBusy()

    monkeypatch.setattr(invoice_pdf, "build_pdf", busy)
    error(ada.get(f"{API}/invoices/{number}/pdf"), 503, "invoice_busy")

    def broken(doc):
        raise invoice_pdf.InvoiceFailed("x")

    monkeypatch.setattr(invoice_pdf, "build_pdf", broken)
    error(ada.get(f"{API}/invoices/{number}/pdf"), 500, "invoice_failed")


# ---- the admin routes ----


def test_the_admin_routes_are_dark_with_billing_off_and_admin_only(engine, session, providers):
    make_user(session, "root@example.test", admin=True)
    make_user(session, "ada@example.test")
    with api_client(engine, enabled=False) as off:
        root = Buyer(off, "root@example.test")
        error(root.get(f"{ADMIN_API}/payments"), 404, "not_found")
        error(root.get(f"{ADMIN_API}/refunds"), 404, "not_found")
        error(
            root.post(f"{ADMIN_API}/payments/1/refund", {"reason": "double_charge"}),
            404,
            "not_found",
        )
        error(root.post(f"{ADMIN_API}/refunds/1/resolve", {"outcome": "made"}), 404, "not_found")
    with api_client(engine) as on:
        with TestClient(on.app, follow_redirects=False) as visitor:
            error(visitor.get(f"{ADMIN_API}/payments"), 401, "not_authenticated")
        ada = Buyer(on, "ada@example.test")
        for method, url, body in [
            ("get", f"{ADMIN_API}/payments", None),
            ("get", f"{ADMIN_API}/refunds", None),
            ("post", f"{ADMIN_API}/payments/1/refund", {"reason": "double_charge"}),
            ("post", f"{ADMIN_API}/refunds/1/resolve", {"outcome": "made"}),
        ]:
            response = ada.get(url) if method == "get" else ada.post(url, body)
            error(response, 403, "forbidden")  # a signed-in person who is not an admin


def test_a_missing_csrf_token_is_refused_on_the_admin_writes(session, engine, providers):
    root = second_browser(session, engine, "root@example.test", admin=True)
    error(
        root.post(f"{ADMIN_API}/payments/1/refund", {"reason": "double_charge"}, csrf=False),
        403,
        "csrf_rejected",
    )
    error(
        root.post(f"{ADMIN_API}/refunds/1/resolve", {"outcome": "made"}, csrf=False),
        403,
        "csrf_rejected",
    )


@pytest.fixture
def root(session, engine):
    return second_browser(session, engine, "root@example.test", admin=True)


def test_the_admin_sees_each_payment_and_whether_it_may_be_refunded(session, ada, root, providers):
    payment = buy(session, ada, providers.usd)
    body = root.get(f"{ADMIN_API}/payments").json()
    [row] = body["payments"]
    assert row["id"] == payment.id and row["payer_email"] == "ada@example.test"
    assert row["status"] == "succeeded" and row["invoice_number"] == "BN-2026-900001"
    assert_no_test_wording(json.dumps(body))
    assert row["refund"] is None and row["needs_refund_reason"] is None
    assert row["refund_option"] == {"reason": "first_plan_7d", "allowed": True, "message": None}
    # after the window the same payment says why not
    session.query(Payment).filter(Payment.id == payment.id).update(
        {"paid_at": now() - timedelta(days=8)}
    )
    session.commit()
    option = root.get(f"{ADMIN_API}/payments").json()["payments"][0]["refund_option"]
    assert option["allowed"] is False and "window" in option["message"]
    # only=needs_refund lists nothing here
    assert root.get(f"{ADMIN_API}/payments?only=needs_refund").json() == {"payments": []}


def test_the_admin_refunds_a_payment_and_the_person_sees_it(session, ada, root, providers):
    payment = buy(session, ada, providers.usd)

    response = root.post(
        f"{ADMIN_API}/payments/{payment.id}/refund",
        {"reason": "first_plan_7d", "note": "asked by email"},
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["status"] == "submitted" and body["reason"] == "first_plan_7d"
    assert (body["amount_minor"], body["currency"]) == (1800, "USD")
    assert body["payer_email"] == "ada@example.test" and body["note"] == "asked by email"
    assert body["reference"].startswith("bnrf-") and body["needs_resolution"] is False
    assert providers.usd.refund_calls == [(payment.provider_tx_id, 1800, body["reference"])]
    # the person's own screens: the payment and the invoice say refunded
    [row] = ada.get(f"{API}/payments").json()["payments"]
    assert row["status"] == "refunded"
    [invoice] = ada.get(f"{API}/invoices").json()["invoices"]
    assert invoice["payment_status"] == "refunded" and invoice["refunded_at"] is not None
    assert ada.get(f"{API}/subscription").json()["state"] == "none"
    # the admin's own lists
    refunded = root.get(f"{ADMIN_API}/payments").json()["payments"][0]
    assert refunded["status"] == "refunded" and refunded["refund"]["status"] == "submitted"
    assert refunded["refund_option"] is None
    [listed] = root.get(f"{ADMIN_API}/refunds").json()["refunds"]
    assert listed["id"] == body["id"] and listed["payment_id"] == payment.id
    # and the refunded invoice's PDF says so
    number = invoice["number"]
    pdf = ada.get(f"{API}/invoices/{number}/pdf")
    assert pdf.status_code == 200 and pdf.content.startswith(b"%PDF-")


def test_a_refused_refund_is_a_409_with_a_code_and_a_message(session, ada, root, providers):
    payment = buy(session, ada, providers.usd)
    wrong = root.post(f"{ADMIN_API}/payments/{payment.id}/refund", {"reason": "pack_14d"})
    assert error(wrong, 409, "wrong_kind")["message"] == "This is not a pack payment."
    error(
        root.post(f"{ADMIN_API}/payments/{payment.id}/refund", {"reason": "double_charge"}),
        409,
        "not_a_double_charge",
    )
    error(
        root.post(f"{ADMIN_API}/payments/987654/refund", {"reason": "double_charge"}),
        404,
        "not_found",
    )
    assert providers.usd.refund_calls == []
    assert rows(session, Refund) == 0


@pytest.mark.parametrize(
    "body",
    [
        {},
        {"reason": "because"},
        {"reason": "double_charge", "amount_minor": 1},  # an amount is never taken from the request
        {"reason": "double_charge", "note": "x" * 501},
        {"reason": 5},
    ],
)
def test_a_bad_refund_request_is_a_422_and_writes_nothing(session, ada, root, providers, body):
    payment = buy(session, ada, providers.usd)
    error(root.post(f"{ADMIN_API}/payments/{payment.id}/refund", body), 422, "validation_error")
    assert rows(session, Refund) == 0 and providers.usd.refund_calls == []


def test_an_unknown_outcome_is_listed_for_a_person_and_resolved_from_the_admin_route(
    session, ada, root, providers
):
    from app.billing.types import OutcomeUnknown

    payment = buy(session, ada, providers.usd)
    providers.usd.refund_error = OutcomeUnknown("timed out")
    body = root.post(
        f"{ADMIN_API}/payments/{payment.id}/refund", {"reason": "first_plan_7d"}
    ).json()
    assert body["status"] == "unknown" and body["needs_resolution"] is True
    assert body["error"] == "no_answer"
    # nothing asks again, from either route
    providers.usd.refund_error = None
    error(
        root.post(f"{ADMIN_API}/payments/{payment.id}/refund", {"reason": "first_plan_7d"}),
        409,
        "refund_in_progress",
    )
    assert len(providers.usd.refund_calls) == 1

    done = root.post(f"{ADMIN_API}/refunds/{body['id']}/resolve", {"outcome": "not_made"}).json()
    assert done["status"] == "failed" and done["needs_resolution"] is False
    error(
        root.post(f"{ADMIN_API}/refunds/{body['id']}/resolve", {"outcome": "made"}),
        409,
        "not_unresolved",
    )
    error(root.post(f"{ADMIN_API}/refunds/4242/resolve", {"outcome": "made"}), 404, "not_found")
    error(
        root.post(f"{ADMIN_API}/refunds/{body['id']}/resolve", {"outcome": "maybe"}),
        422,
        "validation_error",
    )


def test_a_double_charge_shows_in_the_needs_refund_list_and_is_refunded(
    session, ada, root, providers
):
    from app.billing.payments import new_payment
    from app.models import PlanPrice

    buy(session, ada, providers.usd)
    user = session.scalars(select(User).where(User.email == "ada@example.test")).one()
    second = new_payment(
        session, payer=user, provider="flutterwave", kind="plan", currency="USD", amount_minor=1800
    )
    second.plan_price_id = session.scalar(
        select(PlanPrice.id).where(PlanPrice.plan_code == "p20", PlanPrice.currency == "USD")
    )
    session.commit()
    pay(session, providers.usd, second, now() + timedelta(minutes=5))
    listed = root.get(f"{ADMIN_API}/payments?only=needs_refund").json()["payments"]
    assert [p["id"] for p in listed] == [second.id]
    assert listed[0]["refund_option"]["reason"] == "double_charge"
    assert listed[0]["refund_option"]["allowed"] is True and listed[0]["invoice_number"] is None

    out = root.post(f"{ADMIN_API}/payments/{second.id}/refund", {"reason": "double_charge"}).json()
    assert out["status"] == "submitted"
    assert ada.get(f"{API}/subscription").json()["state"] == "active"  # the real plan is intact


def test_an_invoice_made_before_this_change_still_lists_and_downloads(session, ada, providers):
    """Rows made earlier keep their stored `BN-T-` number (nothing is rewritten). They are listed
    and downloaded under the shown number, and the old spelling is not a number any more."""
    buy(session, ada, providers.usd)
    stored = session.scalars(select(Invoice)).one().number
    assert stored == "BN-T-2026-000001"
    [invoice] = ada.get(f"{API}/invoices").json()["invoices"]
    assert invoice["number"] == "BN-2026-900001"
    got = ada.get(f"{API}/invoices/BN-2026-900001/pdf")
    assert got.status_code == 200 and got.content.startswith(b"%PDF-")
    assert 'filename="invoice-BN-2026-900001.pdf"' in got.headers["content-disposition"]
    error(ada.get(f"{API}/invoices/BN-T-2026-000001/pdf"), 404, "not_found")
    if shutil.which("pdftotext"):
        text = subprocess.run(
            ["pdftotext", "-layout", "-", "-"], input=got.content, capture_output=True
        ).stdout.decode()
        assert_no_test_wording(text)


def test_a_live_invoice_is_listed_and_downloaded_under_its_stored_number(
    session, ada, providers, monkeypatch
):
    payment = buy(session, ada, providers.usd)
    session.query(Invoice).delete()
    session.flush()
    from tests.billing_support import settings

    live = invoices.issue(session, payment, now(), settings(billing_live=True))
    session.commit()
    assert live.number == "BN-2026-000001"
    [invoice] = ada.get(f"{API}/invoices").json()["invoices"]
    assert invoice["number"] == "BN-2026-000001" and "test_mode" not in invoice
    assert ada.get(f"{API}/invoices/BN-2026-000001/pdf").status_code == 200
