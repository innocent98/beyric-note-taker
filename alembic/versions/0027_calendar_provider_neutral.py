"""Provider-neutral calendar connections (Outlook phase O, M1)

Renames the Google-named identity columns of `calendar_connections` to neutral names
(`google_account_email` to `account_email`, `google_sub` to `account_subject`,
`google_hosted_domain` to `account_domain`), adds the three Microsoft columns (`tenant_id`,
`account_kind`, `tenant_domains`), widens the provider check to `microsoft`, and gives
`oauth_flows` a `provider` (existing rows are Google's). The sign-in column `users.google_sub` is a
different thing and is not touched. No data moves: existing Google rows keep their values under the
new names.

Downgrade renames back and drops the new columns; it fails while a `microsoft` connection or flow
exists (delete those rows first, they cannot be represented before this migration).

Revision ID: 0027
Revises: 0026
Create Date: 2026-11-05
"""

import sqlalchemy as sa
from alembic import op

revision = "0027"
down_revision = "0026"
branch_labels = None
depends_on = None

RENAMES = (
    ("google_account_email", "account_email"),
    ("google_sub", "account_subject"),
    ("google_hosted_domain", "account_domain"),
)


def upgrade() -> None:
    for old, new in RENAMES:
        op.alter_column("calendar_connections", old, new_column_name=new)
    op.add_column("calendar_connections", sa.Column("tenant_id", sa.String(36), nullable=True))
    op.add_column("calendar_connections", sa.Column("account_kind", sa.String(8), nullable=True))
    op.add_column("calendar_connections", sa.Column("tenant_domains", sa.Text, nullable=True))
    op.create_check_constraint(
        "ck_calendar_connections_account_kind",
        "calendar_connections",
        "account_kind is null or account_kind in ('personal', 'work')",
    )
    op.drop_constraint("ck_calendar_connections_provider", "calendar_connections", type_="check")
    op.create_check_constraint(
        "ck_calendar_connections_provider",
        "calendar_connections",
        "provider in ('google', 'ics', 'microsoft')",
    )
    op.add_column(
        "oauth_flows",
        sa.Column("provider", sa.String(16), nullable=False, server_default="google"),
    )
    op.create_check_constraint(
        "ck_oauth_flows_provider", "oauth_flows", "provider in ('google', 'microsoft')"
    )


def downgrade() -> None:
    op.drop_constraint("ck_oauth_flows_provider", "oauth_flows", type_="check")
    op.drop_column("oauth_flows", "provider")
    op.drop_constraint("ck_calendar_connections_provider", "calendar_connections", type_="check")
    op.create_check_constraint(
        "ck_calendar_connections_provider", "calendar_connections", "provider in ('google', 'ics')"
    )
    op.drop_constraint(
        "ck_calendar_connections_account_kind", "calendar_connections", type_="check"
    )
    op.drop_column("calendar_connections", "tenant_domains")
    op.drop_column("calendar_connections", "account_kind")
    op.drop_column("calendar_connections", "tenant_id")
    for old, new in RENAMES:
        op.alter_column("calendar_connections", new, new_column_name=old)
