"""The person's own billing profile and billing country (billing spec section 5.2, stage S5).

- `GET /api/billing/profile`: the caller's profile (every field null until they save one) and
  what their country pays with.
- `PUT /api/billing/profile`: replace the four details (name or company, address, tax id, email).
- `PUT /api/billing/profile/country`: set or change the billing country under the change rules.
- `GET /api/billing/route?country=NG`: what a country would pay with. The screen states it before
  any payment ("Prices in naira, paid through Paystack").

Dark: every route answers a plain 404 (`not_found`) while `BILLING_ENABLED` is false, and for any
account that is not on `BILLING_ALLOWED_EMAILS`. **The list is closed by default**: empty means no
account may use the routes, and `*` in the list means every account (spec section 8: S5 is on "for
allow-listed accounts only"). The check is the first thing a route does, so nothing is read or
written. They sit under `/api/`, so `auth.guard` already
gives them the login and CSRF rules of every other person route.

Only the caller's own profile is ever read or written: the account is the signed-in user, never an
id from the path or the body, and there is no route that takes another person's id.

Errors, all in the API's one shape (`{"error": {"code", "message"}}`):

- 404 `not_found`: billing is off, or the account is not on the allow-list.
- 422 `billing_country_missing`, `billing_country_invalid`, `billing_country_unsupported`: the
  country is empty, is not an ISO code, or has no route.
- 422 `billing_email_invalid`: the billing email is not a plain address.
- 422 `validation_error`: a field is too long, of the wrong type, or has control characters.
- 409 `country_change_too_soon`, `country_change_pending`: the change rules.
"""

import unicodedata
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field, field_validator
from sqlalchemy.orm import Session

from app import auth, services, signup
from app.api_schemas import Body, ErrorResponse, UtcDatetime
from app.billing import profile as profiles
from app.billing import routing
from app.db import get_session
from app.models import BillingProfile, User

router = APIRouter(prefix="/api/billing", tags=["billing"])

_NOT_FOUND = {404: {"model": ErrorResponse, "description": "billing is off for this account"}}


def billing_user(request: Request, user: User = Depends(auth.current_user)) -> User:
    """The signed-in user, once billing is on for them; a 404 before anything is read otherwise."""
    settings = request.app.state.settings
    if not settings.billing_enabled:
        raise HTTPException(404, "Not found.")
    allowed = {
        part.strip().lower() for part in settings.billing_allowed_emails.split(",") if part.strip()
    }
    if "*" not in allowed and user.email.lower() not in allowed:
        raise HTTPException(404, "Not found.")
    return user


class RouteOut(BaseModel):
    # What a person in this country pays with: `NGN` or `USD`, and the provider's name.
    currency: str
    provider: str


class RoutePreviewOut(RouteOut):
    billing_country: str


class ProfileOut(BaseModel):
    # All four are null until the person saves them.
    name_or_company: str | None
    address: str | None
    tax_id: str | None
    billing_email: str | None
    # The two-letter country they chose; null until they do. It is never guessed.
    billing_country: str | None
    # A change that waits for the end of the paid period, and when it takes effect; both null
    # when nothing is waiting. Until then `billing_country` and `route` are the old ones.
    pending_billing_country: str | None
    pending_effective_at: UtcDatetime | None
    # What `billing_country` pays with; null when no country is chosen or none can be routed.
    route: RouteOut | None
    # When the next country change is allowed (one every 30 days); null when one is allowed now.
    country_change_allowed_at: UtcDatetime | None


class CountryChangeOut(BaseModel):
    # `applied` (it took effect now), `scheduled` (it waits for the end of the paid period) or
    # `unchanged` (the same country as now; nothing was written).
    result: str
    profile: ProfileOut


class ProfileIn(Body):
    """Replaces all four details. A field that is null, empty or only spaces clears it."""

    name_or_company: str | None = Field(default=None, max_length=200)
    address: str | None = Field(default=None, max_length=500)
    tax_id: str | None = Field(default=None, max_length=64)
    billing_email: str | None = Field(default=None, max_length=254)

    @field_validator("name_or_company", "tax_id", "billing_email")
    @classmethod
    def _one_line(cls, value: str | None) -> str | None:
        return _clean(value, multiline=False)

    @field_validator("address")
    @classmethod
    def _lines(cls, value: str | None) -> str | None:
        return _clean(value, multiline=True)


class CountryIn(Body):
    # A two-letter ISO 3166-1 country code such as `NG` or `GB` (upper or lower case).
    billing_country: str


def _clean(value: str | None, *, multiline: bool) -> str | None:
    """Trimmed text, or None when nothing is left. No control or invisible formatting characters
    (a NUL cannot be stored; a right-to-left override or a zero-width space would reach invoices);
    an address may have line breaks."""
    if value is None:
        return None
    value = value.strip()
    allowed = {"\n"} if multiline else set()
    if any(unicodedata.category(ch) in ("Cc", "Cf") and ch not in allowed for ch in value):
        raise ValueError("must not contain control characters")
    return value or None


def _route_of(country: str | None, routes_text: str) -> RouteOut | None:
    if country is None:
        return None
    try:
        found = routing.provider_for(country, routes_text)
    except routing.RoutingError:
        return None
    return RouteOut(currency=found.currency, provider=found.provider)


def _profile_out(
    db: Session, user: User, row: BillingProfile | None, routes_text: str, now: datetime
) -> ProfileOut:
    """The profile as it applies at `now`: a scheduled change whose time has come is shown as
    applied (`profiles.country_view`) without this read writing anything."""
    if row is None:
        return ProfileOut(
            name_or_company=None,
            address=None,
            tax_id=None,
            billing_email=None,
            billing_country=None,
            pending_billing_country=None,
            pending_effective_at=None,
            route=None,
            country_change_allowed_at=profiles.country_change_allowed_at(db, user.id, now),
        )
    view = profiles.country_view(row, now)
    return ProfileOut(
        name_or_company=row.name_or_company,
        address=row.address,
        tax_id=row.tax_id,
        billing_email=row.billing_email,
        billing_country=view.billing_country,
        pending_billing_country=view.pending_billing_country,
        pending_effective_at=view.pending_effective_at,
        route=_route_of(view.billing_country, routes_text),
        country_change_allowed_at=profiles.country_change_allowed_at(db, user.id, now),
    )


@router.get("/profile", responses=_NOT_FOUND)
def read_profile(
    request: Request,
    db: Session = Depends(get_session),
    user: User = Depends(billing_user),
) -> ProfileOut:
    """The caller's billing profile. A read writes nothing: it never creates a row (a person who
    has saved nothing gets every field null) and never commits; a scheduled change whose time has
    come is shown as applied and is written by the next change or by the renewal."""
    now = datetime.now(UTC)
    row = profiles.get_profile(db, user)
    routes_text = request.app.state.settings.billing_country_routes
    return _profile_out(db, user, row, routes_text, now)


@router.put(
    "/profile",
    responses={
        **_NOT_FOUND,
        422: {"model": ErrorResponse, "description": "`billing_email_invalid`, `validation_error`"},
    },
)
def write_profile(
    request: Request,
    body: ProfileIn,
    db: Session = Depends(get_session),
    user: User = Depends(billing_user),
) -> ProfileOut:
    """Replace the caller's name or company, address, tax id and billing email. The country is not
    set here: it changes only through `PUT /api/billing/profile/country`."""
    email = None
    if body.billing_email is not None:
        email = signup.normalise_email(body.billing_email)
        if email is None:
            raise services.Refused(
                422, "billing_email_invalid", "That is not an email address we can send to."
            )
    now = datetime.now(UTC)
    row = profiles.save_details(
        db,
        user,
        name_or_company=body.name_or_company,
        address=body.address,
        tax_id=body.tax_id,
        billing_email=email,
        now=now,
    )
    db.commit()
    routes_text = request.app.state.settings.billing_country_routes
    return _profile_out(db, user, row, routes_text, now)


@router.put(
    "/profile/country",
    responses={
        **_NOT_FOUND,
        409: {
            "model": ErrorResponse,
            "description": "`country_change_too_soon`, `country_change_pending`",
        },
        422: {
            "model": ErrorResponse,
            "description": "`billing_country_missing`, `billing_country_invalid`, "
            "`billing_country_unsupported`",
        },
    },
)
def write_country(
    request: Request,
    body: CountryIn,
    db: Session = Depends(get_session),
    user: User = Depends(billing_user),
) -> CountryChangeOut:
    """Set the caller's billing country (the first choice) or change it. The currency and the
    provider follow from it; a change that crosses between Nigeria and elsewhere while a paid
    subscription is live waits for the end of the period (`result` is `scheduled`)."""
    routes_text = request.app.state.settings.billing_country_routes
    now = datetime.now(UTC)
    try:
        change = profiles.change_country(
            db, user, body.billing_country, routes_text=routes_text, now=now
        )
    except routing.RoutingError as exc:
        db.rollback()
        raise services.Refused(422, exc.code, exc.message) from None
    except profiles.CountryChangeRefused as exc:
        db.rollback()  # a refusal leaves nothing written
        raise services.Refused(exc.status, exc.code, exc.message) from None
    db.commit()
    return CountryChangeOut(
        result=change.result, profile=_profile_out(db, user, change.profile, routes_text, now)
    )


@router.get(
    "/route",
    responses={
        **_NOT_FOUND,
        422: {
            "model": ErrorResponse,
            "description": "`billing_country_missing`, `billing_country_invalid`, "
            "`billing_country_unsupported`",
        },
    },
)
def preview_route(
    request: Request,
    country: str = Query(default=""),
    user: User = Depends(billing_user),
) -> RoutePreviewOut:
    """What a country would pay with, so the screen can say "Prices in naira, paid through
    Paystack" before the person commits to it. Reads the route table only; nothing is stored."""
    routes_text = request.app.state.settings.billing_country_routes
    try:
        code = routing.clean_country(country)
        found = routing.provider_for(code, routes_text)
    except routing.RoutingError as exc:
        raise services.Refused(422, exc.code, exc.message) from None
    return RoutePreviewOut(billing_country=code, currency=found.currency, provider=found.provider)
