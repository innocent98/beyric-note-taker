"""Billing stage S9: invoices, the invoice counter, refunds (dark)

Stage S9 of docs/superpowers/specs/2026-11-15-billing-and-plans-design.md (sections 3.5, 6 and 7).

- `invoice_counters`: the last number given in each year. A number is taken with one atomic
  upsert whose row lock lasts to the end of the issuing transaction, so two payments settled at the
  same moment queue on it: no number twice, and a rollback gives its number back (no gaps).
- `invoices`: one per payment that was used, `BN-<year>-<six digits>`, a unique number, a unique
  payment, the seller and the buyer as snapshots, the VAT included in the price (`vat_rate_bp`,
  default 0), `subtotal + vat = total` held by a check. A money record: it outlives the person
  (`payer_user_id` is set null on account deletion) and has no foreign key that can delete it.
- `refunds`: one per refund of a payment, whole payments only, with our own `reference` (the key a
  provider can use to refuse a retry) and a partial unique index that allows one live refund per
  payment. The grant that was ended for it is remembered so a refused refund gives the hours back.
- `payments.needs_refund_reason`: set when a payment succeeded and nothing could use it. It is the
  fact that makes a refund a "double charge", and such a payment is never invoiced. Payments that
  already needed a refund before this migration (their admin alert exists, nothing on the row) stay
  null: an admin sees them in the alert and refunds them by hand, as before S9.

Additive only: the old code keeps working during a rollout. Downgrade refuses while any invoice or
refund exists (money records that cannot be rebuilt).

Revision ID: 0053
Revises: 0052
Create Date: 2026-11-17
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = "0053"
down_revision = "0052"
branch_labels = None
depends_on = None

CURRENCIES = ("NGN", "USD")
REASONS = ("double_charge", "first_plan_7d", "pack_14d")
STATUSES = ("pending", "submitted", "unknown", "failed")


def _in(column: str, values: tuple[str, ...]) -> str:
    return f"{column} in ({', '.join(repr(v) for v in values)})"


def upgrade() -> None:
    op.add_column("payments", sa.Column("needs_refund_reason", sa.Text(), nullable=True))

    op.create_table(
        "invoice_counters",
        sa.Column("year", sa.Integer(), primary_key=True, autoincrement=False),
        sa.Column("last_number", sa.Integer(), server_default="0", nullable=False),
        sa.CheckConstraint("last_number >= 0", name="ck_invoice_counters_number"),
    )

    op.create_table(
        "invoices",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column("number", sa.String(20), nullable=False),
        sa.Column(
            "payment_id",
            sa.BigInteger(),
            sa.ForeignKey("payments.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "payer_user_id",
            sa.BigInteger(),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("issued_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("currency", sa.String(3), nullable=False),
        sa.Column("subtotal_minor", sa.BigInteger(), nullable=False),
        sa.Column("vat_rate_bp", sa.Integer(), server_default="0", nullable=False),
        sa.Column("vat_minor", sa.BigInteger(), server_default="0", nullable=False),
        sa.Column("total_minor", sa.BigInteger(), nullable=False),
        sa.Column("seller", JSONB(), nullable=False),
        sa.Column("buyer", JSONB(), nullable=False),
        sa.Column("line", JSONB(), nullable=False),
        sa.Column("test_mode", sa.Boolean(), server_default=sa.false(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.UniqueConstraint("number", name="uq_invoices_number"),
        sa.UniqueConstraint("payment_id", name="uq_invoices_payment_id"),
        sa.CheckConstraint(_in("currency", CURRENCIES), name="ck_invoices_currency"),
        sa.CheckConstraint("total_minor > 0", name="ck_invoices_total"),
        sa.CheckConstraint("vat_minor >= 0 and subtotal_minor >= 0", name="ck_invoices_parts"),
        sa.CheckConstraint("subtotal_minor + vat_minor = total_minor", name="ck_invoices_sum"),
        sa.CheckConstraint("vat_rate_bp >= 0 and vat_rate_bp <= 10000", name="ck_invoices_rate"),
    )
    op.create_index("ix_invoices_payer_user_id", "invoices", ["payer_user_id"])

    op.create_table(
        "refunds",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column(
            "payment_id",
            sa.BigInteger(),
            sa.ForeignKey("payments.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("reference", sa.String(64), nullable=False),
        sa.Column("reason", sa.String(16), nullable=False),
        sa.Column("status", sa.String(12), server_default="pending", nullable=False),
        sa.Column("amount_minor", sa.BigInteger(), nullable=False),
        sa.Column("currency", sa.String(3), nullable=False),
        sa.Column("provider_refund_id", sa.String(128), nullable=True),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column(
            "created_by",
            sa.BigInteger(),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "grant_id",
            sa.BigInteger(),
            sa.ForeignKey("hour_grants.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("grant_expires_before", sa.DateTime(timezone=True), nullable=True),
        sa.Column("seconds_removed", sa.Integer(), server_default="0", nullable=False),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "resolved_by",
            sa.BigInteger(),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.UniqueConstraint("reference", name="uq_refunds_reference"),
        sa.CheckConstraint(_in("reason", REASONS), name="ck_refunds_reason"),
        sa.CheckConstraint(_in("status", STATUSES), name="ck_refunds_status"),
        sa.CheckConstraint(_in("currency", CURRENCIES), name="ck_refunds_currency"),
        sa.CheckConstraint("amount_minor > 0", name="ck_refunds_amount"),
    )
    op.create_index("ix_refunds_payment_id", "refunds", ["payment_id"])
    op.create_index(
        "uq_refunds_payment_live",
        "refunds",
        ["payment_id"],
        unique=True,
        postgresql_where=sa.text("status in ('pending', 'submitted', 'unknown')"),
    )


def downgrade() -> None:
    conn = op.get_bind()
    invoices = conn.execute(sa.text("select count(*) from invoices")).scalar()
    refunds = conn.execute(sa.text("select count(*) from refunds")).scalar()
    if invoices or refunds:
        raise RuntimeError(
            f"Cannot downgrade 0053: {invoices} invoice(s) and {refunds} refund(s) exist, and "
            "they are money records that cannot be rebuilt. Export them first, then delete them "
            "by hand if you must."
        )
    op.drop_index("uq_refunds_payment_live", table_name="refunds")
    op.drop_index("ix_refunds_payment_id", table_name="refunds")
    op.drop_table("refunds")
    op.drop_index("ix_invoices_payer_user_id", table_name="invoices")
    op.drop_table("invoices")
    op.drop_table("invoice_counters")
    op.drop_column("payments", "needs_refund_reason")
