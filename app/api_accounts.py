"""The JSON routes a visitor uses before they have an account, and the account routes after:
sign-up, email verification, password reset, the pilot's state, the waitlist, Google sign-in,
and the notification bell. Included into `app.api.router`, so they share its `/api` prefix, its
error shape and its OpenAPI file.

The public ones are listed one by one in `auth.PUBLIC_API`; they skip the login check but still
need the CSRF header on every non-GET. Their work is in `app/signup.py`, whose first rule is that
no answer may depend on whether an address already has an account.
"""

from datetime import UTC, datetime

from fastapi import APIRouter, Depends, Request
from sqlalchemy.orm import Session

from app import edge, limits, signup
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
    """Whether sign-up is `open`, `full` or `closed`. Public. No counts of other people."""
    return PilotOut(state=signup.pilot_state(session, _now()), prefill_email=None)


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
def verify_email(body: VerifyIn, session: Session = Depends(get_session)) -> Verified:
    """Confirm an email with the token from the link and the password chosen at sign-up.
    Public. The page the link opens must show a password field and a button that calls this
    (mail scanners open links, and a GET must not verify). Does not sign the user in.
    400 `token_invalid_or_expired`: wrong, used or older than 24 hours. 400 `password_mismatch`:
    not the password submitted with this link's sign-up; the link stays usable."""
    signup.verify_email(session, body.token, body.password, _now())
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
