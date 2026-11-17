"""Billing stage S8a: plans, prices, packs and subscriptions (dark)

Stage S8 of docs/superpowers/specs/2026-11-15-billing-and-plans-design.md (sections 3 and 7), the
money half. New tables nothing from before reads, plus additive columns on `payments`:

- `plans` and `plan_prices`: what is for sale, as data. A price row is one price for one plan in
  one currency from `active_from` to `active_until`; a price change is a new row. Seeded here with
  what is DECIDED: one plan of 20 hours a month at 18 USD and at NGN 25,000 (Adebayo, 2026-11-17:
  1 USD is about 1500 NGN, "round figure of 25k"; 2,500,000 kobo), not public (`is_public` false,
  so nothing prints it on /pricing). No second tier is seeded: it is an open fact.
- `products`: packs, one row per (code, currency). One pack of 10 hours, valid 12 months, at 10 USD
  (the spec says "about 10 USD": confirm the number) and at NGN 14,000 (1,400,000 kobo): **the naira
  pack price is only a PROPOSAL, not confirmed by Adebayo, so that row is seeded INACTIVE** and is
  not sold until `update products set active = true where code = 'pack_10h' and currency = 'NGN'`.
- `subscriptions`: one row per subscription with the route it was SOLD on (`currency` and
  `provider`), so a later edit of the country-to-provider table cannot change what an existing
  subscription is. A partial unique index allows one LIVE row (`active` or `past_due`) per person.
- `payments` gets what a payment needs to be applied by the grant handler without guessing:
  `plan_price_id` / `product_id` (what was bought), `cycle_end` (the subscription period it pays
  for: the identity that makes a second payment for the same period a duplicate), `attempt` (the
  retry slot of a saved-card renewal; a partial unique index lets one slot hold one payment, so two
  schedulers racing cannot both charge), and `pay_url` (the payer's own link for a charge that
  needs them, kept so the person is sent to THAT charge and never to a second checkout).
- Foreign keys the earlier stages left out because the tables did not exist yet:
  `payments.subscription_id`, `hour_grants.source_payment_id`, `hour_grants.subscription_id`.
  Every existing value in those columns is null, so adding them cannot fail.
- A partial index on the webhook inbox rows still waiting, for the sweeper.

Downgrade refuses while any subscription exists or any payment carries one of the new columns:
those are money records that cannot be rebuilt.

Revision ID: 0052
Revises: 0051
Create Date: 2026-11-17
"""

from datetime import UTC, datetime

import sqlalchemy as sa
from alembic import op

revision = "0052"
down_revision = "0051"
branch_labels = None
depends_on = None

CURRENCIES = ("NGN", "USD")
STATUSES = ("active", "past_due", "lapsed", "canceled", "ended")
LIVE = ("active", "past_due")


def _in(column: str, values: tuple[str, ...]) -> str:
    return f"{column} in ({', '.join(repr(v) for v in values)})"


def upgrade() -> None:
    plans = op.create_table(
        "plans",
        sa.Column("code", sa.String(32), primary_key=True),
        sa.Column("name", sa.String(80), nullable=False),
        sa.Column("monthly_seconds", sa.Integer(), nullable=False),
        sa.Column("is_public", sa.Boolean(), server_default=sa.false(), nullable=False),
        sa.Column("sort_order", sa.Integer(), server_default="0", nullable=False),
        sa.Column("active", sa.Boolean(), server_default=sa.true(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.CheckConstraint("monthly_seconds > 0", name="ck_plans_seconds"),
    )

    prices = op.create_table(
        "plan_prices",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column(
            "plan_code",
            sa.String(32),
            sa.ForeignKey("plans.code", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("currency", sa.String(3), nullable=False),
        sa.Column("interval", sa.String(8), server_default="month", nullable=False),
        sa.Column("amount_minor", sa.BigInteger(), nullable=False),
        sa.Column(
            "active_from", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column("active_until", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(_in("currency", CURRENCIES), name="ck_plan_prices_currency"),
        sa.CheckConstraint("interval = 'month'", name="ck_plan_prices_interval"),
        sa.CheckConstraint("amount_minor > 0", name="ck_plan_prices_amount"),
        sa.CheckConstraint(
            "active_until is null or active_until > active_from", name="ck_plan_prices_window"
        ),
        sa.UniqueConstraint("plan_code", "currency", "active_from", name="uq_plan_prices_start"),
    )
    op.create_index("ix_plan_prices_plan_code", "plan_prices", ["plan_code"])

    products = op.create_table(
        "products",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column("code", sa.String(32), nullable=False),
        sa.Column("name", sa.String(80), nullable=False),
        sa.Column("currency", sa.String(3), nullable=False),
        sa.Column("seconds", sa.Integer(), nullable=False),
        sa.Column("amount_minor", sa.BigInteger(), nullable=False),
        sa.Column("valid_months", sa.Integer(), nullable=False),
        sa.Column("active", sa.Boolean(), server_default=sa.true(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.CheckConstraint(_in("currency", CURRENCIES), name="ck_products_currency"),
        sa.CheckConstraint("seconds > 0", name="ck_products_seconds"),
        sa.CheckConstraint("amount_minor > 0", name="ck_products_amount"),
        sa.CheckConstraint("valid_months > 0", name="ck_products_months"),
        sa.UniqueConstraint("code", "currency", name="uq_products_code_currency"),
    )

    op.create_table(
        "subscriptions",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column(
            "user_id",
            sa.BigInteger(),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "payer_user_id",
            sa.BigInteger(),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "plan_code",
            sa.String(32),
            sa.ForeignKey("plans.code", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "price_id",
            sa.BigInteger(),
            sa.ForeignKey("plan_prices.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("status", sa.String(10), nullable=False),
        sa.Column("currency", sa.String(3), nullable=False),
        sa.Column("provider", sa.String(16), nullable=False),
        sa.Column("current_period_start", sa.DateTime(timezone=True), nullable=False),
        sa.Column("current_period_end", sa.DateTime(timezone=True), nullable=False),
        sa.Column("cancel_at_period_end", sa.Boolean(), server_default=sa.false(), nullable=False),
        sa.Column("canceled_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "pending_plan_code",
            sa.String(32),
            sa.ForeignKey("plans.code", ondelete="RESTRICT"),
            nullable=True,
        ),
        sa.Column("grace_ends_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("next_charge_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("retry_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.CheckConstraint(_in("status", STATUSES), name="ck_subscriptions_status"),
        sa.CheckConstraint(_in("currency", CURRENCIES), name="ck_subscriptions_currency"),
        sa.CheckConstraint(
            "current_period_end > current_period_start", name="ck_subscriptions_period"
        ),
        sa.CheckConstraint("retry_count >= 0", name="ck_subscriptions_retry"),
    )
    op.create_index("ix_subscriptions_user_id", "subscriptions", ["user_id"])
    op.create_index(
        "uq_subscriptions_one_live",
        "subscriptions",
        ["user_id"],
        unique=True,
        postgresql_where=sa.text(_in("status", LIVE)),
    )
    # The scheduler's question: which live subscriptions have something due.
    op.create_index(
        "ix_subscriptions_live_due",
        "subscriptions",
        ["current_period_end"],
        postgresql_where=sa.text(_in("status", LIVE)),
    )

    # ---- payments: what was bought, which period it pays for, the retry slot, the payer's link
    op.add_column(
        "payments",
        sa.Column(
            "plan_price_id",
            sa.BigInteger(),
            sa.ForeignKey("plan_prices.id", ondelete="RESTRICT"),
            nullable=True,
        ),
    )
    op.add_column(
        "payments",
        sa.Column(
            "product_id",
            sa.BigInteger(),
            sa.ForeignKey("products.id", ondelete="RESTRICT"),
            nullable=True,
        ),
    )
    op.add_column("payments", sa.Column("cycle_end", sa.DateTime(timezone=True), nullable=True))
    op.add_column("payments", sa.Column("attempt", sa.Integer(), nullable=True))
    op.add_column("payments", sa.Column("pay_url", sa.Text(), nullable=True))
    op.add_column(
        "payments", sa.Column("last_checked_at", sa.DateTime(timezone=True), nullable=True)
    )
    op.create_check_constraint("ck_payments_attempt", "payments", "attempt is null or attempt >= 0")
    op.create_foreign_key(
        "fk_payments_subscription_id",
        "payments",
        "subscriptions",
        ["subscription_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_index("ix_payments_subscription_id", "payments", ["subscription_id"])
    op.create_index(
        "uq_payments_renewal_slot",
        "payments",
        ["subscription_id", "cycle_end", "attempt"],
        unique=True,
        postgresql_where=sa.text("kind = 'renewal' and attempt is not null"),
    )
    op.create_index(
        "ix_payments_pending",
        "payments",
        ["created_at"],
        postgresql_where=sa.text("status = 'pending'"),
    )

    # ---- the grants' own foreign keys
    op.create_foreign_key(
        "fk_hour_grants_source_payment",
        "hour_grants",
        "payments",
        ["source_payment_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_foreign_key(
        "fk_hour_grants_subscription",
        "hour_grants",
        "subscriptions",
        ["subscription_id"],
        ["id"],
        ondelete="SET NULL",
    )

    op.create_index(
        "ix_billing_events_waiting",
        "billing_events",
        ["received_at"],
        postgresql_where=sa.text("processed_at is null"),
    )

    # ---- what is for sale: decided facts (and one inactive proposal)
    now = datetime.now(UTC)
    op.bulk_insert(
        plans,
        [
            {
                "code": "plan_20h",
                "name": "20 hours a month",
                "monthly_seconds": 20 * 3600,
                "is_public": False,
                "sort_order": 1,
                "active": True,
            }
        ],
    )
    op.bulk_insert(
        prices,
        [
            {
                "plan_code": "plan_20h",
                "currency": "USD",
                "interval": "month",
                "amount_minor": 1800,
                "active_from": now,
            },
            {  # decided 2026-11-17: NGN 25,000 a month (kobo)
                "plan_code": "plan_20h",
                "currency": "NGN",
                "interval": "month",
                "amount_minor": 2_500_000,
                "active_from": now,
            },
        ],
    )
    op.bulk_insert(
        products,
        [
            {
                "code": "pack_10h",
                "name": "10 extra hours",
                "currency": "USD",
                "seconds": 10 * 3600,
                "amount_minor": 1000,
                "valid_months": 12,
                "active": True,
            },
            {  # a PROPOSAL (10 of 18 USD, scaled), not confirmed: inactive until he says so
                "code": "pack_10h",
                "name": "10 extra hours",
                "currency": "NGN",
                "seconds": 10 * 3600,
                "amount_minor": 1_400_000,
                "valid_months": 12,
                "active": False,
            },
        ],
    )


def downgrade() -> None:
    conn = op.get_bind()
    subscriptions = conn.execute(sa.text("select count(*) from subscriptions")).scalar()
    bought = conn.execute(
        sa.text(
            "select count(*) from payments where plan_price_id is not null "
            "or product_id is not null or cycle_end is not null or attempt is not null "
            "or pay_url is not null"
        )
    ).scalar()
    if subscriptions or bought:
        raise RuntimeError(
            f"Cannot downgrade 0052: {subscriptions} subscription(s) and {bought} payment(s) "
            "that say what they bought exist, and they cannot be rebuilt. Export them first, "
            "then delete them by hand if you must."
        )
    op.drop_index("ix_billing_events_waiting", table_name="billing_events")
    op.drop_constraint("fk_hour_grants_subscription", "hour_grants", type_="foreignkey")
    op.drop_constraint("fk_hour_grants_source_payment", "hour_grants", type_="foreignkey")
    op.drop_index("ix_payments_pending", table_name="payments")
    op.drop_index("uq_payments_renewal_slot", table_name="payments")
    op.drop_index("ix_payments_subscription_id", table_name="payments")
    op.drop_constraint("fk_payments_subscription_id", "payments", type_="foreignkey")
    op.drop_constraint("ck_payments_attempt", "payments", type_="check")
    for column in (
        "last_checked_at",
        "pay_url",
        "attempt",
        "cycle_end",
        "product_id",
        "plan_price_id",
    ):
        op.drop_column("payments", column)
    op.drop_index("ix_subscriptions_live_due", table_name="subscriptions")
    op.drop_index("uq_subscriptions_one_live", table_name="subscriptions")
    op.drop_index("ix_subscriptions_user_id", table_name="subscriptions")
    op.drop_table("subscriptions")
    op.drop_table("products")
    op.drop_index("ix_plan_prices_plan_code", table_name="plan_prices")
    op.drop_table("plan_prices")
    op.drop_table("plans")
