"""What the admin screens do: users, pilot settings, the waitlist, failed mail.

Every function here takes the rows it acts on after the route has already required an admin. The
admin can see and change accounts and the pilot, and nothing of any user's meetings, notes or
calendar: there is no function in this module that reads them.
"""

import logging
from datetime import datetime

from fastapi import HTTPException
from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from app import mail, signup, tokens, usage
from app.config import Settings
from app.models import AppState, AuthToken, MailOutbox, Notification, User, WaitlistEntry
from app.services import Refused

logger = logging.getLogger(__name__)

MAIL_FAILURES_SHOWN = 50
ADMIN_LOCK = 6003  # serialises "is this the last admin?" with the change that depends on it


# ---- users ----


def list_users(session: Session, limit: int, before_id: int | None) -> list[User]:
    """Users that exist for the admin (not the ones being deleted), newest first."""
    query = select(User).where(User.status != "deleted").order_by(User.id.desc()).limit(limit)
    if before_id is not None:
        query = query.where(User.id < before_id)
    return list(session.scalars(query))


def user_or_404(session: Session, user_id: int) -> User:
    user = session.get(User, user_id) if 0 < user_id <= 2**63 - 1 else None
    if user is None or user.status == "deleted":
        raise HTTPException(404, "No such user")
    return user


def _is_last_admin(session: Session, user: User) -> bool:
    """Whether `user` is the only active admin: the one who cannot be suspended or deleted."""
    if not (user.is_admin and user.status == "active"):
        return False
    others = session.scalar(
        select(func.count())
        .select_from(User)
        .where(User.is_admin.is_(True), User.status == "active", User.id != user.id)
    )
    return others == 0


def _refuse_last_admin() -> Refused:
    return Refused(
        409,
        "last_admin",
        "This is the only active admin account, so it cannot be suspended or deleted.",
    )


def _lock_admins(session: Session, user: User) -> None:
    """Take the admin lock and re-read `user`, so the last-admin check and the change that
    follows it cannot interleave with another admin's (two admins removing each other at once)."""
    session.execute(text("select pg_advisory_xact_lock(:key)"), {"key": ADMIN_LOCK})
    session.refresh(user)


def update_user(session: Session, user: User, changes: dict) -> User:
    """Apply `changes` (only `status`, between active and suspended, and `hours_cap_seconds`).
    Commits. The last active admin cannot be suspended. Suspending also signs the user out for
    good (their `session_epoch` moves on), so reactivating them later does not bring an old
    cookie back to life."""
    suspending = changes.get("status") == "suspended"
    if suspending:
        _lock_admins(session, user)
        if _is_last_admin(session, user):
            session.rollback()
            raise _refuse_last_admin()
        user.session_epoch = User.session_epoch + 1
    if changes.get("status") is not None:
        user.status = changes["status"]
    if "hours_cap_seconds" in changes:
        user.hours_cap_seconds = changes["hours_cap_seconds"]
    session.commit()
    return user


def schedule_deletion(session: Session, user: User) -> None:
    """Mark the user `deleted`: out at once, slot free, the worker purges them
    (`app/deletion.py`). Commits. The last active admin cannot be deleted."""
    _lock_admins(session, user)
    if _is_last_admin(session, user):
        session.rollback()
        raise _refuse_last_admin()
    user.status = "deleted"
    session.commit()


def reset_link(
    session: Session, settings: Settings, user: User, now: datetime, by: int | None = None
) -> str:
    """A one-time password-reset link for the admin to hand over (the no-mail fallback). Only for
    a verified, active, ordinary account: a link is not proof of the address, and an admin must
    never be able to take over another admin's account. The user is told in the app, and the
    admin's act is logged (ids only, never the link). Commits."""
    if user.is_admin:
        raise Refused(409, "admin_account", "A reset link cannot be made for an admin account.")
    if user.status != "active":
        raise Refused(409, "account_suspended", "This account is suspended.")
    if user.email_verified_at is None:
        raise Refused(409, "user_not_verified", "This account has not confirmed its email yet.")
    link = signup.issue_reset(session, settings, user, now)
    session.add(
        Notification(
            user_id=user.id,
            kind="account.reset_link_issued",
            severity="warning",
            title="A password reset link was made for your account",
            body=(
                "An administrator made a one-time password reset link for your account. If you "
                "did not ask for this, tell us at once."
            ),
            dedupe_key=f"reset_link:{now.timestamp()}",
            created_at=now,
        )
    )
    session.commit()
    logger.info("admin %s made a reset link for user %s", by, user.id)
    return link


# ---- pilot settings ----


def pilot(session: Session, now: datetime) -> dict:
    state = session.get(AppState, 1) or AppState(
        signups_open=False, max_accounts=10, default_hours_cap_seconds=usage.DEFAULT_CAP_SECONDS,
        password_signup_enabled=False,
    )  # fmt: skip
    return {
        "signups_open": bool(state.signups_open),
        "max_accounts": state.max_accounts,
        "default_hours_cap_seconds": state.default_hours_cap_seconds,
        "password_signup_enabled": bool(state.password_signup_enabled),
        "slots_taken": signup.slots_taken(session, now),
        "state": signup.pilot_state(session, now),
    }


def set_pilot(session: Session, values: dict, now: datetime) -> dict:
    state = session.get(AppState, 1)
    if state is None:
        state = AppState(id=1)
        session.add(state)
        session.flush()  # insert with the column defaults first, so a None below is an UPDATE
    for key, value in values.items():
        setattr(state, key, value)
    session.commit()
    return pilot(session, now)


# ---- the waitlist ----


def list_waitlist(session: Session, limit: int, before_id: int | None) -> list[WaitlistEntry]:
    """Entries still of interest (not removed), oldest first, `before_id` meaning "after this id"
    for the next page."""
    query = (
        select(WaitlistEntry)
        .where(WaitlistEntry.status != "removed")
        .order_by(WaitlistEntry.id)
        .limit(limit)
    )
    if before_id is not None:
        query = query.where(WaitlistEntry.id > before_id)
    return list(session.scalars(query))


def entry_or_404(session: Session, entry_id: int) -> WaitlistEntry:
    entry = session.get(WaitlistEntry, entry_id) if 0 < entry_id <= 2**63 - 1 else None
    if entry is None:
        raise HTTPException(404, "No such waitlist entry")
    return entry


def _revoke_invite(session: Session, entry: WaitlistEntry, now: datetime) -> None:
    if entry.invite_token_id is not None:
        token = session.get(AuthToken, entry.invite_token_id)
        if token is not None and token.used_at is None:
            token.used_at = now


def invite(
    session: Session, settings: Settings, entry: WaitlistEntry, now: datetime
) -> tuple[bool, str | None, datetime]:
    """Reserve a slot for this person for 7 days and invite them. Returns (emailed, link, expiry):
    the link is for the admin to hand over only when no mail provider exists. Commits. Takes the
    sign-up lock, so an invite and a sign-up cannot both take the last slot; inviting again
    replaces the earlier link and holds one slot."""
    session.execute(text("select pg_advisory_xact_lock(:key)"), {"key": signup.SIGNUP_LOCK})
    session.refresh(entry)
    if entry.status in ("joined", "removed"):
        session.rollback()
        raise Refused(409, "waitlist_entry_closed", "This person is no longer on the waiting list.")
    _revoke_invite(session, entry, now)
    state = session.get(AppState, 1)
    if (
        state is not None
        and state.max_accounts is not None
        and signup.slots_taken(session, now) >= state.max_accounts
    ):
        session.rollback()
        raise Refused(
            409, "pilot_full", "There is no free place to reserve. Raise the limit first."
        )
    raw = tokens.issue(session, purpose="waitlist_invite", email=entry.email, user_id=None, now=now)
    token = session.scalars(
        select(AuthToken).where(AuthToken.token_hash == tokens.digest(raw))
    ).one()
    link = signup.link(settings, "/signup", raw, param="invite")
    entry.status, entry.invited_at, entry.invite_token_id = "invited", now, token.id
    emailed = mail.build_mailer(settings) is not None
    if emailed:
        mail.enqueue(session, entry.email, "waitlist_invite", {"link": link, "name": entry.name})
    session.commit()
    return emailed, None if emailed else link, token.expires_at


def remove_entry(session: Session, entry: WaitlistEntry, now: datetime) -> None:
    """Take someone off the list; a live invitation is cancelled and its slot freed. Commits."""
    _revoke_invite(session, entry, now)
    entry.status = "removed"
    session.commit()


# ---- mail ----


def mail_failures(session: Session) -> list[MailOutbox]:
    return list(
        session.scalars(
            select(MailOutbox)
            .where(MailOutbox.status == "failed")
            .order_by(MailOutbox.id.desc())
            .limit(MAIL_FAILURES_SHOWN)
        )
    )
