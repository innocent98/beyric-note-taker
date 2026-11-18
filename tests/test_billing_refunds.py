"""Refunds (billing stage S9, `app/billing/refunds.py`): the three cases of spec 3.5 and nothing
else, the removal of the hours a refund covers, and above all that a refund can never be paid twice.

Real Postgres; the provider is scripted (`FakeProvider`, named after the real one the route picks);
the payer pays through the REAL webhook inbox and settlement rules. The races are real: two
connections (threads, each its own session) ask for the same refund, and a recording is counted
on a second connection while the refund waits for the person's hours lock.
"""

import threading
import time
from datetime import timedelta

import pytest
from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from app import entitlements, hours
from app.billing import refunds
from app.billing import subscriptions as subs
from app.billing.payments import new_payment
from app.billing.types import OutcomeUnknown, ProviderError
from app.models import (
    HourGrant,
    Meeting,
    Notification,
    Payment,
    PaymentMethod,
    PlanPrice,
    Refund,
    Subscription,
    Template,
    UsageAllocation,
    UsageEntry,
)
from app.usage import email_hash
from tests.billing_support import (  # noqa: F401  (fixtures used by name)
    HOUR,
    P20,
    T0,
    buy_plan,
    choose_country,
    grants,
    live,
    make_catalog,
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


def payment_of(session, kind="plan") -> Payment:
    session.expire_all()
    return session.scalars(
        select(Payment).where(Payment.kind == kind).order_by(Payment.id.desc())
    ).first()


def refund_of(session, payment) -> Refund:
    session.expire_all()
    return session.scalars(
        select(Refund).where(Refund.payment_id == payment.id).order_by(Refund.id.desc())
    ).first()


def ask(session, payment, reason, admin, now, note=None):
    return refunds.request(session, payment.id, reason, admin, note, settings(), now)


def refused(session, payment, reason, admin, now) -> str:
    with pytest.raises(refunds.Refused) as exc:
        ask(session, payment, reason, admin, now)
    session.rollback()
    assert exc.value.status == 409
    return exc.value.code


def buy_pack(session, user, provider, now):
    started = subs.start_pack_checkout(session, user, "pack10", settings(), now)
    pay(session, provider, started.payment, now)
    return payment_of(session, "pack")


def spend(session, user, seconds, now):
    """A counted recording of `seconds`, split across the person's grants like the pipeline does."""
    entry = UsageEntry(user_id=user.id, email_hash=email_hash(user.email), seconds=seconds)
    session.add(entry)
    session.flush()
    hours.allocate(session, user, entry, now)
    session.commit()


# ---- a first plan payment, within 7 days, no hours used ---------------------------------------


def test_a_first_plan_payment_is_refunded_and_takes_its_hours_and_its_plan_with_it(
    session, ada, admin, providers
):
    buy_plan(session, ada, providers.usd)
    payment = payment_of(session)
    now = T0 + 2 * DAY
    assert status(session, ada, now).seconds_left == P20

    refund = ask(session, payment, "first_plan_7d", admin, now, note="  asked by email  ")

    assert refund.status == "submitted" and refund.reason == "first_plan_7d"
    assert (refund.amount_minor, refund.currency) == (1800, "USD")  # always the whole payment
    assert refund.note == "asked by email" and refund.created_by == admin.id
    assert refund.provider_refund_id == "fake-refund-1" and refund.resolved_at == now
    # the provider was asked exactly once, for the whole payment, with our own reference
    assert providers.usd.refund_calls == [
        (payment.provider_tx_id, 1800, refund.reference),
    ]
    assert refund.reference.startswith("bnrf-") and 8 <= len(refund.reference) <= 64
    session.expire_all()
    assert session.get(Payment, payment.id).status == "refunded"
    assert live(session, ada) is None  # the plan ended
    assert session.scalars(select(Subscription)).one().status == "ended"
    assert session.scalar(select(func.count()).select_from(PaymentMethod)) == 0  # card deleted
    held = status(session, ada, now)
    assert held.seconds_left == 0 and held.state == "none"  # the hours it bought are gone
    [grant] = grants(session, ada, "plan_period")
    assert grant.seconds == P20 and grant.expires_at == now  # kept as history, ended at the refund
    told = session.scalars(select(Notification).where(Notification.kind == "billing.refund")).all()
    assert len(told) == 1 and "USD 18.00" in told[0].body


def test_the_seventh_day_is_inside_the_window_and_one_second_later_is_not(
    session, ada, admin, providers
):
    buy_plan(session, ada, providers.usd)
    payment = payment_of(session)
    assert refused(
        session, payment, "first_plan_7d", admin, T0 + 7 * DAY + timedelta(seconds=1)
    ) == ("window_over")
    assert providers.usd.refund_calls == []  # refused before the provider was ever asked
    assert ask(session, payment, "first_plan_7d", admin, T0 + 7 * DAY).status == "submitted"


def test_hours_used_from_the_payment_refuse_the_refund(session, ada, admin, providers):
    buy_plan(session, ada, providers.usd)
    payment = payment_of(session)
    spend(session, ada, HOUR, T0 + DAY)

    assert refused(session, payment, "first_plan_7d", admin, T0 + 2 * DAY) == "hours_used"
    assert providers.usd.refund_calls == []
    assert status(session, ada, T0 + 2 * DAY).seconds_left == P20 - HOUR  # nothing was removed
    assert refund_of(session, payment) is None


def test_trial_hours_are_spent_first_so_a_person_who_only_used_the_trial_can_be_refunded(
    session, ada, admin, providers
):
    session.add(
        HourGrant(
            user_id=ada.id,
            email_hash=email_hash(ada.email),
            kind="trial",
            seconds=3 * HOUR,
            starts_at=T0 - DAY,
            expires_at=T0 + 10 * DAY,
        )
    )
    session.commit()
    buy_plan(session, ada, providers.usd)
    payment = payment_of(session)
    spend(session, ada, 2 * HOUR, T0 + DAY)  # comes out of the trial, the plan is untouched

    assert ask(session, payment, "first_plan_7d", admin, T0 + 2 * DAY).status == "submitted"
    assert status(session, ada, T0 + 2 * DAY).seconds_left == HOUR  # the trial's last hour stays


def test_a_recording_in_progress_blocks_it(session, ada, admin, providers):
    buy_plan(session, ada, providers.usd)
    payment = payment_of(session)
    now = T0 + DAY
    template = Template(owner_user_id=ada.id, name="General", instructions="x")
    session.add(template)
    session.flush()
    session.add(
        Meeting(
            template_id=template.id,
            owner_user_id=ada.id,
            url="https://meet.google.com/abc-defg-hij",
            platform="google_meet",
            native_id="abc-defg-hij",
            status="bot_sent",
            started_polling_at=now - timedelta(minutes=1),
        )
    )
    session.commit()
    assert refused(session, payment, "first_plan_7d", admin, now) == "recording_in_progress"


def test_only_the_first_plan_payment_ever_can_be_refunded_this_way(session, ada, admin, providers):
    buy_plan(session, ada, providers.usd)
    first = payment_of(session)
    ask(session, first, "first_plan_7d", admin, T0 + DAY)
    # the person buys again after the refund, and that is not a first payment
    started = subs.start_plan_checkout(session, ada, "p20", settings(), T0 + 2 * DAY)
    pay(session, providers.usd, started.payment, T0 + 2 * DAY)
    second = payment_of(session)
    assert second.id != first.id and second.status == "succeeded"
    assert refused(session, second, "first_plan_7d", admin, T0 + 3 * DAY) == "not_first_payment"


def test_a_plan_that_was_upgraded_cannot_be_refunded_by_its_first_payment(
    session, ada, admin, providers
):
    sub = buy_plan(session, ada, providers.usd)
    first = payment_of(session)
    sub.current_period_end = sub.current_period_start + timedelta(days=30)
    session.commit()
    started = subs.change_plan(session, ada, "p40", settings(), T0 + DAY).started
    pay(session, providers.usd, started.payment, T0 + DAY)
    assert refused(session, first, "first_plan_7d", admin, T0 + 2 * DAY) == "plan_changed"


def test_the_case_must_fit_the_payment(session, ada, admin, providers):
    buy_plan(session, ada, providers.usd)
    plan_payment = payment_of(session)
    pack_payment = buy_pack(session, ada, providers.usd, T0 + DAY)
    assert refused(session, plan_payment, "pack_14d", admin, T0 + 2 * DAY) == "wrong_kind"
    assert refused(session, pack_payment, "first_plan_7d", admin, T0 + 2 * DAY) == "wrong_kind"
    # a used payment is not a double charge, whatever the admin says it is
    assert refused(session, plan_payment, "double_charge", admin, T0 + 2 * DAY) == (
        "not_a_double_charge"
    )
    assert refused(session, pack_payment, "double_charge", admin, T0 + 2 * DAY) == (
        "not_a_double_charge"
    )
    assert providers.usd.refund_calls == []


def test_a_renewal_and_an_upgrade_are_never_refundable_as_a_first_plan(
    session, ada, admin, providers
):
    sub = buy_plan(session, ada, providers.usd)
    sub.current_period_end = sub.current_period_start + timedelta(days=30)
    session.commit()
    started = subs.change_plan(session, ada, "p40", settings(), T0 + DAY).started
    pay(session, providers.usd, started.payment, T0 + DAY)
    upgrade = payment_of(session, "upgrade")
    assert refused(session, upgrade, "first_plan_7d", admin, T0 + 2 * DAY) == "wrong_kind"
    assert refused(session, upgrade, "pack_14d", admin, T0 + 2 * DAY) == "wrong_kind"
    assert refused(session, upgrade, "double_charge", admin, T0 + 2 * DAY) == "not_a_double_charge"


# ---- an unused pack within 14 days ---------------------------------------------------------


def test_an_unused_pack_is_refunded_within_14_days_and_its_hours_go(session, ada, admin, providers):
    buy_plan(session, ada, providers.usd)
    pack = buy_pack(session, ada, providers.usd, T0 + DAY)
    now = T0 + 10 * DAY
    assert status(session, ada, now).seconds_left == P20 + 10 * HOUR

    refund = ask(session, pack, "pack_14d", admin, now)

    assert refund.status == "submitted" and refund.amount_minor == 1000
    session.expire_all()
    assert session.get(Payment, pack.id).status == "refunded"
    assert status(session, ada, now).seconds_left == P20  # only the pack's hours went
    assert live(session, ada).status == "active"  # a pack refund never touches the plan
    assert session.scalar(select(func.count()).select_from(PaymentMethod)) == 1
    [pack_grant] = grants(session, ada, "pack")
    assert pack_grant.expires_at == now


def test_the_fourteenth_day_is_in_and_one_second_later_is_out(session, ada, admin, providers):
    buy_plan(session, ada, providers.usd)
    pack = buy_pack(session, ada, providers.usd, T0 + DAY)
    paid = pack.paid_at
    assert refused(session, pack, "pack_14d", admin, paid + 14 * DAY + timedelta(seconds=1)) == (
        "window_over"
    )
    assert ask(session, pack, "pack_14d", admin, paid + 14 * DAY).status == "submitted"


def test_a_pack_with_used_hours_is_refused(session, ada, admin, providers):
    """The pack is spent after the plan's hours, so use the plan's hours first, then one of the
    pack's: a refund of a pack with used hours is refused."""
    buy_plan(session, ada, providers.usd)
    pack = buy_pack(session, ada, providers.usd, T0 + DAY)
    spend(session, ada, P20 + HOUR, T0 + 2 * DAY)  # all 20 plan hours and 1 hour of the pack

    assert refused(session, pack, "pack_14d", admin, T0 + 3 * DAY) == "hours_used"
    assert providers.usd.refund_calls == []
    assert status(session, ada, T0 + 3 * DAY).seconds_left == 9 * HOUR  # nothing was removed


def test_a_pack_after_the_account_is_gone_is_refused(session, ada, admin, providers):
    buy_plan(session, ada, providers.usd)
    pack = buy_pack(session, ada, providers.usd, T0 + DAY)
    pack.payer_user_id = None  # account deleted: money records outlive the person
    session.commit()
    assert refused(session, pack, "pack_14d", admin, T0 + 2 * DAY) == "payer_gone"


# ---- a double charge -------------------------------------------------------------------------


def second_plan_payment(session, ada, providers, now):
    """A second checkout paid by a person who already has a live plan: it grants nothing."""
    second = new_payment(
        session, payer=ada, provider="flutterwave", kind="plan", currency="USD", amount_minor=1800
    )
    second.plan_price_id = session.scalar(
        select(PlanPrice.id).where(PlanPrice.plan_code == "p20", PlanPrice.currency == "USD")
    )
    session.commit()
    pay(session, providers.usd, second, now)
    session.expire_all()
    return session.get(Payment, second.id)


def test_a_double_charge_is_refunded_in_full_and_changes_nothing_else(
    session, ada, admin, providers
):
    buy_plan(session, ada, providers.usd)
    duplicate = second_plan_payment(session, ada, providers, T0 + timedelta(hours=2))
    assert duplicate.needs_refund_reason == "the person already has a live plan"
    assert grants(session, ada, "plan_period")[0].seconds == P20 and len(grants(session, ada)) == 1

    # refundable even long after any window: a double charge has none
    refund = ask(session, duplicate, "double_charge", admin, T0 + 40 * DAY)

    assert refund.status == "submitted" and refund.amount_minor == 1800
    assert refund.grant_id is None and refund.seconds_removed == 0
    session.expire_all()
    assert session.get(Payment, duplicate.id).status == "refunded"
    assert live(session, ada).status == "active"  # the real plan is untouched
    assert len(grants(session, ada)) == 1 and grants(session, ada)[0].expires_at > T0 + 20 * DAY
    assert len(providers.usd.refund_calls) == 1


def test_a_payment_that_is_not_paid_or_already_refunded_is_refused(session, ada, admin, providers):
    choose_country(session, ada, "US")
    started = subs.start_plan_checkout(session, ada, "p20", settings(), T0)
    assert refused(session, started.payment, "first_plan_7d", admin, T0) == "payment_not_paid"
    buy_plan(session, ada, providers.usd)
    payment = payment_of(session)
    ask(session, payment, "first_plan_7d", admin, T0 + DAY)
    assert refused(session, payment, "first_plan_7d", admin, T0 + 2 * DAY) == "already_refunded"
    assert len(providers.usd.refund_calls) == 1


def test_an_unknown_reason_and_an_unknown_payment_are_refused_before_anything_is_written(
    session, ada, admin, providers
):
    buy_plan(session, ada, providers.usd)
    payment = payment_of(session)
    with pytest.raises(refunds.Refused) as bad:
        ask(session, payment, "because_i_say_so", admin, T0)
    assert bad.value.status == 422
    with pytest.raises(refunds.Refused) as gone:
        refunds.request(session, 987654, "double_charge", admin, None, settings(), T0)
    assert (gone.value.status, gone.value.code) == (404, "not_found")
    session.rollback()
    assert session.scalar(select(func.count()).select_from(Refund)) == 0
    assert providers.usd.refund_calls == []


# ---- what the provider answers ---------------------------------------------------------------


def test_a_provider_that_refuses_closes_the_refund_and_gives_the_hours_back(
    session, ada, admin, providers
):
    buy_plan(session, ada, providers.usd)
    payment = payment_of(session)
    providers.usd.refund_error = ProviderError("refused")

    refund = ask(session, payment, "first_plan_7d", admin, T0 + DAY)

    assert refund.status == "failed" and refund.error == "provider_refused"
    session.expire_all()
    assert session.get(Payment, payment.id).status == "succeeded"  # nothing moved
    assert live(session, ada).status == "active"  # the plan stands
    assert status(session, ada, T0 + DAY).seconds_left == P20  # the hours are back
    assert grants(session, ada, "plan_period")[0].expires_at > T0 + 20 * DAY

    # and it may be asked for again: a refused refund is closed
    providers.usd.refund_error = None
    again = ask(session, payment, "first_plan_7d", admin, T0 + 2 * DAY)
    assert again.status == "submitted" and again.id != refund.id
    assert again.reference != refund.reference
    assert len(providers.usd.refund_calls) == 2


def test_a_provider_that_cannot_be_reached_at_all_closes_it_like_a_refusal(
    session, ada, admin, providers, monkeypatch
):
    buy_plan(session, ada, providers.usd)
    payment = payment_of(session)
    monkeypatch.setattr(refunds, "provider_named", lambda name, cfg: None)
    refund = ask(session, payment, "first_plan_7d", admin, T0 + DAY)
    assert refund.status == "failed" and refund.error == "provider_unavailable"
    assert status(session, ada, T0 + DAY).seconds_left == P20


def test_no_answer_is_unknown_never_retried_and_the_hours_stay_off(session, ada, admin, providers):
    buy_plan(session, ada, providers.usd)
    payment = payment_of(session)
    providers.usd.refund_error = OutcomeUnknown("timed out")
    providers.usd.refund_acts_on_error = True  # the worst case: it WAS refunded, the answer lost

    refund = ask(session, payment, "first_plan_7d", admin, T0 + DAY)

    assert refund.status == "unknown" and refund.error == "no_answer"
    session.expire_all()
    assert session.get(Payment, payment.id).status == "succeeded"  # we do not know yet
    assert status(session, ada, T0 + DAY).seconds_left == 0  # the hours stay off meanwhile
    alerts = session.scalars(
        select(Notification).where(Notification.kind == "admin.billing_refund_unknown")
    ).all()
    assert len(alerts) == 1 and refund.reference in alerts[0].body

    # nothing asks again: not the same refund, not a new one
    providers.usd.refund_error = None
    assert refused(session, payment, "first_plan_7d", admin, T0 + 2 * DAY) == "refund_in_progress"
    assert len(providers.usd.refund_calls) == 1 and len(providers.usd.refunds) == 1


def test_a_person_who_looked_and_found_it_makes_the_payment_refunded(
    session, ada, admin, providers
):
    buy_plan(session, ada, providers.usd)
    payment = payment_of(session)
    providers.usd.refund_error = OutcomeUnknown("timed out")
    refund = ask(session, payment, "first_plan_7d", admin, T0 + DAY)

    done = refunds.resolve(session, refund.id, "made", admin, T0 + 2 * DAY)

    assert done.status == "submitted" and done.resolved_by == admin.id
    session.expire_all()
    assert session.get(Payment, payment.id).status == "refunded"
    assert live(session, ada) is None
    assert len(providers.usd.refund_calls) == 1  # resolving never calls the provider


def test_a_person_who_looked_and_found_none_closes_it_and_restores_the_hours(
    session, ada, admin, providers
):
    buy_plan(session, ada, providers.usd)
    payment = payment_of(session)
    providers.usd.refund_error = OutcomeUnknown("timed out")
    refund = ask(session, payment, "first_plan_7d", admin, T0 + DAY)

    done = refunds.resolve(session, refund.id, "not_made", admin, T0 + 2 * DAY)

    assert done.status == "failed" and done.error == "confirmed_not_made"
    assert status(session, ada, T0 + 2 * DAY).seconds_left == P20
    # only now can a new refund start, and it is a NEW one with its own reference
    providers.usd.refund_error = None
    again = ask(session, payment, "first_plan_7d", admin, T0 + 3 * DAY)
    assert again.status == "submitted" and again.reference != refund.reference


def test_resolve_only_works_on_an_unresolved_refund(session, ada, admin, providers):
    buy_plan(session, ada, providers.usd)
    payment = payment_of(session)
    done = ask(session, payment, "first_plan_7d", admin, T0 + DAY)
    with pytest.raises(refunds.Refused) as exc:
        refunds.resolve(session, done.id, "not_made", admin, T0 + 2 * DAY)  # it exists: no
    assert (exc.value.status, exc.value.code) == (409, "not_unresolved")
    session.rollback()
    with pytest.raises(refunds.Refused) as gone:
        refunds.resolve(session, 4242, "made", admin, T0)
    assert gone.value.status == 404
    session.rollback()
    with pytest.raises(refunds.Refused) as bad:
        refunds.resolve(session, done.id, "maybe", admin, T0)
    assert bad.value.status == 422


def test_a_refund_a_dead_process_left_pending_is_resolved_like_an_unknown_one(
    session, ada, admin, providers
):
    buy_plan(session, ada, providers.usd)
    payment = payment_of(session)
    # the process died after the row was written and before the provider answered
    orphan = refunds._start(session, payment.id, "first_plan_7d", admin, None, T0 + DAY)
    assert orphan.status == "pending"
    with pytest.raises(refunds.Refused) as young:  # too young: the other process may be working
        refunds.resolve(session, orphan.id, "not_made", admin, T0 + DAY + timedelta(minutes=2))
    assert young.value.code == "not_unresolved"
    session.rollback()
    assert refused(session, payment, "first_plan_7d", admin, T0 + DAY) == "refund_in_progress"

    refunds.alert_stuck(session, T0 + DAY + timedelta(minutes=11))
    refunds.alert_stuck(session, T0 + DAY + timedelta(minutes=12))  # once, not every pass
    session.expire_all()
    assert (
        session.scalar(
            select(func.count())
            .select_from(Notification)
            .where(Notification.kind == "admin.billing_refund_stuck")
        )
        == 1
    )
    done = refunds.resolve(session, orphan.id, "not_made", admin, T0 + DAY + timedelta(minutes=11))
    assert done.status == "failed"
    assert status(session, ada, T0 + 2 * DAY).seconds_left == P20


def test_a_provider_that_raises_something_unexpected_is_treated_as_no_answer(
    session, ada, admin, providers
):
    buy_plan(session, ada, providers.usd)
    payment = payment_of(session)
    providers.usd.refund_error = RuntimeError("segfault in the adapter")
    refund = ask(session, payment, "first_plan_7d", admin, T0 + DAY)
    assert refund.status == "unknown"  # it may have gone out: never assumed refused
    assert status(session, ada, T0 + DAY).seconds_left == 0


def test_the_fake_provider_refuses_a_refund_without_a_reference(providers):
    with pytest.raises(ProviderError):
        providers.usd.refund("tx-1", 100, reference="")
    with pytest.raises(TypeError):
        providers.usd.refund("tx-1", 100)  # the keyword is required: there is no default


# ---- the races ----------------------------------------------------------------------------


def test_two_admins_pressing_refund_at_once_make_one_refund_and_one_provider_call(
    session, engine, ada, admin, providers, monkeypatch
):
    buy_plan(session, ada, providers.usd)
    payment = payment_of(session)
    boss = make_user(session, "boss@example.test", admin=True)
    inside = threading.Event()
    original = providers.usd.refund

    def slow_refund(tx_id, amount, *, reference):
        inside.set()
        time.sleep(0.6)  # the provider is slow: the second admin presses meanwhile
        return original(tx_id, amount, reference=reference)

    monkeypatch.setattr(providers.usd, "refund", slow_refund)
    results = {}

    def first():
        with Session(engine) as db:
            who = db.get(type(admin), admin.id)
            results["first"] = refunds.request(
                db, payment.id, "first_plan_7d", who, None, settings(), T0 + DAY
            ).status

    def second():
        inside.wait(5)
        with Session(engine) as db:
            who = db.get(type(boss), boss.id)
            try:
                refunds.request(db, payment.id, "first_plan_7d", who, None, settings(), T0 + DAY)
                results["second"] = "accepted"
            except refunds.Refused as exc:
                results["second"] = exc.code

    threads = [threading.Thread(target=first), threading.Thread(target=second)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(20)

    assert results == {"first": "submitted", "second": "refund_in_progress"}
    assert len(providers.usd.refund_calls) == 1  # the provider was asked ONCE
    session.expire_all()
    assert session.scalar(select(func.count()).select_from(Refund)) == 1


def test_a_recording_counted_while_the_refund_waits_for_the_hours_lock_refuses_it(
    session, engine, ada, admin, providers
):
    """The check and the removal run under the person's hours lock, the one `hours.allocate`
    takes: a recording counted on another connection at that moment is seen, not raced."""
    buy_plan(session, ada, providers.usd)
    payment = payment_of(session)
    grant = grants(session, ada, "plan_period")[0]
    holds, outcome = threading.Event(), {}

    def counting():
        with Session(engine) as db:
            who = db.get(type(ada), ada.id)
            hours.lock_person(db, who)  # a recording is being counted: the lock is held
            holds.set()
            time.sleep(0.6)
            entry = UsageEntry(user_id=who.id, email_hash=email_hash(who.email), seconds=HOUR)
            db.add(entry)
            db.flush()
            db.add(UsageAllocation(usage_entry_id=entry.id, grant_id=grant.id, seconds=HOUR))
            db.commit()

    def refunding():
        holds.wait(5)
        with Session(engine) as db:
            who = db.get(type(admin), admin.id)
            started = time.monotonic()
            try:
                refunds.request(db, payment.id, "first_plan_7d", who, None, settings(), T0 + DAY)
                outcome["result"] = "refunded"
            except refunds.Refused as exc:
                outcome["result"] = exc.code
            outcome["waited"] = time.monotonic() - started

    threads = [threading.Thread(target=counting), threading.Thread(target=refunding)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(20)

    assert outcome["result"] == "hours_used"  # it saw the recording that was counted meanwhile
    assert outcome["waited"] >= 0.4  # and it really waited for the other connection's lock
    assert providers.usd.refund_calls == []
    assert status(session, ada, T0 + DAY).seconds_left == P20 - HOUR


def test_while_a_refund_is_pending_the_hours_it_covers_cannot_be_spent(
    session, ada, admin, providers
):
    buy_plan(session, ada, providers.usd)
    payment = payment_of(session)
    refunds._start(session, payment.id, "first_plan_7d", admin, None, T0 + DAY)  # not yet answered
    held = entitlements.held_after_backlog(session, ada, T0 + DAY + timedelta(seconds=1))
    assert [g.kind for g, _ in held] == []  # the grant is not in force: no recording can use it


# ---- the idempotence of the whole thing ------------------------------------------------------


def test_there_is_never_more_than_one_live_refund_per_payment_in_the_database(
    session, ada, admin, providers
):
    from sqlalchemy.exc import IntegrityError

    buy_plan(session, ada, providers.usd)
    payment = payment_of(session)
    ask(session, payment, "first_plan_7d", admin, T0 + DAY)
    session.add(
        Refund(
            payment_id=payment.id,
            reference="bnrf-dup-0001",
            reason="double_charge",
            status="pending",
            amount_minor=1800,
            currency="USD",
        )
    )
    with pytest.raises(IntegrityError):
        session.flush()
    session.rollback()


# ---- a provider's answer that arrives after a person closed the refund ------------------------


def test_a_late_success_after_the_refund_was_closed_as_not_made_changes_nothing_and_alerts(
    session, engine, ada, admin, providers, monkeypatch
):
    """The request's call is slow; meanwhile (10 minutes on) a person looks at the dashboard, finds
    nothing, and closes the refund as `not_made` (the hours come back). Then the provider's answer
    lands: it must NOT overwrite the closed row (no 500, no payment marked refunded while the hours
    are granted): the admins are told instead."""
    buy_plan(session, ada, providers.usd)
    payment = payment_of(session)
    original = providers.usd.refund
    seen = {}

    def slow(tx_id, amount, *, reference):
        with Session(engine) as other:  # the person, on another connection, while the call hangs
            row = other.scalars(select(Refund).where(Refund.reference == reference)).one()
            late = row.created_at + timedelta(minutes=11)
            seen["closed"] = refunds.resolve(other, row.id, "not_made", admin, late).status
        return original(tx_id, amount, reference=reference)  # the money DID move

    monkeypatch.setattr(providers.usd, "refund", slow)
    result = ask(session, payment, "first_plan_7d", admin, T0 + DAY)

    assert seen["closed"] == "failed"
    assert result.status == "failed"  # untouched by the late answer
    session.expire_all()
    assert session.get(Payment, payment.id).status == "succeeded"  # not marked refunded
    assert status(session, ada, T0 + DAY).seconds_left == P20  # the hours are still the person's
    assert live(session, ada).status == "active"
    [alert] = session.scalars(
        select(Notification).where(Notification.kind == "admin.billing_refund_late")
    ).all()
    assert result.reference in alert.body and "Nothing was changed" in alert.body


def test_a_late_answer_does_not_crash_when_a_new_refund_is_already_live(
    session, engine, ada, admin, providers, monkeypatch
):
    buy_plan(session, ada, providers.usd)
    payment = payment_of(session)
    original = providers.usd.refund
    calls = []

    def slow(tx_id, amount, *, reference):
        calls.append(reference)
        if len(calls) == 1:
            with Session(engine) as other:
                row = other.scalars(select(Refund).where(Refund.reference == reference)).one()
                late = row.created_at + timedelta(minutes=11)
                refunds.resolve(other, row.id, "not_made", admin, late)
                # a NEW refund starts and stays live (its provider call answers unknown)
                providers.usd.refund_error = OutcomeUnknown("slow too")
                again = refunds.request(
                    other, payment.id, "first_plan_7d", admin, None, settings(), late
                )
                providers.usd.refund_error = None
                seen = again.status
                assert seen == "unknown"
        return original(tx_id, amount, reference=reference)

    monkeypatch.setattr(providers.usd, "refund", slow)
    first = ask(session, payment, "first_plan_7d", admin, T0 + DAY)  # no 500 from the live index

    assert first.status == "failed"
    session.expire_all()
    live_rows = session.scalars(select(Refund).where(Refund.status == "unknown")).all()
    assert len(live_rows) == 1  # the second refund is untouched
    assert session.get(Payment, payment.id).status == "succeeded"
    assert (
        len(
            session.scalars(
                select(Notification).where(Notification.kind == "admin.billing_refund_late")
            ).all()
        )
        == 1
    )


def test_a_late_success_after_the_person_said_made_is_no_alert(
    session, engine, ada, admin, providers, monkeypatch
):
    buy_plan(session, ada, providers.usd)
    payment = payment_of(session)
    original = providers.usd.refund

    def slow(tx_id, amount, *, reference):
        with Session(engine) as other:
            row = other.scalars(select(Refund).where(Refund.reference == reference)).one()
            late = row.created_at + timedelta(minutes=11)
            refunds.resolve(other, row.id, "made", admin, late)
        return original(tx_id, amount, reference=reference)

    monkeypatch.setattr(providers.usd, "refund", slow)
    result = ask(session, payment, "first_plan_7d", admin, T0 + DAY)
    assert result.status == "submitted"
    session.expire_all()
    assert session.get(Payment, payment.id).status == "refunded"
    assert not session.scalars(
        select(Notification).where(Notification.kind == "admin.billing_refund_late")
    ).all()


# ---- while a first-plan refund is not settled, the plan is not touched -------------------------


def test_a_plan_with_an_unsettled_refund_cannot_be_changed_or_upgraded(
    session, ada, admin, providers
):
    sub = buy_plan(session, ada, providers.usd)
    sub.current_period_end = sub.current_period_start + timedelta(days=30)
    session.commit()
    payment = payment_of(session)
    providers.usd.refund_error = OutcomeUnknown("no answer")
    ask(session, payment, "first_plan_7d", admin, T0 + DAY)

    for call in (
        lambda: subs.change_plan(session, ada, "p40", settings(), T0 + 2 * DAY),
        lambda: subs.preview_plan_change(session, ada, "p40", T0 + 2 * DAY),
    ):
        with pytest.raises(subs.Refused) as refused_:
            call()
        session.rollback()
        assert (refused_.value.status, refused_.value.code) == (409, "refund_pending")


def test_the_clock_does_not_charge_a_plan_with_an_unsettled_refund_and_catches_up_after(
    session, ada, admin, providers
):
    from app.billing import renewals

    sub = buy_plan(session, ada, providers.usd)
    payment = payment_of(session)
    providers.usd.refund_error = OutcomeUnknown("no answer")
    refund = ask(session, payment, "first_plan_7d", admin, T0 + DAY)
    session.execute(
        update(Subscription)
        .where(Subscription.id == sub.id)
        .values(current_period_end=T0 + timedelta(hours=1))
    )
    session.commit()
    due = T0 + 2 * DAY

    assert renewals.run_due(session, due, settings()) == 0  # not even looked at
    assert providers.usd.charges == []
    assert (
        session.scalar(select(func.count()).select_from(Payment).where(Payment.kind == "renewal"))
        == 0
    )

    # the person looked: no refund exists. The plan is the person's again and the clock charges once
    providers.usd.refund_error = None
    refunds.resolve(session, refund.id, "not_made", admin, due)
    assert renewals.run_due(session, due + timedelta(minutes=1), settings()) == 1
    assert len(providers.usd.charges) == 1


def test_an_upgrade_paid_while_the_refund_is_unsettled_is_a_payment_owed_back_not_a_plan_change(
    session, ada, admin, providers
):
    sub = buy_plan(session, ada, providers.usd)
    sub.current_period_end = sub.current_period_start + timedelta(days=30)
    session.commit()
    started = subs.change_plan(session, ada, "p40", settings(), T0 + DAY).started  # checkout open
    payment = payment_of(session)
    providers.usd.refund_error = OutcomeUnknown("no answer")
    ask(session, payment, "first_plan_7d", admin, T0 + DAY)

    pay(session, providers.usd, started.payment, T0 + DAY + timedelta(hours=1))

    session.expire_all()
    upgrade = session.get(Payment, started.payment.id)
    assert upgrade.status == "succeeded" and upgrade.needs_refund_reason is not None
    assert session.scalars(select(Subscription)).one().plan_code == "p20"  # not changed
    # and it can be refunded in full as the double charge it is
    providers.usd.refund_error = None
    assert ask(session, upgrade, "double_charge", admin, T0 + 2 * DAY).status == "submitted"


def test_a_double_charge_refund_never_ends_the_plan_even_for_a_plan_payment(
    session, ada, admin, providers
):
    buy_plan(session, ada, providers.usd)
    duplicate = second_plan_payment(session, ada, providers, T0 + timedelta(hours=2))
    duplicate.subscription_id = live(session, ada).id  # even if it ever pointed at the real plan
    session.commit()
    ask(session, duplicate, "double_charge", admin, T0 + DAY)
    assert live(session, ada).status == "active"  # keyed on the reason, not on the payment's kind
    assert session.scalar(select(func.count()).select_from(PaymentMethod)) == 1  # the card stays


def test_a_double_charged_payment_with_an_earlier_date_does_not_hide_the_real_first_payment(
    session, ada, admin, providers
):
    buy_plan(session, ada, providers.usd)
    first = payment_of(session)
    duplicate = second_plan_payment(session, ada, providers, T0 + timedelta(hours=2))
    duplicate.paid_at = first.paid_at - timedelta(days=1)  # sorts before the real one
    session.commit()
    assert ask(session, first, "first_plan_7d", admin, T0 + DAY).status == "submitted"
