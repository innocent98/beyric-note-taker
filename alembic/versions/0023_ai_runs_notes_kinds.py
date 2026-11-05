"""ai_runs records the notes and speaker-labelling calls too

`ck_ai_runs_kind` widens from ('extract', 'translate') to also allow 'notes' and 'label', so the
ledger shows the whole model cost of a meeting, not only the AI workspace calls. Existing rows are
untouched. Downgrade deletes the rows of the two new kinds first, so the narrow check can return.

Revision ID: 0023
Revises: 0021
Create Date: 2026-11-05
"""

from alembic import op

revision = "0023"
down_revision = "0021"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_constraint("ck_ai_runs_kind", "ai_runs", type_="check")
    op.create_check_constraint(
        "ck_ai_runs_kind", "ai_runs", "kind in ('extract', 'translate', 'notes', 'label')"
    )


def downgrade() -> None:
    op.execute("delete from ai_runs where kind in ('notes', 'label')")
    op.drop_constraint("ck_ai_runs_kind", "ai_runs", type_="check")
    op.create_check_constraint("ck_ai_runs_kind", "ai_runs", "kind in ('extract', 'translate')")
