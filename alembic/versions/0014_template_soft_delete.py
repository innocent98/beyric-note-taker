"""Templates can be deleted: `deleted_at`, and a name that is unique only among live templates

A deleted template is kept (its row is only marked), because meetings keep the id of the template
their notes were written with: the notes and the name the meeting shows survive, and a meeting that
is still being written finishes with the template it started with. Nothing that lists or chooses
templates sees a marked row. So that a deleted name can be used again, the unique constraint on
(owner, name) becomes a unique index over the live rows only (same name, so code that reads the
name of the constraint is unaffected). Additive: existing rows are live.

Downgrade renames each deleted template (" (deleted <id>)") so the plain constraint can come back.

Revision ID: 0014
Revises: 0013
Create Date: 2026-11-04
"""

import sqlalchemy as sa
from alembic import op

revision = "0014"
down_revision = "0013"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("templates", sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True))
    op.drop_constraint("uq_templates_owner_name", "templates", type_="unique")
    op.create_index(
        "uq_templates_owner_name",
        "templates",
        ["owner_user_id", "name"],
        unique=True,
        postgresql_where=sa.text("deleted_at is null"),
    )


def downgrade() -> None:
    op.execute(
        "update templates set name = left(name, 100 - length(' (deleted ' || id || ')'))"
        " || ' (deleted ' || id || ')' where deleted_at is not null"
    )
    op.drop_index("uq_templates_owner_name", table_name="templates")
    op.create_unique_constraint("uq_templates_owner_name", "templates", ["owner_user_id", "name"])
    op.drop_column("templates", "deleted_at")
