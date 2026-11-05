"""W6 Share notes by email: shares, their recipients, the opt-out token purpose

`meeting_shares` holds one request to mail a meeting's notes (language, attachment, transcript tick,
the sender's own message) and `meeting_share_recipients` the people it goes to. Neither holds any
meeting text: the notes are read when each mail is sent. Both go with the meeting (cascade).
`share_usage` is the daily limit's ledger (a count and a time per account, no address), kept apart
so deleting a meeting does not give capacity back. `auth_tokens.purpose` gains `mail_optout`: the
hashed token behind "Stop mail from Beyric Note Taker" in a shared-notes mail.

Revision ID: 0029
Revises: 0026
Create Date: 2026-11-05
"""

import sqlalchemy as sa
from alembic import op

revision = "0029"
down_revision = "0026"
branch_labels = None
depends_on = None

TIMESTAMP = sa.DateTime(timezone=True)
OLD = "purpose in ('verify_email', 'reset_password', 'waitlist_invite')"
NEW = "purpose in ('verify_email', 'reset_password', 'waitlist_invite', 'mail_optout')"


def upgrade() -> None:
    op.drop_constraint("ck_auth_tokens_purpose", "auth_tokens", type_="check")
    op.create_check_constraint("ck_auth_tokens_purpose", "auth_tokens", NEW)
    op.create_table(
        "meeting_shares",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column(
            "meeting_id",
            sa.Integer(),
            sa.ForeignKey("meetings.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "sender_user_id",
            sa.BigInteger(),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("language", sa.String(2), nullable=False),
        sa.Column("attach", sa.String(8), nullable=False, server_default="none"),
        sa.Column("include_transcript", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("message", sa.Text(), nullable=False, server_default=""),
        sa.Column("status", sa.String(24), nullable=False),
        sa.Column("created_at", TIMESTAMP, nullable=False, server_default=sa.func.now()),
        sa.Column("revoked_at", TIMESTAMP),
        sa.CheckConstraint(
            "status in ('waiting_translation', 'queued', 'failed', 'revoked')",
            name="ck_meeting_shares_status",
        ),
        sa.CheckConstraint("language in ('en', 'fr', 'es')", name="ck_meeting_shares_language"),
        sa.CheckConstraint("attach in ('none', 'pdf', 'docx')", name="ck_meeting_shares_attach"),
    )
    op.create_index("ix_meeting_shares_meeting_id", "meeting_shares", ["meeting_id", "id"])
    op.create_index(
        "ix_meeting_shares_sender_created", "meeting_shares", ["sender_user_id", "created_at"]
    )
    op.create_table(
        "meeting_share_recipients",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column(
            "share_id",
            sa.BigInteger(),
            sa.ForeignKey("meeting_shares.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("email", sa.String(254), nullable=False),
        sa.Column("name", sa.String(100)),
        sa.Column("user_id", sa.BigInteger(), sa.ForeignKey("users.id", ondelete="SET NULL")),
        sa.Column("status", sa.String(16), nullable=False, server_default="pending"),
        sa.Column("sent_at", TIMESTAMP),
        sa.CheckConstraint(
            "status in ('pending', 'sent', 'not_sent', 'failed')",
            name="ck_meeting_share_recipients_status",
        ),
        sa.CheckConstraint("email = lower(email)", name="ck_meeting_share_recipients_email_lower"),
        sa.UniqueConstraint("share_id", "email", name="uq_meeting_share_recipients_share_email"),
    )
    op.create_table(
        "share_usage",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column(
            "user_id",
            sa.BigInteger(),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("recipients", sa.Integer(), nullable=False),
        sa.Column("created_at", TIMESTAMP, nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_share_usage_user_created", "share_usage", ["user_id", "created_at"])
    # One "Notes shared with you" mail per recipient row, however many passes or retries.
    op.create_index(
        "uq_mail_outbox_notes_shared",
        "mail_outbox",
        [sa.text("(payload->>'share_id')"), sa.text("(payload->>'recipient_id')")],
        unique=True,
        postgresql_where=sa.text("template = 'notes_shared'"),
    )


def downgrade() -> None:
    op.drop_index("uq_mail_outbox_notes_shared", table_name="mail_outbox")
    op.drop_table("share_usage")
    op.drop_table("meeting_share_recipients")
    op.drop_table("meeting_shares")
    op.execute("delete from auth_tokens where purpose = 'mail_optout'")
    op.drop_constraint("ck_auth_tokens_purpose", "auth_tokens", type_="check")
    op.create_check_constraint("ck_auth_tokens_purpose", "auth_tokens", OLD)
