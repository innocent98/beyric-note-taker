"""What a transcript was made from: `meetings.transcript_source`, and the sampled shadow check's
numbers: `transcript_shadow_metrics`

`TRANSCRIPT_SOURCE=skribby` makes the notes from the bot provider's own transcript instead of our
OpenAI transcription of the audio (`app/transcript_source.py`), falling back to ours when
Skribby's is missing or too short. `meetings.transcript_source` records which one a meeting's
transcript came from (`own` or `skribby`); it is null for a meeting made before this migration (all
`own`) and until the transcript is saved. Nothing is filled in for old rows.

`transcript_shadow_metrics` holds, for a sampled share of meetings (`TRANSCRIPT_SHADOW_PERCENT`),
numbers comparing the two transcripts: word counts, the share of words in common, speakers, segments
with no name, audio seconds, which source the notes used. Numbers only, no transcript text, no name,
no email; the meeting is the one link and the row is deleted with it (`ON DELETE CASCADE`).

The shadow check's own OpenAI transcription is a new ledger kind, `shadow` (`ck_ai_runs_kind` is
widened): it is real spend, so cost totals count it, but it is not a meeting's transcript and it is
written with no `user_id` (the owner did not cause it), so a per-account view never charges them.

Revision ID: 0039
Revises: 0038
Create Date: 2026-11-09
"""

import sqlalchemy as sa
from alembic import op

_KINDS_NEW = (
    "kind in ('extract', 'translate', 'notes', 'label', 'transcribe', 'ask', 'topics', 'shadow')"
)
_KINDS_OLD = "kind in ('extract', 'translate', 'notes', 'label', 'transcribe', 'ask', 'topics')"

revision = "0039"
down_revision = "0038"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("meetings", sa.Column("transcript_source", sa.String(16)))
    op.create_table(
        "transcript_shadow_metrics",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column(
            "meeting_id",
            sa.Integer(),
            sa.ForeignKey("meetings.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("skribby_words", sa.Integer(), nullable=False),
        sa.Column("own_words", sa.Integer(), nullable=False),
        sa.Column("shared_words", sa.Integer(), nullable=False),
        sa.Column("shared_share", sa.Float(), nullable=False),
        sa.Column("skribby_speakers", sa.Integer(), nullable=False),
        sa.Column("unnamed_segments", sa.Integer(), nullable=False),
        sa.Column("segments", sa.Integer(), nullable=False),
        sa.Column("audio_seconds", sa.Integer(), nullable=False),
        sa.Column("notes_source", sa.String(16), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.UniqueConstraint("meeting_id", name="uq_transcript_shadow_metrics_meeting"),
    )
    op.drop_constraint("ck_ai_runs_kind", "ai_runs", type_="check")
    op.create_check_constraint("ck_ai_runs_kind", "ai_runs", _KINDS_NEW)


def downgrade() -> None:
    op.execute("delete from ai_runs where kind = 'shadow'")
    op.drop_constraint("ck_ai_runs_kind", "ai_runs", type_="check")
    op.create_check_constraint("ck_ai_runs_kind", "ai_runs", _KINDS_OLD)
    op.drop_table("transcript_shadow_metrics")
    op.drop_column("meetings", "transcript_source")
