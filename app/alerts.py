"""Alerts: what the app tells a user, or the admins, when something needs them (plan C11b).

One call writes the in-app notification (`notifications`, the bell) and, when asked, queues the
email (`app/mail.py`). Three rules hold for every caller (the join runner, the connection state
machine, the disconnected mail):

* **Deduped by key, at the database.** A notification is unique per user and `dedupe_key`
  (`INSERT ... ON CONFLICT DO NOTHING`), so two processes racing write one row, and **the mail is
  queued only by the call that wrote the row**: a repeated alert is neither a second bell entry
  nor a second email. `bucket_key` makes a key that repeats every 30 minutes, so a condition that
  lasts an hour alerts twice, not every ten seconds.
* **A mail problem never reaches the caller.** Alerts are sent from code that is doing something
  more important (placing a bot). The notification row is written first; the mail is queued in a
  savepoint, and whatever goes wrong there is logged by exception type only (a provider's message
  can hold an address) and rolled back alone.
* **Nothing here commits.** The caller's transaction owns the rows, as with `mail.enqueue`: they
  stand or fall with the action that raised the alert.

The email respects the person: not sent when the user has switched `notify_email` off, when the
address is suppressed (a bounce, a complaint, an unsubscribe), when the account is not active, or
when no mail provider is configured. The bell is not email, so it rings regardless.

**Admin alerts never carry a meeting link.** A meeting link can hold a Zoom passcode, so any
web address in an admin alert (and the known meeting hosts without a scheme) is replaced by
"[link removed]" before the text is stored or mailed; the caller names the user and the time.
"""

import logging
import re
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app import mail
from app.config import get_settings
from app.mail_message import valid_address
from app.models import SEVERITIES, Notification, User, UserSettings

logger = logging.getLogger(__name__)

ALERT_TEMPLATE = "alert"  # the mail template for an alert: payload {"title": ..., "body": ...}
TITLE_LIMIT = 200  # notifications.title
KEY_LIMIT = 200  # notifications.dedupe_key
ADMIN_KIND_PREFIX = "admin."
LINK_REMOVED = "[link removed]"

# A web address with a scheme or `www.`, or a link to a meeting host written without one.
_URL = re.compile(
    r"""(?ix)
    (?:\b(?:https?://|www\.)|
       \b(?:[\w-]+\.)*(?:zoom\.us|zoom\.com|meet\.google\.com|teams\.microsoft\.com|teams\.live\.com)/)
    \S*
    """
)


def bucket_key(kind: str, now: datetime, minutes: int = 30) -> str:
    """A dedupe key that is the same for every moment inside one `minutes`-long bucket (aligned to
    the Unix epoch, so every process agrees) and changes at its edge: `kind:<bucket number>`."""
    if now.tzinfo is None:
        raise ValueError("bucket_key needs a timezone-aware time")
    if minutes <= 0:
        raise ValueError("minutes must be positive")
    return f"{kind}:{int(now.timestamp() // (minutes * 60))}"


def _strip_links(text: str) -> str:
    return _URL.sub(LINK_REMOVED, text)


def _check(*, severity: str, dedupe_key: str) -> None:
    if severity not in SEVERITIES:
        raise ValueError(f"severity must be one of {SEVERITIES}")
    if not dedupe_key or len(dedupe_key) > KEY_LIMIT:
        raise ValueError(f"dedupe_key must be 1 to {KEY_LIMIT} characters")


def _write(
    session: Session,
    user_id: int,
    *,
    kind: str,
    severity: str,
    title: str,
    body: str,
    dedupe_key: str,
    meeting_id: int | None,
) -> bool:
    """Write the notification unless one with this key exists. True when this call wrote it.
    A user who is gone (the foreign key) is logged and skipped, never raised."""
    try:
        with session.begin_nested():
            written = session.execute(
                insert(Notification)
                .values(
                    user_id=user_id,
                    kind=kind,
                    severity=severity,
                    title=title[:TITLE_LIMIT],
                    body=body,
                    meeting_id=meeting_id,
                    dedupe_key=dedupe_key,
                )
                .on_conflict_do_nothing(index_elements=["user_id", "dedupe_key"])
                .returning(Notification.id)
            ).scalar()
    except IntegrityError as exc:
        logger.warning("alert %s not written (%s)", kind, type(exc).__name__)
        return False
    return written is not None


def _mail_wanted(session: Session, address: str) -> bool:
    """Whether any mail may go to `address` at all: a provider is configured and the address is
    not suppressed (a bounce, a complaint or an unsubscribe)."""
    return mail.is_configured(get_settings()) and mail.suppression_reason(session, address) is None


def _wants_email(session: Session, user: User) -> bool:
    """The account's own switch: active, and `user_settings.notify_email` on (on when the account
    has no settings row yet)."""
    if user.status != "active":
        return False
    value = session.scalar(select(UserSettings.notify_email).where(UserSettings.user_id == user.id))
    return (value is None or bool(value)) and _mail_wanted(session, user.email)


def _queue_mail(session: Session, address: str, title: str, body: str, template: str) -> None:
    """Queue the email in a savepoint; a failure is logged by type and undoes only the mail."""
    try:
        with session.begin_nested():
            mail.enqueue(session, address, template, {"title": title, "body": body})
    except Exception as exc:
        logger.warning("alert mail not queued (%s)", type(exc).__name__)


def notify_user(
    session: Session,
    user_id: int,
    *,
    kind: str,
    severity: str,
    title: str,
    body: str,
    dedupe_key: str,
    meeting_id: int | None = None,
    email_template: str | None = None,
) -> None:
    """Tell one user: a bell entry, and an email when `email_template` names one (the `alert`
    template for plain text). A repeated `dedupe_key` for the same user does nothing at all.
    Raises ValueError for a caller bug (an unknown severity, a key that is empty or too long); never
    for a mail problem or a user who no longer exists."""
    _check(severity=severity, dedupe_key=dedupe_key)
    written = _write(
        session,
        user_id,
        kind=kind,
        severity=severity,
        title=title,
        body=body,
        dedupe_key=dedupe_key,
        meeting_id=meeting_id,
    )
    if not written or email_template is None:
        return
    try:
        user = session.get(User, user_id)
        if user is not None and _wants_email(session, user):
            _queue_mail(session, user.email, title, body, email_template)
    except Exception as exc:  # reading the account or its settings must not reach the caller either
        logger.warning("alert mail not queued (%s)", type(exc).__name__)


def notify_admins(session: Session, *, kind: str, title: str, body: str, dedupe_key: str) -> None:
    """Tell every active admin: a bell entry each, and one email per new `dedupe_key` to
    `ADMIN_ALERT_EMAIL`, or, when that is empty, one to each admin who has not switched
    `notify_email` off. `kind` starts with "admin.". Web addresses are removed from the text first
    (see the module note): name the user and the time in `body`, never the meeting."""
    if not kind.startswith(ADMIN_KIND_PREFIX):
        raise ValueError(f"an admin alert's kind starts with {ADMIN_KIND_PREFIX!r}")
    _check(severity="warning", dedupe_key=dedupe_key)
    title, body = _strip_links(title), _strip_links(body)
    admins = list(
        session.scalars(
            select(User).where(User.is_admin.is_(True), User.status == "active").order_by(User.id)
        )
    )
    if not admins:
        logger.warning("admin alert %s not written: no active admin", kind)
        return
    fresh = [
        admin
        for admin in admins
        if _write(
            session,
            admin.id,
            kind=kind,
            severity="warning",
            title=title,
            body=body,
            dedupe_key=dedupe_key,
            meeting_id=None,
        )
    ]
    if not fresh:
        return
    try:
        _mail_admins(session, fresh, title, body)
    except Exception as exc:
        logger.warning("admin alert mail not queued (%s)", type(exc).__name__)


def _mail_admins(session: Session, fresh: list[User], title: str, body: str) -> None:
    address = get_settings().admin_alert_email.strip().lower()
    if address:
        if valid_address(address) and _mail_wanted(session, address):
            _queue_mail(session, address, title, body, ALERT_TEMPLATE)
        elif not valid_address(address):
            logger.warning("ADMIN_ALERT_EMAIL is not a single plain address: alert not mailed")
        return
    for admin in fresh:
        if _wants_email(session, admin):
            _queue_mail(session, admin.email, title, body, ALERT_TEMPLATE)
