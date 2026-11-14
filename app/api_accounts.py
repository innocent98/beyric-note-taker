"""The JSON routes a visitor uses before they have an account, and the account routes after:
sign-up, email verification, password reset, the pilot's state, the waitlist, Google sign-in,
and the notification bell. Included into `app.api.router`, so they share its `/api` prefix, its
error shape and its OpenAPI file.

The public ones are listed one by one in `auth.PUBLIC_API`; they skip the login check but still
need the CSRF header on every non-GET. Their work is in `app/signup.py`, whose first rule is that
no answer may depend on whether an address already has an account.
"""

import logging
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session

from app import (
    auth,
    edge,
    google_oauth,
    google_signin,
    limits,
    notifications,
    ratelimit,
    services,
    signup,
)
from app.api_schemas import (
    Accepted,
    EmailIn,
    ErrorResponse,
    NotificationList,
    NotificationOut,
    PasswordChanged,
    PilotOut,
    ResetIn,
    SignUp,
    UnreadCount,
    Verified,
    VerifyIn,
    WaitlistAccepted,
    WaitlistIn,
)
from app.db import get_session
from app.models import User

logger = logging.getLogger(__name__)

router = APIRouter()

CHECK_YOUR_EMAIL = Accepted(status="check_your_email")


def _now() -> datetime:
    return datetime.now(UTC)


def _address(request: Request) -> str:
    return edge.client_address(request, request.app.state.settings)


_origin_warned = edge.Throttle(86400.0)


def _origin(request: Request) -> str | None:
    """The keyed hash of the client address to remember on a new account, or None where the
    address cannot be told apart from everyone else's: in production with no EDGE_SHARED_SECRET
    configured at all, every visitor carries the proxy's address, and a cap per address would be a
    cap for the whole site (three sign-ups would close the form for two days). The cap is skipped,
    and the log says so, once a day. When a secret IS configured, a request without it did not
    come through nginx (anything on the host can reach the app on localhost): it keeps the cap,
    on the address it really has."""
    settings = request.app.state.settings
    if settings.app_env == "production" and not settings.edge_shared_secret:
        if _origin_warned.ready():
            logger.warning(
                "no per-address sign-up cap: EDGE_SHARED_SECRET is not set, so every client "
                "carries the proxy's address. Set it in .env and in nginx (deploy/README.md, "
                "'Edge secret')."
            )
        return None
    return ratelimit.origin_key(ratelimit.store_secret(settings), _address(request))


def _limits(request: Request) -> limits.PublicLimits:
    return request.app.state.public_limits


@router.get("/pilot", tags=["pilot"])
def read_pilot(request: Request, session: Session = Depends(get_session)) -> PilotOut:
    """Whether sign-up is `open`, `full` or `closed`. Public. No counts of other people.

    `prefill_email` is the Google address of a visitor just turned away by `pilot_full` (a signed
    15-minute cookie), so the waitlist form can start filled in. `google_signin` says whether the
    Google button should be shown."""
    settings = request.app.state.settings
    now = _now()
    prefill = google_signin.read_prefill(
        google_signin.prefill_key(settings), request.cookies.get(google_signin.PREFILL_COOKIE), now
    )
    return PilotOut(
        state=signup.pilot_state(session, now),
        prefill_email=prefill,
        google_signin=google_oauth.enabled(settings),
    )


@router.post(
    "/signup",
    status_code=202,
    tags=["signup"],
    responses={code: {"model": ErrorResponse} for code in (400, 403, 409, 422, 429)},
)
def sign_up(body: SignUp, request: Request, session: Session = Depends(get_session)) -> Accepted:
    """Create an account with a password. Public (needs the X-CSRF-Token).

    Answers 202 `check_your_email` for a new address AND for one that already has an account (a
    verified one gets a "you already have an account" mail), so it cannot be used to find out who
    is registered. The account cannot sign in until the emailed link is confirmed. Errors that do
    not depend on the address: 403 `pilot_closed`, 409 `pilot_full`, 403
    `password_signup_disabled`, 400 `invite_invalid`, 422 `password_too_weak`, `invalid_email`,
    `invalid_name`, `terms_required`, `recording_ack_required`, 429 `too_many_requests` (5
    sign-ups per hour per address) and `server_busy`. `accept_terms` and `accept_recording` must
    be true: the account records the version of each that was accepted.
    A second sign-up for the same address within a minute is answered 202 and does nothing.
    """
    if not _limits(request).signup_address.hit(_address(request)):
        raise signup.Refused(429, "too_many_requests", "Too many attempts. Try again later.")
    signup.sign_up(
        session,
        request.app.state.settings,
        name=body.name,
        email=body.email,
        password=body.password,
        accept_terms=body.accept_terms,
        accept_recording=body.accept_recording,
        invite_token=body.invite_token,
        now=_now(),
        mail_allowed=lambda address: _limits(request).signup_email.hit(limits.email_key(address)),
        origin=_origin(request),
        origin_allowed=_limits(request).signup_origin.hit,
    )
    return CHECK_YOUR_EMAIL


@router.post("/signup/resend", status_code=202, tags=["signup"])
def resend_verification(
    body: EmailIn, request: Request, session: Session = Depends(get_session)
) -> Accepted:
    """Mail a new verification link to an unconfirmed account. Public. Always 202, whatever the
    address and whether or not a mail was sent: one per minute per email and 5 per hour per
    client address are sent, the rest are dropped silently."""
    allowed = _limits(request).resend_address.hit(_address(request)) and (
        _limits(request).resend_email.hit(limits.email_key(body.email))
    )
    if allowed:
        signup.resend_verification(session, request.app.state.settings, body.email, _now())
    return CHECK_YOUR_EMAIL


@router.post(
    "/verify-email",
    tags=["signup"],
    responses={code: {"model": ErrorResponse} for code in (400, 429)},
)
def verify_email(
    body: VerifyIn, request: Request, session: Session = Depends(get_session)
) -> Verified:
    """Confirm an email with the token from the link and the password chosen at sign-up.
    Public. The page the link opens must show a password field and a button that calls this
    (mail scanners open links, and a GET must not verify). Does not sign the user in.
    400 `token_invalid_or_expired`: wrong, used or older than 24 hours. 400 `password_mismatch`:
    not the password submitted with this link's sign-up; the link stays usable until 5 wrong
    passwords, after which it answers `token_invalid_or_expired`."""
    signup.verify_email(
        session,
        body.token,
        body.password,
        _now(),
        _limits(request).verify_wrong,
        _limits(request).signup_origin,
    )
    return Verified(status="verified")


@router.post("/password/forgot", status_code=202, tags=["password"])
def forgot_password(
    body: EmailIn, request: Request, session: Session = Depends(get_session)
) -> Accepted:
    """Mail a password-reset link (valid 30 minutes) if the address has a confirmed account.
    Public. Always 202 with the same body: it never says whether the address has an account."""
    key = limits.email_key(body.email)
    allowed = (
        _limits(request).forgot_address.hit(_address(request))
        and _limits(request).forgot_email.hit(key)
        and _limits(request).forgot_email_hour.hit(key)
    )
    if allowed:
        signup.request_reset(session, request.app.state.settings, body.email, _now())
    return CHECK_YOUR_EMAIL


@router.post(
    "/password/reset",
    tags=["password"],
    responses={code: {"model": ErrorResponse} for code in (400, 422, 429)},
)
def reset_password(
    body: ResetIn, request: Request, session: Session = Depends(get_session)
) -> PasswordChanged:
    """Choose a new password with the token from the reset mail. Public. Signs that user out
    everywhere and does not sign them in. 400 `token_invalid_or_expired`; 422
    `password_too_weak` leaves the token usable."""
    signup.reset_password(
        session, body.token, body.password, _now(), request.app.state.limiter.unlock
    )
    return PasswordChanged(status="password_changed")


# ---- sign in with Google ----

GOOGLE_RESPONSES = {302: {"description": "Redirect"}, 404: {"model": ErrorResponse}}


def _unavailable() -> signup.Refused:
    return signup.Refused(404, "google_signin_unavailable", "Sign in with Google is not available.")


def _login_error(code: str) -> RedirectResponse:
    return RedirectResponse(f"/login?error={code}", status_code=302)


@router.get("/auth/google/start", status_code=302, tags=["google"], responses=GOOGLE_RESPONSES)
def google_start(
    request: Request,
    accept_terms: bool = Query(False),
    accept_recording: bool = Query(False),
    return_to: str | None = Query(None, max_length=500),
    session: Session = Depends(get_session),
    google: google_oauth.GoogleOAuth | None = Depends(google_oauth.get_google),
) -> RedirectResponse:
    """Send the browser to Google's consent screen (scopes `openid email profile` only). Public;
    navigate to it, do not fetch it. `accept_terms=true` must be sent by the sign-up screen: it
    and `accept_recording=true` (the recording box) are what let a NEW account be created (an
    existing one signs in without either). `return_to` is
    a relative path to land on afterwards; anything else becomes `/`. 404
    `google_signin_unavailable` when the server has no Google client configured."""
    if google is None:
        raise _unavailable()
    url, state = google_signin.start_flow(session, google, return_to, _now())
    settings = request.app.state.settings
    response = RedirectResponse(url, status_code=302)
    response.set_cookie(
        google_signin.FLOW_COOKIE,
        google_signin.flow_cookie_value(state, accept_terms, accept_recording),
        max_age=int(google_signin.FLOW_TTL.total_seconds()),
        httponly=True,
        samesite="lax",
        secure=settings.cookie_secure,
        path=google_signin.FLOW_COOKIE_PATH,
    )
    return response


@router.get("/auth/google/callback", status_code=302, tags=["google"], responses=GOOGLE_RESPONSES)
def google_callback(
    request: Request,
    code: str | None = Query(None, max_length=2000),
    state: str | None = Query(None, max_length=2000),
    error: str | None = Query(None, max_length=200),
    session: Session = Depends(get_session),
    google: google_oauth.GoogleOAuth | None = Depends(google_oauth.get_google),
) -> RedirectResponse:
    """Where Google sends the browser back. Public (checked by the single-use `state` and the
    `google_flow` cookie of the browser that started the flow). Always a redirect, never JSON:
    success goes to the stored `return_to` with a new session; a failure goes to
    `/login?error=<code>` with `google_denied`, `google_failed`, `terms_required`,
    `recording_ack_required`, `account_suspended`, `pilot_closed` or `email_unsupported`; a full
    pilot goes to `/pilot-full` and sets a 15-minute cookie that `GET /api/pilot` reads as
    `prefill_email` (the address is never put in a URL)."""
    if google is None:
        raise _unavailable()
    now = _now()
    cookie = request.cookies.get(google_signin.FLOW_COOKIE)
    flow = google_signin.finish_flow(session, state, now)
    response = _login_error("google_failed")
    if flow is not None and state and google_signin.flow_cookie_matches(cookie, state):
        response = _finish_google(request, session, google, flow, code, error, cookie, now)
    response.delete_cookie(google_signin.FLOW_COOKIE, path=google_signin.FLOW_COOKIE_PATH)
    return response


def _finish_google(request, session, google, flow, code, error, cookie, now) -> RedirectResponse:
    if error is not None:
        return _login_error("google_denied")
    if not code:
        return _login_error("google_failed")
    try:
        identity = google.exchange(
            code=code, code_verifier=flow.code_verifier, nonce=flow.nonce, now=now
        )
    except google_oauth.GoogleError:
        return _login_error("google_failed")
    outcome = google_signin.complete(
        session,
        identity,
        accepted_terms=google_signin.cookie_accepted_terms(cookie),
        accepted_recording=google_signin.cookie_accepted_recording(cookie),
        now=now,
    )
    if outcome.user is not None:
        services.start_session(request, session, outcome.user)
        return RedirectResponse(flow.return_to or "/", status_code=302)
    if outcome.full:
        settings = request.app.state.settings
        response = RedirectResponse("/pilot-full", status_code=302)
        response.set_cookie(
            google_signin.PREFILL_COOKIE,
            google_signin.sign_prefill(google_signin.prefill_key(settings), outcome.email, now),
            max_age=google_signin.PREFILL_SECONDS,
            httponly=True,
            samesite="lax",
            secure=settings.cookie_secure,
            path=google_signin.PREFILL_COOKIE_PATH,
        )
        return response
    return _login_error(outcome.error)


# ---- waitlist ----


@router.post(
    "/waitlist",
    status_code=202,
    tags=["waitlist"],
    responses={code: {"model": ErrorResponse} for code in (422,)},
)
def join_waitlist(
    body: WaitlistIn, request: Request, session: Session = Depends(get_session)
) -> WaitlistAccepted:
    """Leave an email (and optionally a name) for when a place opens. Public. Always 202
    `on_the_list`, whether or not the address is already listed or has an account; a repeat
    changes nothing, and 5 per hour per address and 1 per minute per email are dropped silently.
    422 `invalid_email` for a malformed address."""
    address = signup.normalise_email(body.email)
    if address is None:
        raise signup.Refused(422, "invalid_email", "Enter a valid email address.")
    allowed = _limits(request).waitlist_address.hit(_address(request)) and (
        _limits(request).waitlist_email.hit(limits.email_key(address))
    )
    if allowed:
        signup.join_waitlist(session, address, body.name)
    return WaitlistAccepted(status="on_the_list")


# ---- notifications ----

LIST_DEFAULT, LIST_MAX = 50, 100


@router.get("/notifications", tags=["notifications"])
def list_notifications(
    limit: int = Query(LIST_DEFAULT, ge=1, le=LIST_MAX),
    before_id: int | None = Query(None, ge=1, le=services.MAX_ID),
    session: Session = Depends(get_session),
    user: User = Depends(auth.current_user),
) -> NotificationList:
    """The caller's notifications, newest first. For the next page pass `before_id` =
    `next_before_id`."""
    rows = notifications.list_notifications(session, user, limit + 1, before_id)
    page = rows[:limit]
    return NotificationList(
        notifications=[NotificationOut.model_validate(row) for row in page],
        has_more=len(rows) > limit,
        next_before_id=page[-1].id if len(rows) > limit else None,
    )


@router.post(
    "/notifications/read-all",
    tags=["notifications"],
)
def read_all_notifications(
    session: Session = Depends(get_session), user: User = Depends(auth.current_user)
) -> UnreadCount:
    """Mark all of the caller's notifications read."""
    notifications.mark_all_read(session, user, _now())
    return UnreadCount(unread_notifications=notifications.unread_count(session, user))


@router.post(
    "/notifications/{notification_id:id}/read",
    tags=["notifications"],
    responses={404: {"model": ErrorResponse}},
)
def read_notification(
    notification_id: int,
    session: Session = Depends(get_session),
    user: User = Depends(auth.current_user),
) -> NotificationOut:
    """Mark one notification read (repeating it keeps the first read time). 404 for someone
    else's."""
    return NotificationOut.model_validate(
        notifications.mark_read(session, user, notification_id, _now())
    )
