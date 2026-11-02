"""store Vexa recording ids as bigint

Vexa's recording ids exceed 32 bits (676401909012 seen live on 2026-11-02), so saving one into
an integer column failed every meeting that reached the recording stage.

Revision ID: 0004
Revises: 0003
Create Date: 2026-11-02
"""

import sqlalchemy as sa
from alembic import op

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.alter_column(
        "meetings", "vexa_recording_id", type_=sa.BigInteger(), existing_type=sa.Integer()
    )


def downgrade() -> None:
    # Fails, and changes nothing, if a stored id no longer fits in an integer.
    op.alter_column(
        "meetings", "vexa_recording_id", type_=sa.Integer(), existing_type=sa.BigInteger()
    )
