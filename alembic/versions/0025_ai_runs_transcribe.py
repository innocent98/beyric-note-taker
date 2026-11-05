"""ai_runs records transcription, with the audio seconds

Adds the nullable `audio_seconds` column (the length of the audio sent for transcription, priced
per minute, not per token) and widens `ck_ai_runs_kind` to allow 'transcribe'. Existing rows are
untouched. Downgrade deletes the 'transcribe' rows first, so the narrow check can return.

Revision ID: 0025
Revises: 0024
Create Date: 2026-11-05
"""

import sqlalchemy as sa
from alembic import op

revision = "0025"
down_revision = "0024"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("ai_runs", sa.Column("audio_seconds", sa.Integer(), nullable=True))
    op.drop_constraint("ck_ai_runs_kind", "ai_runs", type_="check")
    op.create_check_constraint(
        "ck_ai_runs_kind",
        "ai_runs",
        "kind in ('extract', 'translate', 'notes', 'label', 'transcribe')",
    )


def downgrade() -> None:
    op.execute("delete from ai_runs where kind = 'transcribe'")
    op.drop_constraint("ck_ai_runs_kind", "ai_runs", type_="check")
    op.create_check_constraint(
        "ck_ai_runs_kind", "ai_runs", "kind in ('extract', 'translate', 'notes', 'label')"
    )
    op.drop_column("ai_runs", "audio_seconds")
