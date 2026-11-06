"""W10 Integrations, part 2: Linear as the team's tracker

`integrations.kind` may now be `linear`; `integrations.label` holds a non-secret name to show (the
Linear team's name). `tracker_issues` records the issue a task became (written before the call
with `external_id` null as the claim; `task_id` is unique, so a task is pushed once).

Revision ID: 0033
Revises: 0032
Create Date: 2026-11-06
"""

import sqlalchemy as sa
from alembic import op

revision = "0033"
down_revision = "0032"
branch_labels = None
depends_on = None

TIMESTAMP = sa.DateTime(timezone=True)


def upgrade() -> None:
    op.add_column("integrations", sa.Column("label", sa.String(100)))
    op.drop_constraint("ck_integrations_kind", "integrations", type_="check")
    op.create_check_constraint(
        "ck_integrations_kind", "integrations", "kind in ('slack_webhook', 'linear')"
    )
    op.create_table(
        "tracker_issues",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column(
            "task_id",
            sa.BigInteger(),
            sa.ForeignKey("tasks.id", ondelete="CASCADE"),
            nullable=False,
            unique=True,
        ),
        sa.Column(
            "team_id",
            sa.BigInteger(),
            sa.ForeignKey("teams.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("kind", sa.String(24), nullable=False),
        sa.Column("external_id", sa.String(64)),
        sa.Column("identifier", sa.String(30)),
        sa.Column("url", sa.String(300)),
        sa.Column("created_at", TIMESTAMP, nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_tracker_issues_team_created", "tracker_issues", ["team_id", "created_at"])


def downgrade() -> None:
    op.drop_table("tracker_issues")
    op.execute("delete from integrations where kind = 'linear'")
    op.drop_constraint("ck_integrations_kind", "integrations", type_="check")
    op.create_check_constraint("ck_integrations_kind", "integrations", "kind in ('slack_webhook')")
    op.drop_column("integrations", "label")
