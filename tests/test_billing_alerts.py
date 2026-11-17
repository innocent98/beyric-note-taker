"""What billing tells the admins in stage S9 (`settle.py`, `renewals.sweep`): a second charge for a
payment that is already paid, a successful charge the rules refused, a dispute, payments that have
waited a day, and the sweeper's invoice and refund checks. Always by ids, never an email.

Real Postgres; the provider is scripted; events go through the REAL inbox and settlement rules.
"""

from datetime import timedelta

import pytest
from sqlalchemy import func, select

from app.billing import renewals
from app.billing import subscriptions as subs
from app.billing.types import ProviderError, ProviderTransaction
from app.models import Invoice, Notification, Payment
from tests.billing_support import (  # noqa: F401  (fixtures used by name)
    T0,
    buy_plan,
    choose_country,
    deliver,
    make_catalog,
    settings,
)
from tests.test_web import make_user


@pytest.fixture(autouse=True)
def catalog(session, vault_key):
    make_catalog(session)


@pytest.fixture
def ada(session):
    return make_user(session, "ada@example.test")


@pytest.fixture
def admin(session):
    return make_user(session, "admin@example.test", admin=True)


def alerts(session, kind) -> list[Notification]:
    session.expire_all()
    return list(session.scalars(select(Notification).where(Notification.kind == kind)))


def second_charge(provider, payment, tx_id="fx-second", **over):
    tx = provider.add_transaction(
        ProviderTransaction(
            provider_tx_id=tx_id,
            reference=payment.reference,
            status=over.pop("status", "successful"),
            amount_minor=over.pop("amount_minor", payment.amount_minor),
            currency=over.pop("currency", payment.currency),
        )
    )
    return provider.event_body(provider.event_for(tx))


# ---- a second payment on an already paid reference ----------------------------------------


def test_a_second_successful_charge_under_another_transaction_id_tells_the_admins(
    session, ada, admin, providers
):
    buy_plan(session, ada, providers.usd)
    payment = session.scalars(select(Payment)).one()

    deliver(session, providers.usd, second_charge(providers.usd, payment), T0 + timedelta(hours=1))

    [alert] = alerts(session, "admin.billing_second_payment")
    assert f"Payment {payment.id}" in alert.body and "fx-second" in alert.body
    assert "ada@example.test" not in alert.body  # ids only, never an email
    session.expire_all()
    assert session.get(Payment, payment.id).status == "succeeded"  # nothing else changed
    assert session.get(Payment, payment.id).provider_tx_id != "fx-second"


def test_the_same_transaction_again_is_not_a_second_payment(session, ada, admin, providers):
    buy_plan(session, ada, providers.usd)
    payment = session.scalars(select(Payment)).one()
    tx = providers.usd.transactions[payment.provider_tx_id]
    deliver(
        session,
        providers.usd,
        providers.usd.event_body(providers.usd.event_for(tx, kind="charge_succeeded")),
        T0 + timedelta(hours=1),
    )
    assert alerts(session, "admin.billing_second_payment") == []


def test_a_second_charge_the_provider_does_not_confirm_is_no_alert(session, ada, admin, providers):
    buy_plan(session, ada, providers.usd)
    payment = session.scalars(select(Payment)).one()
    body = second_charge(providers.usd, payment, tx_id="fx-failed", status="failed")
    deliver(session, providers.usd, body, T0 + timedelta(hours=1))
    assert alerts(session, "admin.billing_second_payment") == []


def test_a_second_charge_alert_is_made_once_however_often_the_event_comes(
    session, ada, admin, providers
):
    buy_plan(session, ada, providers.usd)
    payment = session.scalars(select(Payment)).one()
    body = second_charge(providers.usd, payment)
    deliver(session, providers.usd, body, T0 + timedelta(hours=1))
    deliver(session, providers.usd, body, T0 + timedelta(hours=2))
    assert len(alerts(session, "admin.billing_second_payment")) == 1


def test_a_provider_that_cannot_be_asked_parks_the_event_instead_of_dropping_the_alert(
    session, ada, admin, providers
):
    from app.billing import inbox, settle
    from app.billing.types import RetryLater

    buy_plan(session, ada, providers.usd)
    payment = session.scalars(select(Payment)).one()
    body = second_charge(providers.usd, payment)
    providers.usd.fetch_error = ProviderError("down")
    row, event = inbox.receive(
        session, providers.usd, providers.usd.signed_headers(body), body, T0 + timedelta(hours=1)
    )
    with pytest.raises(RetryLater):
        settle.process(session, providers.usd, row, event, T0 + timedelta(hours=1))
    assert alerts(session, "admin.billing_second_payment") == []
    session.expire_all()
    assert row.processed_at is None  # waiting: the provider sends it again, or the sweeper does

    providers.usd.fetch_error = None
    settle.process(session, providers.usd, row, event, T0 + timedelta(hours=2))
    assert len(alerts(session, "admin.billing_second_payment")) == 1


# ---- a successful charge the rules refused -------------------------------------------------


def test_a_successful_charge_the_rules_refuse_tells_the_admins(session, ada, admin, providers):
    choose_country(session, ada, "US")
    started = subs.start_plan_checkout(session, ada, "p20", settings(), T0)
    tx = providers.usd.complete(started.payment, amount_minor=900)  # half of what we asked
    body = providers.usd.event_body(providers.usd.event_for(tx))
    deliver(session, providers.usd, body, T0)

    session.expire_all()
    assert session.scalars(select(Payment)).one().status == "pending"  # nothing granted
    [alert] = alerts(session, "admin.billing_charge_refused")
    assert "amount too low" in alert.body and "Money may have moved" in alert.body


def test_a_declined_payment_or_an_unknown_reference_is_not_an_alert(session, ada, admin, providers):
    choose_country(session, ada, "US")
    started = subs.start_plan_checkout(session, ada, "p20", settings(), T0)
    declined = providers.usd.complete(started.payment, status="failed")
    deliver(session, providers.usd, providers.usd.event_body(providers.usd.event_for(declined)), T0)
    stranger = providers.usd.add_transaction(
        ProviderTransaction(
            provider_tx_id="fx-x",
            reference="bn-0-someone-elses",
            status="successful",
            amount_minor=100,
            currency="USD",
        )
    )
    deliver(session, providers.usd, providers.usd.event_body(providers.usd.event_for(stranger)), T0)
    assert alerts(session, "admin.billing_charge_refused") == []


# ---- a dispute and a refund notice ------------------------------------------------------------


def test_a_dispute_tells_the_admins_and_a_refund_notice_does_not(session, ada, admin, providers):
    buy_plan(session, ada, providers.usd)
    payment = session.scalars(select(Payment)).one()
    tx = providers.usd.transactions[payment.provider_tx_id]

    deliver(
        session,
        providers.usd,
        providers.usd.event_body(providers.usd.event_for(tx, kind="refund")),
        T0 + timedelta(days=1),
    )
    assert alerts(session, "admin.billing_dispute") == []
    deliver(
        session,
        providers.usd,
        providers.usd.event_body(providers.usd.event_for(tx, kind="dispute")),
        T0 + timedelta(days=1),
    )
    [alert] = alerts(session, "admin.billing_dispute")
    assert f"payment {payment.id}" in alert.body and "ada@example.test" not in alert.body


# ---- the sweeper's S9 checks --------------------------------------------------------------


def test_payments_that_waited_more_than_a_day_are_named_once_a_day(session, ada, admin, providers):
    choose_country(session, ada, "US")
    old = subs.start_plan_checkout(session, ada, "p20", settings(), T0).payment
    old_id = old.id
    session.query(Payment).filter(Payment.id == old_id).update({"created_at": T0})
    session.commit()

    # not yet a day old: nothing
    renewals.sweep(session, T0 + timedelta(hours=23), settings())
    assert alerts(session, "admin.billing_pending_old") == []
    # a day and an hour old: one digest, however many passes run that day
    renewals.sweep(session, T0 + timedelta(hours=25), settings())
    renewals.sweep(session, T0 + timedelta(hours=26), settings())
    [alert] = alerts(session, "admin.billing_pending_old")
    assert str(old_id) in alert.body and "1 payment" in alert.body
    # two days and more: it has left the band, so it is not named for ever
    renewals.sweep(session, T0 + timedelta(hours=49), settings())
    assert len(alerts(session, "admin.billing_pending_old")) == 1


def test_the_sweeper_issues_an_invoice_that_is_missing(session, ada, admin, providers):
    buy_plan(session, ada, providers.usd)
    session.query(Invoice).delete()
    session.commit()
    renewals.sweep(session, T0 + timedelta(minutes=10), settings())
    assert session.scalar(select(func.count()).select_from(Invoice)) == 1


def test_one_failing_sweep_step_does_not_stop_the_others(
    session, ada, admin, providers, monkeypatch
):
    buy_plan(session, ada, providers.usd)
    session.query(Invoice).delete()
    session.commit()

    def broken(session, now):
        raise RuntimeError("boom")

    monkeypatch.setattr(renewals, "_alert_stuck_refunds", broken)
    # the tuple in `sweep` names the functions: patch it the way it is looked up
    renewals.sweep(session, T0 + timedelta(minutes=10), settings())
    assert session.scalar(select(func.count()).select_from(Invoice)) == 1
