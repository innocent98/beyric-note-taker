"""The JSON routes a visitor uses before they have an account, and the account routes after:
sign-up, email verification, password reset, the pilot's state, the waitlist, Google sign-in,
and the notification bell. Included into `app.api.router`, so they share its `/api` prefix, its
error shape and its OpenAPI file.

The public ones are listed one by one in `auth.PUBLIC_API`; they skip the login check but still
need the CSRF header on every non-GET. Their work is in `app/signup.py`, whose first rule is that
no answer may depend on whether an address already has an account.
"""

from datetime import UTC, datetime

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session

from app import edge, google_oauth, google_signin, limits, services, signup
from app.api_schemas import (
    Accepted,
    EmailIn,
    ErrorResponse,
    PasswordChanged,
    PilotOut,
    ResetIn,
    SignUp,
    Verified,
    VerifyIn,
)
from app.db import get_session

router = APIRouter()

CHECK_YOUR_EMAIL = Accepted(status="check_your_email")


def _now() -> datetime:
    return datetime.now(UTC)


def _address(request: Request) -> str:
    return edge.client_address(request, request.app.state.settings)


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
    `invalid_name`, `terms_required`, 429 `too_many_requests` (5 sign-ups per hour per address)
    and `server_busy`. `accept_terms` must be true: the account records the version accepted.
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
        invite_token=body.invite_token,
        now=_now(),
        mail_allowed=lambda address: _limits(request).signup_email.hit(limits.email_key(address)),
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
    signup.verify_email(session, body.token, body.password, _now(), _limits(request).verify_wrong)
    return Verified(status="verified")


@router.post("/password/forgot", status_code=202, tags=["password"])
def forgot_password(
    body: EmailIn, request: Request, session: Session = Depends(get_session)
) -> Accepted:
    """Mail a password-reset link (valid 30 minutes) if the address has a confirmed account.
    Public. Always 202 with the same body: it never says whether the address has an account."""
    allowed = _limits(request).forgot_address.hit(_address(request)) and (
        _limits(request).forgot_email.hit(limits.email_key(body.email))
    )
    if allowed:
        signup.request_reset(session, request.app.state.settings, body.email, _now())
    return CHECK_YOUR_EMAIL


@router.post(
    "/password/reset",
    tags=["password"],
    responses={code: {"model": ErrorResponse} for code in (400, 422, 429)},
)
def reset_password(body: ResetIn, session: Session = Depends(get_session)) -> PasswordChanged:
    """Choose a new password with the token from the reset mail. Public. Signs that user out
    everywhere and does not sign them in. 400 `token_invalid_or_expired`; 422
    `password_too_weak` leaves the token usable."""
    signup.reset_password(session, body.token, body.password, _now())
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
    return_to: str | None = Query(None, max_length=500),
    session: Session = Depends(get_session),
    google: google_oauth.GoogleOAuth | None = Depends(google_oauth.get_google),
) -> RedirectResponse:
    """Send the browser to Google's consent screen (scopes `openid email profile` only). Public;
    navigate to it, do not fetch it. `accept_terms=true` must be sent by the sign-up screen: it
    is what lets a NEW account be created (an existing one signs in without it). `return_to` is
    a relative path to land on afterwards; anything else becomes `/`. 404
    `google_signin_unavailable` when the server has no Google client configured."""
    if google is None:
        raise _unavailable()
    url, state = google_signin.start_flow(session, google, return_to, _now())
    settings = request.app.state.settings
    response = RedirectResponse(url, status_code=302)
    response.set_cookie(
        google_signin.FLOW_COOKIE,
        google_signin.flow_cookie_value(state, accept_terms),
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
    `account_suspended`, `pilot_closed` or `email_unsupported`; a full pilot goes to
    `/pilot-full` and sets a 15-minute cookie that `GET /api/pilot` reads as `prefill_email`
    (the address is never put in a URL)."""
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
