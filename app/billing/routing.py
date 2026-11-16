"""Which currency and which provider a billing country pays with (billing spec section 5.2).

**The key is the billing country the person chose and saved on their billing profile.** It is never
guessed from an address, a VPN, a browser language or a card: a missing or unknown country is an
explicit error, and the caller decides what to tell the person.

The table is data, the setting `BILLING_COUNTRY_ROUTES`: comma-separated `COUNTRY=CURRENCY:provider`
entries, where `COUNTRY` is a two-letter ISO code or `*` for every other country.

    NG=NGN:paystack,*=USD:flutterwave          (the default: Nigeria pays naira through Paystack;
                                                any other country pays dollars through Flutterwave)

To switch one provider off, remove its entry: a country with no route and no `*` entry is refused
with `CountryUnsupported`, never sent to the other provider by default. A bad value stops the
process at startup, naming the setting (`app/config.py`).
"""

import re
from dataclasses import dataclass
from functools import lru_cache

from app.billing.countries import ISO_COUNTRIES
from app.billing.types import BillingError
from app.models import BILLING_CURRENCIES

DEFAULT_ROUTES = "NG=NGN:paystack,*=USD:flutterwave"
ANY_OTHER = "*"
_PROVIDER = re.compile(r"[a-z][a-z0-9_]{1,15}")  # the `payments.provider` column holds 16


@dataclass(frozen=True)
class Route:
    """What a person in a billing country pays with."""

    currency: str
    provider: str


class RoutingError(BillingError):
    """The billing country cannot be routed. `code` is the word the API sends."""

    code = "billing_country_invalid"

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


class CountryMissing(RoutingError):
    """No billing country is set. Nothing is guessed in its place."""

    code = "billing_country_missing"


class CountryInvalid(RoutingError):
    """The value is not an ISO 3166-1 alpha-2 country code."""

    code = "billing_country_invalid"


class CountryUnsupported(RoutingError):
    """A real country that the table has no route for (a provider was switched off)."""

    code = "billing_country_unsupported"


def clean_country(raw: object) -> str:
    """The two-letter code in `raw`, upper-cased. Raises `CountryMissing` for nothing at all and
    `CountryInvalid` for anything that is not an assigned ISO code (a name, `UK`, `XK`, `ZZ`)."""
    if raw is None or (isinstance(raw, str) and not raw.strip()):
        raise CountryMissing("Choose the country you are billed from.")
    code = raw.strip().upper() if isinstance(raw, str) else ""
    if code not in ISO_COUNTRIES:
        raise CountryInvalid("That is not a country code we know. Pick one from the list.")
    return code


@lru_cache(maxsize=16)
def parse_routes(text: str) -> dict[str, Route]:
    """The route table in `text`, or `ValueError` naming the first bad entry."""
    routes: dict[str, Route] = {}
    for entry in (part.strip() for part in text.split(",")):
        if not entry:
            continue
        country, equals, rest = entry.partition("=")
        currency, colon, provider = rest.partition(":")
        country, currency, provider = country.strip().upper(), currency.strip(), provider.strip()
        if not (equals and colon):
            raise ValueError(f"{entry!r} must look like COUNTRY=CURRENCY:provider")
        if country != ANY_OTHER and country not in ISO_COUNTRIES:
            raise ValueError(f"{country!r} is not a country code (or *)")
        if currency not in BILLING_CURRENCIES:
            raise ValueError(
                f"{currency!r} is not a currency we take: {', '.join(BILLING_CURRENCIES)}"
            )
        if not _PROVIDER.fullmatch(provider):
            raise ValueError(f"{provider!r} is not a provider name (lower-case letters)")
        if country in routes:
            raise ValueError(f"{country!r} is listed twice")
        routes[country] = Route(currency, provider)
    if not routes:
        raise ValueError("it lists no routes")
    return routes


def provider_for(billing_country: object, routes_text: str = DEFAULT_ROUTES) -> Route:
    """The currency and provider for `billing_country`.

    Raises `CountryMissing` (nothing chosen), `CountryInvalid` (not a country code) or
    `CountryUnsupported` (a country the table has no route for). Never guesses."""
    country = clean_country(billing_country)
    routes = parse_routes(routes_text)
    route = routes.get(country) or routes.get(ANY_OTHER)
    if route is None:
        raise CountryUnsupported("We cannot take payments from that country yet.")
    return route
