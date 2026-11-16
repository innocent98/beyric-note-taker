"""Payment rows and the fake provider's own interface (billing stage S4)."""

import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.billing.fake import FakeProvider
from app.billing.payments import new_payment
from app.billing.provider import PaymentProvider
from app.models import Payment
from tests.test_web import make_user


def make(session, payer, **over):
    values = dict(provider="fake", kind="plan", currency="NGN", amount_minor=500_000)
    return new_payment(session, payer=payer, **{**values, **over})


def test_a_new_payment_is_pending_and_its_reference_carries_its_id(session):
    payer = make_user(session, "payer@example.test")

    payment = make(session, payer)
    session.commit()

    assert payment.status == "pending" and payment.paid_at is None
    assert payment.reference.startswith(f"bn-{payment.id}-")
    assert not payment.country_mismatch


def test_references_are_unique_per_payment(session):
    payer = make_user(session, "payer@example.test")

    refs = {make(session, payer).reference for _ in range(5)}

    assert len(refs) == 5


def test_the_database_refuses_a_zero_amount_and_an_unknown_currency(session):
    payer = make_user(session, "payer@example.test")
    for bad in ({"amount_minor": 0}, {"currency": "EUR"}, {"kind": "gift"}):
        with pytest.raises(IntegrityError):
            make(session, payer, **bad)
            session.flush()
        session.rollback()


def test_the_payer_going_away_keeps_the_payment(session):
    payer = make_user(session, "payer@example.test")
    payment = make(session, payer)
    session.commit()

    session.delete(payer)
    session.commit()

    kept = session.scalars(select(Payment).where(Payment.id == payment.id)).one()
    assert kept.payer_user_id is None


def test_the_fake_provider_has_the_whole_interface(session):
    provider = FakeProvider()
    payer = make_user(session, "payer@example.test")
    payment = make(session, payer)

    assert isinstance(provider, PaymentProvider)
    checkout = provider.start_checkout(payment, payer, "https://app.test/return")
    assert checkout.reference == payment.reference and payment.reference in checkout.url
    tx = provider.charge_saved_method("tok", 1000, "NGN", "bn-renewal")
    assert provider.fetch_transaction(tx.provider_tx_id) == tx and tx.status == "successful"
    assert provider.refund(tx.provider_tx_id, 400) == "fake-refund-1"
    assert provider.refunds == [(tx.provider_tx_id, 400)]
