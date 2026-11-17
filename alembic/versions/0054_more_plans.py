"""Billing: two more monthly plans, Pro and Scale (data only)

Decided by Adebayo on 2026-11-17: next to the seeded 20-hour plan (`plan_20h`, USD 18, NGN 25,000;
migration 0052, unchanged) two bigger monthly plans:

| code        | name  | hours a month | USD       | NGN                    |
|-------------|-------|---------------|-----------|------------------------|
| `plan_50h`  | Pro   | 50            | 40.00     | 56,000.00 (5,600,000 kobo)  |
| `plan_100h` | Scale | 100           | 80.00     | 112,000.00 (11,200,000 kobo) |

Rows only, no schema change: plans and prices are data (billing spec 2.1 and 3.4), so the plans
route, the checkout, the change-plan preview (the proration and the downgrade at renewal) and the
renewal read them without any code change. `sort_order` 2 and 3 puts them after the first plan.
Not public (`is_public` false): the public /pricing page prints them from
`web/components/pricing/content.ts`, which a test pins to these rows, like the first plan's.

The names "Pro" and "Scale" are the lead's proposal. They are written once here (`PRO_NAME`,
`SCALE_NAME`) and once in `web/components/pricing/content.ts`; a rename after this has run is a
new migration that updates `plans.name`, plus that one line.

A price change is a NEW `plan_prices` row, never an edit of these (spec 3.4: a subscription keeps
its price until its renewal).

Downgrade removes the two plans and their prices, and refuses while any subscription, pending
downgrade or payment points at either: those are money records.

Revision ID: 0054
Revises: 0052
Create Date: 2026-11-17

NOTE FOR MERGING: PR #234 (S9) takes revision 0053 (down revision 0052). This migration is
numbered 0054 and still points at 0052 so that CI has one head while #234 is unmerged. **Merge
#234 first, then change `down_revision` below to "0053"** (one line); two heads fail the single-
head check."""

from datetime import UTC, datetime

import sqlalchemy as sa
from alembic import op

revision = "0054"
down_revision = "0052"  # becomes "0053" once #234 (S9) has merged
branch_labels = None
depends_on = None

PRO_NAME = "Pro"
SCALE_NAME = "Scale"

# (code, name, hours a month, sort order, USD minor units, NGN minor units)
PLANS = (
    ("plan_50h", PRO_NAME, 50, 2, 4000, 5_600_000),
    ("plan_100h", SCALE_NAME, 100, 3, 8000, 11_200_000),
)


def upgrade() -> None:
    plans = sa.table(
        "plans",
        sa.column("code"),
        sa.column("name"),
        sa.column("monthly_seconds"),
        sa.column("is_public"),
        sa.column("sort_order"),
        sa.column("active"),
    )
    prices = sa.table(
        "plan_prices",
        sa.column("plan_code"),
        sa.column("currency"),
        sa.column("interval"),
        sa.column("amount_minor"),
        sa.column("active_from"),
    )
    now = datetime.now(UTC)
    op.bulk_insert(
        plans,
        [
            {
                "code": code,
                "name": name,
                "monthly_seconds": hours * 3600,
                "is_public": False,
                "sort_order": order,
                "active": True,
            }
            for code, name, hours, order, _usd, _ngn in PLANS
        ],
    )
    op.bulk_insert(
        prices,
        [
            {
                "plan_code": code,
                "currency": currency,
                "interval": "month",
                "amount_minor": amount,
                "active_from": now,
            }
            for code, _name, _hours, _order, usd, ngn in PLANS
            for currency, amount in (("USD", usd), ("NGN", ngn))
        ],
    )


def downgrade() -> None:
    conn = op.get_bind()
    codes = tuple(code for code, *_ in PLANS)
    in_use = conn.execute(
        sa.text(
            "select (select count(*) from subscriptions where plan_code = any(:codes) "
            "or pending_plan_code = any(:codes)) + "
            "(select count(*) from payments where plan_price_id in "
            "(select id from plan_prices where plan_code = any(:codes)))"
        ),
        {"codes": list(codes)},
    ).scalar()
    if in_use:
        raise RuntimeError(
            "Refusing to downgrade 0054: a subscription, a pending downgrade or a payment "
            "points at the Pro or Scale plan. Those are money records that cannot be rebuilt."
        )
    conn.execute(
        sa.text("delete from plan_prices where plan_code = any(:codes)"), {"codes": list(codes)}
    )
    conn.execute(sa.text("delete from plans where code = any(:codes)"), {"codes": list(codes)})
