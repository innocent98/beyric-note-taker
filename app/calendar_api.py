"""The calendar routes of the JSON API. Included into `app.api.router`, so they share its `/api`
prefix, its error shape and its OpenAPI file.

This file holds the two OAuth routes (the work is in `app/calendar_connect.py`). The calendar
state, settings and upcoming meetings are added here by later tasks.
"""

from datetime import UTC, datetime
from typing import Literal

from fastapi import APIRouter, Depends, Query, Request, Response
from fastapi.responses import RedirectResponse
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session

from app import auth, calendar_connect, google_oauth, services
from app.api_schemas import ErrorResponse
from app.db import get_session
from app.models import User

router = APIRouter(prefix="/calendar", tags=["calendar"])

CONNECT_RESPONSES = {code: {"model": ErrorResponse} for code in (404, 503)}
CALLBACK_RESPONSES = {302: {"description": "Redirect"}, 404: {"model": ErrorResponse}}


class ConnectIn(BaseModel):
    """`features` are what to ask Google for: `calendar` (read events, to decide which meetings
    to join) and/or `meet` (learn that a Meet call you host began). `return_to` is a relative
    path to land on afterwards; anything else becomes `/settings/calendar`."""

    model_config = ConfigDict(extra="forbid", strict=True)

    features: list[Literal["calendar", "meet"]] = Field(
        default=["calendar"], min_length=1, max_length=2
    )
    return_to: str | None = Field(default=None, max_length=500)


class ConnectOut(BaseModel):
    """`authorize_url` is where to send the browser (navigate to it; do not fetch it).
    `show_unverified_notice` is true until Google has verified the app: show the "Google hasn't
    verified this app" explainer first."""

    authorize_url: str
    show_unverified_notice: bool


def _now() -> datetime:
    return datetime.now(UTC)


def _enabled(request: Request, google: google_oauth.GoogleOAuth | None) -> google_oauth.GoogleOAuth:
    """The Google client, or 404 `calendar_unavailable` while the calendar features are off (the
    `calendar_enabled` flag) or the server has no Google client configured."""
    if google is None or not request.app.state.settings.calendar_enabled:
        raise services.Refused(404, "calendar_unavailable", "Google Calendar is not available.")
    return google


@router.post("/google/connect", responses=CONNECT_RESPONSES)
def calendar_google_connect(
    body: ConnectIn,
    request: Request,
    response: Response,
    session: Session = Depends(get_session),
    user: User = Depends(auth.current_user),
    google: google_oauth.GoogleOAuth | None = Depends(google_oauth.get_google),
) -> ConnectOut:
    """Start connecting Google Calendar (CSRF header needed). Returns the Google consent URL to
    navigate to. Only the permissions in `features` are asked for, each at the moment its feature
    is switched on; permissions given earlier are kept. Sets the `calendar_flow` cookie that ties
    the callback to this browser. 404 `calendar_unavailable` when the calendar features are off,
    503 `calendar_unavailable` when the server cannot store a token safely."""
    google = _enabled(request, google)
    settings = request.app.state.settings
    try:
        url, state = calendar_connect.start_connect(
            session, user, body.features, body.return_to, google=google, now=_now()
        )
    except calendar_connect.ConnectUnavailable:
        raise services.Refused(
            503, "calendar_unavailable", "Google Calendar cannot be connected right now."
        ) from None
    response.set_cookie(
        calendar_connect.FLOW_COOKIE,
        state,
        max_age=int(calendar_connect.FLOW_TTL.total_seconds()),
        httponly=True,
        samesite="lax",
        secure=settings.cookie_secure,
        path=calendar_connect.FLOW_COOKIE_PATH,
    )
    return ConnectOut(authorize_url=url, show_unverified_notice=not settings.google_app_verified)


@router.get("/google/callback", status_code=302, responses=CALLBACK_RESPONSES)
def calendar_google_callback(
    request: Request,
    code: str | None = Query(None, max_length=2000),
    state: str | None = Query(None, max_length=2000),
    error: str | None = Query(None, max_length=200),
    session: Session = Depends(get_session),
    google: google_oauth.GoogleOAuth | None = Depends(google_oauth.get_google),
) -> RedirectResponse:
    """Where Google sends the browser back after the consent screen. Public in the guard (it
    answers a signed-out visitor itself) but checked by the single-use `state`, the signed-in
    user the flow was started for, and the `calendar_flow` cookie. Always a redirect, never JSON:
    success goes to the stored `return_to` with `?calendar=connected`; a failure goes there with
    `?calendar_error=` `google_denied`, `google_failed`, `scope_missing` (the calendar box was
    unticked), `no_refresh_token` or `wrong_account`; nobody signed in goes to
    `/login?error=google_failed`. 404 `calendar_unavailable` while the feature is off."""
    google = _enabled(request, google)
    settings = request.app.state.settings
    result = calendar_connect.finish_connect(
        session,
        request.state.user,
        state=state,
        code=code,
        google=google,
        settings=settings,
        now=_now(),
        flow_cookie=request.cookies.get(calendar_connect.FLOW_COOKIE),
        error=error,
    )
    response = RedirectResponse(result.redirect_to, status_code=302)
    response.delete_cookie(calendar_connect.FLOW_COOKIE, path=calendar_connect.FLOW_COOKIE_PATH)
    return response
