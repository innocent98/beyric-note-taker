"""ownership: every meeting, template and vocabulary term belongs to a user; usage ledger

Safe on the live database: the new columns start nullable, every existing row is given to the
admin (the first admin user, created by 0006), and only then are they made NOT NULL. Template
names and vocabulary terms become unique per owner instead of globally. The admin's calendar
template points at their own "General" copy.

Downgrade restores the global unique names and drops the ownership columns and the usage ledger.
It FAILS (nothing changes, the migration is transactional) when two users hold a template with
the same name, or a vocabulary term the same: remove or rename one first. Usage entries are lost.

Revision ID: 0007
Revises: 0006
Create Date: 2026-11-03
"""

import sqlalchemy as sa
from alembic import op

revision = "0007"
down_revision = "0006"
branch_labels = None
depends_on = None

OWNED = ("meetings", "templates", "vocabulary_terms")


def upgrade() -> None:
    conn = op.get_bind()
    for table in OWNED:
        op.add_column(table, sa.Column("owner_user_id", sa.BigInteger(), nullable=True))
    admin_id = conn.execute(
        sa.text("select id from users where is_admin order by id limit 1")
    ).scalar_one_or_none()
    if admin_id is None:
        raise RuntimeError("migration 0007 needs the admin user that 0006 creates")
    for table in OWNED:
        conn.execute(sa.text(f"update {table} set owner_user_id = :id"), {"id": admin_id})  # noqa: S608
        op.alter_column(table, "owner_user_id", nullable=False)
        op.create_foreign_key(
            f"{table}_owner_user_id_fkey", table, "users", ["owner_user_id"], ["id"]
        )

    op.drop_constraint("templates_name_key", "templates", type_="unique")
    op.create_unique_constraint("uq_templates_owner_name", "templates", ["owner_user_id", "name"])
    op.drop_constraint("vocabulary_terms_term_key", "vocabulary_terms", type_="unique")
    op.create_unique_constraint(
        "uq_vocabulary_owner_term", "vocabulary_terms", ["owner_user_id", "term"]
    )
    op.create_index("ix_meetings_owner_user_id", "meetings", ["owner_user_id", "id"])

    op.add_column(
        "meetings", sa.Column("source", sa.String(16), nullable=False, server_default="manual")
    )
    op.create_check_constraint(
        "ck_meetings_source", "meetings", "source in ('manual', 'calendar', 'instant_meet')"
    )
    op.add_column("meetings", sa.Column("title", sa.Text(), nullable=True))
    op.add_column("meetings", sa.Column("recorded_seconds", sa.Integer(), nullable=True))

    op.create_table(
        "usage_entries",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column("user_id", sa.BigInteger(), sa.ForeignKey("users.id", ondelete="SET NULL")),
        sa.Column("email_hash", sa.String(64), nullable=False),
        sa.Column(
            "meeting_id",
            sa.Integer(),
            sa.ForeignKey("meetings.id", ondelete="SET NULL"),
            unique=True,
        ),
        sa.Column("seconds", sa.Integer(), nullable=False),
        sa.Column(
            "counted_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
    )
    op.create_index("ix_usage_entries_email_hash", "usage_entries", ["email_hash"])

    conn.execute(
        sa.text(
            "update user_settings set calendar_template_id = "
            "(select id from templates where owner_user_id = :id and name = 'General') "
            "where user_id = :id"
        ),
        {"id": admin_id},
    )


def downgrade() -> None:
    op.drop_table("usage_entries")
    op.drop_column("meetings", "recorded_seconds")
    op.drop_column("meetings", "title")
    op.drop_constraint("ck_meetings_source", "meetings", type_="check")
    op.drop_column("meetings", "source")
    op.drop_index("ix_meetings_owner_user_id", table_name="meetings")

    op.drop_constraint("uq_vocabulary_owner_term", "vocabulary_terms", type_="unique")
    op.create_unique_constraint("vocabulary_terms_term_key", "vocabulary_terms", ["term"])
    op.drop_constraint("uq_templates_owner_name", "templates", type_="unique")
    op.create_unique_constraint("templates_name_key", "templates", ["name"])

    # user_settings.calendar_template_id may point at templates: nothing to do, it stays valid.
    for table in OWNED:
        op.drop_constraint(f"{table}_owner_user_id_fkey", table, type_="foreignkey")
        op.drop_column(table, "owner_user_id")
