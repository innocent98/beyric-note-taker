"""Decisions as a record: status, who decided, evidence, revisit date, replaced by, related tasks

`decisions.status` is `approved` (every old row), `proposed` (the model read the bullet as
suggested, not agreed), or `replaced` / `rejected` (set by hand only). `decided_by` and `quote` are
what the model said and the code checked against the transcript (null otherwise, never a guess).
`revisit_on` is a date a person chose. `edited_at` is set when a person changes status, revisit
date or replaced-by: from then on a re-extraction leaves all three alone. `replaced_by_id` points at
the decision that replaced this one (a person chose it; it goes null if that decision is deleted).
`decision_tasks` links a decision to the tasks that carry it out, written by the extraction from
the model's `related_actions` (a link row goes when either side does). No row is rewritten: old
decisions keep status `approved` and no decider.

Revision ID: 0036
Revises: 0035
Create Date: 2026-11-08
"""

import sqlalchemy as sa
from alembic import op

revision = "0036"
down_revision = "0035"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "decisions",
        sa.Column("status", sa.String(10), nullable=False, server_default="approved"),
    )
    op.add_column("decisions", sa.Column("decided_by", sa.String(100)))
    op.add_column("decisions", sa.Column("quote", sa.String(300)))
    op.add_column("decisions", sa.Column("revisit_on", sa.Date()))
    op.add_column("decisions", sa.Column("edited_at", sa.DateTime(timezone=True)))
    op.add_column(
        "decisions",
        sa.Column("replaced_by_id", sa.BigInteger(), nullable=True),
    )
    op.create_foreign_key(
        "fk_decisions_replaced_by_id",
        "decisions",
        "decisions",
        ["replaced_by_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_check_constraint(
        "ck_decisions_status",
        "decisions",
        "status in ('approved', 'proposed', 'replaced', 'rejected')",
    )
    op.create_index("ix_decisions_replaced_by_id", "decisions", ["replaced_by_id"])
    op.create_table(
        "decision_tasks",
        sa.Column(
            "decision_id",
            sa.BigInteger(),
            sa.ForeignKey("decisions.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column(
            "task_id",
            sa.BigInteger(),
            sa.ForeignKey("tasks.id", ondelete="CASCADE"),
            primary_key=True,
        ),
    )
    op.create_index("ix_decision_tasks_task_id", "decision_tasks", ["task_id"])


def downgrade() -> None:
    op.drop_index("ix_decision_tasks_task_id", table_name="decision_tasks")
    op.drop_table("decision_tasks")
    op.drop_index("ix_decisions_replaced_by_id", table_name="decisions")
    op.drop_constraint("ck_decisions_status", "decisions", type_="check")
    op.drop_constraint("fk_decisions_replaced_by_id", "decisions", type_="foreignkey")
    op.drop_column("decisions", "replaced_by_id")
    op.drop_column("decisions", "edited_at")
    op.drop_column("decisions", "revisit_on")
    op.drop_column("decisions", "quote")
    op.drop_column("decisions", "decided_by")
    op.drop_column("decisions", "status")
