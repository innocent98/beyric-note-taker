"""The Tasks screen's JSON routes, the meeting page's task list and the Tasks settings. Included
into `app.api.router`, so they share its `/api` prefix, login check, CSRF rule and error shape.

A task nobody may see is 404, never 403. A task the caller can see but not change is 403
`team_forbidden` (`app/tasks.py` holds the rules). No model call happens in a request: the tasks
are made by the worker (`app/workspace.py`).
"""

from datetime import UTC, datetime
from typing import Literal

from fastapi import APIRouter, Depends, Query, Response
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session
from starlette.exceptions import HTTPException as StarletteHTTPException

from app import auth, mail, services, tasks, teams, timezones, titles, workspace
from app.api_schemas import (
    AttentionOut,
    ErrorResponse,
    ExtractQueued,
    MeetingRef,
    MeetingTasks,
    PersonOut,
    TaskCreate,
    TaskList,
    TaskOut,
    TaskPatch,
    TasksSettingsIn,
    TasksSettingsOut,
)
from app.db import get_session
from app.extraction import TranscriptIndex
from app.models import Meeting, Task, User, UserSettings
from app.visibility import can_read_clause, owns

router = APIRouter()

FORBIDDEN = "You can't do that to this task. Ask an owner or admin of your team."
SEE_ONLY = {403: {"model": ErrorResponse, "description": "`team_forbidden`"}}


def _person(user: User, viewer: User) -> PersonOut:
    return PersonOut(
        user_id=user.id,
        name=titles.clean_title(user.name, 100) or user.email.split("@")[0],
        is_you=user.id == viewer.id,
    )


def present(
    session: Session, viewer: User, rows: list[Task], *, evidence: bool = False
) -> list[TaskOut]:
    """The tasks as the screen shows them, with the assignee's name, the meeting when the viewer
    may read it, and what the viewer may do. Two queries however many tasks. Who said the quote
    and on which transcript row (`quote_speaker`, `quote_line`) is worked out for a suggested task,
    and for any task when `evidence` is set (one task asked for by id): reading a transcript for
    every task of a long list would cost more than the board needs."""
    people = {
        u.id: u
        for u in session.scalars(
            select(User).where(
                User.id.in_({t.assignee_user_id for t in rows if t.assignee_user_id})
            )
        )
    }
    meetings = {
        m.id: m
        for m in session.scalars(
            select(Meeting).where(
                Meeting.id.in_({t.meeting_id for t in rows if t.meeting_id}),
                can_read_clause(viewer),
            )
        )
    }
    members = tasks.memberships(session, viewer)  # the viewer may be on several teams
    indexes: dict[int, TranscriptIndex] = {}
    out = []
    for task in rows:
        allowed = tasks.permissions(task, viewer, members.get(task.team_id))
        meeting = meetings.get(task.meeting_id) if task.meeting_id else None
        # Where the quote was said: only for a meeting the viewer may read (an assignee of a
        # private meeting's task gets the quote and nothing around it, spec 8.2).
        said = None
        if meeting is not None and task.quote and (task.suggested or evidence):
            if meeting.id not in indexes:  # each transcript is read once per request
                indexes[meeting.id] = TranscriptIndex(
                    meeting.speaker_transcript or meeting.transcript or ""
                )
            said = indexes[meeting.id].locate(task.quote)
        out.append(
            TaskOut(
                id=task.id,
                title=task.title,
                status=task.status,
                assignee=(
                    _person(people[task.assignee_user_id], viewer)
                    if task.assignee_user_id in people
                    else None
                ),
                spoken_owner=task.spoken_owner,
                due_date=task.due_date,
                due_phrase=task.due_phrase,
                quote=task.quote,
                is_new=task.is_new,
                unverified=task.unverified,
                source=task.source,
                assigned_on=task.assigned_on,
                done_at=task.done_at,
                created_at=task.created_at,
                from_meeting=task.meeting_id is not None,
                meeting=(
                    MeetingRef(
                        id=meeting.id,
                        title=services.meeting_title(meeting),
                        platform=meeting.platform,
                        created_at=meeting.created_at,
                    )
                    if meeting
                    else None
                ),
                project_id=task.project_id,
                milestone_id=task.milestone_id,
                sprint_id=task.sprint_id,
                carried=task.carried,
                check_project=task.check_project,
                suggested=task.suggested,
                confidence=task.confidence,
                # The model's reason may quote the speaker, so, like the speaker and the row, only
                # for someone who may read the meeting.
                reason=task.reason if meeting is not None else None,
                quote_speaker=said[0] if said else None,
                quote_line=said[1] if said else None,
                can_update=allowed.update,
                can_edit=allowed.edit,
                can_assign=allowed.assign,
                can_claim=allowed.claim,
                can_delete=allowed.delete,
                can_decide=task.suggested
                and (allowed.edit or (meeting is not None and owns(session, viewer, meeting))),
            )
        )
    return out


def _task_or_404(session: Session, user: User, task_id: int) -> Task:
    task = tasks.get(session, user, task_id)
    if task is None:
        raise StarletteHTTPException(404, "No such task")
    return task


def _team_people(session: Session, user: User) -> list[PersonOut]:
    team = teams.ensure_team(session, user)
    return [
        PersonOut(user_id=m.user_id, name=m.display_name, is_you=m.user_id == user.id)
        for m in tasks.members_of(session, team.id)
    ]


@router.get("/tasks", tags=["tasks"], responses={422: {"model": ErrorResponse}})
def list_tasks(
    assignee: str | None = Query(
        None,
        pattern=r"^(me|none|\d{1,10})$",
        description="`me`, `none` (Needs owner) or a user id",
    ),
    status: Literal["todo", "doing", "done"] | None = None,
    suggested: bool = Query(
        False,
        description="`false` (the default): the Board and Backlog tasks, suggested ones left out. "
        "`true`: only the suggested tasks (the Suggested tasks section).",
    ),
    project: str | None = Query(
        None,
        pattern=r"^(none|\d{1,10})$",
        description="`none` (No project) or a project id",
    ),
    milestone: int | None = Query(None, ge=1, le=services.MAX_ID),
    sprint: str | None = Query(
        None,
        pattern=r"^(none|current|next|\d{1,10})$",
        description="`none` (Backlog), `current`, `next` or a sprint id",
    ),
    limit: int = Query(tasks.LIST_DEFAULT, ge=1, le=tasks.LIST_MAX),
    before_id: int | None = Query(None, ge=1, le=services.MAX_ID),
    session: Session = Depends(get_session),
    user: User = Depends(auth.current_user),
) -> TaskList:
    """The tasks the caller can see: those assigned to them, those from meetings they can read,
    and their team's manual tasks. Newest first; for the next page pass `before_id` =
    `next_before_id`. `attention` counts every open task the caller sees, whatever the filter; a
    suggested task is in no count."""
    rows, more = tasks.list_tasks(
        session,
        user,
        assignee=assignee,
        status=status,
        limit=limit,
        before_id=before_id,
        project=project,
        milestone=milestone,
        sprint=sprint,
        suggested=suggested,
    )
    return TaskList(
        tasks=present(session, user, rows),
        people=_team_people(session, user),
        attention=AttentionOut(**tasks.attention(session, user)),
        has_more=more,
        next_before_id=rows[-1].id if more else None,
    )


@router.post(
    "/tasks",
    status_code=201,
    tags=["tasks"],
    responses={422: {"model": ErrorResponse, "description": "`assignee_not_on_team`"}},
)
def create_task(
    body: TaskCreate,
    session: Session = Depends(get_session),
    user: User = Depends(auth.current_user),
) -> TaskOut:
    """Make a task by hand, in the caller's team. Anyone on the team may. It has no meeting."""
    zone = _zone(session, user)
    try:
        task = tasks.create(
            session,
            user,
            title=body.title,
            assignee_user_id=body.assignee_user_id,
            due_date=body.due_date,
            status=body.status,
            project_id=body.project_id,
            sprint_id=body.sprint_id if "sprint_id" in body.model_fields_set else tasks.UNSET,
            today=timezones.localise(datetime.now(UTC), zone)[0].date(),
        )
    except tasks.TaskInvalid as exc:
        raise services.Refused(422, exc.code, exc.message) from None
    return present(session, user, [task])[0]


def _zone(session: Session, user: User) -> str | None:
    return session.scalar(select(UserSettings.time_zone).where(UserSettings.user_id == user.id))


@router.get("/tasks/{task_id:id}", tags=["tasks"], responses={404: {"model": ErrorResponse}})
def get_task(
    task_id: int,
    session: Session = Depends(get_session),
    user: User = Depends(auth.current_user),
) -> TaskOut:
    return present(session, user, [_task_or_404(session, user, task_id)], evidence=True)[0]


@router.patch(
    "/tasks/{task_id:id}",
    tags=["tasks"],
    responses={**SEE_ONLY, 404: {"model": ErrorResponse}, 422: {"model": ErrorResponse}},
)
def patch_task(
    task_id: int,
    body: TaskPatch,
    session: Session = Depends(get_session),
    user: User = Depends(auth.current_user),
) -> TaskOut:
    """Change some fields of a task. Owners and admins may change anything. A member may change
    the status and due date of a task assigned to them, take a task that has no owner (set
    `assignee_user_id` to themselves), and change everything about a task they made; anything
    more is 403 `team_forbidden` and nothing is changed. A status change clears "New"."""
    task = _task_or_404(session, user, task_id)
    changes = {k: getattr(body, k) for k in body.model_fields_set}
    if changes.get("status", "") is None or changes.get("title", "") is None:
        raise services.Refused(422, "validation_error", "Status and title cannot be null.")
    if "check_project" in changes and changes["check_project"] is not False:
        raise services.Refused(422, "validation_error", "check_project can only be set to false.")
    try:
        tasks.update(session, user, task, changes, datetime.now(UTC))
    except tasks.TaskForbidden:
        raise services.Refused(403, "team_forbidden", FORBIDDEN) from None
    except tasks.TaskInvalid as exc:
        raise services.Refused(422, exc.code, exc.message) from None
    return present(session, user, [task])[0]


@router.delete(
    "/tasks/{task_id:id}",
    status_code=204,
    tags=["tasks"],
    responses={**SEE_ONLY, 404: {"model": ErrorResponse}},
)
def delete_task(
    task_id: int,
    session: Session = Depends(get_session),
    user: User = Depends(auth.current_user),
) -> Response:
    """Delete a task. Owners and admins may delete any task they can see; a member only the tasks
    they made, and the person who owns a meeting may also dismiss (delete) a suggested task from
    it. An AI task deleted here does not come back when the notes are read again."""
    task = _task_or_404(session, user, task_id)
    try:
        tasks.delete(session, user, task)
    except tasks.TaskForbidden:
        raise services.Refused(403, "team_forbidden", FORBIDDEN) from None
    return Response(status_code=204)


@router.post(
    "/tasks/{task_id:id}/accept",
    tags=["tasks"],
    responses={
        **SEE_ONLY,
        404: {"model": ErrorResponse},
        409: {"model": ErrorResponse, "description": "`not_suggested`"},
    },
)
def accept_task(
    task_id: int,
    session: Session = Depends(get_session),
    user: User = Depends(auth.current_user),
) -> TaskOut:
    """Accept a suggested task: it joins the board, is filed into a project and sprint as a new
    task is, and its assignee is emailed as they would have been (when the meeting owner's "Email
    assignees" is on and they were not already mailed about this meeting). Whoever may edit the
    task, or owns the meeting it came from, may accept it (403 `team_forbidden` otherwise); a
    person who cannot read the meeting gets 404.
    Idempotent: accepting a task that was already accepted answers 200 with the task and sends
    nothing. 409 `not_suggested` for a task that never was a suggestion. To dismiss one, `DELETE`
    it."""
    task = _task_or_404(session, user, task_id)
    try:
        accepted = tasks.accept(session, user, task)
    except tasks.TaskForbidden:
        session.rollback()
        raise services.Refused(403, "team_forbidden", FORBIDDEN) from None
    except tasks.NotSuggested:
        session.rollback()
        raise services.Refused(
            409, "not_suggested", "This task is not waiting to be confirmed."
        ) from None
    if accepted and task.meeting_id is not None:
        _mail_accepted(session, task)
    session.commit()
    return present(session, user, [task], evidence=True)[0]


def _mail_accepted(session: Session, task: Task) -> None:
    """What creation would have sent: the assignee mail, for a person who is not the meeting's
    owner, when the owner emails assignees and the meeting is not a quiet or an old one. The
    mail's own once-per-person-per-meeting rule stands: someone already mailed about this meeting
    is not mailed again. In the caller's transaction."""
    meeting = session.get(Meeting, task.meeting_id)
    if meeting is None:
        return
    prefs = workspace.owner_prefs(session, meeting.owner_user_id)
    recent = datetime.now(UTC) - meeting.created_at < mail.KEEP_SENT_FOR
    if prefs.email_assignees and recent and not meeting.extraction_quiet:
        mail.queue_tasks_assigned(
            session, meeting, tasks.new_assignees([task], meeting.owner_user_id)
        )


@router.get(
    "/meetings/{meeting_id:id}/tasks",
    tags=["tasks"],
    responses={404: {"model": ErrorResponse}},
)
def meeting_tasks(
    meeting_id: int,
    session: Session = Depends(get_session),
    user: User = Depends(auth.current_user),
) -> MeetingTasks:
    """A meeting's action items as tasks, for its page ("8 tasks created, assigned to 3 people").
    Anyone who can read the meeting may ask."""
    meeting = services.visible_meeting_or_404(session, user, meeting_id)
    rows = tasks.meeting_tasks(session, user, meeting.id)
    return MeetingTasks(
        tasks=present(session, user, rows),
        assigned_people=len(
            {t.assignee_user_id for t in rows if t.assignee_user_id and not t.suggested}
        ),
        to_confirm=sum(1 for t in rows if t.suggested),
        extraction_status=meeting.extraction_status if meeting.status == "ready" else None,
    )


@router.post(
    "/meetings/{meeting_id:id}/tasks/extract",
    status_code=202,
    tags=["tasks"],
    responses={404: {"model": ErrorResponse}, 409: {"model": ErrorResponse}},
)
def extract_tasks(
    meeting_id: int,
    session: Session = Depends(get_session),
    user: User = Depends(auth.current_user),
) -> ExtractQueued:
    """Read the tasks out of an older meeting's notes ("Extract tasks from this meeting"). Owner
    only. 409 `extraction_pending` while the meeting is already being read, 409 `tasks_off` when
    the owner has switched tasks off. The tasks it makes are not New and nobody is emailed."""
    meeting = services.owned_meeting_or_404(session, user, meeting_id)
    if meeting.status != "ready" or not meeting.notes:
        raise services.Refused(409, "not_ready", "This meeting has no notes to read yet.")
    if meeting.extraction_status in ("pending", "running"):
        raise services.Refused(
            409, "extraction_pending", "This meeting is already being read. Check back shortly."
        )
    if not workspace.owner_prefs(session, user.id)[0]:
        raise services.Refused(
            409, "tasks_off", "Turn on “Create tasks from action items” in Settings first."
        )
    workspace.mark_pending(meeting, quiet=True)
    session.commit()
    return ExtractQueued(extraction_status="pending")


# ---- Settings > Tasks ----

settings_router = APIRouter(prefix="/settings", tags=["settings"])


@settings_router.get("/tasks")
def read_tasks_settings(
    session: Session = Depends(get_session), user: User = Depends(auth.current_user)
) -> TasksSettingsOut:
    """The three switches of Settings > Tasks (all on by default, also for an account with no
    settings row yet)."""
    prefs = workspace.owner_prefs(session, user.id)
    return TasksSettingsOut(
        auto_create_tasks=prefs.auto_create_tasks,
        email_assignees=prefs.email_assignees,
        ask_before_unclear=prefs.ask_before_unclear,
    )


@settings_router.put("/tasks")
def write_tasks_settings(
    body: TasksSettingsIn,
    session: Session = Depends(get_session),
    user: User = Depends(auth.current_user),
) -> TasksSettingsOut:
    """Set the switches. They are read when a meeting is read (tasks, and which are suggestions) and
    when the mail is sent (assignees), so turning email off also stops mail already queued.
    `ask_before_unclear` may be left out: it then stays as it is (a client from before it existed).
    Turning it off does not touch the tasks already waiting under Suggested tasks."""
    values = {"auto_create_tasks": body.auto_create_tasks, "email_assignees": body.email_assignees}
    if body.ask_before_unclear is not None:
        values["ask_before_unclear"] = body.ask_before_unclear
    session.execute(
        insert(UserSettings)
        .values(user_id=user.id, **values)
        .on_conflict_do_update(index_elements=["user_id"], set_=values)
    )
    session.commit()
    return read_tasks_settings(session, user)


router.include_router(settings_router)
