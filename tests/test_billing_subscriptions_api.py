"""The subscription routes over HTTP (billing stage S8a, `app/billing/subscriptions_api.py`).

The app's own routes with billing on, real Postgres, a scripted provider. A person pays through the
real webhook inbox and settlement rules (`tests/billing_support.py`), because the routes grant
nothing themselves. The routes use the real clock, so the subscription tests move the stored dates
back instead of the clock."""

from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select, update

from app import auth
from app.billing import renewals
from app.models import HourGrant, Payment, Subscription
from tests.billing_support import HOUR, P20, make_catalog, pay, settings
from tests.test_billing_profile import API, Person, api_client, error, rows
from tests.test_web import make_user

CSRF = auth.CSRF_HEADER


class Buyer(Person):
    def post(self, url, body=None, csrf=True):
        headers = {CSRF: self.csrf} if csrf else {}
        return self.client.post(url, json=body if body is not None else {}, headers=headers)


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


def user_of(session, email="ada@example.test"):
    from app.models import User

    return session.scalars(select(User).where(User.email == email)).one()


def now() -> datetime:
    return datetime.now(UTC)


def buy(session, ada, providers, *, country="US", plan="p20", provider=None):
    """Ada buys `plan` through the routes and the webhook, as a person would."""
    provider = provider or providers.usd
    assert ada.put(f"{API}/profile/country", {"billing_country": country}).status_code == 200
    out = ada.post(f"{API}/checkout", {"kind": "plan", "code": plan})
    assert out.status_code == 200, out.text
    payment = session.scalars(
        select(Payment).where(Payment.reference == out.json()["payment"]["reference"])
    ).one()
    pay(session, provider, payment, now(), card_token="tok-api")
    session.expire_all()
    return out.json()


# ---- dark ---------------------------------------------------------------------------------------


def test_with_billing_off_every_route_is_a_404_and_nothing_is_written(engine, session, providers):
    make_user(session, "ada@example.test")
    with api_client(engine, enabled=False) as off:
        ada = Buyer(off, "ada@example.test")
        for method, url, body in [
            ("get", f"{API}/plans", None),
            ("get", f"{API}/subscription", None),
            ("get", f"{API}/payments/bn-1-x", None),
            ("post", f"{API}/checkout", {"kind": "plan", "code": "p20"}),
            ("post", f"{API}/subscription/cancel", None),
            ("post", f"{API}/subscription/resume", None),
            ("post", f"{API}/subscription/change-plan", {"plan_code": "p40"}),
            ("get", f"{API}/subscription/change-plan/preview?plan_code=p40", None),
            ("post", f"{API}/subscription/pay-link", None),
        ]:
            response = getattr(ada, method)(url, *([body] if method == "post" else []))
            error(response, 404, "not_found")
        error(ada.post(f"{API}/checkout", {"surprise": 1}), 404, "not_found")  # not a 422 either
    assert rows(session, Payment) == 0 and rows(session, Subscription) == 0


def test_an_account_not_on_the_allow_list_gets_the_same_404(engine, session, providers):
    make_user(session, "ada@example.test")
    with api_client(engine, billing_allowed_emails="someone-else@example.test") as closed:
        ada = Buyer(closed, "ada@example.test")
        error(ada.get(f"{API}/plans"), 404, "not_found")
        error(ada.post(f"{API}/checkout", {"kind": "plan", "code": "p20"}), 404, "not_found")
    assert rows(session, Payment) == 0


def test_a_visitor_and_a_missing_csrf_token_are_refused_first(web, ada):
    with TestClient(web.app, follow_redirects=False) as visitor:
        error(visitor.get(f"{API}/subscription"), 401, "not_authenticated")
    error(
        ada.post(f"{API}/checkout", {"kind": "plan", "code": "p20"}, csrf=False),
        403,
        "csrf_rejected",
    )
    error(ada.post(f"{API}/subscription/cancel", csrf=False), 403, "csrf_rejected")


# ---- plans --------------------------------------------------------------------------------------


def test_plans_have_no_prices_until_the_person_has_chosen_a_billing_country(ada):
    body = ada.get(f"{API}/plans").json()
    assert body == {"route": None, "plans": [], "packs": []}  # nothing is guessed


@pytest.mark.parametrize(
    ("country", "route", "p20", "pack"),
    [
        ("NG", {"currency": "NGN", "provider": "paystack"}, 2_500_000, 1_500_000),
        ("US", {"currency": "USD", "provider": "flutterwave"}, 1800, 1000),
    ],
)
def test_plans_are_priced_in_the_currency_the_country_pays_with(ada, country, route, p20, pack):
    ada.put(f"{API}/profile/country", {"billing_country": country})

    body = ada.get(f"{API}/plans").json()

    assert body["route"] == route
    assert [p["code"] for p in body["plans"]] == ["p20", "p40"]
    first = body["plans"][0]
    assert first["monthly_seconds"] == P20
    assert first["price"] == {"currency": route["currency"], "amount_minor": p20}
    assert body["packs"][0]["price"]["amount_minor"] == pack
    assert (body["packs"][0]["seconds"], body["packs"][0]["valid_months"]) == (10 * HOUR, 12)


def test_a_plan_without_a_price_in_the_currency_is_listed_with_a_null_price(
    engine, session, providers
):
    from app.models import PlanPrice

    session.query(PlanPrice).filter_by(plan_code="p40", currency="NGN").delete()
    session.commit()
    make_user(session, "ben@example.test")
    with api_client(engine) as client:
        ben = Buyer(client, "ben@example.test")
        ben.put(f"{API}/profile/country", {"billing_country": "NG"})
        plans = {p["code"]: p["price"] for p in ben.get(f"{API}/plans").json()["plans"]}
    assert plans["p40"] is None and plans["p20"]["amount_minor"] == 2_500_000


# ---- checkout and the page the payer returns to ------------------------------------------------


def test_a_checkout_makes_a_pending_payment_and_grants_nothing(session, ada, providers):
    ada.put(f"{API}/profile/country", {"billing_country": "US"})

    out = ada.post(f"{API}/checkout", {"kind": "plan", "code": "p20"})

    assert out.status_code == 200, out.text
    body = out.json()
    assert set(body) == {"payment", "checkout_url"}
    payment = body["payment"]
    assert (payment["kind"], payment["status"], payment["currency"], payment["amount_minor"]) == (
        "plan",
        "pending",
        "USD",
        1800,
    )
    assert payment["paid_at"] is None and body["checkout_url"] == payment["pay_url"]
    assert rows(session, HourGrant) == 0 and rows(session, Subscription) == 0
    # the page the payer returns to polls this and still sees `pending`: it grants nothing
    again = ada.get(f"{API}/payments/{payment['reference']}")
    assert again.status_code == 200 and again.json()["status"] == "pending"
    state = ada.get(f"{API}/subscription").json()
    assert state["state"] == "none" and state["subscription"] is None
    assert state["pending_payment"]["reference"] == payment["reference"]


def test_after_the_verified_payment_the_state_shows_the_plan_and_the_hours(session, ada, providers):
    body = buy(session, ada, providers)
    assert ada.get(f"{API}/payments/{body['payment']['reference']}").json()["status"] == "succeeded"

    state = ada.get(f"{API}/subscription").json()

    assert state["state"] == "active" and state["pending_payment"] is None
    assert state["hours"] == {"seconds_left": P20, "holdings_seconds": P20, "warning": None}
    sub = state["subscription"]
    assert (sub["status"], sub["plan_code"], sub["currency"], sub["amount_minor"]) == (
        "active",
        "p20",
        "USD",
        1800,
    )
    assert (sub["can_cancel"], sub["can_resume"], sub["can_change_plan"], sub["can_pay_now"]) == (
        True,
        False,
        True,
        False,
    )
    assert sub["grace_ends_at"] is None and sub["next_retry_at"] is None


def test_someone_elses_payment_is_a_plain_404(session, engine, ada, providers):
    ada.put(f"{API}/profile/country", {"billing_country": "US"})
    reference = ada.post(f"{API}/checkout", {"kind": "plan", "code": "p20"}).json()["payment"][
        "reference"
    ]
    make_user(session, "ben@example.test")
    with api_client(engine) as client:
        ben = Buyer(client, "ben@example.test")
        error(ben.get(f"{API}/payments/{reference}"), 404, "not_found")
        error(ben.get(f"{API}/payments/bn-nonsense"), 404, "not_found")
        assert ben.get(f"{API}/subscription").json()["pending_payment"] is None


def test_the_checkout_refusals_have_their_own_codes(session, ada, providers, monkeypatch):
    error(
        ada.post(f"{API}/checkout", {"kind": "plan", "code": "p20"}), 422, "billing_country_missing"
    )
    ada.put(f"{API}/profile/country", {"billing_country": "US"})
    error(ada.post(f"{API}/checkout", {"kind": "plan", "code": "nope"}), 404, "plan_not_found")
    error(ada.post(f"{API}/checkout", {"kind": "pack", "code": "pack10"}), 409, "plan_required")
    error(ada.post(f"{API}/checkout", {"kind": "gift", "code": "x"}), 422, "validation_error")
    error(ada.post(f"{API}/checkout", {"kind": "plan"}), 422, "validation_error")

    from app.billing.types import ProviderError

    providers.usd.checkout_error = ProviderError("down")
    error(ada.post(f"{API}/checkout", {"kind": "plan", "code": "p20"}), 502, "provider_error")
    providers.usd.checkout_error = None

    from app.billing.provider import PROVIDER_FACTORIES

    monkeypatch.delitem(PROVIDER_FACTORIES, "flutterwave")
    error(ada.post(f"{API}/checkout", {"kind": "plan", "code": "p20"}), 503, "provider_unavailable")
    monkeypatch.setitem(PROVIDER_FACTORIES, "flutterwave", lambda s: providers.usd)

    buy(session, ada, providers)
    error(ada.post(f"{API}/checkout", {"kind": "plan", "code": "p20"}), 409, "already_subscribed")
    assert rows(session, HourGrant) == 1


def test_a_pack_checkout_with_a_plan_is_paid_and_granted(session, ada, providers):
    buy(session, ada, providers)
    out = ada.post(f"{API}/checkout", {"kind": "pack", "code": "pack10"})
    assert out.status_code == 200 and out.json()["payment"]["kind"] == "pack"
    payment = session.scalars(select(Payment).order_by(Payment.id.desc())).first()
    pay(session, providers.usd, payment, now())

    state = ada.get(f"{API}/subscription").json()
    assert state["hours"]["seconds_left"] == P20 + 10 * HOUR


# ---- cancel, resume, change plan, pay link ----------------------------------------------------


def test_cancel_and_resume(session, ada, providers):
    error(ada.post(f"{API}/subscription/cancel"), 409, "no_subscription")
    buy(session, ada, providers)

    canceled = ada.post(f"{API}/subscription/cancel")
    assert canceled.status_code == 200
    sub = canceled.json()
    assert (sub["cancel_at_period_end"], sub["can_cancel"], sub["can_resume"]) == (
        True,
        False,
        True,
    )
    assert sub["status"] == "active"  # access to the period's end
    assert (
        ada.post(f"{API}/subscription/cancel").json()["cancel_at_period_end"] is True
    )  # idempotent

    resumed = ada.post(f"{API}/subscription/resume")
    assert resumed.status_code == 200 and resumed.json()["cancel_at_period_end"] is False
    error(ada.post(f"{API}/subscription/resume"), 409, "not_canceled")


def test_change_plan_downgrade_then_upgrade(session, ada, providers):
    buy(session, ada, providers, plan="p40")
    error(ada.post(f"{API}/subscription/change-plan", {"plan_code": "p40"}), 409, "same_plan")
    error(ada.post(f"{API}/subscription/change-plan", {"plan_code": "nope"}), 404, "plan_not_found")

    down = ada.post(f"{API}/subscription/change-plan", {"plan_code": "p20"})
    assert down.status_code == 200, down.text
    assert down.json()["result"] == "scheduled" and down.json()["payment"] is None
    assert down.json()["subscription"]["pending_plan_code"] == "p20"
    assert down.json()["subscription"]["amount_minor"] == 1800  # what the renewal will charge

    cleared = ada.post(f"{API}/subscription/change-plan", {"plan_code": "p40"})
    assert cleared.json()["result"] == "cleared"
    assert cleared.json()["subscription"]["pending_plan_code"] is None


def test_an_upgrade_through_the_route_is_a_checkout(session, ada, providers):
    buy(session, ada, providers, plan="p20")

    up = ada.post(f"{API}/subscription/change-plan", {"plan_code": "p40"})

    assert up.status_code == 200, up.text
    body = up.json()
    assert body["result"] == "checkout" and body["checkout_url"].startswith(
        "https://fake.test/pay/"
    )
    assert body["payment"]["kind"] == "upgrade" and 0 < body["payment"]["amount_minor"] <= 1600
    assert body["subscription"]["plan_code"] == "p20"  # not changed until the payment is verified


def test_the_pay_link_is_for_a_plan_in_its_grace_and_the_state_says_so(session, ada, providers):
    error(ada.post(f"{API}/subscription/pay-link"), 409, "no_subscription")
    buy(session, ada, providers)
    error(ada.post(f"{API}/subscription/pay-link"), 409, "nothing_due")

    # the due moment was an hour ago: move the stored dates back and let the clock run once
    when = now()
    sub = session.scalars(select(Subscription)).one()
    shift = sub.current_period_end - (when - timedelta(hours=1))
    session.execute(
        update(Subscription)
        .where(Subscription.id == sub.id)
        .values(
            current_period_start=Subscription.current_period_start - shift,
            current_period_end=Subscription.current_period_end - shift,
        )
    )
    session.execute(
        update(HourGrant).values(
            starts_at=HourGrant.starts_at - shift, expires_at=HourGrant.expires_at - shift
        )
    )
    session.commit()
    providers.usd.charge_outcome = "failed"
    renewals.run_due(session, when, settings())

    state = ada.get(f"{API}/subscription").json()
    sub_out = state["subscription"]
    assert (state["state"], sub_out["status"]) == ("past_due", "past_due")
    assert (sub_out["can_pay_now"], sub_out["can_change_plan"], sub_out["can_cancel"]) == (
        True,
        False,
        True,
    )
    assert sub_out["grace_ends_at"] is not None and sub_out["next_retry_at"] is not None
    assert state["hours"]["seconds_left"] == P20  # the old period's leftover: no new hours
    error(ada.post(f"{API}/subscription/change-plan", {"plan_code": "p40"}), 409, "payment_overdue")
    error(ada.post(f"{API}/subscription/resume"), 409, "not_canceled")

    link = ada.post(f"{API}/subscription/pay-link")
    assert link.status_code == 200, link.text
    assert link.json()["payment"]["kind"] == "renewal"
    again = ada.post(f"{API}/subscription/pay-link")  # the same open page, not a second checkout
    assert again.json()["checkout_url"] == link.json()["checkout_url"]
    assert rows(session, Payment) == 1 + 1 + 1  # the plan, the failed attempt, the one link


def test_the_lapsed_state_after_the_grace(session, ada, providers):
    buy(session, ada, providers)
    when = now()
    sub = session.scalars(select(Subscription)).one()
    shift = sub.current_period_end - (when - timedelta(hours=80))
    session.execute(
        update(Subscription)
        .where(Subscription.id == sub.id)
        .values(
            current_period_start=Subscription.current_period_start - shift,
            current_period_end=Subscription.current_period_end - shift,
        )
    )
    session.execute(
        update(HourGrant).values(
            starts_at=HourGrant.starts_at - shift, expires_at=HourGrant.expires_at - shift
        )
    )
    session.commit()

    state = ada.get(f"{API}/subscription").json()  # even before the worker has run: the clock says

    assert state["state"] == "lapsed" and state["subscription"]["status"] == "lapsed"
    assert state["hours"]["seconds_left"] == 0
    assert state["subscription"]["can_pay_now"] is False


def test_the_preview_route_reads_and_changes_nothing(session, ada, providers):
    error(ada.get(f"{API}/subscription/change-plan/preview?plan_code=p40"), 409, "no_subscription")
    buy(session, ada, providers, plan="p20")
    before = rows(session, Payment)

    up = ada.get(f"{API}/subscription/change-plan/preview?plan_code=p40")
    assert up.status_code == 200, up.text
    body = up.json()
    assert (body["kind"], body["plan_code"], body["currency"], body["takes_effect"]) == (
        "upgrade",
        "p40",
        "USD",
        "now",
    )
    assert 0 < body["amount_minor"] <= 1600 and body["extra_seconds"] > 0
    error(ada.get(f"{API}/subscription/change-plan/preview?plan_code=p20"), 409, "same_plan")
    error(ada.get(f"{API}/subscription/change-plan/preview?plan_code=nope"), 404, "plan_not_found")
    error(ada.get(f"{API}/subscription/change-plan/preview"), 422, "validation_error")
    assert rows(session, Payment) == before  # a read
