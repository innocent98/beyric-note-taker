"""Meeting recipes: `documents.failure_detail`

Why a draft whose structure was refused was refused, in the validator's own words: a short sentence
about positions and counts ("block 7: table row 3 has 5 cells for 4 columns"), never a word of a
meeting, a brief or the model's reply. Found by the first real-model run, where a draft failed as
`structure` with nothing to say why. Nullable, empty for every row that exists.

Downgrade drops the column.

Revision ID: 0061
Revises: 0060
Create Date: 2026-11-25
"""

import sqlalchemy as sa
from alembic import op

revision = "0061"
down_revision = "0060"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("documents", sa.Column("failure_detail", sa.String(200)))


def downgrade() -> None:
    op.drop_column("documents", "failure_detail")
