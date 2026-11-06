"""Sprints (W9, spec 6.4, ambiguity rows 3 and 9).

A **series** is a run of sprints: the team's, or a project's own when the project has a sprint
length of its own (`projects.sprint_length`). A series always has exactly two open sprints, the
current one (the earlier) and the next; closed ones stay as history. Dates are days in the team's
zone, never meeting times.

* **Windows.** From a start day and a length: 1 week 5 days (Monday to Friday when aligned),
  1 week 7 days, 2 weeks, 1 month 30 days, 3 or 6 calendar months, or custom days (1 to 365).
  The next sprint starts the day after the previous one ends; an aligned 5-day sprint starts on the
  next Monday.
* **Closing.** A sprint closes when its end date has passed in the team's zone (the scheduler's
  `rollover_all`), or by hand ("Close sprint", an owner or admin). Unfinished tasks move to the
  next sprint with `carried` set. Closing early starts the next sprint today; its end date does
  not change.
* **Filing a task** (`place`): no due date is the Backlog, a due date inside the current sprint or
  before it is the current sprint, a later one is the next sprint.
* **Who may do what** is not decided here: this module trusts its callers (`app/api_sprints.py`,
  `app/tasks.py`, `app/projects.py`) to have checked the team and the role, and it only ever
  reads or writes sprints of the team id it is given.

Every write to a team's series takes the team's advisory lock first, so two requests (or a request
and the scheduler) never make the same sprint twice or close it twice. The lock is held to the end
of the transaction; the caller commits.
"""

import calendar
import logging
from datetime import UTC, date, datetime, timedelta

from sqlalchemy import ColumnElement, false, select, text, update
from sqlalchemy.orm import Session

from app import timezones
from app.models import Project, Sprint, Task, Team, User, UserSettings

logger = logging.getLogger(__name__)

# Next to SCHEDULER_LOCK (6010): held for one transaction, per team.
SPRINT_LOCK = 6020
MAX_CATCH_UP = 60  # sprints one rollover pass closes for one series (a long outage catches up)
CLOSED_SHOWN = 20  # closed sprints the board's selector lists

DAYS = {"week5": 5, "week7": 7, "week14": 14, "month1": 30}
MONTHS = {"month3": 3, "month6": 6}


def name_of(sprint: Sprint) -> str:
    return f"Sprint {sprint.number}"


# ---- dates ----


def add_months(day: date, months: int) -> date:
    index = day.month - 1 + months
    year, month = day.year + index // 12, index % 12 + 1
    return date(year, month, min(day.day, calendar.monthrange(year, month)[1]))


def window_end(length: str, custom_days: int, start: date) -> date:
    """The last day of a sprint of this length that starts on `start`."""
    if length in MONTHS:
        return add_months(start, MONTHS[length]) - timedelta(days=1)
    days = custom_days if length == "custom" else DAYS[length]
    return start + timedelta(days=days - 1)


def next_start(length: str, aligned: bool, previous_end: date) -> date:
    """The day after the previous sprint; an aligned 5-day sprint opens on the next Monday."""
    day = previous_end + timedelta(days=1)
    if length == "week5" and aligned:
        while day.weekday() != 0:
            day += timedelta(days=1)
    return day


def first_start(length: str, aligned: bool, today: date) -> date:
    """Where a brand new series begins: today, or for an aligned 5-day sprint the Monday of this
    week (the next Monday when today is a weekend)."""
    if length == "week5" and aligned:
        if today.weekday() >= 5:
            return today + timedelta(days=7 - today.weekday())
        return today - timedelta(days=today.weekday())
    return today


def team_today(session: Session, team: Team, now: datetime | None = None) -> date:
    """Today in the team's zone: the zone its owner chose in Settings, else UTC."""
    zone = session.scalar(
        select(UserSettings.time_zone).where(UserSettings.user_id == team.owner_user_id)
    )
    return timezones.localise(now or datetime.now(UTC), zone)[0].date()


def user_today(session: Session, user: User) -> date:
    """Today in the viewer's own zone (their Settings), for "4 days left" on their screen."""
    zone = session.scalar(select(UserSettings.time_zone).where(UserSettings.user_id == user.id))
    return timezones.localise(datetime.now(UTC), zone)[0].date()


# ---- series ----


def lock(session: Session, team_id: int) -> None:
    session.execute(text("select pg_advisory_xact_lock(:k, :t)"), {"k": SPRINT_LOCK, "t": team_id})


def length_of(team: Team, project: Project | None) -> str:
    """The sprint length a series runs on: the project's own, else the team's."""
    return (
        project.sprint_length
        if project is not None and project.sprint_length
        else team.sprint_length
    )


def series_key(session: Session, team_id: int, project_id: int | None) -> int | None:
    """The `project_id` of the series a task of this project lives in: the project's own series
    when it has a sprint length of its own, else the team's (None)."""
    if project_id is None:
        return None
    own = session.scalar(
        select(Project.sprint_length).where(Project.id == project_id, Project.team_id == team_id)
    )
    return project_id if own else None


def series_query(team_id: int, key: int | None):
    return select(Sprint).where(
        Sprint.team_id == team_id,
        Sprint.project_id.is_(None) if key is None else Sprint.project_id == key,
    )


def open_sprints(session: Session, team_id: int, key: int | None) -> list[Sprint]:
    """The series' open sprints, current first."""
    return list(
        session.scalars(
            series_query(team_id, key).where(Sprint.closed_at.is_(None)).order_by(Sprint.number)
        )
    )


def _project(session: Session, team_id: int, key: int | None) -> Project | None:
    if key is None:
        return None
    return session.scalars(
        select(Project).where(Project.id == key, Project.team_id == team_id)
    ).first()


def _make(session: Session, team: Team, key: int | None, number: int, start: date) -> Sprint:
    length = length_of(team, _project(session, team.id, key))
    sprint = Sprint(
        team_id=team.id,
        project_id=key,
        number=number,
        start_date=start,
        end_date=window_end(length, team.sprint_custom_days, start),
    )
    session.add(sprint)
    session.flush()
    return sprint


def ensure_series(
    session: Session, team: Team, key: int | None, today: date | None = None
) -> list[Sprint]:
    """The series' current and next sprint, made when missing (first use, or a project that just
    got a length of its own). Takes the team's lock."""
    lock(session, team.id)
    opened = open_sprints(session, team.id, key)
    if len(opened) >= 2:
        return opened[:2]
    project = _project(session, team.id, key)
    length = length_of(team, project)
    aligned = team.sprint_align_weekdays
    if not opened:
        last = session.scalars(
            series_query(team.id, key).order_by(Sprint.number.desc()).limit(1)
        ).first()
        if last is None:
            start = first_start(length, aligned, today or team_today(session, team))
            opened = [_make(session, team, key, 1, start)]
        else:
            start = next_start(length, aligned, last.end_date)
            opened = [_make(session, team, key, last.number + 1, start)]
    tail = opened[-1]
    opened.append(
        _make(session, team, key, tail.number + 1, next_start(length, aligned, tail.end_date))
    )
    return opened


def role_ids(session: Session, team_id: int) -> dict[str, set[int]]:
    """The ids of every series' current and next sprint in the team."""
    out: dict[str, set[int]] = {"current": set(), "next": set()}
    seen: dict[int | None, int] = {}
    rows = session.scalars(
        select(Sprint)
        .where(Sprint.team_id == team_id, Sprint.closed_at.is_(None))
        .order_by(Sprint.project_id.nulls_first(), Sprint.number)
    )
    for sprint in rows:
        place = seen.get(sprint.project_id, 0)
        seen[sprint.project_id] = place + 1
        if place < 2:
            out["current" if place == 0 else "next"].add(sprint.id)
    return out


def role_of(sprint: Sprint, opened: list[Sprint]) -> str:
    """`closed`, `current` or `next`, given the series' open sprints (current first)."""
    if sprint.closed_at is not None:
        return "closed"
    return "current" if opened and opened[0].id == sprint.id else "next"


# ---- closing ----


def close_current(
    session: Session, team: Team, key: int | None, now: datetime, today: date, *, early: bool
) -> int:
    """Close the series' current sprint: unfinished tasks move to the next sprint with `carried`
    set, the next becomes current (starting today when this is an early close), and a new next is
    made. Returns how many tasks moved (all of them, whoever may see them). Takes the lock."""
    current, nxt = ensure_series(session, team, key, today)
    moved = session.execute(
        update(Task)
        .where(Task.sprint_id == current.id, Task.status != "done", Task.deleted_at.is_(None))
        .values(sprint_id=nxt.id, carried=True)
    ).rowcount
    current.closed_at = now
    current.closed_early = early
    if early and today < nxt.start_date:
        nxt.start_date = today  # starts today; its end date does not change (ambiguity row 9)
    length = length_of(team, _project(session, team.id, key))
    _make(
        session,
        team,
        key,
        nxt.number + 1,
        next_start(length, team.sprint_align_weekdays, nxt.end_date),
    )
    return moved


def rollover_series(
    session: Session, team: Team, key: int | None, now: datetime, today: date
) -> int:
    """Close every sprint of the series whose end date has passed. Returns how many it closed."""
    closed = 0
    while closed < MAX_CATCH_UP:
        current = ensure_series(session, team, key, today)[0]
        if current.end_date >= today:
            break
        close_current(session, team, key, now, today, early=False)
        closed += 1
    return closed


def rollover_team(session: Session, team: Team, now: datetime) -> int:
    """Keep every series of the team current: make the missing sprints, close the ones that ended.
    Commits."""
    today = team_today(session, team, now)
    keys: list[int | None] = [None]
    keys += list(
        session.scalars(
            select(Project.id).where(Project.team_id == team.id, Project.sprint_length.is_not(None))
        )
    )
    closed = sum(rollover_series(session, team, key, now, today) for key in keys)
    session.commit()
    return closed


def rollover_all(session: Session, now: datetime) -> int:
    """The scheduler's hourly job. A team that fails is logged by type only and the others go on;
    safe to run twice (the team's lock and the end-date test make it idempotent)."""
    closed = 0
    for team_id in list(session.scalars(select(Team.id).order_by(Team.id))):
        try:
            team = session.get(Team, team_id)
            if team is not None:
                closed += rollover_team(session, team, now)
        except Exception as exc:
            session.rollback()
            logger.error("sprint rollover failed for team %s: %s", team_id, type(exc).__name__)
    return closed


# ---- settings ----


def reflow(session: Session, team: Team) -> None:
    """After the team's length, custom days or alignment changed: redraw the open sprints of every
    series from their start days (the current keeps its start; the next follows it). Closed
    sprints are history and are not touched. Tasks stay where they are."""
    lock(session, team.id)
    keys: list[int | None] = [None]
    keys += list(session.scalars(select(Project.id).where(Project.team_id == team.id)))
    for key in keys:
        opened = open_sprints(session, team.id, key)
        if not opened:
            continue
        length = length_of(team, _project(session, team.id, key))
        opened[0].end_date = window_end(length, team.sprint_custom_days, opened[0].start_date)
        for before, sprint in zip(opened, opened[1:], strict=False):
            sprint.start_date = next_start(length, team.sprint_align_weekdays, before.end_date)
            sprint.end_date = window_end(length, team.sprint_custom_days, sprint.start_date)
    session.flush()


def _move_open_tasks(
    session: Session, source: list[Sprint], target: list[Sprint], project_id: int
) -> None:
    """Move the project's tasks from each open sprint of one series to its counterpart (current
    to current, next to next) in the other."""
    for old, new in zip(source, target, strict=False):
        session.execute(
            update(Task)
            .where(Task.project_id == project_id, Task.sprint_id == old.id)
            .values(sprint_id=new.id)
        )


def set_project_length(session: Session, team: Team, project: Project, length: str | None) -> None:
    """Give a project its own sprint length (or take it back to the team's). A new own series is
    made, and the project's tasks in the team's current and next sprints move into it; taking it
    back moves them the other way and drops the project's open sprints. Closed sprints stay."""
    if project.sprint_length == length:
        return
    lock(session, team.id)
    today = team_today(session, team)
    had = project.sprint_length is not None
    if length is None:
        mine = open_sprints(session, team.id, project.id)
        project.sprint_length = None
        session.flush()
        shared = ensure_series(session, team, None, today)
        _move_open_tasks(session, mine, shared, project.id)
        for sprint in mine:
            session.delete(sprint)
    elif not had:
        shared = ensure_series(session, team, None, today)
        project.sprint_length = length
        session.flush()
        own = ensure_series(session, team, project.id, today)
        _move_open_tasks(session, shared, own, project.id)
    else:
        project.sprint_length = length
        session.flush()
        reflow(session, team)
    session.flush()


def release_project(session: Session, team: Team, project: Project) -> None:
    """Before a project is deleted: its tasks stay, so they follow into the team's series."""
    if project.sprint_length is not None:
        set_project_length(session, team, project, None)


# ---- tasks ----


class Placer:
    """Files tasks into sprints by their due date (ambiguity row 3), caching the series it reads.
    One per batch of tasks; `sprint_for` is the only call."""

    def __init__(self, session: Session, team_id: int, today: date | None = None) -> None:
        self.session = session
        self.team = session.get(Team, team_id)
        self.today = today
        self._series: dict[int | None, list[Sprint]] = {}

    def sprint_for(self, project_id: int | None, due: date | None) -> int | None:
        if due is None or self.team is None:
            return None
        key = series_key(self.session, self.team.id, project_id)
        if key not in self._series:
            if self.today is None:
                self.today = team_today(self.session, self.team)
            self._series[key] = ensure_series(self.session, self.team, key, self.today)
        current, nxt = self._series[key]
        return current.id if due <= current.end_date else nxt.id


def usable(session: Session, team_id: int, project_id: int | None, sprint_id: int) -> bool:
    """True when a task of this team and project may be planned into this sprint: it is an open
    sprint of the team, in the series the task's project uses."""
    sprint = session.get(Sprint, sprint_id)
    return (
        sprint is not None
        and sprint.team_id == team_id
        and sprint.closed_at is None
        and sprint.project_id == series_key(session, team_id, project_id)
    )


def rehome(session: Session, task: Task, new_project_id: int | None) -> None:
    """A task moved to a project that uses another series keeps its place in time: the current
    sprint becomes the current sprint there, the next the next. The Backlog and closed sprints stay
    as they are."""
    if task.sprint_id is None:
        return
    old = session.get(Sprint, task.sprint_id)
    if old is None or old.closed_at is not None or old.team_id != task.team_id:
        return
    key = series_key(session, task.team_id, new_project_id)
    if key == old.project_id:
        return
    team = session.get(Team, task.team_id)
    mine = open_sprints(session, task.team_id, old.project_id)
    wanted = 0 if mine and mine[0].id == old.id else 1
    task.sprint_id = ensure_series(session, team, key)[wanted].id


def sprint_filter(session: Session, team_id: int | None, value: str) -> ColumnElement[bool]:
    """The `where` for the task list's `sprint` filter: `none` (Backlog), `current`, `next`, or an
    id. The team's own current or next sprint stands for every series' one (the board of "All
    projects"); any other id is exactly that sprint, and an id of another team matches nothing."""
    if value == "none":
        return Task.sprint_id.is_(None)
    if team_id is None:
        return false()
    roles = role_ids(session, team_id)
    if value in roles:
        return Task.sprint_id.in_(roles[value])
    sprint = session.scalars(
        select(Sprint).where(Sprint.id == int(value), Sprint.team_id == team_id)
    ).first()
    if sprint is None:
        return false()
    if sprint.project_id is None:
        for ids in roles.values():
            if sprint.id in ids:
                return Task.sprint_id.in_(ids)
    return Task.sprint_id == sprint.id
