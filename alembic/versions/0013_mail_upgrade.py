"""AI workspace W0.3: mail suppressions and the `skipped` outbox status

`mail_suppressions` holds addresses that must not be mailed again (a bounce, a complaint, an
unsubscribe), as a hash only: the sender checks it at send time, and the table holds no address
a person could be contacted at. A mail the sender decides not to send (suppressed, the meeting
gone, the owner leaving) is `skipped`, which is not a failure and is not an alert. A unique index
allows one `notes_ready` mail per meeting (a done notes row keeps its ids-only payload).

Revision ID: 0013
Revises: 0012
Create Date: 2026-11-04
"""

import sqlalchemy as sa
from alembic import op

revision = "0013"
down_revision = "0012"
branch_labels = None
depends_on = None

TIMESTAMP = sa.DateTime(timezone=True)


def upgrade() -> None:
    op.create_table(
        "mail_suppressions",
        sa.Column("email_hash", sa.String(64), primary_key=True),
        sa.Column("reason", sa.String(16), nullable=False),
        sa.Column("created_at", TIMESTAMP, nullable=False, server_default=sa.func.now()),
        sa.CheckConstraint(
            "reason in ('bounce', 'complaint', 'unsubscribed')", name="ck_mail_suppressions_reason"
        ),
    )
    op.drop_constraint("ck_mail_outbox_status", "mail_outbox", type_="check")
    op.create_check_constraint(
        "ck_mail_outbox_status",
        "mail_outbox",
        "status in ('pending', 'sent', 'failed', 'skipped')",
    )

    op.execute(
        "create unique index uq_mail_outbox_notes_ready_meeting on mail_outbox "
        "((payload->>'meeting_id')) where template = 'notes_ready'"
    )


def downgrade() -> None:
    op.execute("drop index if exists uq_mail_outbox_notes_ready_meeting")
    op.execute("update mail_outbox set status = 'failed' where status = 'skipped'")
    op.drop_constraint("ck_mail_outbox_status", "mail_outbox", type_="check")
    op.create_check_constraint(
        "ck_mail_outbox_status", "mail_outbox", "status in ('pending', 'sent', 'failed')"
    )
    op.drop_table("mail_suppressions")
