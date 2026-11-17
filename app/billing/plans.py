"""What can be bought, what it costs, and the arithmetic of periods and upgrades (billing spec
3.4 and 7).

Plans and prices are rows (`plans`, `plan_prices`, `products`), never code: nothing here names a
plan. The price a person pays is the row valid at the moment they pay, in the currency of the route
they are billed on. A subscription keeps the row it was sold at until its next renewal (spec 3.4,
"price change"), which asks for the current row again (`renewal_price`).

Time and money arithmetic is plain integers, so a test can assert exact numbers:

- `add_months`: one calendar month later, the day clamped to the month's length (31 January plus
  one month is 28 February). A renewal period starts where the last ended, so the day of the
  month can drift down once and then stays (28 February plus one month is 28 March).
- `prorate_upgrade`: the price difference for the part of the period still to run, rounded DOWN to
  a whole minor unit, and the extra hours for the same part, rounded DOWN to 0.1 hour (360 s).
"""

import calendar
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.billing.types import BillingError
from app.models import Plan, PlanPrice, Product, Subscription

TENTH_OF_AN_HOUR = 360  # seconds


class NoUpgrade(BillingError):
    """The upgrade cannot be priced: `code` is what the API says."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code, self.message = code, message


def add_months(moment: datetime, months: int = 1) -> datetime:
    """`moment` plus `months` calendar months, the day clamped to the target month's length."""
    index = moment.year * 12 + (moment.month - 1) + months
    year, month = divmod(index, 12)
    month += 1
    day = min(moment.day, calendar.monthrange(year, month)[1])
    return moment.replace(year=year, month=month, day=day)


def active_plans(db: Session) -> list[Plan]:
    return list(
        db.scalars(select(Plan).where(Plan.active.is_(True)).order_by(Plan.sort_order, Plan.code))
    )


def get_plan(db: Session, code: str) -> Plan | None:
    return db.get(Plan, code)


def current_price(db: Session, plan_code: str, currency: str, now: datetime) -> PlanPrice | None:
    """The price of `plan_code` in `currency` valid at `now` (the newest row that has started and
    not ended), or None when none is: a plan with no price in a currency cannot be bought there."""
    return db.scalars(
        select(PlanPrice)
        .where(
            PlanPrice.plan_code == plan_code,
            PlanPrice.currency == currency,
            PlanPrice.active_from <= now,
            (PlanPrice.active_until.is_(None)) | (PlanPrice.active_until > now),
        )
        .order_by(PlanPrice.active_from.desc(), PlanPrice.id.desc())
        .limit(1)
    ).first()


def active_products(db: Session, currency: str) -> list[Product]:
    return list(
        db.scalars(
            select(Product)
            .where(Product.active.is_(True), Product.currency == currency)
            .order_by(Product.code)
        )
    )


def get_product(db: Session, code: str, currency: str) -> Product | None:
    return db.scalars(
        select(Product).where(
            Product.code == code, Product.currency == currency, Product.active.is_(True)
        )
    ).first()


def renewal_price(db: Session, sub: Subscription, now: datetime) -> PlanPrice | None:
    """The price the next renewal charges, in the currency the subscription was sold in: the
    current price of the plan the person will be on (the downgrade they asked for, else their
    plan). If that plan has no current price in the currency (a price row was ended), the price
    row the subscription already holds, for the plan it is on. None when even that is gone."""
    for code in (sub.pending_plan_code, sub.plan_code):
        if code is None:
            continue
        price = current_price(db, code, sub.currency, now)
        if price is not None:
            return price
    held = db.get(PlanPrice, sub.price_id)
    return held if held is not None and held.currency == sub.currency else None


@dataclass(frozen=True)
class Proration:
    amount_minor: int  # what the upgrade costs now, in minor units
    extra_seconds: int  # the hours added to the current period


def prorate_upgrade(
    *,
    old_amount_minor: int,
    new_amount_minor: int,
    old_seconds: int,
    new_seconds: int,
    period_start: datetime,
    period_end: datetime,
    now: datetime,
) -> Proration:
    """The price difference and the extra hours for the part of the period left (spec 3.4).

    `amount = (new - old) * left / period`, rounded down; `extra = (new_hours - old_hours) * left /
    period`, rounded down to 0.1 hour. Raises `NoUpgrade` when the new plan is not dearer or not
    bigger, or when so little of the period is left that the amount rounds to nothing."""
    if new_amount_minor <= old_amount_minor or new_seconds <= old_seconds:
        raise NoUpgrade("not_an_upgrade", "That plan is not a bigger one.")
    total = int((period_end - period_start).total_seconds())
    left = int((period_end - now).total_seconds())
    if total <= 0 or left <= 0:
        raise NoUpgrade("period_over", "Your period has ended; change plan at the renewal.")
    amount = (new_amount_minor - old_amount_minor) * left // total
    extra = ((new_seconds - old_seconds) * left // total) // TENTH_OF_AN_HOUR * TENTH_OF_AN_HOUR
    if amount <= 0:
        raise NoUpgrade(
            "period_almost_over",
            "Your period ends very soon: change plan at the renewal instead.",
        )
    return Proration(amount_minor=amount, extra_seconds=extra)
