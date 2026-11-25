"""The meeting picker of cross-meeting recipes (plan T15). Included into `app.api.router`, so it
shares its `/api` prefix, login check, CSRF rule and error shape.

`POST /api/recipes/picker/resolve` is a read made as a POST because its body is a choice (a
series, a project, a sprint, a date range or hand-picked ids): it answers with the meetings that
choice means for the caller, at most 12, newest first, and how many were left out because the
caller cannot read them. Nothing about a left-out meeting is returned, not even its id. The team is
the `X-Team-Id` header, as on `GET /api/meetings`.
"""

from datetime import date

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from app import auth, services, team_admin, timezones, titles
from app.api_schemas import ErrorResponse
from app.db import get_session
from app.models import User, UserSettings
from app.recipes import picker
from app.recipes.picker_spec import MAX_MEETINGS, PickerSpec, left_out_sentence

router = APIRouter()


class PickedMeeting(BaseModel):
    id: int
    # The meeting's name, as the Meetings list shows it.
    title: str
    # The day it was made (`YYYY-MM-DD`), in the caller's time zone: the day the list heads it with.
    day: date


class PickerPreview(BaseModel):
    # At most `max_meetings`, newest first: exactly what a draft would be made from.
    meetings: list[PickedMeeting]
    # Chosen meetings the caller cannot read (or that do not exist): left out, never named.
    left_out_count: int
    # A plain sentence for `left_out_count`; null when none were left out.
    left_out_message: str | None
    # True when more than `max_meetings` matched and the oldest were cut.
    capped: bool
    max_meetings: int


@router.post(
    "/recipes/picker/resolve",
    tags=["documents"],
    responses={
        404: {
            "model": ErrorResponse,
            "description": "No such team, recurring meeting, project or sprint for the caller",
        },
        422: {
            "model": ErrorResponse,
            "description": "`validation_error` (name exactly one way of choosing), "
            "`invalid_date_range`, `invalid_time_zone`",
        },
    },
)
def resolve_meetings(
    body: PickerSpec,
    team_id: int | None = Depends(team_admin.requested_team),
    session: Session = Depends(get_session),
    user: User = Depends(auth.current_user),
) -> PickerPreview:
    """Preview the meetings a cross-meeting recipe would read for this choice: those the caller may
    read, ready, newest first, at most 12. Send the returned ids as `meeting_ids` of
    `POST /api/documents`, together with this body as `picker`, so the draft keeps the
    `left_out_count`."""
    found = picker.resolve_meetings(session, user, body, team_id=team_id)
    stored = session.scalar(select(UserSettings.time_zone).where(UserSettings.user_id == user.id))
    zone = services.pick_zone(stored, body.tz)
    return PickerPreview(
        meetings=[
            PickedMeeting(
                id=m.id,
                title=titles.meeting_name(m),
                day=timezones.localise(m.created_at, zone)[0].date(),
            )
            for m in found.meetings
        ],
        left_out_count=found.left_out_count,
        left_out_message=left_out_sentence(found.left_out_count),
        capped=found.capped,
        max_meetings=MAX_MEETINGS,
    )
