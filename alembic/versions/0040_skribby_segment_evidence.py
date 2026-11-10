"""What Skribby sent for a meeting, kept 7 days: `skribby_segment_evidence`

Meetings 29 and 30 (2026-11-10) were labelled badly and there was no way to tell whether Skribby's
names or our alignment were at fault: the app deletes Skribby's copy minutes after the meeting and
keeps only our own text. This table keeps, per meeting, Skribby's transcript items (diarization
tag, `speaker_name`, the NAMES in `potential_speaker_names`, start, end, text) and the name derived
for each tag and why, written just before Skribby's copy is deleted.

It holds the words people said, so it is short-lived: the retention job that deletes audio deletes
these rows 7 days after they were written, the meeting's deletion cascades to them, and an account's
deletion removes them. Nothing reads them but `scripts/skribby_segments_report.py`.

Revision ID: 0040
Revises: 0039
Create Date: 2026-11-10
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = "0040"
down_revision = "0039"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "skribby_segment_evidence",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column(
            "meeting_id",
            sa.Integer(),
            sa.ForeignKey("meetings.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("items", JSONB(), nullable=False),
        sa.Column("tags", JSONB(), nullable=False),
        sa.Column("by_elimination", sa.Boolean(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.UniqueConstraint("meeting_id", name="uq_skribby_segment_evidence_meeting"),
    )
    op.create_index(
        "ix_skribby_segment_evidence_created_at", "skribby_segment_evidence", ["created_at"]
    )


def downgrade() -> None:
    op.drop_index("ix_skribby_segment_evidence_created_at", table_name="skribby_segment_evidence")
    op.drop_table("skribby_segment_evidence")
