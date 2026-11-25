"""`/api/workspace-prefs`: what a person sets once per workspace instead of once overall.

Workspaces design, section 3: the note taker's name, the auto-join rule and its two modifiers, and
the template their calendar's meetings use. Everything else on the Settings screen is either the
person's own (`/api/settings/...`, which never switches) or the workspace's (`PATCH /api/team`,
owners and admins).

Included into `app.api.router`, so it shares its `/api` prefix, login and CSRF rules and error
shape. Acts on the ACTIVE workspace (the `X-Team-Id` header, else the default workspace) and only
ever on the caller's own row there: any member may change their own, nobody else's.
"""

from fastapi import APIRouter, Depends, Request
from sqlalchemy.orm import Session

from app import (
    auth,
    bot_names,
    calendar_api,
    calendar_connect,
    services,
    workspace_context,
    workspace_prefs,
)
from app.api_schemas import ErrorResponse, WorkspacePrefsIn, WorkspacePrefsOut
from app.db import get_session
from app.models import Team, User

router = APIRouter(prefix="/workspace-prefs", tags=["settings"])

ERRORS = {
    404: {
        "model": ErrorResponse,
        "description": "A team the caller is not in, or a template of another workspace",
    },
    422: {"model": ErrorResponse, "description": "`bot_name_invalid`"},
}


def _out(request: Request, session: Session, user: User, team: Team) -> WorkspacePrefsOut:
    prefs = workspace_prefs.for_user(session, user.id, team.id)
    default = user.bot_name or bot_names.default_name(request.app.state.settings, None)
    return WorkspacePrefsOut(
        team_id=team.id,
        bot_name=prefs.own_bot_name,
        bot_name_default=default,
        warning=bot_names.recorder_warning(prefs.own_bot_name),
        auto_join_rule=prefs.auto_join_rule,
        join_unanswered=prefs.join_unanswered,
        skip_one_on_ones=prefs.skip_one_on_ones,
        calendar_template_id=prefs.calendar_template_id,
    )


@router.get("", responses={404: ERRORS[404]})
def read_workspace_prefs(
    request: Request,
    workspace: Team = Depends(workspace_context.current),
    session: Session = Depends(get_session),
    user: User = Depends(auth.current_user),
) -> WorkspacePrefsOut:
    """The caller's settings in the active workspace: their note taker name there, the auto-join
    rule, "also join invites I haven't answered", "skip one-on-ones" and the calendar template. A
    workspace where they never changed anything shows their own defaults."""
    return _out(request, session, user, workspace)


@router.put("", responses=ERRORS)
def write_workspace_prefs(
    body: WorkspacePrefsIn,
    request: Request,
    workspace: Team = Depends(workspace_context.current),
    session: Session = Depends(get_session),
    user: User = Depends(auth.current_user),
) -> WorkspacePrefsOut:
    """Change the caller's settings in the active workspace (CSRF header needed); a field left out
    stays as it is, and nothing of any other workspace is touched. Changing the rule or a box
    decides the workspace's stored calendar events again, as `PUT /api/calendar/settings` does."""
    sent = body.model_fields_set
    name_given = "bot_name" in sent
    new_name = None
    if name_given:
        try:
            new_name = bot_names.clean_bot_name(body.bot_name)
        except bot_names.BotNameInvalid as exc:
            raise services.Refused(422, "bot_name_invalid", str(exc)) from None
    set_template = "calendar_template_id" in sent
    if set_template and body.calendar_template_id is not None:
        services.template_or_404(session, workspace, body.calendar_template_id)
    if name_given:
        workspace_prefs.row_for_update(session, user.id, workspace.id).bot_name = new_name
    conn = calendar_connect.live_calendar_connection(session, user)
    calendar_api.apply_prefs(
        session,
        user,
        conn,
        workspace.id,
        request.app.state.settings,
        rule=body.auto_join_rule,
        join_unanswered=body.join_unanswered,
        skip_one_on_ones=body.skip_one_on_ones,
        set_template=set_template,
        calendar_template_id=body.calendar_template_id,
    )
    return _out(request, session, user, workspace)
