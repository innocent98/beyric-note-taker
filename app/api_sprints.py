"""The sprint board's routes (W9, spec 6.4). Included into `app.api.router`, so they share its
`/api` prefix, login check, CSRF rule and error shape.

Sprints belong to the caller's team. Every member reads them; an owner or admin closes one (403
`team_forbidden` for a member). A sprint or project that is not on the caller's team is 404,
never 403. The counts on a sprint follow what the caller may see (`app/tasks.visible`). The
sprint settings are on `GET`/`PATCH /api/team`; a project's own length is on
`PATCH /api/projects/{id}`.
"""

from datetime import UTC, datetime

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app import auth, projects, services, sprints, tasks, team_admin
from app.api_schemas import ErrorResponse, SprintClosed, SprintList, SprintOut
from app.db import get_session
from app.models import Sprint, Task, Team, User

router = APIRouter()

FORBIDDEN = {403: {"model": ErrorResponse, "description": "`team_forbidden`"}}
NOT_FOUND = {404: {"model": ErrorResponse, "description": "Not on the caller's team"}}


def _sprint_or_404(session: Session, me, sprint_id: int) -> Sprint:
    found = session.scalars(
        select(Sprint).where(Sprint.id == sprint_id, Sprint.team_id == me.team_id)
    ).first()
    if found is None:
        raise services.Refused(404, "not_found", "No such sprint.")
    return found


def _counts(
    session: Session, user: User, task_filter, ids: list[int]
) -> dict[int, tuple[int, int]]:
    rows = session.execute(
        select(Task.sprint_id, func.count(), func.count().filter(Task.status == "done"))
        .where(Task.sprint_id.in_(ids), tasks.visible(user), task_filter)
        .group_by(Task.sprint_id)
    )
    return {sid: (total, done) for sid, total, done in rows}


def _out(sprint: Sprint, state: str, total: int, done: int) -> SprintOut:
    return SprintOut(
        id=sprint.id,
        number=sprint.number,
        name=sprints.name_of(sprint),
        start_date=sprint.start_date,
        end_date=sprint.end_date,
        state=state,
        closed_at=sprint.closed_at,
        closed_early=sprint.closed_early,
        tasks_total=total,
        tasks_done=done,
    )


def present(
    session: Session,
    user: User,
    team: Team,
    key: int | None,
    rows: list[Sprint],
    project_id: int | None,
) -> list[SprintOut]:
    """The sprints with their counts. Open ones of the team's series stand for every series' one
    in the same place (the board of "all projects"); a project's own series is counted alone."""
    task_filter = Task.project_id == project_id if project_id is not None else True
    roles = sprints.role_ids(session, team.id)
    opened = sprints.open_sprints(session, team.id, key)
    out = []
    for sprint in rows:
        state = sprints.role_of(sprint, opened)
        if state != "closed" and key is None:
            ids = list(roles[state])
        else:
            ids = [sprint.id]
        counted = _counts(session, user, task_filter, ids).values()
        total, done = sum(t for t, _ in counted), sum(d for _, d in counted)
        out.append(_out(sprint, state, total, done))
    return out


@router.get("/sprints", tags=["sprints"], responses=NOT_FOUND)
def list_sprints(
    project: int | None = Query(None, ge=1, le=services.MAX_ID),
    session: Session = Depends(get_session),
    user: User = Depends(auth.current_user),
) -> SprintList:
    """The sprints of the board: up to 20 closed ones, the current and the next. With no
    `project` it is the team's series, counting the tasks of every project; with a project it is
    that project's series (its own when it has a sprint length of its own, else the team's)
    counting that project's tasks. The first call for a team makes its first two sprints."""
    me = team_admin.my_membership(session, user)
    team = session.get(Team, me.team_id)
    key, found = None, None
    if project is not None:
        found = projects.project_of_team(session, me.team_id, project)
        if found is None:
            raise services.Refused(404, "not_found", "No such project.")
        key = sprints.series_key(session, me.team_id, project)
    today = sprints.team_today(session, team)
    current, nxt = sprints.ensure_series(session, team, key, today)
    session.commit()
    closed = list(
        reversed(
            session.scalars(
                sprints.series_query(me.team_id, key)
                .where(Sprint.closed_at.is_not(None))
                .order_by(Sprint.number.desc())
                .limit(sprints.CLOSED_SHOWN)
            ).all()
        )
    )
    shown = present(session, user, team, key, [*closed, current, nxt], project)
    length = sprints.length_of(team, found if key is not None else None)
    return SprintList(
        project_id=project,
        own_series=key is not None,
        today=sprints.user_today(session, user),
        sprint_length=length,
        sprint_custom_days=team.sprint_custom_days,
        sprint_align_weekdays=team.sprint_align_weekdays,
        sprints=shown,
        current_id=current.id,
        next_id=nxt.id,
        can_manage=me.role in team_admin.MANAGER_ROLES,
    )


@router.post(
    "/sprints/{sprint_id:id}/close",
    tags=["sprints"],
    responses={
        **FORBIDDEN,
        **NOT_FOUND,
        409: {"model": ErrorResponse, "description": "`sprint_not_current`"},
    },
)
def close_sprint(
    sprint_id: int,
    session: Session = Depends(get_session),
    user: User = Depends(auth.current_user),
) -> SprintClosed:
    """Close the current sprint now (owner or admin). Its unfinished tasks move to the next
    sprint and are marked carried over; the next sprint starts today (its end date does not
    change) and a new next is made. 409 `sprint_not_current` for a sprint that is closed or is
    still the next one."""
    me = team_admin.my_membership(session, user)
    sprint = _sprint_or_404(session, me, sprint_id)
    team_admin.require_manager(me)
    team = session.get(Team, me.team_id)
    now = datetime.now(UTC)
    sprints.lock(session, team.id)
    session.refresh(sprint)  # re-read under the lock: someone else may have closed it first
    session.refresh(team)
    key = sprint.project_id
    opened = sprints.open_sprints(session, team.id, key)
    if sprint.closed_at is not None or not opened or opened[0].id != sprint.id:
        raise services.Refused(409, "sprint_not_current", "Only the current sprint can be closed.")
    moved = session.scalar(
        select(func.count())
        .select_from(Task)
        .where(
            Task.sprint_id == sprint.id,
            Task.status != "done",
            tasks.visible(user),
        )
    )
    today = sprints.team_today(session, team, now)
    sprints.close_current(session, team, key, now, today, early=today < sprint.end_date)
    session.commit()
    after = sprints.open_sprints(session, team.id, key)
    both = present(session, user, team, key, [sprint, after[0]], key)
    return SprintClosed(closed=both[0], current=both[1], moved=moved or 0)
