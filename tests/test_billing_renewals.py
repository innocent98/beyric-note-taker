"""The billing clock, end to end with a fake clock and a scripted provider (billing stage S8a,
`app/billing/renewals.py`): renewals, the retries on day 1 and day 2, the 3-day grace, the lapse,
"no new hours until a payment succeeds", the pay link, a 3-D Secure charge's own link, an unknown
charge outcome, two workers at once, the sweeper, a country change that crossed routes, a price
change and a downgrade at renewal.

Real Postgres; the clock is the `now` each call is given; the provider is `FakeProvider`. Money
moves only through the real settlement rules (a charge answer is re-read before it grants).
"""

import threading
from datetime import timedelta

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app import entitlements, hours
from app.billing import plans, renewals, settle, states
from app.billing import subscriptions as subs
from app.billing.payments import new_payment
from app.billing.types import ProviderError, RetryLater
from app.models import (
    BillingEvent,
    HourGrant,
    Payment,
    PlanPrice,
    Subscription,
    UsageEntry,
)
from app.usage import email_hash
from tests.billing_support import (
    HOUR,
    P20,
    P40,
    T0,
    buy_plan,
    choose_country,
    deliver,
    grants,
    live,
    make_catalog,
    notices,
    pay,
    settings,
    status,
)
from tests.test_web import make_user

DAY = timedelta(days=1)


@pytest.fixture(autouse=True)
def catalog(session, vault_key):
    make_catalog(session)


@pytest.fixture
def ada(session):
    return make_user(session, "ada@example.test")


@pytest.fixture
def admin(session):
    return make_user(session, "admin@example.test", admin=True)


@pytest.fixture
def bought(session, ada, providers):
    """Ada holds a 20-hour plan bought on T0 with a saved card; returns its due moment."""
    return buy_plan(session, ada, providers.usd).current_period_end


def tick(session, now):
    renewals.run_due(session, now, settings())
    session.expire_all()


def renewal_payments(session) -> list[Payment]:
    session.expire_all()
    return list(
        session.scalars(select(Payment).where(Payment.kind == "renewal").order_by(Payment.id))
    )


def fail_all(providers):
    providers.usd.charge_outcome = "failed"


# ---- a renewal on time ------------------------------------------------------------------------


def test_the_saved_card_is_charged_at_the_due_moment_and_a_new_period_starts_at_it(
    session, ada, providers, bought
):
    due = bought
    tick(session, due - timedelta(seconds=1))
    assert providers.usd.charges == []  # nothing before the due moment

    tick(session, due + timedelta(seconds=7))

    [(token, amount, currency, reference)] = providers.usd.charges
    assert (token, amount, currency) == ("tok-1", 1800, "USD")
    sub = live(session, ada)
    assert sub.status == "active"
    assert (sub.current_period_start, sub.current_period_end) == (due, plans.add_months(due))
    assert sub.retry_count == 0 and sub.grace_ends_at is None and sub.next_charge_at is None
    old, new = grants(session, ada)
    assert (old.expires_at, new.starts_at, new.seconds) == (due, due, P20)
    [payment] = renewal_payments(session)
    assert (payment.reference, payment.status, payment.attempt, payment.cycle_end) == (
        reference,
        "succeeded",
        0,
        due,
    )
    assert payment.subscription_id == sub.id and new.source_payment_id == payment.id
    assert len(notices(session, ada, "billing.receipt")) == 2  # the first plan and this renewal


def test_unused_hours_do_not_roll_over(session, ada, providers, bought):
    due = bought
    entry = UsageEntry(user_id=ada.id, email_hash=email_hash(ada.email), seconds=8 * HOUR)
    session.add(entry)
    session.flush()
    hours.allocate(session, ada, entry, T0 + DAY)
    session.commit()
    assert status(session, ada, due - timedelta(hours=1)).seconds_left == P20 - 8 * HOUR

    tick(session, due + timedelta(seconds=7))

    assert status(session, ada, due + timedelta(hours=1)).seconds_left == P20  # not P20 + 12 h


def test_the_charge_asks_the_providers_email_and_an_unchanged_price_row(
    session, ada, providers, bought
):
    tick(session, bought + timedelta(seconds=7))
    [payment] = renewal_payments(session)
    assert payment.plan_price_id == live(session, ada).price_id
    assert (payment.provider, payment.currency, payment.amount_minor) == (
        "flutterwave",
        "USD",
        1800,
    )


# ---- the grace: retries on day 1 and day 2, then the lapse ------------------------------------


def test_every_attempt_fails_and_the_subscription_lapses_with_no_new_hours_ever(
    session, ada, admin, providers, bought
):
    due = bought
    fail_all(providers)
    first_grants = [g.id for g in grants(session, ada)]

    tick(session, due + timedelta(seconds=7))  # attempt 0

    sub = live(session, ada)
    assert (sub.status, sub.retry_count) == ("past_due", 1)
    assert sub.grace_ends_at == due + timedelta(hours=72)
    assert sub.next_charge_at == due + DAY
    assert sub.current_period_end == due  # the period is not renewed
    assert len(notices(session, ada, "billing.failed")) == 1  # the day-0 mail
    assert grants(session, ada)[0].expires_at == due + timedelta(hours=72)  # leftover: usable

    tick(session, due + timedelta(hours=23, minutes=59))
    assert len(providers.usd.charges) == 1  # nothing between the attempts

    tick(session, due + DAY)  # attempt 1 (day 1)
    assert (live(session, ada).retry_count, live(session, ada).next_charge_at) == (
        2,
        due + 2 * DAY,
    )
    assert notices(session, ada, "billing.last_day") == []

    tick(session, due + 2 * DAY)  # attempt 2 (day 2): the last
    sub = live(session, ada)
    assert (sub.status, sub.retry_count, sub.next_charge_at) == ("past_due", 3, None)
    assert len(notices(session, ada, "billing.last_day")) == 1  # the day-2 mail
    assert len(providers.usd.charges) == 3

    tick(session, due + timedelta(hours=71, minutes=59))
    assert live(session, ada).status == "past_due" and len(providers.usd.charges) == 3

    tick(session, due + timedelta(hours=72))  # the grace ends
    session.expire_all()
    sub = session.scalars(select(Subscription)).one()
    assert sub.status == "lapsed" and sub.next_charge_at is None
    assert live(session, ada) is None
    assert len(notices(session, ada, "billing.lapsed")) == 1

    # whatever happened, not one new hour was ever granted
    assert [g.id for g in grants(session, ada)] == first_grants
    assert [p.status for p in renewal_payments(session)] == ["failed"] * 3
    # a lapsed person can do nothing more, and nothing more is ever done to them
    tick(session, due + timedelta(days=10))
    assert len(providers.usd.charges) == 3


def test_during_the_grace_the_worst_case_is_zero_hours_beyond_what_was_paid_for(
    session, ada, providers, bought
):
    """Spec 3.3: no new hours until a payment succeeds, so the extra a person can record in the 3
    days is what was left of the period they paid for, and nothing else (the running-meeting
    overshoot is the only slack, and is not a grant)."""
    due = bought
    entry = UsageEntry(user_id=ada.id, email_hash=email_hash(ada.email), seconds=P20 - 1800)
    session.add(entry)
    session.flush()
    hours.allocate(session, ada, entry, T0 + DAY)  # 30 minutes of the period are left
    session.commit()
    fail_all(providers)

    for moment in (0, 24, 48):
        tick(session, due + timedelta(hours=moment, seconds=7))

    during = due + timedelta(hours=60)
    seen = status(session, ada, during)
    assert (seen.state, seen.seconds_left) == ("past_due", 1800)  # the leftover, still usable
    assert entitlements.check_join(session, ada, during).allowed
    assert entitlements.can(session, ada, "ask", during)  # Ask, translate, rewrite stay on
    assert len(grants(session, ada)) == 1  # not one new grant

    spent = UsageEntry(user_id=ada.id, email_hash=email_hash(ada.email), seconds=1800)
    session.add(spent)
    session.flush()
    hours.allocate(session, ada, spent, during)
    session.commit()
    after = status(session, ada, during + timedelta(minutes=1))
    assert (after.state, after.seconds_left) == ("past_due", 0)
    decision = entitlements.check_join(session, ada, during + timedelta(minutes=1))
    assert (decision.allowed, decision.reason) == (False, "no_hours")  # nothing new starts

    tick(session, due + timedelta(hours=72))
    end = status(session, ada, due + timedelta(hours=73))
    assert (end.state, end.seconds_left) == ("lapsed", 0)
    assert not entitlements.can(session, ada, "ask", due + timedelta(hours=73))
    assert len(grants(session, ada)) == 1  # to the very end


def test_the_leftover_of_the_old_period_ends_with_the_grace_not_before(
    session, ada, providers, bought
):
    due = bought
    fail_all(providers)
    tick(session, due + timedelta(seconds=7))
    assert status(session, ada, due + timedelta(hours=71)).seconds_left == P20
    assert status(session, ada, due + timedelta(hours=72)).seconds_left == 0


def test_payment_succeeds_on_day_2_starts_a_new_period_from_the_payment_date(
    session, ada, providers, bought
):
    due = bought
    fail_all(providers)
    tick(session, due + timedelta(seconds=7))
    tick(session, due + DAY)
    providers.usd.charge_outcome = "successful"  # the card works again

    tick(session, due + 2 * DAY + timedelta(minutes=2))  # attempt 2 pays

    sub = live(session, ada)
    paid = due + 2 * DAY + timedelta(minutes=2)
    assert sub.status == "active"
    assert (sub.current_period_start, sub.current_period_end) == (paid, plans.add_months(paid))
    assert (sub.grace_ends_at, sub.next_charge_at, sub.retry_count) == (None, None, 0)
    old, new = grants(session, ada)
    assert old.expires_at == paid  # the old leftover ends the moment the new period starts
    assert (new.starts_at, new.seconds) == (paid, P20)
    assert status(session, ada, paid + timedelta(hours=1)).seconds_left == P20  # no rollover
    assert [p.status for p in renewal_payments(session)] == ["failed", "failed", "succeeded"]
    assert notices(session, ada, "billing.lapsed") == []


def test_payment_succeeds_on_day_1_the_same_way(session, ada, providers, bought):
    due = bought
    fail_all(providers)
    tick(session, due + timedelta(seconds=7))
    providers.usd.charge_outcome = "successful"

    tick(session, due + DAY + timedelta(minutes=1))

    sub = live(session, ada)
    assert sub.status == "active" and sub.current_period_start == due + DAY + timedelta(minutes=1)


def test_a_worker_that_was_down_charges_once_and_never_fires_the_retries_back_to_back(
    session, ada, providers, bought
):
    due = bought
    fail_all(providers)
    late = due + timedelta(hours=30)  # nobody looked until 30 hours after the due moment

    tick(session, late)
    assert len(providers.usd.charges) == 1
    sub = live(session, ada)
    assert sub.retry_count == 1 and sub.grace_ends_at == due + timedelta(hours=72)
    assert sub.next_charge_at >= late + renewals.MIN_RETRY_GAP  # not "already due"

    tick(session, late + timedelta(minutes=5))
    assert len(providers.usd.charges) == 1


def test_no_saved_card_means_no_charge_a_recorded_failed_attempt_and_the_same_grace(
    session, ada, providers, bought
):
    from app.models import PaymentMethod

    session.query(PaymentMethod).delete()
    session.commit()
    due = bought

    tick(session, due + timedelta(seconds=7))

    assert providers.usd.charges == []
    sub = live(session, ada)
    assert (sub.status, sub.retry_count) == ("past_due", 1)
    [attempt] = renewal_payments(session)
    assert (attempt.status, attempt.attempt) == ("failed", 0)  # still written down


def test_a_card_that_cannot_be_opened_falls_back_to_the_pay_link(
    session, ada, providers, bought, monkeypatch
):
    from app import vault

    monkeypatch.setattr(vault, "decrypt", lambda *a, **k: (_ for _ in ()).throw(vault.VaultError()))

    tick(session, bought + timedelta(seconds=7))

    assert providers.usd.charges == [] and live(session, ada).status == "past_due"


# ---- the pay link ---------------------------------------------------------------------------


def test_the_pay_link_is_a_fresh_checkout_for_the_renewal_amount_and_paying_it_renews(
    session, ada, providers, bought
):
    due = bought
    fail_all(providers)
    tick(session, due + timedelta(seconds=7))

    started = renewals.pay_link(session, ada, settings(), due + timedelta(hours=3))

    assert started.url.startswith("https://fake.test/pay/")
    payment = started.payment
    assert (payment.kind, payment.amount_minor, payment.currency, payment.cycle_end) == (
        "renewal",
        1800,
        "USD",
        due,
    )
    assert payment.attempt is None  # a link is not a charge attempt
    pay(session, providers.usd, payment, due + timedelta(hours=4), card_token="tok-2")

    sub = live(session, ada)
    assert sub.status == "active" and sub.current_period_start == due + timedelta(hours=4)
    assert subs.saved_card(session, ada.id, "flutterwave")[0] == "tok-2"  # the new card is kept


def test_the_pay_link_is_only_for_a_plan_in_its_grace(session, ada, providers, bought):
    with pytest.raises(subs.Refused) as early:
        renewals.pay_link(session, ada, settings(), bought - DAY)
    assert (early.value.status, early.value.code) == (409, "nothing_due")
    session.rollback()
    with pytest.raises(subs.Refused) as none:
        renewals.pay_link(session, make_user(session, "ben@example.test"), settings(), bought)
    assert none.value.code == "no_subscription"


def test_a_3d_secure_charge_gets_its_own_link_and_never_a_second_checkout(
    session, ada, providers, bought
):
    due = bought
    providers.usd.charge_outcome = "pending"
    checkouts_before = len(providers.usd.checkouts)

    tick(session, due + timedelta(seconds=7))

    [payment] = renewal_payments(session)
    assert payment.status == "pending" and payment.pay_url == providers.usd.payer_action_url
    assert live(session, ada).status == "past_due" and live(session, ada).retry_count == 1

    started = renewals.pay_link(session, ada, settings(), due + timedelta(hours=2))
    assert started.url == providers.usd.payer_action_url  # THAT charge's link
    assert started.payment.id == payment.id
    assert len(providers.usd.checkouts) == checkouts_before  # no second checkout

    # day 1: the charge is still open with the payer: no new charge is made, its slot is spent
    tick(session, due + DAY)
    assert len(providers.usd.charges) == 1
    assert live(session, ada).retry_count == 2

    # the payer finally completes it: the provider reports a real transaction under our reference
    pay(session, providers.usd, payment, due + DAY + timedelta(hours=3), tx_id="real-3ds-1")

    sub = live(session, ada)
    assert sub.status == "active" and sub.current_period_start == due + DAY + timedelta(hours=3)
    assert session.scalars(
        select(Payment).where(Payment.id == payment.id)
    ).one().provider_tx_id == ("real-3ds-1")


def test_an_open_3d_secure_charge_keeps_its_own_link_however_old_it_is(
    session, ada, providers, bought
):
    due = bought
    providers.usd.charge_outcome = "pending"
    tick(session, due + timedelta(seconds=7))
    checkouts = len(providers.usd.checkouts)

    started = renewals.pay_link(session, ada, settings(), due + timedelta(days=2, hours=1))

    assert started.url == providers.usd.payer_action_url  # the charge's own page, two days on
    assert len(providers.usd.checkouts) == checkouts  # and the provider was ASKED, no new page
    assert providers.usd.lookups  # it is still open there


def test_a_new_page_only_after_the_provider_says_the_open_charge_failed(
    session, ada, providers, bought
):
    due = bought
    providers.usd.charge_outcome = "pending"
    tick(session, due + timedelta(seconds=7))
    [charge] = renewal_payments(session)
    providers.usd.complete(charge, status="failed", tx_id=charge.reference)  # abandoned at the bank

    started = renewals.pay_link(session, ada, settings(), due + timedelta(days=2))

    assert started.url != providers.usd.payer_action_url
    assert started.payment.attempt is None and started.payment.id != charge.id
    assert renewal_payments(session)[0].status == "failed"  # the old charge is closed


def test_the_pay_link_refuses_rather_than_guess_when_the_open_charge_cannot_be_checked(
    session, ada, providers, bought
):
    due = bought
    providers.usd.charge_outcome = "pending"
    tick(session, due + timedelta(seconds=7))
    checkouts = len(providers.usd.checkouts)
    providers.usd.fetch_error = ProviderError("down")

    with pytest.raises(subs.Refused) as refused:
        renewals.pay_link(session, ada, settings(), due + timedelta(days=2))

    assert (refused.value.status, refused.value.code) == (502, "provider_error")
    assert len(providers.usd.checkouts) == checkouts  # nothing new was made


def test_the_pay_link_settles_a_charge_the_provider_says_was_paid(session, ada, providers, bought):
    due = bought
    providers.usd.charge_outcome = "pending"
    tick(session, due + timedelta(seconds=7))
    [charge] = renewal_payments(session)
    providers.usd.complete(charge, tx_id="real-paid-1")  # the payer finished it after all

    with pytest.raises(subs.Refused) as refused:
        renewals.pay_link(session, ada, settings(), due + timedelta(hours=5))

    assert refused.value.code == "nothing_due"
    assert live(session, ada).status == "active"  # renewed, from the payment


def test_a_charge_in_flight_with_no_link_is_not_given_a_second_page(
    session, ada, providers, bought
):
    due = bought
    providers.usd.charge_outcome = "failed"
    tick(session, due + timedelta(seconds=7))  # past_due
    providers.usd.charge_outcome = "queued"
    tick(session, due + DAY)  # the day-1 charge is accepted and queued: no verdict, no link

    with pytest.raises(subs.Refused) as refused:
        renewals.pay_link(session, ada, settings(), due + DAY + timedelta(hours=1))

    assert (refused.value.status, refused.value.code) == (409, "payment_in_progress")


def test_a_queued_charge_is_not_a_failure_and_opens_no_grace(session, ada, providers, bought):
    due = bought
    providers.usd.charge_outcome = "queued"

    tick(session, due + timedelta(seconds=7))

    sub = live(session, ada)
    assert (sub.status, sub.retry_count) == ("active", 0)  # nothing decided
    assert notices(session, ada, "billing.failed") == []
    assert renewal_payments(session)[0].status == "pending"
    providers.usd.complete(renewal_payments(session)[0])  # it went through
    tick(session, due + timedelta(minutes=2))
    assert live(session, ada).current_period_start == due  # renewed on time


def test_a_late_payment_for_a_period_that_already_renewed_grants_nothing(
    session, ada, admin, providers, bought
):
    due = bought
    fail_all(providers)
    tick(session, due + timedelta(seconds=7))
    link = renewals.pay_link(session, ada, settings(), due + timedelta(hours=1))
    providers.usd.charge_outcome = "successful"
    tick(session, due + DAY)  # the day-1 retry pays first
    assert live(session, ada).status == "active"

    pay(session, providers.usd, link.payment, due + DAY + timedelta(hours=2))  # then the link too

    assert len(grants(session, ada)) == 2  # the first plan and ONE renewal
    assert live(session, ada).current_period_start == due + DAY
    [alert] = notices(session, admin, "admin.billing_refund_needed")
    assert str(link.payment.id) in alert.body


def test_a_late_payment_after_the_lapse_brings_the_subscription_back(
    session, ada, providers, bought
):
    due = bought
    fail_all(providers)
    tick(session, due + timedelta(seconds=7))
    link = renewals.pay_link(session, ada, settings(), due + timedelta(hours=1))
    for hours_in in (24, 48, 72):
        tick(session, due + timedelta(hours=hours_in))
    assert live(session, ada) is None  # lapsed

    paid = due + timedelta(days=5)
    pay(session, providers.usd, link.payment, paid)

    sub = live(session, ada)
    assert sub.status == "active" and sub.current_period_start == paid
    assert status(session, ada, paid + timedelta(hours=1)).state == "active"


def test_a_late_payment_beside_a_new_plan_the_person_bought_since_grants_nothing(
    session, ada, admin, providers, bought
):
    due = bought
    fail_all(providers)
    tick(session, due + timedelta(seconds=7))
    link = renewals.pay_link(session, ada, settings(), due + timedelta(hours=1))
    for hours_in in (24, 48, 72):
        tick(session, due + timedelta(hours=hours_in))
    again = subs.start_plan_checkout(session, ada, "p20", settings(), due + timedelta(days=4))
    pay(session, providers.usd, again.payment, due + timedelta(days=4))

    pay(session, providers.usd, link.payment, due + timedelta(days=5))  # the old link, too late

    assert (
        session.scalar(
            select(func.count()).select_from(Subscription).where(Subscription.status == "active")
        )
        == 1
    )
    assert notices(session, admin, "admin.billing_refund_needed")


# ---- an outcome nobody knows ------------------------------------------------------------------


@pytest.mark.parametrize("silence", ["error", "unknown"])  # ProviderError, OutcomeUnknown
def test_a_charge_with_no_answer_is_resolved_by_reference_never_charged_again(
    session, ada, providers, bought, silence
):
    due = bought
    providers.usd.charge_outcome = silence

    tick(session, due + timedelta(seconds=7))

    [payment] = renewal_payments(session)
    assert payment.status == "pending"
    sub = live(session, ada)
    assert (sub.status, sub.retry_count) == ("active", 0)  # nothing is decided about the person

    tick(session, due + timedelta(minutes=2))  # still no answer: wait, do not charge again
    assert len(providers.usd.charges) == 1 and live(session, ada).status == "active"

    providers.usd.complete(payment)  # it had gone through after all
    tick(session, due + timedelta(minutes=3))

    sub = live(session, ada)
    # paid within the hour after the due moment: the new period still starts AT it
    assert sub.status == "active" and sub.current_period_start == due
    assert len(providers.usd.charges) == 1  # exactly one charge, ever
    assert renewal_payments(session)[0].status == "succeeded"


def test_an_unknown_outcome_does_not_hold_up_the_lapse(session, ada, admin, providers, bought):
    due = bought
    providers.usd.charge_outcome = "error"
    tick(session, due + timedelta(seconds=7))
    # for the first six hours an unknown charge is waited on and the subscription stays `active`
    # (the clock alone, the effective status, protects the rest of the product); after that it is
    # given up and the status machine moves (review S1: it never stays `active` for ever)
    assert status(session, ada, due + timedelta(hours=73)).state == "lapsed"
    tick(session, due + timedelta(hours=7))
    assert notices(session, admin, "admin.billing_unresolved_charge")


def test_a_successful_answer_that_does_not_match_what_we_asked_grants_nothing(
    session, ada, admin, providers, bought, monkeypatch
):
    due = bought
    real = providers.usd.charge_saved_method

    def short(token, amount, currency, reference, **kw):
        tx = real(token, amount - 1, currency, reference, **kw)  # the provider took less
        return tx

    monkeypatch.setattr(providers.usd, "charge_saved_method", short)

    tick(session, due + timedelta(seconds=7))

    assert len(grants(session, ada)) == 1  # no renewal
    assert live(session, ada).status == "past_due"  # a failed attempt
    assert renewal_payments(session)[0].status == "pending"
    assert notices(session, admin, "admin.billing_payment_refused")


# ---- two at once -----------------------------------------------------------------------------


def test_a_subscription_another_worker_is_holding_is_skipped(
    session, ada, providers, bought, engine
):
    due = bought
    sub_id = live(session, ada).id
    with Session(engine) as holder:
        holder.scalars(
            select(Subscription).where(Subscription.id == sub_id).with_for_update()
        ).all()

        renewals.process_one(session, sub_id, due + timedelta(seconds=7), settings())

    assert providers.usd.charges == [] and renewal_payments(session) == []


@pytest.mark.parametrize("held", ["pending", "failed"])
def test_an_attempt_slot_that_is_taken_is_never_charged_twice(
    session, ada, providers, bought, held
):
    due = bought
    sub = live(session, ada)
    session.add(
        Payment(
            payer_user_id=ada.id,
            subscription_id=sub.id,
            provider="flutterwave",
            kind="renewal",
            currency="USD",
            amount_minor=1800,
            reference="bn-taken-slot",
            cycle_end=due,
            attempt=0,
            status=held,
            created_at=due,  # made by the other worker just now on the test's clock
        )
    )
    session.commit()

    renewals.process_one(session, sub.id, due + timedelta(seconds=7), settings())

    assert providers.usd.charges == []  # the unique slot held it back
    session.expire_all()
    after = live(session, ada)
    if held == "failed":
        # a pass recorded the attempt as failed and died before the status moved: finished now
        assert (after.status, after.retry_count) == ("past_due", 1)
    else:
        assert (after.status, after.retry_count) == ("active", 0)  # another worker owns it


def test_three_workers_racing_for_one_renewal_charge_once_and_renew_once(
    session, ada, providers, bought, engine
):
    due = bought
    sub_id = live(session, ada).id
    gate = threading.Barrier(3)
    errors = []

    def worker():
        try:
            with Session(engine, expire_on_commit=False) as mine:
                gate.wait(timeout=10)
                renewals.process_one(mine, sub_id, due + timedelta(seconds=7), settings())
        except Exception as exc:  # pragma: no cover - reported below
            errors.append(repr(exc))

    threads = [threading.Thread(target=worker) for _ in range(3)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=30)

    assert errors == []
    assert len(providers.usd.charges) == 1
    assert len(grants(session, ada)) == 2  # the first plan and one renewal
    assert [p.status for p in renewal_payments(session)] == ["succeeded"]
    assert live(session, ada).current_period_start == due


def test_two_renewal_payments_settled_at_once_grant_one_period(
    session, ada, admin, providers, bought, engine
):
    """The same period paid twice and settled by two workers at the same moment (a webhook and the
    sweeper): the subscription's row lock makes one the renewal and the other a duplicate."""
    due = bought
    fail_all(providers)
    tick(session, due + timedelta(seconds=7))
    one = renewals.pay_link(session, ada, settings(), due + timedelta(hours=1))
    # a second page for the same period (two tabs, or a link made before the fix): the pay link
    # itself no longer makes one, so the row is made directly
    sub = live(session, ada)
    second_row = new_payment(
        session,
        payer=ada,
        provider="flutterwave",
        kind="renewal",
        currency="USD",
        amount_minor=1800,
        subscription_id=sub.id,
    )
    second_row.plan_price_id, second_row.cycle_end = sub.price_id, sub.current_period_end
    session.commit()
    second = subs.Started(second_row, "https://fake.test/pay/second")
    txs = [
        providers.usd.complete(one.payment, tx_id="race-1"),
        providers.usd.complete(second.payment, tx_id="race-2"),
    ]
    gate = threading.Barrier(2)
    errors = []

    def settle_one(payment_id, tx):
        try:
            with Session(engine, expire_on_commit=False) as mine:
                gate.wait(timeout=10)
                settle.settle_transaction(
                    mine, providers.usd, payment_id, tx, due + timedelta(hours=3)
                )
                mine.commit()
        except Exception as exc:  # pragma: no cover - reported below
            errors.append(repr(exc))

    threads = [
        threading.Thread(target=settle_one, args=(p.id, tx))
        for p, tx in zip((one.payment, second.payment), txs, strict=True)
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=30)

    assert errors == []
    assert len(grants(session, ada)) == 2  # the first plan and ONE renewal
    assert live(session, ada).status == "active"
    assert len(notices(session, admin, "admin.billing_refund_needed")) == 1


# ---- the sweeper -----------------------------------------------------------------------------


def test_a_payment_whose_webhook_never_came_is_settled_by_asking_the_provider(
    session, ada, providers
):
    choose_country(session, ada, "US")
    started = subs.start_plan_checkout(session, ada, "p20", settings(), T0)
    providers.usd.complete(started.payment, card_token="tok-lost", payer_email="p@example.test")

    renewals.sweep(session, T0 + timedelta(minutes=1), settings())  # too young: the webhook's
    assert live(session, ada) is None

    renewals.sweep(session, T0 + timedelta(minutes=3), settings())

    sub = live(session, ada)
    assert sub is not None and sub.status == "active"
    assert subs.saved_card(session, ada.id, "flutterwave")[0] == "tok-lost"  # the card is kept too
    assert [g.seconds for g in grants(session, ada)] == [P20]


def test_the_sweeper_ignores_an_unpaid_checkout_and_old_ones(session, ada, providers):
    choose_country(session, ada, "US")
    started = subs.start_plan_checkout(session, ada, "p20", settings(), T0)
    providers.usd.complete(started.payment, status="pending")

    renewals.sweep(session, T0 + timedelta(minutes=10), settings())
    assert live(session, ada) is None and started.payment.status == "pending"

    providers.usd.complete(started.payment)  # paid, but four days on: nobody is looking any more
    renewals.sweep(session, T0 + timedelta(days=4), settings())
    assert live(session, ada) is None


def test_an_event_stored_but_never_decided_is_decided_by_the_sweeper(session, ada, providers):
    choose_country(session, ada, "US")
    started = subs.start_plan_checkout(session, ada, "p20", settings(), T0)
    tx = providers.usd.complete(started.payment, card_token="tok")
    body = providers.usd.event_body(providers.usd.event_for(tx))
    providers.usd.fetch_error = ProviderError("provider is down")

    with pytest.raises(RetryLater):  # the webhook answers 503 and the vendor will retry
        deliver(session, providers.usd, body, T0)
    row = session.scalars(select(BillingEvent)).one()
    assert row.processed_at is None and live(session, ada) is None

    providers.usd.fetch_error = None  # the provider is back, the vendor's retries have run out
    renewals.sweep(session, T0 + timedelta(minutes=5), settings())

    session.expire_all()
    assert session.scalars(select(BillingEvent)).one().processed_at is not None
    assert live(session, ada).status == "active"
    assert len(grants(session, ada)) == 1


def test_a_failed_sweep_never_stops_the_pass_or_the_clock(
    session, ada, providers, bought, monkeypatch
):
    due = bought
    monkeypatch.setattr(renewals, "sweep", lambda *a: (_ for _ in ()).throw(RuntimeError("boom")))

    renewals.run(session, due + timedelta(seconds=7), settings())  # the due part still ran

    assert len(providers.usd.charges) == 1 and live(session, ada).current_period_start == due


# ---- cancel, downgrade, price change, route change at the renewal --------------------------


def test_a_cancelled_plan_is_not_charged_and_ends_at_the_period_end(
    session, ada, providers, bought
):
    due = bought
    subs.cancel(session, ada, T0 + DAY)
    session.commit()

    tick(session, due + timedelta(seconds=7))

    assert providers.usd.charges == []
    session.expire_all()
    sub = session.scalars(select(Subscription)).one()
    assert sub.status == "canceled"
    assert status(session, ada, due + timedelta(hours=1)).state == "none"
    assert len(notices(session, ada, "billing.ended")) == 1
    assert notices(session, ada, "billing.lapsed") == []


def test_cancelling_in_the_grace_stops_the_retries_and_the_end_is_a_cancel_not_a_lapse(
    session, ada, providers, bought
):
    due = bought
    fail_all(providers)
    tick(session, due + timedelta(seconds=7))

    subs.cancel(session, ada, due + timedelta(hours=2))
    session.commit()
    tick(session, due + DAY)
    tick(session, due + 2 * DAY)
    assert len(providers.usd.charges) == 1  # no surprise charge after the person said stop

    tick(session, due + timedelta(hours=72))
    session.expire_all()
    assert session.scalars(select(Subscription)).one().status == "canceled"
    assert notices(session, ada, "billing.lapsed") == []
    with pytest.raises(subs.Refused):
        subs.resume(session, ada, due + timedelta(hours=80))


@pytest.mark.parametrize(("plan", "target"), [("p20", "p40"), ("p40", "p20")])
def test_in_the_grace_upgrade_downgrade_and_resume_are_refused(
    session, ada, providers, plan, target
):
    due = buy_plan(session, ada, providers.usd, plan=plan).current_period_end
    fail_all(providers)
    tick(session, due + timedelta(seconds=7))
    during = due + timedelta(hours=5)

    with pytest.raises(subs.Refused) as refused:
        subs.change_plan(session, ada, target, settings(), during)
    assert (refused.value.status, refused.value.code) == (409, "payment_overdue")
    session.rollback()
    subs.cancel(session, ada, during)
    session.commit()
    with pytest.raises(subs.Refused) as resume:
        subs.resume(session, ada, during)
    assert resume.value.code == "payment_overdue"


def test_a_downgrade_is_applied_by_the_renewal_at_the_cheaper_price(session, ada, providers):
    due = buy_plan(session, ada, providers.usd, plan="p40").current_period_end
    subs.change_plan(session, ada, "p20", settings(), T0 + DAY)
    session.commit()

    tick(session, due + timedelta(seconds=7))

    [(_, amount, _, _)] = providers.usd.charges
    assert amount == 1800
    sub = live(session, ada)
    assert (sub.plan_code, sub.pending_plan_code) == ("p20", None)
    assert [g.seconds for g in grants(session, ada)] == [
        P40,
        P20,
    ]  # the new period is the small one


def test_a_price_change_applies_at_the_renewal_not_before(session, ada, providers, bought):
    due = bought
    old_row = session.scalars(
        select(PlanPrice).where(PlanPrice.plan_code == "p20", PlanPrice.currency == "USD")
    ).one()
    session.add(
        PlanPrice(plan_code="p20", currency="USD", amount_minor=2000, active_from=due - 2 * DAY)
    )
    session.commit()
    assert (
        live(session, ada).price_id == old_row.id
    )  # the subscription keeps the price it was sold at

    tick(session, due + timedelta(seconds=7))

    [(_, amount, _, _)] = providers.usd.charges
    assert amount == 2000
    sub = live(session, ada)
    assert sub.price_id != old_row.id
    assert session.get(PlanPrice, sub.price_id).amount_minor == 2000


def test_a_country_change_that_crossed_routes_makes_the_renewal_a_fresh_checkout_on_the_new_route(
    session, ada, providers
):
    sub = buy_plan(session, ada, providers.ngn, country="NG")
    due = sub.current_period_end
    choose_country(
        session, ada, "US", now=T0 + 10 * DAY
    )  # crosses Nigeria and elsewhere: scheduled
    session.expire_all()
    from app.models import BillingProfile

    profile = session.scalars(select(BillingProfile)).one()
    assert profile.billing_country == "NG" and profile.pending_effective_at == due

    tick(session, due + timedelta(seconds=7))

    assert providers.ngn.charges == [] and providers.usd.charges == []  # the NG card cannot move
    assert live(session, ada).status == "past_due"
    started = renewals.pay_link(session, ada, settings(), due + timedelta(hours=1))
    assert (started.payment.provider, started.payment.currency, started.payment.amount_minor) == (
        "flutterwave",
        "USD",
        1800,
    )
    paid = due + timedelta(hours=2)
    pay(session, providers.usd, started.payment, paid, card_token="tok-us")

    sub = live(session, ada)
    assert (sub.provider, sub.currency, sub.status) == ("flutterwave", "USD", "active")
    assert sub.current_period_start == paid
    from app.billing.profile import live_subscription

    assert live_subscription(session, ada.id, paid).provider == "flutterwave"  # the route moved


def test_an_ended_account_is_never_charged(session, ada, providers, bought):
    subs.end_for_deleted_account(session, ada, T0 + DAY)
    session.commit()
    tick(session, bought + timedelta(seconds=7))
    assert providers.usd.charges == []
    session.expire_all()
    assert session.scalar(select(func.count()).select_from(HourGrant)) == 1


def test_the_scheduler_runs_the_billing_clock_only_while_billing_is_on(engine, monkeypatch):
    """The clock is a job of the leader-elected scheduler process (not the worker, which can sit
    for minutes in one transcription), on the sync loop so a slow provider never delays a join."""
    from datetime import timedelta as td

    from app.clock import SystemClock
    from app.scheduler import Scheduler, direct_connection_factory, register_jobs

    def registered(**over):
        cfg = settings(**{"calendar_enabled": False, **over})
        sched = Scheduler(
            SystemClock(),
            lambda: Session(engine),
            leader_conn_factory=direct_connection_factory(cfg.database_url),
        )
        register_jobs(sched, cfg)
        return sched

    off = registered(billing_enabled=False)
    assert "billing_clock" not in [j.name for loop in off._jobs.values() for j in loop]
    on = registered()
    [clock] = [j for j in on._jobs["sync"] if j.name == "billing_clock"]
    assert clock.every == td(seconds=60)
    assert on._jobs["dispatch"] == []  # never on the dispatch loop


def test_abandoned_checkouts_cannot_starve_a_payment_that_was_just_paid(session, ada, providers):
    """60 abandoned checkouts are older than the payment whose webhook was lost; the sweep takes
    the ones asked about least recently first and backs off the abandoned, so the paid one is
    settled in the first pass, and the abandoned are not asked about again a minute later."""
    from app.billing.payments import new_payment

    choose_country(session, ada, "US")
    for i in range(60):
        row = new_payment(
            session,
            payer=ada,
            provider="flutterwave",
            kind="pack",
            currency="USD",
            amount_minor=1000,
        )
        row.created_at = T0 - timedelta(minutes=30) + timedelta(seconds=i)
    session.commit()
    paid = subs.start_plan_checkout(session, ada, "p20", settings(), T0)
    providers.usd.complete(paid.payment, card_token="tok-lost")

    now = T0 + timedelta(minutes=3)
    renewals.sweep(session, now, settings())

    assert live(session, ada) is not None  # settled in the very first pass
    assert len(providers.usd.lookups) <= renewals.BATCH + 1
    # the next pass takes the ones never asked about; each payment has been asked about once
    renewals.sweep(session, now + timedelta(seconds=30), settings())
    assert len(providers.usd.lookups) == 61
    renewals.sweep(session, now + timedelta(seconds=60), settings())  # inside every back-off
    assert len(providers.usd.lookups) == 61  # nobody was asked about again too soon
    renewals.sweep(session, now + timedelta(minutes=45), settings())  # the 30-minute tier is due
    assert len(providers.usd.lookups) > 61


# ---- delta review: the pay link must never strand a person in the grace -------------------------


def test_an_unpaid_pay_link_page_is_returned_again_however_old_it_is(
    session, ada, providers, bought
):
    """The provider has no record of a checkout nobody paid. A day later the person presses Pay now
    again: the same page, never a 502 for the rest of the grace (it cannot double-charge)."""
    due = bought
    fail_all(providers)
    tick(session, due + timedelta(seconds=7))
    first = renewals.pay_link(session, ada, settings(), due + timedelta(hours=1))
    checkouts = len(providers.usd.checkouts)

    for later in (timedelta(days=1, hours=2), timedelta(days=2, hours=1)):
        again = renewals.pay_link(session, ada, settings(), due + later)
        assert again.url == first.url and again.payment.id == first.payment.id
    assert len(providers.usd.checkouts) == checkouts  # and no second page was made


def test_a_pay_link_page_being_opened_right_now_blocks_a_second_click(
    session, ada, providers, bought
):
    """Request A has committed its row and is waiting for the provider's page (the lock is free
    meanwhile). A double click must not make a second page for the same period."""
    due = bought
    fail_all(providers)
    tick(session, due + timedelta(seconds=7))
    sub = live(session, ada)
    opening = new_payment(
        session,
        payer=ada,
        provider="flutterwave",
        kind="renewal",
        currency="USD",
        amount_minor=1800,
        subscription_id=sub.id,
    )
    opening.plan_price_id, opening.cycle_end = sub.price_id, sub.current_period_end
    opening.created_at = due + timedelta(hours=1)
    session.commit()
    checkouts = len(providers.usd.checkouts)

    with pytest.raises(subs.Refused) as refused:
        renewals.pay_link(session, ada, settings(), due + timedelta(hours=1, seconds=30))

    assert (refused.value.status, refused.value.code) == (409, "payment_in_progress")
    assert len(providers.usd.checkouts) == checkouts  # no second page
    session.expire_all()
    assert session.get(Payment, opening.id).status == "pending"  # and the first is left alone


def test_a_half_made_pay_link_row_with_no_page_is_abandoned_not_a_block(
    session, ada, providers, bought
):
    due = bought
    fail_all(providers)
    tick(session, due + timedelta(seconds=7))
    sub = live(session, ada)
    orphan = new_payment(
        session,
        payer=ada,
        provider="flutterwave",
        kind="renewal",
        currency="USD",
        amount_minor=1800,
        subscription_id=sub.id,
    )
    orphan.plan_price_id, orphan.cycle_end = sub.price_id, sub.current_period_end
    orphan.created_at = due + timedelta(hours=1)  # the process died before the page was stored
    session.commit()

    started = renewals.pay_link(session, ada, settings(), due + timedelta(hours=1, minutes=11))

    assert started.payment.id != orphan.id and started.url.startswith("https://fake.test/pay/")
    session.expire_all()
    assert session.get(Payment, orphan.id).status == "failed"


def test_a_queued_charge_that_stays_pending_alerts_an_admin_after_six_hours(
    session, ada, admin, providers, bought
):
    due = bought
    providers.usd.charge_outcome = "queued"
    tick(session, due + timedelta(seconds=7))
    assert notices(session, admin, "admin.billing_unresolved_charge") == []

    tick(session, due + timedelta(hours=7))  # still pending at the provider, nothing for the payer

    assert notices(session, admin, "admin.billing_unresolved_charge")


def test_a_payment_the_rules_refuse_is_not_reported_as_received(
    session, ada, admin, providers, bought
):
    due = bought
    providers.usd.charge_outcome = "pending"
    tick(session, due + timedelta(seconds=7))
    [charge] = renewal_payments(session)
    providers.usd.complete(charge, tx_id="short-1", amount_minor=charge.amount_minor - 1)

    with pytest.raises(subs.Refused) as refused:
        renewals.pay_link(session, ada, settings(), due + timedelta(hours=3))

    assert (refused.value.status, refused.value.code) == (409, "payment_in_progress")
    assert "received" not in refused.value.message
    assert notices(session, admin, "admin.billing_payment_refused")
    assert live(session, ada).status == "past_due"  # nothing was granted


def test_the_sweeper_alerts_an_admin_for_a_paid_payment_the_rules_refuse(
    session, ada, admin, providers
):
    choose_country(session, ada, "US")
    started = subs.start_plan_checkout(session, ada, "p20", settings(), T0)
    providers.usd.complete(started.payment, amount_minor=started.payment.amount_minor - 1)

    renewals.sweep(session, T0 + timedelta(minutes=3), settings())

    assert live(session, ada) is None  # nothing granted
    assert notices(session, admin, "admin.billing_payment_refused")


# ---- review S1: an attempt whose outcome cannot be found must end in a defined state -----------


def test_a_charge_the_provider_refused_outright_is_a_failed_attempt_at_once(
    session, ada, admin, providers, bought
):
    """A 4xx, a missing key, an input check: the call made no charge, so there is nothing to look
    up. The attempt failed, the grace opens, the person can pay, and the admins are told."""
    due = bought
    providers.usd.charge_outcome = "refused"

    tick(session, due + timedelta(seconds=7))

    [payment] = renewal_payments(session)
    assert payment.status == "failed"
    sub = live(session, ada)
    assert (sub.status, sub.retry_count) == ("past_due", 1)
    assert sub.grace_ends_at == states.grace_end(due)
    assert sub.next_charge_at == due + DAY
    assert notices(session, ada, "billing.failed")
    assert notices(session, admin, "admin.billing_charge_refused")

    started = renewals.pay_link(session, ada, settings(), due + timedelta(hours=1))
    assert started.url.startswith("https://fake.test/pay/")  # a page, not a 502

    providers.usd.charge_outcome = "successful"  # the day-1 retry is made as the table says
    tick(session, due + DAY + timedelta(seconds=7))
    assert len(providers.usd.charges) == 2


@pytest.mark.parametrize("silence", ["error", "unknown"])  # ProviderError, OutcomeUnknown
def test_a_charge_nobody_can_find_moves_the_clock_after_six_hours_but_is_never_charged_again(
    session, ada, admin, providers, bought, silence
):
    """The charge call got no answer and the lookup by our reference finds nothing, for ever. The
    attempt waits (the provider may be down) for UNRESOLVED_AFTER, then counts as a failed attempt
    FOR THE CLOCK: grace, mails, the retry slots and the lapse run. But its payment stays
    `pending`, because the charge may exist: nothing is charged again for the period while it is
    unresolved (a second charge could debit the person twice), so the plan runs out its grace and
    lapses, and a new plan can be bought once it has."""
    due = bought
    providers.usd.charge_outcome = silence

    tick(session, due + timedelta(seconds=7))
    tick(session, due + timedelta(hours=5))
    [payment] = renewal_payments(session)
    assert payment.status == "pending"  # still waiting: the provider may just be down
    assert live(session, ada).status == "active"
    assert notices(session, admin, "admin.billing_unresolved_charge") == []

    tick(session, due + timedelta(hours=7))

    [payment] = renewal_payments(session)
    assert payment.status == "pending"  # never closed on a guess
    sub = live(session, ada)
    assert (sub.status, sub.retry_count) == ("past_due", 1)
    assert sub.grace_ends_at == states.grace_end(due)
    assert notices(session, ada, "billing.failed")
    assert notices(session, admin, "admin.billing_unresolved_charge")

    tick(session, due + DAY + timedelta(seconds=7))  # the day-1 slot: counted, NOT charged
    tick(session, due + 2 * DAY + timedelta(seconds=7))  # the day-2 slot
    assert len(providers.usd.charges) == 1
    sub = live(session, ada)
    assert (sub.status, sub.retry_count) == ("past_due", 3)
    assert notices(session, ada, "billing.last_day")
    tick(session, due + timedelta(hours=73))
    assert live(session, ada) is None  # lapsed by the clock
    new = subs.start_plan_checkout(session, ada, "p20", settings(), due + timedelta(hours=74))
    assert new.payment.kind == "plan"


@pytest.mark.parametrize("silence", ["error", "unknown"])
def test_a_charge_given_up_that_turns_out_paid_is_settled_by_the_next_attempt_not_charged_again(
    session, ada, admin, providers, bought, silence
):
    """The provider's lookup was down for the six hours (so the charge was given up for the
    clock), and has the answer by the day-1 slot: the payment is looked up FIRST, settled, and
    nothing is charged again. One charge, one new period."""
    due = bought
    providers.usd.charge_outcome = silence
    providers.usd.fetch_error = ProviderError("the lookup is down")
    tick(session, due + timedelta(seconds=7))
    tick(session, due + timedelta(hours=7))
    [payment] = renewal_payments(session)
    assert live(session, ada).status == "past_due"

    providers.usd.fetch_error = None
    providers.usd.complete(payment)  # it had gone through after all
    tick(session, due + DAY + timedelta(seconds=7))

    sub = live(session, ada)
    assert sub.status == "active" and sub.retry_count == 0
    assert len(providers.usd.charges) == 1  # exactly one charge, ever
    assert renewal_payments(session)[0].status == "succeeded"
    assert len(grants(session, ada, "plan_period")) == 2  # one new period, once


def test_a_given_up_charge_paid_by_its_webhook_later_is_still_settled(
    session, ada, admin, providers, bought
):
    due = bought
    providers.usd.charge_outcome = "error"
    tick(session, due + timedelta(seconds=7))
    tick(session, due + timedelta(hours=7))
    [payment] = renewal_payments(session)
    assert live(session, ada).status == "past_due"

    pay(session, providers.usd, payment, due + timedelta(hours=8))

    sub = live(session, ada)
    assert sub.status == "active" and sub.retry_count == 0
    assert renewal_payments(session)[0].status == "succeeded"
    assert len(grants(session, ada, "plan_period")) == 2


def test_a_queued_charge_that_succeeds_after_the_window_is_never_charged_twice(
    session, ada, admin, providers, bought
):
    """The provider itself says `pending`, nothing for the payer to do. Six hours on the clock
    moves; the day-1 slot asks first, finds it paid, settles it and charges nothing."""
    due = bought
    providers.usd.charge_outcome = "queued"
    tick(session, due + timedelta(seconds=7))
    tick(session, due + timedelta(hours=7))
    [payment] = renewal_payments(session)
    assert payment.status == "pending" and live(session, ada).status == "past_due"
    assert notices(session, admin, "admin.billing_unresolved_charge")

    providers.usd.complete(payment)
    tick(session, due + DAY + timedelta(seconds=7))

    assert len(providers.usd.charges) == 1
    sub = live(session, ada)
    assert sub.status == "active" and sub.retry_count == 0
    assert renewal_payments(session)[0].status == "succeeded"
    assert len(grants(session, ada, "plan_period")) == 2


def test_a_queued_charge_still_pending_at_the_retry_moves_the_clock_and_charges_nothing(
    session, ada, admin, providers, bought
):
    due = bought
    providers.usd.charge_outcome = "queued"
    tick(session, due + timedelta(seconds=7))
    tick(session, due + timedelta(hours=7))
    tick(session, due + DAY + timedelta(seconds=7))
    tick(session, due + 2 * DAY + timedelta(seconds=7))

    assert len(providers.usd.charges) == 1  # the open charge is never repeated
    sub = live(session, ada)
    assert (sub.status, sub.retry_count) == ("past_due", 3)
    assert renewal_payments(session)[0].status == "pending"
    tick(session, due + timedelta(hours=73))
    assert live(session, ada) is None  # lapsed; a late success would still settle (webhook)


def test_the_pay_link_makes_no_second_page_beside_a_charge_nobody_can_find(
    session, ada, admin, providers, bought
):
    """A page paid beside a charge that may exist is a double charge: it waits (502, retry) until
    the provider can say what became of the charge."""
    due = bought
    providers.usd.charge_outcome = "error"
    tick(session, due + timedelta(seconds=7))
    tick(session, due + timedelta(hours=7))
    pages = len(providers.usd.checkouts)

    with pytest.raises(subs.Refused) as refused:
        renewals.pay_link(session, ada, settings(), due + timedelta(hours=8))

    assert (refused.value.status, refused.value.code) == (502, "provider_error")
    assert len(providers.usd.checkouts) == pages


def test_a_charge_blocked_by_our_key_opens_the_grace_without_any_failure_for_the_person(
    session, ada, admin, providers, bought
):
    """A 401 or a missing key (ours) and a 429 (the provider's) say nothing about the person: the
    grace opens (their leftover hours stay usable, Pay now works) but no retry is spent and no
    mail says the payment failed. The admins are told and the charge is tried again later."""
    due = bought
    providers.usd.charge_outcome = "blocked"

    tick(session, due + timedelta(seconds=7))

    sub = live(session, ada)
    assert (sub.status, sub.retry_count) == ("past_due", 0)  # no retry spent
    assert sub.grace_ends_at == states.grace_end(due)
    assert sub.next_charge_at == due + timedelta(seconds=7) + renewals.BLOCKED_RETRY
    assert grants(session, ada, "plan_period")[0].expires_at == sub.grace_ends_at  # hours kept
    assert notices(session, ada, "billing.failed") == []
    assert notices(session, admin, "admin.billing_charge_blocked")
    [payment] = renewal_payments(session)
    assert payment.status == "failed" and payment.attempt is None  # nothing sent; slot is free
    assert payment.provider_tx_id == f"blocked-{payment.reference}"  # told apart from a page

    tick(session, due + timedelta(minutes=2))  # not tried again at once
    assert len(providers.usd.charges) == 1

    providers.usd.charge_outcome = "successful"  # the key is fixed
    tick(session, due + timedelta(hours=1))
    assert len(providers.usd.charges) == 2
    sub = live(session, ada)
    assert sub.status == "active" and sub.next_charge_at is None and sub.grace_ends_at is None
    assert len(grants(session, ada, "plan_period")) == 2


def test_a_block_late_in_the_grace_extends_it_so_our_outage_does_not_lapse_the_plan(
    session, ada, admin, providers, bought
):
    due = bought
    providers.usd.charge_outcome = "failed"  # the first charge is a real decline: the grace opens
    tick(session, due + timedelta(seconds=7))
    providers.usd.charge_outcome = "blocked"  # then OUR key breaks
    tick(session, due + timedelta(hours=71, minutes=45))
    sub = live(session, ada)
    assert sub.retry_count == 1  # the blocked pass spent nothing
    assert sub.grace_ends_at == due + timedelta(hours=71, minutes=45) + timedelta(hours=24)

    tick(session, due + timedelta(hours=73))  # past the normal end: still not lapsed
    assert live(session, ada).status == "past_due"
    assert notices(session, ada, "billing.lapsed") == []

    providers.usd.charge_outcome = "successful"  # fixed
    tick(session, due + timedelta(hours=73, minutes=30))
    assert live(session, ada).status == "active"


def test_the_extension_for_a_block_has_a_cap_and_then_the_plan_lapses_as_told(
    session, ada, admin, providers, bought
):
    due = bought
    providers.usd.charge_outcome = "blocked"
    for step in range(0, 24 * 9 + 1, 6):  # a key broken for nine days, a pass every six hours
        tick(session, due + timedelta(hours=step, seconds=7))
    assert live(session, ada).status == "past_due"  # still inside grace + the cap (3 + 7 days)
    assert notices(session, ada, "billing.lapsed") == []

    tick(session, due + timedelta(days=10, hours=1))

    assert live(session, ada) is None  # lapsed, the cap reached
    assert notices(session, ada, "billing.lapsed")
    assert notices(session, ada, "billing.failed") == []  # never told "your payment failed"


def test_the_blocked_alert_is_sent_once_every_six_hours_not_every_pass(
    session, ada, admin, providers, bought
):
    due = bought
    providers.usd.charge_outcome = "blocked"
    tick(session, due + timedelta(seconds=7))
    tick(session, due + timedelta(hours=1))
    tick(session, due + timedelta(hours=2))
    assert len(notices(session, admin, "admin.billing_charge_blocked")) == 1

    tick(session, due + timedelta(hours=6, minutes=10))

    assert len(notices(session, admin, "admin.billing_charge_blocked")) == 2


def test_an_open_pay_link_page_holds_the_saved_card_retry_for_two_hours(
    session, ada, admin, providers, bought
):
    """A person who opened the page is probably paying it: the retry waits, so the page and the
    card are not both charged. A page abandoned for two hours no longer holds anything."""
    due = bought
    providers.usd.charge_outcome = "failed"
    tick(session, due + timedelta(seconds=7))  # attempt 0 declined: the grace opens
    renewals.pay_link(session, ada, settings(), due + timedelta(hours=23, minutes=30))
    charges = len(providers.usd.charges)

    tick(session, due + DAY + timedelta(seconds=7))  # the day-1 retry is due, the page is open
    assert len(providers.usd.charges) == charges
    assert live(session, ada).retry_count == 1  # waiting spends no slot

    tick(session, due + timedelta(hours=25, minutes=40))  # the page is two hours old
    assert len(providers.usd.charges) == charges + 1


def test_a_person_who_cannot_pay_beside_an_unresolved_charge_is_reported_every_day(
    session, ada, admin, providers, bought
):
    """The dead end (the mail says open Billing and pay, the page cannot be made while a charge
    may exist) must not be silent: the admins are told when it is hit and again each day."""
    due = bought
    providers.usd.charge_outcome = "error"
    tick(session, due + timedelta(seconds=7))
    tick(session, due + timedelta(hours=7))
    assert len(notices(session, admin, "admin.billing_unresolved_charge")) == 1

    for later in (8, 9):
        with pytest.raises(subs.Refused):
            renewals.pay_link(session, ada, settings(), due + timedelta(hours=later))
    assert len(notices(session, admin, "admin.billing_payer_blocked")) == 1  # once a day

    tick(session, due + DAY + timedelta(seconds=7))  # the next day's pass reminds them again
    assert len(notices(session, admin, "admin.billing_unresolved_charge")) == 2
    with pytest.raises(subs.Refused):
        renewals.pay_link(session, ada, settings(), due + DAY + timedelta(hours=1))
    assert len(notices(session, admin, "admin.billing_payer_blocked")) == 2


def test_giving_up_never_overwrites_a_payment_a_webhook_settled_meanwhile(
    session, ada, admin, providers, bought, engine, monkeypatch
):
    """The lookup fails, and while we waited on it a webhook (another process) settled the payment:
    the give-up is a conditional update, so it finds nothing to close and leaves `succeeded`."""
    due = bought
    providers.usd.charge_outcome = "error"
    tick(session, due + timedelta(seconds=7))
    [payment] = renewal_payments(session)

    def settled_elsewhere_then_fail(reference):
        with Session(engine) as other:
            row = other.get(Payment, payment.id)
            row.status = "succeeded"
            other.commit()
        raise ProviderError("down")

    monkeypatch.setattr(providers.usd, "verify_reference", settled_elsewhere_then_fail)

    tick(session, due + timedelta(hours=7))

    assert renewal_payments(session)[0].status == "succeeded"  # not overwritten with `failed`
    assert notices(session, admin, "admin.billing_unresolved_charge") == []
    assert notices(session, ada, "billing.failed") == []
