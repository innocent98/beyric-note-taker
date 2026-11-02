"""initial schema and seed templates

Revision ID: 0001
Revises:
Create Date: 2026-11-02
"""

import sqlalchemy as sa
from alembic import op

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None

SEED_TEMPLATES = [
    (
        "General",
        "Write a summary, key points, decisions, action items with owners, and open questions.",
    ),
    (
        "Client call",
        "Focus on what the client asked for, commitments made, deadlines, concerns raised, "
        "and next steps. Group action items by who owns them.",
    ),
    (
        "Team update",
        "Group by person or workstream: progress, blockers, and what is planned next. "
        "List decisions and action items at the end.",
    ),
    (
        "Interview",
        "Summarise the candidate's background, strengths, concerns, notable answers, "
        "and any follow-up questions. Do not give a hire or no-hire verdict.",
    ),
]


def upgrade() -> None:
    templates = op.create_table(
        "templates",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("name", sa.String(100), nullable=False, unique=True),
        sa.Column("instructions", sa.Text(), nullable=False),
    )
    op.create_table(
        "vocabulary_terms",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("term", sa.String(200), nullable=False, unique=True),
    )
    op.create_table(
        "meetings",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("url", sa.Text(), nullable=False),
        sa.Column("platform", sa.String(32), nullable=False),
        sa.Column("native_id", sa.String(200), nullable=False),
        sa.Column("template_id", sa.Integer(), sa.ForeignKey("templates.id"), nullable=False),
        sa.Column("vexa_meeting_id", sa.String(100)),
        sa.Column("status", sa.String(16), nullable=False, server_default="bot_sent"),
        sa.Column("step", sa.String(16), nullable=False, server_default="waiting"),
        sa.Column("failure_reason", sa.Text()),
        sa.Column("transcript", sa.Text()),
        sa.Column("speaker_transcript", sa.Text()),
        sa.Column("notes", sa.Text()),
        sa.Column("started_polling_at", sa.DateTime(timezone=True)),
        sa.Column("audio_deleted_at", sa.DateTime(timezone=True)),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.CheckConstraint(
            "status in ('bot_sent', 'processing', 'ready', 'failed')", name="ck_meetings_status"
        ),
        sa.CheckConstraint(
            "step in ('waiting', 'audio', 'transcribing', 'speakers', 'notes', 'done')",
            name="ck_meetings_step",
        ),
    )
    op.bulk_insert(templates, [{"name": n, "instructions": i} for n, i in SEED_TEMPLATES])


def downgrade() -> None:
    op.drop_table("meetings")
    op.drop_table("vocabulary_terms")
    op.drop_table("templates")
