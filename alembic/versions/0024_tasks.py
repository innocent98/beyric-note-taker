"""W2 Tasks: the tasks table, the two Tasks settings, the quiet flag, the one-mail index

`tasks` holds the action items read from a meeting's notes and the tasks people type in. A task
belongs to a team and, for an action item, to its meeting: `meetings.id` is ON DELETE CASCADE, so a
deleted meeting takes its tasks with it (spec 3.3). `user_settings` gets the two switches of the
Settings > Tasks card (both on). `meetings.extraction_quiet` marks a meeting read by the backfill
or on demand, whose tasks are not New and are never mailed. The partial unique index on
`mail_outbox` is what makes "one mail per assignee per meeting" true however many workers or
passes run. Nothing is backfilled here: `scripts/backfill_extraction.py --tasks` queues the recent
meetings.

Revision ID: 0024
Revises: 0023
Create Date: 2026-11-05
"""

import sqlalchemy as sa
from alembic import op

revision = "0024"
down_revision = "0023"
branch_labels = None
depends_on = None

TIMESTAMP = sa.DateTime(timezone=True)


def upgrade() -> None:
    op.create_table(
        "tasks",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column(
            "team_id",
            sa.BigInteger(),
            sa.ForeignKey("teams.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("meeting_id", sa.Integer(), sa.ForeignKey("meetings.id", ondelete="CASCADE")),
        sa.Column("title", sa.String(200), nullable=False, server_default=""),
        sa.Column(
            "assignee_user_id", sa.BigInteger(), sa.ForeignKey("users.id", ondelete="SET NULL")
        ),
        sa.Column("spoken_owner", sa.String(100)),
        sa.Column("due_date", sa.Date()),
        sa.Column("due_phrase", sa.String(100)),
        sa.Column("status", sa.String(8), nullable=False, server_default="todo"),
        sa.Column("quote", sa.String(300)),
        sa.Column("is_new", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("unverified", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("source", sa.String(8), nullable=False, server_default="manual"),
        sa.Column("ai_fingerprint", sa.String(64)),
        sa.Column("position", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("edited_at", TIMESTAMP),
        sa.Column("assigned_on", sa.Date()),
        sa.Column("done_at", TIMESTAMP),
        sa.Column(
            "created_by_user_id", sa.BigInteger(), sa.ForeignKey("users.id", ondelete="SET NULL")
        ),
        sa.Column("deleted_at", TIMESTAMP),
        sa.Column("created_at", TIMESTAMP, nullable=False, server_default=sa.func.now()),
        sa.CheckConstraint("status in ('todo', 'doing', 'done')", name="ck_tasks_status"),
        sa.CheckConstraint("source in ('ai', 'manual')", name="ck_tasks_source"),
    )
    op.create_index("ix_tasks_meeting_id", "tasks", ["meeting_id"])
    op.create_index("ix_tasks_team_id", "tasks", ["team_id", "id"])
    op.create_index("ix_tasks_assignee_user_id", "tasks", ["assignee_user_id"])
    op.create_index(
        "uq_tasks_meeting_fingerprint",
        "tasks",
        ["meeting_id", "ai_fingerprint"],
        unique=True,
        postgresql_where=sa.text("meeting_id is not null and ai_fingerprint is not null"),
    )
    op.add_column(
        "user_settings",
        sa.Column("auto_create_tasks", sa.Boolean(), nullable=False, server_default=sa.true()),
    )
    op.add_column(
        "user_settings",
        sa.Column("email_assignees", sa.Boolean(), nullable=False, server_default=sa.true()),
    )
    op.add_column(
        "meetings",
        sa.Column("extraction_quiet", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.execute(
        "create unique index uq_mail_outbox_tasks_assigned on mail_outbox "
        "((payload->>'meeting_id'), (payload->>'user_id')) where template = 'tasks_assigned'"
    )


def downgrade() -> None:
    op.execute("drop index if exists uq_mail_outbox_tasks_assigned")
    op.drop_column("meetings", "extraction_quiet")
    op.drop_column("user_settings", "email_assignees")
    op.drop_column("user_settings", "auto_create_tasks")
    op.drop_table("tasks")
