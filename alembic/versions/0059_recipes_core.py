"""Meeting recipes: the tables and the `recipe` ledger kind

`documents` (a draft: validated blocks with sources, a status and the worker's claim),
`document_meetings` (the meetings a draft came from, cascading from both sides),
`document_shares` (a draft shared read-only with a team), `recipes` (personal or team recipes; the
ten starters are code constants) and `team_briefs` (a team's own reference text, one per team).
`ck_ai_runs_kind` is widened for the new kind `recipe`. No row is added: nothing exists to migrate.

This number is a placeholder (the integration branch `feature/recipes` is renumbered into one chain
when it is rebased onto `main`).

Downgrade deletes the `recipe` ledger rows first (the old constraint would refuse them), then drops
the tables; every draft, share, recipe and brief is lost.

Revision ID: 0059
Revises: 0058
Create Date: 2026-11-24
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

_KINDS_OLD = (
    "kind in ('extract', 'translate', 'notes', 'label', 'transcribe', 'ask', 'topics', "
    "'shadow', 'vocabulary')"
)
_KINDS_NEW = (
    "kind in ('extract', 'translate', 'notes', 'label', 'transcribe', 'ask', 'topics', "
    "'shadow', 'vocabulary', 'recipe')"
)
_OUTPUT_TYPES = "output_type in ('document', 'email', 'table', 'board')"

revision = "0059"
down_revision = "0058"
branch_labels = None
depends_on = None


def _now():
    return sa.func.now()


def upgrade() -> None:
    op.drop_constraint("ck_ai_runs_kind", "ai_runs", type_="check")
    op.create_check_constraint("ck_ai_runs_kind", "ai_runs", _KINDS_NEW)

    op.create_table(
        "recipes",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column(
            "owner_user_id",
            sa.BigInteger(),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("team_id", sa.BigInteger(), sa.ForeignKey("teams.id", ondelete="CASCADE")),
        sa.Column("key", sa.String(32)),
        sa.Column("name", sa.String(100), nullable=False),
        sa.Column("instruction", sa.Text(), nullable=False),
        sa.Column("output_type", sa.String(16), nullable=False),
        sa.Column(
            "options",
            postgresql.JSONB(),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column("scope", sa.String(8), server_default="one", nullable=False),
        sa.Column("uses_brief", sa.Boolean(), server_default=sa.true(), nullable=False),
        sa.Column("archived_at", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=_now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=_now(), nullable=False),
        sa.CheckConstraint(_OUTPUT_TYPES, name="ck_recipes_output_type"),
        sa.CheckConstraint("scope in ('one', 'many')", name="ck_recipes_scope"),
    )
    op.create_index("ix_recipes_owner_user_id", "recipes", ["owner_user_id"])
    op.create_index("ix_recipes_team_id", "recipes", ["team_id"])

    op.create_table(
        "documents",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column(
            "owner_user_id",
            sa.BigInteger(),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("team_id", sa.BigInteger(), sa.ForeignKey("teams.id", ondelete="SET NULL")),
        sa.Column("recipe_id", sa.BigInteger(), sa.ForeignKey("recipes.id", ondelete="SET NULL")),
        sa.Column("recipe_key", sa.String(32)),
        sa.Column("title", sa.String(200), nullable=False),
        sa.Column("output_type", sa.String(16), nullable=False),
        sa.Column("status", sa.String(16), server_default="writing", nullable=False),
        sa.Column(
            "blocks", postgresql.JSONB(), server_default=sa.text("'[]'::jsonb"), nullable=False
        ),
        sa.Column(
            "options", postgresql.JSONB(), server_default=sa.text("'{}'::jsonb"), nullable=False
        ),
        sa.Column("use_brief", sa.Boolean(), server_default=sa.true(), nullable=False),
        sa.Column("left_out_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("edited", sa.Boolean(), server_default=sa.false(), nullable=False),
        sa.Column("source_hash", sa.String(64)),
        sa.Column("model", sa.String(100)),
        sa.Column("prompt_version", sa.String(32)),
        sa.Column("failure_code", sa.String(32)),
        sa.Column("attempts", sa.Integer(), server_default="0", nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=_now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=_now(), nullable=False),
        sa.CheckConstraint("status in ('writing', 'ready', 'failed')", name="ck_documents_status"),
        sa.CheckConstraint(_OUTPUT_TYPES, name="ck_documents_output_type"),
    )
    op.create_index("ix_documents_owner_created", "documents", ["owner_user_id", "created_at"])
    op.create_index("ix_documents_status_started", "documents", ["status", "started_at"])

    op.create_table(
        "document_meetings",
        sa.Column(
            "document_id",
            sa.BigInteger(),
            sa.ForeignKey("documents.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column(
            "meeting_id",
            sa.Integer(),
            sa.ForeignKey("meetings.id", ondelete="CASCADE"),
            primary_key=True,
        ),
    )
    op.create_index("ix_document_meetings_meeting_id", "document_meetings", ["meeting_id"])

    op.create_table(
        "document_shares",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column(
            "document_id",
            sa.BigInteger(),
            sa.ForeignKey("documents.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "team_id",
            sa.BigInteger(),
            sa.ForeignKey("teams.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "shared_by_user_id",
            sa.BigInteger(),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=_now(), nullable=False),
        sa.UniqueConstraint("document_id", "team_id", name="uq_document_shares_document_team"),
    )
    op.create_index("ix_document_shares_team_id", "document_shares", ["team_id"])

    op.create_table(
        "team_briefs",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column(
            "team_id",
            sa.BigInteger(),
            sa.ForeignKey("teams.id", ondelete="CASCADE"),
            nullable=False,
            unique=True,
        ),
        sa.Column("text", sa.Text(), server_default="", nullable=False),
        sa.Column(
            "updated_by_user_id",
            sa.BigInteger(),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
        ),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=_now(), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("team_briefs")
    op.drop_table("document_shares")
    op.drop_table("document_meetings")
    op.drop_table("documents")
    op.drop_table("recipes")
    op.execute("delete from ai_runs where kind = 'recipe'")
    op.drop_constraint("ck_ai_runs_kind", "ai_runs", type_="check")
    op.create_check_constraint("ck_ai_runs_kind", "ai_runs", _KINDS_OLD)
