"""Filing a task into a project (spec 6.2): code, never the model.

Keyword hits in the meeting title, the task title and the quote. A clear winner files quietly; a
weak or tied match files to the best project and sets `check_project`; a team with one project
files there unflagged; no hit means no project. The rule for a meeting that belongs to a series
linked to a project (step 1) waits for W8: `series_project` is the seam, it returns None until
series exist. Kept apart from `app/projects.py` so that `app/tasks.py` can file a task without
importing the module that counts tasks.
"""

import re
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.extraction import normalise
from app.models import Meeting, Project

CONFIDENT_HITS = 2


@dataclass(frozen=True)
class Filing:
    project_id: int | None
    check: bool  # a guess: show "Check project"


def series_project(session: Session, meeting: Meeting) -> int | None:
    """The project the meeting's series is linked to, when it has one (spec 6.2 step 1). Series
    arrive with W8; until then no meeting has one. W8 returns `series.project_id` here and
    nothing else in the filing changes."""
    return None


def hits(keywords: list[str], text: str) -> int:
    """How many of the project's keywords appear in `text` as whole words or whole phrases, case
    and accents ignored. A keyword counts once however often it is said."""
    haystack = f" {normalise(text)} "
    count = 0
    for word in keywords:
        needle = normalise(word)
        if needle and re.search(rf"(?<!\w){re.escape(needle)}(?!\w)", haystack):
            count += 1
    return count


def file_task(projects: list[Project], *, series_project_id: int | None, text: str) -> Filing:
    """Where a task goes. `text` is the meeting title, the task title and the quote, joined.

    1. a series linked to a project: that one, confident;
    2. a team with one project: that one, unflagged, whatever the text says;
    3. otherwise score every project: no hit is no project; one clear winner with two or more
       hits is confident; one hit, or a tie for the lead, files to the best (the oldest of the
       tied) and asks for a check."""
    by_id = {p.id: p for p in projects}
    if series_project_id in by_id:
        return Filing(series_project_id, False)
    if len(projects) == 1:
        return Filing(projects[0].id, False)
    scores = sorted(((hits(p.keywords or [], text), -p.id, p.id) for p in projects), reverse=True)
    if not scores or scores[0][0] == 0:
        return Filing(None, False)
    best, tied = scores[0], len(scores) > 1 and scores[1][0] == scores[0][0]
    return Filing(best[2], tied or best[0] < CONFIDENT_HITS)


def team_projects(session: Session, team_id: int) -> list[Project]:
    return list(
        session.scalars(select(Project).where(Project.team_id == team_id).order_by(Project.id))
    )


def filing_text(meeting_title: str, task_title: str, quote: str | None) -> str:
    return " ".join(part for part in (meeting_title, task_title, quote or "") if part)
