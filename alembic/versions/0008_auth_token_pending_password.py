"""bind a sign-up's password to its verification link

A verification link confirms the password submitted with the sign-up that made it, and the person
must type that password to use it. Otherwise someone could pre-register a victim's address with a
password they know and wait for the victim to click a link.

Revision ID: 0008
Revises: 0007
Create Date: 2026-11-03
"""

import sqlalchemy as sa
from alembic import op

revision = "0008"
down_revision = "0007"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("auth_tokens", sa.Column("pending_password_hash", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("auth_tokens", "pending_password_hash")
