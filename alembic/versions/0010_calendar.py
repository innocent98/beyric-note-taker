"""calendar connections, events and scheduled joins

The three tables behind calendar auto-join (spec 5.1). Additive only: nothing existing is touched,
so it is safe while the pilot is up. The downgrade drops the three tables and with them every
connection, event and scheduled join; meetings already recorded are untouched (a scheduled join
points at its meeting, never the other way round).

Differences from the spec text, all deliberate (plan task C1): `source_event_id` and
`source_updated_at` replace the Google-named columns so the pasted-link (ICS) source needs no
rename; `provider` allows `google` and `ics`; `scheduled_joins.rechecked_at` lets the 2-minute
re-check run once; `calendar_events.all_day` and `event_type` are kept so a rule change can re-run
`will_join` from the stored row alone.

Revision ID: 0010
Revises: 0009
Create Date: 2026-11-03
"""

import sqlalchemy as sa
from alembic import op

revision = "0010"
down_revision = "0009"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "calendar_connections",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column(
            "user_id",
            sa.BigInteger(),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("provider", sa.String(16), nullable=False, server_default="google"),
        sa.Column("google_account_email", sa.String(254)),
        sa.Column("google_sub", sa.String(64)),
        sa.Column("google_hosted_domain", sa.String(253)),
        sa.Column("status", sa.String(16), nullable=False, server_default="connected"),
        sa.Column("needs_reconnect_reason", sa.String(16)),
        sa.Column("granted_scopes", sa.Text()),
        sa.Column("refresh_token_enc", sa.LargeBinary()),
        sa.Column("key_version", sa.Integer()),
        sa.Column(
            "connected_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column("last_sync_at", sa.DateTime(timezone=True)),
        sa.Column("last_error", sa.Text()),
        sa.Column("last_error_at", sa.DateTime(timezone=True)),
        sa.Column("sync_token", sa.Text()),
        sa.Column("expected_expiry_at", sa.DateTime(timezone=True)),
        sa.Column("meet_subscription_name", sa.Text()),
        sa.Column("meet_subscription_expires_at", sa.DateTime(timezone=True)),
        sa.Column("meet_subscription_state", sa.String(16), nullable=False, server_default="none"),
        sa.CheckConstraint(
            "provider in ('google', 'ics')", name="ck_calendar_connections_provider"
        ),
        sa.CheckConstraint(
            "status in ('connected', 'degraded', 'needs_reconnect', 'disconnected')",
            name="ck_calendar_connections_status",
        ),
        sa.CheckConstraint(
            "needs_reconnect_reason is null or "
            "needs_reconnect_reason in ('expired', 'revoked', 'error')",
            name="ck_calendar_connections_reason",
        ),
        sa.CheckConstraint(
            "meet_subscription_state in ('none', 'active', 'needs_reconnect', 'error')",
            name="ck_calendar_connections_meet_state",
        ),
        sa.UniqueConstraint("user_id", "provider", name="uq_calendar_connections_user_provider"),
    )

    op.create_table(
        "calendar_events",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column(
            "user_id",
            sa.BigInteger(),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "connection_id",
            sa.BigInteger(),
            sa.ForeignKey("calendar_connections.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("source_event_id", sa.String(1024), nullable=False),
        sa.Column("recurring_event_id", sa.String(1024)),
        sa.Column("title", sa.Text(), nullable=False, server_default=""),
        sa.Column("starts_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("ends_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("platform", sa.String(16)),
        sa.Column("meeting_url", sa.Text()),
        sa.Column("native_id", sa.String(200)),
        sa.Column("organizer_self", sa.Boolean()),
        sa.Column("attendee_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("self_response", sa.String(16)),
        sa.Column("status", sa.String(16), nullable=False, server_default="confirmed"),
        sa.Column("all_day", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("event_type", sa.String(32), nullable=False, server_default="default"),
        sa.Column("override", sa.Boolean()),
        sa.Column("source_updated_at", sa.DateTime(timezone=True)),
        sa.Column(
            "synced_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.CheckConstraint(
            "platform is null or platform in ('google_meet', 'zoom', 'teams')",
            name="ck_calendar_events_platform",
        ),
        sa.CheckConstraint(
            "status in ('confirmed', 'tentative', 'cancelled')", name="ck_calendar_events_status"
        ),
        sa.CheckConstraint(
            "self_response is null or "
            "self_response in ('accepted', 'declined', 'tentative', 'needsAction')",
            name="ck_calendar_events_response",
        ),
        sa.UniqueConstraint("connection_id", "source_event_id", name="uq_calendar_events_source"),
    )
    op.create_index("ix_calendar_events_user_starts", "calendar_events", ["user_id", "starts_at"])

    op.create_table(
        "scheduled_joins",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column(
            "user_id",
            sa.BigInteger(),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("source", sa.String(16), nullable=False, server_default="calendar"),
        sa.Column(
            "calendar_event_id",
            sa.BigInteger(),
            sa.ForeignKey("calendar_events.id", ondelete="SET NULL"),
        ),
        sa.Column("platform", sa.String(16), nullable=False),
        sa.Column("native_id", sa.String(200), nullable=False),
        sa.Column("meeting_url", sa.Text(), nullable=False),
        sa.Column("starts_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("ends_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("run_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("state", sa.String(16), nullable=False, server_default="scheduled"),
        sa.Column("skip_reason", sa.String(32)),
        sa.Column("template_id", sa.Integer(), sa.ForeignKey("templates.id", ondelete="SET NULL")),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("deadline_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("meeting_id", sa.Integer(), sa.ForeignKey("meetings.id", ondelete="SET NULL")),
        sa.Column("last_error", sa.Text()),
        sa.Column("rechecked_at", sa.DateTime(timezone=True)),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.CheckConstraint(
            "source in ('calendar', 'instant_meet')", name="ck_scheduled_joins_source"
        ),
        sa.CheckConstraint(
            "platform in ('google_meet', 'zoom', 'teams')", name="ck_scheduled_joins_platform"
        ),
        sa.CheckConstraint(
            "state in ('scheduled', 'dispatching', 'joined', 'completed', 'skipped', "
            "'cancelled', 'no_capacity', 'failed')",
            name="ck_scheduled_joins_state",
        ),
        sa.UniqueConstraint("user_id", "calendar_event_id", name="uq_scheduled_joins_user_event"),
    )
    op.create_index("ix_scheduled_joins_state_run_at", "scheduled_joins", ["state", "run_at"])
    op.create_index(
        "uq_scheduled_joins_live_code",
        "scheduled_joins",
        ["platform", "native_id"],
        unique=True,
        postgresql_where=sa.text("state in ('dispatching', 'no_capacity', 'joined')"),
    )


def downgrade() -> None:
    op.drop_table("scheduled_joins")  # takes its indexes with it
    op.drop_table("calendar_events")
    op.drop_table("calendar_connections")
