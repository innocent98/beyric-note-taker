"""W3 Team: invitations, the team's "share new meetings" default

`team_invites` holds one row per invitation to join a team. Only the SHA-256 hash of the link's
token is stored (the raw token is shown once, in the mail). A row is live while it is neither
accepted nor revoked and has not expired; a partial unique index allows one live row per team and
address (an expired one is reused by "resend"). `claimed_user_id` is the account created from the
link before its email is confirmed: it joins the team when the address is confirmed. A live
invitation to an address with no account holds a place of the pilot (`app/signup.slots_taken`), so
nothing here needs a counter. `teams.share_new_meetings` is the team default for "Share with team"
(off); it is read when a meeting's notes are first written, never when the bot joins.

Revision ID: 0025
Revises: 0024
Create Date: 2026-11-05
"""

import sqlalchemy as sa
from alembic import op

revision = "0025"
down_revision = "0024"
branch_labels = None
depends_on = None

TIMESTAMP = sa.DateTime(timezone=True)


def upgrade() -> None:
    op.add_column(
        "teams",
        sa.Column("share_new_meetings", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.create_table(
        "team_invites",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column(
            "team_id",
            sa.BigInteger(),
            sa.ForeignKey("teams.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("email", sa.String(254), nullable=False),
        sa.Column("name", sa.String(100)),
        sa.Column("role", sa.String(8), nullable=False, server_default="member"),
        sa.Column("token_hash", sa.String(64), nullable=False, unique=True),
        sa.Column("invited_by", sa.BigInteger(), sa.ForeignKey("users.id", ondelete="SET NULL")),
        sa.Column(
            "claimed_user_id", sa.BigInteger(), sa.ForeignKey("users.id", ondelete="SET NULL")
        ),
        sa.Column("expires_at", TIMESTAMP, nullable=False),
        sa.Column("accepted_at", TIMESTAMP),
        sa.Column("revoked_at", TIMESTAMP),
        sa.Column("last_sent_at", TIMESTAMP),
        sa.Column("created_at", TIMESTAMP, nullable=False, server_default=sa.func.now()),
        sa.CheckConstraint("role in ('admin', 'member')", name="ck_team_invites_role"),
        sa.CheckConstraint("email = lower(email)", name="ck_team_invites_email_lower"),
    )
    op.create_index("ix_team_invites_email", "team_invites", ["email"])
    op.create_index(
        "uq_team_invites_live",
        "team_invites",
        ["team_id", "email"],
        unique=True,
        postgresql_where=sa.text("accepted_at is null and revoked_at is null"),
    )


def downgrade() -> None:
    op.drop_table("team_invites")
    op.drop_column("teams", "share_new_meetings")
