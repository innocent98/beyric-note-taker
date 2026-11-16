"""Billing stage S5: the billing profile, the billing country and its history (dark)

Stage S5 of docs/superpowers/specs/2026-11-15-billing-and-plans-design.md (sections 5.2 and 7).
Two new tables that nothing from before reads, so the code from before this migration ignores
them:

- `billing_profiles`: one row per person (`user_id` unique): who they are billed as, and the
  two-letter `billing_country` they chose, which decides the currency and the provider. A country
  change that has to wait for the end of a paid period sits in `pending_billing_country` with
  `pending_effective_at` (both set or both null). The spec puts the pending country on the
  subscription; the subscriptions table is stage S8's (migration 0052 now), so it sits here until
  then and S8 reads it from here. Deleted with the account.
- `billing_country_changes`: the history, one row per change (`old_country` is null for the first
  choice). `created_at` is when the person asked (the 30-day rule counts from it); `effective_at`
  is when it applies. Deleted with the account.

The spec called this "the second half of 0050". 0050 shipped with stage S4, so this is its own
revision and the later spec numbers move up by one: subscriptions 0052, invoices 0053.

Downgrade drops both tables. It refuses while any profile or history row exists: they are what a
person told us and an audit trail, and cannot be rebuilt.

Revision ID: 0051
Revises: 0050
Create Date: 2026-11-17
"""

import sqlalchemy as sa
from alembic import op

revision = "0051"
down_revision = "0050"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "billing_profiles",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column(
            "user_id",
            sa.BigInteger(),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
            unique=True,
        ),
        sa.Column("name_or_company", sa.String(200), nullable=True),
        sa.Column("address", sa.String(500), nullable=True),
        sa.Column("billing_country", sa.String(2), nullable=True),
        sa.Column("tax_id", sa.String(64), nullable=True),
        sa.Column("billing_email", sa.String(254), nullable=True),
        sa.Column("pending_billing_country", sa.String(2), nullable=True),
        sa.Column("pending_effective_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.CheckConstraint(
            "billing_country is null or billing_country ~ '^[A-Z]{2}$'",
            name="ck_billing_profiles_country",
        ),
        sa.CheckConstraint(
            "pending_billing_country is null or pending_billing_country ~ '^[A-Z]{2}$'",
            name="ck_billing_profiles_pending_country",
        ),
        sa.CheckConstraint(
            "(pending_billing_country is null) = (pending_effective_at is null)",
            name="ck_billing_profiles_pending_pair",
        ),
    )

    op.create_table(
        "billing_country_changes",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column(
            "user_id",
            sa.BigInteger(),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("old_country", sa.String(2), nullable=True),
        sa.Column("new_country", sa.String(2), nullable=False),
        sa.Column("effective_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.CheckConstraint(
            "old_country is null or old_country ~ '^[A-Z]{2}$'",
            name="ck_billing_country_changes_old",
        ),
        sa.CheckConstraint("new_country ~ '^[A-Z]{2}$'", name="ck_billing_country_changes_new"),
    )
    op.create_index(
        "ix_billing_country_changes_user_created",
        "billing_country_changes",
        ["user_id", "created_at"],
    )


def downgrade() -> None:
    conn = op.get_bind()
    profiles = conn.execute(sa.text("select count(*) from billing_profiles")).scalar()
    changes = conn.execute(sa.text("select count(*) from billing_country_changes")).scalar()
    if profiles or changes:
        raise RuntimeError(
            f"Cannot downgrade 0051: {profiles} billing profile(s) and {changes} country "
            "change(s) exist, and they cannot be rebuilt. Export them first, then delete them by "
            "hand if you must."
        )
    op.drop_index("ix_billing_country_changes_user_created", table_name="billing_country_changes")
    op.drop_table("billing_country_changes")
    op.drop_table("billing_profiles")
