"""Keeps coming up: stored repeat topics per series, and the ledger kind `topics`

`series_topics` holds what the worker's one model call per series found: topics discussed in two or
more of the series' meetings without a decision. It is replaced as a set at each computation and
read as stored (a page load never calls the model). `meeting_ids` are the meetings the topic cites
and `source_meeting_ids` every meeting the model was shown. Every read filters them through what
the viewer may read, hides a topic with fewer than two left and, when the viewer cannot read all
the meetings the model saw, shows it only if its words are in the notes they can read.

Three columns on `series` drive the job: `topics_signature` (a hash of what the last successful
call was given, so an unchanged series costs no call), `topics_checked_at` (null means "look again
now"; a new meeting's notes, a meeting joining or leaving the series set it null) and
`topics_attempts` (failed calls since the input last changed; the job stops at three).
`ai_runs.kind` is widened to allow `topics`; no row is rewritten.

Revision ID: 0037
Revises: 0036
Create Date: 2026-11-08
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = "0037"
down_revision = "0036"
branch_labels = None
depends_on = None

_KINDS_NEW = "kind in ('extract', 'translate', 'notes', 'label', 'transcribe', 'ask', 'topics')"
_KINDS_OLD = "kind in ('extract', 'translate', 'notes', 'label', 'transcribe', 'ask')"


def upgrade() -> None:
    op.create_table(
        "series_topics",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column(
            "series_id",
            sa.BigInteger(),
            sa.ForeignKey("series.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("position", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("label", sa.String(60), nullable=False),
        sa.Column("agenda", sa.String(100), nullable=False),
        sa.Column("reason", sa.String(12), nullable=False),
        sa.Column("meeting_ids", JSONB(), nullable=False),
        sa.Column("source_meeting_ids", JSONB(), nullable=False, server_default="[]"),
        sa.Column(
            "computed_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.CheckConstraint(
            "reason in ('no_decision', 'no_owner', 'not_final')", name="ck_series_topics_reason"
        ),
    )
    op.create_index("ix_series_topics_series_id", "series_topics", ["series_id", "position"])
    op.add_column("series", sa.Column("topics_signature", sa.String(64)))
    op.add_column("series", sa.Column("topics_checked_at", sa.DateTime(timezone=True)))
    op.add_column(
        "series",
        sa.Column("topics_attempts", sa.Integer(), nullable=False, server_default="0"),
    )
    op.drop_constraint("ck_ai_runs_kind", "ai_runs", type_="check")
    op.create_check_constraint("ck_ai_runs_kind", "ai_runs", _KINDS_NEW)


def downgrade() -> None:
    op.execute("delete from ai_runs where kind = 'topics'")
    op.drop_constraint("ck_ai_runs_kind", "ai_runs", type_="check")
    op.create_check_constraint("ck_ai_runs_kind", "ai_runs", _KINDS_OLD)
    op.drop_column("series", "topics_attempts")
    op.drop_column("series", "topics_checked_at")
    op.drop_column("series", "topics_signature")
    op.drop_index("ix_series_topics_series_id", table_name="series_topics")
    op.drop_table("series_topics")
