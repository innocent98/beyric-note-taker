"""A customer who really paid but whose grant keeps failing in the background is never silent.

The provider says a payment is `successful`, a grant handler raises, the step is undone and the
payment stays open. The webhook path (`settle.process`) and both sweepers (`renewals.sweep`) tell
the admins ONCE per payment, under the key the cancel button shares
(`notify.grant_failed_key`), and only when the failure has gone on for `GRANT_ALERT_AFTER`. They
never tell them for a payment the provider says failed or pending, nor when the provider could not
be asked. What is stored and logged never holds the exception's text.

Real Postgres; the clock is the `now` each call is given; the provider is `FakeProvider`.
"""

import logging
from datetime import timedelta

import pytest
from sqlalchemy import select
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from app import alerts
from app.billing import notify, renewals, settle
from app.billing import subscriptions as subs
from app.billing.types import ProviderError, RetryLater
from app.models import BillingEvent, HourGrant, Notification, Payment
from tests.billing_support import T0, choose_country, deliver, make_catalog, settings
from tests.test_billing_cancel_checkout import cancel, start, stored
from tests.test_billing_profile import api_client
from tests.test_billing_subscriptions_api import Buyer
from tests.test_web import make_user

KIND = "admin.billing_grant_failed"
SECRET = "secret provider text ada@example.test 1999"
MIN = timedelta(minutes=1)


@pytest.fixture(autouse=True)
def catalog(session, vault_key):
    make_catalog(session)


@pytest.fixture
def ada(session):
    return make_user(session, "ada@example.test")


@pytest.fixture
def bea(session):
    return make_user(session, "bea@example.test")


@pytest.fixture
def admin(session):
    return make_user(session, "admin@example.test", admin=True)


@pytest.fixture
def web(engine):
    with api_client(engine) as client:
        yield client


@pytest.fixture
def buyer(session, web, providers) -> Buyer:
    make_user(session, "carl@example.test")
    return Buyer(web, "carl@example.test")


def checkout(session, user, providers, *, status="successful"):
    """A plan checkout `user` started at T0 and the provider now reports as `status`."""
    choose_country(session, user, "US")
    started = subs.start_plan_checkout(session, user, "p20", settings(), T0)
    tx = providers.usd.complete(started.payment, card_token="tok", status=status)
    return started.payment, tx


def webhook_body(providers, tx) -> bytes:
    return providers.usd.event_body(providers.usd.event_for(tx))


def failing_grant(monkeypatch, *, only_payment=None, times=None):
    """A grant handler that raises (with text that must never be kept). `only_payment` limits it to
    one payment id, `times` makes it fail that many times and then work."""
    seen = {"calls": 0}

    def boom(db, payment):
        if only_payment is not None and payment.id != only_payment:
            return
        seen["calls"] += 1
        if times is None or seen["calls"] <= times:
            raise RuntimeError(SECRET)

    monkeypatch.setattr(settle, "GRANT_HANDLERS", [*settle.GRANT_HANDLERS, boom])
    return seen


def grant_alerts(session, payment_id=None) -> list[Notification]:
    session.expire_all()
    query = select(Notification).where(Notification.kind == KIND)
    if payment_id is not None:
        query = query.where(Notification.dedupe_key == notify.grant_failed_key(payment_id))
    return list(session.scalars(query))


def sweep(session, now):
    renewals.sweep(session, now, settings())
    session.expire_all()


def expected_body(payment_id) -> str:
    return (
        f"The provider reports payment {payment_id} paid, but it could not be settled "
        f"(RuntimeError). Look at it."
    )


def assert_no_leak(*texts):
    for text in texts:
        for leaked in ("secret", "ada@example.test", "1999"):
            assert leaked not in (text or "")


# ---- the webhook path ----------------------------------------------------------------------------


def test_a_paid_event_whose_grant_keeps_failing_alerts_once_after_the_threshold(
    session, ada, admin, providers, monkeypatch
):
    payment, tx = checkout(session, ada, providers)
    body = webhook_body(providers, tx)
    failing_grant(monkeypatch)

    with pytest.raises(RetryLater):  # the first delivery: logged, nobody is paged
        deliver(session, providers.usd, body, T0)
    assert grant_alerts(session) == []

    for minutes in (10, 29):  # the provider's own retries, still inside the threshold
        with pytest.raises(RetryLater):
            deliver(session, providers.usd, body, T0 + minutes * MIN)
    assert grant_alerts(session) == []

    for minutes in (31, 45, 90, 600):  # persistent: told once, never again however many passes
        with pytest.raises(RetryLater):
            deliver(session, providers.usd, body, T0 + minutes * MIN)

    [alert] = grant_alerts(session, payment.id)
    assert len(grant_alerts(session)) == 1 and alert.user_id == admin.id
    assert alert.title == "A paid payment could not be granted"
    assert alert.body == expected_body(payment.id)
    session.expire_all()
    assert session.get(Payment, payment.id).status == "pending"  # undone: nothing was kept
    assert session.scalars(select(HourGrant)).first() is None


def test_the_stored_error_the_alert_and_the_logs_hold_no_exception_text(
    session, ada, admin, providers, monkeypatch, caplog
):
    payment, tx = checkout(session, ada, providers)
    body = webhook_body(providers, tx)
    failing_grant(monkeypatch)
    caplog.set_level(logging.DEBUG)

    for minutes in (0, 31):
        with pytest.raises(RetryLater):
            deliver(session, providers.usd, body, T0 + minutes * MIN)

    session.expire_all()
    row = session.scalars(select(BillingEvent)).one()
    assert row.error == "RuntimeError" and row.processed_at is None  # the type, still waiting
    [alert] = grant_alerts(session, payment.id)
    assert_no_leak(row.error, alert.title, alert.body, caplog.text)
    assert "RuntimeError" in caplog.text  # the type is what is logged


def test_a_provider_error_stores_its_type_not_its_text(session, ada, providers, caplog):
    payment, tx = checkout(session, ada, providers)
    body = webhook_body(providers, tx)
    providers.usd.fetch_error = ProviderError(SECRET)
    caplog.set_level(logging.DEBUG)

    with pytest.raises(RetryLater):
        deliver(session, providers.usd, body, T0)

    row = session.scalars(select(BillingEvent)).one()
    assert row.error == "ProviderError"
    assert_no_leak(row.error, caplog.text)


def test_a_provider_that_cannot_be_asked_never_alerts_however_old_the_event(
    session, ada, admin, providers, monkeypatch
):
    payment, tx = checkout(session, ada, providers)
    body = webhook_body(providers, tx)
    failing_grant(monkeypatch)
    providers.usd.fetch_error = ProviderError("the provider is down")

    for minutes in (0, 40, 400):  # long past the threshold, but nothing is known to be paid
        with pytest.raises(RetryLater):
            deliver(session, providers.usd, body, T0 + minutes * MIN)
    sweep(session, T0 + 500 * MIN)

    assert grant_alerts(session) == []


def test_one_failed_pass_that_the_next_clears_never_alerts(
    session, ada, admin, providers, monkeypatch
):
    payment, tx = checkout(session, ada, providers)
    body = webhook_body(providers, tx)
    failing_grant(monkeypatch, times=1)  # a blip: the first attempt fails, the next works

    with pytest.raises(RetryLater):
        deliver(session, providers.usd, body, T0)
    sweep(session, T0 + 3 * MIN)

    session.expire_all()
    assert session.get(Payment, payment.id).status == "succeeded"
    assert grant_alerts(session) == []


def test_a_charge_the_provider_says_failed_or_pending_never_alerts(
    session, ada, bea, admin, providers, monkeypatch
):
    failed, failed_tx = checkout(session, ada, providers, status="failed")
    pending, pending_tx = checkout(session, bea, providers, status="pending")
    failing_grant(monkeypatch)  # would raise if anything tried to grant
    for tx in (failed_tx, pending_tx):
        body = webhook_body(providers, tx)
        try:
            deliver(session, providers.usd, body, T0 + 40 * MIN)
        except RetryLater:
            pass
    sweep(session, T0 + 3 * MIN)
    sweep(session, T0 + 90 * MIN)

    assert grant_alerts(session) == []
    session.expire_all()
    assert session.get(Payment, pending.id).status == "pending"


# ---- the sweeper -------------------------------------------------------------------------------


def test_the_sweeper_alerts_once_for_a_paid_payment_whose_grant_keeps_failing(
    session, ada, admin, providers, monkeypatch, caplog
):
    payment, tx = checkout(session, ada, providers)  # no webhook ever came
    failing_grant(monkeypatch)
    caplog.set_level(logging.DEBUG)

    sweep(session, T0 + 3 * MIN)
    sweep(session, T0 + 6 * MIN)
    sweep(session, T0 + 25 * MIN)
    assert grant_alerts(session) == []  # first failures: logged only

    for minutes in (31, 35, 40, 55, 70, 130, 400):  # persistent: one alert, however many passes
        sweep(session, T0 + minutes * MIN)

    [alert] = grant_alerts(session, payment.id)
    assert len(grant_alerts(session)) == 1
    assert alert.body == expected_body(payment.id)
    assert_no_leak(alert.title, alert.body, caplog.text)
    assert session.get(Payment, payment.id).status == "pending"
    assert session.scalars(select(HourGrant)).first() is None


def test_a_stored_event_and_the_payment_sweeper_on_one_payment_give_one_alert(
    session, ada, admin, providers, monkeypatch
):
    payment, tx = checkout(session, ada, providers)
    body = webhook_body(providers, tx)
    failing_grant(monkeypatch)
    with pytest.raises(RetryLater):
        deliver(session, providers.usd, body, T0)  # the event waits; both sweepers now see it

    sweep(session, T0 + 3 * MIN)
    assert grant_alerts(session) == []
    sweep(session, T0 + 31 * MIN)  # the event sweeper and the payment sweeper both fail it
    sweep(session, T0 + 90 * MIN)

    assert len(grant_alerts(session, payment.id)) == 1
    row = session.scalars(select(BillingEvent)).one()
    assert row.error == "RuntimeError" and row.processed_at is None


def test_the_payment_is_granted_once_the_handler_works_again_and_no_more_alerts_follow(
    session, ada, admin, providers, monkeypatch
):
    payment, tx = checkout(session, ada, providers)
    seen = failing_grant(monkeypatch, times=9)  # fails for a while, then recovers

    for minutes in (3, 6, 31, 34, 37, 40, 43, 46, 49):
        sweep(session, T0 + minutes * MIN)
    assert len(grant_alerts(session, payment.id)) == 1

    for minutes in (52, 55):
        sweep(session, T0 + minutes * MIN)

    assert session.get(Payment, payment.id).status == "succeeded"
    assert seen["calls"] >= 10
    assert len(grant_alerts(session)) == 1  # the alert stays as the record; nothing new


@pytest.mark.parametrize("state", ["failed", "pending"])
def test_the_sweeper_never_alerts_for_a_payment_the_provider_says_is_not_paid(
    session, ada, admin, providers, monkeypatch, state
):
    payment, tx = checkout(session, ada, providers, status=state)
    failing_grant(monkeypatch)

    for minutes in (3, 40, 100, 400):
        sweep(session, T0 + minutes * MIN)

    assert grant_alerts(session) == []
    assert session.get(Payment, payment.id).status == "pending"  # the sweeper leaves it alone


@pytest.mark.parametrize("error", [ProviderError(SECRET), ValueError(SECRET)])
def test_a_provider_that_cannot_be_asked_never_alerts_from_the_sweeper(
    session, ada, admin, providers, monkeypatch, error
):
    """Also a lookup that blows up in an unexpected way (an answer the adapter cannot read): the
    provider has not said `successful`, so nothing is known to be paid."""
    payment, tx = checkout(session, ada, providers)
    failing_grant(monkeypatch)
    providers.usd.fetch_error = error

    for minutes in (3, 40, 100):
        sweep(session, T0 + minutes * MIN)

    assert grant_alerts(session) == []


def test_an_alert_that_cannot_be_written_does_not_stop_the_sweep(
    session, ada, bea, admin, providers, monkeypatch, caplog
):
    first, _ = checkout(session, ada, providers)
    second, _ = checkout(session, bea, providers)  # newer: the sweeper reaches it first
    failing_grant(monkeypatch, only_payment=second.id)

    def cannot_write(*args, **kwargs):
        raise OperationalError("INSERT", {}, Exception("the alert table is gone"))

    monkeypatch.setattr(alerts, "notify_admins", cannot_write)
    caplog.set_level(logging.DEBUG)

    sweep(session, T0 + 31 * MIN)

    assert session.get(Payment, first.id).status == "succeeded"  # the older one still settled
    assert session.get(Payment, second.id).status == "pending"
    assert grant_alerts(session) == []
    assert "admin alert admin.billing_grant_failed failed: OperationalError" in caplog.text
    assert "the alert table is gone" not in caplog.text


def test_an_alert_whose_commit_fails_does_not_stop_the_sweep(
    session, ada, bea, admin, providers, monkeypatch, caplog
):
    first, _ = checkout(session, ada, providers)
    second, _ = checkout(session, bea, providers)
    state = {"failed_grant": False, "failed_commit": 0}

    def boom(db, payment):
        if payment.id == second.id:
            state["failed_grant"] = True
            raise RuntimeError(SECRET)

    real_commit = Session.commit

    def commit_fails_once_after_the_grant(self):
        if state["failed_grant"] and not state["failed_commit"]:  # the alert's own commit
            state["failed_commit"] += 1
            raise OperationalError("COMMIT", {}, Exception("connection lost"))
        return real_commit(self)

    monkeypatch.setattr(settle, "GRANT_HANDLERS", [*settle.GRANT_HANDLERS, boom])
    monkeypatch.setattr(Session, "commit", commit_fails_once_after_the_grant)
    caplog.set_level(logging.DEBUG)

    sweep(session, T0 + 31 * MIN)

    assert state["failed_commit"] == 1  # the commit guard really ran
    assert session.get(Payment, first.id).status == "succeeded"  # and the pass went on
    assert session.get(Payment, second.id).status == "pending"
    assert grant_alerts(session) == []
    assert "not saved: OperationalError" in caplog.text
    assert_no_leak(caplog.text)


# ---- the cancel button and the sweeper share one alert -------------------------------------------


def test_a_cancel_and_then_a_sweeper_on_the_same_payment_give_one_alert(
    session, buyer, admin, providers, monkeypatch
):
    payment = start(buyer)
    row = stored(session, payment["reference"])
    providers.usd.complete(row, card_token="tok-boom")
    failing_grant(monkeypatch)

    assert cancel(buyer, payment["reference"]).status_code == 409  # the cancel path tells first
    [alert] = grant_alerts(session, row.id)
    assert alert.body == expected_body(row.id)  # the same words whichever path wrote it

    for minutes in (120, 125, 300):
        sweep(session, row.created_at + minutes * MIN)  # the sweeper sees the same failure

    assert len(grant_alerts(session)) == 1
    assert alert.dedupe_key == f"admin.billing_grant_failed:{row.id}"


def test_a_sweeper_and_then_a_cancel_on_the_same_payment_give_one_alert(
    session, buyer, admin, providers, monkeypatch
):
    payment = start(buyer)
    row = stored(session, payment["reference"])
    providers.usd.complete(row, card_token="tok-boom")
    failing_grant(monkeypatch)

    sweep(session, row.created_at + 120 * MIN)  # the sweeper tells first
    [alert] = grant_alerts(session, row.id)
    assert alert.body == expected_body(row.id)

    for _ in range(2):  # then the person presses Cancel, twice
        assert cancel(buyer, payment["reference"]).status_code == 409

    assert len(grant_alerts(session)) == 1
