"""Workspaces settings (slice W2): templates and vocabulary belong to the workspace, not the author

Spec: docs/superpowers/specs/2026-11-24-workspaces-design.md (sections 3 and 12.5). Migration 0062
gave every template and vocabulary term a workspace and kept the uniqueness per author (a person's
templates were theirs). From now on a workspace's templates and terms are shared by its members, so
the uniqueness is per workspace:

* `templates.team_id` becomes NOT NULL. 0062 backfilled every row, so nothing is null; the
  migration refuses to go on if one is, rather than guess.
* a name is unique among a workspace's live templates (`uq_templates_team_name`), whoever wrote it.
  The cutover copied several members' templates into one workspace, so the same name can exist
  more than once there. Of such rows the lowest id stays. A later one with the SAME instructions is
  retired (`deleted_at`, because a meeting may name it) and what pointed at it moves to the one that
  stays; a later one with DIFFERENT instructions is renamed "Name (2)", "Name (3)" so nothing a
  person wrote is lost or silently changed.
* a term is unique in a workspace (`uq_vocabulary_team_term`): the later copy of a term goes (same
  text, nothing is lost).
* `meetings.kept_private` (default false): the recorder pressed "keep private" on a meeting born
  shared (decision D4). Nothing may share it again by default; existing meetings are false, so
  nothing about them changes.

`teams.share_new_meetings` is not touched: each existing team keeps its stored value (new teams are
made with it on by the code, `teams.create_owned_team`). Existing meetings are not touched.

Downgrade restores the per-author uniqueness. Rows retired or renamed here stay retired or renamed.
No data is lost in either direction; nothing here needs a lock longer than the index builds.

Revision ID: 0063
Revises: 0062
"""

import sqlalchemy as sa
from alembic import op

revision = "0063"
down_revision = "0062"
branch_labels = None
depends_on = None

NAME_LIMIT = 100


def _free_name(bind, team_id: int, name: str, number: int) -> str:
    """ "Name (2)" or the next number free in the workspace, within the column's length."""
    while True:
        suffix = f" ({number})"
        candidate = name[: NAME_LIMIT - len(suffix)] + suffix
        taken = bind.execute(
            sa.text(
                "select 1 from templates where team_id = :t and name = :n and deleted_at is null"
            ),
            {"t": team_id, "n": candidate},
        ).first()
        if taken is None:
            return candidate
        number += 1


def upgrade() -> None:
    bind = op.get_bind()

    missing = bind.execute(sa.text("select count(*) from templates where team_id is null")).scalar()
    if missing:
        raise RuntimeError(f"{missing} templates have no workspace: 0062 should have filled them")

    groups = bind.execute(
        sa.text(
            "select team_id, name from templates where deleted_at is null "
            "group by team_id, name having count(*) > 1"
        )
    ).all()
    for team_id, name in groups:
        rows = bind.execute(
            sa.text(
                "select id, instructions from templates "
                "where team_id = :t and name = :n and deleted_at is null order by id"
            ),
            {"t": team_id, "n": name},
        ).all()
        keeper_id, keeper_text = rows[0]
        number = 2
        for dup_id, instructions in rows[1:]:
            if instructions == keeper_text:
                bind.execute(
                    sa.text("update templates set deleted_at = now() where id = :i"),
                    {"i": dup_id},
                )
                for table in ("workspace_prefs", "scheduled_joins"):
                    bind.execute(
                        sa.text(  # noqa: S608
                            f"update {table} set "
                            + (
                                "calendar_template_id"
                                if table == "workspace_prefs"
                                else "template_id"
                            )
                            + " = :k where "
                            + (
                                "calendar_template_id"
                                if table == "workspace_prefs"
                                else "template_id"
                            )
                            + " = :d"
                        ),
                        {"k": keeper_id, "d": dup_id},
                    )
            else:
                renamed = _free_name(bind, team_id, name, number)
                number += 1
                bind.execute(
                    sa.text("update templates set name = :n where id = :i"),
                    {"n": renamed, "i": dup_id},
                )

    op.alter_column("templates", "team_id", nullable=False)
    op.drop_index("uq_templates_owner_team_name", table_name="templates")
    op.create_index(
        "uq_templates_team_name",
        "templates",
        ["team_id", "name"],
        unique=True,
        postgresql_where=sa.text("deleted_at is null"),
    )

    op.add_column(
        "meetings",
        sa.Column("kept_private", sa.Boolean(), nullable=False, server_default=sa.false()),
    )

    op.execute(
        """
        delete from vocabulary_terms a using vocabulary_terms b
        where a.team_id = b.team_id and a.term = b.term and a.id > b.id
        """
    )
    op.drop_constraint("uq_vocabulary_owner_team_term", "vocabulary_terms", type_="unique")
    op.create_unique_constraint("uq_vocabulary_team_term", "vocabulary_terms", ["team_id", "term"])


def downgrade() -> None:
    op.drop_column("meetings", "kept_private")
    op.drop_constraint("uq_vocabulary_team_term", "vocabulary_terms", type_="unique")
    op.create_unique_constraint(
        "uq_vocabulary_owner_team_term", "vocabulary_terms", ["owner_user_id", "team_id", "term"]
    )
    op.drop_index("uq_templates_team_name", table_name="templates")
    op.create_index(
        "uq_templates_owner_team_name",
        "templates",
        ["owner_user_id", sa.text("coalesce(team_id, 0)"), "name"],
        unique=True,
        postgresql_where=sa.text("deleted_at is null"),
    )
    op.alter_column("templates", "team_id", nullable=True)
