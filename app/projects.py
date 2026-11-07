"""Projects and milestones (W4, spec 4, 6.2, 6.3, 8.2, D14).

Three things live here:

* **Filing a task into a project** is `app/filing.py` (code, never the model), used by the task
  reconciler and by `propose` below.
* **The projects of a team** (`create`, `update`, `delete`, counts and the headline milestone).
  Every member reads them, an owner or admin changes them. A project of another team is "no such
  project" (404); a member who may not change it gets 403 `team_forbidden`. The counts inside a
  project follow what the viewer may see (`tasks.visible`).
* **Milestones.** The extraction call may *propose* one (`propose`): stored with `confirmed_at`
  null and the words said, never created on its own. A proposal is shown on the meeting page and
  confirmed by an owner or admin; only confirmed milestones appear on the Tasks and Projects
  screens or can be given to a task.
"""

from datetime import UTC, date, datetime

from sqlalchemy import and_, func, select
from sqlalchemy.orm import Session

from app import filing, sprints, tasks, titles
from app.extraction import CheckedMilestone, normalise
from app.models import Meeting, Milestone, Project, Series, Task, Team, User

# The six swatches the Projects screen draws (the designer's PCOLORS).
PALETTE = ("#0E6B5E", "#3C4A7A", "#8A4B00", "#8A2E5C", "#1F6FB2", "#4A524E")
MAX_NAME_CHARS = 100
MAX_KEYWORDS = 20
MAX_KEYWORD_CHARS = 40
MAX_MILESTONE_CHARS = 200
MAX_QUOTE_CHARS = 300
MAX_PROJECTS = 50  # per team: filing costs projects x keywords per task
MAX_PROPOSALS = 5  # per meeting: a long meeting that names many dates is not a list of goals


class ProjectInvalid(Exception):
    """The change is not valid; `code` and `message` are safe to show."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code, self.message = code, message


# ---- keywords ----


def clean_keywords(raw: list[str]) -> list[str]:
    """Plain, trimmed, one line each, no duplicates (case-insensitive), at most `MAX_KEYWORDS`.
    Junk and empty entries are dropped; too many or too long is refused."""
    out: list[str] = []
    seen: set[str] = set()
    for word in raw:
        text = " ".join(titles.clean_title(word, MAX_KEYWORD_CHARS + 1).split())
        if not text:
            continue
        if len(text) > MAX_KEYWORD_CHARS:
            raise ProjectInvalid(
                "keyword_too_long", f"A keyword can be at most {MAX_KEYWORD_CHARS} characters."
            )
        if text.casefold() in seen:
            continue
        seen.add(text.casefold())
        out.append(text)
    if len(out) > MAX_KEYWORDS:
        raise ProjectInvalid(
            "too_many_keywords", f"A project can have at most {MAX_KEYWORDS} keywords."
        )
    return out


def clean_name(raw: str) -> str:
    return titles.clean_title(raw, MAX_NAME_CHARS)


# ---- who may do what ----


def project_of_team(session: Session, team_id: int, project_id: int) -> Project | None:
    return session.scalars(
        select(Project).where(Project.id == project_id, Project.team_id == team_id)
    ).first()


def milestone_of_team(session: Session, team_id: int, milestone_id: int) -> Milestone | None:
    return session.scalars(
        select(Milestone).where(Milestone.id == milestone_id, Milestone.team_id == team_id)
    ).first()


# ---- projects ----


def next_colour(count: int) -> str:
    return PALETTE[count % len(PALETTE)]


def create(
    session: Session, team_id: int, *, name: str, color: str | None, keywords: list[str]
) -> Project:
    existing = session.scalar(
        select(func.count()).select_from(Project).where(Project.team_id == team_id)
    )
    if existing >= MAX_PROJECTS:
        raise ProjectInvalid(
            "too_many_projects", f"A team can have at most {MAX_PROJECTS} projects."
        )
    project = Project(
        team_id=team_id,
        name=clean_name(name),
        color=color or next_colour(existing),
        keywords=clean_keywords(keywords),
    )
    session.add(project)
    session.commit()
    return project


def update(session: Session, project: Project, changes: dict) -> Project:
    if "name" in changes:
        project.name = clean_name(changes["name"])
    if "color" in changes:
        project.color = changes["color"]
    if "keywords" in changes:
        project.keywords = clean_keywords(changes["keywords"])
    if "sprint_length" in changes:
        # Null follows the team's length; anything else is the project's own (W9).
        team = session.get(Team, project.team_id)
        sprints.set_project_length(session, team, project, changes["sprint_length"])
    session.commit()
    return project


def delete(session: Session, project: Project) -> None:
    """The project and its milestones go; its tasks stay with no project (the foreign keys are
    SET NULL). A task that was waiting for a project check no longer needs one.

    Locks in the order `series.link_project` does: the project row first (FOR UPDATE here, FOR
    SHARE there), then the series rows. A link and a delete of the same project therefore queue
    behind each other instead of crossing (which would abort one of them with a deadlock), and a
    link that waited finds the project gone (404) or is cleared by the delete that follows it."""
    session.scalars(select(Project.id).where(Project.id == project.id).with_for_update()).first()
    session.query(Task).filter(Task.project_id == project.id).update(
        {Task.check_project: False}, synchronize_session=False
    )
    # The recurring meetings linked to it stay and are no longer linked (`series.project_id` has no
    # foreign key to cascade).
    session.query(Series).filter(Series.project_id == project.id).update(
        {Series.project_id: None}, synchronize_session=False
    )
    # Its tasks stay: those planned in its own sprints follow into the team's (W9).
    sprints.release_project(session, session.get(Team, project.team_id), project)
    session.delete(project)
    session.commit()


def open_task_counts(session: Session, user: User, project_ids: list[int]) -> dict[int, int]:
    """Open tasks per project that the viewer may see."""
    if not project_ids:
        return {}
    rows = session.execute(
        select(Task.project_id, func.count())
        .where(Task.project_id.in_(project_ids), Task.status != "done", tasks.visible(user))
        .group_by(Task.project_id)
    )
    return {pid: n for pid, n in rows}


def sprint_counts(
    session: Session, user: User, team_id: int, project_ids: list[int]
) -> dict[int, tuple[int, int]]:
    """`(in the current sprint, in the Backlog)` per project: open tasks the viewer may see. A
    project's tasks are only ever in the series it uses, so "the current sprint" is whichever
    series' current sprint holds them."""
    if not project_ids:
        return {}
    current = sprints.role_ids(session, team_id)["current"]
    rows = session.execute(
        select(
            Task.project_id,
            func.count().filter(Task.sprint_id.in_(current)),
            func.count().filter(Task.sprint_id.is_(None)),
        )
        .where(Task.project_id.in_(project_ids), Task.status != "done", tasks.visible(user))
        .group_by(Task.project_id)
    )
    return {pid: (in_current, backlog) for pid, in_current, backlog in rows}


def milestone_progress(session: Session, user: User, ids: list[int]) -> dict[int, tuple[int, int]]:
    """`(tasks, done)` per milestone, over the tasks the viewer may see."""
    if not ids:
        return {}
    rows = session.execute(
        select(
            Task.milestone_id,
            func.count(),
            func.count().filter(Task.status == "done"),
        )
        .where(Task.milestone_id.in_(ids), tasks.visible(user))
        .group_by(Task.milestone_id)
    )
    return {mid: (total, done) for mid, total, done in rows}


def confirmed_milestones(
    session: Session, team_id: int, *, project_id: int | None = None
) -> list[Milestone]:
    query = select(Milestone).where(
        Milestone.team_id == team_id, Milestone.confirmed_at.is_not(None)
    )
    if project_id is not None:
        query = query.where(Milestone.project_id == project_id)
    return list(session.scalars(query.order_by(Milestone.due_date, Milestone.id)))


def headline_milestone(milestones: list[Milestone], today: date) -> Milestone | None:
    """The one line on a project: the next milestone still ahead, else the latest that passed."""
    ahead = [m for m in milestones if m.due_date >= today]
    if ahead:
        return min(ahead, key=lambda m: (m.due_date, m.id))
    return max(milestones, key=lambda m: (m.due_date, m.id), default=None)


# ---- milestone proposals (D14) ----


def propose(
    session: Session,
    meeting: Meeting,
    team_id: int,
    proposals: list[CheckedMilestone],
    projects: list[Project],
    meeting_title: str,
) -> list[Milestone]:
    """Make the meeting's proposals match what the model found (already checked: real date, quote
    in the transcript). A rewrite of the notes replaces the unconfirmed ones; a confirmed
    milestone is never touched, and a proposal equal to one is not repeated. Names and quotes are
    plain text, clipped. The project is filed by the same keyword rule as a task, so a confirmed
    milestone lands on the right project without anyone choosing. The caller commits."""
    session.query(Milestone).filter(
        Milestone.source_meeting_id == meeting.id, Milestone.confirmed_at.is_(None)
    ).delete(synchronize_session=False)
    # Only confirmed ones count as "already there": another meeting's unconfirmed proposal must not
    # hide this one from the people who can read this meeting.
    have = {
        (normalise(m.name), m.due_date)
        for m in session.scalars(
            select(Milestone).where(
                Milestone.team_id == team_id, Milestone.confirmed_at.is_not(None)
            )
        )
    }
    made: list[Milestone] = []
    for item in proposals[:MAX_PROPOSALS]:
        name = titles.clean_title(item.name, MAX_MILESTONE_CHARS)
        due = date.fromisoformat(item.date)
        if not name or (normalise(name), due) in have:
            continue
        have.add((normalise(name), due))
        placed = filing.file_task(
            projects,
            series_project_id=filing.series_project(session, meeting),
            text=filing.filing_text(meeting_title, name, item.quote),
        )
        row = Milestone(
            team_id=team_id,
            project_id=placed.project_id,
            name=name,
            due_date=due,
            source_meeting_id=meeting.id,
            quote=titles.clean_title(item.quote, MAX_QUOTE_CHARS) or None,
        )
        session.add(row)
        made.append(row)
    session.flush()
    return made


def confirm(
    session: Session, milestone: Milestone, project_id: int | None, *, set_project: bool
) -> None:
    milestone.confirmed_at = datetime.now(UTC)
    if set_project:
        milestone.project_id = project_id
    session.commit()


def meeting_milestones(
    session: Session, user: User, meeting_id: int, team_id: int
) -> list[Milestone]:
    """The milestones that came from this meeting, confirmed or not, within the viewer's team."""
    return list(
        session.scalars(
            select(Milestone)
            .where(and_(Milestone.source_meeting_id == meeting_id, Milestone.team_id == team_id))
            .order_by(Milestone.due_date, Milestone.id)
        )
    )
