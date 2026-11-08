"""ai_runs records Ask Beyric questions

Widens `ck_ai_runs_kind` to allow 'ask' (one row per model call made to answer a question on the
Ask Beyric page; the daily cap counts them) and `ck_ai_runs_status` to allow 'pending' (the row is
written before the model is called, so no connection is held during the call, and a crash leaves
a row that still counts). No column changes. Downgrade deletes the 'ask' rows first (the only
kind that is ever pending), so the narrow checks can return.

Revision ID: 0034
Revises: 0033
Create Date: 2026-11-08
"""

from alembic import op

revision = "0034"
down_revision = "0033"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_constraint("ck_ai_runs_kind", "ai_runs", type_="check")
    op.create_check_constraint(
        "ck_ai_runs_kind",
        "ai_runs",
        "kind in ('extract', 'translate', 'notes', 'label', 'transcribe', 'ask')",
    )
    op.drop_constraint("ck_ai_runs_status", "ai_runs", type_="check")
    op.create_check_constraint(
        "ck_ai_runs_status", "ai_runs", "status in ('ok', 'failed', 'pending')"
    )


def downgrade() -> None:
    op.execute("delete from ai_runs where kind = 'ask'")
    op.drop_constraint("ck_ai_runs_status", "ai_runs", type_="check")
    op.create_check_constraint("ck_ai_runs_status", "ai_runs", "status in ('ok', 'failed')")
    op.drop_constraint("ck_ai_runs_kind", "ai_runs", type_="check")
    op.create_check_constraint(
        "ck_ai_runs_kind",
        "ai_runs",
        "kind in ('extract', 'translate', 'notes', 'label', 'transcribe')",
    )
