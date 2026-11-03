"""Creating an account with a password, confirming the email, and resetting the password.

Every route here is public, so the rule is: **nothing in an answer may depend on whether an email
address already has an account.** Sign-up, resend and forgot all answer the same 202 for a new
address, an existing one and a malformed one, and the work that differs (a mail, a row) happens
after the answer is decided. The pilot's state (closed, full) is checked first and answers
everyone alike, an existing address included.

Tokens are single use and stored hashed (`app/tokens.py`). Verification and reset do NOT start a
session: the person signs in afterwards, so the code that issues sessions stays in one place
(`services.log_in`). A password reset bumps the user's `session_epoch`, which signs that user out
everywhere.

Pre-registration. Anyone can submit the sign-up form with someone else's address. A password
stored at sign-up and "activated" by whoever clicks the mailed link would then hand the account to
the person who chose it. So each verification token carries the hash of the password from ITS OWN
submission (`auth_tokens.pending_password_hash`), and `verify_email` needs that password typed
again: the real owner of the mailbox knows the one they chose, a link produced by anyone else's
submission refuses it, and the account's password becomes the verified one. A later submission
never invalidates an earlier link and never changes what an earlier link will confirm.
`users.password_hash` of an unverified row only serves the "confirm your email" answer at sign-in.
"""

import logging
import re
from collections.abc import Callable
from datetime import datetime

from sqlalchemy import func, select, text, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app import accounts, mail, passwords, tokens
from app.config import Settings
from app.limits import Limit
from app.models import AppState, AuthToken, User, WaitlistEntry
from app.services import Refused

logger = logging.getLogger(__name__)

# The "Last updated" date of the terms and privacy pages the person accepted at sign-up. Bump it
# with the pages: it is recorded on the account (`users.terms_version`).
TERMS_VERSION = "2026-11-03"
SIGNUP_LOCK = 6002  # serialises "count the slots" + "take one" (next to services.NEW_MEETING_LOCK)
MAX_NAME_CHARS = 200
MAX_EMAIL_CHARS = 254

_LOCAL = r"[a-z0-9.!#$%&'*+/=?^_`{|}~-]+"
_LABEL = r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?"
EMAIL_PATTERN = re.compile(rf"^{_LOCAL}@{_LABEL}(?:\.{_LABEL})+$")


def normalise_email(raw: str) -> str | None:
    """The address as stored (trimmed, lower-case), or None when it is not a plain ASCII
    address. International addresses are refused for now: Python and Postgres lower-case them
    differently, which the `users` check constraint would trip over."""
    email = raw.strip().lower()
    if len(email) > MAX_EMAIL_CHARS or not email.isascii() or not EMAIL_PATTERN.match(email):
        return None
    return email


# ---- the pilot ----


def slots_taken(session: Session, now: datetime) -> int:
    """Accounts that hold one of the pilot's slots: every user who is not an admin and not
    deleted (suspended and unverified ones too), plus invitations still waiting to be used."""
    users = session.scalar(
        select(func.count())
        .select_from(User)
        .where(User.is_admin.is_(False), User.status != "deleted")
    )
    invites = session.scalar(
        select(func.count())
        .select_from(AuthToken)
        .where(
            AuthToken.purpose == "waitlist_invite",
            AuthToken.used_at.is_(None),
            AuthToken.expires_at > now,
        )
    )
    return users + invites


def pilot_state(session: Session, now: datetime) -> str:
    """`closed` (the admin has not opened sign-up), `full` (the cap is reached) or `open`."""
    state = session.get(AppState, 1)
    if state is None or not state.signups_open:
        return "closed"
    if state.max_accounts is not None and slots_taken(session, now) >= state.max_accounts:
        return "full"
    return "open"


def _refuse_by_state(state: str) -> None:
    if state == "closed":
        raise Refused(
            403,
            "pilot_closed",
            "Sign-up is not open yet. Check back soon or join the waiting list.",
        )
    if state == "full":
        raise Refused(
            409,
            "pilot_full",
            "The pilot is full. Leave your email and we will write when a place opens.",
        )


# ---- sign-up ----


def link(settings: Settings, path: str, token: str | None = None, param: str = "token") -> str:
    """An absolute link on the public site, with a token in the query when there is one."""
    base = settings.public_base_url.rstrip("/")
    return f"{base}{path}" + (f"?{param}={token}" if token else "")


def _send_verification(
    session: Session, settings: Settings, user: User, password_hash: str, now: datetime
) -> None:
    """A verification token bound to `password_hash` and its mail, in the caller's transaction.
    Earlier tokens stay valid for their own passwords (see the module docstring)."""
    raw = tokens.issue(
        session,
        purpose="verify_email",
        email=user.email,
        user_id=user.id,
        now=now,
        pending_password_hash=password_hash,
    )
    mail.enqueue(
        session,
        user.email,
        "verify_email",
        {"link": link(settings, "/verify-email", raw), "name": user.name},
    )


def _hash(password: str) -> str:
    try:
        return passwords.hash_password(password)
    except passwords.HashBusy:
        raise Refused(429, "server_busy", "The server is busy. Try again in a moment.") from None


def _check_password(password: str) -> None:
    try:
        passwords.check_new_password(password)
    except passwords.PasswordRejected as exc:
        raise Refused(422, exc.code, exc.message) from None


def sign_up(
    session: Session,
    settings: Settings,
    *,
    name: str,
    email: str,
    password: str,
    accept_terms: bool,
    invite_token: str | None,
    now: datetime,
    mail_allowed: Callable[[str], bool] = lambda address: True,
) -> None:
    """Create an unverified account and queue its verification mail, or do the quiet equivalent
    for an address that already has one. Commits. Raises `Refused` for what the person can fix
    (a weak password, no terms) and for the pilot's state; never for who has an account.
    `mail_allowed(address)` is asked once the request would touch an account or send a mail, and
    answers whether this address may be mailed now (the caller's per-email limit); when it says
    no, nothing happens and the caller still answers 202."""
    address = normalise_email(email)
    name = name.strip()
    if address is None:
        raise Refused(422, "invalid_email", "Enter a valid email address.")
    if not name or len(name) > MAX_NAME_CHARS:
        raise Refused(422, "invalid_name", f"Enter your name (up to {MAX_NAME_CHARS} characters).")
    if not accept_terms:
        raise Refused(
            422, "terms_required", "Accept the Terms and the Privacy Policy to create an account."
        )
    _check_password(password)
    password_hash = _hash(password)  # always, so an existing address costs the same time
    try:
        session.execute(text("select pg_advisory_xact_lock(:key)"), {"key": SIGNUP_LOCK})
        _sign_up_locked(
            session, settings, name, address, password_hash, invite_token, now, mail_allowed
        )
        session.commit()
    except IntegrityError:  # lost a race on the unique email: the address has an account now
        session.rollback()
    except Refused:
        session.rollback()
        raise


def _sign_up_locked(
    session: Session,
    settings: Settings,
    name: str,
    address: str,
    password_hash: str,
    invite_token: str | None,
    now: datetime,
    mail_allowed: Callable[[str], bool],
) -> None:
    invite = None
    if invite_token:
        invite = tokens.find_live(session, invite_token, "waitlist_invite", now)
        if invite is None or invite.email != address:
            raise Refused(
                400,
                "invite_invalid",
                "That invitation is not valid any more. Join the waiting list to get a new one.",
            )
    else:
        _refuse_by_state(pilot_state(session, now))
    state = session.get(AppState, 1)
    if state is None or not state.password_signup_enabled:
        raise Refused(
            403,
            "password_signup_disabled",
            "Creating an account with a password is not available yet. Use Google to sign up.",
        )
    if not mail_allowed(address):
        return
    # The row is locked from here to the commit: `verify_email` takes the same lock before it
    # writes, so a confirmation and a re-submission for one address cannot interleave (an
    # attacker's write must never land on an account confirmed a moment before).
    existing = session.scalars(
        select(User).where(User.email == address).with_for_update(of=User)
    ).first()
    if existing is None:
        user = User(
            email=address,
            name=name,
            password_hash=password_hash,
            terms_accepted_at=now,
            terms_version=TERMS_VERSION,
        )
        session.add(user)
        session.flush()
        accounts.add_user_defaults(session, user)
        if invite is not None:
            tokens.consume(session, invite_token, "waitlist_invite", now)
            session.execute(
                update(WaitlistEntry).where(WaitlistEntry.email == address).values(status="joined")
            )
        _send_verification(session, settings, user, password_hash, now)
    elif existing.status == "active" and existing.email_verified_at is None:
        # Still unconfirmed: the sign-in answer follows the newest submission, but what a link
        # confirms is decided by that link's own token (see the module docstring).
        existing.name, existing.password_hash = name, password_hash
        existing.terms_accepted_at, existing.terms_version = now, TERMS_VERSION
        _send_verification(session, settings, existing, password_hash, now)
    elif existing.status != "deleted":
        mail.enqueue(
            session,
            address,
            "already_registered",
            {"link": link(settings, "/login"), "name": existing.name},
        )


def verify_email(
    session: Session,
    raw_token: str,
    password: str,
    now: datetime,
    wrong_guesses: Limit | None = None,
) -> None:
    """Confirm the email the token was sent to, given the password chosen with that sign-up.
    Commits. The token is used up only when the password is right, so a typo can be fixed, but
    five wrong passwords (`wrong_guesses`, counted per token) kill it."""
    token = tokens.find_live(session, raw_token, "verify_email", now)
    user = session.get(User, token.user_id) if token else None
    if user is None or user.status == "deleted" or user.email != token.email:
        raise _bad_token()
    guess_key = tokens.digest(raw_token)
    if wrong_guesses is not None and wrong_guesses.full(guess_key):
        raise _bad_token()
    try:
        right = passwords.verify_password(token.pending_password_hash, password)
    except passwords.HashBusy:
        raise Refused(429, "server_busy", "The server is busy. Try again in a moment.") from None
    if not right:
        if wrong_guesses is not None:
            wrong_guesses.add(guess_key)
        raise Refused(
            400,
            "password_mismatch",
            "That is not the password you chose when you signed up. "
            "If you do not remember it, create the account again.",
        )
    if tokens.consume(session, raw_token, "verify_email", now) is None:  # used a moment ago
        session.rollback()
        raise _bad_token()
    # Lock the account before writing and look again: a sign-up for this address may have held it.
    user = session.get(User, user.id, with_for_update=True, populate_existing=True)
    if user is None or user.status == "deleted":
        session.rollback()
        raise _bad_token()
    user.email_verified_at = user.email_verified_at or now
    user.password_hash = token.pending_password_hash
    tokens.revoke(session, "verify_email", user.id, now)
    session.commit()


def resend_verification(session: Session, settings: Settings, email: str, now: datetime) -> None:
    """Mail a new verification link to an unverified account; nothing at all otherwise. The new
    link confirms the same password as the newest link before it (a resend never takes a
    password), so with none to copy there is nothing to send. Commits. The caller answers 202
    either way."""
    address = normalise_email(email)
    user = session.scalars(select(User).where(User.email == address)).first() if address else None
    if user is None or user.status != "active" or user.email_verified_at is not None:
        return
    newest = session.scalars(
        select(AuthToken)
        .where(AuthToken.user_id == user.id, AuthToken.purpose == "verify_email")
        .order_by(AuthToken.id.desc())
    ).first()
    if newest is None or newest.pending_password_hash is None:
        return
    _send_verification(session, settings, user, newest.pending_password_hash, now)
    session.commit()


# ---- password reset ----


def _bad_token() -> Refused:
    return Refused(
        400,
        "token_invalid_or_expired",
        "This link has expired or was already used. Ask for a new one.",
    )


def issue_reset(session: Session, settings: Settings, user: User, now: datetime) -> str:
    """A 30-minute reset token for `user` (older ones stop working); the link to send. In the
    caller's transaction, no commit."""
    tokens.revoke(session, "reset_password", user.id, now)
    raw = tokens.issue(
        session, purpose="reset_password", email=user.email, user_id=user.id, now=now
    )
    return link(settings, "/reset-password", raw)


def request_reset(session: Session, settings: Settings, email: str, now: datetime) -> None:
    """Mail a reset link to a verified, active account; nothing otherwise. Commits. The caller
    answers 202 either way."""
    address = normalise_email(email)
    user = session.scalars(select(User).where(User.email == address)).first() if address else None
    if user is None or user.status != "active" or user.email_verified_at is None:
        return
    link = issue_reset(session, settings, user, now)
    mail.enqueue(session, user.email, "reset_password", {"link": link, "name": user.name})
    session.commit()


def reset_password(session: Session, raw_token: str, password: str, now: datetime) -> None:
    """Set a new password with a reset token and sign the user out everywhere. Commits. A weak
    password is refused before the token is touched, so the person can try again."""
    _check_password(password)
    password_hash = _hash(password)
    token = tokens.consume(session, raw_token, "reset_password", now)
    user = session.get(User, token.user_id) if token else None
    if user is None or user.status != "active" or user.email != token.email:
        session.rollback()
        raise _bad_token()
    user.password_hash = password_hash
    user.session_epoch = User.session_epoch + 1
    tokens.revoke(session, "reset_password", user.id, now)
    session.commit()


# ---- the waitlist ----


def join_waitlist(session: Session, address: str, name: str | None) -> None:
    """Add an address to the waiting list; nothing changes for one that is already there (or was
    removed by the admin). Commits."""
    name = name.strip()[:MAX_NAME_CHARS] if name and name.strip() else None
    exists = session.scalars(select(WaitlistEntry.id).where(WaitlistEntry.email == address)).first()
    if exists is None:
        try:
            session.add(WaitlistEntry(email=address, name=name))
            session.commit()
        except IntegrityError:  # two requests at once: the other one won
            session.rollback()
