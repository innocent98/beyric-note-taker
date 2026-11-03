"""sign-up columns: a verification link's own password, and when an account was deleted

`auth_tokens.pending_password_hash`: a verification link confirms the password submitted with the
sign-up that made it, and the person must type that password to use it. Otherwise someone could
pre-register a victim's address with a password they know and wait for the victim to click a link.

`users.deleted_at`: when the admin deleted the account. The worker purges deleted accounts, waiting
for Vexa to settle; this starts the clock for giving up after a day (`app/deletion.py`).

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
    op.add_column("users", sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    op.drop_column("users", "deleted_at")
    op.drop_column("auth_tokens", "pending_password_hash")
