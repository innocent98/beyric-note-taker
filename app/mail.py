"""Email: a `Mailer` per provider, and an outbox that sends with retries.

An action that needs to send mail (sign-up, password reset, a waitlist invite, notes ready) only
inserts a `mail_outbox` row in its own transaction (`enqueue`): a mail provider that is down can
never fail the action. The worker sends pending rows (`deliver_pending`) and tidies up
(`sweep_outbox`). What each mail says is in `app/mail_templates.py`, its layout in
`app/mail_layout.py`, the message and its checks in `app/mail_message.py`.

Two kinds of row (`app/mail_templates.py`). A **link** row holds the link in its `payload` while
the mail is pending, because the link carries the RAW token (`auth_tokens` keeps only its hash):
that is the one place a usable token is stored. The payload is replaced by `{}` the moment the
mail is sent or has failed for good (normally within seconds), and any row still unsent an hour
after it was queued is failed and scrubbed too (by the hourly sweep, so a row can wait up to
about two hours), so a backup taken in between can hold a live link for that long at most. A
**notes** row holds ids only: the notes are read when the mail is sent, so no meeting text is ever
in the outbox. When no mail provider is configured (`is_configured`) callers do not make a token
or a row at all.

What the sender decides, at SEND time and not when the mail was queued:
- an address that is not one plain address fails the row;
- a suppressed address (`mail_suppressions`: a bounce or complaint stops all mail, an unsubscribe
  stops everything except link/account mail) skips it;
- a notes mail goes only while the meeting exists, is ready, and its owner is active, and only
  while the template's own guards pass (a revoked share, a switched-off setting); else it is
  `skipped` with a reason slug;
- notes mail is capped per recipient per day, and held back (still pending, no attempt used) once
  the day's quota is spent; link mail is never held back, it is the sign-in path.
`skipped` is not a failure: nothing alerts on it.

Errors are recorded by kind only (`HTTP 500`, an exception's class name): a provider's reply or
an exception message can hold the address, the link or the API key.
"""

import base64
import logging
from datetime import UTC, datetime, timedelta

import httpx
from sqlalchemy import delete, func, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app import mail_templates
from app.config import Settings
from app.mail_message import Attachment, MailError, Message, valid_address
from app.mail_templates import LINK_TEMPLATES, NOTES_TEMPLATES, SIGNATURE, Skip
from app.models import MailOutbox, MailSuppression, Meeting, User
from app.usage import email_hash

__all__ = ["Attachment", "MailError", "Message", "SIGNATURE"]

logger = logging.getLogger(__name__)

PROVIDERS = ("", "resend", "console")
RESEND_URL = "https://api.resend.com/emails"
SEND_TIMEOUT_SECONDS = 15.0
# A mail is tried at once and then again 1, 5 and 30 minutes after the try before: that is, at
# these ages. Counted from creation, so a worker that was down catches up with no extra state.
RETRY_AGES = (timedelta(0), timedelta(minutes=1), timedelta(minutes=6), timedelta(minutes=36))
MAX_ATTEMPTS = len(RETRY_AGES)
SCRUB_AFTER = timedelta(hours=1)
# A notes mail holds no secret, so it may wait for the next day's quota (see `deliver_pending`);
# after this it is failed as expired, because the notes it would show have moved on.
HOLD_NOTES_MAIL_FOR = timedelta(hours=24)
KEEP_SENT_FOR = timedelta(days=30)
BATCH = 25  # rows one pass sends, so a backlog cannot hold the worker for long

# The link templates (sign-up, reset, waitlist invite) under their names, for callers that ask
# which templates exist.
TEMPLATES = LINK_TEMPLATES


class Mailer:
    def send(self, message: Message, key: str) -> None:
        """Send `message`. `key` is stable per outbox row, so a provider that supports it can
        drop the duplicate when the worker crashes after sending and before recording it."""
        raise NotImplementedError


class ConsoleMailer(Mailer):
    """Prints the mail, links included. Development only: `build_mailer` refuses it in
    production, because a printed link is a login."""

    def send(self, message: Message, key: str) -> None:
        files = "".join(
            f"\n[attached {a.filename}, {len(a.content)} bytes]" for a in message.attachments
        )
        print(f"--- mail to {message.to}: {message.subject}\n{message.text}{files}\n---")  # noqa: T201


class ResendMailer(Mailer):
    def __init__(self, settings: Settings, transport: httpx.BaseTransport | None = None) -> None:
        self._key = settings.resend_api_key
        self._from = settings.mail_from
        self._client = httpx.Client(timeout=SEND_TIMEOUT_SECONDS, transport=transport)

    def send(self, message: Message, key: str) -> None:
        body: dict = {
            "from": self._from,
            "to": [message.to],
            "subject": message.subject,
            "text": message.text,
            "html": message.html,
        }
        if message.reply_to:
            body["reply_to"] = message.reply_to
        if message.headers:
            body["headers"] = dict(message.headers)
        if message.attachments:
            body["attachments"] = [
                {
                    "filename": a.filename,
                    "content": base64.b64encode(a.content).decode("ascii"),
                    "content_type": a.content_type,
                }
                for a in message.attachments
            ]
        try:
            response = self._client.post(
                RESEND_URL,
                headers={"Authorization": f"Bearer {self._key}", "Idempotency-Key": key},
                json=body,
            )
        except httpx.HTTPError as exc:
            raise MailError(type(exc).__name__, permanent=False) from None
        if response.status_code >= 300:
            status = response.status_code
            permanent = 400 <= status < 500 and status not in (408, 429)
            raise MailError(f"HTTP {status}", permanent=permanent)


def check_settings(settings: Settings) -> None:
    """Fail at start on a mail setting that is wrong, not on the first sign-up."""
    if settings.mail_provider not in PROVIDERS:
        raise RuntimeError(f"mail_provider must be one of {PROVIDERS[1:]} or empty")
    if settings.mail_provider == "console" and settings.app_env == "production":
        raise RuntimeError("mail_provider console is refused in production: it prints links")
    if settings.mail_provider == "resend" and not settings.resend_api_key:
        raise RuntimeError("mail_provider resend needs RESEND_API_KEY")


def _provider(settings: Settings) -> str | None:
    """ "resend", "console", or None when production has no provider configured."""
    check_settings(settings)
    provider = settings.mail_provider or ("resend" if settings.resend_api_key else "")
    if provider == "resend":
        return "resend"
    if provider == "console" or settings.app_env != "production":
        return "console"
    return None


def is_configured(settings: Settings) -> bool:
    """Whether mail can be sent at all. Asking builds nothing (no HTTP client)."""
    return _provider(settings) is not None


def build_mailer(settings: Settings) -> Mailer | None:
    """The mailer for these settings, or None when production has no provider configured."""
    provider = _provider(settings)
    if provider == "resend":
        return ResendMailer(settings)
    return ConsoleMailer() if provider == "console" else None


def render(template: str, payload: dict, to: str, settings: Settings) -> Message:
    """The mail for a link template and payload. Raises KeyError for an unknown template or a
    payload without its link."""
    return mail_templates.render_link(template, payload, to, settings)


# ---- suppression ----

HARD_REASONS = ("bounce", "complaint")


def suppress(session: Session, email: str, reason: str) -> None:
    """Never mail `email` again (in the caller's transaction, no commit). Idempotent: the first
    reason stays. Only the address's hash is kept."""
    session.execute(
        insert(MailSuppression)
        .values(email_hash=email_hash(email), reason=reason)
        .on_conflict_do_nothing(index_elements=["email_hash"])
    )


def suppression_reason(session: Session, email: str) -> str | None:
    return session.scalar(
        select(MailSuppression.reason).where(MailSuppression.email_hash == email_hash(email))
    )


def lift_suppression(session: Session, email: str) -> bool:
    """Allow mail to `email` again (an operator fixing a wrong entry). True if there was one."""
    result = session.execute(
        delete(MailSuppression).where(MailSuppression.email_hash == email_hash(email))
    )
    return result.rowcount > 0


def _is_suppressed(session: Session, row: MailOutbox) -> bool:
    reason = suppression_reason(session, row.to_email)
    if reason is None:
        return False
    # An unsubscribe is about notes and reminders: the mail that lets a person into their own
    # account (verify, reset) is not stopped by it. A bounce or complaint stops everything.
    return reason in HARD_REASONS or row.template not in LINK_TEMPLATES


# ---- the outbox ----


def enqueue(session: Session, to: str, template: str, payload: dict) -> MailOutbox:
    """Queue a mail in the caller's transaction (no commit: it stands or falls with the action
    that asked for it). A notes template's payload must be ids only: ValueError otherwise."""
    if template in NOTES_TEMPLATES:
        mail_templates.check_id_payload(payload)
    row = MailOutbox(to_email=to.strip().lower(), template=template, payload=payload)
    session.add(row)
    session.flush()
    return row


def queue_notes_ready(session: Session, meeting: Meeting) -> MailOutbox | None:
    """Queue the notes-ready mail for a meeting that has just become ready, in the caller's
    transaction. Not queued for an owner who is not active or has switched the mail off, nor
    while one is already queued for the meeting (the sender checks all of it again at send time,
    so this only saves rows)."""
    owner = session.get(User, meeting.owner_user_id)
    if owner is None or owner.status != "active":
        return None
    if not mail_templates.notes_ready_wanted(session, owner):
        return None
    queued = session.scalar(
        select(MailOutbox.id).where(
            MailOutbox.template == "notes_ready",
            MailOutbox.payload["meeting_id"].as_integer() == meeting.id,
        )
    )
    if queued is not None:
        return None
    return enqueue(session, owner.email, "notes_ready", {"meeting_id": meeting.id})


def _due(row: MailOutbox, now: datetime) -> bool:
    return row.attempts < MAX_ATTEMPTS and now >= row.created_at + RETRY_AGES[row.attempts]


def _finish(row: MailOutbox, status: str, error: str | None) -> None:
    row.status, row.payload, row.last_error = status, {}, error


def _sent_since(session: Session, since: datetime, to: str | None = None) -> int:
    query = (
        select(func.count())
        .select_from(MailOutbox)
        .where(MailOutbox.status == "sent", MailOutbox.sent_at >= since)
    )
    if to is not None:
        query = query.where(
            MailOutbox.to_email == to, MailOutbox.template.in_(list(NOTES_TEMPLATES))
        )
    return session.scalar(query)


def _record_build_failure(session: Session, mail_id: int, error: str) -> None:
    """A template builder raised something unexpected. Count it as an attempt (so the row stops
    after the last retry instead of failing every pass for a day) and keep the error's type only.
    The session may be unusable after a database error, so it starts over from the row."""
    session.rollback()
    row = session.get(MailOutbox, mail_id, with_for_update=True, populate_existing=True)
    if row is not None and row.status == "pending":
        row.attempts += 1
        row.last_error = error
        if row.attempts >= MAX_ATTEMPTS:
            _finish(row, "failed", error)
        logger.warning("mail %s: could not build (%s), attempt %s", mail_id, error, row.attempts)
    session.commit()


def deliver_pending(session: Session, mailer: Mailer, settings: Settings, now: datetime) -> int:
    """Send every pending mail that is due; returns how many went. Each row is handled and
    committed on its own, under a row lock another worker skips, so one failure never holds up
    or undoes the rest."""
    ids = list(
        session.scalars(
            select(MailOutbox.id)
            .where(MailOutbox.status == "pending")
            .order_by(MailOutbox.id)
            .limit(BATCH * 4)
        )
    )
    # Resend counts the day in UTC and counts every mail, account mail included.
    day_start = now.astimezone(UTC).replace(hour=0, minute=0, second=0, microsecond=0)
    sent_today = _sent_since(session, day_start) if settings.mail_daily_cap > 0 else 0
    session.rollback()
    sent = 0
    for mail_id in ids:
        row = session.scalars(
            select(MailOutbox)
            .where(MailOutbox.id == mail_id, MailOutbox.status == "pending")
            .with_for_update(skip_locked=True)
            .execution_options(populate_existing=True)
        ).first()
        if row is None or not _due(row, now):
            session.rollback()
            continue
        if sent >= BATCH:
            session.rollback()
            break
        notes_mail = row.template in NOTES_TEMPLATES
        if not valid_address(row.to_email):
            _finish(row, "failed", "invalid address")
            session.commit()
            continue
        if _is_suppressed(session, row):
            _finish(row, "skipped", "suppressed")
            session.commit()
            continue
        if notes_mail and settings.mail_recipient_daily_cap > 0:
            recent = _sent_since(session, now - timedelta(days=1), row.to_email)
            if recent >= settings.mail_recipient_daily_cap:
                _finish(row, "skipped", "recipient_cap")
                session.commit()
                continue
        if notes_mail and 0 < settings.mail_daily_cap <= sent_today:
            session.rollback()  # still pending, no attempt used: tomorrow's quota will send it
            continue
        try:
            message = mail_templates.prepare(
                session, row.template, row.payload, row.to_email, settings, now
            )
        except KeyError:
            _finish(row, "failed", "unknown template")
            session.commit()
            continue
        except ValueError:  # a field that could not be put in a mail safely
            _finish(row, "failed", "invalid message")
            session.commit()
            continue
        except (
            Exception
        ) as exc:  # a builder reads the database: one bad row must not block the rest
            _record_build_failure(session, mail_id, type(exc).__name__)
            continue
        if isinstance(message, Skip):
            _finish(row, "skipped", message.reason)
            session.commit()
            continue
        row.attempts += 1
        try:
            mailer.send(message, f"mail-{row.id}")
        except MailError as exc:
            error, permanent = str(exc), exc.permanent
        except Exception as exc:  # whatever a provider library throws: this row, not the pass
            error, permanent = type(exc).__name__, False
        else:
            _finish(row, "sent", None)
            row.sent_at = now
            sent += 1
            sent_today += 1
            session.commit()
            continue
        row.last_error = error
        if permanent or row.attempts >= MAX_ATTEMPTS:
            _finish(row, "failed", error)
        logger.warning("mail %s: send failed (%s), attempt %s", row.id, error, row.attempts)
        session.commit()
    return sent


def sweep_outbox(session: Session, now: datetime) -> None:
    """Scrub links out of mail that will never go, fail unsent mail past its hold (an hour for a
    link, a day for a notes mail), and delete sent and skipped rows after 30 days."""
    notes = list(NOTES_TEMPLATES)
    session.execute(
        update(MailOutbox)
        .where(
            MailOutbox.status == "pending",
            MailOutbox.template.not_in(notes),
            MailOutbox.created_at < now - SCRUB_AFTER,
        )
        .values(status="failed", last_error="expired", payload={})
    )
    session.execute(
        update(MailOutbox)
        .where(
            MailOutbox.status == "pending",
            MailOutbox.template.in_(notes),
            MailOutbox.created_at < now - HOLD_NOTES_MAIL_FOR,
        )
        .values(status="failed", last_error="expired", payload={})
    )
    session.execute(
        update(MailOutbox)
        .where(MailOutbox.status == "failed", MailOutbox.created_at < now - SCRUB_AFTER)
        .values(payload={})
    )
    session.execute(
        delete(MailOutbox).where(
            MailOutbox.status.in_(("sent", "skipped")),
            MailOutbox.created_at < now - KEEP_SENT_FOR,
        )
    )
    session.commit()


def utcnow() -> datetime:
    return datetime.now(UTC)
