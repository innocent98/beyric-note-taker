"""The calendar routes of the JSON API. Included into `app.api.router`, so they share its `/api`
prefix, its error shape and its OpenAPI file.

This file holds the OAuth routes and disconnect (the work is in `app/calendar_connect.py`) and the
read the settings page is built on, `GET /api/calendar`. The settings, per-meeting override and the
rest of C15a are added here by later tasks; they extend `CalendarOut`.
"""

from datetime import UTC, datetime, timedelta
from typing import Literal

from fastapi import APIRouter, Depends, Query, Request, Response
from fastapi.responses import RedirectResponse
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app import auth, calendar_connect, calendar_text, google_oauth, services
from app.api_schemas import ErrorResponse
from app.db import get_session
from app.gcal_rules import JoinSettings, will_join
from app.models import CalendarConnection, CalendarEvent, User, UserSettings

router = APIRouter(prefix="/calendar", tags=["calendar"])

CONNECT_RESPONSES = {code: {"model": ErrorResponse} for code in (404, 503)}
CALLBACK_RESPONSES = {302: {"description": "Redirect"}, 404: {"model": ErrorResponse}}
STATE_RESPONSES = {404: {"model": ErrorResponse}}

UPCOMING_DAYS = 7
UPCOMING_LIMIT = 100


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


class DisconnectOut(BaseModel):
    """`status` is always `disconnected`: the call is safe to repeat, and also answers 200 when
    nothing was connected. `revoked` is false when Google could not be asked to remove the access
    (it was unreachable, or the stored key could not be read): the data is deleted here
    regardless, but tell the person to also remove Beyric at myaccount.google.com/permissions.
    It is true when Google confirmed, and also when there was nothing left to revoke (a repeat
    cannot re-check an earlier failure: act on the first answer)."""

    status: Literal["disconnected"] = "disconnected"
    revoked: bool


class UpcomingEvent(BaseModel):
    """One synced event in the next seven days. Only what a person needs to recognise it: no
    attendees, description, location or meeting link (a Zoom link can carry a passcode).
    `will_join_text` is the answer in words ("Will join", "No video link", ...): show it as is."""

    id: int
    title: str
    starts_at: datetime
    ends_at: datetime
    # `google_meet`, `zoom`, `teams`, or null when the event has no video link.
    platform: Literal["google_meet", "zoom", "teams"] | None
    platform_label: str | None = Field(description="Google Meet, Zoom or Microsoft Teams; or null")
    will_join: bool
    will_join_text: str


class CalendarOut(BaseModel):
    """The calendar settings page's whole state. `state` is for choosing what to draw
    (`not_connected` shows the Connect button; `connected` and `degraded` show the connected card;
    `needs_reconnect` shows it with a Connect again button); `state_text` is the sentence for it.
    `connected` is true while the calendar can still be read (`connected` or `degraded`).
    `google_email` is the Google account that was connected, and is null when none is.
    `last_sync_at` is the last time the calendar was read in full, null before the first sync.
    `last_error` is the last failure in plain words, null when the last attempt worked."""

    connected: bool
    state: Literal["not_connected", "connected", "degraded", "needs_reconnect"]
    state_text: str
    google_email: str | None
    last_sync_at: datetime | None
    last_error: str | None
    upcoming: list[UpcomingEvent]


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


@router.post("/google/disconnect")
def calendar_google_disconnect(
    session: Session = Depends(get_session),
    user: User = Depends(auth.current_user),
    google: google_oauth.GoogleOAuth | None = Depends(google_oauth.get_google),
) -> DisconnectOut:
    """Disconnect the caller's Google account (CSRF header needed, no body). Revokes the access at
    Google, then deletes the caller's calendar events, all their scheduled joins (every state) and
    calendar notifications, and the stored token. Meetings already recorded, the hours used and
    the settings stay; a meeting being recorded right now carries on. Safe to repeat (200 again),
    and it works while the calendar features are switched off. If Google cannot be reached the
    local deletion still happens and `revoked` is false: tell the person to remove Beyric at
    myaccount.google.com/permissions.
    Afterwards `connect` works again, for the same or another Google account."""
    revoked = calendar_connect.disconnect(session, user, google)
    return DisconnectOut(revoked=revoked)


def _calendar_connection(session: Session, user: User) -> CalendarConnection | None:
    """The user's Google connection if it gives the calendar feature (a connection made only for
    Meet is not a connected calendar), else None."""
    conn = session.scalars(
        select(CalendarConnection).where(
            CalendarConnection.user_id == user.id, CalendarConnection.provider == "google"
        )
    ).first()
    if conn is None or conn.status == "disconnected":
        return None
    if "calendar" not in calendar_connect.granted_features(conn):
        return None
    return conn


def _upcoming(
    session: Session, conn: CalendarConnection, now: datetime, *, teams_join_enabled: bool
) -> list[UpcomingEvent]:
    """Events not over yet that start within seven days, soonest first, each with the join rules'
    answer for this user. Cancelled events are left out."""
    prefs = session.get(UserSettings, conn.user_id)
    join_settings = JoinSettings(
        rule=prefs.auto_join_rule if prefs else "all",
        teams_join_enabled=teams_join_enabled,
    )
    rows = session.scalars(
        select(CalendarEvent)
        .where(
            CalendarEvent.connection_id == conn.id,
            CalendarEvent.user_id == conn.user_id,
            CalendarEvent.status != "cancelled",
            CalendarEvent.ends_at > now,
            CalendarEvent.starts_at < now + timedelta(days=UPCOMING_DAYS),
        )
        .order_by(CalendarEvent.starts_at, CalendarEvent.id)
        .limit(UPCOMING_LIMIT)
    ).all()
    out = []
    for row in rows:
        decision = will_join(row, row.override, join_settings, now=now)
        out.append(
            UpcomingEvent(
                id=row.id,
                title=row.title,
                starts_at=row.starts_at,
                ends_at=row.ends_at,
                platform=row.platform,
                platform_label=calendar_text.platform_label(row.platform),
                will_join=decision.join,
                will_join_text=calendar_text.join_text(decision),
            )
        )
    return out


@router.get("", responses=STATE_RESPONSES)
def calendar_state(
    request: Request,
    session: Session = Depends(get_session),
    user: User = Depends(auth.current_user),
    google: google_oauth.GoogleOAuth | None = Depends(google_oauth.get_google),
) -> CalendarOut:
    """Everything the calendar settings page shows, in one read (no CSRF header needed): whether
    Google Calendar is connected, which Google address, the last time it was read, the last
    failure in plain words, and the next seven days of synced events, each with whether the bot
    will join it and why not when it will not. Nothing is fetched from Google here: this reads what
    the sync job last stored. 404 `calendar_unavailable` while the calendar features are off."""
    _enabled(request, google)
    conn = _calendar_connection(session, user)
    if conn is None:
        return CalendarOut(
            connected=False,
            state="not_connected",
            state_text=calendar_text.state_text(None, "not_connected"),
            google_email=None,
            last_sync_at=None,
            last_error=None,
            upcoming=[],
        )
    state = conn.status if conn.status in ("connected", "degraded") else "needs_reconnect"
    return CalendarOut(
        connected=state != "needs_reconnect",
        state=state,
        state_text=calendar_text.state_text(conn, state),
        google_email=conn.google_account_email,
        last_sync_at=conn.last_sync_at,
        last_error=calendar_text.error_text(conn.last_error),
        upcoming=_upcoming(
            session,
            conn,
            _now(),
            teams_join_enabled=request.app.state.settings.teams_join_enabled,
        ),
    )
