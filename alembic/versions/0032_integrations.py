"""W10 Integrations: a team's Slack webhook (and, later, a tracker), and the post log

`integrations` holds one row per team and kind. `config_enc` is the vault blob (purpose
`integration`, owner `team:<id>:<kind>`) of the secret, so a blob copied to another team or kind
does not open. `integration_posts` is written before each outbound call: a partial unique index
makes one post per meeting true across retries, and its recent rows are the per-team rate cap.

Revision ID: 0032
Revises: 0030
Create Date: 2026-11-06
"""

import sqlalchemy as sa
from alembic import op

revision = "0032"
down_revision = "0030"
branch_labels = None
depends_on = None

TIMESTAMP = sa.DateTime(timezone=True)


def upgrade() -> None:
    op.create_table(
        "integrations",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column(
            "team_id",
            sa.BigInteger(),
            sa.ForeignKey("teams.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("kind", sa.String(24), nullable=False),
        sa.Column("config_enc", sa.LargeBinary(), nullable=False),
        sa.Column("key_version", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(12), nullable=False, server_default="connected"),
        sa.Column("last_error", sa.String(200)),
        sa.Column("last_error_at", TIMESTAMP),
        sa.Column("last_posted_at", TIMESTAMP),
        sa.Column("connected_by", sa.BigInteger(), sa.ForeignKey("users.id", ondelete="SET NULL")),
        sa.Column("created_at", TIMESTAMP, nullable=False, server_default=sa.func.now()),
        sa.CheckConstraint("kind in ('slack_webhook')", name="ck_integrations_kind"),
        sa.CheckConstraint("status in ('connected', 'failing')", name="ck_integrations_status"),
        sa.UniqueConstraint("team_id", "kind", name="uq_integrations_team_kind"),
    )
    op.create_table(
        "integration_posts",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column(
            "team_id",
            sa.BigInteger(),
            sa.ForeignKey("teams.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("kind", sa.String(24), nullable=False),
        sa.Column("meeting_id", sa.Integer(), sa.ForeignKey("meetings.id", ondelete="CASCADE")),
        sa.Column("created_at", TIMESTAMP, nullable=False, server_default=sa.func.now()),
    )
    op.create_index(
        "ix_integration_posts_team_created", "integration_posts", ["team_id", "created_at"]
    )
    op.create_index(
        "uq_integration_posts_meeting",
        "integration_posts",
        ["team_id", "kind", "meeting_id"],
        unique=True,
        postgresql_where=sa.text("meeting_id is not null"),
    )


def downgrade() -> None:
    op.drop_table("integration_posts")
    op.drop_table("integrations")
