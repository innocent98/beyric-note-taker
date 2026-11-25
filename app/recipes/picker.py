"""Choosing the meetings of a cross-meeting draft (plan T15, spec 5.3b and 5.4).

A status report, a client briefing or a revision plan is made from several meetings. A person picks
them one of five ways (`PickerSpec`, exactly one): a recurring meeting (`series_id`), a project, a
sprint, a date range, or by hand. `resolve_meetings` turns that into the meetings the caller may
read: **at most 12, newest first**, ready ones with something to read.

The permission rule is the draft's own (`service._readable_meetings`): a meeting the caller cannot
read (`visibility.can_read_clause`) is **left out and counted**, and nothing about it (title, day,
id, words) reaches the caller or the model; the count is all that is said. A meeting that is merely
not ready yet is not offered and not counted: it is not hidden, it is not there yet.

Each way goes through the module that already owns the idea, never a second rule:

* a series: `series.get` (404 for one of another team or one the caller may not see) and
  `series.in_row` (the row's own meetings, scope included);
* a project: `projects.project_of_team` on the active team (404 for another team's); its meetings
  are those its tasks come from, plus the meetings of the recurring meetings linked to it
  (`Series.project_id`, `series.rows_in_scope`), which is how filing sends a task there;
* a sprint: the sprint of the active team (404 otherwise), `sprints.sprint_filter` for the meetings
  its tasks come from (the board's own rule for "this sprint"), plus the meetings of the team's list
  held on the sprint's days;
* a date range: the Meetings list's own rule (`visibility.readable_in` on the active team, days
  made by `services.day_range_utc` in the person's time zone);
* by hand: the ids, each read through `can_read_clause`, as a request for a draft reads them.

The active team is `X-Team-Id` (`team_admin.requested_team`), as on the Meetings list.
"""

from dataclasses import dataclass

from sqlalchemy import and_, func, select
from sqlalchemy.orm import Session
from sqlalchemy.sql.elements import ColumnElement

from app import projects, services, sprints, tasks, team_admin
from app import series as series_rules
from app.ask import transcript_of
from app.models import Meeting, Project, Series, Sprint, Task, Team, User, UserSettings
from app.recipes.picker_spec import MAX_MEETINGS, PickerSpec
from app.visibility import active_team_clause, can_read_clause, readable_in

# The newest meetings looked at before the cap: a team with hundreds of meetings in one scope does
# not load them all to offer twelve.
SCAN_LIMIT = 200


@dataclass
class Resolved:
    """`meetings`: at most `MAX_MEETINGS`, newest first, all readable by the caller.
    `left_out_count`: how many chosen meetings the caller cannot read (never named). `capped`: more
    than twelve readable ones matched and the oldest were cut."""

    meetings: list[Meeting]
    left_out_count: int
    capped: bool


@dataclass
class _Scope:
    """What was chosen: a condition on `meetings`; `counts_unreadable` is whether a matching
    meeting the caller cannot read is counted as left out (true where the scope is a named thing
    that has meetings of its own; false for a list, which is only what the caller may read)."""

    clause: ColumnElement[bool]
    counts_unreadable: bool = True


def usable(meeting: Meeting) -> bool:
    """A meeting a draft can be made from: ready, with notes or a transcript (the same test as
    `service._readable_meetings`)."""
    return meeting.status == "ready" and bool(
        (meeting.notes or "").strip() or transcript_of(meeting).strip()
    )


def _not_found(what: str) -> services.Refused:
    return services.Refused(404, "not_found", f"No such {what}.")


def _task_meetings(*conditions: ColumnElement[bool]):
    return select(Task.meeting_id).where(
        Task.meeting_id.is_not(None), Task.deleted_at.is_(None), tasks.confirmed(), *conditions
    )


def _series_scope(session: Session, user: User, series_id: int) -> _Scope:
    found = series_rules.get(session, user, series_id)
    if found is None:
        raise _not_found("recurring meeting")
    return _Scope(series_rules.in_row(session, found))


def _project_scope(session: Session, user: User, team_id: int | None, project_id: int) -> _Scope:
    me = team_admin.my_membership(session, user, team_id)
    project: Project | None = projects.project_of_team(session, me.team_id, project_id)
    if project is None:
        raise _not_found("project")
    linked = list(
        session.scalars(
            select(Series.id).where(Series.project_id == project.id, Series.team_id == me.team_id)
        )
    )
    return _Scope(
        (Meeting.id.in_(_task_meetings(Task.project_id == project.id, Task.team_id == me.team_id)))
        | series_rules.rows_in_scope(session, linked)
    )


def _sprint_scope(session: Session, user: User, team_id: int | None, sprint_id: int) -> _Scope:
    me = team_admin.my_membership(session, user, team_id)
    sprint = session.scalars(
        select(Sprint).where(Sprint.id == sprint_id, Sprint.team_id == me.team_id)
    ).first()
    if sprint is None:
        raise _not_found("sprint")
    team = session.get(Team, me.team_id)
    zone = session.scalar(
        select(UserSettings.time_zone).where(UserSettings.user_id == team.owner_user_id)
    )
    start, before = services.day_range_utc(zone, sprint.start_date, sprint.end_date)
    active = team_admin.active_team(session, user, me.team_id)
    planned = Meeting.id.in_(
        _task_meetings(
            Task.team_id == me.team_id, sprints.sprint_filter(session, me.team_id, str(sprint.id))
        )
    )
    held = and_(
        Meeting.created_at >= start, Meeting.created_at < before, active_team_clause(user, active)
    )
    return _Scope(planned | held)


def _days_scope(session: Session, user: User, team_id: int | None, spec: PickerSpec) -> _Scope:
    stored = session.scalar(select(UserSettings.time_zone).where(UserSettings.user_id == user.id))
    zone = services.pick_zone(stored, spec.tz)
    start, before = services.day_range_utc(zone, spec.from_day, spec.to_day)
    active = team_admin.active_team(session, user, team_id)
    clause = readable_in(user, active)
    if start is not None:
        clause = and_(clause, Meeting.created_at >= start)
    if before is not None:
        clause = and_(clause, Meeting.created_at < before)
    return _Scope(clause, counts_unreadable=False)


def resolve_meetings(
    session: Session, user: User, spec: PickerSpec, *, team_id: int | None = None
) -> Resolved:
    """The meetings `spec` names that `user` may read: at most 12, newest first. `team_id` is the
    active team (the `X-Team-Id` header; None: the caller's default). 404 for a series, project,
    sprint or team that is not the caller's."""
    unique_ids: list[int] = []
    if spec.meeting_ids is not None:
        unique_ids = list(dict.fromkeys(spec.meeting_ids))
        scope = _Scope(Meeting.id.in_(unique_ids))
    elif spec.series_id is not None:
        scope = _series_scope(session, user, spec.series_id)
    elif spec.project_id is not None:
        scope = _project_scope(session, user, team_id, spec.project_id)
    elif spec.sprint_id is not None:
        scope = _sprint_scope(session, user, team_id, spec.sprint_id)
    else:
        scope = _days_scope(session, user, team_id, spec)

    rows = list(
        session.scalars(
            select(Meeting)
            .where(scope.clause, can_read_clause(user))
            .order_by(Meeting.created_at.desc(), Meeting.id.desc())
            .limit(SCAN_LIMIT)
        )
    )
    offered = [m for m in rows if usable(m)]
    if spec.meeting_ids is not None:
        # an id that is not readable and an id that does not exist look the same: left out
        readable = set(
            session.scalars(
                select(Meeting.id).where(Meeting.id.in_(unique_ids), can_read_clause(user))
            )
        )
        left_out = len(unique_ids) - len(readable)
    elif scope.counts_unreadable:
        # total minus readable, not "not readable": a NOT over the read rule is null (so false) for
        # a meeting with no team, and would count nothing
        total = session.scalar(select(func.count()).select_from(Meeting).where(scope.clause))
        readable_count = session.scalar(
            select(func.count()).select_from(Meeting).where(scope.clause, can_read_clause(user))
        )
        left_out = (total or 0) - (readable_count or 0)
    else:
        left_out = 0
    return Resolved(
        offered[:MAX_MEETINGS],
        left_out,
        len(offered) > MAX_MEETINGS or len(rows) == SCAN_LIMIT,
    )


__all__ = ["MAX_MEETINGS", "PickerSpec", "Resolved", "resolve_meetings", "usable"]
