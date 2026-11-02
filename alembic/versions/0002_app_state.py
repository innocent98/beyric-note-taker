"""app_state table holding the session epoch

Revision ID: 0002
Revises: 0001
Create Date: 2026-11-02
"""

import sqlalchemy as sa
from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "app_state",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("session_epoch", sa.Integer(), nullable=False, server_default="0"),
        sa.CheckConstraint("id = 1", name="ck_app_state_single_row"),
    )
    op.execute("insert into app_state (id, session_epoch) values (1, 0)")


def downgrade() -> None:
    op.drop_table("app_state")
