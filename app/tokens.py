"""Single-use tokens for email verification, password reset and waitlist invites.

The token handed out is 32 random bytes (URL-safe text); the database holds only its SHA-256
hash, so a leaked table cannot be used to verify an account or reset a password. A token works
once (`consume` marks it used in the same statement that finds it, so two parallel requests
cannot both win) and until its expiry. Nothing here commits: the caller's transaction decides.
"""

import hashlib
import secrets
from datetime import datetime, timedelta

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.models import AuthToken

TTL = {
    "verify_email": timedelta(hours=24),
    "reset_password": timedelta(minutes=30),
    "waitlist_invite": timedelta(days=7),
}
MAX_TOKEN_CHARS = 200  # anything longer cannot be one of ours


def digest(raw: str) -> str:
    return hashlib.sha256(raw.encode()).hexdigest()


def issue(
    session: Session,
    *,
    purpose: str,
    email: str,
    user_id: int | None,
    now: datetime,
    ttl: timedelta | None = None,
    pending_password_hash: str | None = None,
) -> str:
    """Add a token row and return the raw token, shown once (in a mail or to the admin)."""
    raw = secrets.token_urlsafe(32)
    session.add(
        AuthToken(
            user_id=user_id,
            email=email,
            purpose=purpose,
            token_hash=digest(raw),
            expires_at=now + (ttl or TTL[purpose]),
            pending_password_hash=pending_password_hash,
        )
    )
    session.flush()
    return raw


def consume(session: Session, raw: str, purpose: str, now: datetime) -> AuthToken | None:
    """Use up a token: the row it belongs to, or None when it is unknown, of another purpose,
    already used or expired."""
    if not raw or len(raw) > MAX_TOKEN_CHARS or not raw.isascii():
        return None
    token_id = session.scalars(
        update(AuthToken)
        .where(
            AuthToken.token_hash == digest(raw),
            AuthToken.purpose == purpose,
            AuthToken.used_at.is_(None),
            AuthToken.expires_at > now,
        )
        .values(used_at=now)
        .returning(AuthToken.id)
    ).first()
    if token_id is None:
        return None
    return session.scalars(
        select(AuthToken).where(AuthToken.id == token_id).execution_options(populate_existing=True)
    ).one()


def revoke(session: Session, purpose: str, user_id: int, now: datetime) -> None:
    """Use up every live token of this purpose for this user (a newer one replaces them)."""
    session.execute(
        update(AuthToken)
        .where(
            AuthToken.user_id == user_id,
            AuthToken.purpose == purpose,
            AuthToken.used_at.is_(None),
        )
        .values(used_at=now)
    )


def find_live(session: Session, raw: str, purpose: str, now: datetime) -> AuthToken | None:
    """The live token for `raw` without using it up (to check an invite before acting on it)."""
    if not raw or len(raw) > MAX_TOKEN_CHARS or not raw.isascii():
        return None
    return session.scalars(
        select(AuthToken).where(
            AuthToken.token_hash == digest(raw),
            AuthToken.purpose == purpose,
            AuthToken.used_at.is_(None),
            AuthToken.expires_at > now,
        )
    ).first()
