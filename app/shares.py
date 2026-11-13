"""Share notes by email (spec section 10, W6): the requests, their recipients and their limits.

A share is a row of ids and the sender's own words (`MeetingShare`, `MeetingShareRecipient`). The
notes are never copied into it or into `mail_outbox`: each mail is built from the meeting when it
is SENT (`app/mail_templates.py`, template `notes_shared`), after the sender, the meeting and the
share have been checked again.

Flow: `create_share` checks the sender and the limits, then either queues one mail per recipient
(`queued`) or, for a language not yet translated, asks for the translation first
(`waiting_translation`). The translation worker calls `on_translation_finished` when it is done,
which queues the mails or fails the share; `sweep` does the same for a share the hook missed, and
gives up on one that has waited too long. That is the "one job" of the spec: the sender asks once.

No link goes to anyone outside the sender's team (decision D7): see the template. The opt-out
token in the footer is the only link an outside recipient gets.
"""

import logging
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session, object_session

from app import mail, titles, translation
from app.mail_message import clean_line, valid_address
from app.models import (
    Meeting,
    MeetingShare,
    MeetingShareRecipient,
    MeetingTranslation,
    Notification,
    ShareUsage,
    TeamMember,
    User,
)
from app.services import Refused

logger = logging.getLogger(__name__)

MAX_RECIPIENTS = 10  # per share (D15)
DAILY_RECIPIENTS = 20  # mails a day per account, counted as recipients of shares made (D15)
MAX_MESSAGE_CHARS = 500
WAIT_FOR_TRANSLATION = timedelta(hours=2)  # a share waiting longer than this is failed
PENDING_GIVE_UP = timedelta(
    hours=30
)  # mail is held at most 24h, after up to 3h of translation wait
TEMPLATE = "notes_shared"


@dataclass(frozen=True)
class RecipientIn:
    email: str
    name: str | None = None


def clean_message(text: str | None) -> str:
    """The sender's message as one safe plain-text line of at most 500 characters: web addresses
    removed and bare domains defanged (a mail client would link them). Shown escaped."""
    return titles.mail_line(text or "", MAX_MESSAGE_CHARS)


def _lock_sender(session: Session, user_id: int) -> None:
    """Serialise one account's shares so two at once cannot both pass the daily limit."""
    session.execute(select(func.pg_advisory_xact_lock(0x7368_0000_0000_0000 | user_id)))


def used_today(session: Session, user_id: int, now: datetime) -> int:
    """Recipients the account asked to mail in the last 24 hours, from the `share_usage` ledger
    (not from the shares, which go with their meeting): failed shares count, and deleting a
    meeting gives nothing back."""
    return (
        session.scalar(
            select(func.coalesce(func.sum(ShareUsage.recipients), 0)).where(
                ShareUsage.user_id == user_id, ShareUsage.created_at > now - timedelta(hours=24)
            )
        )
        or 0
    )


def _team_members_by_email(
    session: Session, sender: User, emails: list[str]
) -> dict[str, tuple[int, str | None]]:
    """The active members, among `emails`, of any team the sender belongs to: address -> (user id,
    name). Being linked to an account decides nothing about what they may read: the mail's "Open
    notes" button asks the one rule (`visibility.via_team`) at send time."""
    their_teams = select(TeamMember.team_id).where(TeamMember.user_id == sender.id)
    rows = session.execute(
        select(User.email, User.id, User.name)
        .distinct()
        .join(TeamMember, TeamMember.user_id == User.id)
        .where(
            TeamMember.team_id.in_(their_teams),
            User.status == "active",
            func.lower(User.email).in_(emails),
        )
    )
    return {email.lower(): (user_id, name) for email, user_id, name in rows}


def normalise(recipients: list[RecipientIn]) -> list[RecipientIn]:
    """Lower-cased, de-duplicated, valid recipients (in the order given). Raises `Refused`."""
    seen: dict[str, RecipientIn] = {}
    for item in recipients:
        email = item.email.strip().lower()
        if not valid_address(email):
            raise Refused(422, "invalid_recipient", "One of the email addresses is not valid.")
        name = clean_line(item.name or "", 100) or None
        seen.setdefault(email, RecipientIn(email, name))
    if not seen:
        raise Refused(422, "no_recipients", "Choose at least one recipient.")
    if len(seen) > MAX_RECIPIENTS:
        raise Refused(
            422,
            "too_many_recipients",
            f"You can send to at most {MAX_RECIPIENTS} people at a time.",
        )
    return list(seen.values())


def _fresh_translation(
    session: Session, meeting: Meeting, language: str
) -> MeetingTranslation | None:
    row = session.scalars(
        select(MeetingTranslation).where(
            MeetingTranslation.meeting_id == meeting.id, MeetingTranslation.language == language
        )
    ).first()
    if row is not None and row.status == "ready" and not translation.is_stale(row, meeting):
        return row
    return None


def create_share(
    session: Session,
    sender: User,
    meeting: Meeting,
    recipients: list[RecipientIn],
    *,
    language: str,
    attach: str,
    include_transcript: bool,
    message: str | None,
    now: datetime | None = None,
) -> MeetingShare:
    """Make a share and queue its mails (or its translation). Commits. Raises `Refused`:
    403 `email_not_verified`, 409 `not_ready`, 422 `language_unsupported` / `invalid_recipient` /
    `no_recipients` / `too_many_recipients`, 429 `share_limit` and `translation_limit`."""
    now = now or datetime.now(UTC)
    if sender.email_verified_at is None:
        raise Refused(403, "email_not_verified", "Confirm your email address before sharing notes.")
    if language not in translation.LANGUAGES:
        raise Refused(422, "language_unsupported", translation.unsupported_message())
    people = normalise(recipients)
    if meeting.status != "ready" or not meeting.notes:
        raise Refused(409, "not_ready", "The notes are not ready yet.")
    if used_today(session, sender.id, now) + len(people) > DAILY_RECIPIENTS:
        raise Refused(
            429,
            "share_limit",
            f"You can send notes to {DAILY_RECIPIENTS} people a day. Try again tomorrow.",
        )
    ready = language == "en" or _fresh_translation(session, meeting, language) is not None
    if not ready:
        # May raise 429 `translation_limit` or 409. Commits, which ends the transaction below.
        translation.request_translation(session, sender, meeting, language, now=now)
    _lock_sender(session, sender.id)
    if used_today(session, sender.id, now) + len(people) > DAILY_RECIPIENTS:  # the real check
        session.rollback()
        raise Refused(
            429,
            "share_limit",
            f"You can send notes to {DAILY_RECIPIENTS} people a day. Try again tomorrow.",
        )
    members = _team_members_by_email(session, sender, [p.email for p in people])
    share = MeetingShare(
        meeting_id=meeting.id,
        sender_user_id=sender.id,
        language=language,
        attach=attach,
        include_transcript=include_transcript,
        message=clean_message(message),
        status="waiting_translation",
    )
    session.add(share)
    session.add(ShareUsage(user_id=sender.id, recipients=len(people), created_at=now))
    session.flush()
    for person in people:
        member = members.get(person.email)
        session.add(
            MeetingShareRecipient(
                share_id=share.id,
                email=person.email,
                name=person.name or (member[1] if member else None),
                user_id=member[0] if member else None,
            )
        )
    session.flush()
    if ready or _fresh_translation(session, meeting, language) is not None:
        release(session, share)
    session.commit()
    if share.status == "waiting_translation":
        # The translation may have finished between the re-check above and this commit, when the
        # worker's hook could not see the share yet.
        on_translation_finished(session, meeting.id, language)
        session.refresh(share)
    return share


def release(session: Session, share: MeetingShare) -> None:
    """Queue one mail per recipient (in the caller's transaction)."""
    share.status = "queued"
    for recipient in session.scalars(
        select(MeetingShareRecipient).where(MeetingShareRecipient.share_id == share.id)
    ):
        mail.enqueue(
            session,
            recipient.email,
            TEMPLATE,
            {"share_id": share.id, "recipient_id": recipient.id, "meeting_id": share.meeting_id},
        )


def _fail(session: Session, share: MeetingShare, reason: str) -> None:
    share.status = "failed"
    session.execute(
        update(MeetingShareRecipient)
        .where(
            MeetingShareRecipient.share_id == share.id, MeetingShareRecipient.status == "pending"
        )
        .values(status="failed")
    )
    try:
        with session.begin_nested():
            session.execute(
                insert(Notification)
                .values(
                    user_id=share.sender_user_id,
                    kind="share.failed",
                    severity="warning",
                    title="Your notes were not sent",
                    body=(
                        "The notes you shared were not sent because the translation could not "
                        f"be finished ({reason}). Open the meeting and share them again."
                    ),
                    meeting_id=share.meeting_id,
                    dedupe_key=f"share.failed:{share.id}",
                )
                .on_conflict_do_nothing(index_elements=["user_id", "dedupe_key"])
            )
    except Exception as exc:  # telling someone must not stop the queue
        logger.warning("share %s: could not tell the sender (%s)", share.id, type(exc).__name__)


def _settle_waiting(session: Session, share: MeetingShare, now: datetime) -> None:
    """Decide a share that is waiting for its translation, from the translation's state now."""
    meeting = session.get(Meeting, share.meeting_id)
    if meeting is None:
        return
    if _fresh_translation(session, meeting, share.language) is not None:
        release(session, share)
        return
    row = session.scalars(
        select(MeetingTranslation).where(
            MeetingTranslation.meeting_id == meeting.id,
            MeetingTranslation.language == share.language,
        )
    ).first()
    if row is None or row.status == "failed" or translation.is_stale(row, meeting):
        _fail(session, share, "translation_failed")
    elif share.created_at < now - WAIT_FOR_TRANSLATION:
        _fail(session, share, "translation_timeout")


def on_translation_finished(session: Session, meeting_id: int, language: str) -> int:
    """The translation worker finished (or failed) a translation: queue the mails of the shares
    waiting for it, or fail them. Commits. Returns how many shares it settled."""
    now = datetime.now(UTC)
    waiting = list(
        session.scalars(
            select(MeetingShare)
            .where(
                MeetingShare.meeting_id == meeting_id,
                MeetingShare.language == language,
                MeetingShare.status == "waiting_translation",
            )
            .with_for_update()
        )
    )
    for share in waiting:
        _settle_waiting(session, share, now)
    session.commit()
    return len(waiting)


def sweep(session: Session, now: datetime) -> None:
    """Settle shares the hook missed, and mark recipients whose mail expired unsent as failed."""
    waiting = list(
        session.scalars(
            select(MeetingShare)
            .where(MeetingShare.status == "waiting_translation")
            .with_for_update(skip_locked=True)
        )
    )
    for share in waiting:
        _settle_waiting(session, share, now)
    session.execute(
        update(MeetingShareRecipient)
        .where(
            MeetingShareRecipient.status == "pending",
            MeetingShareRecipient.share_id.in_(
                select(MeetingShare.id).where(
                    MeetingShare.status == "queued",
                    MeetingShare.created_at < now - PENDING_GIVE_UP,
                )
            ),
        )
        .values(status="failed")
    )
    session.commit()


def record_outcome(row: mail.MailOutbox) -> None:
    """A `notes_shared` mail was finished by the sender: tell its recipient row, in the same
    transaction. `skipped` of any kind (a suppressed address included) is `not_sent`: the sender
    is never told why."""
    session = object_session(row)
    recipient_id = (row.payload or {}).get("recipient_id")
    if session is None or type(recipient_id) is not int:
        return
    status = {"sent": "sent", "skipped": "not_sent", "failed": "failed"}.get(row.status)
    if status is None:
        return
    values: dict = {"status": status}
    if status == "sent":
        values["sent_at"] = datetime.now(UTC)
    session.execute(
        update(MeetingShareRecipient)
        .where(MeetingShareRecipient.id == recipient_id)
        .values(**values)
    )


def shares_of(session: Session, meeting: Meeting) -> list[tuple[MeetingShare, list]]:
    """The meeting's shares, newest first, each with its recipients."""
    shares = list(
        session.scalars(
            select(MeetingShare)
            .where(MeetingShare.meeting_id == meeting.id)
            .order_by(MeetingShare.id.desc())
        )
    )
    by_share: dict[int, list[MeetingShareRecipient]] = {s.id: [] for s in shares}
    if shares:
        for recipient in session.scalars(
            select(MeetingShareRecipient)
            .where(MeetingShareRecipient.share_id.in_(by_share))
            .order_by(MeetingShareRecipient.id)
        ):
            by_share[recipient.share_id].append(recipient)
    return [(s, by_share[s.id]) for s in shares]
