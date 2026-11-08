"""Suggested tasks: action items the AI is unsure about wait for a person

`tasks.suggested` marks a task that waits under "Suggested tasks" until a person accepts it (or
dismisses it, which is the existing delete and keeps its tombstone). `tasks.confidence` is what the
model said about the item (`high`, `medium` or `low`; null for a task made before extract-v2 or by
hand) and `tasks.reason` is the model's plain words on why an unclear item is unclear (only for
`medium` and `low`). `user_settings.ask_before_unclear` is the switch in Settings > Tasks, on for
everyone. No row is rewritten: old tasks keep `suggested` false and `confidence` null.

Revision ID: 0035
Revises: 0034
Create Date: 2026-11-08
"""

import sqlalchemy as sa
from alembic import op

revision = "0035"
down_revision = "0034"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "tasks",
        sa.Column("suggested", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.add_column("tasks", sa.Column("confidence", sa.String(8)))
    op.add_column("tasks", sa.Column("reason", sa.String(200)))
    op.create_check_constraint(
        "ck_tasks_confidence",
        "tasks",
        "confidence is null or confidence in ('high', 'medium', 'low')",
    )
    op.add_column(
        "user_settings",
        sa.Column("ask_before_unclear", sa.Boolean(), nullable=False, server_default=sa.true()),
    )


def downgrade() -> None:
    op.drop_column("user_settings", "ask_before_unclear")
    op.drop_constraint("ck_tasks_confidence", "tasks", type_="check")
    op.drop_column("tasks", "reason")
    op.drop_column("tasks", "confidence")
    op.drop_column("tasks", "suggested")
