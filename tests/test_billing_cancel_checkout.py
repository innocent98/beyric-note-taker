"""Cancelling an unfinished checkout (`POST /api/billing/payments/{reference}/cancel`).

A money path, so every answer the provider can give is a test: the page was paid in the meantime
(settle it, never cancel it), the provider says it failed, it says it is unpaid, it cannot be asked,
the payment is someone else's, it is already settled. The provider is a scripted fake and a payer
pays through the real webhook inbox and settlement rules, as in the other billing API tests."""

import threading
import time
from datetime import UTC, datetime, timedelta

import httpx
import pytest
from sqlalchemy import select
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from app.billing import cancel_checkout, renewals, settle
from app.billing.types import NoRecord, ProviderError, ProviderTransaction
from app.models import HourGrant, Notification, Payment, Refund, Subscription
from tests.billing_support import P20, make_catalog, pay, settings
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


# ---- a provider with no record of the page (Flutterwave makes one only when the payer tries) ----


def test_a_page_the_provider_has_no_record_of_is_unpaid_and_is_cancelled(session, ada, providers):
    """`NoRecord`: the provider is reachable and has nothing under our reference (Flutterwave for a
    hosted page nobody opened). Nobody paid it, so it is closed: safe, because a `failed` payment
    is still settled if the page is paid later."""
    payment = start(ada)
    providers.usd.fetch_error = NoRecord("no transaction was found")

    out = cancel(ada, payment["reference"])

    assert out.status_code == 200, out.text
    assert out.json()["result"] == "cancelled" and out.json()["payment"]["status"] == "failed"
    assert stored(session, payment["reference"]).status == "failed"
    assert ada.get(f"{API}/subscription").json()["pending_payment"] is None


def test_the_flutterwave_adapter_answering_no_transaction_cancels_the_page(
    session, ada, providers, monkeypatch
):
    """The real Flutterwave adapter on a scripted wire: HTTP 400 "no transaction" is `NoRecord`."""
    from app.billing.flutterwave import FlutterwaveProvider
    from app.billing.provider import PROVIDER_FACTORIES
    from tests.test_billing_flutterwave import NO_TRANSACTION, Wire, reply
    from tests.test_billing_flutterwave import settings as flutterwave_settings

    payment = start(ada)
    wire = Wire(reply(NO_TRANSACTION, 400))
    real = FlutterwaveProvider(flutterwave_settings(), transport=httpx.MockTransport(wire))
    monkeypatch.setitem(PROVIDER_FACTORIES, "flutterwave", lambda s: real)

    out = cancel(ada, payment["reference"])

    assert out.status_code == 200, out.text
    assert out.json()["result"] == "cancelled"
    assert wire.sent == [("GET", "/v3/transactions/verify_by_reference")]
    assert stored(session, payment["reference"]).status == "failed"


def test_the_flutterwave_adapter_being_refused_its_key_is_still_a_502(
    session, ada, providers, monkeypatch
):
    from app.billing.flutterwave import FlutterwaveProvider
    from app.billing.provider import PROVIDER_FACTORIES
    from tests.test_billing_flutterwave import Wire, reply
    from tests.test_billing_flutterwave import settings as flutterwave_settings

    payment = start(ada)
    wire = Wire(reply('{"status":"error","message":"bad key"}', 401))
    real = FlutterwaveProvider(flutterwave_settings(), transport=httpx.MockTransport(wire))
    monkeypatch.setitem(PROVIDER_FACTORIES, "flutterwave", lambda s: real)

    error(cancel(ada, payment["reference"]), 502, "provider_error")
    assert stored(session, payment["reference"]).status == "pending"


# ---- a grant that fails, and a refund that starts while the provider answers --------------------


def test_a_grant_handler_that_fails_is_a_409_in_the_modules_shape_and_changes_nothing(
    session, ada, providers, monkeypatch
):
    payment = start(ada)
    providers.usd.complete(stored(session, payment["reference"]), card_token="tok-boom")

    def boom(db, row):
        raise RuntimeError("a grant failed")

    monkeypatch.setattr(settle, "GRANT_HANDLERS", [*settle.GRANT_HANDLERS, boom])

    out = cancel(ada, payment["reference"])

    assert error(out, 409, "payment_in_progress")
    assert stored(session, payment["reference"]).status == "pending"  # undone: nothing was kept
    assert rows(session, HourGrant) == 0 and rows(session, Subscription) == 0


def test_a_paid_page_whose_grant_fails_tells_the_admins_once_and_still_answers_409(
    session, ada, providers, monkeypatch
):
    make_user(session, "admin@example.test", admin=True)
    payment = start(ada)
    row = stored(session, payment["reference"])
    providers.usd.complete(row, card_token="tok-boom")

    def boom(db, row):
        raise RuntimeError("secret provider text ada@example.test 1999")

    monkeypatch.setattr(settle, "GRANT_HANDLERS", [*settle.GRANT_HANDLERS, boom])
    key = f"admin.billing_cancel_grant_failed:{row.id}"

    for _ in range(2):  # the second cancel must not make a second alert
        out = cancel(ada, payment["reference"])
        assert error(out, 409, "payment_in_progress")

    session.expire_all()
    alerts = list(session.scalars(select(Notification).where(Notification.dedupe_key == key)))
    assert len(alerts) == 1 and alerts[0].kind == "admin.billing_cancel_grant_failed"
    assert str(row.id) in alerts[0].body and "RuntimeError" in alerts[0].body
    for leaked in ("secret", "ada@example.test", "1999"):
        assert leaked not in alerts[0].body and leaked not in alerts[0].title
    assert stored(session, payment["reference"]).status == "pending"
    assert rows(session, HourGrant) == 0 and rows(session, Subscription) == 0


def test_a_grant_failure_whose_alert_cannot_be_saved_is_still_the_same_409(
    session, ada, providers, monkeypatch
):
    make_user(session, "admin@example.test", admin=True)
    payment = start(ada)
    providers.usd.complete(stored(session, payment["reference"]), card_token="tok-boom")
    failed = {"grant": False, "commit": 0}

    def boom(db, row):
        failed["grant"] = True
        raise RuntimeError("a grant failed")

    real_commit = Session.commit

    def commit_fails_once_after_the_grant(self):
        if failed["grant"] and not failed["commit"]:  # the helper's commit of the alert
            failed["commit"] += 1
            raise OperationalError("COMMIT", {}, Exception("connection lost"))
        return real_commit(self)

    monkeypatch.setattr(settle, "GRANT_HANDLERS", [*settle.GRANT_HANDLERS, boom])
    monkeypatch.setattr(Session, "commit", commit_fails_once_after_the_grant)

    assert error(cancel(ada, payment["reference"]), 409, "payment_in_progress")
    assert failed["commit"] == 1  # the commit guard really ran
    assert stored(session, payment["reference"]).status == "pending"
    assert rows(session, HourGrant) == 0 and rows(session, Notification) == 0


def test_a_page_the_provider_says_failed_does_not_alert_when_a_handler_fails(
    session, ada, providers, monkeypatch
):
    make_user(session, "admin@example.test", admin=True)
    payment = start(ada)
    providers.usd.complete(stored(session, payment["reference"]), status="failed")

    def boom(*args, **kwargs):
        raise RuntimeError("settling failed")

    monkeypatch.setattr(settle, "settle_transaction", boom)

    assert error(cancel(ada, payment["reference"]), 409, "payment_in_progress")
    assert rows(session, Notification) == 0  # nothing was paid: nothing for an admin to chase


def test_a_refund_that_starts_while_the_provider_answers_stops_a_pack_being_closed(
    session, ada, providers, monkeypatch
):
    bought = buy(session, ada, providers)
    plan_payment = stored(session, bought["payment"]["reference"])
    pack = ada.post(f"{API}/checkout", {"kind": "pack", "code": "pack10"}).json()["payment"]
    unpaid = providers.usd.complete(stored(session, pack["reference"]), status="pending")
    real_lookup = cancel_checkout.lookup_by_reference

    def lookup_then_a_refund_starts(adapter, reference):
        real_lookup(adapter, reference)
        session.add(
            Refund(
                payment_id=plan_payment.id,
                reference="refund-ref-race",
                reason="first_plan_7d",
                status="pending",
                amount_minor=plan_payment.amount_minor,
                currency=plan_payment.currency,
            )
        )
        session.commit()
        return unpaid

    monkeypatch.setattr(cancel_checkout, "lookup_by_reference", lookup_then_a_refund_starts)

    error(cancel(ada, pack["reference"]), 409, "refund_pending")
    assert stored(session, pack["reference"]).status == "pending"


# ---- real lock contention: a second connection settles while the cancel waits for the row ------


def test_a_webhook_holding_the_row_makes_the_cancel_wait_and_then_answer_paid(
    session, ada, providers, monkeypatch
):
    """Two connections, two threads. Our test session holds the payment row's lock (as a webhook
    settling it does). The cancel's provider lookup says "unpaid", then its write BLOCKS on the
    lock; the webhook then commits the payment as paid and releases it; the cancel wakes, reads the
    row again and answers `paid`: it never marks a paid payment failed."""
    payment = start(ada)
    row = stored(session, payment["reference"])
    unpaid = providers.usd.complete(row, status="pending")
    looked_up = threading.Event()
    real_lookup = cancel_checkout.lookup_by_reference

    def lookup(adapter, reference):
        real_lookup(adapter, reference)
        looked_up.set()
        return unpaid

    monkeypatch.setattr(cancel_checkout, "lookup_by_reference", lookup)
    session.scalars(select(Payment).where(Payment.id == row.id).with_for_update()).one()  # held
    answers = []
    worker = threading.Thread(target=lambda: answers.append(cancel(ada, payment["reference"])))
    worker.start()
    assert looked_up.wait(10)
    time.sleep(0.7)  # the cancel is now waiting on the row lock
    assert worker.is_alive() and answers == []

    # The settlement the webhook and the sweeper make, in ONE transaction that holds the row until
    # it commits (`pay` through the inbox commits the event row first, which frees the row early).
    paid = providers.usd.complete(row, card_token="tok-contended", payer_email="p@example.test")
    assert settle.settle_transaction(session, providers.usd, row.id, paid, now()) is None
    session.commit()  # releases the lock
    worker.join(15)

    assert not worker.is_alive()
    assert answers[0].status_code == 200, answers[0].text
    assert answers[0].json()["result"] == "paid"
    assert stored(session, payment["reference"]).status == "succeeded"
    assert [g.seconds for g in session.scalars(select(HourGrant))] == [P20]  # granted once


# ---- the sweeper also asks about a cancelled page, so a lost webhook is not a lost payment -------


def cancelled_page(session, ada, providers):
    """A plan checkout the person cancelled: `failed`, with its page."""
    payment = start(ada)
    providers.usd.complete(stored(session, payment["reference"]), status="pending")
    assert cancel(ada, payment["reference"]).json()["result"] == "cancelled"
    return stored(session, payment["reference"])


def test_the_sweeper_settles_a_cancelled_page_that_was_paid_later_with_a_lost_webhook(
    session, ada, providers
):
    row = cancelled_page(session, ada, providers)
    providers.usd.complete(row, card_token="tok-late-sweep", payer_email="payer@example.test")
    # no webhook: the provider's retries ran out. The sweeper asks about the failed page.

    renewals.sweep(session, row.created_at + timedelta(hours=1), settings())

    session.expire_all()
    assert stored(session, row.reference).status == "succeeded"
    assert [g.seconds for g in session.scalars(select(HourGrant))] == [P20]


def test_the_sweeper_leaves_a_cancelled_page_alone_while_the_provider_says_unpaid(
    session, ada, providers
):
    row = cancelled_page(session, ada, providers)
    lookups = len(providers.usd.lookups)

    renewals.sweep(session, row.created_at + timedelta(hours=1), settings())

    assert len(providers.usd.lookups) == lookups + 1  # it asked
    assert stored(session, row.reference).status == "failed" and rows(session, HourGrant) == 0
    # and is not asked again inside the half hour back-off
    renewals.sweep(session, row.created_at + timedelta(hours=1, minutes=10), settings())
    assert len(providers.usd.lookups) == lookups + 1


@pytest.mark.parametrize(
    ("change", "age"),
    [
        ({"kind": "renewal"}, timedelta(hours=1)),  # not a plan or pack checkout
        ({"pay_url": None}, timedelta(hours=1)),  # no page was ever made
        ({}, timedelta(days=8)),  # older than the window
    ],
)
def test_the_sweeper_does_not_touch_other_failed_payments(session, ada, providers, change, age):
    row = cancelled_page(session, ada, providers)
    if change:
        session.execute(Payment.__table__.update().where(Payment.id == row.id).values(**change))
        session.commit()
    providers.usd.complete(
        stored(session, row.reference), card_token="tok-other", payer_email="payer@example.test"
    )
    lookups = len(providers.usd.lookups)

    renewals.sweep(session, row.created_at + age, settings())

    assert len(providers.usd.lookups) == lookups  # never asked
    assert stored(session, row.reference).status == "failed"
