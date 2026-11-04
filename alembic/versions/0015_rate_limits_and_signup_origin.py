"""Rate limits in the database, and where an unconfirmed account was made from

`rate_hits` holds the hits of the login and sign-up limits (a keyed hash of the key, never an
address or an email), so a lockout survives a restart and two processes share it.
`users.signup_origin` is a keyed hash of the client address an account was made from; the sign-up
flood defence uses it to cap unconfirmed accounts per address and to clear the busiest address's
rows first. Both additive; the downgrade drops them.

Revision ID: 0015
Revises: 0014
Create Date: 2026-11-04
"""

import sqlalchemy as sa
from alembic import op

revision = "0015"
down_revision = "0014"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "rate_hits",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column("bucket", sa.String(32), nullable=False),
        sa.Column("key", sa.String(32), nullable=False),
        sa.Column("at", sa.Float(precision=53), nullable=False),
    )
    op.create_index("ix_rate_hits_bucket_key_at", "rate_hits", ["bucket", "key", "at"])
    op.add_column("users", sa.Column("signup_origin", sa.String(32), nullable=True))
    op.create_index(
        "ix_users_signup_origin_unconfirmed",
        "users",
        ["signup_origin"],
        postgresql_where=sa.text("email_verified_at is null"),
    )


def downgrade() -> None:
    op.drop_index("ix_users_signup_origin_unconfirmed", table_name="users")
    op.drop_column("users", "signup_origin")
    op.drop_index("ix_rate_hits_bucket_key_at", table_name="rate_hits")
    op.drop_table("rate_hits")
