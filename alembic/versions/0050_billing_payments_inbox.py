"""Billing stage S4: payments, saved payment methods and the webhook inbox (dark)

Stage S4 of docs/superpowers/specs/2026-11-15-billing-and-plans-design.md (sections 5.4 and 7).
Three new tables that nothing from before reads, so the code from before this migration ignores
them:

- `payments`: one row per attempt to take money, made by us before anyone is sent to a provider.
  `reference` is unique (ours); (`provider`, `provider_tx_id`) is unique once the provider's id is
  known. `payer_user_id` is set null when an account is deleted: money records outlive the person.
  `subscription_id` has no foreign key: its table arrives with stage S8 (migration 0051).
- `payment_methods`: a saved card, only the provider's token, encrypted (app/vault.py). Nothing
  writes it until stage S8. Deleted with the account.
- `billing_events`: the webhook inbox, unique on (`provider`, `dedupe_key`) so a replay is one row.

The billing profile tables of stage S5 are NOT here: this migration ships with S4, so S5 gets its
own revision.

Downgrade drops the three tables. It refuses while any payment or saved card exists: those are
money records that cannot be rebuilt. (The inbox alone can go; it only holds what the providers
sent.)

Revision ID: 0050
Revises: 0049
Create Date: 2026-11-16
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = "0050"
down_revision = "0049"
branch_labels = None
depends_on = None

PAYMENT_KINDS = ("plan", "renewal", "upgrade", "pack")
PAYMENT_STATUSES = ("pending", "succeeded", "failed", "refunded", "partly_refunded")
CURRENCIES = ("NGN", "USD")
EVENT_KINDS = ("charge_succeeded", "charge_failed", "refund", "dispute")


def _in(column: str, values: tuple[str, ...]) -> str:
    return f"{column} in ({', '.join(repr(v) for v in values)})"


def upgrade() -> None:
    op.create_table(
        "payments",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column(
            "payer_user_id",
            sa.BigInteger(),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("subscription_id", sa.BigInteger(), nullable=True),
        sa.Column("provider", sa.String(16), nullable=False),
        sa.Column("provider_tx_id", sa.String(128), nullable=True),
        sa.Column("reference", sa.String(64), nullable=False, unique=True),
        sa.Column("kind", sa.String(12), nullable=False),
        sa.Column("currency", sa.String(3), nullable=False),
        sa.Column("amount_minor", sa.BigInteger(), nullable=False),
        sa.Column("status", sa.String(16), server_default="pending", nullable=False),
        sa.Column("paid_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("card_country", sa.String(2), nullable=True),
        sa.Column("country_mismatch", sa.Boolean(), server_default=sa.false(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.CheckConstraint(_in("kind", PAYMENT_KINDS), name="ck_payments_kind"),
        sa.CheckConstraint(_in("status", PAYMENT_STATUSES), name="ck_payments_status"),
        sa.CheckConstraint(_in("currency", CURRENCIES), name="ck_payments_currency"),
        sa.CheckConstraint("amount_minor > 0", name="ck_payments_amount"),
        sa.UniqueConstraint("provider", "provider_tx_id", name="uq_payments_provider_tx"),
    )
    op.create_index("ix_payments_payer_user_id", "payments", ["payer_user_id"])

    op.create_table(
        "payment_methods",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column(
            "user_id",
            sa.BigInteger(),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("provider", sa.String(16), nullable=False),
        sa.Column("token_enc", sa.LargeBinary(), nullable=False),
        sa.Column("key_version", sa.Integer(), nullable=False),
        sa.Column("brand", sa.String(24), nullable=True),
        sa.Column("last4", sa.String(4), nullable=True),
        sa.Column("expiry", sa.String(7), nullable=True),
        sa.Column("email_at_provider", sa.String(254), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
    )
    op.create_index("ix_payment_methods_user_id", "payment_methods", ["user_id"])

    op.create_table(
        "billing_events",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column("provider", sa.String(16), nullable=False),
        sa.Column("dedupe_key", sa.String(200), nullable=False),
        sa.Column("type", sa.String(24), nullable=False),
        sa.Column("payload", JSONB(), nullable=False),
        sa.Column("signature_ok", sa.Boolean(), server_default=sa.true(), nullable=False),
        sa.Column(
            "received_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column("processed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.CheckConstraint(_in("type", EVENT_KINDS), name="ck_billing_events_type"),
        sa.UniqueConstraint("provider", "dedupe_key", name="uq_billing_events_dedupe"),
    )


def downgrade() -> None:
    conn = op.get_bind()
    payments = conn.execute(sa.text("select count(*) from payments")).scalar()
    methods = conn.execute(sa.text("select count(*) from payment_methods")).scalar()
    if payments or methods:
        raise RuntimeError(
            f"Cannot downgrade 0050: {payments} payment(s) and {methods} saved card(s) exist, and "
            "they cannot be rebuilt. Export them first, then delete them by hand if you must."
        )
    op.drop_table("billing_events")
    op.drop_table("payment_methods")
    op.drop_index("ix_payments_payer_user_id", table_name="payments")
    op.drop_table("payments")
