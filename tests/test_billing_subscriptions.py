"""Buying a plan or a pack, what a verified payment does, cancel and change plan (billing stage
S8a, `app/billing/subscriptions.py`).

Real Postgres; the payer "pays" through the REAL webhook inbox and settlement rules, with only the
provider scripted (`FakeProvider`, named after the real provider the route picks). What these
prove: hours come only from a verified payment (a wrong amount, currency or reference never
grants; a charge answer or a redirect is not an input at all); a replayed webhook grants once; a
second payment for the same plan or period grants nothing and tells the admins; the card token is
saved encrypted; and the person's rights follow the subscription (`entitlements`).
"""

from datetime import timedelta

import pytest
from sqlalchemy import func, select

from app import entitlements, vault
from app.billing import plans, routing
from app.billing import subscriptions as subs
from app.billing.profile import live_subscription
from app.models import (
    HourGrant,
    Payment,
    PaymentMethod,
    PlanPrice,
    Subscription,
    UsageAllocation,
    UsageEntry,
)
from app.usage import email_hash
from tests.billing_support import (  # noqa: F401  (fixtures used by name)
    HOUR,
    KEY,
    P20,
    P40,
    T0,
    buy_plan,
    choose_country,
    grants,
    live,
    make_catalog,
    notices,
    pay,
    settings,
    status,
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


def payments(session, **where) -> list[Payment]:
    session.expire_all()
    return list(session.scalars(select(Payment).filter_by(**where).order_by(Payment.id)))


# ---- the first plan ---------------------------------------------------------------------------


def test_a_first_plan_is_paid_through_the_webhook_and_grants_one_period(session, ada, providers):
    sub = buy_plan(session, ada, providers.usd)

    assert (sub.status, sub.plan_code, sub.currency, sub.provider) == (
        "active",
        "p20",
        "USD",
        "flutterwave",
    )
    assert (sub.current_period_start, sub.current_period_end) == (T0, plans.add_months(T0))
    assert sub.user_id == sub.payer_user_id == ada.id
    [grant] = grants(session, ada)
    assert (grant.kind, grant.seconds, grant.starts_at, grant.expires_at) == (
        "plan_period",
        P20,
        T0,
        sub.current_period_end,
    )
    assert grant.subscription_id == sub.id
    [payment] = payments(session)
    assert (payment.status, payment.kind, payment.amount_minor, payment.currency) == (
        "succeeded",
        "plan",
        1800,
        "USD",
    )
    assert grant.source_payment_id == payment.id and payment.subscription_id == sub.id
    seen = status(session, ada, T0 + timedelta(hours=1))
    assert (seen.state, seen.seconds_left) == ("active", P20)
    [receipt] = notices(session, ada, "billing.receipt")
    assert "USD 18.00" in receipt.body


def test_the_card_token_is_saved_encrypted_and_can_be_opened_for_renewals(session, ada, providers):
    buy_plan(session, ada, providers.usd, token="tok-secret-1")

    [method] = session.scalars(select(PaymentMethod)).all()
    assert (method.user_id, method.provider, method.email_at_provider) == (
        ada.id,
        "flutterwave",
        "payer@example.test",
    )
    assert b"tok-secret-1" not in bytes(method.token_enc)  # never stored readable
    assert subs.saved_card(session, ada.id, "flutterwave") == ("tok-secret-1", "payer@example.test")
    assert subs.saved_card(session, ada.id, "paystack") is None  # a card belongs to one provider


def test_a_new_card_replaces_the_old_one_and_the_token_never_reaches_the_inbox(
    session, ada, providers
):
    buy_plan(session, ada, providers.usd, token="tok-old")
    sub = live(session, ada)
    # an upgrade paid with another card replaces the saved one
    started = subs.change_plan(session, ada, "p40", settings(), T0 + timedelta(days=1)).started
    pay(session, providers.usd, started.payment, T0 + timedelta(days=1), card_token="tok-new")

    assert [m.provider for m in session.scalars(select(PaymentMethod))] == ["flutterwave"]
    assert subs.saved_card(session, ada.id, "flutterwave")[0] == "tok-new"
    from app.models import BillingEvent

    for event in session.scalars(select(BillingEvent)):
        assert "tok-old" not in str(event.payload) and "tok-new" not in str(event.payload)
    assert sub.id == live(session, ada).id


def test_a_card_that_cannot_be_saved_never_stops_the_hours_that_were_paid_for(
    session, ada, admin, providers, monkeypatch
):
    monkeypatch.setattr(
        vault, "get_settings", lambda: __import__("tests.test_web").test_web.make_settings()
    )
    sub = buy_plan(session, ada, providers.usd)  # no vault key: the card cannot be encrypted

    assert sub.status == "active" and [g.seconds for g in grants(session, ada)] == [P20]
    assert session.scalar(select(func.count()).select_from(PaymentMethod)) == 0
    assert notices(session, admin, "admin.billing_card_not_saved")  # a human is told


def test_the_same_event_delivered_twice_grants_once(session, ada, providers):
    from app.billing import inbox, settle
    from tests.billing_support import deliver

    choose_country(session, ada, "US")
    started = subs.start_plan_checkout(session, ada, "p20", settings(), T0)
    tx = providers.usd.complete(started.payment, card_token="t", tx_id="fx-1")
    body = providers.usd.event_body(providers.usd.event_for(tx))
    deliver(session, providers.usd, body, T0)
    deliver(session, providers.usd, body, T0 + timedelta(minutes=5))  # the vendor's retry
    # even if the stored event were decided again, the payment is already paid: nothing grants
    row, event = inbox.receive(session, providers.usd, providers.usd.signed_headers(body), body, T0)
    settle.process(session, providers.usd, row, event, T0 + timedelta(minutes=9))

    assert len(grants(session, ada, "plan_period")) == 1
    assert session.scalar(select(func.count()).select_from(Subscription)) == 1
    assert len(notices(session, ada, "billing.receipt")) == 1


@pytest.mark.parametrize(
    ("wrong", "why"),
    [
        ({"amount_minor": 1799}, "amount too low"),
        ({"currency": "NGN"}, "currency mismatch"),
        ({"reference": "bn-someone-else"}, "reference mismatch"),
        ({"status": "failed"}, "failed"),
    ],
    ids=["amount", "currency", "reference", "declined"],
)
def test_a_wrong_amount_currency_or_reference_never_grants(session, ada, providers, wrong, why):
    choose_country(session, ada, "US")
    started = subs.start_plan_checkout(session, ada, "p20", settings(), T0)

    pay(session, providers.usd, started.payment, T0, **wrong)

    assert grants(session, ada) == []
    assert live(session, ada) is None
    [payment] = payments(session)
    assert payment.status in ("pending", "failed")  # never `succeeded`
    assert session.scalar(select(func.count()).select_from(Subscription)) == 0


def test_an_overpayment_still_grants_exactly_one_plan_period(session, ada, providers):
    choose_country(session, ada, "US")
    started = subs.start_plan_checkout(session, ada, "p20", settings(), T0)
    pay(session, providers.usd, started.payment, T0, amount_minor=2500)  # the provider took more

    assert [g.seconds for g in grants(session, ada)] == [P20]


def test_the_checkout_makes_and_commits_the_payment_before_the_provider_is_asked(
    session, ada, providers, monkeypatch
):
    seen = {}
    real = providers.usd.start_checkout

    def spy(payment, payer, url):
        from sqlalchemy.orm import Session

        with Session(session.get_bind()) as other:  # another connection: only a commit is visible
            seen["row"] = other.scalar(
                select(Payment).where(Payment.reference == payment.reference)
            )
        return real(payment, payer, url)

    monkeypatch.setattr(providers.usd, "start_checkout", spy)
    choose_country(session, ada, "US")

    started = subs.start_plan_checkout(session, ada, "p20", settings(), T0)

    assert seen["row"] is not None and seen["row"].status == "pending"
    assert started.url == f"https://fake.test/pay/{started.payment.reference}"
    assert started.payment.pay_url == started.url


def test_a_provider_that_fails_leaves_a_failed_payment_and_no_subscription(session, ada, providers):
    from app.billing.types import ProviderError

    providers.usd.checkout_error = ProviderError("down")
    choose_country(session, ada, "US")

    with pytest.raises(subs.Refused) as refused:
        subs.start_plan_checkout(session, ada, "p20", settings(), T0)

    assert (refused.value.status, refused.value.code) == (502, "provider_error")
    [payment] = payments(session)
    assert payment.status == "failed" and payment.pay_url is None


@pytest.mark.parametrize(
    ("country", "provider_name", "currency", "amount"),
    [("NG", "ngn", "NGN", 2_500_000), ("US", "usd", "USD", 1800), ("GB", "usd", "USD", 1800)],
)
def test_the_billing_country_picks_the_provider_the_currency_and_the_price(
    session, ada, providers, country, provider_name, currency, amount
):
    provider = getattr(providers, provider_name)
    sub = buy_plan(session, ada, provider, country=country)

    assert (sub.provider, sub.currency) == (provider.name, currency)
    [payment] = payments(session)
    assert (payment.provider, payment.currency, payment.amount_minor) == (
        provider.name,
        currency,
        amount,
    )


def test_checkout_refuses_without_a_country_a_price_a_provider_or_with_a_plan_already(
    session, ada, providers, monkeypatch
):
    with pytest.raises(routing.CountryMissing):
        subs.start_plan_checkout(session, ada, "p20", settings(), T0)  # nothing is guessed

    choose_country(session, ada, "NG")
    session.query(PlanPrice).filter_by(currency="NGN").delete()
    session.commit()
    with pytest.raises(subs.Refused) as no_price:
        subs.start_plan_checkout(session, ada, "p20", settings(), T0)
    assert (no_price.value.status, no_price.value.code) == (422, "price_not_available")

    with pytest.raises(subs.Refused) as no_plan:
        subs.start_plan_checkout(session, ada, "nope", settings(), T0)
    assert no_plan.value.code == "plan_not_found"

    from app.billing.provider import PROVIDER_FACTORIES

    monkeypatch.delitem(PROVIDER_FACTORIES, "flutterwave")
    choose_country(session, ada, "US", now=T0 - timedelta(days=60))  # more than 30 days on
    with pytest.raises(subs.Refused) as no_provider:
        subs.start_plan_checkout(session, ada, "p20", settings(), T0)
    assert (no_provider.value.status, no_provider.value.code) == (503, "provider_unavailable")

    monkeypatch.setitem(PROVIDER_FACTORIES, "flutterwave", lambda s: providers.usd)
    buy_plan(session, ada, providers.usd)
    with pytest.raises(subs.Refused) as again:
        subs.start_plan_checkout(session, ada, "p20", settings(), T0 + timedelta(days=1))
    assert (again.value.status, again.value.code) == (409, "already_subscribed")


def test_a_second_plan_payment_for_a_person_with_a_plan_grants_nothing_and_tells_the_admins(
    session, ada, admin, providers
):
    choose_country(session, ada, "US")
    first = subs.start_plan_checkout(session, ada, "p20", settings(), T0)
    second = subs.start_plan_checkout(
        session, ada, "p20", settings(), T0
    )  # two tabs, two checkouts
    pay(session, providers.usd, first.payment, T0, card_token="t1")
    pay(session, providers.usd, second.payment, T0 + timedelta(minutes=1), card_token="t2")

    assert [g.seconds for g in grants(session, ada)] == [P20]
    assert session.scalar(select(func.count()).select_from(Subscription)) == 1
    assert {p.status for p in payments(session)} == {"succeeded"}  # the money did arrive
    [alert] = notices(session, admin, "admin.billing_refund_needed")
    assert str(second.payment.id) in alert.body and "@" not in alert.body  # ids only


# ---- packs ------------------------------------------------------------------------------------


def test_a_pack_needs_a_live_plan(session, ada, providers):
    with pytest.raises(subs.Refused) as refused:
        subs.start_pack_checkout(session, ada, "pack10", settings(), T0)
    assert (refused.value.status, refused.value.code) == (409, "plan_required")


def test_a_paid_pack_grants_ten_hours_valid_twelve_months_and_keeps_the_saved_card(
    session, ada, providers
):
    buy_plan(session, ada, providers.usd, token="tok-plan")
    started = subs.start_pack_checkout(session, ada, "pack10", settings(), T0 + timedelta(days=3))
    pay(session, providers.usd, started.payment, T0 + timedelta(days=3), card_token="tok-pack")

    [pack] = grants(session, ada, "pack")
    paid = T0 + timedelta(days=3)
    assert (pack.seconds, pack.starts_at, pack.expires_at) == (
        10 * HOUR,
        paid,
        plans.add_months(paid, 12),
    )
    assert subs.saved_card(session, ada.id, "flutterwave")[0] == "tok-plan"  # a pack keeps the card
    assert status(session, ada, paid + timedelta(hours=1)).seconds_left == P20 + 10 * HOUR


def test_a_pack_is_paid_on_the_route_the_plan_was_sold_on_not_on_a_later_country(
    session, ada, providers
):
    buy_plan(session, ada, providers.usd, country="US")
    choose_country(session, ada, "NG", now=T0 + timedelta(days=1))  # crossing: scheduled for later

    started = subs.start_pack_checkout(session, ada, "pack10", settings(), T0 + timedelta(days=2))

    assert (started.payment.provider, started.payment.currency) == ("flutterwave", "USD")
    assert providers.ngn.checkouts == []


def test_a_pack_not_sold_in_the_plans_currency_is_refused(session, ada, providers):
    buy_plan(session, ada, providers.usd)
    with pytest.raises(subs.Refused) as refused:
        subs.start_pack_checkout(session, ada, "nope", settings(), T0)
    assert (refused.value.status, refused.value.code) == (404, "product_not_found")


def test_a_pack_bought_in_the_grace_is_granted_and_does_not_end_the_grace(session, ada, providers):
    from app.billing import renewals

    buy_plan(session, ada, providers.usd)
    providers.usd.charge_outcome = "failed"
    due = live(session, ada).current_period_end
    renewals.run_due(session, due, settings())
    assert live(session, ada).status == "past_due"

    started = subs.start_pack_checkout(session, ada, "pack10", settings(), due + timedelta(hours=2))
    pay(session, providers.usd, started.payment, due + timedelta(hours=2))

    assert [g.seconds for g in grants(session, ada, "pack")] == [10 * HOUR]
    assert live(session, ada).status == "past_due"  # a pack pays for a pack, not for the plan


# ---- cancel and resume ------------------------------------------------------------------------


def test_cancel_keeps_access_to_the_period_end_and_asking_twice_changes_nothing(
    session, ada, providers
):
    buy_plan(session, ada, providers.usd)
    now = T0 + timedelta(days=5)

    subs.cancel(session, ada, now)
    session.commit()
    again = subs.cancel(session, ada, now + timedelta(hours=1))
    session.commit()

    sub = live(session, ada)
    assert sub.cancel_at_period_end and sub.canceled_at == now and again.canceled_at == now
    seen = status(session, ada, now)
    assert (seen.state, seen.seconds_left) == ("active", P20)  # nothing is taken away early
    assert [g.expires_at for g in grants(session, ada)] == [sub.current_period_end]  # no refund


def test_resume_takes_a_cancel_back_while_the_period_runs(session, ada, providers):
    buy_plan(session, ada, providers.usd)
    with pytest.raises(subs.Refused) as nothing:
        subs.resume(session, ada, T0)
    assert nothing.value.code == "not_canceled"

    subs.cancel(session, ada, T0)
    subs.resume(session, ada, T0 + timedelta(days=1))
    session.commit()

    sub = live(session, ada)
    assert not sub.cancel_at_period_end and sub.canceled_at is None


def test_without_a_plan_cancel_and_resume_and_change_are_refused(session, ada, providers):
    for call in (
        lambda: subs.cancel(session, ada, T0),
        lambda: subs.resume(session, ada, T0),
        lambda: subs.change_plan(session, ada, "p40", settings(), T0),
    ):
        with pytest.raises(subs.Refused) as refused:
            call()
        assert (refused.value.status, refused.value.code) == (409, "no_subscription")
        session.rollback()


# ---- change plan: upgrade, downgrade ----------------------------------------------------------

HALF = T0 + timedelta(days=15, hours=12)  # a 30-day period (1 to 31 December), exactly half gone


def test_an_upgrade_is_a_checkout_for_the_prorated_difference_and_adds_the_extra_hours(
    session, ada, providers
):
    sub = buy_plan(session, ada, providers.usd)
    assert (sub.current_period_end - sub.current_period_start) == timedelta(days=31)  # Dec -> Jan

    change = subs.change_plan(session, ada, "p40", settings(), HALF)
    session.commit()

    assert change.result == "checkout"
    left = (sub.current_period_end - HALF).total_seconds()
    total = 31 * 86400
    assert change.started.payment.amount_minor == (3400 - 1800) * int(left) // total
    assert change.started.payment.kind == "upgrade"
    # nothing changes until the payment is verified
    assert live(session, ada).plan_code == "p20" and len(grants(session, ada)) == 1

    pay(session, providers.usd, change.started.payment, HALF + timedelta(minutes=3))

    sub = live(session, ada)
    assert (sub.plan_code, sub.pending_plan_code) == ("p40", None)
    assert sub.current_period_end == plans.add_months(T0)  # the period does not move
    extra = [g for g in grants(session, ada) if g.source_payment_id == change.started.payment.id]
    assert len(extra) == 1
    assert (
        extra[0].seconds == (P40 - P20) * int(left) // total // 360 * 360
    )  # rounded down to 0.1 h
    assert extra[0].expires_at == sub.current_period_end
    assert status(session, ada, HALF + timedelta(hours=1)).seconds_left == P20 + extra[0].seconds


def test_upgrade_arithmetic_on_a_simple_period(session, ada, providers):
    sub = buy_plan(session, ada, providers.usd)
    sub.current_period_end = sub.current_period_start + timedelta(days=30)
    session.commit()
    now = sub.current_period_start + timedelta(days=15)  # exactly half

    change = subs.change_plan(session, ada, "p40", settings(), now)

    assert change.started.payment.amount_minor == 800  # (34.00 - 18.00) / 2
    pay(session, providers.usd, change.started.payment, now)
    [extra] = [g for g in grants(session, ada) if g.id != grants(session, ada)[0].id]
    assert extra.seconds == 10 * HOUR  # (40 - 20) hours / 2


def test_a_downgrade_waits_for_the_renewal_and_touches_nothing_now(session, ada, providers):
    buy_plan(session, ada, providers.usd, plan="p40")

    change = subs.change_plan(session, ada, "p20", settings(), T0 + timedelta(days=2))
    session.commit()

    assert (change.result, change.started) == ("scheduled", None)
    sub = live(session, ada)
    assert (sub.plan_code, sub.pending_plan_code) == ("p40", "p20")
    assert [g.seconds for g in grants(session, ada)] == [P40]
    assert providers.usd.checkouts == [providers.usd.checkouts[0]]  # only the first purchase


def test_choosing_the_plan_you_are_on_takes_a_waiting_downgrade_back(session, ada, providers):
    buy_plan(session, ada, providers.usd, plan="p40")
    subs.change_plan(session, ada, "p20", settings(), T0 + timedelta(days=2))

    change = subs.change_plan(session, ada, "p40", settings(), T0 + timedelta(days=3))

    assert change.result == "cleared" and live(session, ada).pending_plan_code is None
    with pytest.raises(subs.Refused) as same:
        subs.change_plan(session, ada, "p40", settings(), T0 + timedelta(days=4))
    assert same.value.code == "same_plan"


def test_an_upgrade_paid_after_the_period_moved_on_grants_nothing(session, ada, admin, providers):
    from app.billing import renewals

    buy_plan(session, ada, providers.usd)
    change = subs.change_plan(session, ada, "p40", settings(), HALF)
    session.commit()
    due = live(session, ada).current_period_end
    renewals.run_due(session, due + timedelta(seconds=5), settings())  # the renewal got in first

    pay(session, providers.usd, change.started.payment, due + timedelta(hours=2))

    sub = live(session, ada)
    assert sub.plan_code == "p20"  # not upgraded: the period it was for is over
    assert [g.seconds for g in grants(session, ada)] == [P20, P20]  # the two periods, no extra
    assert notices(session, admin, "admin.billing_refund_needed")


@pytest.mark.parametrize(
    ("target", "code"),
    [("p20", "same_plan")],
)
def test_changing_to_the_same_plan_is_refused(session, ada, providers, target, code):
    buy_plan(session, ada, providers.usd)
    with pytest.raises(subs.Refused) as refused:
        subs.change_plan(session, ada, target, settings(), T0 + timedelta(days=1))
    assert refused.value.code == code


def test_an_upgrade_with_hours_left_so_few_the_price_rounds_to_nothing_is_refused(
    session, ada, providers
):
    sub = buy_plan(session, ada, providers.usd)
    near_end = sub.current_period_end - timedelta(seconds=1)
    with pytest.raises(subs.Refused) as refused:
        subs.change_plan(session, ada, "p40", settings(), near_end)
    assert (refused.value.status, refused.value.code) == (409, "period_almost_over")


def test_a_plan_with_no_price_in_the_currency_cannot_be_chosen(session, ada, providers):
    buy_plan(session, ada, providers.usd)
    session.query(PlanPrice).filter_by(plan_code="p40", currency="USD").delete()
    session.commit()
    with pytest.raises(subs.Refused) as refused:
        subs.change_plan(session, ada, "p40", settings(), T0 + timedelta(days=1))
    assert (refused.value.status, refused.value.code) == (422, "price_not_available")


# ---- what the person may do follows the subscription -------------------------------------------


def test_the_states_of_a_person_by_the_clock_and_what_each_may_do(session, ada, providers):
    from app.billing import renewals

    buy_plan(session, ada, providers.usd)
    due = live(session, ada).current_period_end
    providers.usd.charge_outcome = "failed"
    asks = lambda at: entitlements.can(session, ada, "ask", at)  # noqa: E731

    assert (
        status(session, ada, due - timedelta(hours=1)).state,
        asks(due - timedelta(hours=1)),
    ) == (
        "active",
        True,
    )
    # the worker has not run yet: the clock alone already says past_due, and Ask stays open
    assert (
        status(session, ada, due + timedelta(hours=1)).state,
        asks(due + timedelta(hours=1)),
    ) == (
        "past_due",
        True,
    )
    renewals.run_due(session, due, settings())
    assert status(session, ada, due + timedelta(hours=40)).state == "past_due"
    # and after the grace: lapsed, and Ask, translation and rewriting are closed
    after = due + timedelta(hours=72)
    assert (status(session, ada, after).state, asks(after)) == ("lapsed", False)
    for feature in entitlements.FEATURES:
        assert not entitlements.can(session, ada, feature, after)


def test_a_lapsed_person_with_pilot_hours_left_still_shows_pilot(session, ada, providers):
    from app.billing import renewals

    buy_plan(session, ada, providers.usd)
    due = live(session, ada).current_period_end
    session.add(
        HourGrant(user_id=ada.id, email_hash=email_hash(ada.email), kind="pilot", seconds=HOUR)
    )
    session.commit()
    providers.usd.charge_outcome = "failed"
    for hours in (0, 24, 48, 72):
        renewals.run_due(session, due + timedelta(hours=hours), settings())

    assert status(session, ada, due + timedelta(hours=73)).state == "pilot"


def test_a_cancelled_plan_after_its_period_is_none_not_lapsed(session, ada, providers):
    from app.billing import renewals

    buy_plan(session, ada, providers.usd)
    subs.cancel(session, ada, T0 + timedelta(days=1))
    session.commit()
    due = live(session, ada).current_period_end

    renewals.run_due(session, due, settings())

    assert status(session, ada, due + timedelta(hours=1)).state == "none"
    assert providers.usd.charges == []  # never charged


def test_a_person_who_never_had_a_plan_is_unchanged(session, ada):
    assert status(session, ada, T0).state == "none"


# ---- the stage S5 stub is gone ---------------------------------------------------------------


def test_the_live_subscription_answer_is_the_real_one_with_the_route_it_was_sold_on(
    session, ada, providers
):
    assert live_subscription(session, ada.id, T0) is None
    sub = buy_plan(session, ada, providers.ngn, country="NG")

    answer = live_subscription(session, ada.id, T0 + timedelta(days=1))

    assert (answer.period_end, answer.currency, answer.provider) == (
        sub.current_period_end,
        "NGN",
        "paystack",
    )
    assert not getattr(live_subscription, "is_stub", False)
    subs.cancel(session, ada, T0 + timedelta(days=1))
    session.commit()
    assert live_subscription(session, ada.id, T0) is not None  # cancelled but still paid: live


def test_a_lapsed_subscription_is_not_live(session, ada, providers):
    from app.billing import renewals

    buy_plan(session, ada, providers.usd)
    due = live(session, ada).current_period_end
    providers.usd.charge_outcome = "failed"
    for hours in (0, 24, 48, 72):
        renewals.run_due(session, due + timedelta(hours=hours), settings())
    session.expire_all()

    assert live_subscription(session, ada.id, due + timedelta(hours=80)) is None


# ---- accounts ---------------------------------------------------------------------------------


def test_deleting_the_account_ends_the_plan_and_deletes_the_saved_cards(session, ada, providers):
    buy_plan(session, ada, providers.usd)

    subs.end_for_deleted_account(session, ada, T0 + timedelta(days=2))
    session.commit()

    session.expire_all()
    sub = session.scalars(select(Subscription)).one()
    assert sub.status == "ended" and sub.next_charge_at is None
    assert session.scalar(select(func.count()).select_from(PaymentMethod)) == 0
    assert live(session, ada) is None


def test_a_recording_is_split_across_plan_hours_like_any_other_grant(session, ada, providers):
    """The plan's hours are ordinary grants: a counted recording is allocated against them, and a
    renewal's new period does not roll the unused ones over."""
    from app import hours

    buy_plan(session, ada, providers.usd)
    entry = UsageEntry(user_id=ada.id, email_hash=email_hash(ada.email), seconds=7 * HOUR)
    session.add(entry)
    session.flush()
    hours.allocate(session, ada, entry, T0 + timedelta(days=2))
    session.commit()

    assert status(session, ada, T0 + timedelta(days=3)).seconds_left == P20 - 7 * HOUR
    assert session.scalar(select(func.sum(UsageAllocation.seconds))) == 7 * HOUR


# ---- a found S4 bug, closed here ---------------------------------------------------------------


def test_a_transaction_id_another_payment_already_holds_is_refused_not_a_500(
    session, ada, providers
):
    """The provider reports a transaction id that another payment already carries. Settlement used
    to flush the claim outside its savepoint, so the unique violation poisoned the transaction (a
    500 and a stuck webhook). Now it is the recorded refusal, and nothing is granted."""
    from app.billing import settle

    first = buy_plan(session, ada, providers.usd)  # holds transaction `fake-tx-1`
    pack = subs.start_pack_checkout(session, ada, "pack10", settings(), T0 + timedelta(days=1))
    tx = providers.usd.complete(pack.payment, tx_id="fake-tx-1")  # the same id, a second payment

    refusal = settle.settle_transaction(
        session, providers.usd, pack.payment.id, tx, T0 + timedelta(days=1)
    )
    session.commit()

    assert refusal == "transaction already used by another payment"
    session.expire_all()
    assert payments(session)[-1].status == "pending"
    assert grants(session, ada, "pack") == [] and first is not None


# ---- the preview ----------------------------------------------------------------------------


def test_the_preview_says_what_an_upgrade_costs_before_the_person_commits(session, ada, providers):
    sub = buy_plan(session, ada, providers.usd)
    sub.current_period_end = sub.current_period_start + timedelta(days=30)
    session.commit()
    half = sub.current_period_start + timedelta(days=15)
    before = len(providers.usd.checkouts)

    preview = subs.preview_plan_change(session, ada, "p40", half)

    assert (preview.kind, preview.amount_minor, preview.extra_seconds) == (
        "upgrade",
        800,
        10 * HOUR,
    )
    assert (preview.currency, preview.takes_effect) == ("USD", "now")
    # a preview is a read: no payment, no checkout, and the real change charges the same amount
    assert len(providers.usd.checkouts) == before and len(payments(session)) == 1
    change = subs.change_plan(session, ada, "p40", settings(), half)
    assert change.started.payment.amount_minor == preview.amount_minor


def test_the_preview_of_a_downgrade_and_of_taking_one_back(session, ada, providers):
    buy_plan(session, ada, providers.usd, plan="p40")

    down = subs.preview_plan_change(session, ada, "p20", T0 + timedelta(days=1))
    assert (down.kind, down.amount_minor, down.extra_seconds, down.takes_effect) == (
        "downgrade",
        1800,
        None,
        "next_renewal",
    )
    assert live(session, ada).pending_plan_code is None  # nothing was changed

    subs.change_plan(session, ada, "p20", settings(), T0 + timedelta(days=1))
    clear = subs.preview_plan_change(session, ada, "p40", T0 + timedelta(days=2))
    assert (clear.kind, clear.amount_minor, clear.takes_effect) == ("clear", None, "now")


def test_the_preview_refuses_for_the_same_reasons_as_the_change(session, ada, providers):
    with pytest.raises(subs.Refused) as none:
        subs.preview_plan_change(session, ada, "p40", T0)
    assert none.value.code == "no_subscription"
    buy_plan(session, ada, providers.usd)
    for code, expected in (("p20", "same_plan"), ("nope", "plan_not_found")):
        with pytest.raises(subs.Refused) as refused:
            subs.preview_plan_change(session, ada, code, T0 + timedelta(days=1))
        assert refused.value.code == expected


# ---- the rules follow the clock, not only the stored status ------------------------------------


def test_buttons_follow_the_clock_when_the_scheduler_has_not_run(session, ada, providers):
    """The plan's due moment passed an hour ago and nothing has looked at it yet (the scheduler was
    down): a person pressing a button is already in the grace, or past it."""
    sub = buy_plan(session, ada, providers.usd)
    due = sub.current_period_end
    in_grace = due + timedelta(hours=1)

    for target in ("p40",):
        with pytest.raises(subs.Refused) as refused:
            subs.change_plan(session, ada, target, settings(), in_grace)
        assert refused.value.code == "payment_overdue"
        session.rollback()
    subs.cancel(session, ada, in_grace)  # allowed, and it stops the retries
    session.commit()
    with pytest.raises(subs.Refused) as resume:
        subs.resume(session, ada, in_grace)
    assert resume.value.code == "payment_overdue"
    session.rollback()

    # cancelled, the period over and the scheduler still not run: there is nothing to resume
    with pytest.raises(subs.Refused) as over:
        subs.resume(session, ada, due + timedelta(hours=2))
    assert over.value.code == "no_subscription"
