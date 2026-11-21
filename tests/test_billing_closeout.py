"""`scripts/billing_close_test_data.py`: the one-time closeout of test-era billing data, run just
before billing goes live. Real Postgres, the real purchase flow with the fake providers, a fixed
clock (every time below is passed in, nothing reads the wall clock except where it says so).

Timeline: the TEST era is 2026-11-01; the cutover `T` is 2026-11-10 12:00 UTC; the script runs at
`NOW` = T + 30 minutes; the LIVE purchases are made between T and NOW. (Rows made by the real flow
take `created_at` from the server's clock for subscriptions and cards, so `stamp` sets those two to
the time the row is meant to have.)"""

import importlib.util
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from sqlalchemy import func, select, text
from sqlalchemy.orm import sessionmaker

from app.billing import renewals
from app.billing import subscriptions as subs
from app.billing.payments import new_payment
from app.models import (
    BillingEvent,
    HourGrant,
    Invoice,
    Payment,
    PaymentMethod,
    Refund,
    Subscription,
)
from tests.billing_support import (
    HOUR,
    buy_plan,
    choose_country,
    grants,
    make_catalog,
    pay,
    settings,
)
from tests.test_web import make_user

_spec = importlib.util.spec_from_file_location(
    "billing_close_test_data",
    Path(__file__).parent.parent / "scripts" / "billing_close_test_data.py",
)
script = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(script)

ERA = datetime(2026, 11, 1, 9, 0, tzinfo=UTC)  # test keys were in use
T = datetime(2026, 11, 10, 12, 0, tzinfo=UTC)  # the cutover
NOW = T + timedelta(minutes=30)  # when the closeout runs
LIVE_AT = T + timedelta(minutes=10)  # a payment made after the cutover (live keys)
T_TEXT = "2026-11-10T12:00:00Z"
SECRET = "SECRET-TOKEN-do-not-print"


@pytest.fixture(autouse=True)
def catalog(session, vault_key):
    make_catalog(session)


@pytest.fixture
def factory(engine):
    return sessionmaker(bind=engine, expire_on_commit=False)


def stamp(session, user, when):
    """The real flow gives a subscription and a card the server's clock: say when they were made."""
    session.execute(
        text("update subscriptions set created_at = :t where user_id = :u"),
        {"t": when, "u": user.id},
    )
    session.execute(
        text("update payment_methods set created_at = :t where user_id = :u"),
        {"t": when, "u": user.id},
    )
    session.commit()
    session.expire_all()


def run(factory, *, before=T_TEXT, apply=False, now=NOW, cfg=None, **kw):
    lines: list[str] = []
    code = script.run(
        factory, cfg or settings(), before, apply=apply, now=now, out=lines.append, **kw
    )
    return code, "\n".join(lines)


def snapshot(session):
    """Everything the closeout may touch, as plain values, to compare before and after."""
    session.expire_all()
    return {
        "subs": sorted(
            (s.id, s.status, s.next_charge_at, s.grace_ends_at, s.pending_plan_code, s.canceled_at)
            for s in session.scalars(select(Subscription))
        ),
        "grants": sorted((g.id, g.kind, g.expires_at) for g in session.scalars(select(HourGrant))),
        "cards": sorted(c.id for c in session.scalars(select(PaymentMethod))),
        "payments": sorted((p.id, p.status) for p in session.scalars(select(Payment))),
    }


class World:
    """People in every state the closeout must treat differently."""

    def __init__(self, session, providers, with_live=False):
        self.session, self.providers = session, providers
        # ada: a test plan (Flutterwave) with a card, plus a test pack bought on it
        self.ada = make_user(session, "ada@example.test")
        buy_plan(session, self.ada, providers.usd, ERA, token=SECRET)
        pack = subs.start_pack_checkout(session, self.ada, "pack10", settings(), ERA)
        pay(session, providers.usd, pack.payment, ERA, card_token=None)
        stamp(session, self.ada, ERA)
        # ben: a test plan (Paystack) that is past due, with a scheduled downgrade and a retry
        # clock, and the three grants that are NOT bought: trial, pilot, admin gift
        self.ben = make_user(session, "ben@example.test")
        buy_plan(session, self.ben, providers.ngn, ERA, country="NG", plan="p40", token="tok-ben")
        stamp(session, self.ben, ERA)
        sub = session.scalars(select(Subscription).where(Subscription.user_id == self.ben.id)).one()
        sub.status = "past_due"
        sub.grace_ends_at = ERA + timedelta(days=33)
        sub.next_charge_at = ERA + timedelta(days=31)
        sub.retry_count = 1
        sub.pending_plan_code = "p20"
        for kind in ("trial", "pilot", "admin_gift"):
            session.add(
                HourGrant(
                    user_id=self.ben.id,
                    email_hash="h-ben",
                    kind=kind,
                    seconds=3 * HOUR,
                    starts_at=ERA,
                    expires_at=NOW + timedelta(days=20),
                )
            )
        session.commit()
        # cat: an abandoned test checkout, still pending, and nothing else
        self.cat = make_user(session, "cat@example.test")
        choose_country(session, self.cat, "US")
        self.abandoned = subs.start_plan_checkout(session, self.cat, "p20", settings(), ERA).payment
        if not with_live:
            return
        # dan: a LIVE plan bought after T, with a card, a pack and a checkout still pending
        self.dan = make_user(session, "dan@example.test")
        buy_plan(session, self.dan, providers.usd, LIVE_AT, token="tok-dan")
        stamp(session, self.dan, LIVE_AT)
        self.eve = make_user(session, "eve@example.test")
        choose_country(session, self.eve, "US")
        self.live_pending = subs.start_plan_checkout(
            session, self.eve, "p20", settings(), T + timedelta(minutes=5)
        ).payment
        session.commit()


@pytest.fixture
def world(session, providers):
    return World(session, providers)


@pytest.fixture
def world_live(session, providers):
    """The same, plus rows made AFTER the cutover (dan's live plan, eve's pending checkout). The
    script refuses to run while such rows exist, so these are for tests that call it directly."""
    return World(session, providers, with_live=True)


# ---- what it does ----


def test_a_dry_run_prints_counts_and_changes_nothing(session, factory, world):
    before = snapshot(session)

    code, out = run(factory)

    assert code == 0, out
    assert snapshot(session) == before
    assert "DRY RUN" in out and "PROOF: not run" in out
    assert "backup.sh" in out
    # ada and ben: 2 live subscriptions; 3 in-force bought grants (ada plan + pack, ben plan);
    # 2 cards; 1 abandoned checkout
    table = {kind: getattr(script.count(session, T, NOW), kind) for kind in script.KINDS}
    assert table == {"subscriptions": 2, "grants": 3, "cards": 2, "payments": 1}


def test_apply_closes_every_kind_of_test_era_row(session, factory, world):
    code, out = run(factory, apply=True)

    assert code == 0, out
    assert "PROOF: OK" in out
    session.expire_all()
    for user in (world.ada, world.ben):
        sub = session.scalars(select(Subscription).where(Subscription.user_id == user.id)).one()
        assert sub.status == "ended"
        assert sub.next_charge_at is None and sub.grace_ends_at is None
        assert sub.pending_plan_code is None
        assert sub.canceled_at == NOW
        assert subs.live_row(session, user.id) is None  # a new plan can be bought again
    # the bought hours ended now; the trial, pilot and gift hours are exactly as they were
    for grant in grants(session, world.ada) + [
        g for g in grants(session, world.ben) if g.kind == "plan_period"
    ]:
        assert grant.expires_at == NOW, grant.kind
    kept = [g for g in grants(session, world.ben) if g.kind != "plan_period"]
    assert {g.kind for g in kept} == {"trial", "pilot", "admin_gift"}
    assert all(g.expires_at == NOW + timedelta(days=20) for g in kept)
    # the two test cards are gone
    assert (
        session.scalars(
            select(PaymentMethod).where(PaymentMethod.user_id.in_([world.ada.id, world.ben.id]))
        ).all()
        == []
    )
    # the abandoned checkout is failed, with no provider asked
    assert session.get(Payment, world.abandoned.id).status == "failed"
    assert world.providers.usd.lookups == [] and world.providers.ngn.lookups == []


def test_nothing_made_after_t_is_touched(session, world_live):
    """Called directly (the whole run refuses while rows after T exist): the person with a live
    plan made after T and the one with a live pending checkout have nothing to close."""
    live_sub = session.scalars(
        select(Subscription).where(Subscription.user_id == world_live.dan.id)
    ).one()
    live_grant = grants(session, world_live.dan, "plan_period")[0]
    before = snapshot(session)

    for person in (world_live.dan, world_live.eve):
        assert script.count(session, T, NOW, person.id).total() == 0
        assert script.close_person(session, person.id, T, NOW).total() == 0
    session.commit()

    assert snapshot(session)["subs"] == before["subs"]
    assert subs.live_row(session, world_live.dan.id) is not None
    assert live_sub.status == "active" and grants(session, world_live.dan, "plan_period")[0] == (
        live_grant
    )
    assert session.get(Payment, world_live.live_pending.id).status == "pending"
    assert session.scalars(
        select(PaymentMethod).where(PaymentMethod.user_id == world_live.dan.id)
    ).one()


def test_money_records_are_never_deleted(session, factory, world):
    counts = lambda: tuple(  # noqa: E731
        session.scalar(select(func.count()).select_from(model))
        for model in (Payment, Invoice, Refund)
    )
    session.expire_all()
    before = counts()
    statuses = {p.id: p.status for p in session.scalars(select(Payment))}

    run(factory, apply=True)

    session.expire_all()
    assert counts() == before and before[1] == 3  # the three paid test payments have invoices
    for payment in session.scalars(select(Payment)):
        # a payment only ever moves from pending to failed
        assert payment.status == statuses[payment.id] or (
            statuses[payment.id] == "pending" and payment.status == "failed"
        )


def test_a_second_apply_changes_nothing(session, factory, world):
    run(factory, apply=True)
    once = snapshot(session)

    code, out = run(factory, apply=True)

    assert code == 0 and "PROOF: OK" in out
    assert snapshot(session) == once
    assert "People with rows to close: 0" in out


def test_rows_of_a_deleted_person_are_closed_too(session, factory, world):
    sub = session.scalars(select(Subscription).where(Subscription.user_id == world.ada.id)).one()
    session.execute(text("update subscriptions set user_id = null where id = :i"), {"i": sub.id})
    session.execute(
        text("update hour_grants set user_id = null where user_id = :u"), {"u": world.ada.id}
    )
    session.commit()

    code, out = run(factory, apply=True)

    assert code == 0 and "PROOF: OK" in out, out
    session.expire_all()
    assert session.get(Subscription, sub.id).status == "ended"


def test_a_closed_person_can_buy_a_live_plan(session, factory, world, providers):
    run(factory, apply=True)

    started = subs.start_plan_checkout(
        session, world.ada, "p20", settings(), NOW + timedelta(hours=1)
    )

    assert started.payment.status == "pending"  # no `already_subscribed`


# ---- what makes it stop ----


@pytest.mark.parametrize(
    "before",
    [None, "", "   ", "garbage", "2026-11-10", "2026-11-10T12:00:00", "2026-11-11T12:00:00Z"],
    ids=["missing", "empty", "blank", "garbage", "date only", "no zone", "future"],
)
def test_it_refuses_a_missing_or_bad_before(session, factory, world, before):
    snap = snapshot(session)

    code, out = run(factory, before=before, apply=True)

    assert code == 2 and out.startswith("REFUSED"), out
    assert snapshot(session) == snap
    assert "garbage" not in out.replace("REFUSED", "")  # the value is never echoed back


def test_it_refuses_while_billing_is_live(session, factory, world):
    snap = snapshot(session)

    code, out = run(factory, apply=True, cfg=settings(billing_live=True))

    assert code == 2 and "BILLING_LIVE is true" in out
    assert snapshot(session) == snap


def test_it_refuses_while_someone_is_paying(session, factory, world):
    close = T + timedelta(minutes=5)  # T is five minutes old
    session.execute(
        text("update payments set created_at = :t where id = :i"),
        {"t": T - timedelta(minutes=5), "i": world.abandoned.id},  # before T, 10 minutes ago
    )
    session.commit()
    snap = snapshot(session)

    code, out = run(factory, apply=True, now=close)

    assert code == 2 and "may be paying" in out and "30 minutes" in out
    assert snapshot(session) == snap
    # one made more than 30 minutes before the run does not stop it
    session.execute(
        text("update payments set created_at = :t where id = :i"),
        {"t": close - timedelta(minutes=31), "i": world.abandoned.id},
    )
    session.commit()
    assert run(factory, apply=True, now=close)[0] == 0


def test_one_persons_failure_rolls_back_that_person_and_the_rest_go_on(
    session, factory, world, monkeypatch
):
    real = script.close_person

    def broken_for_ada(sess, user_id, before, now):
        changed = real(sess, user_id, before, now)  # real writes, then the failure
        if user_id == world.ada.id:
            raise RuntimeError("boom")
        return changed

    monkeypatch.setattr(script, "close_person", broken_for_ada)

    code, out = run(factory, apply=True)

    assert code == 1, out
    assert "PROOF: FAILED" in out and "FAILED for one person (RuntimeError)" in out
    assert "boom" not in out
    session.expire_all()
    ada_sub = session.scalars(
        select(Subscription).where(Subscription.user_id == world.ada.id)
    ).one()
    ben_sub = session.scalars(
        select(Subscription).where(Subscription.user_id == world.ben.id)
    ).one()
    assert ada_sub.status == "active"  # rolled back whole: not half closed
    assert [g for g in grants(session, world.ada) if g.expires_at == NOW] == []
    assert session.scalars(select(PaymentMethod).where(PaymentMethod.user_id == world.ada.id)).one()
    assert ben_sub.status == "ended"  # the next person was still closed
    # and the run can be repeated once the cause is gone
    monkeypatch.setattr(script, "close_person", real)
    code, out = run(factory, apply=True)
    assert code == 0 and "PROOF: OK" in out


def test_the_output_has_no_name_email_token_reference_or_id(session, factory, world):
    world_text = []
    for apply in (False, True, True):
        world_text.append(run(factory, apply=apply)[1])
    out = "\n".join(world_text)
    forbidden = [SECRET, "tok-ben", "tok-dan", "example.test", "ada", "ben@", "bn-"]
    forbidden += [p.reference for p in session.scalars(select(Payment))]
    for needle in forbidden:
        assert needle not in out, needle


# ---- the after-state is inert ----


def test_a_closed_subscription_is_never_charged_or_asked_about(session, factory, world):
    far = ERA + timedelta(days=35)  # past ada's and ben's period ends, before dan's
    live = settings(billing_live=True, billing_live_since=T_TEXT)
    due_now = (
        select(func.count())
        .select_from(Subscription)
        .where(
            Subscription.status.in_(("active", "past_due")), Subscription.current_period_end <= far
        )
    )
    assert session.scalar(due_now) == 2  # control: before the closeout both would be charged

    run(factory, apply=True)

    assert session.scalar(due_now) == 0
    assert renewals.run_due(session, far, live) == 0
    session.expire_all()
    for user in (world.ada, world.ben):
        sub = session.scalars(select(Subscription).where(Subscription.user_id == user.id)).one()
        assert sub.status == "ended"
    providers = world.providers
    assert providers.usd.charges == [] and providers.ngn.charges == []
    assert providers.usd.lookups == [] and providers.ngn.lookups == []
    # and the person is told nothing: no "could not renew" mail or bell
    from app.models import Notification

    assert (
        session.scalars(
            select(Notification).where(
                Notification.kind.like("billing.%"), Notification.kind != "billing.receipt"
            )
        ).all()
        == []
    )


def test_the_sweeper_skips_test_era_payments_and_events(session, factory, world_live, monkeypatch):
    """Pending test checkouts, failed test checkouts with a page, and parked test webhook events
    are never asked about on the live API once the cutover is in force."""
    page = new_payment(
        session,
        payer=world_live.cat,
        provider="flutterwave",
        kind="plan",
        currency="USD",
        amount_minor=1800,
    )
    page.status, page.pay_url, page.created_at = (
        "failed",
        "https://fake.test/pay",
        NOW - timedelta(hours=2),
    )
    session.add(
        BillingEvent(
            provider="flutterwave",
            dedupe_key="evt-test-era",
            type="charge_succeeded",
            payload={"x": 1},
            received_at=NOW - timedelta(hours=1),
        )
    )
    # the abandoned checkout is a few hours old, so it is inside the sweep's look-back window
    session.execute(
        text("update payments set created_at = :t where id = :i"),
        {"t": T - timedelta(hours=5), "i": world_live.abandoned.id},
    )
    session.commit()
    parsed: list[bytes] = []

    def spy(raw):
        parsed.append(raw)
        raise ValueError("not a real event")

    monkeypatch.setattr(world_live.providers.usd, "parse_event", spy)
    far = NOW + timedelta(hours=3)  # the sweep looks back days, so every row above is in range

    renewals.sweep(session, far, settings(billing_live=True, billing_live_since=T_TEXT))

    asked = world_live.providers.usd.lookups + world_live.providers.ngn.lookups
    assert asked == [
        world_live.live_pending.reference
    ]  # eve's checkout, made after T; no test-era one
    assert parsed == []
    # control: without the cutover the same sweep asks about the test-era rows too
    world_live.providers.usd.lookups.clear()
    world_live.providers.ngn.lookups.clear()
    session.execute(text("update payments set last_checked_at = null"))
    session.commit()
    renewals.sweep(session, far, settings())
    assert {world_live.abandoned.reference, page.reference} <= set(
        world_live.providers.usd.lookups + world_live.providers.ngn.lookups
    )
    assert len(parsed) == 1


# ---- the window between T and the flip, a stale T, open refunds (Opus review of PR 255) ----


def test_it_refuses_when_billing_rows_were_made_at_or_after_t(session, factory, world_live):
    """Rows made after T while BILLING_LIVE is false are test rows the closeout would skip and the
    app would then read as live: the run refuses, and says how many, never whose."""
    snap = snapshot(session)

    code, out = run(factory, apply=True)

    assert code == 2 and out.startswith("REFUSED"), out
    assert "made at or after --before" in out and "Pause billing first" in out
    assert "2 payments" in out and "1 subscriptions" in out and "1 saved cards" in out
    assert "example.test" not in out and "tok-dan" not in out
    assert snapshot(session) == snap
    assert run(factory, apply=False)[0] == 2  # a dry run says so too


def test_a_checkout_finished_after_t_stops_the_run(session, factory, world):
    """A failed test checkout paid after the closeout would make a new test plan dated after T:
    the guard sees any row at or after T, so it cannot start the run then."""
    assert run(factory, apply=True)[0] == 0
    ben = world.ben
    buy_plan(session, ben, world.providers.ngn, T + timedelta(minutes=2), country="NG", token="x")

    code, out = run(factory, apply=True)

    assert code == 2 and "made at or after --before" in out


def test_a_stale_before_is_refused_unless_the_age_is_raised(session, factory, world):
    snap = snapshot(session)
    late = T + timedelta(minutes=61)

    code, out = run(factory, apply=True, now=late)

    assert code == 2 and "older than 60 minutes" in out and "--max-age-minutes" in out
    assert snapshot(session) == snap
    assert run(factory, apply=True, now=T + timedelta(minutes=60))[0] == 0  # the bound is 60
    assert run(factory, apply=True, now=T + timedelta(hours=5), max_age_minutes=600)[0] == 0


def make_refund(session, payment, status):
    from app.models import Refund

    session.add(
        Refund(
            payment_id=payment.id,
            reference=f"bnrf-{payment.id}-aaaaaaaaaaaaaaaa",
            reason="first_plan_7d",
            status=status,
            amount_minor=payment.amount_minor,
            currency=payment.currency,
            created_at=ERA + timedelta(days=1),
        )
    )
    session.commit()


@pytest.mark.parametrize("status", ["pending", "unknown"])
def test_it_refuses_while_a_refund_of_a_test_payment_is_open(session, factory, world, status):
    first = session.scalars(select(Payment).order_by(Payment.id)).first()
    make_refund(session, first, status)
    snap = snapshot(session)

    for apply in (False, True):
        code, out = run(factory, apply=apply)
        assert code == 2 and "refund(s) of test-era payments are still open" in out, out
        assert "resolve it as made or not made" in out and "1 refund" in out
    assert snapshot(session) == snap


def test_a_decided_refund_does_not_stop_the_run(session, factory, world):
    first = session.scalars(select(Payment).order_by(Payment.id)).first()
    make_refund(session, first, "submitted")

    assert run(factory, apply=True)[0] == 0


def test_a_grant_linked_only_through_the_subscription_is_closed(session, world):
    """An upgrade's grant can carry the subscription and a payment made after T (or none)."""
    sub = session.scalars(select(Subscription).where(Subscription.user_id == world.ada.id)).one()
    odd = HourGrant(
        user_id=world.ada.id,
        email_hash="h",
        kind="plan_period",
        seconds=HOUR,
        starts_at=ERA,
        expires_at=NOW + timedelta(days=5),
        subscription_id=sub.id,
    )
    session.add(odd)
    session.commit()

    changed = script.close_person(session, world.ada.id, T, NOW)
    session.commit()

    assert changed.grants == 3  # the plan's, the pack's and this one
    session.refresh(odd)
    assert odd.expires_at == NOW


def test_a_partly_used_grant_keeps_its_allocations(session, factory, world):
    from app import hours
    from app.models import UsageAllocation, UsageEntry
    from app.usage import email_hash

    entry = UsageEntry(user_id=world.ada.id, email_hash=email_hash(world.ada.email), seconds=HOUR)
    session.add(entry)
    session.flush()
    hours.allocate(session, world.ada, entry, ERA + timedelta(days=1))
    session.commit()
    allocated = session.scalars(select(UsageAllocation.grant_id)).all()
    assert allocated  # an hour was taken from a bought grant

    assert run(factory, apply=True)[0] == 0

    session.expire_all()
    for grant_id in allocated:
        grant = session.get(HourGrant, grant_id)
        assert grant is not None and grant.expires_at == NOW  # ended, not deleted
    assert session.scalars(select(UsageAllocation)).all()  # the ledger is intact


def test_one_person_with_test_and_live_history_keeps_the_live_side(session, factory, world):
    """Ada paid on test keys, the closeout ran, then she paid live: closing her again (direct call;
    the whole run refuses once rows exist after T) leaves everything live untouched."""
    assert run(factory, apply=True)[0] == 0
    live_at = NOW + timedelta(hours=1)
    buy_plan(session, world.ada, world.providers.usd, live_at, token="tok-live-ada")
    stamp_live = text(
        "update subscriptions set created_at = :t where user_id = :u and status = 'active'"
    )
    session.execute(stamp_live, {"t": live_at, "u": world.ada.id})
    session.execute(
        text("update payment_methods set created_at = :t where user_id = :u"),
        {"t": live_at, "u": world.ada.id},
    )
    session.commit()
    before = snapshot(session)

    assert script.count(session, T, live_at + timedelta(hours=1), world.ada.id).total() == 0
    assert script.close_person(session, world.ada.id, T, live_at + timedelta(hours=1)).total() == 0
    session.commit()

    assert snapshot(session) == before
    live = subs.live_row(session, world.ada.id)
    assert live is not None and live.created_at == live_at
    assert session.scalars(select(PaymentMethod).where(PaymentMethod.user_id == world.ada.id)).one()
    # her test plan and test hours stay ended
    assert [g.expires_at for g in grants(session, world.ada, "plan_period")][0] == NOW


def test_a_database_error_prints_its_type_and_nothing_it_carried(
    session, factory, world, monkeypatch
):
    from sqlalchemy.exc import OperationalError

    def broken(*a, **kw):
        raise OperationalError("SELECT * FROM users WHERE id = 4242", {"id": 4242}, Exception("x"))

    monkeypatch.setattr(script, "check_guards", broken)

    code, out = run(factory, apply=True)

    assert code == 1 and "OperationalError" in out
    assert "4242" not in out and "SELECT" not in out
    monkeypatch.undo()


def test_the_dry_run_says_how_many_test_payments_have_no_invoice(session, factory, world):
    session.execute(text("delete from invoices where id = (select min(id) from invoices)"))
    session.commit()

    code, out = run(factory)

    assert code == 0 and "1 succeeded test payment(s) have no invoice" in out
