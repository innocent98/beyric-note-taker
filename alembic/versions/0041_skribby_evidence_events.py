"""Who was speaking when, per the meeting platform: `skribby_segment_evidence.events`

Meeting 32 (3 people) came back from Skribby with only 2 speaker tags and some lines under the wrong
one. We could not tell whether the model's voice separation or Skribby's own name matching was at
fault. Skribby also records who started speaking and when, taken from the meeting platform itself
(`GET /bot/{id}?with-speaker-events=true`, https://skribby.io/docs/guides/speaker-timelines). This
adds a nullable JSONB column that keeps that answer next to the transcript evidence, so each tag's
name can be compared with the platform's own "who is speaking" signal.

Same row, same retention (7 days) and same deletion (the meeting, the account) as the rest of
`skribby_segment_evidence`; null when the events could not be fetched. Nothing else changes.

Revision ID: 0041
Revises: 0040
Create Date: 2026-11-11
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = "0041"
down_revision = "0040"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("skribby_segment_evidence", sa.Column("events", JSONB(), nullable=True))


def downgrade() -> None:
    op.drop_column("skribby_segment_evidence", "events")
