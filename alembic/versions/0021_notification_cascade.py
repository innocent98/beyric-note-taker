"""A notification about a meeting goes with the meeting

`notifications.meeting_id` was `ON DELETE SET NULL`, which would leave a notification whose title
and body name a deleted meeting. Deleting a meeting (W0.5) must take them with it, so the foreign
key now cascades. No data moves.

Revision ID: 0021
Revises: 0020 (re-point to 0020 once the calendar migration is on main)
Create Date: 2026-11-05
"""

from alembic import op

revision = "0021"
down_revision = "0020"
branch_labels = None
depends_on = None

_FK = "notifications_meeting_id_fkey"


def upgrade() -> None:
    op.drop_constraint(_FK, "notifications", type_="foreignkey")
    op.create_foreign_key(
        _FK, "notifications", "meetings", ["meeting_id"], ["id"], ondelete="CASCADE"
    )


def downgrade() -> None:
    op.drop_constraint(_FK, "notifications", type_="foreignkey")
    op.create_foreign_key(
        _FK, "notifications", "meetings", ["meeting_id"], ["id"], ondelete="SET NULL"
    )
