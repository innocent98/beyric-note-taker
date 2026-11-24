"""Meetings: a person's edits to the transcript (`transcript_original` and three more columns)

The transcript editor (rename a speaker, fix a line, move a line) changes the text the page, Ask
Beyric, the exports and the translations all read (`speaker_transcript`, else `transcript`). The
edit is reversible: the text as it was before the FIRST edit is kept once in `transcript_original`,
and "Restore original" puts it back and clears all three edit columns.

* `transcript_original`: null until the first edit; then the text the displayed column held.
* `transcript_edited_at` / `transcript_edited_by_user_id`: when and by whom the last edit was made
  (the user is nulled if the account is deleted).
* `notes_source_version`: the version (a 16-character hash) of the exact text the CURRENT notes
  were written from, set when the owner rewrites the notes and by the rebuild script. Null for
  every meeting already in the table, which is right: their notes were written from the transcript
  as it is before any edit. "The notes are out of date" is this against the shown text.

All four are nullable, so the upgrade is instant and nothing changes for anyone until a person
edits a transcript. Downgrade drops them: edits stay in the text, the original is lost (restore the
original first if it matters).

Revision ID: 0057
Revises: 0056
Create Date: 2026-11-24
"""

import sqlalchemy as sa
from alembic import op

revision = "0057"
down_revision = "0056"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("meetings", sa.Column("transcript_original", sa.Text(), nullable=True))
    op.add_column(
        "meetings", sa.Column("transcript_edited_at", sa.DateTime(timezone=True), nullable=True)
    )
    op.add_column(
        "meetings", sa.Column("transcript_edited_by_user_id", sa.BigInteger(), nullable=True)
    )
    op.create_foreign_key(
        "fk_meetings_transcript_edited_by_user_id",
        "meetings",
        "users",
        ["transcript_edited_by_user_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.add_column("meetings", sa.Column("notes_source_version", sa.String(16), nullable=True))


def downgrade() -> None:
    op.drop_column("meetings", "notes_source_version")
    op.drop_constraint("fk_meetings_transcript_edited_by_user_id", "meetings", type_="foreignkey")
    op.drop_column("meetings", "transcript_edited_by_user_id")
    op.drop_column("meetings", "transcript_edited_at")
    op.drop_column("meetings", "transcript_original")
