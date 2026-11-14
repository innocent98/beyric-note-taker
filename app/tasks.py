"""Tasks: action items read from a meeting's notes, and tasks people type in (spec 3.1 to 3.3, 6.1).

Three things live here and are kept apart on purpose:

* **Owner matching** (`match_owner`) is code, never the model: a name said in a meeting is matched
  against the real, active members of the meeting owner's team. No match is "Needs owner". In a
  team of one the owner can only be the account holder or nobody.
* **Reconciling a re-run** (`reconcile`) keeps what a person did. Same bullet, same row; a task a
  person touched is never overwritten or removed; a deleted AI task stays as a tombstone so the
  same bullet does not bring it back.
* **Suggested tasks** (`Task.suggested`): an action item the model rated `medium` or `low` waits
  for a person when "Ask before adding unclear tasks" is on. It is made unfiled and counted nowhere
  (`confirmed`), mailed to nobody, and a person accepts it (`accept`, which files it and lets the
  assignee mail go) or dismisses it (`delete`, which keeps the tombstone). A re-run never flips an
  accepted task back and never brings a dismissed one back.
* **Who may see and change a task** (`visible`, `permissions`, `update`, ...). A task is visible to
  its assignee, to whoever can read the meeting it came from (`app/visibility.py`), and, for a
  manual task, to the team. A task nobody may see is "no such task" (404, never 403). A task the
  caller can see but not change answers 403 `team_forbidden`.

Titles are plain text of at most 200 characters, quotes at most 300; nothing here, and nothing in
the API, turns either into a link.
"""

import logging
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta

from sqlalchemy import and_, func, or_, select
from sqlalchemy.orm import Session

from app import filing, sprints, teams, titles
from app.extraction import UNCLEAR, CheckedAction, fingerprint, normalise
from app.models import Meeting, Milestone, Project, Task, Team, TeamMember, User
from app.visibility import can_read_clause

logger = logging.getLogger(__name__)

MAX_TITLE_CHARS = 200
MAX_ALIASES = 20
MAX_ALIAS_CHARS = 60
MIN_TOKEN_CHARS = 2
LIST_DEFAULT, LIST_MAX = 200, 500
STATUSES = ("todo", "doing", "done")
MANAGER_ROLES = ("owner", "admin")


@dataclass(frozen=True)
class Member:
    """An active member of a team, as owner matching and the assignee list see them."""

    user_id: int
    name: str | None
    email: str
    role: str
    aliases: tuple[str, ...]

    @property
    def display_name(self) -> str:
        return titles.clean_title(self.name, 100) or self.email.split("@")[0]


def members_of(session: Session, team_id: int) -> list[Member]:
    """The team's active members (a suspended or deleted account is nobody), joined first first."""
    rows = session.execute(
        select(User, TeamMember)
        .join(TeamMember, TeamMember.user_id == User.id)
        .where(TeamMember.team_id == team_id, User.status == "active")
        .order_by(TeamMember.joined_at, TeamMember.id)
    ).all()
    return [
        Member(user.id, user.name, user.email, member.role, tuple(member.aliases or ()))
        for user, member in rows
    ]


# ---- owner matching (spec 3.2) ----


def _tokens(*names: str) -> set[str]:
    return {t for name in names for t in name.split() if len(t) >= MIN_TOKEN_CHARS}


def match_owner(spoken: str | None, members: list[Member]) -> int | None:
    """The user id the spoken name stands for, or None ("Needs owner").

    Names are compared lower-cased, without accents or punctuation. A member can be called by their
    display name, any word of it, the part of their email before the @, or an alias. Precedence:
    first the whole name (display name or alias) matches exactly; then every word of the spoken
    name is a word of the same member. Either way it must pick exactly one member: two Daniels
    never match, and "Daniel Smith" does not match "Daniel Okoro"."""
    wanted = normalise(spoken or "")
    if not wanted:
        return None
    exact, by_tokens = [], []
    spoken_tokens = _tokens(wanted)
    for member in members:
        full = normalise(member.name or "")
        aliases = [normalise(a) for a in member.aliases]
        local = normalise(member.email.split("@")[0])
        if wanted in {n for n in [full, *aliases] if n}:
            exact.append(member.user_id)
        elif spoken_tokens and spoken_tokens <= _tokens(full, local, *aliases):
            by_tokens.append(member.user_id)
    if exact:
        return exact[0] if len(exact) == 1 else None
    return by_tokens[0] if len(by_tokens) == 1 else None


def add_alias(session: Session, team_id: int, user_id: int, spoken: str) -> None:
    """Remember that `spoken` ("Dan") is how the meeting named this member, so the next meeting
    matches it by itself (spec 3.2). Nothing happens when the name already matches them, when the
    alias is junk, or when they have as many as are allowed."""
    alias = " ".join(titles.clean_title(spoken, MAX_ALIAS_CHARS).split())
    if not alias or any(c.isdigit() for c in alias):
        return
    member = session.scalars(
        select(TeamMember).where(TeamMember.team_id == team_id, TeamMember.user_id == user_id)
    ).first()
    if member is None or len(member.aliases or []) >= MAX_ALIASES:
        return
    known = {normalise(a) for a in member.aliases or []}
    if normalise(alias) in known:
        return
    member.aliases = [*(member.aliases or []), alias]


# ---- reading a run into tasks (spec 3.3) ----


def confirmed():
    """The `where` condition that leaves out suggested tasks: every board list, count, progress
    figure and mail section uses it (the design leaves a task out until it is accepted)."""
    return Task.suggested.is_(False)


# How long after its first pass a never-answered meeting's parser tasks still count as made by the
# same extraction (the attempts are minutes apart, three at most).
PROVISIONAL_FOR = timedelta(hours=1)


def fingerprints_of(session: Session, meeting_id: int) -> frozenset[str]:
    """The fingerprints of the AI tasks a meeting already has (before a first pass)."""
    return frozenset(
        fp
        for fp in session.scalars(
            select(Task.ai_fingerprint).where(Task.meeting_id == meeting_id, Task.source == "ai")
        )
        if fp
    )


def fresh_fingerprints(
    first_pass: list[Task], known: frozenset[str], never_answered: bool, now: datetime
) -> frozenset[str]:
    """Which tasks of the first pass belong to this extraction and may still turn suggested when
    the model rates them unclear: those this pass made, and, for a meeting no answer was ever
    written for, the unrated ones an earlier attempt made a short while ago. Never a task an
    answered run made (its assignee was mailed), nor an old meeting's (a week-old task)."""
    out = {
        t.ai_fingerprint for t in first_pass if t.ai_fingerprint and t.ai_fingerprint not in known
    }
    if never_answered:
        out |= {
            t.ai_fingerprint
            for t in first_pass
            if t.ai_fingerprint
            and t.confidence is None
            and t.created_at is not None
            and now - t.created_at < PROVISIONAL_FOR
        }
    return frozenset(out)


def touched(task: Task) -> bool:
    """A person did something to it (edited, moved, assigned, ticked): a re-run leaves it alone."""
    return task.edited_at is not None or task.status != "todo"


def _due(value: str | None) -> date | None:
    return date.fromisoformat(value) if value else None  # already checked to be a real date


def reconcile(
    session: Session,
    meeting: Meeting,
    team_id: int,
    bullets: list[str],
    items: list[CheckedAction],
    members: list[Member],
    day: date,
    *,
    quiet: bool,
    refresh: bool = True,
    suggest: bool = False,
    fresh: frozenset[str] = frozenset(),
) -> list[Task]:
    """Make the meeting's AI tasks match its action `bullets`, keyed by fingerprint. `items` are
    the checked results, one per bullet and in the same order. Returns the live AI tasks.

    * same fingerprint, untouched: kept (same id), fields refreshed from this run;
    * same fingerprint, touched or deleted by a person: left exactly as it is;
    * vanished and untouched: removed (a vanished tombstone too);
    * `refresh=False` (the pass before the model is asked) only adds and removes: it never
      overwrites what an earlier answer from the model refined;
    * new: created, "New" unless the meeting is `quiet` (backfill), owner matched by code.

    `suggest` is "Ask before adding unclear tasks": an item the model rated `medium` or `low` is
    then made as a suggested task, unfiled. The model's answer comes after the notes parser's tasks
    exist, so a task the parser made **in this extraction** (its fingerprint is in `fresh`: made by
    this attempt's first pass, or by an earlier attempt of a meeting never answered yet) and that
    is still unrated may turn suggested when the model rates it unclear, and is unfiled then
    (accepting files it). No other task ever turns suggested: not one that an answered run made
    (and mailed) and left unrated, not one of an older meeting, not one a person touched. A
    suggested task is never turned back by a later run: only a person does either.
    The caller commits."""
    existing = {
        task.ai_fingerprint: task
        for task in session.scalars(
            select(Task).where(Task.meeting_id == meeting.id, Task.source == "ai")
        )
        if task.ai_fingerprint
    }
    wanted = {fingerprint(text): (position, text) for position, text in enumerate(bullets)}
    for key, task in existing.items():
        if key not in wanted and (task.deleted_at is not None or not touched(task)):
            session.delete(task)
    by_index = {item.index: item for item in items}
    team_projects = filing.team_projects(session, team_id)
    meeting_name = titles.meeting_name(meeting)
    series_project = filing.series_project(session, meeting)
    placer = sprints.Placer(session, team_id)
    kept: list[Task] = []
    for key, (position, _text) in wanted.items():
        task = existing.get(key)
        if task is not None and task.deleted_at is not None:
            continue  # deleted by a person: the same bullet does not bring it back
        if task is not None and (touched(task) or task.team_id != team_id):
            # Touched by a person, or moved to another team while the model was asked (its owner
            # left): never filed into this team's projects.
            kept.append(task)
            continue
        item = by_index.get(position)
        if task is not None and not refresh:
            kept.append(task)
            continue
        if item is None:  # `items` has one per bullet; this keeps a short list from deleting
            if task is not None:
                kept.append(task)
            continue
        waiting = suggest and item.confidence in UNCLEAR
        # Held back, unfiled: new and unclear, or first rated unclear, or already waiting.
        hold = (
            waiting
            if task is None
            else task.suggested
            or (task.confidence is None and waiting and task.ai_fingerprint in fresh)
        )
        fields = {
            "title": item.title[:MAX_TITLE_CHARS],
            "spoken_owner": item.spoken_owner,
            "assignee_user_id": match_owner(item.spoken_owner, members),
            "due_date": _due(item.due_date),
            "due_phrase": item.due_phrase,
            "quote": item.quote,
            "unverified": item.unverified,
            "position": position,
            "assigned_on": day,
        }
        if hold:
            fields["project_id"], fields["check_project"], fields["sprint_id"] = None, False, None
        else:
            placed = filing.file_task(
                team_projects,
                series_project_id=series_project,
                text=filing.filing_text(meeting_name, fields["title"], item.quote),
            )
            fields["project_id"], fields["check_project"] = placed.project_id, placed.check
            # Filed into a sprint by its due date (spec 6.4); a task a person touched is never here.
            fields["sprint_id"] = placer.sprint_for(placed.project_id, fields["due_date"])
        fields["carried"] = False
        if task is None or task.confidence is None:  # rated once: what the model first said stays
            fields["confidence"], fields["reason"] = item.confidence, item.reason
        if task is None:
            task = Task(
                team_id=team_id,
                meeting_id=meeting.id,
                source="ai",
                ai_fingerprint=key,
                status="todo",
                is_new=not quiet,
                suggested=hold,
                **fields,
            )
            session.add(task)
        else:
            for name, value in fields.items():
                setattr(task, name, value)
            if hold:
                task.suggested = True
        kept.append(task)
    session.flush()
    return kept


def new_assignees(tasks: list[Task], owner_id: int) -> list[int]:
    """Who is to be told about these tasks: each person they are assigned to, once, never the
    meeting's owner (for their own tasks), never anyone for a task a person has taken over or
    finished, and never anyone for a suggested task (accepting it is what tells them)."""
    seen: list[int] = []
    for task in tasks:
        who = task.assignee_user_id
        if (
            who is None
            or who == owner_id
            or task.status != "todo"
            or task.deleted_at
            or task.suggested
            or not task.is_new
        ):
            continue
        if who not in seen:
            seen.append(who)
    return seen


# ---- who may see and change a task (spec 6.1, 8.2) ----


def visible(user: User):
    """The `where` condition for "this caller may see this task". Deleted (tombstoned) tasks are
    never visible."""
    their_teams = select(TeamMember.team_id).where(TeamMember.user_id == user.id)
    readable = Task.meeting_id.in_(select(Meeting.id).where(can_read_clause(user)))
    return and_(
        Task.deleted_at.is_(None),
        or_(
            # Assigned to them AND still on that task's team: a person who left or was removed
            # keeps no window into the old team's tasks (a done task still names them). A person
            # may be on several teams: any of them counts.
            and_(Task.assignee_user_id == user.id, Task.team_id.in_(their_teams)),
            readable,
            and_(Task.meeting_id.is_(None), Task.team_id.in_(their_teams)),
        ),
    )


def membership(session: Session, user: User, team_id: int | None = None) -> TeamMember | None:
    """The caller's membership of `team_id` (a task's team, for what they may do to the task), or
    naming none, of their default team (`teams.default_membership`)."""
    return teams.membership(session, user.id, team_id)


def memberships(session: Session, user: User) -> dict[int, TeamMember]:
    """Every membership of the caller by team id, for a list of tasks from several teams."""
    return {
        m.team_id: m
        for m in session.scalars(select(TeamMember).where(TeamMember.user_id == user.id))
    }


@dataclass(frozen=True)
class Permissions:
    """What one caller may do to one task. Owners and admins of the task's team may do all of it.
    A member may change the status and due date of a task assigned to them, take a task nobody
    has, and change everything about a task they made themselves; they may not reassign or delete
    other people's tasks."""

    update: bool  # status and due date
    edit: bool  # the title
    assign: bool  # give it to someone else
    claim: bool  # take it: it has no owner yet
    delete: bool


def permissions(task: Task, user: User, member: TeamMember | None) -> Permissions:
    in_team = member is not None and member.team_id == task.team_id
    manager = in_team and member.role in MANAGER_ROLES
    mine = task.assignee_user_id == user.id
    made = task.source == "manual" and task.created_by_user_id == user.id
    return Permissions(
        update=manager or mine or made,
        edit=manager or made,
        assign=manager or made,
        claim=in_team and task.assignee_user_id is None,
        delete=manager or made,
    )


class TaskForbidden(Exception):
    """The caller can see the task but may not do that to it (403 `team_forbidden`)."""


class TaskInvalid(Exception):
    """The change is not valid; `code` and `message` are safe to show."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code, self.message = code, message


def get(session: Session, user: User, task_id: int) -> Task | None:
    return session.scalars(select(Task).where(Task.id == task_id, visible(user))).first()


def list_tasks(
    session: Session,
    user: User,
    *,
    assignee: str | None,
    status: str | None,
    limit: int,
    before_id: int | None,
    project: str | None = None,
    milestone: int | None = None,
    sprint: str | None = None,
    suggested: bool = False,
    team_id: int | None = None,
) -> tuple[list[Task], bool]:
    """The tasks the caller may see, newest first. `assignee` is `me`, `none` (Needs owner) or a
    user id; `project` is `none` (no project) or a project id; `milestone` is a milestone id;
    `sprint` is `none` (Backlog), `current`, `next` or a sprint id. Suggested tasks are left out
    (the Board and the Backlog); `suggested=True` lists only those instead (the Suggested tasks
    section). `team_id` limits the list to one team's board (the caller's membership of it is
    checked by the route); none lists every team's, as before. Returns up to `limit` and whether
    there are more."""
    query = select(Task).where(visible(user), Task.suggested.is_(suggested))
    if team_id is not None:
        query = query.where(Task.team_id == team_id)
    if assignee == "me":
        query = query.where(Task.assignee_user_id == user.id)
    elif assignee == "none":
        query = query.where(Task.assignee_user_id.is_(None))
    elif assignee is not None:
        query = query.where(Task.assignee_user_id == int(assignee))
    if status is not None:
        query = query.where(Task.status == status)
    if project == "none":
        query = query.where(Task.project_id.is_(None))
    elif project is not None:
        query = query.where(Task.project_id == int(project))
    if milestone is not None:
        query = query.where(Task.milestone_id == milestone)
    if sprint is not None:
        member = membership(session, user, team_id)
        query = query.where(
            sprints.sprint_filter(session, member.team_id if member else None, sprint)
        )
    if before_id is not None:
        query = query.where(Task.id < before_id)
    rows = list(session.scalars(query.order_by(Task.id.desc()).limit(limit + 1)))
    return rows[:limit], len(rows) > limit


def meeting_tasks(session: Session, user: User, meeting_id: int) -> list[Task]:
    """The tasks of one meeting the caller may read, in the order of its notes."""
    return list(
        session.scalars(
            select(Task)
            .where(Task.meeting_id == meeting_id, visible(user))
            .order_by(Task.position, Task.id)
        )
    )


def counts_for_meetings(session: Session, user: User, meeting_ids: list[int]) -> dict[int, int]:
    """How many tasks each meeting has (for the meetings list: "8 tasks"), those the caller sees,
    suggested ones not counted."""
    if not meeting_ids:
        return {}
    rows = session.execute(
        select(Task.meeting_id, func.count())
        .where(Task.meeting_id.in_(meeting_ids), visible(user), confirmed())
        .group_by(Task.meeting_id)
    )
    return {meeting_id: count for meeting_id, count in rows}


def attention(session: Session, user: User, team_id: int | None = None) -> dict[str, int]:
    """The "needs attention" counts over every open task the caller sees (of one team's board when
    `team_id` is given): tasks with no owner, and tasks whose quote could not be found in the
    transcript, and tasks filed by a guess."""
    open_ = and_(visible(user), Task.status != "done", confirmed())
    if team_id is not None:
        open_ = and_(open_, Task.team_id == team_id)
    needs_owner = session.scalar(
        select(func.count()).select_from(Task).where(open_, Task.assignee_user_id.is_(None))
    )
    unverified = session.scalar(
        select(func.count()).select_from(Task).where(open_, Task.unverified.is_(True))
    )
    check_project = session.scalar(
        select(func.count()).select_from(Task).where(open_, Task.check_project.is_(True))
    )
    return {"needs_owner": needs_owner, "unverified": unverified, "check_project": check_project}


def new_count(session: Session, user: User) -> int:
    """`badges.tasks_new`: open tasks the caller sees that are still marked New."""
    return session.scalar(
        select(func.count())
        .select_from(Task)
        .where(visible(user), Task.is_new.is_(True), Task.status != "done", confirmed())
    )


def check_assignable(
    session: Session, team_id: int, project_id: int | None, milestone_id: int | None
) -> None:
    """A task may only be given a project of its own team and a confirmed milestone of its own
    team (a proposal is not a milestone yet). Raises `TaskInvalid` (422) otherwise."""
    if (
        project_id is not None
        and session.scalar(
            select(Project.id).where(Project.id == project_id, Project.team_id == team_id)
        )
        is None
    ):
        raise TaskInvalid("project_not_on_team", "That project is not on your team.")
    if (
        milestone_id is not None
        and session.scalar(
            select(Milestone.id).where(
                Milestone.id == milestone_id,
                Milestone.team_id == team_id,
                Milestone.confirmed_at.is_not(None),
            )
        )
        is None
    ):
        raise TaskInvalid("milestone_not_on_team", "That milestone is not on your team.")


class Unset:
    """Marks "the caller said nothing" where `None` is an answer (a task in the Backlog)."""


UNSET = Unset()


def check_sprint(session: Session, team_id: int, project_id: int | None, sprint_id: int) -> None:
    """A task may only be planned into an open sprint of its own team, in the series its project
    uses. Raises `TaskInvalid` (422) otherwise; a closed sprint or another team's is the same
    answer, so nothing is learned about sprints that are not the caller's."""
    if not sprints.usable(session, team_id, project_id, sprint_id):
        raise TaskInvalid("sprint_not_on_team", "That sprint is not available for this task.")


def _title(value: str) -> str:
    return titles.clean_title(value, MAX_TITLE_CHARS)


def _assignable(session: Session, task_team_id: int, user_id: int) -> None:
    if user_id not in {m.user_id for m in members_of(session, task_team_id)}:
        raise TaskInvalid("assignee_not_on_team", "That person is not on your team.")


def create(
    session: Session,
    user: User,
    *,
    title: str,
    assignee_user_id: int | None,
    due_date: date | None,
    status: str,
    today: date,
    project_id: int | None = None,
    sprint_id: int | None | Unset = UNSET,
    team_id: int | None = None,
) -> Task:
    """A manual task in the caller's team (`team_id`, a team the route has checked they belong to;
    none: their default team). Anyone on the team may make one. Left out, the sprint
    follows the due date (the Backlog without one); `None` is the Backlog and an id is that sprint,
    which must be an open sprint of the team's series for the task's project. Commits."""
    team = session.get(Team, team_id) if team_id is not None else teams.ensure_team(session, user)
    if assignee_user_id is not None:
        _assignable(session, team.id, assignee_user_id)
    check_assignable(session, team.id, project_id, None)
    if isinstance(sprint_id, Unset):
        sprint_id = sprints.Placer(session, team.id).sprint_for(project_id, due_date)
    elif sprint_id is not None:
        sprints.lock(session, team.id)  # the check and the write see the same series
        check_sprint(session, team.id, project_id, sprint_id)
    task = Task(
        sprint_id=sprint_id,
        team_id=team.id,
        title=_title(title),
        assignee_user_id=assignee_user_id,
        due_date=due_date,
        status=status,
        project_id=project_id,
        done_at=datetime.now(UTC) if status == "done" else None,
        source="manual",
        created_by_user_id=user.id,
        assigned_on=today,
    )
    session.add(task)
    session.commit()
    return task


def update(session: Session, user: User, task: Task, changes: dict, now: datetime) -> Task:
    """Apply a partial change (`title`, `status`, `assignee_user_id`, `due_date`). A field given
    with the value it already has is not a change. The whole request is refused (`TaskForbidden`)
    if any real change is more than the caller may make, and nothing is applied. Commits."""
    allowed = permissions(task, user, membership(session, user, task.team_id))
    real = {k: v for k, v in changes.items() if getattr(task, k) != v}
    if "title" in real:
        real["title"] = _title(real["title"])
        if real["title"] == task.title:
            del real["title"]
    if not real:
        return task
    if "title" in real and not allowed.edit:
        raise TaskForbidden
    if ("status" in real or "due_date" in real) and not allowed.update:
        raise TaskForbidden
    # Filing is the owner's and admin's (spec 6.3), and the maker's of a manual task: a member given
    # a task may change its status and due date, not where it is filed.
    if (
        {"project_id", "milestone_id", "check_project", "sprint_id"} & real.keys()
    ) and not allowed.edit:
        raise TaskForbidden
    if "project_id" in real or "milestone_id" in real:
        check_assignable(session, task.team_id, real.get("project_id"), real.get("milestone_id"))
    if "project_id" in real:
        real["check_project"] = False  # a person chose it: nothing left to check
    if "sprint_id" in real:
        if real["sprint_id"] is not None:
            sprints.lock(session, task.team_id)
            check_sprint(
                session, task.team_id, real.get("project_id", task.project_id), real["sprint_id"]
            )
        real["carried"] = False  # a person placed it: no longer "carried over"
    if "assignee_user_id" in real:
        target = real["assignee_user_id"]
        claiming = target == user.id and task.assignee_user_id is None and allowed.claim
        if not (allowed.assign or claiming):
            raise TaskForbidden
        if target is not None:
            members = members_of(session, task.team_id)
            if target not in {m.user_id for m in members}:
                raise TaskInvalid("assignee_not_on_team", "That person is not on your team.")
            if (
                allowed.assign
                and task.spoken_owner
                and task.assignee_user_id is None
                and match_owner(task.spoken_owner, members) != target
            ):
                add_alias(session, task.team_id, target, task.spoken_owner)
    if "project_id" in real and "sprint_id" not in real:
        # Another project can mean another series: keep its place in time (current, next).
        sprints.rehome(session, task, real["project_id"])
    for name, value in real.items():
        setattr(task, name, value)
    if "status" in real:
        task.is_new = False
        task.done_at = now if real["status"] == "done" else None
    task.edited_at = now
    session.commit()
    return task


def delete(session: Session, user: User, task: Task) -> None:
    """Delete a task. A manual task is removed; an AI task is kept as a tombstone with its words
    cleared, so the same bullet does not bring it back when the notes are read again (this is how a
    suggested task is dismissed: the person who owns its meeting may dismiss it too). Commits."""
    if not (
        permissions(task, user, membership(session, user, task.team_id)).delete
        or (task.suggested and owns_meeting_of(session, user, task))
    ):
        raise TaskForbidden
    if task.source == "ai" and task.ai_fingerprint:
        task.deleted_at = datetime.now(UTC)
        task.title, task.quote, task.spoken_owner, task.due_phrase = "", None, None, None
        task.reason = None
        task.assignee_user_id, task.due_date, task.is_new = None, None, False
    else:
        session.delete(task)
    session.commit()


def owns_meeting_of(session: Session, user: User, task: Task) -> bool:
    """Whether the caller recorded the meeting the task came from (the owner of a meeting may decide
    its suggestions whatever their role on the team)."""
    return (
        task.meeting_id is not None
        and session.scalar(select(Meeting.owner_user_id).where(Meeting.id == task.meeting_id))
        == user.id
    )


def can_decide(session: Session, user: User, task: Task) -> bool:
    """Whether the caller may accept or dismiss this suggested task: whoever may edit it, or the
    person who owns its meeting. A teammate who may merely read it may not."""
    return permissions(task, user, membership(session, user, task.team_id)).edit or owns_meeting_of(
        session, user, task
    )


class NotSuggested(Exception):
    """The task was never waiting for a decision (409 `not_suggested`)."""


def accept(session: Session, user: User, task: Task, now: datetime | None = None) -> bool:
    """Accept a suggested task: it becomes a task, filed as creation files one (a project by the
    series link or the keywords, a sprint by its due date, unless a person already chose them).
    Whoever may edit the task, or owns its meeting, may accept it (`TaskForbidden` otherwise).
    Accepting counts as a person's touch (`edited_at`): a later re-run leaves the task alone, does
    not file it again and, if the notes are reworded, does not delete it and ask again.

    Idempotent: the row is locked and read again, so a second click (or a second tab) finds it
    already accepted. That answers True only for the call that did it, so the caller sends the
    assignee mail once, and False for a task that is not waiting any more but was rated unclear
    (an accepted one). A task that never was a suggestion (a clear commitment, a manual task, one
    made before suggestions) raises `NotSuggested`. Does not commit: the mail goes in the same
    transaction."""
    if not can_decide(session, user, task):
        raise TaskForbidden
    session.refresh(task, with_for_update=True)
    if task.deleted_at is not None:
        raise NotSuggested
    if not task.suggested:
        if task.source == "ai" and task.confidence in UNCLEAR:
            return False
        raise NotSuggested
    task.suggested = False
    task.edited_at = now or datetime.now(UTC)
    meeting = session.get(Meeting, task.meeting_id) if task.meeting_id else None
    if meeting is not None and task.project_id is None:
        placed = filing.file_task(
            filing.team_projects(session, task.team_id),
            series_project_id=filing.series_project(session, meeting),
            text=filing.filing_text(titles.meeting_name(meeting), task.title, task.quote),
        )
        task.project_id, task.check_project = placed.project_id, placed.check
    if task.sprint_id is None and not task.carried:
        task.sprint_id = sprints.Placer(session, task.team_id).sprint_for(
            task.project_id, task.due_date
        )
    session.flush()
    return True
