"""count the bots sent for a meeting

Zoom sometimes refuses the bot's browser, so the worker sends a new bot, up to a cap.

Revision ID: 0005
Revises: 0004
Create Date: 2026-11-02
"""

import sqlalchemy as sa
from alembic import op

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "meetings",
        sa.Column("join_attempts", sa.Integer(), nullable=False, server_default="1"),
    )


def downgrade() -> None:
    op.drop_column("meetings", "join_attempts")
