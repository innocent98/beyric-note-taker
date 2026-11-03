"""AI workspace W1: the model-call ledger, decisions, open questions, review items

`ai_runs` records each model call of the workspace (tokens, status, never exception text) and
outlives its meeting. `decisions`, `open_questions` and `review_items` hold what code reads out of
a meeting's notes, and are deleted with the meeting. `meetings` gets the extraction state. Nothing
is backfilled here: existing meetings keep a null `extraction_status` until
`scripts/backfill_extraction.py` runs.

Revision ID: 0011
Revises: 0010
Create Date: 2026-11-03
"""

import sqlalchemy as sa
from alembic import op

revision = "0011"
down_revision = "0010"
branch_labels = None
depends_on = None

TIMESTAMP = sa.DateTime(timezone=True)


def _derived(name: str, *columns: sa.Column, unique: str, index: sa.Index | None = None) -> None:
    op.create_table(
        name,
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column(
            "meeting_id",
            sa.Integer(),
            sa.ForeignKey("meetings.id", ondelete="CASCADE"),
            nullable=False,
        ),
        *columns,
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("fingerprint", sa.String(64), nullable=False),
        sa.Column("created_at", TIMESTAMP, nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("meeting_id", "fingerprint", name=unique),
    )
    op.create_index(f"ix_{name}_meeting_id", name, ["meeting_id"])


def upgrade() -> None:
    op.create_table(
        "ai_runs",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column("meeting_id", sa.Integer(), sa.ForeignKey("meetings.id", ondelete="SET NULL")),
        sa.Column("kind", sa.String(16), nullable=False),
        sa.Column("model", sa.String(100), nullable=False),
        sa.Column("prompt_version", sa.String(32)),
        sa.Column("input_tokens", sa.Integer()),
        sa.Column("output_tokens", sa.Integer()),
        sa.Column("status", sa.String(16), nullable=False, server_default="ok"),
        sa.Column("error", sa.String(100)),
        sa.Column("created_at", TIMESTAMP, nullable=False, server_default=sa.func.now()),
        sa.CheckConstraint("kind in ('extract', 'translate')", name="ck_ai_runs_kind"),
        sa.CheckConstraint("status in ('ok', 'failed')", name="ck_ai_runs_status"),
    )
    op.create_index("ix_ai_runs_meeting_id", "ai_runs", ["meeting_id"])

    _derived(
        "decisions",
        sa.Column("hidden_at", TIMESTAMP),
        unique="uq_decisions_meeting_fp",
    )
    _derived(
        "open_questions",
        sa.Column("resolved_at", TIMESTAMP),
        unique="uq_open_questions_meeting_fp",
    )
    _derived(
        "review_items",
        sa.Column(
            "owner_user_id",
            sa.BigInteger(),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("heard_as", sa.String(100)),
        sa.Column("checked_at", TIMESTAMP),
        unique="uq_review_items_meeting_fp",
    )
    op.create_index(
        "ix_review_items_owner_checked", "review_items", ["owner_user_id", "checked_at"]
    )

    op.add_column("meetings", sa.Column("extraction_status", sa.String(16)))
    op.add_column(
        "meetings",
        sa.Column("extraction_attempts", sa.Integer(), nullable=False, server_default="0"),
    )
    op.add_column("meetings", sa.Column("extraction_attempted_at", TIMESTAMP))
    op.add_column("meetings", sa.Column("extraction_version", sa.String(32)))
    op.create_check_constraint(
        "ck_meetings_extraction_status",
        "meetings",
        "extraction_status is null or "
        "extraction_status in ('pending', 'running', 'done', 'failed', 'skipped')",
    )


def downgrade() -> None:
    op.drop_constraint("ck_meetings_extraction_status", "meetings", type_="check")
    for column in (
        "extraction_version",
        "extraction_attempted_at",
        "extraction_attempts",
        "extraction_status",
    ):
        op.drop_column("meetings", column)
    for table in ("review_items", "open_questions", "decisions", "ai_runs"):
        op.drop_table(table)
