"""The billing country flow (the fix after the first live complaint): the country is SUGGESTED from
the saved time zone, changes freely until the person has ever paid, and keeps the 30-day and
end-of-period rules from the first payment on. Service rules on real Postgres first, then the routes
over HTTP. Helpers and the signed-in browser come from `tests/test_billing_profile.py`."""

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select

from app.billing import profile as profiles
from app.billing import routing
from app.billing.profile import CHANGE_WINDOW, CountryChangeRefused
from app.billing.zone_countries import ZONE_COUNTRIES, country_for_zone
from app.models import BillingCountryChange, BillingProfile, User, UserSettings
from tests.test_billing_profile import (  # noqa: F401  (fixtures)
    API,
    NOW,
    PERIOD_END,
    ada,
    error,
    has_paid,
    history,
    person,
    rows,
    sell,
    set_country,
    web,
)

# ---- before the first payment: free ----------------------------------------------------------


def test_before_any_payment_the_country_changes_instantly_any_number_of_times(session, person):
    countries = ["ET", "NG", "US", "GB", "NG"]
    for minute, country in enumerate(countries):
        change = set_country(session, person, country, now=NOW + timedelta(minutes=minute))
        assert change.result == "applied" and change.profile.billing_country == country

    # Every change still wrote its history row: the first has no old country, the rest do.
    assert [(old, new) for old, new, _ in history(session, person)] == [
        (None, "ET"),
        ("ET", "NG"),
        ("NG", "US"),
        ("US", "GB"),
        ("GB", "NG"),
    ]
    assert profiles.country_lock(session, person.id, NOW) is None
    assert profiles.country_change_allowed_at(session, person.id, NOW) is None


@pytest.mark.parametrize("status", ["pending", "failed"])
def test_a_payment_that_never_took_money_does_not_lock_the_country(session, person, status):
    has_paid(session, person, status=status)

    set_country(session, person, "ET", now=NOW)
    assert set_country(session, person, "NG", now=NOW + timedelta(minutes=1)).result == "applied"
    assert set_country(session, person, "US", now=NOW + timedelta(minutes=2)).result == "applied"


# ---- from the first payment on: the rule as it was --------------------------------------------


@pytest.mark.parametrize("status", ["succeeded", "refunded", "partly_refunded"])
def test_after_a_payment_one_change_in_30_days_applies_and_the_next_is_refused_with_the_date(
    session, person, status
):
    has_paid(session, person, at=NOW - timedelta(days=60), status=status)
    set_country(session, person, "NG", now=NOW - timedelta(days=50))  # the first choice: free

    assert set_country(session, person, "US", now=NOW).result == "applied"  # one fix is allowed

    with pytest.raises(CountryChangeRefused) as raised:
        set_country(session, person, "GB", now=NOW + timedelta(days=1))
    assert (raised.value.status, raised.value.code) == (409, "country_change_too_soon")
    # The refusal says the date (30 days after the change) in plain words.
    due = NOW + CHANGE_WINDOW
    assert f"{due.day} {due:%B %Y}" in raised.value.message


def test_changes_made_while_it_was_free_do_not_count_towards_the_30_days(session, person):
    for minute, country in enumerate(["ET", "NG", "US", "NG"]):  # four changes, all free
        set_country(session, person, country, now=NOW + timedelta(minutes=minute))
    has_paid(session, person, at=NOW + timedelta(hours=1))  # then the first payment

    assert profiles.country_change_allowed_at(session, person.id, NOW + timedelta(hours=2)) is None
    assert set_country(session, person, "US", now=NOW + timedelta(hours=2)).result == "applied"
    with pytest.raises(CountryChangeRefused):
        set_country(session, person, "NG", now=NOW + timedelta(hours=3))


def test_a_live_plan_locks_the_country_even_with_no_payment_on_record(session, person, monkeypatch):
    sell(monkeypatch, "US")
    set_country(session, person, "US", now=NOW - timedelta(days=1))
    set_country(session, person, "GB", now=NOW - timedelta(hours=1))  # one fix
    assert profiles.country_lock(session, person.id, NOW) is not None

    with pytest.raises(CountryChangeRefused) as raised:
        set_country(session, person, "FR", now=NOW)
    assert raised.value.code == "country_change_too_soon"


def test_after_a_payment_a_crossing_change_with_a_live_plan_still_waits_for_the_period_end(
    session, person, monkeypatch
):
    sell(monkeypatch, "US")
    has_paid(session, person)
    set_country(session, person, "US", now=NOW - timedelta(days=100))

    change = set_country(session, person, "NG", now=NOW)

    assert change.result == "scheduled" and change.profile.billing_country == "US"
    assert change.profile.pending_effective_at == PERIOD_END


def test_a_waiting_change_is_refused_with_the_date_it_takes_effect(session, person, monkeypatch):
    sell(monkeypatch, "US")
    has_paid(session, person)
    set_country(session, person, "US", now=NOW - timedelta(days=100))
    set_country(session, person, "NG", now=NOW)

    with pytest.raises(CountryChangeRefused) as raised:
        set_country(session, person, "GB", now=NOW + timedelta(days=1))
    assert raised.value.code == "country_change_pending"
    assert f"{PERIOD_END.day} {PERIOD_END:%B %Y}" in raised.value.message


# ---- the suggestion ---------------------------------------------------------------------------


def zone_for(session, user, zone):
    session.add(UserSettings(user_id=user.id, time_zone=zone))
    session.commit()


@pytest.mark.parametrize(
    ("zone", "country"),
    [
        ("Africa/Lagos", "NG"),
        ("Africa/Addis_Ababa", "ET"),
        ("Europe/London", "GB"),
        ("Asia/Calcutta", "IN"),
        ("UTC", None),
        ("Etc/GMT+1", None),
        ("Not/AZone", None),
        (None, None),
        ("", None),
    ],
)
def test_a_time_zone_points_to_its_country_or_to_none(zone, country):
    assert country_for_zone(zone) == country


def test_every_zone_in_the_table_points_to_a_country_the_server_accepts():
    for zone, country in ZONE_COUNTRIES.items():
        assert routing.clean_country(country) == country, zone


def test_the_suggestion_comes_from_the_saved_time_zone(session, person):
    zone_for(session, person, "Africa/Lagos")
    assert profiles.suggest_country(session, person, routing.DEFAULT_ROUTES) == "NG"


def test_no_zone_or_a_zone_with_no_country_suggests_nothing(session, person):
    assert profiles.suggest_country(session, person, routing.DEFAULT_ROUTES) is None
    zone_for(session, person, "UTC")
    assert profiles.suggest_country(session, person, routing.DEFAULT_ROUTES) is None


def test_a_country_that_cannot_be_routed_is_not_suggested(session, person):
    zone_for(session, person, "Africa/Lagos")
    assert profiles.suggest_country(session, person, "NG=off,*=USD:flutterwave") is None


# ---- over HTTP --------------------------------------------------------------------------------


def person_of(session, email="ada@example.test") -> User:
    return session.scalar(select(User).where(User.email == email))


def test_the_profile_suggests_nigeria_for_a_lagos_zone_and_saves_nothing(session, ada):
    zone_for(session, person_of(session), "Africa/Lagos")

    body = ada.get(f"{API}/profile").json()

    assert body["billing_country"] is None and body["route"] is None
    assert body["suggested_country"] == "NG"
    assert body["suggested_route"] == {"currency": "NGN", "provider": "paystack"}
    assert body["country_locked"] is False
    assert rows(session, BillingProfile) == 0 and rows(session, BillingCountryChange) == 0


def test_another_zone_suggests_its_country_on_the_dollar_route(session, ada):
    zone_for(session, person_of(session), "Africa/Addis_Ababa")

    body = ada.get(f"{API}/profile").json()

    assert body["suggested_country"] == "ET"
    assert body["suggested_route"] == {"currency": "USD", "provider": "flutterwave"}


def test_no_zone_suggests_nothing(session, ada):
    body = ada.get(f"{API}/profile").json()
    assert body["suggested_country"] is None and body["suggested_route"] is None


def test_once_a_country_is_saved_nothing_is_suggested(session, ada):
    zone_for(session, person_of(session), "Africa/Lagos")
    ada.put(f"{API}/profile/country", {"billing_country": "GB"})

    body = ada.get(f"{API}/profile").json()

    assert body["billing_country"] == "GB"
    assert body["suggested_country"] is None and body["suggested_route"] is None


def test_three_changes_before_paying_all_apply_then_the_rule_starts_with_the_payment(session, ada):
    user = person_of(session)
    for country in ("ET", "NG", "US"):
        response = ada.put(f"{API}/profile/country", {"billing_country": country})
        assert response.status_code == 200 and response.json()["result"] == "applied"
        profile = response.json()["profile"]
        assert profile["country_locked"] is False
        assert profile["country_change_allowed_at"] is None
    assert rows(session, BillingCountryChange) == 3
    for change in session.scalars(select(BillingCountryChange)):  # they were made earlier
        change.created_at -= timedelta(hours=1)
    session.commit()

    has_paid(session, user, at=datetime.now(UTC) - timedelta(minutes=1))
    after_paying = ada.get(f"{API}/profile").json()
    assert after_paying["country_locked"] is True
    assert after_paying["country_change_allowed_at"] is None  # the free changes do not count

    fix = ada.put(f"{API}/profile/country", {"billing_country": "NG"})
    assert fix.json()["result"] == "applied"
    assert fix.json()["profile"]["country_change_allowed_at"] is not None

    refused = ada.put(f"{API}/profile/country", {"billing_country": "GB"})
    message = error(refused, 409, "country_change_too_soon")["message"]
    assert "30 days" in message and "You can change it again on" in message
    assert rows(session, BillingCountryChange) == 4  # the refusal wrote nothing


def test_the_plans_preview_prices_a_country_nothing_is_saved(session, ada):
    from tests.billing_support import make_catalog

    make_catalog(session)

    ngn = ada.get(f"{API}/plans?country=NG").json()
    usd = ada.get(f"{API}/plans?country=et").json()
    none = ada.get(f"{API}/plans").json()

    assert ngn["route"] == {"currency": "NGN", "provider": "paystack"}
    assert {p["price"]["currency"] for p in ngn["plans"]} == {"NGN"}
    assert usd["route"] == {"currency": "USD", "provider": "flutterwave"}
    assert {p["price"]["currency"] for p in usd["plans"]} == {"USD"}
    assert none["route"] is None and none["plans"] == []
    assert rows(session, BillingProfile) == 0


@pytest.mark.parametrize(
    ("value", "code"),
    [("", "billing_country_missing"), ("Nigeria", "billing_country_invalid")],
)
def test_the_plans_preview_refuses_a_country_that_is_not_one(ada, value, code):
    error(ada.get(f"{API}/plans?country={value}"), 422, code)
