"""The Projects screen's routes, the milestones on the Tasks board, and the meeting page's
"Set milestone" line (spec 6.3). Included into `app.api.router`, so they share its `/api`
prefix, login check, CSRF rule and error shape.

Projects and milestones belong to the caller's team. Every member reads them; an owner or admin
creates, changes and deletes (403 `team_forbidden` for a member). An id that is not on the
caller's team is 404, never 403. Counts inside a project or milestone follow what the caller may
see (`app/tasks.visible`). No model call happens in a request: the proposals are made by the
worker (`app/workspace.py`).
"""

from datetime import UTC, date, datetime

from fastapi import APIRouter, Depends, Query, Response
from sqlalchemy import select
from sqlalchemy.orm import Session

from app import auth, filing, projects, services, team_admin, timezones, titles
from app.api_schemas import (
    ErrorResponse,
    MeetingMilestones,
    MeetingRef,
    MilestoneConfirm,
    MilestoneIn,
    MilestoneLine,
    MilestoneList,
    MilestoneOut,
    MilestonePatch,
    ProjectIn,
    ProjectList,
    ProjectOut,
    ProjectPatch,
)
from app.db import get_session
from app.models import Meeting, Milestone, Project, User, UserSettings
from app.visibility import can_read_clause

router = APIRouter()

FORBIDDEN = {403: {"model": ErrorResponse, "description": "`team_forbidden`"}}
NOT_FOUND = {404: {"model": ErrorResponse, "description": "Not on the caller's team"}}
INVALID = {422: {"model": ErrorResponse}}


def _today(session: Session, user: User) -> date:
    zone = session.scalar(select(UserSettings.time_zone).where(UserSettings.user_id == user.id))
    return timezones.localise(datetime.now(UTC), zone)[0].date()


def _invalid(exc: projects.ProjectInvalid) -> services.Refused:
    return services.Refused(422, exc.code, exc.message)


def _project_or_404(session: Session, me, project_id: int) -> Project:
    found = projects.project_of_team(session, me.team_id, project_id)
    if found is None:
        raise services.Refused(404, "not_found", "No such project.")
    return found


def _milestone_or_404(session: Session, user: User, me, milestone_id: int) -> Milestone:
    """A milestone of the caller's team. A proposal is part of its meeting (spec 8.2): someone who
    cannot read that meeting gets 404 on it, whatever their role, so an admin cannot confirm or
    read a date out of a private meeting by trying ids."""
    found = projects.milestone_of_team(session, me.team_id, milestone_id)
    if found is not None and found.confirmed_at is None:
        # Fail closed: a proposal whose meeting is gone or unreadable is no one's.
        readable = (
            session.scalar(
                select(Meeting.id).where(
                    Meeting.id == found.source_meeting_id, can_read_clause(user)
                )
            )
            if found.source_meeting_id is not None
            else None
        )
        if readable is None:
            found = None
    if found is None:
        raise services.Refused(404, "not_found", "No such milestone.")
    return found


def _project_out(
    rows: list[Project], counts: dict[int, int], headline: dict[int, Milestone]
) -> list[ProjectOut]:
    return [
        ProjectOut(
            id=p.id,
            name=p.name,
            color=p.color,
            keywords=list(p.keywords or []),
            open_tasks=counts.get(p.id, 0),
            milestone=(
                MilestoneLine(
                    id=headline[p.id].id, name=headline[p.id].name, due_date=headline[p.id].due_date
                )
                if p.id in headline
                else None
            ),
            created_at=p.created_at,
        )
        for p in rows
    ]


def present_projects(
    session: Session, user: User, team_id: int, rows: list[Project]
) -> list[ProjectOut]:
    today = _today(session, user)
    counts = projects.open_task_counts(session, user, [p.id for p in rows])
    by_project: dict[int, list[Milestone]] = {}
    for milestone in projects.confirmed_milestones(session, team_id):
        if milestone.project_id is not None:
            by_project.setdefault(milestone.project_id, []).append(milestone)
    headline = {
        pid: line
        for pid, items in by_project.items()
        if (line := projects.headline_milestone(items, today)) is not None
    }
    return _project_out(rows, counts, headline)


@router.get("/projects", tags=["projects"])
def list_projects(
    session: Session = Depends(get_session), user: User = Depends(auth.current_user)
) -> ProjectList:
    """The team's projects, oldest first. Every member reads them. `open_tasks` counts only the
    open tasks the caller may see."""
    me = team_admin.my_membership(session, user)
    rows = filing.team_projects(session, me.team_id)
    return ProjectList(
        projects=present_projects(session, user, me.team_id, rows),
        can_manage=me.role in team_admin.MANAGER_ROLES,
    )


@router.post("/projects", status_code=201, tags=["projects"], responses={**FORBIDDEN, **INVALID})
def create_project(
    body: ProjectIn,
    session: Session = Depends(get_session),
    user: User = Depends(auth.current_user),
) -> ProjectOut:
    """Make a project (owner or admin). A blank name is allowed: the screen opens a blank project
    and saves the name as it is typed."""
    me = team_admin.my_membership(session, user)
    team_admin.require_manager(me)
    try:
        project = projects.create(
            session, me.team_id, name=body.name, color=body.color, keywords=body.keywords
        )
    except projects.ProjectInvalid as exc:
        raise _invalid(exc) from None
    return present_projects(session, user, me.team_id, [project])[0]


@router.get("/projects/{project_id:id}", tags=["projects"], responses=NOT_FOUND)
def get_project(
    project_id: int,
    session: Session = Depends(get_session),
    user: User = Depends(auth.current_user),
) -> ProjectOut:
    me = team_admin.my_membership(session, user)
    project = _project_or_404(session, me, project_id)
    return present_projects(session, user, me.team_id, [project])[0]


@router.patch(
    "/projects/{project_id:id}", tags=["projects"], responses={**FORBIDDEN, **NOT_FOUND, **INVALID}
)
def patch_project(
    project_id: int,
    body: ProjectPatch,
    session: Session = Depends(get_session),
    user: User = Depends(auth.current_user),
) -> ProjectOut:
    """Change the name, colour or keywords (owner or admin). The screen sends one field per
    change, debounced; a field left out is untouched. `keywords` replaces the whole list."""
    me = team_admin.my_membership(session, user)
    project = _project_or_404(session, me, project_id)
    team_admin.require_manager(me)
    changes = {k: getattr(body, k) for k in body.model_fields_set}
    if any(v is None for v in changes.values()):
        raise services.Refused(422, "validation_error", "A field cannot be null.")
    try:
        projects.update(session, project, changes)
    except projects.ProjectInvalid as exc:
        raise _invalid(exc) from None
    return present_projects(session, user, me.team_id, [project])[0]


@router.delete(
    "/projects/{project_id:id}",
    status_code=204,
    tags=["projects"],
    responses={**FORBIDDEN, **NOT_FOUND},
)
def delete_project(
    project_id: int,
    session: Session = Depends(get_session),
    user: User = Depends(auth.current_user),
) -> Response:
    """Delete a project (owner or admin). Its tasks stay and now have no project; its milestones
    go with it."""
    me = team_admin.my_membership(session, user)
    project = _project_or_404(session, me, project_id)
    team_admin.require_manager(me)
    projects.delete(session, project)
    return Response(status_code=204)


# ---- milestones ----


def present_milestones(
    session: Session, user: User, me, rows: list[Milestone]
) -> list[MilestoneOut]:
    progress = projects.milestone_progress(
        session, user, [m.id for m in rows if m.confirmed_at is not None]
    )
    meetings = {
        m.id: m
        for m in session.scalars(
            select(Meeting).where(
                Meeting.id.in_({m.source_meeting_id for m in rows if m.source_meeting_id}),
                can_read_clause(user),
            )
        )
    }
    manager = me.role in team_admin.MANAGER_ROLES
    out = []
    for m in rows:
        source = meetings.get(m.source_meeting_id) if m.source_meeting_id else None
        total, done = progress.get(m.id, (0, 0))
        out.append(
            MilestoneOut(
                id=m.id,
                name=m.name,
                due_date=m.due_date,
                project_id=m.project_id,
                source_meeting=(
                    MeetingRef(
                        id=source.id,
                        title=services.meeting_title(source),
                        platform=source.platform,
                        created_at=source.created_at,
                    )
                    if source
                    else None
                ),
                # The words said belong to the meeting: only for someone who may read it.
                quote=m.quote if source else None,
                confirmed_at=m.confirmed_at,
                task_count=total,
                done_count=done,
                can_confirm=manager and m.confirmed_at is None,
                can_edit=manager,
            )
        )
    return out


@router.get("/milestones", tags=["projects"])
def list_milestones(
    project_id: int | None = Query(None, ge=1, le=services.MAX_ID),
    session: Session = Depends(get_session),
    user: User = Depends(auth.current_user),
) -> MilestoneList:
    """The team's confirmed milestones, soonest first, with how many tasks are filed under each
    and how many are done (over the tasks the caller may see). Proposals are not listed here:
    they live on the meeting they came from (`GET /api/meetings/{id}/milestones`)."""
    me = team_admin.my_membership(session, user)
    rows = projects.confirmed_milestones(session, me.team_id, project_id=project_id)
    return MilestoneList(milestones=present_milestones(session, user, me, rows))


def _project_for(session: Session, me, project_id: int | None) -> None:
    if project_id is not None and projects.project_of_team(session, me.team_id, project_id) is None:
        raise services.Refused(422, "project_not_on_team", "That project is not on your team.")


@router.post("/milestones", status_code=201, tags=["projects"], responses={**FORBIDDEN, **INVALID})
def create_milestone(
    body: MilestoneIn,
    session: Session = Depends(get_session),
    user: User = Depends(auth.current_user),
) -> MilestoneOut:
    """Make a milestone by hand (owner or admin). It is confirmed at once."""
    me = team_admin.my_membership(session, user)
    team_admin.require_manager(me)
    _project_for(session, me, body.project_id)
    row = Milestone(
        team_id=me.team_id,
        project_id=body.project_id,
        name=titles.clean_title(body.name, projects.MAX_MILESTONE_CHARS),
        due_date=body.due_date,
        confirmed_at=datetime.now(UTC),
    )
    if not row.name:
        raise services.Refused(422, "invalid_name", "Give the milestone a name.")
    session.add(row)
    session.commit()
    return present_milestones(session, user, me, [row])[0]


@router.patch(
    "/milestones/{milestone_id:id}",
    tags=["projects"],
    responses={**FORBIDDEN, **NOT_FOUND, **INVALID},
)
def patch_milestone(
    milestone_id: int,
    body: MilestonePatch,
    session: Session = Depends(get_session),
    user: User = Depends(auth.current_user),
) -> MilestoneOut:
    """Rename, move to another date or another project (owner or admin)."""
    me = team_admin.my_membership(session, user)
    row = _milestone_or_404(session, user, me, milestone_id)
    team_admin.require_manager(me)
    changes = {k: getattr(body, k) for k in body.model_fields_set}
    if changes.get("name", "") is None or changes.get("due_date", "") is None:
        raise services.Refused(422, "validation_error", "Name and date cannot be null.")
    if "project_id" in changes:
        _project_for(session, me, changes["project_id"])
        row.project_id = changes["project_id"]
    if "name" in changes:
        name = titles.clean_title(changes["name"], projects.MAX_MILESTONE_CHARS)
        if not name:
            raise services.Refused(422, "invalid_name", "Give the milestone a name.")
        row.name = name
    if "due_date" in changes:
        row.due_date = changes["due_date"]
    session.commit()
    return present_milestones(session, user, me, [row])[0]


@router.delete(
    "/milestones/{milestone_id:id}",
    status_code=204,
    tags=["projects"],
    responses={**FORBIDDEN, **NOT_FOUND},
)
def delete_milestone(
    milestone_id: int,
    session: Session = Depends(get_session),
    user: User = Depends(auth.current_user),
) -> Response:
    """Delete a milestone, or turn a proposal down (owner or admin). Tasks filed under it stay,
    with no milestone."""
    me = team_admin.my_membership(session, user)
    row = _milestone_or_404(session, user, me, milestone_id)
    team_admin.require_manager(me)
    session.delete(row)
    session.commit()
    return Response(status_code=204)


@router.post(
    "/milestones/{milestone_id:id}/confirm",
    tags=["projects"],
    responses={**FORBIDDEN, **NOT_FOUND, **INVALID},
)
def confirm_milestone(
    milestone_id: int,
    body: MilestoneConfirm | None = None,
    session: Session = Depends(get_session),
    user: User = Depends(auth.current_user),
) -> MilestoneOut:
    """Turn a proposal into a milestone ("Set milestone: Client Portal launch, 8 Dec?" then
    Confirm). Owner or admin. Confirming one that is already confirmed changes nothing. Send a
    `project_id` to choose the project; left out, the one it was filed under stays."""
    me = team_admin.my_membership(session, user)
    row = _milestone_or_404(session, user, me, milestone_id)
    team_admin.require_manager(me)
    chosen = body is not None and "project_id" in body.model_fields_set
    if chosen:
        _project_for(session, me, body.project_id)
    if row.confirmed_at is None:
        projects.confirm(session, row, body.project_id if chosen else None, set_project=chosen)
    return present_milestones(session, user, me, [row])[0]


@router.get(
    "/meetings/{meeting_id:id}/milestones",
    tags=["projects"],
    responses={404: {"model": ErrorResponse}},
)
def meeting_milestones(
    meeting_id: int,
    session: Session = Depends(get_session),
    user: User = Depends(auth.current_user),
) -> MeetingMilestones:
    """The dates proposed from this meeting and the milestones already set from it, for its page.
    Anyone who can read the meeting may ask; `can_confirm` says whether the caller may confirm."""
    meeting = services.visible_meeting_or_404(session, user, meeting_id)
    me = team_admin.my_membership(session, user)
    rows = projects.meeting_milestones(session, user, meeting.id, me.team_id)
    return MeetingMilestones(milestones=present_milestones(session, user, me, rows))
