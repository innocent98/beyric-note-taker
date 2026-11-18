"""Cancelling an unfinished checkout (`POST /api/billing/payments/{reference}/cancel`).

A money path, so every answer the provider can give is a test: the page was paid in the meantime
(settle it, never cancel it), the provider says it failed, it says it is unpaid, it cannot be asked,
the payment is someone else's, it is already settled. The provider is a scripted fake and a payer
pays through the real webhook inbox and settlement rules, as in the other billing API tests."""

from datetime import UTC, datetime

import pytest
from sqlalchemy import select

from app.billing import cancel_checkout
from app.billing.types import ProviderError, ProviderTransaction
from app.models import HourGrant, Notification, Payment, Refund, Subscription
from tests.billing_support import P20, make_catalog, pay
from tests.test_billing_profile import API, api_client, error, rows
from tests.test_billing_subscriptions_api import Buyer, buy
from tests.test_web import make_user


@pytest.fixture(autouse=True)
def catalog(session, vault_key):
    make_catalog(session)


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


def start(ada, kind="plan", code="p20", country="US") -> dict:
    ada.put(f"{API}/profile/country", {"billing_country": country})
    out = ada.post(f"{API}/checkout", {"kind": kind, "code": code})
    assert out.status_code == 200, out.text
    return out.json()["payment"]


def stored(session, reference) -> Payment:
    session.expire_all()
    return session.scalars(select(Payment).where(Payment.reference == reference)).one()


def cancel(ada, reference, csrf=True):
    return ada.post(f"{API}/payments/{reference}/cancel", csrf=csrf)


# ---- the provider says the page is unpaid ------------------------------------------------------


def test_an_abandoned_page_is_cancelled_and_the_notice_goes_away(session, ada, providers):
    payment = start(ada)
    providers.usd.complete(stored(session, payment["reference"]), status="pending")  # abandoned

    out = cancel(ada, payment["reference"])

    assert out.status_code == 200, out.text
    body = out.json()
    assert set(body) == {"result", "payment"} and body["result"] == "cancelled"
    assert body["payment"]["reference"] == payment["reference"]
    assert body["payment"]["status"] == "failed" and body["payment"]["pay_url"] is None
    assert stored(session, payment["reference"]).status == "failed"
    assert providers.usd.lookups == [payment["reference"]]  # the provider was asked first
    state = ada.get(f"{API}/subscription").json()
    assert state["pending_payment"] is None and state["state"] == "none"
    assert rows(session, HourGrant) == 0 and rows(session, Subscription) == 0


def test_a_page_the_provider_has_no_record_of_is_not_cancelled(session, ada, providers):
    """A provider that has nothing under our reference gives no answer: changes nothing (the same
    signal as a provider that is down: the two cannot be told apart)."""
    payment = start(ada)

    out = cancel(ada, payment["reference"])

    assert error(out, 502, "provider_error")
    assert stored(session, payment["reference"]).status == "pending"


def test_a_cancelled_checkout_can_be_started_again_with_a_new_payment(session, ada, providers):
    payment = start(ada)
    providers.usd.complete(stored(session, payment["reference"]), status="pending")
    assert cancel(ada, payment["reference"]).status_code == 200

    again = ada.post(f"{API}/checkout", {"kind": "plan", "code": "p20"})

    assert again.status_code == 200
    assert again.json()["payment"]["reference"] != payment["reference"]


# ---- the page was paid in the meantime ---------------------------------------------------------


def test_a_page_paid_in_the_meantime_is_settled_and_never_cancelled(session, ada, providers):
    payment = start(ada)
    # the payer paid in another tab and the webhook has not arrived: only the provider knows
    providers.usd.complete(stored(session, payment["reference"]), card_token="tok-late")

    out = cancel(ada, payment["reference"])

    assert out.status_code == 200, out.text
    body = out.json()
    assert body["result"] == "paid" and body["payment"]["status"] == "succeeded"
    assert stored(session, payment["reference"]).status == "succeeded"
    sub = session.scalars(select(Subscription)).one()  # the plan and its hours are in
    assert sub.plan_code == "p20"
    assert [g.seconds for g in session.scalars(select(HourGrant))] == [P20]
    state = ada.get(f"{API}/subscription").json()
    assert state["state"] == "active" and state["pending_payment"] is None


def test_a_payment_settled_while_the_provider_was_asked_is_not_marked_failed(
    session, ada, providers, monkeypatch
):
    """The webhook settles the payment between our lookup (which said unpaid) and our write: the
    write looks again under the lock and finds it paid."""
    payment = start(ada)
    row = stored(session, payment["reference"])
    unpaid = providers.usd.complete(row, status="pending")
    real_lookup = cancel_checkout.lookup_by_reference

    def lookup_then_the_webhook_lands(adapter, reference):
        found = real_lookup(adapter, reference)  # the provider said: not paid
        assert found.status == "pending"
        pay(session, providers.usd, row, now(), card_token="tok-race")  # then it was paid
        return unpaid

    monkeypatch.setattr(cancel_checkout, "lookup_by_reference", lookup_then_the_webhook_lands)

    out = cancel(ada, payment["reference"])

    assert out.status_code == 200, out.text
    assert out.json()["result"] == "paid"
    assert stored(session, payment["reference"]).status == "succeeded"
    assert [g.seconds for g in session.scalars(select(HourGrant))] == [P20]  # granted once


def test_a_page_paid_wrongly_is_refused_told_to_the_admins_and_left_pending(
    session, ada, providers
):
    make_user(session, "admin@example.test", admin=True)
    payment = start(ada)
    row = stored(session, payment["reference"])
    providers.usd.complete(row, amount_minor=row.amount_minor - 1)  # paid, but too little

    out = cancel(ada, payment["reference"])

    assert error(out, 409, "payment_in_progress")
    assert stored(session, payment["reference"]).status == "pending"
    assert rows(session, HourGrant) == 0
    kinds = {n.kind for n in session.scalars(select(Notification))}
    assert "admin.billing_payment_refused" in kinds


# ---- the provider says it failed ---------------------------------------------------------------


def test_a_page_the_provider_says_failed_is_marked_failed(session, ada, providers):
    payment = start(ada)
    providers.usd.complete(stored(session, payment["reference"]), status="failed")

    out = cancel(ada, payment["reference"])

    assert out.status_code == 200, out.text
    assert out.json()["result"] == "failed"
    assert out.json()["payment"]["status"] == "failed"
    assert stored(session, payment["reference"]).status == "failed"
    assert rows(session, HourGrant) == 0


# ---- the provider cannot be asked --------------------------------------------------------------


def test_a_provider_that_cannot_be_asked_is_a_502_and_changes_nothing(session, ada, providers):
    payment = start(ada)
    providers.usd.complete(stored(session, payment["reference"]), status="pending")
    providers.usd.fetch_error = ProviderError("the provider is down")

    out = cancel(ada, payment["reference"])

    error(out, 502, "provider_error")
    assert stored(session, payment["reference"]).status == "pending"
    assert (
        ada.get(f"{API}/subscription").json()["pending_payment"]["reference"]
        == (payment["reference"])
    )


def test_a_payment_whose_provider_is_not_configured_is_a_502_and_changes_nothing(
    session, ada, providers, monkeypatch
):
    from app.billing.provider import PROVIDER_FACTORIES

    payment = start(ada)
    monkeypatch.delitem(PROVIDER_FACTORIES, "flutterwave")

    error(cancel(ada, payment["reference"]), 502, "provider_error")
    assert stored(session, payment["reference"]).status == "pending"


@pytest.mark.parametrize(
    "wrong", [{"reference": "someone-elses-reference"}, {"amount_minor": 12.5}]
)
def test_a_report_about_another_payment_or_a_malformed_one_is_no_answer(
    session, ada, providers, monkeypatch, wrong
):
    payment = start(ada)
    tx = ProviderTransaction(
        **{
            "provider_tx_id": "tx-odd",
            "reference": payment["reference"],
            "status": "pending",
            "amount_minor": 1800,
            "currency": "USD",
            **wrong,
        }
    )
    monkeypatch.setattr(cancel_checkout, "lookup_by_reference", lambda adapter, reference: tx)

    error(cancel(ada, payment["reference"]), 502, "provider_error")
    assert stored(session, payment["reference"]).status == "pending"


# ---- whose payment, and what kind --------------------------------------------------------------


def test_someone_elses_payment_is_a_plain_404_and_is_not_touched(session, engine, ada, providers):
    payment = start(ada)
    providers.usd.complete(stored(session, payment["reference"]), status="pending")
    make_user(session, "ben@example.test")
    with api_client(engine) as client:
        ben = Buyer(client, "ben@example.test")
        error(cancel(ben, payment["reference"]), 404, "not_found")
    assert stored(session, payment["reference"]).status == "pending"
    assert providers.usd.lookups == []  # the provider was not even asked


def test_an_unknown_reference_is_the_same_404(ada):
    error(cancel(ada, "pay_does_not_exist"), 404, "not_found")


def test_a_renewal_or_an_upgrade_cannot_be_cancelled_here(session, ada, providers):
    payment = start(ada)
    for kind in ("renewal", "upgrade"):
        row = stored(session, payment["reference"])
        row.kind = kind
        session.commit()
        error(cancel(ada, payment["reference"]), 409, "cannot_cancel")
        assert stored(session, payment["reference"]).status == "pending"
    assert providers.usd.lookups == []


def test_billing_off_is_a_404_and_nothing_is_asked(engine, session, providers):
    make_user(session, "ada@example.test")
    with api_client(engine, enabled=False) as off:
        error(cancel(Buyer(off, "ada@example.test"), "pay_any"), 404, "not_found")
    assert providers.usd.lookups == []


def test_cancelling_needs_the_csrf_token(session, ada, providers):
    payment = start(ada)

    out = cancel(ada, payment["reference"], csrf=False)

    assert out.status_code == 403
    assert stored(session, payment["reference"]).status == "pending"


# ---- already settled ---------------------------------------------------------------------------


def test_a_payment_that_is_already_paid_answers_paid_and_changes_nothing(session, ada, providers):
    bought = buy(session, ada, providers)
    reference = bought["payment"]["reference"]
    grants_before = rows(session, HourGrant)

    out = cancel(ada, reference)

    assert out.status_code == 200, out.text
    assert out.json()["result"] == "paid" and out.json()["payment"]["status"] == "succeeded"
    assert stored(session, reference).status == "succeeded"
    assert rows(session, HourGrant) == grants_before
    assert providers.usd.lookups == []  # nothing to ask: it is settled


def test_cancelling_twice_asks_again_and_ends_the_same(session, ada, providers):
    payment = start(ada)
    providers.usd.complete(stored(session, payment["reference"]), status="pending")

    first = cancel(ada, payment["reference"])
    second = cancel(ada, payment["reference"])

    assert (first.json()["result"], second.json()["result"]) == ("cancelled", "cancelled")
    assert stored(session, payment["reference"]).status == "failed"


# ---- a page paid after it was cancelled --------------------------------------------------------


def test_a_cancelled_page_that_is_paid_later_is_still_settled_by_the_webhook(
    session, ada, providers
):
    payment = start(ada)
    row = stored(session, payment["reference"])
    providers.usd.complete(row, status="pending")
    assert cancel(ada, payment["reference"]).json()["result"] == "cancelled"
    assert stored(session, payment["reference"]).status == "failed"

    # the payer pays the old page anyway; the provider's webhook arrives
    pay(session, providers.usd, stored(session, payment["reference"]), now(), card_token="tok-2")

    assert stored(session, payment["reference"]).status == "succeeded"
    assert [g.seconds for g in session.scalars(select(HourGrant))] == [P20]
    state = ada.get(f"{API}/subscription").json()
    assert state["state"] == "active" and state["pending_payment"] is None


# ---- a pack, and a refund in the way -----------------------------------------------------------


def test_a_pack_checkout_can_be_cancelled(session, ada, providers):
    buy(session, ada, providers)
    pack = ada.post(f"{API}/checkout", {"kind": "pack", "code": "pack10"})
    assert pack.status_code == 200, pack.text
    reference = pack.json()["payment"]["reference"]
    providers.usd.complete(stored(session, reference), status="pending")

    out = cancel(ada, reference)

    assert out.status_code == 200, out.text
    assert out.json()["result"] == "cancelled"
    assert stored(session, reference).status == "failed"
    assert ada.get(f"{API}/subscription").json()["pending_payment"] is None
    assert [g.kind for g in session.scalars(select(HourGrant))] == ["plan_period"]


def test_nothing_moves_while_a_refund_of_the_plan_is_being_settled(session, ada, providers):
    bought = buy(session, ada, providers)
    plan_payment = stored(session, bought["payment"]["reference"])
    pack = ada.post(f"{API}/checkout", {"kind": "pack", "code": "pack10"}).json()["payment"]
    providers.usd.complete(stored(session, pack["reference"]), status="pending")
    session.add(
        Refund(
            payment_id=plan_payment.id,
            reference="refund-ref-1",
            reason="first_plan_7d",
            status="pending",
            amount_minor=plan_payment.amount_minor,
            currency=plan_payment.currency,
        )
    )
    session.commit()

    out = cancel(ada, pack["reference"])

    error(out, 409, "refund_pending")
    assert stored(session, pack["reference"]).status == "pending"
    assert providers.usd.lookups == []
