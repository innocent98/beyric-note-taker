"""The billing profile and the rules for changing the billing country (billing spec section 5.2).

A person has one profile: who they are billed as, and the country they are billed from. The country
decides the currency and the provider (`app/billing/routing.py`). It is chosen by the person (never
guessed) and only they change their own: nothing here takes a user other than the one passed in, and
the routes pass the signed-in user. The screen may SUGGEST one (`suggest_country`, from the saved
time zone) but a suggestion is never saved: only `change_country`, which the person asks for,
writes it.

**Changing the country** (`change_country`), with a table test of every row in `tests/`:

- the same country as now: `unchanged`, nothing is written;
- a change already waiting for the end of a period: refused, `country_change_pending` (409);
- **before the person has ever paid** (no successful payment, no live subscription: `country_lock`
  is None): `applied` at once, instantly, any number of times. Nothing is at stake yet, and a
  wrong first pick (a country chosen from a list by mistake) must not trap them. Each change still
  writes its `billing_country_changes` row;
- **from the first successful payment or a live subscription on**, the rules below apply, and
  only changes made since then count towards the 30 days (the changes made while it was free do
  not):
- the first choice: `applied` at once, and not counted as a change for the 30-day rule;
- another change within the last 30 days: refused, `country_change_too_soon` (409);
- no live subscription: `applied` at once;
- a live subscription, same currency and provider: `applied` at once, nothing else moves;
- a live subscription, crossing Nigeria and elsewhere (a different route): `scheduled` for the end
  of the paid period. The profile keeps the old country until then and
  `pending_billing_country` holds the new one.

**An open plan checkout is closed by any change** (`supersede_open_plan_checkouts`): it is priced in
the currency of the country it was opened under, so it must not stay open beside another country
(two currencies open at once, or a plan that stays on naira while the profile says dollars). If the
old page is paid anyway, `_plan_paid` in `subscriptions.py` makes no plan and tells the admins to
refund it. Packs and upgrades are paid on the route the subscription was sold on, so they are
not touched.
**Dates in the refusals** are written in the person's saved time zone (UTC when none), the one the
card shows.

Every applied or scheduled change writes one `billing_country_changes` row. A scheduled change takes
effect through `apply_due_pending` (called when the profile is read or changed, and by the renewal
of stage S8); it writes no second row, the first already carries `effective_at`.

**The live subscription is one question** (`live_subscription`): the person's live subscription as
`LiveSubscription(period_end, currency, provider)`, or None if they have none. The route it was
SOLD on is what a change is compared with, not today's route table: an operator editing the table
mid-period must not make a Nigerian naira subscription look like a dollar one. Stage S8 made
it real: it reads the subscriptions table (the stub and its tripwire are gone). Tests may still
replace it with a stub for a particular branch.
"""

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Literal
from zoneinfo import ZoneInfo

from sqlalchemy import func, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.billing import cutover, routing
from app.billing.types import BillingError
from app.billing.zone_countries import country_for_zone
from app.config import Settings
from app.models import (
    LIVE_SUBSCRIPTION_STATUSES,
    BillingCountryChange,
    BillingProfile,
    Payment,
    Subscription,
    User,
    UserSettings,
)

# At most one change in this many days. The first choice is not a change.
CHANGE_WINDOW = timedelta(days=30)

ChangeResult = Literal["applied", "scheduled", "unchanged"]


@dataclass(frozen=True)
class LiveSubscription:
    """What a change needs to know about a live paid subscription: when its paid period ends and the
    currency and provider it was sold on (stored on the subscription by stage S8)."""

    period_end: datetime
    currency: str
    provider: str

    @property
    def route(self) -> routing.Route:
        return routing.Route(self.currency, self.provider)


@dataclass(frozen=True)
class CountryLock:
    """The person has paid (or has a live plan), so the change rules apply. `since` is when counting
    changes towards the 30 days starts: the first successful payment. None when the lock comes from
    a live subscription with no payment on record (every change counts)."""

    since: datetime | None


@dataclass(frozen=True)
class CountryView:
    """The country that applies at one moment: a scheduled change whose time has come counts as
    applied, whether or not anything has written it to the row yet."""

    billing_country: str | None
    pending_billing_country: str | None
    pending_effective_at: datetime | None


class CountryChangeRefused(BillingError):
    """A change the rules do not allow. `status`, `code` and `message` are what the API says."""

    def __init__(self, status: int, code: str, message: str) -> None:
        super().__init__(message)
        self.status, self.code, self.message = status, code, message


@dataclass(frozen=True)
class CountryChange:
    result: ChangeResult
    profile: BillingProfile


def has_subscription_table(table_names) -> bool:
    """True if any table is about subscriptions, whatever it is called: the tripwire below must not
    depend on one spelling. Kept as the test of record that the table exists (stage S8 made it)."""
    return any("subscription" in name for name in table_names)


def live_subscription(db: Session, user_id: int, now: datetime) -> LiveSubscription | None:
    """The person's live subscription (`active` or `past_due`), or None. `period_end` is when the
    paid period ends (during the grace it is already past, so a change applies at once: nothing
    paid is left to protect), and `currency` and `provider` are the route it was SOLD on, stored on
    the row, so a change in the country table mid-period cannot turn a naira subscription into a
    dollar one. This replaced the stage S5 stub (and its tripwire) in stage S8."""
    row = db.execute(
        select(Subscription.current_period_end, Subscription.currency, Subscription.provider).where(
            Subscription.user_id == user_id,
            Subscription.status.in_(LIVE_SUBSCRIPTION_STATUSES),
        )
    ).first()
    if row is None:
        return None
    return LiveSubscription(period_end=row[0], currency=row[1], provider=row[2])


# A payment that took the person's money at least once (a refund later does not undo that).
_PAID_STATUSES = ("succeeded", "refunded", "partly_refunded")


def country_lock(
    db: Session, user_id: int, now: datetime, settings: Settings | None
) -> CountryLock | None:
    """None while the person has never paid and has no live subscription: the country is then
    free to change, instantly and without limit. Otherwise a `CountryLock` and the change rules
    apply. Once a person has paid it stays locked even after the plan ends. A payment made before
    the go-live cutover (`BILLING_LIVE_SINCE`, passed as `settings`) took no money and does not
    lock. `settings` is REQUIRED on purpose (a caller that left it out would silently get no
    cutover): pass `None` only where there is no cutover to apply (tests, dev scripts)."""
    first_paid = db.scalar(
        select(func.min(func.coalesce(Payment.paid_at, Payment.created_at))).where(
            Payment.payer_user_id == user_id,
            Payment.status.in_(_PAID_STATUSES),
            cutover.live_only(Payment.created_at, settings),
        )
    )
    if first_paid is not None:
        return CountryLock(first_paid)
    if live_subscription(db, user_id, now) is not None:
        return CountryLock(None)
    return None


def suggest_country(db: Session, user: User, routes_text: str) -> str | None:
    """The country the saved time zone points to (`Africa/Lagos` gives `NG`), or None when the
    person has no zone, the zone has no country or the country cannot be routed. Only a suggestion
    for the screen to confirm: it is never saved here."""
    zone = db.scalar(select(UserSettings.time_zone).where(UserSettings.user_id == user.id))
    country = country_for_zone(zone)
    if country is None:
        return None
    try:
        routing.provider_for(country, routes_text)
    except routing.RoutingError:
        return None
    return country


def country_view(row: BillingProfile | None, at: datetime) -> CountryView:
    """The profile's countries as they apply at `at` (without changing the row)."""
    if row is None:
        return CountryView(None, None, None)
    pending, due = row.pending_billing_country, row.pending_effective_at
    if pending is not None and due is not None and due <= at:
        return CountryView(pending, None, None)
    return CountryView(row.billing_country, pending, due)


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


def settle_due_country(db: Session, user: User, now: datetime) -> CountryView:
    """Write a scheduled country change whose time has come (locking the profile row), and return
    the countries as they apply now. For the renewal and the pay link: the route they use must be
    the one the person is on, not the one before a change that has already taken effect. Flushed,
    not committed."""
    if get_profile(db, user) is None:
        return CountryView(None, None, None)
    profile = _locked_profile(db, user)
    apply_due_pending(profile, now)
    db.flush()
    return country_view(profile, now)


def apply_due_pending(profile: BillingProfile, now: datetime) -> bool:
    """Make a scheduled country change take effect once its time has come, in the row. True if it
    did. Call it with the row locked (`_locked_profile`); a read only uses `country_view`."""
    pending, due = profile.pending_billing_country, profile.pending_effective_at
    if pending is None or due is None or due > now:
        return False
    profile.billing_country = pending
    profile.pending_billing_country = None
    profile.pending_effective_at = None
    profile.updated_at = now
    return True


def country_change_allowed_at(
    db: Session, user_id: int, now: datetime, settings: Settings | None
) -> datetime | None:
    """When the next change is allowed, or None when one is allowed now: always before the person
    has paid. Only real changes made since the lock began count (a row with an `old_country`); the
    first choice does not, and neither do the changes made while the country was still free."""
    lock = country_lock(db, user_id, now, settings)
    if lock is None:
        return None
    conditions = [
        BillingCountryChange.user_id == user_id,
        BillingCountryChange.old_country.is_not(None),
    ]
    if lock.since is not None:
        conditions.append(BillingCountryChange.created_at >= lock.since)
    last = db.scalar(select(func.max(BillingCountryChange.created_at)).where(*conditions))
    if last is None:
        return None
    allowed = last + CHANGE_WINDOW
    return allowed if allowed > now else None


def _zone_of(db: Session, user_id: int) -> ZoneInfo | None:
    """The person's saved time zone, or None (UTC): the zone the card shows dates in."""
    name = db.scalar(select(UserSettings.time_zone).where(UserSettings.user_id == user_id))
    if not name:
        return None
    try:
        return ZoneInfo(name)
    except Exception:
        return None


def _day(moment: datetime, zone: ZoneInfo | None) -> str:
    """`17 December 2026` in the person's time zone (UTC when none is saved)."""
    local = moment.astimezone(zone or UTC)
    return f"{local.day} {local:%B %Y}"


def supersede_open_plan_checkouts(db: Session, user_id: int) -> int:
    """Close the person's unpaid PLAN checkouts (status `failed`: nothing was bought). Called when a
    change of country takes effect. Packs and upgrades are not touched: they are paid on the route
    the subscription was sold on, whatever the country says. Flushed, not committed."""
    result = db.execute(
        update(Payment)
        .where(
            Payment.payer_user_id == user_id,
            Payment.kind == "plan",
            Payment.status == "pending",
        )
        .values(status="failed")
    )
    return result.rowcount or 0


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
    settings: Settings | None,
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
    zone = _zone_of(db, user.id)

    if profile.pending_billing_country is not None:
        raise CountryChangeRefused(
            409,
            "country_change_pending",
            "A change of billing country is already waiting for the end of your paid period"
            + (
                f" ({_day(profile.pending_effective_at, zone)})."
                if profile.pending_effective_at
                else "."
            )
            + " It cannot be changed again until then.",
        )
    old = profile.billing_country
    if country == old:
        return CountryChange("unchanged", profile)

    scheduled_for = None
    # Before the first payment nothing is locked: any change applies at once (see the module note).
    if old is not None and country_lock(db, user.id, now, settings) is not None:
        allowed_at = country_change_allowed_at(db, user.id, now, settings)
        if allowed_at is not None:
            raise CountryChangeRefused(
                409,
                "country_change_too_soon",
                "Since your first payment your billing country can be changed once every 30 days. "
                f"You can change it again on {_day(allowed_at, zone)}.",
            )
        live = live_subscription(db, user.id, now)
        # Crossing is judged against the route the subscription was sold on, not today's table.
        if live is not None and live.period_end > now and live.route != new_route:
            scheduled_for = live.period_end

    if old is not None:  # a checkout exists only under a saved country, so a first choice has none
        supersede_open_plan_checkouts(db, user.id)
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
