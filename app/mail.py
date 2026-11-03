"""Email: a `Mailer` per provider, and an outbox that sends with retries.

An action that needs to send mail (sign-up, password reset, a waitlist invite) only inserts a
`mail_outbox` row in its own transaction (`enqueue`): a mail provider that is down can never fail
the action. The worker sends pending rows (`deliver_pending`) and tidies up (`sweep_outbox`).

A row holds the link in its `payload` while the mail is pending, because the link carries a
token. The payload is replaced by `{}` the moment the mail is sent or has failed for good, and
any row that is still unsent 24 hours after it was queued is failed and scrubbed too: by then
every token in it has expired (the longest lives 24 hours; an invite lives 7 days, but its link
is shown to the admin as well and can be issued again).

Errors are recorded by kind only (`HTTP 500`, an exception's class name): a provider's reply or
an exception message can hold the address, the link or the API key.
"""

import html
import logging
import re
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import httpx
from sqlalchemy import delete, select, update
from sqlalchemy.orm import Session

from app.config import Settings
from app.models import MailOutbox

logger = logging.getLogger(__name__)

PROVIDERS = ("", "resend", "console")
RESEND_URL = "https://api.resend.com/emails"
SEND_TIMEOUT_SECONDS = 15.0
# A mail is tried at once and then again 1, 5 and 30 minutes after the try before: that is, at
# these ages. Counted from creation, so a worker that was down catches up with no extra state.
RETRY_AGES = (timedelta(0), timedelta(minutes=1), timedelta(minutes=6), timedelta(minutes=36))
MAX_ATTEMPTS = len(RETRY_AGES)
SCRUB_AFTER = timedelta(hours=24)
KEEP_SENT_FOR = timedelta(days=30)
BATCH = 25  # rows one pass sends, so a backlog cannot hold the worker for long


@dataclass(frozen=True)
class Message:
    to: str
    subject: str
    text: str
    html: str


class MailError(Exception):
    """A send that failed. `permanent` is True when trying again cannot help (a 4xx other than
    408 and 429: a bad address, a bad key). The text never holds provider detail."""

    def __init__(self, text: str, *, permanent: bool) -> None:
        super().__init__(text)
        self.permanent = permanent


class Mailer:
    def send(self, message: Message, key: str) -> None:
        """Send `message`. `key` is stable per outbox row, so a provider that supports it can
        drop the duplicate when the worker crashes after sending and before recording it."""
        raise NotImplementedError


class ConsoleMailer(Mailer):
    """Prints the mail, links included. Development only: `build_mailer` refuses it in
    production, because a printed link is a login."""

    def send(self, message: Message, key: str) -> None:
        print(f"--- mail to {message.to}: {message.subject}\n{message.text}\n---")  # noqa: T201


class ResendMailer(Mailer):
    def __init__(self, settings: Settings, transport: httpx.BaseTransport | None = None) -> None:
        self._key = settings.resend_api_key
        self._from = settings.mail_from
        self._client = httpx.Client(timeout=SEND_TIMEOUT_SECONDS, transport=transport)

    def send(self, message: Message, key: str) -> None:
        try:
            response = self._client.post(
                RESEND_URL,
                headers={"Authorization": f"Bearer {self._key}", "Idempotency-Key": key},
                json={
                    "from": self._from,
                    "to": [message.to],
                    "subject": message.subject,
                    "text": message.text,
                    "html": message.html,
                },
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


def build_mailer(settings: Settings) -> Mailer | None:
    """The mailer for these settings, or None when production has no provider configured."""
    check_settings(settings)
    provider = settings.mail_provider or ("resend" if settings.resend_api_key else "")
    if provider == "resend":
        return ResendMailer(settings)
    if provider == "console" or settings.app_env != "production":
        return ConsoleMailer()
    return None


# ---- what each mail says ----

SIGNATURE = "Beyric Note Taker, a product of Beyrichq LTD"


def _lines(*parts: str | None) -> str:
    return "\n\n".join(part for part in parts if part)


# A name goes into the greeting only when it is plainly a name: letters, then letters, spaces,
# apostrophes and hyphens, at most 60. The waitlist form takes a name from anyone, and mail goes
# to whatever address they typed, so a name is never allowed to carry a line break, a link or
# any text of its own into an email from us.
PLAIN_NAME = re.compile(r"[^\W\d_](?:[^\W\d_]|[ '\u2019-]){0,59}")


def _hello(payload: dict) -> str:
    name = payload.get("name")
    if isinstance(name, str) and PLAIN_NAME.fullmatch(name.strip()):
        return f"Hello {name.strip()},"
    return "Hello,"


def _verify(payload: dict) -> tuple[str, str, str]:
    return (
        "Confirm your email for Beyric Note Taker",
        _lines(
            _hello(payload),
            "Open this link to confirm your email address and finish creating your account. "
            "It works once and expires in 24 hours:",
            payload["link"],
            "If you did not create an account, ignore this email.",
        ),
        "Confirm your email",
    )


def _reset(payload: dict) -> tuple[str, str, str]:
    return (
        "Reset your Beyric Note Taker password",
        _lines(
            _hello(payload),
            "Open this link to choose a new password. It works once and expires in 30 minutes:",
            payload["link"],
            "If you did not ask for this, ignore this email: your password has not changed.",
        ),
        "Choose a new password",
    )


def _already(payload: dict) -> tuple[str, str, str]:
    return (
        "You already have a Beyric Note Taker account",
        _lines(
            _hello(payload),
            "Someone, probably you, tried to create an account with this email address, but "
            "it already has one. Sign in here, or use Forgot password on the sign-in page if "
            "you do not remember it:",
            payload["link"],
            "If this was not you, you can ignore this email.",
        ),
        "Sign in",
    )


def _invite(payload: dict) -> tuple[str, str, str]:
    return (
        "A place is waiting for you on Beyric Note Taker",
        _lines(
            _hello(payload),
            "A place has opened up in the Beyric Note Taker pilot and it is reserved for this "
            "email address for 7 days. Use this link to create your account:",
            payload["link"],
        ),
        "Create your account",
    )


TEMPLATES: dict[str, Callable[[dict], tuple[str, str, str]]] = {
    "verify_email": _verify,
    "reset_password": _reset,
    "already_registered": _already,
    "waitlist_invite": _invite,
}


def render(template: str, payload: dict, to: str, settings: Settings) -> Message:
    """The mail for a template and payload. Raises KeyError for an unknown template or a
    payload without its link."""
    subject, text, button = TEMPLATES[template](payload)
    link = html.escape(payload["link"], quote=True)
    body = "".join(
        f"<p>{html.escape(part)}</p>" for part in text.split("\n\n") if part != payload["link"]
    )
    page = (
        f'<div style="font-family:system-ui,sans-serif;max-width:520px">{body}'
        f'<p><a href="{link}" style="display:inline-block;padding:10px 18px;background:#1c1c1c;'
        f'color:#fff;text-decoration:none;border-radius:6px">{html.escape(button)}</a></p>'
        f'<p style="color:#666;font-size:13px">{html.escape(SIGNATURE)}</p></div>'
    )
    return Message(to=to, subject=subject, text=f"{text}\n\n{SIGNATURE}", html=page)


# ---- the outbox ----


def enqueue(session: Session, to: str, template: str, payload: dict) -> MailOutbox:
    """Queue a mail in the caller's transaction (no commit: it stands or falls with the action
    that asked for it)."""
    row = MailOutbox(to_email=to.strip().lower(), template=template, payload=payload)
    session.add(row)
    session.flush()
    return row


def _due(row: MailOutbox, now: datetime) -> bool:
    return row.attempts < MAX_ATTEMPTS and now >= row.created_at + RETRY_AGES[row.attempts]


def _finish(row: MailOutbox, status: str, error: str | None) -> None:
    row.status, row.payload, row.last_error = status, {}, error


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
        try:
            message = render(row.template, row.payload, row.to_email, settings)
        except KeyError:
            _finish(row, "failed", "unknown template")
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
            session.commit()
            continue
        row.last_error = error
        if permanent or row.attempts >= MAX_ATTEMPTS:
            _finish(row, "failed", error)
        logger.warning("mail %s: send failed (%s), attempt %s", row.id, error, row.attempts)
        session.commit()
    return sent


def sweep_outbox(session: Session, now: datetime) -> None:
    """Scrub links out of mail that will never go, fail unsent mail older than a day, and delete
    sent rows after 30 days."""
    cutoff = now - SCRUB_AFTER
    session.execute(
        update(MailOutbox)
        .where(MailOutbox.status == "pending", MailOutbox.created_at < cutoff)
        .values(status="failed", last_error="expired", payload={})
    )
    session.execute(
        update(MailOutbox)
        .where(MailOutbox.status == "failed", MailOutbox.created_at < cutoff)
        .values(payload={})
    )
    session.execute(
        delete(MailOutbox).where(
            MailOutbox.status == "sent", MailOutbox.created_at < now - KEEP_SENT_FOR
        )
    )
    session.commit()


def utcnow() -> datetime:
    return datetime.now(UTC)
