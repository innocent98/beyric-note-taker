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


def test_the_table_is_data_so_one_provider_can_be_switched_off():
    only_nigeria = "NG=NGN:paystack"  # Flutterwave switched off: nobody else can pay
    assert provider_for("NG", only_nigeria) == NAIRA
    with pytest.raises(CountryUnsupported) as raised:
        provider_for("US", only_nigeria)
    assert raised.value.code == "billing_country_unsupported"

    only_dollars = "*=USD:flutterwave"  # Paystack switched off: Nigeria pays dollars like the rest
    assert (
        provider_for("NG", only_dollars) == DOLLARS and provider_for("US", only_dollars) == DOLLARS
    )


def test_a_named_country_beats_the_wildcard_wherever_it_is_written():
    assert provider_for("GH", "*=USD:flutterwave,GH=USD:paystack") == Route("USD", "paystack")
    assert provider_for("US", "*=USD:flutterwave,GH=USD:paystack") == DOLLARS


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
        "NG=NGN:a",
        "NG=NGN:" + "p" * 17,  # the payments column holds 16
        "NG=NGN:paystack,NG=USD:flutterwave",  # listed twice
        "*=USD:a,*=USD:b",
    ],
)
def test_a_bad_table_is_refused(text):
    with pytest.raises(ValueError):
        routing.parse_routes(text)


def test_a_bad_setting_stops_the_process_naming_the_setting():
    with pytest.raises(ValidationError) as raised:
        make_settings(billing_country_routes="NG=NGN")
    assert "BILLING_COUNTRY_ROUTES" in str(raised.value)


def test_a_blank_setting_means_the_default():
    assert make_settings(billing_country_routes="  ").billing_country_routes == (
        routing.DEFAULT_ROUTES
    )
    assert make_settings(billing_country_routes="NG=NGN:paystack").billing_country_routes == (
        "NG=NGN:paystack"
    )
