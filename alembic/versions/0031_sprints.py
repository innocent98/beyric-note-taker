"""W9 Sprints: sprints, the team's sprint settings, a project's own length, a task's sprint

`sprints` belong to a team, and to a project when that project has its own sprint length (a
second series). The team keeps its current and next sprint materialised (`app/sprints.py`); closed
ones stay as history. `teams` gets the length, the custom number of days and the Monday to Friday
switch; `projects.sprint_length` is the optional override. `tasks.sprint_id` is SET NULL (a task
whose sprint goes away is in the Backlog) and `tasks.carried` marks a task moved on by a closing
sprint. Nothing is backfilled: existing tasks are in the Backlog, and each team gets its first
two sprints the first time the scheduler's rollover job (or the board) looks at it.

Revision ID: 0031
Revises: 0028
Create Date: 2026-11-06
"""

import sqlalchemy as sa
from alembic import op

revision = "0031"
down_revision = "0028"  # re-point when W6 (0029) and W8 (0030) merge first
branch_labels = None
depends_on = None

TIMESTAMP = sa.DateTime(timezone=True)
LENGTHS = "('week5','week7','week14','month1','month3','month6','custom')"
PROJECT_LENGTHS = "('week5','week7','week14','month1','month3','month6')"


def upgrade() -> None:
    op.add_column(
        "teams",
        sa.Column("sprint_length", sa.String(8), nullable=False, server_default="week5"),
    )
    op.add_column(
        "teams",
        sa.Column("sprint_custom_days", sa.Integer(), nullable=False, server_default="10"),
    )
    op.add_column(
        "teams",
        sa.Column("sprint_align_weekdays", sa.Boolean(), nullable=False, server_default=sa.true()),
    )
    op.create_check_constraint("ck_teams_sprint_length", "teams", f"sprint_length in {LENGTHS}")
    op.create_check_constraint(
        "ck_teams_sprint_custom_days", "teams", "sprint_custom_days between 1 and 365"
    )
    op.add_column("projects", sa.Column("sprint_length", sa.String(8)))
    op.create_check_constraint(
        "ck_projects_sprint_length",
        "projects",
        f"sprint_length is null or sprint_length in {PROJECT_LENGTHS}",
    )
    op.create_table(
        "sprints",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column(
            "team_id",
            sa.BigInteger(),
            sa.ForeignKey("teams.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("project_id", sa.BigInteger(), sa.ForeignKey("projects.id", ondelete="CASCADE")),
        sa.Column("number", sa.Integer(), nullable=False),
        sa.Column("start_date", sa.Date(), nullable=False),
        sa.Column("end_date", sa.Date(), nullable=False),
        sa.Column("closed_at", TIMESTAMP),
        sa.Column("closed_early", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("created_at", TIMESTAMP, nullable=False, server_default=sa.func.now()),
        sa.CheckConstraint("end_date >= start_date", name="ck_sprints_dates"),
    )
    op.create_index("ix_sprints_team_id", "sprints", ["team_id", "project_id"])
    op.create_index(
        "uq_sprints_series_number",
        "sprints",
        ["team_id", sa.text("coalesce(project_id, 0)"), "number"],
        unique=True,
    )
    op.add_column(
        "tasks",
        sa.Column("sprint_id", sa.BigInteger(), sa.ForeignKey("sprints.id", ondelete="SET NULL")),
    )
    op.add_column(
        "tasks", sa.Column("carried", sa.Boolean(), nullable=False, server_default=sa.false())
    )
    op.create_index("ix_tasks_sprint_id", "tasks", ["sprint_id"])


def downgrade() -> None:
    op.drop_index("ix_tasks_sprint_id", table_name="tasks")
    op.drop_column("tasks", "carried")
    op.drop_column("tasks", "sprint_id")
    op.drop_table("sprints")
    op.drop_constraint("ck_projects_sprint_length", "projects")
    op.drop_column("projects", "sprint_length")
    op.drop_constraint("ck_teams_sprint_custom_days", "teams")
    op.drop_constraint("ck_teams_sprint_length", "teams")
    op.drop_column("teams", "sprint_align_weekdays")
    op.drop_column("teams", "sprint_custom_days")
    op.drop_column("teams", "sprint_length")
