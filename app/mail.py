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
import time
from datetime import UTC, datetime, timedelta

import httpx
from sqlalchemy import case, delete, func, select, text, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app import mail_templates
from app.config import Settings
from app.edge import Throttle
from app.mail_message import Attachment, MailError, Message, valid_address
from app.mail_templates import LINK_TEMPLATES, NOTES_TEMPLATES, SIGNATURE, Skip
from app.models import MailOutbox, MailSuppression, Meeting, Notification, User
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
HOUR = timedelta(hours=1)
BUDGET_LOCK = (7002, 1)  # advisory lock (two-int form): the hourly budget's count and the send
ALERT_TEMPLATE = "alert"  # an admin alert (`app/alerts.py`): never held by the budget
# Mail the budget's reserve is for. Everything else (the verification and "already registered"
# mail anyone can make the site send by filling in the sign-up form, and notes mail) may not use
# it, so a flood of sign-ups cannot keep a real person from a reset link or an invitation.
PROTECTED_TEMPLATES = frozenset({"reset_password", "waitlist_invite"})
OVER_BUDGET = "over_budget"  # the reason a held mail carries (`last_error`)
budget_log_throttle = Throttle(600.0)  # the held-mail log line, once in ten minutes

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
    """Never mail `email` again (in the caller's transaction, no commit). Only the address's hash
    is kept. An existing entry stays as it is, except that an `unsubscribed` one becomes a bounce
    or complaint (which also stops sign-in mail), never the other way round."""
    statement = insert(MailSuppression).values(email_hash=email_hash(email), reason=reason)
    session.execute(
        statement.on_conflict_do_update(
            index_elements=["email_hash"],
            set_={"reason": statement.excluded.reason},
            where=(MailSuppression.reason == "unsubscribed")
            & (statement.excluded.reason != "unsubscribed"),
        )
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
    transaction. At most one is ever queued for a meeting: a notes row keeps its ids-only payload
    when it is done, and a unique index on (template, meeting id) stops two transactions that
    race (the loser gets None). Not queued for an owner who is not active or has switched the mail
    off (the sender checks that again at send time, so this only saves rows)."""
    owner = session.get(User, meeting.owner_user_id)
    if owner is None or owner.status != "active":
        return None
    if not mail_templates.notes_ready_wanted(session, owner):
        return None
    try:
        with session.begin_nested():
            return enqueue(session, owner.email, "notes_ready", {"meeting_id": meeting.id})
    except IntegrityError:
        return None


def queue_tasks_assigned(session: Session, meeting: Meeting, user_ids: list[int]) -> int:
    """Queue one "New tasks assigned" mail for each of these people about this meeting, in the
    caller's transaction. A unique index on (meeting, person) is what makes it once: asking again
    for the same pair (a later pass, a rewrite of the notes) queues nothing. Only active people,
    and not the meeting's owner; the sender checks all of it again, and the owner's "Email
    assignees" and the person's own preference, when it sends. Returns how many were queued."""
    queued = 0
    for user_id in user_ids:
        person = session.get(User, user_id)
        if person is None or person.status != "active" or person.id == meeting.owner_user_id:
            continue
        try:
            with session.begin_nested():
                enqueue(
                    session,
                    person.email,
                    "tasks_assigned",
                    {"meeting_id": meeting.id, "user_id": person.id},
                )
            queued += 1
        except IntegrityError:
            continue
    return queued


def _due(row: MailOutbox, now: datetime) -> bool:
    return row.attempts < MAX_ATTEMPTS and now >= row.created_at + RETRY_AGES[row.attempts]


def _finish(row: MailOutbox, status: str, error: str | None) -> None:
    """Close a row. A link row loses its payload (it holds a raw token); a notes row keeps its
    ids, which hold no secret and are what stops a second mail for the same meeting."""
    row.status, row.last_error = status, error
    if row.template not in NOTES_TEMPLATES:
        row.payload = {}
    elif row.template == "notes_shared":
        from app import shares  # shares imports this module

        shares.record_outcome(row)


def _tell_owner_of_cap_skip(session: Session, row: MailOutbox, cap: int) -> None:
    """A notes mail over the per-recipient cap is lost for good, so the owner of its meeting is
    told in the app (a warning notification, one per meeting). Never raises: telling someone must
    not stop the queue, and a notice that cannot be written is only logged. The text names no
    address (a later template may mail someone other than the owner)."""
    meeting_id = (row.payload or {}).get("meeting_id")
    if type(meeting_id) is not int:
        return
    noun = "email" if cap == 1 else "emails"
    try:
        with session.begin_nested():
            meeting = session.get(Meeting, meeting_id)
            if meeting is None:
                return
            session.execute(
                insert(Notification)
                .values(
                    user_id=meeting.owner_user_id,
                    kind="mail.recipient_cap",
                    severity="warning",
                    title="A notes email was not sent",
                    body=(
                        f"The notes email for {mail_templates.meeting_name(meeting)} was not sent: "
                        f"that address has already had {cap} notes {noun} in the last 24 hours, "
                        "which is the daily limit. Your notes are in the app, at "
                        f"/meetings/{meeting.id}."
                    ),
                    meeting_id=meeting.id,
                    dedupe_key=f"mail.recipient_cap:{meeting.id}",
                )
                .on_conflict_do_nothing(index_elements=["user_id", "dedupe_key"])
            )
    except Exception as exc:
        logger.warning(
            "mail %s: could not tell the owner about the cap (%s)", row.id, type(exc).__name__
        )


def _sent_since(
    session: Session, since: datetime, to: str | None = None, shared: bool = False
) -> int:
    query = (
        select(func.count())
        .select_from(MailOutbox)
        .where(MailOutbox.status == "sent", MailOutbox.sent_at >= since)
    )
    if to is not None:
        query = query.where(
            MailOutbox.to_email == to,
            MailOutbox.template.in_(list(NOTES_TEMPLATES)),
            # Mail a colleague chose to send does not use up the address's own notes mail.
            (MailOutbox.template == "notes_shared")
            if shared
            else (MailOutbox.template != "notes_shared"),
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


def deliver_pending(
    session: Session,
    mailer: Mailer,
    settings: Settings,
    now: datetime,
    *,
    budget_seconds: float | None = None,
) -> int:
    """Send every pending mail that is due; returns how many went. Each row is handled and
    committed on its own, under a row lock another worker skips, so one failure never holds up
    or undoes the rest. `budget_seconds` bounds the pass: once that long (real time) has gone, no
    further mail is started (the rest wait for the next pass), so a provider that hangs on every
    call costs one pass a bounded time."""
    started = time.monotonic()
    # Resend counts the day in UTC and counts every mail, account mail included.
    day_start = now.astimezone(UTC).replace(hour=0, minute=0, second=0, microsecond=0)
    sent_today = _sent_since(session, day_start) if settings.mail_daily_cap > 0 else 0
    capped = 0 < settings.mail_daily_cap <= sent_today
    notes = list(NOTES_TEMPLATES)

    def pending_ids(*conditions, first=()) -> list[tuple[int, str]]:
        return [
            (row[0], row[1])
            for row in session.execute(
                select(MailOutbox.id, MailOutbox.template)
                .where(MailOutbox.status == "pending", *conditions)
                .order_by(*first, MailOutbox.id)
                .limit(BATCH * 4)
            )
        ]

    # Sign-in mail first and on its own, so notes mail held for the day's quota (pending for up to
    # a day) can never fill the batch and starve a verification or reset mail. Once the quota is
    # spent notes mail is not even loaded.
    # Three loads, so a backlog of one kind can never hide another: an admin alert or a protected
    # mail (a reset link, an invitation) is loaded on its own, before the verification and
    # "already registered" mail a flood can pile up past a whole batch, which is held and stays
    # pending; of that mail, rows never held come first, so a real person's new verification mail
    # is not stuck behind the held backlog.
    privileged = [ALERT_TEMPLATE, *PROTECTED_TEMPLATES]
    ids = pending_ids(
        MailOutbox.template.in_(privileged),
        first=(
            case((MailOutbox.template == ALERT_TEMPLATE, 0), else_=1),
            case((MailOutbox.last_error == OVER_BUDGET, 1), else_=0),
        ),
    )
    ids += pending_ids(
        MailOutbox.template.not_in(notes), MailOutbox.template.not_in(privileged),
        first=(case((MailOutbox.last_error == OVER_BUDGET, 1), else_=0),),
    )  # fmt: skip
    if not capped:
        ids += pending_ids(MailOutbox.template.in_(notes))
    session.rollback()
    sent = 0
    held = {"account": 0, "bulk": 0}
    in_hour = 0
    # Once a mail has been held, the count of the hour cannot fall within this pass, and a later
    # verification or notes mail has no more room than it had, so each is held without asking the
    # database (and
    # marked in one statement at the end): a flood's backlog costs a pass a few queries, not a
    # lock and a count per row.
    budget_full = False
    skipped: list[int] = []
    for mail_id, template in ids:
        if budget_full and template not in privileged:
            held["bulk" if template in NOTES_TEMPLATES else "account"] += 1
            skipped.append(mail_id)
            continue
        row = session.scalars(
            select(MailOutbox)
            .where(MailOutbox.id == mail_id, MailOutbox.status == "pending")
            .with_for_update(skip_locked=True)
            .execution_options(populate_existing=True)
        ).first()
        if row is None or not _due(row, now):
            session.rollback()
            continue
        if sent >= BATCH or (
            budget_seconds is not None and time.monotonic() - started > budget_seconds
        ):
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
        if notes_mail and 0 < settings.mail_daily_cap <= sent_today:
            session.rollback()  # still pending, no attempt used: tomorrow's quota will send it
            continue
        if settings.mail_hourly_budget > 0 and not _is_admin_alert(session, row, settings):
            # One count and one send at a time across every worker (the lock lasts to the commit),
            # so two workers cannot each see room and both send past the budget.
            session.execute(
                text("select pg_advisory_xact_lock(:c, :k)"),
                {"c": BUDGET_LOCK[0], "k": BUDGET_LOCK[1]},
            )
            in_hour = _sent_since(session, now - HOUR)
            reserve = 0 if row.template in PROTECTED_TEMPLATES else settings.mail_hourly_reserve
            room = settings.mail_hourly_budget - reserve
            if in_hour >= max(room, 0):
                held["bulk" if notes_mail else "account"] += 1
                budget_full = True  # every later non-privileged mail has the same room or less
                if row.last_error != OVER_BUDGET:
                    row.last_error = OVER_BUDGET  # no attempt used: it goes when the hour rolls on
                    session.commit()
                else:
                    session.rollback()
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
        # After the template's own checks (opted out, owner inactive, meeting gone, ...): their
        # reason is the true one, and the owner is told about the cap only for a mail that would
        # otherwise have gone.
        if notes_mail and settings.mail_recipient_daily_cap > 0:
            recent = _sent_since(
                session, now - timedelta(days=1), row.to_email, row.template == "notes_shared"
            )
            if recent >= settings.mail_recipient_daily_cap:
                _tell_owner_of_cap_skip(session, row, settings.mail_recipient_daily_cap)
                _finish(row, "skipped", "recipient_cap")
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
    if skipped:
        session.execute(
            update(MailOutbox)
            .where(
                MailOutbox.id.in_(skipped),
                MailOutbox.status == "pending",
                MailOutbox.last_error.is_distinct_from(OVER_BUDGET),
            )
            .values(last_error=OVER_BUDGET)
        )
        session.commit()
    if held["account"] or held["bulk"]:
        _report_budget(session, settings, now, held, in_hour)
    return sent


unlimited_warned = Throttle(86400.0)


def warn_if_unlimited(settings: Settings) -> None:
    """Say so at start when there is no hourly mail budget: 0 (or less) is unlimited, it does not
    stop mail, and a flood of sign-ups could then spend all of Resend's quota. At most once a
    day."""
    if settings.mail_hourly_budget <= 0 and unlimited_warned.ready():
        logger.warning(
            "MAIL_HOURLY_BUDGET is %s: the hourly mail budget is OFF (unlimited). 0 or less does "
            "not stop mail; to stop mail unset RESEND_API_KEY.",
            settings.mail_hourly_budget,
        )


def _is_admin_alert(session: Session, row: MailOutbox, settings: Settings) -> bool:
    """An alert to an admin (a user who is one, or the `ADMIN_ALERT_EMAIL` mailbox): the only mail
    the budget never holds, so the admins can still be told. An alert to anyone else is held like
    other mail."""
    if row.template != ALERT_TEMPLATE:
        return False
    if row.to_email == settings.admin_alert_email.strip().lower() != "":
        return True
    return (
        session.scalar(
            select(User.id).where(
                User.email == row.to_email, User.is_admin.is_(True), User.status == "active"
            )
        )
        is not None
    )


def _report_budget(
    session: Session, settings: Settings, now: datetime, held: dict[str, int], in_hour: int
) -> None:
    """Mail is waiting for the hourly budget: log it (by counts only, never an address) and tell
    the admins, who get one notice per clock hour (`alerts.notify_admins` dedupes by key). Never
    raises: it must not stop the queue."""
    if budget_log_throttle.ready():
        logger.warning(
            "hourly mail budget used (%s sent in the last hour, limit %s): %s sign-in or account "
            "mails and %s other mails are waiting",
            in_hour,
            settings.mail_hourly_budget,
            held["account"],
            held["bulk"],
        )
    try:
        from app import alerts  # alerts imports this module

        alerts.notify_admins(
            session,
            kind="admin.mail_budget",
            title="Outgoing mail is waiting: the hourly budget is used",
            body=(
                f"{held['account']} sign-in or account emails and {held['bulk']} other emails are "
                f"waiting because {in_hour} emails went out in the last hour (the limit, "
                f"MAIL_HOURLY_BUDGET, is {settings.mail_hourly_budget}). They go as the hour rolls "
                "on, and a sign-in link that waits more than an hour is dropped. If this is not "
                "expected, someone may be flooding the sign-up form."
            ),
            dedupe_key=alerts.bucket_key("admin.mail_budget", now, 60),
        )
        session.commit()
    except Exception as exc:
        session.rollback()
        logger.warning("could not tell the admins about the mail budget (%s)", type(exc).__name__)


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
        .values(
            status="failed",
            last_error=case((MailOutbox.last_error == OVER_BUDGET, OVER_BUDGET), else_="expired"),
            payload={},
        )
    )
    session.execute(
        update(MailOutbox)
        .where(
            MailOutbox.status == "pending",
            MailOutbox.template.in_(notes),
            MailOutbox.created_at < now - HOLD_NOTES_MAIL_FOR,
        )
        .values(
            status="failed",
            last_error=case((MailOutbox.last_error == OVER_BUDGET, OVER_BUDGET), else_="expired"),
        )
    )
    session.execute(
        update(MailOutbox)
        .where(
            MailOutbox.status == "failed",
            MailOutbox.template.not_in(notes),
            MailOutbox.created_at < now - SCRUB_AFTER,
        )
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
