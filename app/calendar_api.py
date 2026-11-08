"""The calendar routes of the JSON API. Included into `app.api.router`, so they share its `/api`
prefix, its error shape and its OpenAPI file.

This file holds the OAuth routes and disconnect (the work is in `app/calendar_connect.py`), the
read the settings page is built on, `GET /api/calendar`, and its writes:
`PUT /api/calendar/settings` (the auto-join rule and the template for calendar meetings) and the
per-meeting switch, `PUT /api/calendar/events/{id}/override`. Both writes re-run the join rules
over the stored events through `gcal_sync` (the one place the rules are applied), so the
`scheduled_joins` rows the runner reads change with them. The rest of C15a (instant-Meet switch,
`upcoming` with the last day, the scheduled-join states) is added here later.
"""

from datetime import UTC, datetime, timedelta
from typing import Literal

from fastapi import APIRouter, Depends, Query, Request, Response
from fastapi.responses import RedirectResponse
from pydantic import BaseModel, ConfigDict, Field, model_validator
from sqlalchemy import select, update
from sqlalchemy.orm import Session
from starlette.exceptions import HTTPException as StarletteHTTPException

from app import (
    auth,
    calendar_connect,
    calendar_providers,
    calendar_text,
    gcal_sync,
    google_oauth,
    send_now,
    services,
    titles,
)
from app.api_schemas import ErrorResponse
from app.db import get_session
from app.gcal_rules import JoinSettings, will_join
from app.models import (
    AppState,
    CalendarConnection,
    CalendarEvent,
    ScheduledJoin,
    User,
    UserSettings,
)
from app.services import get_vexa

router = APIRouter(prefix="/calendar", tags=["calendar"])
# The two routes of the scheduled joins that do not live under `/calendar` (C15b): the list of
# meetings the note taker did not get into, and the button that sends it again.
joins_router = APIRouter(tags=["calendar"])

ProviderName = Literal["google", "microsoft"]  # calendar_providers.ROUTE_PROVIDERS

CONNECT_RESPONSES = {code: {"model": ErrorResponse} for code in (404, 409, 503)}
CALLBACK_RESPONSES = {302: {"description": "Redirect"}, 404: {"model": ErrorResponse}}
STATE_RESPONSES = {404: {"model": ErrorResponse}}
SETTINGS_RESPONSES = {code: {"model": ErrorResponse} for code in (404, 422)}
OVERRIDE_RESPONSES = {code: {"model": ErrorResponse} for code in (404, 409, 422)}
SEND_NOW_RESPONSES = {code: {"model": ErrorResponse} for code in (403, 404, 409, 502)}

UPCOMING_DAYS = 7
UPCOMING_LIMIT = 100


class ConnectIn(BaseModel):
    """`features` are what to ask Google for: `calendar` (read events, to decide which meetings
    to join) and/or `meet` (learn that a Meet call you host began). `return_to` is a relative
    path to land on afterwards; anything else becomes `/settings`."""

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
    `will_join_text` is the short answer in words and `reason_text` the sentence behind a no: show
    both as they are."""

    id: int
    title: str
    starts_at: datetime
    ends_at: datetime
    # `google_meet`, `zoom`, `teams`, or null when the event has no video link.
    platform: Literal["google_meet", "zoom", "teams"] | None
    platform_label: str | None = Field(description="Google Meet, Zoom or Microsoft Teams; or null")
    # How many people are invited: a number, never their addresses.
    attendee_count: int
    will_join: bool
    # `Will join`, `Off` (the rule or the person's switch says no, and they could change it) or
    # `Cannot join` (no link, over, declined, ...). `reason_text` says why, or is null.
    will_join_text: str
    reason_text: str | None
    # The scheduled join behind this event, or null when it has none (the event is not joined).
    # `join_id` is the id Send now takes. `not_recorded_text` is set when the note taker did not
    # get into this meeting (the same sentence as the Not recorded list); `can_send_now` is true
    # when Send now would be accepted: show the button only then.
    join_id: int | None = None
    can_send_now: bool = False
    not_recorded_text: str | None = None


class CalendarOut(BaseModel):
    """The calendar settings page's whole state. `state` is for choosing what to draw
    (`not_connected` shows the Connect button; `connected` and `degraded` show the connected card;
    `needs_reconnect` shows it with a Connect again button); `state_text` is the sentence for it.
    `connected` is true while the calendar can still be read (`connected` or `degraded`).
    `account_email` is the account that was connected (shown as the connected address), and
    `provider` which calendar it is (`google` or `microsoft`); both are null when none is.
    `last_sync_at` is the last time the calendar was read in full, null before the first sync.
    `last_error` is the last failure in plain words, null when the last attempt worked.
    `rule` is the person's auto-join rule (`all`, `organizer` or `none`; `all` until they choose)
    and `rule_text` its label ("Joining all meetings"). `calendar_template_id` is the person's own
    template chosen for calendar meetings, null for the default (their first, oldest, template).
    `auto_join_paused` is true while the operator has stopped calendar auto-join for everyone
    (`AUTOJOIN_ENABLED=false`): the rows still show what the rule says, but no bot is sent, and
    `auto_join_paused_text` is the line to show (null when it is not paused).
    `upcoming` is empty unless connected."""

    connected: bool
    state: Literal["not_connected", "connected", "degraded", "needs_reconnect"]
    state_text: str
    account_email: str | None
    provider: Literal["google", "microsoft"] | None
    last_sync_at: datetime | None
    last_error: str | None
    rule: Literal["all", "organizer", "none"]
    rule_text: str
    calendar_template_id: int | None
    auto_join_paused: bool
    auto_join_paused_text: str | None
    upcoming: list[UpcomingEvent]


class SettingsIn(BaseModel):
    """What to change; a field that is left out stays as it is. `rule` is `all` (every event with
    a video link), `organizer` (only meetings the person organises) or `none` (only the meetings
    they switch on). Changing it also clears their per-meeting switches on events that have not
    started, and re-decides every event from the new rule. `calendar_template_id` is one of the
    person's own templates, or null for the default; it also reaches the meetings already planned
    that have not sent a bot. At least one field is needed."""

    model_config = ConfigDict(extra="forbid", strict=True)

    rule: Literal["all", "organizer", "none"] | None = None
    calendar_template_id: int | None = None

    @model_validator(mode="after")
    def _something_to_change(self):
        if not self.model_fields_set or ("rule" in self.model_fields_set and self.rule is None):
            raise ValueError("send a rule, a calendar_template_id, or both")
        return self


class OverrideIn(BaseModel):
    """The person's switch for one meeting: true joins it, false does not, null clears the switch
    so the rule decides again. The field is required (null must be sent on purpose)."""

    model_config = ConfigDict(extra="forbid", strict=True)

    override: bool | None


def _now() -> datetime:
    return datetime.now(UTC)


def _enabled(request: Request, google: google_oauth.GoogleOAuth | None) -> google_oauth.GoogleOAuth:
    """The Google client, or 404 `calendar_unavailable` while the calendar features are off (the
    `calendar_enabled` flag) or the server has no Google client configured."""
    if google is None or not request.app.state.settings.calendar_enabled:
        raise services.Refused(404, "calendar_unavailable", "Google Calendar is not available.")
    return google


def _provider_client(
    request: Request, provider: str, google: google_oauth.GoogleOAuth | None
) -> google_oauth.GoogleOAuth:
    """The OAuth client of the provider a route names, or 404 `calendar_unavailable`: a provider
    with no row in the registry (Microsoft until it is built) is simply not available."""
    if calendar_providers.get(provider) is None:
        raise services.Refused(404, "calendar_unavailable", "This calendar is not available.")
    return _enabled(request, google)


@router.post("/{provider}/connect", responses=CONNECT_RESPONSES)
def calendar_connect_start(
    provider: ProviderName,
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
    409 `calendar_already_connected` when another provider's calendar is connected (one calendar at
    a time), 503 `calendar_unavailable` when the server cannot store a token safely. `provider` is
    `google`; `microsoft` answers 404 `calendar_unavailable` until it is built."""
    google = _provider_client(request, provider, google)
    settings = request.app.state.settings
    try:
        url, state = calendar_connect.start_connect(
            session, user, body.features, body.return_to, google=google, now=_now()
        )
    except calendar_connect.AlreadyConnected:
        raise services.Refused(
            409,
            "calendar_already_connected",
            "Another calendar is already connected. Disconnect it first.",
        ) from None
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


@router.get("/{provider}/callback", status_code=302, responses=CALLBACK_RESPONSES)
def calendar_connect_callback(
    provider: ProviderName,
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
    unticked), `no_refresh_token`, `wrong_account` or `already_connected` (another provider's
    calendar is connected); nobody signed in goes to
    `/login?error=google_failed`. 404 `calendar_unavailable` while the feature is off."""
    google = _provider_client(request, provider, google)
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


@router.post("/{provider}/disconnect")
def calendar_connect_disconnect(
    provider: ProviderName,
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
    if calendar_providers.get(provider) is None:
        raise services.Refused(404, "calendar_unavailable", "This calendar is not available.")
    revoked = calendar_connect.disconnect(session, user, google, provider)
    return DisconnectOut(revoked=revoked)


def _join_settings(prefs: UserSettings | None, request: Request) -> JoinSettings:
    return JoinSettings(
        rule=prefs.auto_join_rule if prefs else "all",
        teams_join_enabled=request.app.state.settings.teams_join_enabled,
    )


def _upcoming_item(
    row: CalendarEvent,
    join_settings: JoinSettings,
    now: datetime,
    join: ScheduledJoin | None = None,
) -> UpcomingEvent:
    decision = will_join(row, row.override, join_settings, now=now)
    not_recorded = join is not None and calendar_text.is_not_recorded(join, now)
    return UpcomingEvent(
        id=row.id,
        title=titles.clean_title(row.title),
        starts_at=row.starts_at,
        ends_at=row.ends_at,
        platform=row.platform,
        platform_label=calendar_text.platform_label(row.platform),
        attendee_count=row.attendee_count,
        will_join=decision.join,
        will_join_text=calendar_text.join_label(decision),
        reason_text=calendar_text.join_reason(decision),
        join_id=join.id if join is not None else None,
        can_send_now=join is not None and calendar_text.can_send_now(join, now),
        not_recorded_text=(
            calendar_text.not_recorded_text(join.state, join.skip_reason) if not_recorded else None
        ),
    )


def _joins_of(session: Session, user_id: int, event_ids: list[int]) -> dict[int, ScheduledJoin]:
    """The caller's scheduled join for each of these events (one per event at most)."""
    if not event_ids:
        return {}
    rows = session.scalars(
        select(ScheduledJoin).where(
            ScheduledJoin.user_id == user_id, ScheduledJoin.calendar_event_id.in_(event_ids)
        )
    )
    return {row.calendar_event_id: row for row in rows}


def _upcoming(
    session: Session, conn: CalendarConnection, join_settings: JoinSettings, now: datetime
) -> list[UpcomingEvent]:
    """Events not over yet that start within seven days, soonest first, each with the join rules'
    answer for this user. Cancelled events are left out."""
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
    joins = _joins_of(session, conn.user_id, [row.id for row in rows])
    return [_upcoming_item(row, join_settings, now, joins.get(row.id)) for row in rows]


def _calendar_out(request: Request, session: Session, user: User) -> CalendarOut:
    """The whole state, as `GET /api/calendar` and `PUT /api/calendar/settings` answer."""
    conn = calendar_connect.live_calendar_connection(session, user)
    prefs = session.get(UserSettings, user.id)
    rule = prefs.auto_join_rule if prefs else "all"
    template_id = prefs.calendar_template_id if prefs else None
    # Paused when the scheduler (the process that sends the bots) has recorded that auto-join is
    # off. Read from the database only: `web` keeps the environment it was started with, and a
    # restart of `scheduler` alone, as the runbook says, must be enough in both directions.
    paused = session.scalar(select(AppState.autojoin_paused_since)) is not None
    paused_text = calendar_text.AUTO_JOIN_PAUSED_TEXT if paused else None
    if conn is None:
        return CalendarOut(
            connected=False,
            state="not_connected",
            state_text=calendar_text.state_text(None, "not_connected"),
            account_email=None,
            provider=None,
            last_sync_at=None,
            last_error=None,
            rule=rule,
            rule_text=calendar_text.rule_text(rule),
            calendar_template_id=template_id,
            auto_join_paused=paused,
            auto_join_paused_text=paused_text,
            upcoming=[],
        )
    state = conn.status if conn.status in ("connected", "degraded") else "needs_reconnect"
    return CalendarOut(
        connected=state != "needs_reconnect",
        state=state,
        state_text=calendar_text.state_text(conn, state),
        account_email=conn.account_email,
        provider=conn.provider,
        last_sync_at=conn.last_sync_at,
        last_error=calendar_text.error_text(conn.last_error),
        rule=rule,
        rule_text=calendar_text.rule_text(rule),
        calendar_template_id=template_id,
        auto_join_paused=paused,
        auto_join_paused_text=paused_text,
        upcoming=_upcoming(session, conn, _join_settings(prefs, request), _now()),
    )


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
    return _calendar_out(request, session, user)


@router.put("/settings", responses=SETTINGS_RESPONSES)
def calendar_settings(
    body: SettingsIn,
    request: Request,
    session: Session = Depends(get_session),
    user: User = Depends(auth.current_user),
    google: google_oauth.GoogleOAuth | None = Depends(google_oauth.get_google),
) -> CalendarOut:
    """Change the auto-join rule and/or the template for calendar meetings (CSRF header needed);
    a field left out stays as it is. Answers with the whole state, as `GET /api/calendar` does, so
    the page can redraw from it. A changed `rule` clears the person's per-meeting switches on events
    that have not started, then decides every stored event again from the new rule: joins it now
    forbids are cancelled and joins it now allows are made or revived (the same rules the sync
    applies; nothing is fetched from Google and no bot is sent here). Choosing the rule that is
    already set changes nothing. 404 `not_found` for a template that is not the caller's own,
    404 `calendar_unavailable` while the calendar features are off."""
    _enabled(request, google)
    set_template = "calendar_template_id" in body.model_fields_set
    if set_template and body.calendar_template_id is not None:
        services.template_or_404(session, user, body.calendar_template_id)
    prefs = session.get(UserSettings, user.id)
    if prefs is None:
        prefs = UserSettings(user_id=user.id, auto_join_rule="all")
        session.add(prefs)
    now = _now()
    rule_changed = body.rule is not None and body.rule != prefs.auto_join_rule
    if body.rule is not None:
        prefs.auto_join_rule = body.rule
    if set_template:
        prefs.calendar_template_id = body.calendar_template_id
        gcal_sync.set_join_template(session, user.id, body.calendar_template_id, commit=False)
    conn = calendar_connect.live_calendar_connection(session, user)
    if rule_changed:
        session.execute(
            update(CalendarEvent)
            .where(CalendarEvent.user_id == user.id, CalendarEvent.starts_at > now)
            .values(override=None)
        )
    if rule_changed and conn is not None:
        events = session.scalars(
            select(CalendarEvent).where(
                CalendarEvent.connection_id == conn.id,
                CalendarEvent.user_id == user.id,
                CalendarEvent.status != "cancelled",
                CalendarEvent.ends_at > now,
            )
        ).all()
        gcal_sync.rerun_rules(
            session, conn, prefs, list(events), now=now, settings=request.app.state.settings
        )
    else:
        session.commit()
    return _calendar_out(request, session, user)


@router.put("/events/{event_id:id}/override", responses=OVERRIDE_RESPONSES)
def calendar_event_override(
    event_id: int,
    body: OverrideIn,
    request: Request,
    session: Session = Depends(get_session),
    user: User = Depends(auth.current_user),
    google: google_oauth.GoogleOAuth | None = Depends(google_oauth.get_google),
) -> UpcomingEvent:
    """The person's switch for one meeting on the Upcoming list (CSRF header needed): `true` joins
    it, `false` does not, `null` clears the switch so the rule decides again. The switch beats the
    rule in both directions; the stored join is made, revived or cancelled to match, which is what
    the runner reads. Answers with the event's new row. 404 `not_found` for an event that is not
    the caller's (also after disconnecting); 409 `not_joinable` for an event nothing the person
    switches can change (declined, over, cancelled, all day, no meeting link, or Microsoft Teams
    before Teams can be joined): `message` is the reason in plain words, and nothing is stored.
    404 `calendar_unavailable` while the calendar features are off."""
    _enabled(request, google)
    conn = calendar_connect.live_calendar_connection(session, user)
    row = None
    if conn is not None:
        row = session.scalars(
            select(CalendarEvent)
            .where(
                CalendarEvent.id == event_id,
                CalendarEvent.user_id == user.id,
                CalendarEvent.connection_id == conn.id,
            )
            .with_for_update()
        ).first()
    if row is None:
        raise StarletteHTTPException(404, "No such event")
    prefs = session.get(UserSettings, user.id)
    join_settings = _join_settings(prefs, request)
    now = _now()
    # With the switch on, only what the event itself is can still say no: that is "not joinable".
    blocked = will_join(row, True, join_settings, now=now)
    if not blocked.join:
        raise services.Refused(409, "not_joinable", calendar_text.join_reason(blocked))
    row.override = body.override
    gcal_sync.rerun_rules(
        session,
        conn,
        prefs or gcal_sync.DEFAULT_USER_SETTINGS,
        [row],
        now=now,
        settings=request.app.state.settings,
    )
    join = _joins_of(session, user.id, [row.id]).get(row.id)
    return _upcoming_item(row, join_settings, now, join)


# ---- not recorded, and Send now (plan C15b) ----

NOT_RECORDED_DAYS = 7
NOT_RECORDED_LIMIT = 100


class NotRecordedItem(BaseModel):
    """One meeting the note taker did not get into. `reason_text` says why in words (show it as it
    is). `state` is for choosing the look: `no_capacity` (the note taker was busy), `failed` or
    `skipped`. `can_send_now` is true while the meeting is still on and the account may send; show
    the Send now button only then. `join_id` is what `POST /api/scheduled-joins/{id}/send-now` takes
    (it is not an event id)."""

    join_id: int
    title: str
    starts_at: datetime
    ends_at: datetime
    platform: Literal["google_meet", "zoom", "teams"]
    platform_label: str
    state: Literal["failed", "no_capacity", "skipped"]
    reason_text: str
    can_send_now: bool


class NotRecordedOut(BaseModel):
    """The last seven days, newest first, at most 100. A meeting the note taker did join and that
    then failed (not let in, recording failed) is not here: it is an ordinary failed meeting on
    the Meetings list."""

    items: list[NotRecordedItem]


class SendNowOut(BaseModel):
    """The note taker is on its way. `meeting_id` is the new meeting (the Meetings list and its
    page follow it as it is joined and written up). The same answer comes back if the button is
    pressed twice."""

    join_id: int
    meeting_id: int
    status_text: str


def _not_recorded_item(join: ScheduledJoin, title: str | None, now: datetime) -> NotRecordedItem:
    return NotRecordedItem(
        join_id=join.id,
        title=titles.clean_title(title) or titles.alert_name(None, join.platform, join.native_id),
        starts_at=join.starts_at,
        ends_at=join.ends_at,
        platform=join.platform,
        platform_label=calendar_text.PLATFORM_LABELS[join.platform],
        state=join.state,
        reason_text=calendar_text.not_recorded_text(join.state, join.skip_reason),
        can_send_now=calendar_text.can_send_now(join, now),
    )


@joins_router.get("/not-recorded")
def not_recorded(
    session: Session = Depends(get_session),
    user: User = Depends(auth.current_user),
) -> NotRecordedOut:
    """The meetings of the last seven days the note taker did not get into, for the Meetings page
    (no CSRF header needed): the calendar's `failed` and `no_capacity` joins, and the skipped ones
    whose reason means the meeting was not recorded (recording hours used up, too many note takers
    at once, the link still in use, a note taker from another account already there, the calendar
    disconnected, a suspended account). Only the caller's own. Each item says why in plain words
    and whether Send now is on offer. Newest first."""
    now = _now()
    rows = session.execute(
        select(ScheduledJoin, CalendarEvent.title)
        .outerjoin(CalendarEvent, CalendarEvent.id == ScheduledJoin.calendar_event_id)
        .where(
            ScheduledJoin.user_id == user.id,
            ScheduledJoin.run_at <= now,
            ScheduledJoin.starts_at > now - timedelta(days=NOT_RECORDED_DAYS),
            (ScheduledJoin.state.in_(("failed", "no_capacity")))
            | (
                (ScheduledJoin.state == "skipped")
                & ScheduledJoin.skip_reason.in_(calendar_text.NOT_RECORDED_SKIP_REASONS)
            ),
        )
        .order_by(ScheduledJoin.starts_at.desc(), ScheduledJoin.id.desc())
        .limit(NOT_RECORDED_LIMIT)
    ).all()
    return NotRecordedOut(items=[_not_recorded_item(join, title, now) for join, title in rows])


@joins_router.post("/scheduled-joins/{join_id:id}/send-now", responses=SEND_NOW_RESPONSES)
def send_now_route(
    join_id: int,
    request: Request,
    session: Session = Depends(get_session),
    user: User = Depends(auth.current_user),
    vexa=Depends(get_vexa),
) -> SendNowOut:
    """Send the note taker to a meeting it did not get into (CSRF header needed, no body). `join_id`
    comes from `GET /api/not-recorded` or an Upcoming row. Allowed while the meeting is still on,
    for a join that is `failed`, `no_capacity` or `skipped` (not for a suspended account). The note
    taker is sent through the same placement as every other bot, so the whole pre-flight runs again
    and nothing is bypassed: refusals come back with the same codes as pasting a link
    (403 `hours_cap_reached`, 409 `user_limit`, `already_in_meeting`, `meeting_has_note_taker`,
    `bot_busy`) and change nothing. 409 `meeting_ended` once the meeting is over, 409
    `cannot_send_now` for a join that is not in a sendable state (still scheduled, switched off,
    done), 403 `account_suspended`, 409 `template_required` when no calendar template is chosen,
    502 `bot_send_failed` when the bot service did not take it, 404 `not_found` for a join that is
    not the caller's. Pressing it twice answers 200 twice with the same `meeting_id` and sends one
    bot. The join's retry window restarts: 15 minutes from now."""
    sent = send_now.send_now(
        session,
        vexa,
        user,
        join_id,
        now=_now(),
        settings=request.app.state.settings,
    )
    return SendNowOut(
        join_id=sent.join_id, meeting_id=sent.meeting_id, status_text=calendar_text.SENT_TEXT
    )
