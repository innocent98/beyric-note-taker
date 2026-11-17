"""The billing profile, the change rules and the card-country note (billing stage S5).

Part one is the service (`app/billing/profile.py`) on real Postgres with the live subscription
replaced by a stub, because the subscriptions table is stage S8's. Part two is the routes
(`app/billing/profile_api.py`) over HTTP."""

import threading
import time
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app import auth
from app.billing import profile as profiles
from app.billing import routing
from app.billing.profile import CHANGE_WINDOW, CountryChangeRefused, LiveSubscription
from app.db import get_session
from app.main import create_app
from app.models import Base, BillingCountryChange, BillingProfile, User
from tests.test_web import PASSWORD, make_settings, make_user

CSRF = auth.CSRF_HEADER
NOW = datetime(2026, 11, 20, 12, 0, tzinfo=UTC)
PERIOD_END = NOW + timedelta(days=12)


@pytest.fixture
def person(session) -> User:
    return make_user(session, "person@example.test")


def sell(monkeypatch, sold_in="US", *, period_end=PERIOD_END, routes=routing.DEFAULT_ROUTES):
    """Stage S8 will answer "what is this person's live subscription"; here a test says it is one
    sold on the route that `sold_in` pays with, whose paid period ends at `period_end`."""
    route = routing.provider_for(sold_in, routes)
    live = LiveSubscription(period_end, route.currency, route.provider)
    monkeypatch.setattr(profiles, "live_subscription", lambda db, user_id, now: live)


@pytest.fixture
def live_subscription(monkeypatch):
    """A live subscription sold in the US (dollars, Flutterwave), ending at `PERIOD_END`."""
    sell(monkeypatch, "US")


def history(session, user) -> list[tuple]:
    session.expire_all()
    rows = session.scalars(
        select(BillingCountryChange)
        .where(BillingCountryChange.user_id == user.id)
        .order_by(BillingCountryChange.id)
    )
    return [(r.old_country, r.new_country, r.effective_at) for r in rows]


def set_country(session, user, country, now=NOW, **kw):
    change = profiles.change_country(session, user, country, now=now, **kw)
    session.commit()
    return change


def seed_country(session, user, country, *, asked_at=NOW - timedelta(days=100)):
    """A person who chose `country` long enough ago for another change to be allowed."""
    set_country(session, user, country, now=asked_at)


# ---- part one: the change rules -------------------------------------------------------------


def test_a_person_with_no_subscription_has_no_live_one(session, person):
    assert profiles.live_subscription(session, person.id, NOW) is None


def test_the_s5_stub_and_its_tripwire_are_gone_because_the_real_answer_exists(session, person):
    """Stage S8 added the subscriptions table and replaced the stub: the table exists, the function
    carries no stub marker, and it answers from the table with the route the subscription was SOLD
    on (the behaviour is tested in `test_billing_subscriptions.py`). This used to be a tripwire
    that raised while both a table and the stub existed; it now proves the stub went away for the
    right reason (a real answer), not because the table was hidden."""
    assert profiles.has_subscription_table(Base.metadata.tables)  # the table exists
    assert not getattr(profiles.live_subscription, "is_stub", False)  # and the stub is gone
    assert not hasattr(profiles, "_subscriptions_table_exists")

    # the real function reads the table: a live row is answered with the route it was sold on, a
    # lapsed or cancelled one is not live
    from app.models import Plan, PlanPrice, Subscription

    session.add(Plan(code="t", name="T", monthly_seconds=3600))
    session.flush()
    price = PlanPrice(plan_code="t", currency="NGN", amount_minor=100, active_from=NOW)
    session.add(price)
    session.flush()
    row = Subscription(
        user_id=person.id,
        payer_user_id=person.id,
        plan_code="t",
        price_id=price.id,
        status="active",
        currency="NGN",
        provider="paystack",
        current_period_start=NOW,
        current_period_end=PERIOD_END,
    )
    session.add(row)
    session.commit()
    live = profiles.live_subscription(session, person.id, NOW)
    assert (live.period_end, live.currency, live.provider) == (PERIOD_END, "NGN", "paystack")
    assert live.route == routing.Route("NGN", "paystack")
    for status in ("lapsed", "canceled", "ended"):
        row.status = status
        session.commit()
        assert profiles.live_subscription(session, person.id, NOW) is None
    row.status = "past_due"  # the grace is still live: a country change applies at once there
    session.commit()
    assert profiles.live_subscription(session, person.id, NOW) is not None


def test_a_crossing_change_is_judged_against_the_route_the_subscription_was_sold_on(
    session, person
):
    """The country table is edited mid-period: a naira subscription must not become a dollar one."""
    from app.models import Plan, PlanPrice, Subscription

    seed_country(session, person, "NG")
    session.add(Plan(code="t", name="T", monthly_seconds=3600))
    session.flush()
    price = PlanPrice(plan_code="t", currency="NGN", amount_minor=100, active_from=NOW)
    session.add(price)
    session.flush()
    session.add(
        Subscription(
            user_id=person.id,
            payer_user_id=person.id,
            plan_code="t",
            price_id=price.id,
            status="active",
            currency="NGN",
            provider="paystack",
            current_period_start=NOW,
            current_period_end=PERIOD_END,
        )
    )
    session.commit()
    # an operator has since pointed every country, Nigeria too, at dollars through Flutterwave:
    # today's table says NG and GB pay alike, but the subscription was SOLD in naira (Paystack)
    table = "*=USD:flutterwave"
    change = profiles.change_country(session, person, "GB", routes_text=table, now=NOW)

    assert change.result == "scheduled"  # judged against the sold route, not today's table


@pytest.mark.parametrize(
    ("names", "found"),
    [
        (["subscriptions"], True),
        (["billing_subscriptions"], True),  # S8 may name it anything with the word in it
        (["plan_subscriptions", "payments"], True),
        (["payments", "billing_profiles", "billing_country_changes", "users"], False),
        ([], False),
    ],
)
def test_the_tripwire_notices_a_subscription_table_by_any_name(names, found):
    assert profiles.has_subscription_table(names) is found


def test_a_waiting_change_that_matches_the_country_still_clears(session, person):
    """A row whose pending country equals its country (it cannot be made today) must not stay
    stuck refusing every change: applying clears it."""
    seed_country(session, person, "NG")
    row = profiles.get_profile(session, person)
    row.pending_billing_country = "NG"
    row.pending_effective_at = NOW - timedelta(days=1)
    session.commit()

    assert profiles.apply_due_pending(row, NOW)

    assert (row.billing_country, row.pending_billing_country) == ("NG", None)
    assert row.pending_effective_at is None


def test_s8_cannot_add_the_subscriptions_table_and_keep_the_stub():
    """The same tripwire, as a plain test: it fails the day both are true."""
    stub = getattr(profiles.live_subscription, "is_stub", False)
    assert not (stub and profiles.has_subscription_table(Base.metadata.tables)), (
        "stage S8 added `subscriptions` but profile.live_subscription is still the S5 stub: "
        "replace it (return the live subscription's period end, currency and provider)"
    )


def test_the_first_choice_applies_at_once_and_is_written_to_the_history(session, person):
    change = set_country(session, person, "ng")

    assert change.result == "applied"
    assert change.profile.billing_country == "NG"
    assert change.profile.pending_billing_country is None
    assert history(session, person) == [(None, "NG", NOW)]


def test_the_same_country_changes_nothing_and_writes_nothing(session, person):
    seed_country(session, person, "NG")
    before = history(session, person)

    change = set_country(session, person, "NG")

    assert change.result == "unchanged" and change.profile.billing_country == "NG"
    assert history(session, person) == before


# Every row of the spec's table of what happens when the person changes the billing country
# (section 5.2), plus the rows that table implies: a live subscription crossing in either
# direction, and a change inside one currency.
#   (live subscription?, old country, new country, result, country now, pending country)
ROWS = [
    pytest.param(False, "NG", "US", "applied", "US", None, id="no live subscription: Nigeria out"),
    pytest.param(False, "US", "NG", "applied", "NG", None, id="no live subscription: Nigeria in"),
    pytest.param(
        False, "US", "GB", "applied", "GB", None, id="no live subscription: same currency"
    ),
    pytest.param(True, "US", "GB", "applied", "GB", None, id="live, same currency: now"),
    pytest.param(True, "FR", "JP", "applied", "JP", None, id="live, two non-Nigerian countries"),
    pytest.param(True, "US", "NG", "scheduled", "US", "NG", id="live, crossing into Nigeria"),
    pytest.param(True, "NG", "US", "scheduled", "NG", "US", id="live, crossing out of Nigeria"),
    pytest.param(True, "NG", "NG", "unchanged", "NG", None, id="live, the same country"),
]


@pytest.mark.parametrize(("live", "old", "new", "result", "now_country", "pending"), ROWS)
def test_every_row_of_the_change_table(
    session, person, monkeypatch, live, old, new, result, now_country, pending
):
    seed_country(session, person, old)
    if live:
        sell(monkeypatch, old)  # sold on the route the person's old country pays with
    rows_before = len(history(session, person))

    change = set_country(session, person, new)

    assert change.result == result
    assert change.profile.billing_country == now_country
    assert change.profile.pending_billing_country == pending
    assert change.profile.pending_effective_at == (PERIOD_END if pending else None)
    after = history(session, person)
    if result == "unchanged":
        assert len(after) == rows_before  # nothing written
    else:
        assert len(after) == rows_before + 1
        # An applied change is effective when asked; a scheduled one at the end of the period.
        assert after[-1] == (old, new, PERIOD_END if pending else NOW)


def test_a_scheduled_change_keeps_the_old_route_until_its_time_then_takes_effect(
    session, person, live_subscription
):
    seed_country(session, person, "US")
    change = set_country(session, person, "NG")
    profile = change.profile

    # Until the end of the period renewals and packs still use the old country.
    assert not profiles.apply_due_pending(profile, PERIOD_END - timedelta(seconds=1))
    assert profile.billing_country == "US" and profile.pending_billing_country == "NG"
    assert routing.provider_for(profile.billing_country).currency == "USD"

    assert profiles.apply_due_pending(profile, PERIOD_END)
    session.commit()
    assert profile.billing_country == "NG"
    assert profile.pending_billing_country is None and profile.pending_effective_at is None
    assert routing.provider_for(profile.billing_country).currency == "NGN"
    assert len(history(session, person)) == 2  # taking effect writes no second row
    assert not profiles.apply_due_pending(profile, PERIOD_END)  # and nothing is left to apply


@pytest.mark.parametrize("days_later", [5, 40])  # inside the 30 days, and past them
def test_a_second_change_while_one_is_waiting_is_refused(session, person, monkeypatch, days_later):
    sell(monkeypatch, "US", period_end=NOW + timedelta(days=60))
    seed_country(session, person, "US")
    set_country(session, person, "NG")

    for country in ("GB", "US", "NG"):  # another country, the old one (a cancel), the pending one
        with pytest.raises(CountryChangeRefused) as raised:
            set_country(session, person, country, now=NOW + timedelta(days=days_later))
        assert (raised.value.status, raised.value.code) == (409, "country_change_pending")
        session.rollback()
    assert len(history(session, person)) == 2


def test_a_period_end_in_the_past_is_not_a_live_subscription(session, person, monkeypatch):
    sell(monkeypatch, "US", period_end=NOW - timedelta(days=1))
    seed_country(session, person, "US")

    assert set_country(session, person, "NG").result == "applied"


def test_the_crossing_is_judged_on_the_route_the_subscription_was_sold_on(
    session, person, monkeypatch
):
    """An operator edits the table mid-period (Paystack off the wrong way): a Nigerian naira
    subscription must not start to look like a dollar one."""
    seed_country(session, person, "NG")
    sell(monkeypatch, "NG")  # sold in naira through Paystack
    after_the_edit = "*=USD:flutterwave"  # today's table puts Nigeria and the US on one route

    change = set_country(session, person, "US", routes_text=after_the_edit)

    assert change.result == "scheduled"  # the subscription is still naira: it waits
    assert change.profile.billing_country == "NG"


def test_one_change_in_30_days_and_not_a_minute_sooner(session, person):
    seed_country(session, person, "NG", asked_at=NOW - timedelta(days=100))
    set_country(session, person, "US", now=NOW)

    just_before = NOW + CHANGE_WINDOW - timedelta(seconds=1)
    for country in ("NG", "GB"):
        with pytest.raises(CountryChangeRefused) as raised:
            set_country(session, person, country, now=just_before)
        assert (raised.value.status, raised.value.code) == (409, "country_change_too_soon")
        session.rollback()
    assert history(session, person)[-1][:2] == ("NG", "US")  # still two rows, nothing added
    assert len(history(session, person)) == 2
    assert session.get(BillingProfile, person_profile_id(session, person)).billing_country == "US"

    assert set_country(session, person, "NG", now=NOW + CHANGE_WINDOW).result == "applied"
    assert profiles.country_change_allowed_at(session, person.id, NOW + CHANGE_WINDOW) == (
        NOW + CHANGE_WINDOW + CHANGE_WINDOW
    )


def person_profile_id(session, user) -> int:
    return session.scalar(select(BillingProfile.id).where(BillingProfile.user_id == user.id))


def test_the_first_choice_is_not_a_change_so_one_fix_is_allowed_straight_after(session, person):
    set_country(session, person, "NG", now=NOW)  # picked the wrong country by mistake
    assert profiles.country_change_allowed_at(session, person.id, NOW) is None

    assert set_country(session, person, "US", now=NOW + timedelta(minutes=2)).result == "applied"

    with pytest.raises(CountryChangeRefused):  # but only one
        set_country(session, person, "GB", now=NOW + timedelta(minutes=3))


def test_a_scheduled_change_counts_from_the_day_it_was_asked_not_the_day_it_applies(
    session, person, live_subscription
):
    seed_country(session, person, "US")
    set_country(session, person, "NG", now=NOW)  # takes effect in 12 days

    allowed = profiles.country_change_allowed_at(session, person.id, NOW)

    assert allowed == NOW + CHANGE_WINDOW


@pytest.mark.parametrize("bad", [None, "", "Nigeria", "ZZ", "UK"])
def test_a_country_that_is_missing_or_not_one_is_refused_and_writes_nothing(session, person, bad):
    with pytest.raises(routing.RoutingError):
        profiles.change_country(session, person, bad, now=NOW)
    session.rollback()
    assert session.scalar(select(func.count()).select_from(BillingProfile)) == 0
    assert history(session, person) == []


def test_a_country_with_no_route_is_refused_and_writes_nothing(session, person):
    with pytest.raises(routing.CountryUnsupported):
        profiles.change_country(session, person, "US", routes_text="NG=NGN:paystack", now=NOW)
    session.rollback()
    assert session.scalar(select(func.count()).select_from(BillingProfile)) == 0


def test_changing_the_details_never_touches_the_country(session, person):
    seed_country(session, person, "NG")

    row = profiles.save_details(
        session,
        person,
        name_or_company="Acme Ltd",
        address="1 Road\nLagos",
        tax_id="TIN-0001",
        billing_email="bills@example.test",
        now=NOW,
    )
    session.commit()

    assert (row.billing_country, row.pending_billing_country) == ("NG", None)
    assert row.name_or_company == "Acme Ltd" and row.address == "1 Road\nLagos"
    assert len(history(session, person)) == 1


def test_only_the_person_changes_their_own_country(session, person):
    other = make_user(session, "other@example.test")
    seed_country(session, person, "NG")
    seed_country(session, other, "GB")

    set_country(session, person, "US")

    assert profiles.get_profile(session, person).billing_country == "US"
    assert profiles.get_profile(session, other).billing_country == "GB"
    assert history(session, other) == [(None, "GB", NOW - timedelta(days=100))]


def test_deleting_the_account_deletes_the_profile_and_its_history(session, person):
    seed_country(session, person, "NG")
    set_country(session, person, "US")

    session.delete(person)
    session.commit()

    assert session.scalar(select(func.count()).select_from(BillingProfile)) == 0
    assert session.scalar(select(func.count()).select_from(BillingCountryChange)) == 0


# ---- part two: the routes -------------------------------------------------------------------

API = "/api/billing"


def api_client(engine, *, enabled=True, **settings):
    """The app with billing on (or off), open to every account unless a test says otherwise, and a
    way to be signed in as any of its users."""
    settings.setdefault("billing_allowed_emails", "*")
    app = create_app(make_settings(billing_enabled=enabled, **settings))

    def session_override():
        with Session(engine, expire_on_commit=False) as s:
            yield s

    app.dependency_overrides[get_session] = session_override
    return TestClient(app, follow_redirects=False)


class Person:
    """One signed-in browser."""

    def __init__(self, client: TestClient, email: str) -> None:
        self.client = client
        self.csrf = client.get("/api/session").json()["csrf_token"]
        login = client.post(
            "/api/session",
            json={"email": email, "password": PASSWORD},
            headers={CSRF: self.csrf},
        )
        assert login.status_code == 200, login.text
        self.csrf = login.json()["csrf_token"]

    def get(self, url):
        return self.client.get(url)

    def put(self, url, body=None, csrf=True):
        headers = {CSRF: self.csrf} if csrf else {}
        return self.client.put(url, json=body, headers=headers)


@pytest.fixture
def web(engine):
    with api_client(engine) as client:
        yield client


@pytest.fixture
def ada(session, web) -> Person:
    make_user(session, "ada@example.test")
    return Person(web, "ada@example.test")


def other_person(session, engine, email="ben@example.test") -> Person:
    """A second browser, with its own cookies: the same app, a different account."""
    make_user(session, email)
    return Person(api_client(engine).__enter__(), email)


def error(response, status, code):
    assert response.status_code == status, response.text
    body = response.json()
    assert set(body) == {"error"} and body["error"]["code"] == code, body
    return body["error"]


def rows(session, model) -> int:
    session.expire_all()
    return session.scalar(select(func.count()).select_from(model))


EMPTY = {
    "name_or_company": None,
    "address": None,
    "tax_id": None,
    "billing_email": None,
    "billing_country": None,
    "pending_billing_country": None,
    "pending_effective_at": None,
    "route": None,
    "country_change_allowed_at": None,
}


def test_with_billing_off_every_route_is_a_404_and_nothing_is_written(engine, session):
    make_user(session, "ada@example.test")
    with api_client(engine, enabled=False) as off:
        ada = Person(off, "ada@example.test")
        for method, url, body in [
            ("get", f"{API}/profile", None),
            ("put", f"{API}/profile", {"name_or_company": "Acme"}),
            ("put", f"{API}/profile/country", {"billing_country": "NG"}),
            ("get", f"{API}/route?country=NG", None),
        ]:
            response = getattr(ada, method)(url, *([body] if method == "put" else []))
            error(response, 404, "not_found")
    assert rows(session, BillingProfile) == 0 and rows(session, BillingCountryChange) == 0


def test_with_billing_off_even_a_bad_body_is_a_404_not_a_422(engine, session):
    make_user(session, "ada@example.test")
    with api_client(engine, enabled=False) as off:
        ada = Person(off, "ada@example.test")
        error(ada.put(f"{API}/profile", {"surprise": 1}), 404, "not_found")
        error(ada.put(f"{API}/profile/country", {}), 404, "not_found")


def test_a_visitor_and_a_missing_csrf_token_are_refused_before_billing_is_asked(web, ada):
    with TestClient(web.app, follow_redirects=False) as visitor:
        error(visitor.get(f"{API}/profile"), 401, "not_authenticated")
    error(ada.put(f"{API}/profile", {}, csrf=False), 403, "csrf_rejected")
    error(
        ada.put(f"{API}/profile/country", {"billing_country": "NG"}, csrf=False),
        403,
        "csrf_rejected",
    )


def test_a_new_person_reads_an_empty_profile_and_reading_creates_nothing(session, ada):
    response = ada.get(f"{API}/profile")

    assert response.status_code == 200 and response.json() == EMPTY
    assert rows(session, BillingProfile) == 0


def test_the_details_are_saved_trimmed_and_read_back(session, ada):
    response = ada.put(
        f"{API}/profile",
        {
            "name_or_company": "  Acme Ltd ",
            "address": "1 Marina Road\n  Lagos ",
            "tax_id": "TIN-0001",
            "billing_email": " Bills@Example.TEST ",
        },
    )

    expected = {
        **EMPTY,
        "name_or_company": "Acme Ltd",
        "address": "1 Marina Road\n  Lagos",
        "tax_id": "TIN-0001",
        "billing_email": "bills@example.test",
    }
    assert response.status_code == 200 and response.json() == expected
    assert ada.get(f"{API}/profile").json() == expected


def test_a_put_replaces_all_four_and_empty_or_null_clears_one(ada):
    ada.put(f"{API}/profile", {"name_or_company": "Acme", "tax_id": "T1", "address": "x"})

    cleared = ada.put(f"{API}/profile", {"name_or_company": "  ", "tax_id": None}).json()

    assert cleared == EMPTY  # address was left out, so it is cleared too


@pytest.mark.parametrize(
    "body",
    [
        {"billing_email": "not-an-address"},
        {"billing_email": "a@b"},
        {"billing_email": "ünï@example.test"},
    ],
)
def test_a_bad_billing_email_is_422(ada, body):
    error(ada.put(f"{API}/profile", body), 422, "billing_email_invalid")


@pytest.mark.parametrize(
    "body",
    [
        {"name_or_company": "x" * 201},
        {"address": "x" * 501},
        {"tax_id": "x" * 65},
        {"billing_email": "x" * 255},
        {"name_or_company": "line\nbreak"},  # only an address has lines
        {"tax_id": "nul\x00byte"},
        {"address": "bell\x07"},
        {"name_or_company": "del\x7fchar"},
        {"name_or_company": "bidi \u202eoverride"},  # would reorder text on an invoice
        {"tax_id": "zero\u200bwidth"},
        {"address": "next\x85line"},
        {"name_or_company": 5},  # no coercion
        {"billing_country": "NG"},  # the country has its own route and its own rules
        {"surprise": 1},
    ],
)
def test_a_bad_profile_body_is_422_and_stores_nothing(session, ada, body):
    error(ada.put(f"{API}/profile", body), 422, "validation_error")
    assert rows(session, BillingProfile) == 0


def test_saving_the_details_never_sets_or_changes_the_country(ada):
    ada.put(f"{API}/profile/country", {"billing_country": "ng"})

    profile = ada.put(f"{API}/profile", {"name_or_company": "Acme"}).json()

    assert profile["billing_country"] == "NG"


def test_the_first_country_is_applied_and_says_what_it_pays_with(session, ada):
    response = ada.put(f"{API}/profile/country", {"billing_country": "ng"})

    assert response.status_code == 200
    body = response.json()
    assert body["result"] == "applied"
    assert body["profile"]["billing_country"] == "NG"
    assert body["profile"]["route"] == {"currency": "NGN", "provider": "paystack"}
    assert body["profile"]["pending_billing_country"] is None
    assert body["profile"]["country_change_allowed_at"] is None  # the first choice is free
    assert rows(session, BillingCountryChange) == 1


def test_any_other_country_pays_dollars_through_flutterwave(ada):
    body = ada.put(f"{API}/profile/country", {"billing_country": "GB"}).json()

    assert body["profile"]["route"] == {"currency": "USD", "provider": "flutterwave"}


def test_a_second_change_is_applied_once_and_the_third_is_refused_for_30_days(session, ada):
    ada.put(f"{API}/profile/country", {"billing_country": "NG"})
    second = ada.put(f"{API}/profile/country", {"billing_country": "US"})
    assert second.status_code == 200 and second.json()["result"] == "applied"
    allowed = second.json()["profile"]["country_change_allowed_at"]
    assert allowed is not None

    third = ada.put(f"{API}/profile/country", {"billing_country": "GB"})

    error(third, 409, "country_change_too_soon")
    assert ada.get(f"{API}/profile").json()["billing_country"] == "US"
    assert ada.get(f"{API}/profile").json()["country_change_allowed_at"] == allowed
    assert rows(session, BillingCountryChange) == 2  # the refusal wrote nothing


def test_the_same_country_again_is_unchanged_and_writes_nothing(session, ada):
    ada.put(f"{API}/profile/country", {"billing_country": "NG"})

    again = ada.put(f"{API}/profile/country", {"billing_country": "NG"})

    assert again.status_code == 200 and again.json()["result"] == "unchanged"
    assert rows(session, BillingCountryChange) == 1


@pytest.mark.parametrize(
    ("value", "code"),
    [
        ("", "billing_country_missing"),
        ("   ", "billing_country_missing"),
        ("Nigeria", "billing_country_invalid"),
        ("UK", "billing_country_invalid"),
        ("ZZ", "billing_country_invalid"),
        ("NGA", "billing_country_invalid"),
    ],
)
def test_a_country_that_is_missing_or_not_one_is_422_and_nothing_is_guessed(
    session, ada, value, code
):
    error(ada.put(f"{API}/profile/country", {"billing_country": value}), 422, code)
    assert rows(session, BillingProfile) == 0 and rows(session, BillingCountryChange) == 0


@pytest.mark.parametrize("body", [{}, {"billing_country": None}, {"billing_country": 5}, {"x": 1}])
def test_a_bad_country_body_is_422(ada, body):
    error(ada.put(f"{API}/profile/country", body), 422, "validation_error")


def test_a_country_with_no_route_is_422_unsupported(engine, session):
    make_user(session, "ada@example.test")
    with api_client(engine, billing_country_routes="NG=NGN:paystack") as only_nigeria:
        ada = Person(only_nigeria, "ada@example.test")
        error(
            ada.put(f"{API}/profile/country", {"billing_country": "US"}),
            422,
            "billing_country_unsupported",
        )
        assert ada.put(f"{API}/profile/country", {"billing_country": "NG"}).status_code == 200
    assert rows(session, BillingCountryChange) == 1


def test_a_crossing_change_with_a_live_subscription_is_scheduled_and_the_old_route_stays(
    session, ada, monkeypatch
):
    period_end = datetime.now(UTC) + timedelta(days=12)
    ada.put(f"{API}/profile/country", {"billing_country": "US"})
    # a person who chose their country long ago may change it: age the history by hand
    session.execute(
        BillingCountryChange.__table__.update().values(created_at=NOW - timedelta(days=90))
    )
    session.commit()
    sell(monkeypatch, "US", period_end=period_end)

    response = ada.put(f"{API}/profile/country", {"billing_country": "NG"})

    assert response.status_code == 200
    body = response.json()
    assert body["result"] == "scheduled"
    profile = body["profile"]
    assert profile["billing_country"] == "US" and profile["pending_billing_country"] == "NG"
    assert profile["route"] == {"currency": "USD", "provider": "flutterwave"}  # still the old one
    assert datetime.fromisoformat(profile["pending_effective_at"]) == period_end
    error(
        ada.put(f"{API}/profile/country", {"billing_country": "GB"}), 409, "country_change_pending"
    )
    error(
        ada.put(f"{API}/profile/country", {"billing_country": "US"}), 409, "country_change_pending"
    )


def test_reading_after_the_period_ended_shows_the_new_country_and_writes_nothing(session, ada):
    ada.put(f"{API}/profile/country", {"billing_country": "US"})
    row = session.scalar(select(BillingProfile))
    due = datetime.now(UTC) - timedelta(minutes=1)
    row.pending_billing_country = "NG"
    row.pending_effective_at = due
    session.commit()
    stamp = row.updated_at

    profile = ada.get(f"{API}/profile").json()

    assert profile["billing_country"] == "NG" and profile["pending_billing_country"] is None
    assert profile["pending_effective_at"] is None
    assert profile["route"] == {"currency": "NGN", "provider": "paystack"}
    session.expire_all()  # the read changed no row: the change is written by the next write
    row = session.scalar(select(BillingProfile))
    assert (row.billing_country, row.pending_billing_country) == ("US", "NG")
    assert row.pending_effective_at == due and row.updated_at == stamp


def test_a_write_after_the_period_ended_writes_the_waiting_change_first(session, ada):
    ada.put(f"{API}/profile/country", {"billing_country": "US"})
    row = session.scalar(select(BillingProfile))
    row.pending_billing_country = "NG"
    row.pending_effective_at = datetime.now(UTC) - timedelta(minutes=1)
    session.commit()

    ada.put(f"{API}/profile", {"name_or_company": "Acme"})

    session.expire_all()
    row = session.scalar(select(BillingProfile))
    assert (row.billing_country, row.pending_billing_country) == ("NG", None)


def test_the_route_preview_states_the_result_before_anything_is_chosen(session, ada):
    ng = ada.get(f"{API}/route?country=ng")
    gb = ada.get(f"{API}/route?country=GB")

    assert ng.status_code == 200 and ng.json() == {
        "billing_country": "NG",
        "currency": "NGN",
        "provider": "paystack",
    }
    assert gb.json() == {"billing_country": "GB", "currency": "USD", "provider": "flutterwave"}
    error(ada.get(f"{API}/route"), 422, "billing_country_missing")
    error(ada.get(f"{API}/route?country=Nigeria"), 422, "billing_country_invalid")
    assert rows(session, BillingProfile) == 0  # a preview stores nothing


def test_only_the_person_reads_and_changes_their_own_profile(session, engine, ada):
    ben = other_person(session, engine)
    ada.put(f"{API}/profile", {"name_or_company": "Ada's company"})
    ada.put(f"{API}/profile/country", {"billing_country": "NG"})

    assert ben.get(f"{API}/profile").json() == EMPTY  # Ben sees nothing of Ada's
    ben.put(f"{API}/profile", {"name_or_company": "Ben's company"})
    ben.put(f"{API}/profile/country", {"billing_country": "GB"})

    assert ada.get(f"{API}/profile").json()["name_or_company"] == "Ada's company"
    assert ada.get(f"{API}/profile").json()["billing_country"] == "NG"
    assert ben.get(f"{API}/profile").json()["billing_country"] == "GB"
    # Ada's changes did not use up Ben's: each person's own history decides.
    assert (
        ben.put(f"{API}/profile/country", {"billing_country": "FR"}).json()["result"] == "applied"
    )


def test_no_billing_route_takes_a_persons_id(web):
    """Nothing in the API can aim at someone else's row: no path names a person, and the one path
    with a parameter (stage S8's `/payments/{reference}`, stage S9's `/invoices/{number}/pdf`) takes
    a payment reference or an invoice number that the route only answers for the payer who owns it
    (`test_billing_subscriptions_api.py`, `test_billing_invoices_api.py`). The admin's own routes
    (`/api/admin/billing/...`, stage S9) are admin-only and listed in `test_admin.py`."""
    paths = [p for p in web.app.openapi()["paths"] if p.startswith(API) and "webhooks" not in p]
    assert sorted(paths) == [
        f"{API}/checkout",
        f"{API}/invoices",
        f"{API}/invoices/{{number}}/pdf",
        f"{API}/payments",
        f"{API}/payments/{{reference}}",
        f"{API}/plans",
        f"{API}/profile",
        f"{API}/profile/country",
        f"{API}/route",
        f"{API}/subscription",
        f"{API}/subscription/cancel",
        f"{API}/subscription/change-plan",
        f"{API}/subscription/change-plan/preview",
        f"{API}/subscription/pay-link",
        f"{API}/subscription/resume",
    ]
    parameters = {part.strip("{}") for p in paths for part in p.split("/") if part.startswith("{")}
    assert parameters == {"reference", "number"}


def test_only_allow_listed_accounts_use_billing_when_there_is_a_list(engine, session):
    make_user(session, "ada@example.test")
    make_user(session, "ben@example.test")
    with api_client(
        engine, billing_allowed_emails=" Ada@Example.test , nobody@example.test "
    ) as web:
        ada = Person(web, "ada@example.test")
        assert ada.get(f"{API}/profile").status_code == 200
        assert ada.put(f"{API}/profile/country", {"billing_country": "NG"}).status_code == 200
    with api_client(engine, billing_allowed_emails="ada@example.test") as web:
        ben = Person(web, "ben@example.test")
        error(ben.get(f"{API}/profile"), 404, "not_found")
        error(ben.put(f"{API}/profile", {"name_or_company": "x"}), 404, "not_found")
        error(ben.put(f"{API}/profile/country", {"billing_country": "NG"}), 404, "not_found")
        error(ben.get(f"{API}/route?country=NG"), 404, "not_found")
    session.expire_all()
    assert [p.user_id for p in session.scalars(select(BillingProfile))] == [
        session.scalar(select(User.id).where(User.email == "ada@example.test"))
    ]


def test_an_empty_allow_list_is_closed_not_open(engine, session):
    """Billing on but nobody listed (a webhook test, a provider's test mode): no account may write
    billing details or choose a country."""
    make_user(session, "ada@example.test")
    with api_client(engine, billing_allowed_emails="") as web:
        ada = Person(web, "ada@example.test")
        error(ada.get(f"{API}/profile"), 404, "not_found")
        error(ada.put(f"{API}/profile", {"name_or_company": "x"}), 404, "not_found")
        error(ada.put(f"{API}/profile/country", {"billing_country": "NG"}), 404, "not_found")
        error(ada.get(f"{API}/route?country=NG"), 404, "not_found")
    assert rows(session, BillingProfile) == 0


def test_the_default_setting_is_closed():
    assert make_settings().billing_allowed_emails == ""


def test_a_star_opens_the_routes_to_every_account(engine, session):
    make_user(session, "ada@example.test")
    with api_client(engine, billing_allowed_emails="*") as web:
        assert Person(web, "ada@example.test").get(f"{API}/profile").status_code == 200
    with api_client(engine, billing_allowed_emails="someone@example.test, *") as web:
        assert Person(web, "ada@example.test").get(f"{API}/profile").status_code == 200


# ---- the row lock ---------------------------------------------------------------------------


def test_two_simultaneous_changes_cannot_both_pass_the_30_day_rule(engine):
    """Session A changes the country and holds the profile row (not committed yet). Session B's
    change for the same person must wait for it, then see A's history row and be refused: two
    changes in 30 days never both pass. (The next test shows the row lock on its own.)"""
    with Session(engine) as setup:
        user = make_user(setup, "race@example.test")
        user_id = user.id
        seed_country(setup, user, "NG")
    a, b = Session(engine), Session(engine)
    outcome: dict = {}

    def second_change():
        try:
            profiles.change_country(b, b.get(User, user_id), "GB", now=NOW + timedelta(minutes=1))
            outcome["result"] = "applied"
        except CountryChangeRefused as exc:
            outcome["result"] = exc.code
        finally:
            b.rollback()

    try:
        profiles.change_country(a, a.get(User, user_id), "US", now=NOW)  # holds the lock
        worker = threading.Thread(target=second_change)
        worker.start()
        time.sleep(1.0)
        assert worker.is_alive() and not outcome  # B is waiting on A's row lock
        a.commit()
        worker.join(15)
        assert not worker.is_alive()
    finally:
        a.close()
        b.close()

    assert outcome == {"result": "country_change_too_soon"}
    with Session(engine) as check:
        changes = check.scalars(
            select(BillingCountryChange).where(BillingCountryChange.user_id == user_id)
        ).all()
        assert [c.new_country for c in sorted(changes, key=lambda c: c.id)] == ["NG", "US"]
        assert check.scalar(select(BillingProfile.billing_country)) == "US"


def test_a_change_waits_for_the_profile_row_lock(engine):
    """Session A takes only the profile row's lock (it changes nothing). Session B's change must
    wait for it: this is what `FOR UPDATE` alone buys, apart from the insert that makes the row
    (which also waits on a row another transaction is updating)."""
    with Session(engine) as setup:
        user = make_user(setup, "lock@example.test")
        user_id = user.id
        seed_country(setup, user, "NG")
    a, b = Session(engine), Session(engine)
    outcome: dict = {}

    def change():
        try:
            profiles.change_country(b, b.get(User, user_id), "US", now=NOW)
            b.commit()
            outcome["result"] = "applied"
        finally:
            b.close()

    try:
        profiles._locked_profile(a, a.get(User, user_id))  # holds the row lock
        worker = threading.Thread(target=change)
        worker.start()
        time.sleep(1.0)
        assert worker.is_alive() and not outcome  # B is waiting on the row lock
        a.commit()
        worker.join(15)
        assert not worker.is_alive()
    finally:
        a.close()

    assert outcome == {"result": "applied"}
