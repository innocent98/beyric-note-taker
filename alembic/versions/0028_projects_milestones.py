"""W4 Projects: projects, milestones, and a project, milestone and "check project" flag on tasks

`projects` belong to a team and carry the name, colour and keywords tasks are filed by (spec 6.2).
`milestones` are dated goals; the extraction call may propose one from a meeting, stored with
`confirmed_at` null until someone confirms it (D14). `tasks` gets `project_id` and `milestone_id`
(both SET NULL, so deleting a project or milestone leaves its tasks with none) and `check_project`.
`milestones.project_id` cascades (a deleted project takes its milestones); `source_meeting_id` is
SET NULL and `services.delete_meeting` removes the meeting's proposals. Nothing is backfilled:
existing tasks keep no project.

Revision ID: 0028
Revises: 0026
Create Date: 2026-11-05
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0028"
down_revision = "0026"  # 0027 is Outlook M1 (#96): re-point when it merges
branch_labels = None
depends_on = None

TIMESTAMP = sa.DateTime(timezone=True)


def upgrade() -> None:
    op.create_table(
        "projects",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column(
            "team_id",
            sa.BigInteger(),
            sa.ForeignKey("teams.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("name", sa.String(100), nullable=False, server_default=""),
        sa.Column("color", sa.String(7), nullable=False),
        sa.Column(
            "keywords",
            postgresql.ARRAY(sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::text[]"),
        ),
        sa.Column("created_at", TIMESTAMP, nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_projects_team_id", "projects", ["team_id", "id"])
    op.create_table(
        "milestones",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column(
            "team_id",
            sa.BigInteger(),
            sa.ForeignKey("teams.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("project_id", sa.BigInteger(), sa.ForeignKey("projects.id", ondelete="CASCADE")),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("due_date", sa.Date(), nullable=False),
        sa.Column(
            "source_meeting_id", sa.Integer(), sa.ForeignKey("meetings.id", ondelete="SET NULL")
        ),
        sa.Column("quote", sa.String(300)),
        sa.Column("confirmed_at", TIMESTAMP),
        sa.Column("created_at", TIMESTAMP, nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_milestones_team_id", "milestones", ["team_id", "id"])
    op.create_index("ix_milestones_source_meeting_id", "milestones", ["source_meeting_id"])
    op.add_column(
        "tasks",
        sa.Column("project_id", sa.BigInteger(), sa.ForeignKey("projects.id", ondelete="SET NULL")),
    )
    op.add_column(
        "tasks",
        sa.Column(
            "milestone_id", sa.BigInteger(), sa.ForeignKey("milestones.id", ondelete="SET NULL")
        ),
    )
    op.add_column(
        "tasks",
        sa.Column("check_project", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.create_index("ix_tasks_project_id", "tasks", ["project_id"])
    op.create_index("ix_tasks_milestone_id", "tasks", ["milestone_id"])


def downgrade() -> None:
    op.drop_index("ix_tasks_milestone_id", table_name="tasks")
    op.drop_index("ix_tasks_project_id", table_name="tasks")
    op.drop_column("tasks", "check_project")
    op.drop_column("tasks", "milestone_id")
    op.drop_column("tasks", "project_id")
    op.drop_table("milestones")
    op.drop_table("projects")
