"""Shared helpers for the subscription tests (billing stage S8a): a catalog, two fake providers
named after the real ones the route table picks, a way to "pay" a payment through the REAL webhook
inbox and settlement, and a way to read what a person holds. Not a test module."""

import base64
import re
from datetime import UTC, datetime

from sqlalchemy import select

from app import entitlements
from app.billing import inbox, settle
from app.billing import profile as profiles
from app.billing import subscriptions as subs
from app.models import HourGrant, Notification, Plan, PlanPrice, Product, Subscription
from tests.test_web import make_settings

KEY = base64.b64encode(b"K" * 32).decode()
T0 = datetime(2026, 12, 1, 9, 0, tzinfo=UTC)  # the day a plan is bought
LONG_AGO = datetime(2026, 1, 1, tzinfo=UTC)
HOUR = 3600
P20, P40 = 20 * HOUR, 40 * HOUR


def settings(**over):
    return make_settings(**{"billing_enabled": True, "billing_allowed_emails": "*", **over})


def make_catalog(session, *, ngn=True):
    """Two plans (20 and 40 hours), a 10-hour pack, USD prices and (unless `ngn` is False) naira."""
    session.add_all(
        [
            Plan(code="p20", name="Twenty", monthly_seconds=P20, sort_order=1),
            Plan(code="p40", name="Forty", monthly_seconds=P40, sort_order=2),
        ]
    )
    session.flush()
    prices = [("p20", "USD", 1800), ("p40", "USD", 3400)]
    packs = [("USD", 1000)]
    if ngn:
        prices += [("p20", "NGN", 2_500_000), ("p40", "NGN", 4_800_000)]
        packs += [("NGN", 1_500_000)]
    for code, currency, amount in prices:
        session.add(
            PlanPrice(plan_code=code, currency=currency, amount_minor=amount, active_from=LONG_AGO)
        )
    for currency, amount in packs:
        session.add(
            Product(
                code="pack10",
                name="Ten hours",
                currency=currency,
                seconds=10 * HOUR,
                amount_minor=amount,
                valid_months=12,
            )
        )
    session.commit()


def choose_country(session, user, country, now=LONG_AGO):
    profiles.change_country(session, user, country, now=now)
    session.commit()


def pay(session, provider, payment, now, *, card_token=None, **wrong):
    """The payer paid `payment` on the hosted page and the provider sent its webhook: the event
    goes through the real inbox and the real settlement rules at `now`. `wrong` makes the provider's
    own report differ from what we asked for (`amount_minor`, `currency`, `reference`).
    Returns the transaction the provider reports."""
    tx = provider.complete(
        payment, card_token=card_token, payer_email="payer@example.test", **wrong
    )
    # the webhook names OUR reference (as a real one does) whatever the provider's report says
    claim = provider.event_for(tx)
    body = provider.event_body(
        type(claim)(
            event_id=claim.event_id,
            kind=claim.kind,
            reference=payment.reference,
            provider_tx_id=claim.provider_tx_id,
            amount_minor=claim.amount_minor,
            currency=claim.currency,
        )
    )
    deliver(session, provider, body, now)
    return tx


def deliver(session, provider, body: bytes, now):
    row, event = inbox.receive(session, provider, provider.signed_headers(body), body, now)
    if row.processed_at is None:
        settle.process(session, provider, row, event, now)
    return row


def buy_plan(session, user, provider, now=T0, *, plan="p20", country="US", token="tok-1", cfg=None):
    """A first plan bought end to end: country chosen, checkout, the payer pays, the webhook."""
    choose_country(session, user, country)
    started = subs.start_plan_checkout(session, user, plan, cfg or settings(), now)
    pay(session, provider, started.payment, now, card_token=token)
    session.expire_all()
    return live(session, user)


def live(session, user) -> Subscription | None:
    session.expire_all()
    return subs.live_row(session, user.id)


def grants(session, user, kind=None) -> list[HourGrant]:
    session.expire_all()
    query = select(HourGrant).where(HourGrant.user_id == user.id).order_by(HourGrant.id)
    if kind:
        query = query.where(HourGrant.kind == kind)
    return list(session.scalars(query))


def status(session, user, now):
    session.expire_all()
    return entitlements.status(session, user, now)


def notices(session, user, kind) -> list[Notification]:
    session.expire_all()
    return list(
        session.scalars(
            select(Notification).where(Notification.user_id == user.id, Notification.kind == kind)
        )
    )


# Anything that tells a person a payment was made in test mode. The owner's rule (2026-11-20): the
# app carries no such wording anywhere a person or an API client can see it, so the experience is
# the same as in production. Every surface test runs its text through `assert_no_test_wording`.
TEST_WORDING = re.compile(
    r"test[ _-]?mode|test invoice|test keys?|sandbox|no real money|not a tax document|BN-T-",
    re.IGNORECASE,
)


def assert_no_test_wording(*texts: str) -> None:
    for text in texts:
        found = TEST_WORDING.search(text)
        assert found is None, f"test-mode wording {found.group(0)!r} shown in: {text[:300]!r}"
