"""Sign in and sign up with Google, once the person is back from Google.

`start_flow` writes the round trip's state to `oauth_flows` (only the hash of the state; the PKCE
verifier and nonce are needed again at the callback); `finish_flow` uses it up exactly once.
`complete` decides which account the Google identity is, in this order (spec 4.3):

1. a user who already has this Google account (`google_sub`): sign in. If Google's email for the
   account changed, the stored email follows, unless another user has it.
2. a user with the same (verified-by-Google) email: a verified password account gets the Google
   identity linked and keeps its password. An UNVERIFIED password account is verified, linked and
   its password is cleared and its verification links dropped: someone may have pre-registered the
   address with a password they know. An account already linked to a different Google account is
   never relinked.
3. nobody: a new account, verified, with no password, if the pilot has room (or the address holds
   a live waitlist invitation) and the person accepted the terms at the consent text.

Steps 1 and 2 never look at the pilot's state (existing users always get in). Everything runs
under the sign-up advisory lock, so ten parallel new Google sign-ups at nine accounts make one,
and a refusal leaves no half-created user.

The `pilot_email` cookie lets the "pilot full" page pre-fill the waitlist form from the Google
address without ever putting an address in a URL: signed (HMAC under its own key label, so no
session cookie can stand in for it), 15 minutes.
"""

import base64
import hashlib
import hmac
import json
import re
import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta

from sqlalchemy import delete, select, text, update
from sqlalchemy.orm import Session

from app import accounts, google_oauth, signup, tokens
from app.config import Settings
from app.google_oauth import Identity
from app.models import AuthToken, OAuthFlow, User, WaitlistEntry

FLOW_TTL = timedelta(minutes=10)
PREFILL_SECONDS = 15 * 60
FLOW_COOKIE = "google_flow"
FLOW_COOKIE_PATH = "/api/auth/google"
PREFILL_COOKIE = "pilot_email"
PREFILL_COOKIE_PATH = "/api/pilot"
TERMS_FLAG = ".t"  # a flow cookie "<state>.t" means the terms were accepted at the consent text
MAX_NAME_CHARS = 200

# A relative path with exactly one leading slash: never `//host`, a scheme or a backslash.
# `\Z`, not `$`: `$` also matches before a trailing newline.
RETURN_TO_PATTERN = re.compile(r"^/(?![/\\])[A-Za-z0-9._~!$&'()*+,;=:@%/?-]{0,200}\Z")


def safe_return_to(value: str | None) -> str:
    return value if value and RETURN_TO_PATTERN.match(value) else "/"


# ---- the round trip ----


def start_flow(
    session: Session, google: google_oauth.GoogleOAuth, return_to: str | None, now: datetime
) -> tuple[str, str]:
    """Store a new flow and return (consent URL, state). Commits."""
    state, nonce = secrets.token_urlsafe(32), secrets.token_urlsafe(24)
    verifier, challenge = google_oauth.new_pkce()
    session.add(
        OAuthFlow(
            state_hash=tokens.digest(state),
            purpose="signin",
            code_verifier=verifier,
            nonce=nonce,
            return_to=safe_return_to(return_to),
            expires_at=now + FLOW_TTL,
        )
    )
    session.commit()
    return google.authorize_url(state=state, nonce=nonce, code_challenge=challenge), state


def finish_flow(session: Session, state: str | None, now: datetime) -> OAuthFlow | None:
    """Use up the flow for `state`: its row, or None when unknown, used before or expired. The
    row is deleted whatever the answer, so a state can never be tried twice. Commits."""
    if not state or len(state) > 200:
        return None
    flow = session.scalars(
        delete(OAuthFlow)
        .where(OAuthFlow.state_hash == tokens.digest(state), OAuthFlow.purpose == "signin")
        .returning(OAuthFlow)
    ).first()
    session.commit()
    return flow if flow is not None and flow.expires_at > now else None


def flow_cookie_matches(cookie: str | None, state: str) -> bool:
    """Whether this browser started the flow: its cookie is the state, with or without the terms
    flag. Binds the callback to the browser that began it (against login CSRF)."""
    if not cookie:
        return False
    base = cookie.removesuffix(TERMS_FLAG)
    return hmac.compare_digest(base.encode(), state.encode())


def flow_cookie_value(state: str, accepted_terms: bool) -> str:
    return state + (TERMS_FLAG if accepted_terms else "")


def cookie_accepted_terms(cookie: str | None) -> bool:
    return bool(cookie) and cookie.endswith(TERMS_FLAG)


# ---- matching ----


@dataclass(frozen=True)
class Outcome:
    """`user` is set on success. Otherwise `error` is a code for `/login?error=` or, for a full
    pilot, `full` is True and `email` is what the pilot-full page pre-fills."""

    user: User | None = None
    error: str | None = None
    full: bool = False
    email: str | None = None


def complete(
    session: Session, identity: Identity, *, accepted_terms: bool, now: datetime
) -> Outcome:
    """Find or create the account for a Google identity (see the module docstring). Commits."""
    address = signup.normalise_email(identity.email)
    if address is None:
        return Outcome(error="email_unsupported")
    session.execute(text("select pg_advisory_xact_lock(:key)"), {"key": signup.SIGNUP_LOCK})
    outcome = _match(session, identity, address, accepted_terms, now)
    if outcome.user is not None:
        session.commit()
    else:
        session.rollback()
    return outcome


def _match(
    session: Session, identity: Identity, address: str, accepted_terms: bool, now: datetime
) -> Outcome:
    user = session.scalars(select(User).where(User.google_sub == identity.sub)).first()
    if user is not None:
        return _sign_in_by_sub(session, user, address)
    user = session.scalars(
        select(User).where(User.email == address).with_for_update(of=User)
    ).first()
    if user is not None:
        return _link_by_email(session, user, identity, now)
    return _create(session, identity, address, accepted_terms, now)


def _refusal(user: User) -> Outcome | None:
    if user.status == "suspended":
        return Outcome(error="account_suspended")
    if user.status != "active":
        return Outcome(error="google_failed")
    return None


def _sign_in_by_sub(session: Session, user: User, address: str) -> Outcome:
    if refused := _refusal(user):
        return refused
    if user.email != address:
        taken = session.scalars(select(User.id).where(User.email == address)).first()
        if taken is None:
            user.email = address
    return Outcome(user=user)


def _link_by_email(session: Session, user: User, identity: Identity, now: datetime) -> Outcome:
    if refused := _refusal(user):
        return refused
    if user.google_sub is not None:  # linked to another Google account (ours was not found by sub)
        return Outcome(error="google_failed")
    if user.email_verified_at is None and not signup.may_confirm(session, user, now):
        return Outcome(full=True, email=user.email)  # confirming would take a place that is gone
    user.google_sub = identity.sub
    if user.email_verified_at is None:
        signup.redeem_invite(session, user.email, now)  # an invitation for this address is theirs
        # Pre-registration defence: the password on an unverified row may be someone else's.
        user.email_verified_at = now
        user.password_hash = None
        tokens.revoke(session, "verify_email", user.id, now)
    return Outcome(user=user)


def _create(
    session: Session, identity: Identity, address: str, accepted_terms: bool, now: datetime
) -> Outcome:
    invite = tokens.live_invite_for(session, address, now)
    if invite is None:
        state = signup.pilot_state(session, now)
        if state == "closed":
            return Outcome(error="pilot_closed")
        if state == "full":
            return Outcome(full=True, email=address)
    if not accepted_terms:
        return Outcome(error="terms_required")
    name = (identity.name or address.split("@")[0]).strip()[:MAX_NAME_CHARS]
    user = User(
        email=address,
        name=name or None,
        google_sub=identity.sub,
        email_verified_at=now,
        terms_accepted_at=now,
        terms_version=signup.TERMS_VERSION,
    )
    session.add(user)
    session.flush()
    accounts.add_user_defaults(session, user)
    if invite is not None:
        session.execute(update(AuthToken).where(AuthToken.id == invite.id).values(used_at=now))
        session.execute(
            update(WaitlistEntry).where(WaitlistEntry.email == address).values(status="joined")
        )
    return Outcome(user=user)


# ---- the "pilot full" pre-fill cookie ----


def prefill_key(settings: Settings) -> bytes:
    return hmac.new(settings.session_secret.encode(), b"pilot-prefill", "sha256").digest()


def _sig(key: bytes, body: str) -> str:
    return base64.urlsafe_b64encode(hmac.new(key, body.encode(), hashlib.sha256).digest()).decode()


def sign_prefill(key: bytes, email: str, now: datetime) -> str:
    payload = {"e": email, "iat": int(now.timestamp())}
    body = base64.urlsafe_b64encode(json.dumps(payload).encode()).decode().rstrip("=")
    return f"{body}.{_sig(key, body)}"


def read_prefill(key: bytes, cookie: str | None, now: datetime) -> str | None:
    """The email in a prefill cookie this app signed less than 15 minutes ago, else None."""
    if not cookie or cookie.count(".") != 1:
        return None
    body, signature = cookie.split(".")
    if not hmac.compare_digest(signature.encode(), _sig(key, body).encode()):
        return None
    try:
        payload = json.loads(base64.urlsafe_b64decode(body + "=" * (-len(body) % 4)))
        email, issued = payload["e"], payload["iat"]
    except (ValueError, KeyError, TypeError):
        return None
    if not (isinstance(email, str) and isinstance(issued, int) and not isinstance(issued, bool)):
        return None
    return email if 0 <= now.timestamp() - issued <= PREFILL_SECONDS else None
