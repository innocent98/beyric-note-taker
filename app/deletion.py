"""Deleting an account for good: the one function behind both `scripts/delete_user.py` and the
admin API.

The admin's `DELETE /api/admin/users/{id}` only marks the user `deleted` (they cannot sign in and
the slot is free at once, since `deleted` rows are treated as absent everywhere); the worker then
calls `purge_deleted`, which runs `purge_user` for each. `purge_user` stops the user's running
bots, deletes every recording (here and Vexa's copy), then deletes their meetings, templates,
vocabulary, settings, notifications, tokens, queued mail and waitlist row, and finally the user.

What stays: `usage_entries` rows, with only the seconds and `email_hash` (no personal data), so
signing up again with the same address does not reset the 5 hours.

Vexa trouble raises `VexaError` *before* anything of the user is deleted, so the account stays
marked and the next pass tries again; nothing is half-removed except recordings already gone.
"""

import logging
import shutil

from sqlalchemy import delete, select, update
from sqlalchemy.orm import Session

from app.models import (
    AuthToken,
    MailOutbox,
    Meeting,
    Notification,
    Template,
    UsageEntry,
    User,
    UserSettings,
    VocabularyTerm,
    WaitlistEntry,
)
from app.pipeline import audio_path
from app.vexa import VexaError

logger = logging.getLogger(__name__)


VEXA_FINISHED = ("completed", "failed")


def _recording_to_delete(vexa, meeting: Meeting) -> int | None:
    """The id of Vexa's copy of this meeting's recording, or None when there is none to delete.

    The pipeline learns the id only when it first polls a finished meeting, so a meeting deleted
    while still running may not have it stored: ask Vexa by the meeting id it gave us. If Vexa has
    not finished with the meeting yet (no recording id and not `completed` or `failed`), the copy
    may appear after we delete the row, so raise `VexaError` and let the next pass try again."""
    if meeting.vexa_recording_id is not None:
        return meeting.vexa_recording_id
    if not meeting.vexa_meeting_id:
        return None
    info = vexa.get_meeting_by_id(meeting.vexa_meeting_id)
    if info.recording_id is None and info.status not in VEXA_FINISHED:
        raise VexaError("Vexa has not finished the meeting, so its recording is not known yet")
    return info.recording_id


def purge_user(session: Session, user: User, vexa) -> None:
    """Delete `user` and everything of theirs. Commits. Raises `VexaError` when a bot cannot be
    stopped or Vexa's copy of a recording cannot be found or deleted; nothing is deleted then."""
    meetings = list(session.scalars(select(Meeting).where(Meeting.owner_user_id == user.id)))
    for meeting in meetings:
        if meeting.status == "bot_sent":
            vexa.stop_bot(meeting.platform, meeting.native_id)
    for meeting in meetings:
        if meeting.audio_deleted_at is None:
            recording_id = _recording_to_delete(vexa, meeting)
            if recording_id is not None:
                vexa.delete_recording(recording_id)
    for meeting in meetings:
        shutil.rmtree(audio_path(meeting.id).parent, ignore_errors=True)
    email, user_id = user.email, user.id
    session.execute(update(UsageEntry).where(UsageEntry.user_id == user_id).values(user_id=None))
    session.execute(delete(Notification).where(Notification.user_id == user_id))
    session.execute(delete(MailOutbox).where(MailOutbox.to_email == email))
    session.execute(delete(AuthToken).where(AuthToken.email == email))
    session.execute(delete(WaitlistEntry).where(WaitlistEntry.email == email))
    session.execute(delete(UserSettings).where(UserSettings.user_id == user_id))
    session.execute(delete(Meeting).where(Meeting.owner_user_id == user_id))
    session.execute(delete(VocabularyTerm).where(VocabularyTerm.owner_user_id == user_id))
    session.execute(delete(Template).where(Template.owner_user_id == user_id))
    session.execute(delete(User).where(User.id == user_id))
    session.commit()


def purge_deleted(session: Session, vexa) -> int:
    """Purge every user marked `deleted`; returns how many went. Any trouble with one user (Vexa
    down, a bad value, a database error) is logged by type only and leaves that user marked for
    the next pass; it never stops the others and never reaches the worker's loop."""
    ids = list(session.scalars(select(User.id).where(User.status == "deleted").order_by(User.id)))
    purged = 0
    for user_id in ids:
        try:
            user = session.get(User, user_id)
            if user is None:
                continue
            purge_user(session, user, vexa)
        except Exception as exc:  # one user must not stall the rest
            session.rollback()
            logger.warning("user %s: purge waits (%s)", user_id, type(exc).__name__)
            continue
        purged += 1
    return purged
