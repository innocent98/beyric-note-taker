"""recording id, speaker and completion flags, terminal time, worker heartbeat

Revision ID: 0003
Revises: 0002
Create Date: 2026-11-02
"""

import sqlalchemy as sa
from alembic import op

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("meetings", sa.Column("vexa_recording_id", sa.Integer(), nullable=True))
    op.add_column(
        "meetings",
        sa.Column("speakers_incomplete", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.add_column("meetings", sa.Column("completion_reason", sa.String(64), nullable=True))
    op.add_column(
        "meetings", sa.Column("terminal_seen_at", sa.DateTime(timezone=True), nullable=True)
    )
    op.add_column(
        "app_state", sa.Column("worker_heartbeat_at", sa.DateTime(timezone=True), nullable=True)
    )


def downgrade() -> None:
    op.drop_column("app_state", "worker_heartbeat_at")
    op.drop_column("meetings", "terminal_seen_at")
    op.drop_column("meetings", "completion_reason")
    op.drop_column("meetings", "speakers_incomplete")
    op.drop_column("meetings", "vexa_recording_id")
