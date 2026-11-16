"""Billing stage S1: hour grants and allocations, with the pilot backfill (dark)

Stage S1 of docs/superpowers/specs/2026-11-15-billing-and-plans-design.md (sections 7 and 8).
Two new tables nothing from before reads, so the code from before this migration ignores them:

- `hour_grants`: the hours a person holds, one row per source (trial, pilot, plan period, pack,
  admin gift). What is left of a grant is its `seconds` minus its `usage_allocations`.
- `usage_allocations`: the part of one counted recording taken from one grant. Stage S1 writes
  none; stage S2 writes them as each recording is counted.

Backfill: every account that is not an admin and not deleted gets one `pilot` grant of its cap
minus what it has used, never below 0, no expiry. The cap is the account's own override
(`users.hours_cap_seconds`), else `app_state.default_hours_cap_seconds`, else 18000, the same order
as `usage.effective_cap` except that a NULL app default ("no cap" there) becomes 18000 here, as the
spec words it. What it has used is the ledger (`usage_entries`) summed by user id or by
the one-way email fingerprint, the same two keys as `usage.ledger_seconds`, so an account that was
deleted and signed up again keeps the same leftover and nobody loses or gains an hour (bar a recording that commits mid-migration, see the SOP). The
fingerprint is recomputed here in SQL as sha256 of the trimmed, lower-cased email, which is what
`usage.email_hash` does. `starts_at` is the moment the backfill statement starts: the rule reading
the grants counts any usage recorded after it that has no allocation yet (`app/entitlements.py`).

Downgrade drops both tables. It refuses while a grant of any kind but `pilot` exists, because those
come from a payment, a trial or an admin and cannot be rebuilt from the ledger.

Revision ID: 0049
Revises: 0048
Create Date: 2026-11-16
"""

import sqlalchemy as sa
from alembic import op

revision = "0049"
down_revision = "0048"
branch_labels = None
depends_on = None

KINDS = ("trial", "pilot", "plan_period", "pack", "admin_gift")


def upgrade() -> None:
    op.create_table(
        "hour_grants",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column(
            "user_id",
            sa.BigInteger(),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("email_hash", sa.String(64), nullable=False),
        sa.Column("kind", sa.String(16), nullable=False),
        sa.Column("seconds", sa.Integer(), nullable=False),
        sa.Column(
            "starts_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("source_payment_id", sa.BigInteger(), nullable=True),
        sa.Column("subscription_id", sa.BigInteger(), nullable=True),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column(
            "created_by",
            sa.BigInteger(),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.CheckConstraint(
            "kind in (" + ", ".join(repr(k) for k in KINDS) + ")", name="ck_hour_grants_kind"
        ),
        sa.CheckConstraint("seconds >= 0", name="ck_hour_grants_seconds"),
        sa.UniqueConstraint("kind", "source_payment_id", name="uq_hour_grants_kind_payment"),
    )
    op.create_index("ix_hour_grants_user_id", "hour_grants", ["user_id"])
    op.create_index("ix_hour_grants_email_hash", "hour_grants", ["email_hash"])

    op.create_table(
        "usage_allocations",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column(
            "usage_entry_id",
            sa.BigInteger(),
            sa.ForeignKey("usage_entries.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "grant_id",
            sa.BigInteger(),
            sa.ForeignKey("hour_grants.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("seconds", sa.Integer(), nullable=False),
        sa.UniqueConstraint("usage_entry_id", "grant_id", name="uq_usage_allocations_entry_grant"),
        sa.CheckConstraint("seconds > 0", name="ck_usage_allocations_seconds"),
    )
    op.create_index("ix_usage_allocations_usage_entry_id", "usage_allocations", ["usage_entry_id"])
    op.create_index("ix_usage_allocations_grant_id", "usage_allocations", ["grant_id"])

    # The pilot backfill. One statement, so it sees one snapshot of the ledger. `starts_at` is this
    # statement's start (not `now()`, the earlier start of the whole transaction): a recording
    # counted after the snapshot is then never also counted in the grant. One direction is left:
    # a recording whose transaction began before this statement and commits after its snapshot
    # is in neither the grant nor the "since the grant began" term (see the SOP).
    op.execute(
        """
        insert into hour_grants (user_id, email_hash, kind, seconds, starts_at, reason)
        select u.id, h.eh, 'pilot',
               greatest(c.cap - used.seconds, 0)::integer,
               statement_timestamp(),
               'pilot grant: the old cap minus the hours used (migration 0049)'
        from users u
        cross join lateral (
            select encode(sha256(convert_to(lower(trim(u.email)), 'UTF8')), 'hex') as eh
        ) h
        cross join lateral (
            select coalesce(
                u.hours_cap_seconds,
                (select a.default_hours_cap_seconds from app_state a where a.id = 1),
                18000
            ) as cap
        ) c
        cross join lateral (
            select coalesce(sum(e.seconds), 0) as seconds
            from usage_entries e
            where e.user_id = u.id or e.email_hash = h.eh
        ) used
        where not u.is_admin and u.status <> 'deleted' and u.purged_at is null
        """
    )


def downgrade() -> None:
    other = op.get_bind().execute(sa.text("select count(*) from hour_grants where kind <> 'pilot'"))
    count = other.scalar()
    if count:
        raise RuntimeError(
            f"Cannot downgrade 0049: {count} hour grant(s) are not pilot grants (a trial, a plan "
            "period, a pack or an admin gift). They cannot be rebuilt from the ledger; remove "
            "them on purpose first."
        )
    op.drop_table("usage_allocations")
    op.drop_table("hour_grants")
