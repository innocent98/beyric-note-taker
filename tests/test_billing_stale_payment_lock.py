"""The payment row lock reads the row as it is NOW, not as the session saw it earlier.

The app's sessions never expire their objects on commit (`app/db.py`, `expire_on_commit=False`).
A SELECT ... FOR UPDATE that returns a row the session already holds does not overwrite that
object's attributes unless the statement says `populate_existing`. So a caller that loaded a
payment as `pending`, and then waited for a provider answer while a webhook in another process
settled it, used to take the lock, wait for the other process, and still read `pending`: the grants
ran a second time, the paid payment was marked "needs a refund", or a `failed` report was written
over a `succeeded` payment.

These tests play exactly that: session A (built like the app's own sessions) holds the payment
as `pending`, session B settles it and commits, then A settles it again. Nothing may run twice.
Real Postgres; the provider is `FakeProvider`.
"""

from datetime import timedelta

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.billing import settle
from app.billing import subscriptions as subs
from app.db import make_session_factory
from app.models import Payment, Subscription
from tests.billing_support import T0, choose_country, deliver, make_catalog, pay, settings
from tests.test_web import make_user

LATER = T0 + timedelta(minutes=5)


@pytest.fixture(autouse=True)
def catalog(session, vault_key):
    make_catalog(session)


@pytest.fixture
def ada(session):
    return make_user(session, "ada@example.test")


@pytest.fixture
def grant_calls(monkeypatch):
    """Every payment id a grant handler ran for, in order."""
    calls: list[int] = []
    monkeypatch.setattr(
        settle,
        "GRANT_HANDLERS",
        [*settle.GRANT_HANDLERS, lambda db, payment: calls.append(payment.id)],
    )
    return calls


@pytest.fixture
def app_session(engine):
    """A session built like the app's own: nothing expires on commit."""
    with make_session_factory(engine)() as session:
        yield session


def stale_checkout(session, app_session: Session, user, providers):
    """A plan checkout (B, `session`) whose payment session A has loaded and still sees as
    `pending`. Returns (payment as A holds it, the id, the provider's `successful` report)."""
    choose_country(session, user, "US")
    started = subs.start_plan_checkout(session, user, "p20", settings(), T0)
    payment_id = started.payment.id
    tx = providers.usd.complete(started.payment, card_token="tok")
    held = app_session.get(Payment, payment_id)
    assert held.status == "pending"
    return held, payment_id, tx


def count(session: Session, model) -> int:
    session.expire_all()
    return session.scalar(select(func.count()).select_from(model))


def test_settle_transaction_does_not_grant_a_payment_a_webhook_already_settled(
    session, app_session, ada, providers, grant_calls
):
    held, payment_id, tx = stale_checkout(session, app_session, ada, providers)
    pay(session, providers.usd, session.get(Payment, payment_id), T0)  # B: the webhook settles it
    assert grant_calls == [payment_id]

    refusal = settle.settle_transaction(app_session, providers.usd, payment_id, tx, LATER)
    app_session.commit()

    assert refusal is None
    assert grant_calls == [payment_id]  # no grant handler ran a second time
    session.expire_all()
    paid = session.get(Payment, payment_id)
    assert paid.status == "succeeded"
    assert paid.needs_refund_reason is None  # a paid, used payment is not "refund it"
    assert paid.paid_at is not None and paid.paid_at.replace(tzinfo=T0.tzinfo) == T0
    assert count(session, Subscription) == 1
    assert held.status == "succeeded"  # A's own object now shows what is stored


def test_a_failed_report_never_overwrites_a_succeeded_payment(
    session, app_session, ada, providers, grant_calls
):
    held, payment_id, tx = stale_checkout(session, app_session, ada, providers)
    pay(session, providers.usd, session.get(Payment, payment_id), T0)
    failed = providers.usd.complete(held, status="failed", tx_id="fake-tx-late-failure")

    refusal = settle.settle_transaction(app_session, providers.usd, payment_id, failed, LATER)
    app_session.commit()

    assert refusal is None
    session.expire_all()
    assert session.get(Payment, payment_id).status == "succeeded"
    assert grant_calls == [payment_id]


def test_a_second_webhook_event_does_not_grant_a_payment_another_process_settled(
    session, app_session, ada, providers, grant_calls
):
    """The webhook path (`_apply`): this process held the payment as `pending`, a different event
    for it was settled by another process meanwhile."""
    held, payment_id, tx = stale_checkout(session, app_session, ada, providers)
    pay(session, providers.usd, session.get(Payment, payment_id), T0)
    again = providers.usd.event_for(tx)
    again = type(again)(
        event_id="evt-the-providers-own-second-delivery",
        kind=again.kind,
        reference=again.reference,
        provider_tx_id=again.provider_tx_id,
        amount_minor=again.amount_minor,
        currency=again.currency,
    )
    body = providers.usd.event_body(again)

    row = deliver(app_session, providers.usd, body, LATER)

    assert row.processed_at is not None
    assert row.error is None
    assert grant_calls == [payment_id]
    session.expire_all()
    paid = session.get(Payment, payment_id)
    assert paid.status == "succeeded"
    assert paid.needs_refund_reason is None
    assert count(session, Subscription) == 1
