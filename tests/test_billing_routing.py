"""Which currency and provider a billing country pays with (billing stage S5, spec section 5.2).

Pure functions: no database. The table test names every row of the spec's routing table, and the
errors show that nothing is ever guessed."""

import pytest
from pydantic import ValidationError

from app.billing import routing
from app.billing.countries import ISO_COUNTRIES
from app.billing.routing import (
    CountryInvalid,
    CountryMissing,
    CountryUnsupported,
    Route,
    provider_for,
)
from tests.test_web import make_settings

NAIRA = Route("NGN", "paystack")
DOLLARS = Route("USD", "flutterwave")


def test_the_iso_list_is_the_249_assigned_codes():
    assert len(ISO_COUNTRIES) == 249
    assert all(len(code) == 2 and code.isupper() for code in ISO_COUNTRIES)
    # Not codes: the United Kingdom is GB, and Kosovo has no official code.
    assert not {"UK", "XK", "AC", "EU"} & ISO_COUNTRIES


@pytest.mark.parametrize(
    ("country", "expected"),
    [
        ("NG", NAIRA),  # Nigeria: naira through Paystack
        ("ng", NAIRA),  # upper or lower case, and spaces around it, are the same country
        (" Ng ", NAIRA),
        ("US", DOLLARS),  # any other country: US dollars through Flutterwave
        ("GB", DOLLARS),
        ("GH", DOLLARS),  # Paystack's pages list these four too, but our account is Nigerian,
        ("KE", DOLLARS),  # so they go to Flutterwave in dollars (spec 5.2)
        ("ZA", DOLLARS),
        ("CI", DOLLARS),
        ("FR", DOLLARS),
        ("JP", DOLLARS),
    ],
)
def test_every_row_of_the_routing_table(country, expected):
    assert provider_for(country) == expected


@pytest.mark.parametrize("empty", [None, "", "   ", "\t\n"])
def test_nothing_chosen_is_an_explicit_error_never_a_default(empty):
    with pytest.raises(CountryMissing) as raised:
        provider_for(empty)
    assert raised.value.code == "billing_country_missing"


@pytest.mark.parametrize(
    "bad",
    ["Nigeria", "NGA", "N", "UK", "XK", "ZZ", "AC", "12", "N G", "NG,US", 5, 1.5, ["NG"], b"NG"],
)
def test_not_a_country_is_an_explicit_error_never_corrected(bad):
    with pytest.raises(CountryInvalid) as raised:
        provider_for(bad)
    assert raised.value.code == "billing_country_invalid"


def test_a_provider_is_switched_off_with_off_and_its_countries_are_refused():
    paystack_off = "NG=off,*=USD:flutterwave"  # Nigeria cannot pay; everyone else still can
    with pytest.raises(CountryUnsupported) as raised:
        provider_for("NG", paystack_off)
    assert raised.value.code == "billing_country_unsupported"
    assert provider_for("US", paystack_off) == DOLLARS

    flutterwave_off = "NG=NGN:paystack,*=off"  # only Nigeria can pay
    assert provider_for("NG", flutterwave_off) == NAIRA
    for country in ("US", "GB", "GH"):
        with pytest.raises(CountryUnsupported):
            provider_for(country, flutterwave_off)


def test_a_country_with_an_entry_of_its_own_never_falls_through_to_the_wildcard():
    """The failure this guards: Paystack goes down, and Nigerians quietly get a dollar checkout."""
    for table in ("NG=off,*=USD:flutterwave", "*=USD:flutterwave,NG=off"):
        with pytest.raises(CountryUnsupported):
            provider_for("NG", table)


def test_deleting_the_nigeria_entry_is_not_how_paystack_is_switched_off():
    """Removing `NG` leaves Nigeria with no entry of its own, so it takes the `*` route: dollars
    through Flutterwave. That is what the docs say, and why they say to write `NG=off`."""
    assert provider_for("NG", "*=USD:flutterwave") == DOLLARS


def test_with_no_wildcard_a_country_without_an_entry_is_refused():
    only_nigeria = "NG=NGN:paystack"
    assert provider_for("NG", only_nigeria) == NAIRA
    with pytest.raises(CountryUnsupported):
        provider_for("US", only_nigeria)


def test_off_is_read_in_any_case_and_for_a_listed_country():
    assert routing.parse_routes("ng = OFF , * = off") == {"NG": None, "*": None}


def test_a_named_country_beats_the_wildcard_wherever_it_is_written():
    assert provider_for("GH", "*=USD:flutterwave,GH=NGN:paystack") == Route("NGN", "paystack")
    assert provider_for("US", "*=USD:flutterwave,GH=NGN:paystack") == DOLLARS


def test_an_unknown_country_is_refused_even_when_a_wildcard_exists():
    with pytest.raises(CountryInvalid):
        provider_for("ZZ", "*=USD:flutterwave")


def test_the_default_is_the_spec_table():
    assert routing.parse_routes(routing.DEFAULT_ROUTES) == {"NG": NAIRA, "*": DOLLARS}
    assert make_settings().billing_country_routes == routing.DEFAULT_ROUTES


@pytest.mark.parametrize(
    "text",
    [
        "",
        " , ",
        "NG",
        "NG=paystack",  # no currency
        "NG=NGN:",
        "NG=EUR:paystack",  # a currency we do not take
        "ZZ=USD:flutterwave",  # not a country
        "Nigeria=NGN:paystack",
        "NG=NGN:Paystack",  # provider names are lower case
        "NG=NGN:paystak",  # a typo must stop startup, not fail at checkout
        "*=USD:flutterwave_test",  # not a provider we have
        "NG=NGN:fake",  # the fake provider is for tests only
        "NG=USD:paystack",  # Paystack is our naira provider
        "*=NGN:flutterwave",  # Flutterwave is our dollar provider
        "NG=of",
        "NG=off:paystack",
        "NG=NGN:paystack,NG=USD:flutterwave",  # listed twice
        "*=USD:a,*=USD:b",
    ],
)
def test_a_bad_table_is_refused(text):
    with pytest.raises(ValueError):
        routing.parse_routes(text)


@pytest.mark.parametrize(
    "text", ["NG=NGN", "NG=NGN:paystak", "NG=USD:paystack", "*=USD:flutterwave_test", "NG=nope"]
)
def test_a_bad_setting_stops_the_process_naming_the_setting(text):
    with pytest.raises(ValidationError) as raised:
        make_settings(billing_country_routes=text)
    assert "BILLING_COUNTRY_ROUTES" in str(raised.value)


def test_the_switched_off_forms_are_accepted_as_settings():
    for text in ("NG=off,*=USD:flutterwave", "NG=NGN:paystack,*=off"):
        assert make_settings(billing_country_routes=text).billing_country_routes == text


def test_a_blank_setting_means_the_default():
    assert make_settings(billing_country_routes="  ").billing_country_routes == (
        routing.DEFAULT_ROUTES
    )
    assert make_settings(billing_country_routes="NG=NGN:paystack").billing_country_routes == (
        "NG=NGN:paystack"
    )
