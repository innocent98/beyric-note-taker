"""accounts: users, tokens, oauth flows, waitlist, notifications, mail outbox, settings

Creates the admin user from OWNER_EMAIL and OWNER_PASSWORD (read from the environment now, hashed
with argon2id), so the live owner keeps logging in, now with email plus password. In production
the migration refuses to run without both and rolls back; in development and test it makes a
placeholder admin that cannot log in. `app_state.signups_open` is false: nobody can sign up until
the admin opens the pilot.

Revision ID: 0006
Revises: 0005
Create Date: 2026-11-03
"""

import sqlalchemy as sa
from alembic import op
from argon2 import PasswordHasher
from sqlalchemy.dialects.postgresql import JSONB

from app.config import Settings

revision = "0006"
down_revision = "0005"
branch_labels = None
depends_on = None

DEV_ENVS = ("development", "test")
PLACEHOLDER_EMAIL = "owner@example.invalid"


def _in(column: str, values: tuple[str, ...]) -> str:
    return f"{column} in ({', '.join(repr(v) for v in values)})"


def _owner() -> tuple[str, str | None]:
    """The admin's email and password hash, from the environment."""
    settings = Settings()
    email = settings.owner_email.strip().lower()
    password = settings.owner_password
    if email and password:
        return email, PasswordHasher().hash(password)
    if settings.app_env in DEV_ENVS:
        return email or PLACEHOLDER_EMAIL, PasswordHasher().hash(password) if password else None
    raise RuntimeError(
        "OWNER_EMAIL and OWNER_PASSWORD must both be set to run migration 0006: it creates the "
        "admin account from them. Add them to the server .env and deploy again."
    )


def upgrade() -> None:
    email, password_hash = _owner()  # first, so a missing value fails before anything changes

    op.create_table(
        "users",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column("email", sa.String(254), nullable=False, unique=True),
        sa.Column("name", sa.String(200)),
        sa.Column("password_hash", sa.Text()),
        sa.Column("google_sub", sa.String(64), unique=True),
        sa.Column("email_verified_at", sa.DateTime(timezone=True)),
        sa.Column("is_admin", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("status", sa.String(16), nullable=False, server_default="active"),
        sa.Column("hours_cap_seconds", sa.Integer()),
        sa.Column("session_epoch", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("terms_accepted_at", sa.DateTime(timezone=True)),
        sa.Column("terms_version", sa.String(32)),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column("last_login_at", sa.DateTime(timezone=True)),
        sa.CheckConstraint(
            _in("status", ("active", "suspended", "deleted")), name="ck_users_status"
        ),
        sa.CheckConstraint("email = lower(email)", name="ck_users_email_lower"),
    )
    op.create_table(
        "auth_tokens",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column("user_id", sa.BigInteger(), sa.ForeignKey("users.id", ondelete="CASCADE")),
        sa.Column("email", sa.String(254), nullable=False),
        sa.Column("purpose", sa.String(32), nullable=False),
        sa.Column("token_hash", sa.String(64), nullable=False, unique=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("used_at", sa.DateTime(timezone=True)),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.CheckConstraint(
            _in("purpose", ("verify_email", "reset_password", "waitlist_invite")),
            name="ck_auth_tokens_purpose",
        ),
    )
    op.create_table(
        "oauth_flows",
        sa.Column("state_hash", sa.String(64), primary_key=True),
        sa.Column("purpose", sa.String(16), nullable=False),
        sa.Column("user_id", sa.BigInteger(), sa.ForeignKey("users.id", ondelete="CASCADE")),
        sa.Column("code_verifier", sa.String(128), nullable=False),
        sa.Column("nonce", sa.String(64), nullable=False),
        sa.Column("return_to", sa.String(500)),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            _in("purpose", ("signin", "calendar", "meet_scope")), name="ck_oauth_flows_purpose"
        ),
    )
    op.create_table(
        "waitlist_entries",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column("email", sa.String(254), nullable=False, unique=True),
        sa.Column("name", sa.String(200)),
        sa.Column("status", sa.String(16), nullable=False, server_default="waiting"),
        sa.Column("invited_at", sa.DateTime(timezone=True)),
        sa.Column(
            "invite_token_id",
            sa.BigInteger(),
            sa.ForeignKey("auth_tokens.id", ondelete="SET NULL"),
        ),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.CheckConstraint(
            _in("status", ("waiting", "invited", "joined", "removed")),
            name="ck_waitlist_entries_status",
        ),
        sa.CheckConstraint("email = lower(email)", name="ck_waitlist_entries_email_lower"),
    )
    op.create_table(
        "notifications",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column(
            "user_id",
            sa.BigInteger(),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("kind", sa.String(64), nullable=False),
        sa.Column("severity", sa.String(16), nullable=False, server_default="info"),
        sa.Column("title", sa.String(200), nullable=False),
        sa.Column("body", sa.Text(), nullable=False, server_default=""),
        sa.Column("meeting_id", sa.Integer(), sa.ForeignKey("meetings.id", ondelete="SET NULL")),
        sa.Column("dedupe_key", sa.String(200), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column("read_at", sa.DateTime(timezone=True)),
        sa.CheckConstraint(
            _in("severity", ("info", "warning", "error")), name="ck_notifications_severity"
        ),
        sa.UniqueConstraint("user_id", "dedupe_key", name="uq_notifications_user_dedupe"),
    )
    op.create_table(
        "mail_outbox",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column("to_email", sa.String(254), nullable=False),
        sa.Column("template", sa.String(64), nullable=False),
        sa.Column("payload", JSONB(), nullable=False, server_default="{}"),
        sa.Column("status", sa.String(16), nullable=False, server_default="pending"),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("last_error", sa.Text()),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column("sent_at", sa.DateTime(timezone=True)),
        sa.CheckConstraint(
            _in("status", ("pending", "sent", "failed")), name="ck_mail_outbox_status"
        ),
    )
    op.create_table(
        "user_settings",
        sa.Column(
            "user_id",
            sa.BigInteger(),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("auto_join_rule", sa.String(16), nullable=False, server_default="all"),
        sa.Column(
            "calendar_template_id",
            sa.Integer(),
            sa.ForeignKey("templates.id", ondelete="SET NULL"),
        ),
        sa.Column("join_instant_meets", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("instant_meet_mode_override", sa.String(16)),
        sa.Column("notify_email", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.CheckConstraint(
            _in("auto_join_rule", ("all", "organizer", "none")), name="ck_user_settings_rule"
        ),
        sa.CheckConstraint(
            "instant_meet_mode_override is null or "
            + _in("instant_meet_mode_override", ("events", "poll", "off")),
            name="ck_user_settings_instant_mode",
        ),
    )

    op.add_column(
        "app_state",
        sa.Column("signups_open", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.add_column("app_state", sa.Column("max_accounts", sa.Integer(), server_default="10"))
    op.add_column(
        "app_state", sa.Column("default_hours_cap_seconds", sa.Integer(), server_default="18000")
    )
    op.add_column(
        "app_state",
        sa.Column(
            "password_signup_enabled", sa.Boolean(), nullable=False, server_default=sa.false()
        ),
    )
    op.add_column(
        "app_state", sa.Column("scheduler_heartbeat_at", sa.DateTime(timezone=True), nullable=True)
    )

    conn = op.get_bind()
    admin_id = conn.execute(
        sa.text(
            "insert into users (email, password_hash, is_admin, email_verified_at) "
            "values (:email, :hash, true, now()) returning id"
        ),
        {"email": email, "hash": password_hash},
    ).scalar_one()
    conn.execute(sa.text("insert into user_settings (user_id) values (:id)"), {"id": admin_id})


def downgrade() -> None:
    for column in (
        "scheduler_heartbeat_at",
        "password_signup_enabled",
        "default_hours_cap_seconds",
        "max_accounts",
        "signups_open",
    ):
        op.drop_column("app_state", column)
    for table in (
        "user_settings",
        "mail_outbox",
        "notifications",
        "waitlist_entries",
        "oauth_flows",
        "auth_tokens",
        "users",
    ):
        op.drop_table(table)
