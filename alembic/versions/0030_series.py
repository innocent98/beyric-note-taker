"""W8 Series: recurring meetings, the agenda, carry-over flags and reminder settings

`series` is a team's recurring meeting (from the calendar's recurring-event id, or attached by
hand); `meetings.series_id` links a meeting to it. `series_agenda_items` are the typed lines of
"Other agenda items" (consumed when the next meeting is recorded). `series_agenda_sends` is one
press of "Send agenda" (the id its mail rows carry and what the daily caps count) and
`series_reminders` one reminder owed to one person for one occurrence (its unique key is what makes
a reminder go once). `open_questions` gains `carry` ("On next agenda", default true) and
`resolved_by`. `teams` gains the reminder switch and timing (off by default), `user_settings` the
person's own reminder preference (on). `series.project_id` has no foreign key: projects arrive in
their own migration (W4), which writes it.

Revision ID: 0030
Revises: 0026
Create Date: 2026-11-06
"""

import sqlalchemy as sa
from alembic import op

revision = "0030"
down_revision = "0026"
branch_labels = None
depends_on = None

TIMESTAMP = sa.DateTime(timezone=True)


def upgrade() -> None:
    op.create_table(
        "series",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column(
            "team_id",
            sa.BigInteger(),
            sa.ForeignKey("teams.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("owner_user_id", sa.BigInteger(), sa.ForeignKey("users.id", ondelete="SET NULL")),
        sa.Column("title", sa.String(200), nullable=False, server_default=""),
        sa.Column("calendar_recurring_id", sa.String(1024)),
        sa.Column("platform", sa.String(16)),
        sa.Column("project_id", sa.BigInteger()),
        sa.Column("created_at", TIMESTAMP, nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_series_team_id", "series", ["team_id"])
    op.create_index(
        "uq_series_team_recurring",
        "series",
        ["team_id", "calendar_recurring_id"],
        unique=True,
        postgresql_where=sa.text("calendar_recurring_id is not null"),
    )

    op.add_column(
        "meetings",
        sa.Column("series_id", sa.BigInteger(), sa.ForeignKey("series.id", ondelete="SET NULL")),
    )
    op.create_index(
        "ix_meetings_series_id",
        "meetings",
        ["series_id"],
        postgresql_where=sa.text("series_id is not null"),
    )

    op.create_table(
        "series_agenda_items",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column(
            "series_id",
            sa.BigInteger(),
            sa.ForeignKey("series.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("text", sa.String(200), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_by", sa.BigInteger(), sa.ForeignKey("users.id", ondelete="SET NULL")),
        sa.Column(
            "consumed_by_meeting_id",
            sa.BigInteger(),
            sa.ForeignKey("meetings.id", ondelete="SET NULL"),
        ),
        sa.Column("consumed_at", TIMESTAMP),
        sa.Column("created_at", TIMESTAMP, nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_series_agenda_items_series_id", "series_agenda_items", ["series_id"])

    op.create_table(
        "series_agenda_sends",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column(
            "series_id",
            sa.BigInteger(),
            sa.ForeignKey("series.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "sender_user_id", sa.BigInteger(), sa.ForeignKey("users.id", ondelete="SET NULL")
        ),
        sa.Column("recipient_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_at", TIMESTAMP, nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_series_agenda_sends_series_id", "series_agenda_sends", ["series_id"])
    op.create_index(
        "ix_series_agenda_sends_sender", "series_agenda_sends", ["sender_user_id", "created_at"]
    )

    op.create_table(
        "series_reminders",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column(
            "series_id",
            sa.BigInteger(),
            sa.ForeignKey("series.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "user_id",
            sa.BigInteger(),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("kind", sa.String(4), nullable=False),
        sa.Column("starts_at", TIMESTAMP, nullable=False),
        sa.Column(
            "calendar_event_id",
            sa.BigInteger(),
            sa.ForeignKey("calendar_events.id", ondelete="SET NULL"),
        ),
        sa.Column("created_at", TIMESTAMP, nullable=False, server_default=sa.func.now()),
        sa.CheckConstraint("kind in ('1h', '30m')", name="ck_series_reminders_kind"),
        sa.UniqueConstraint(
            "series_id", "user_id", "kind", "starts_at", name="uq_series_reminders_occurrence"
        ),
    )

    op.add_column(
        "open_questions",
        sa.Column("resolved_by", sa.BigInteger(), sa.ForeignKey("users.id", ondelete="SET NULL")),
    )
    op.add_column(
        "open_questions",
        sa.Column("carry", sa.Boolean(), nullable=False, server_default=sa.true()),
    )

    op.add_column(
        "teams",
        sa.Column("reminders_on", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.add_column(
        "teams",
        sa.Column("reminder_timing", sa.String(8), nullable=False, server_default="both"),
    )
    op.create_check_constraint(
        "ck_teams_reminder_timing", "teams", "reminder_timing in ('1h', '30m', 'both')"
    )
    op.add_column(
        "user_settings",
        sa.Column("notify_reminders", sa.Boolean(), nullable=False, server_default=sa.true()),
    )

    op.execute(
        "create unique index uq_mail_outbox_series_agenda on mail_outbox "
        "((payload->>'send_id'), (payload->>'user_id')) where template = 'series_agenda'"
    )
    op.execute(
        "create unique index uq_mail_outbox_series_reminder on mail_outbox "
        "((payload->>'reminder_id')) where template = 'series_reminder'"
    )


def downgrade() -> None:
    op.execute("drop index if exists uq_mail_outbox_series_reminder")
    op.execute("drop index if exists uq_mail_outbox_series_agenda")
    op.drop_column("user_settings", "notify_reminders")
    op.drop_constraint("ck_teams_reminder_timing", "teams", type_="check")
    op.drop_column("teams", "reminder_timing")
    op.drop_column("teams", "reminders_on")
    op.drop_column("open_questions", "carry")
    op.drop_column("open_questions", "resolved_by")
    op.drop_table("series_reminders")
    op.drop_table("series_agenda_sends")
    op.drop_table("series_agenda_items")
    op.drop_index("ix_meetings_series_id", table_name="meetings")
    op.drop_column("meetings", "series_id")
    op.drop_table("series")
