"""A ledger kind for the vocabulary pass: `ai_runs.kind = 'vocabulary'`

With `TRANSCRIPT_SOURCE=skribby` and a model that cannot take the owner's vocabulary
(`elevenlabs/scribe-v2`), a cheap model call corrects the spelling of the owner's own words in the
finished transcript (`app/vocabulary_pass.py`). It is real spend on a meeting the owner caused, so
it is written to the ledger like the labelling and the notes, with the meeting and the owner, as its
own kind so a cost view can tell it apart. `ck_ai_runs_kind` is widened; no table or column
changes, nothing is filled in for old rows.

Downgrade deletes the `vocabulary` rows first (the old constraint would refuse them).

Revision ID: 0042
Revises: 0041
Create Date: 2026-11-13
"""

from alembic import op

_KINDS_NEW = (
    "kind in ('extract', 'translate', 'notes', 'label', 'transcribe', 'ask', 'topics', "
    "'shadow', 'vocabulary')"
)
_KINDS_OLD = (
    "kind in ('extract', 'translate', 'notes', 'label', 'transcribe', 'ask', 'topics', 'shadow')"
)

revision = "0042"
down_revision = "0041"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_constraint("ck_ai_runs_kind", "ai_runs", type_="check")
    op.create_check_constraint("ck_ai_runs_kind", "ai_runs", _KINDS_NEW)


def downgrade() -> None:
    op.execute("delete from ai_runs where kind = 'vocabulary'")
    op.drop_constraint("ck_ai_runs_kind", "ai_runs", type_="check")
    op.create_check_constraint("ck_ai_runs_kind", "ai_runs", _KINDS_OLD)
