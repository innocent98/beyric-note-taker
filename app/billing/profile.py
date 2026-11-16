"""The billing profile and the rules for changing the billing country (billing spec section 5.2).

A person has one profile: who they are billed as, and the country they are billed from. The country
decides the currency and the provider (`app/billing/routing.py`). It is chosen by the person (never
guessed) and only they change their own: nothing here takes a user other than the one passed in, and
the routes pass the signed-in user.

**Changing the country** (`change_country`), with a table test of every row in `tests/`:

- the same country as now: `unchanged`, nothing is written;
- a change already waiting for the end of a period: refused, `country_change_pending` (409);
- the first choice: `applied` at once, and not counted as a change for the 30-day rule;
- another change within the last 30 days: refused, `country_change_too_soon` (409);
- no live subscription: `applied` at once;
- a live subscription, same currency and provider: `applied` at once, nothing else moves;
- a live subscription, crossing Nigeria and elsewhere (a different route): `scheduled` for the end
  of the paid period. The profile keeps the old country until then and
  `pending_billing_country` holds the new one.

Every applied or scheduled change writes one `billing_country_changes` row. A scheduled change takes
effect through `apply_due_pending` (called when the profile is read or changed, and by the renewal
of stage S8); it writes no second row, the first already carries `effective_at`.

**The live subscription is one question** (`live_subscription_period_end`): when does the paid
period of this person's live subscription end, or None if they have none. The subscriptions table
arrives with stage S8, so it answers None for everyone today and S8 fills it in. Tests replace it
with a stub for the three branches.
"""

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Literal

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.billing import routing
from app.billing.types import BillingError
from app.models import BillingCountryChange, BillingProfile, User

# At most one change in this many days. The first choice is not a change.
CHANGE_WINDOW = timedelta(days=30)

ChangeResult = Literal["applied", "scheduled", "unchanged"]


class CountryChangeRefused(BillingError):
    """A change the rules do not allow. `status`, `code` and `message` are what the API says."""

    def __init__(self, status: int, code: str, message: str) -> None:
        super().__init__(message)
        self.status, self.code, self.message = status, code, message


@dataclass(frozen=True)
class CountryChange:
    result: ChangeResult
    profile: BillingProfile


def live_subscription_period_end(db: Session, user_id: int, now: datetime) -> datetime | None:
    """When the paid period of the person's live subscription ends, or None when they have no live
    subscription. **Stage S8 fills this in** (subscriptions do not exist yet); until then nobody has
    one, so every change applies at once."""
    return None


def get_profile(db: Session, user: User) -> BillingProfile | None:
    return db.scalar(select(BillingProfile).where(BillingProfile.user_id == user.id))


def _locked_profile(db: Session, user: User) -> BillingProfile:
    """The person's profile row, made if it does not exist yet, and locked for this transaction so
    two changes at the same moment cannot both pass the 30-day rule."""
    db.execute(
        insert(BillingProfile)
        .values(user_id=user.id)
        .on_conflict_do_nothing(index_elements=["user_id"])
    )
    return db.scalars(
        select(BillingProfile)
        .where(BillingProfile.user_id == user.id)
        .with_for_update()
        .execution_options(populate_existing=True)
    ).one()


def apply_due_pending(profile: BillingProfile, now: datetime) -> bool:
    """Make a scheduled country change take effect once its time has come. True if it did."""
    if profile.pending_billing_country is None or profile.pending_effective_at is None:
        return False
    if profile.pending_effective_at > now:
        return False
    profile.billing_country = profile.pending_billing_country
    profile.pending_billing_country = None
    profile.pending_effective_at = None
    profile.updated_at = now
    return True


def country_change_allowed_at(db: Session, user_id: int, now: datetime) -> datetime | None:
    """When the next change is allowed, or None when one is allowed now. Only real changes count
    (a row with an `old_country`); the first choice does not."""
    last = db.scalar(
        select(func.max(BillingCountryChange.created_at)).where(
            BillingCountryChange.user_id == user_id,
            BillingCountryChange.old_country.is_not(None),
        )
    )
    if last is None:
        return None
    allowed = last + CHANGE_WINDOW
    return allowed if allowed > now else None


def save_details(
    db: Session,
    user: User,
    *,
    name_or_company: str | None,
    address: str | None,
    tax_id: str | None,
    billing_email: str | None,
    now: datetime | None = None,
) -> BillingProfile:
    """Replace the four details on the person's profile (a null clears one). The country is not
    one of them: it changes only through `change_country`. Flushed, not committed."""
    now = now or datetime.now(UTC)
    profile = _locked_profile(db, user)
    apply_due_pending(profile, now)
    profile.name_or_company = name_or_company
    profile.address = address
    profile.tax_id = tax_id
    profile.billing_email = billing_email
    profile.updated_at = now
    db.flush()
    return profile


def change_country(
    db: Session,
    user: User,
    raw_country: object,
    *,
    routes_text: str = routing.DEFAULT_ROUTES,
    now: datetime | None = None,
) -> CountryChange:
    """Set or change the person's billing country under the rules in the module note. Raises a
    `routing.RoutingError` for a country that is missing, not a country, or not routable, and
    `CountryChangeRefused` for a change the rules forbid. Flushed, not committed: the caller
    commits, and a refusal leaves nothing written."""
    now = now or datetime.now(UTC)
    country = routing.clean_country(raw_country)
    new_route = routing.provider_for(country, routes_text)
    profile = _locked_profile(db, user)
    apply_due_pending(profile, now)

    if profile.pending_billing_country is not None:
        raise CountryChangeRefused(
            409,
            "country_change_pending",
            "A change of billing country is already waiting for the end of your paid period.",
        )
    old = profile.billing_country
    if country == old:
        return CountryChange("unchanged", profile)

    scheduled_for = None
    if old is not None:
        allowed_at = country_change_allowed_at(db, user.id, now)
        if allowed_at is not None:
            raise CountryChangeRefused(
                409,
                "country_change_too_soon",
                "You can change your billing country once every 30 days.",
            )
        period_end = live_subscription_period_end(db, user.id, now)
        if (
            period_end is not None
            and period_end > now
            and _route_or_none(old, routes_text) != new_route
        ):
            scheduled_for = period_end

    db.add(
        BillingCountryChange(
            user_id=user.id,
            old_country=old,
            new_country=country,
            effective_at=scheduled_for or now,
            created_at=now,
        )
    )
    if scheduled_for is None:
        profile.billing_country = country
        result: ChangeResult = "applied"
    else:
        profile.pending_billing_country = country
        profile.pending_effective_at = scheduled_for
        result = "scheduled"
    profile.updated_at = now
    db.flush()
    return CountryChange(result, profile)


def _route_or_none(country: str, routes_text: str) -> routing.Route | None:
    """The route of a country already on the profile, or None if the table no longer has one (a
    provider was switched off since): such a change counts as crossing, so it waits."""
    try:
        return routing.provider_for(country, routes_text)
    except routing.RoutingError:
        return None
