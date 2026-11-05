"""A person's own time zone

One nullable column on `user_settings`: the IANA zone name chosen on Settings ("Africa/Lagos").
Null means UTC, which is exactly how every time was written before, so an existing row needs no
backfill. Reminder and notes mails, and the web pages, write times in this zone when it is set.

Revision ID: 0018
Revises: 0017
Create Date: 2026-11-05
"""

import sqlalchemy as sa
from alembic import op

revision = "0018"
down_revision = "0017"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("user_settings", sa.Column("time_zone", sa.String(64), nullable=True))


def downgrade() -> None:
    op.drop_column("user_settings", "time_zone")
