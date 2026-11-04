"""AI workspace W7: translations of a meeting's notes and transcript, and a default language

`meeting_translations` holds one row per (meeting, language): the notes and transcript in French or
Spanish as a separate view (the original is never changed), the hash of the text that was
translated (so rewritten notes show as stale), and the worker's claim columns. A deleted meeting
takes its translations with it. `user_settings.default_language` is the language a meeting opens
in when a translation exists; `en` means the original.

Revision ID: 0014
Revises: 0013
Create Date: 2026-11-04
"""

import sqlalchemy as sa
from alembic import op

revision = "0014"
down_revision = "0013"
branch_labels = None
depends_on = None

TIMESTAMP = sa.DateTime(timezone=True)


def upgrade() -> None:
    op.create_table(
        "meeting_translations",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column(
            "meeting_id",
            sa.Integer(),
            sa.ForeignKey("meetings.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("language", sa.String(2), nullable=False),
        sa.Column("status", sa.String(16), nullable=False, server_default="translating"),
        sa.Column("notes_md", sa.Text()),
        sa.Column("transcript", sa.Text()),
        sa.Column("source_hash", sa.String(64), nullable=False),
        sa.Column("model", sa.String(100)),
        sa.Column("failure_code", sa.String(32)),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("started_at", TIMESTAMP),
        sa.Column("created_at", TIMESTAMP, nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", TIMESTAMP, nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("meeting_id", "language", name="uq_meeting_translations_meeting_lang"),
        sa.CheckConstraint(
            "status in ('translating', 'ready', 'failed')", name="ck_meeting_translations_status"
        ),
        sa.CheckConstraint(
            "language in ('en', 'fr', 'es')", name="ck_meeting_translations_language"
        ),
    )
    # Whose call a ledger row was, so the daily translation limit survives a deleted meeting.
    op.add_column(
        "ai_runs",
        sa.Column(
            "user_id",
            sa.BigInteger(),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )
    op.create_index("ix_ai_runs_user_id_created_at", "ai_runs", ["user_id", "created_at"])
    op.add_column(
        "user_settings",
        sa.Column("default_language", sa.String(2), nullable=False, server_default="en"),
    )
    op.create_check_constraint(
        "ck_user_settings_default_language",
        "user_settings",
        "default_language in ('en', 'fr', 'es')",
    )


def downgrade() -> None:
    op.drop_constraint("ck_user_settings_default_language", "user_settings", type_="check")
    op.drop_column("user_settings", "default_language")
    op.drop_index("ix_ai_runs_user_id_created_at", table_name="ai_runs")
    op.drop_column("ai_runs", "user_id")
    op.drop_table("meeting_translations")
