"""Deleting an account for good: the one function behind both `scripts/delete_user.py` and the
admin API.

The admin's `DELETE /api/admin/users/{id}` only marks the user `deleted` (they cannot sign in and
the slot is free at once, since `deleted` rows are treated as absent everywhere) and stamps
`deleted_at`; the worker then calls `purge_deleted`, which runs `purge_user` for each. The pipeline
leaves a deleted user's meetings alone (`pipeline._owner_deleted`), so nothing more is downloaded,
sent to OpenAI or counted for them. `purge_user` stops any bot that is still in a call, deletes
every recording at Vexa and here, then deletes their meetings (and the speaker evidence kept for
them, `skribby_segment_evidence`), templates, vocabulary, settings,
notifications, queued mail, tokens and waitlist row, and finally the user.

What stays: `usage_entries` rows, with only the seconds and `email_hash` (no personal data), so
signing up again does not reset the 5 hours.

**Settled.** A meeting's Vexa side is settled, and the rows may go, only when Vexa can no longer
produce a recording we would not delete:
- Vexa does not know the meeting (404), or
- the bot is gone (`completed` or `failed`) and either a recording with audio exists (it is deleted
  first), or the meeting `failed`, or the recording itself `failed`, or no audio appeared within
  the pipeline's own grace (`RECORDING_GRACE`) counted from the moment THIS purge first saw Vexa
  report the meeting over (stamped on the row, never taken from when it was created).
Anything else (a bot still in a call, a recording still `in_progress` or in any other unsettled
state, the grace not over, Vexa not answering) raises `VexaError` before anything of the user is
deleted, and the next pass tries again; the bounded give-up below ends a wait that never settles.
A 404 for the meeting is settled but logged at error level with the meeting id when no recording
id is stored, so there is a trace of a copy that could not be looked for.
A bot is stopped whatever our own status says: a meeting given up after six hours is `failed` here
while its bot may still be in the call.

**Bounded.** If a purge keeps waiting for a day (`GIVE_UP_AFTER` since `deleted_at`, which the
first failed pass stamps if the admin API did not), it stops waiting: it logs an error naming the
meetings, tells every admin in the app (`admin.purge_forced`), and deletes our rows anyway without
calling Vexa again. The data then outlives the promise only at Vexa, and the admin knows which
meetings to look at. The alert and the log carry ids, never the person's address.
"""

import logging
import shutil
from datetime import UTC, datetime, timedelta

from sqlalchemy import and_, delete, func, or_, select, update
from sqlalchemy.orm import Session

from app import placement, teams
from app.bot_provider import BotNotFound, provider_for
from app.models import (
    AuthToken,
    Base,
    MailOutbox,
    Meeting,
    Milestone,
    Notification,
    SkribbySegmentEvidence,
    Task,
    TeamInvite,
    Template,
    UsageEntry,
    User,
    UserSettings,
    VocabularyTerm,
    WaitlistEntry,
)
from app.pipeline import RECORDING_GRACE, audio_path
from app.vexa import VexaError

logger = logging.getLogger(__name__)

GIVE_UP_AFTER = timedelta(hours=24)
VEXA_FINISHED = ("completed", "failed")


def recording_to_delete(
    session: Session, vexa, meeting: Meeting, now: datetime, stopped: set[int]
) -> int | None:
    """Settle one meeting's Vexa side (see the module docstring): the id of the recording to
    delete, or None when there is nothing to delete. Raises `VexaError` while it is not settled."""
    stored = meeting.vexa_recording_id
    if not meeting.vexa_meeting_id:
        return stored
    try:
        info = vexa.get_meeting_by_id(meeting.vexa_meeting_id)
    except BotNotFound:
        if stored is None:
            logger.error(
                "meeting %s: Vexa does not know it (404) and no recording id is stored; "
                "treating it as settled",
                meeting.id,
            )
        return stored  # Vexa does not know the meeting: nothing there to wait for
    if info.status not in VEXA_FINISHED:
        if meeting.id not in stopped:  # whatever our status: the bot may still be in the call
            if placement.may_stop_bot(session, meeting):  # never another meeting's bot
                vexa.stop_bot(meeting.platform, meeting.native_id)
            stopped.add(meeting.id)
        raise VexaError("the bot is still in the meeting")
    if info.recording_id is not None:
        return info.recording_id
    if stored is not None:
        return stored
    if info.status == "failed" or info.recording_status == "failed":
        return None
    if info.recording_status not in (None, "completed"):
        raise VexaError("a recording of this meeting is still being written")
    if meeting.terminal_seen_at is None:
        meeting.terminal_seen_at = now  # the grace starts when the purge first sees it over
        session.commit()  # kept although this pass raises: nothing else is pending yet
    if now - meeting.terminal_seen_at >= RECORDING_GRACE:
        return None  # no audio appeared within the grace the pipeline itself allows
    raise VexaError("Vexa has not settled this meeting's recording yet")


def delete_provider_copy(provider, meeting: Meeting, recording_id: int | None) -> None:
    """Delete what the provider holds for `meeting`: the recording when there is one, else (for a
    hosted provider) the record of the bot, which a meeting that never recorded still has. Raises
    like the provider does, so the caller's "not settled yet" handling applies."""
    if recording_id is not None:
        provider.delete_recording(recording_id)
    elif meeting.vexa_meeting_id and hasattr(type(provider), "delete_bot_record"):
        provider.delete_bot_record(meeting.vexa_meeting_id)


def meetings_to_delete(session: Session, user_id: int) -> list[Meeting]:
    """The meetings that go with this account: the ones they recorded that no team owns, and the
    ones moved to a team that is itself going (the account is its only member: `teams.remove_users`
    deletes the team). A meeting moved to a team that stays is the team's (spec 6.4, 9.1): it is
    kept and its recorder becomes a "Former member". A team that goes also takes the meetings moved
    to it by someone whose account is already gone; one moved by a recorder whose account still
    exists goes back to them (the foreign key nulls `owner_team_id`: spec 6.8)."""
    doomed = teams.doomed_teams(session, user_id)
    gone = select(User.id).where(User.status == "deleted")
    return list(
        session.scalars(
            select(Meeting)
            .where(
                or_(
                    and_(
                        Meeting.owner_user_id == user_id,
                        or_(Meeting.owner_team_id.is_(None), Meeting.owner_team_id.in_(doomed)),
                    ),
                    and_(Meeting.owner_team_id.in_(doomed), Meeting.owner_user_id.in_(gone)),
                )
            )
            .order_by(Meeting.id)
        )
    )


def erase_account_row(session: Session, user: User, now: datetime) -> None:
    """Erase the person but keep the row: a team owns meetings they recorded and `meetings.
    owner_user_id` points here. Name, email, credentials and every row that hung on the account
    go, exactly what deleting the row would have done through the foreign keys; the row stays
    `deleted`, with `purged_at` set so the worker does not purge it again. The meetings show its
    recorder as "Former member"."""
    for table in Base.metadata.sorted_tables:
        for key in table.foreign_keys:
            if key.column.table.name != "users" or key.column.name != "id":
                continue
            if key.ondelete == "CASCADE":
                session.execute(delete(table).where(key.parent == user.id))
            elif key.ondelete == "SET NULL":
                session.execute(
                    update(table).where(key.parent == user.id).values({key.parent.name: None})
                )
    user.email = f"former-member-{user.id}@invalid.invalid"
    user.name = user.password_hash = user.google_sub = user.signup_origin = None
    user.email_verified_at = user.terms_accepted_at = user.terms_version = None
    user.hours_cap_seconds, user.is_admin = None, False
    user.session_epoch += 1
    user.status, user.purged_at = "deleted", now
    session.flush()


def purge_user(
    session: Session, user: User, vexa, now: datetime | None = None, *, force: bool = False
) -> None:
    """Delete `user` and everything of theirs. Commits. Raises `VexaError` while Vexa has not
    settled (nothing is deleted then). With `force`, Vexa is not called at all: the caller has
    given up waiting and recorded that."""
    now = now or datetime.now(UTC)
    meetings = meetings_to_delete(session, user.id)
    meeting_ids = [meeting.id for meeting in meetings]
    if not force:
        stopped: set[int] = set()
        for meeting in meetings:
            if meeting.status == "bot_sent":
                if placement.may_stop_bot(session, meeting):  # never another meeting's bot
                    provider_for(vexa, meeting.bot_provider, meeting.bot_region).stop_bot(
                        meeting.platform, meeting.native_id
                    )
                stopped.add(meeting.id)
        for meeting in meetings:
            if meeting.audio_deleted_at is None:
                holder = provider_for(vexa, meeting.bot_provider, meeting.bot_region)
                recording_id = recording_to_delete(session, holder, meeting, now, stopped)
                delete_provider_copy(holder, meeting, recording_id)
    for meeting in meetings:
        shutil.rmtree(audio_path(meeting.id).parent, ignore_errors=True)
    email, user_id = user.email, user.id
    session.execute(update(UsageEntry).where(UsageEntry.user_id == user_id).values(user_id=None))
    session.execute(delete(Notification).where(Notification.user_id == user_id))
    session.execute(delete(MailOutbox).where(MailOutbox.to_email == email))
    session.execute(delete(AuthToken).where(AuthToken.email == email))
    session.execute(delete(WaitlistEntry).where(WaitlistEntry.email == email))
    session.execute(delete(TeamInvite).where(TeamInvite.email == email))  # frees their place
    session.execute(delete(UserSettings).where(UserSettings.user_id == user_id))
    # A date proposed from their meetings goes with them (a confirmed milestone stays, unsourced).
    session.execute(
        delete(Milestone).where(
            Milestone.confirmed_at.is_(None),
            Milestone.source_meeting_id.in_(meeting_ids),
        )
    )
    # What the bot provider sent about who spoke goes first (the meetings' deletion would cascade
    # to it; this does not rely on that).
    session.execute(
        delete(SkribbySegmentEvidence).where(SkribbySegmentEvidence.meeting_id.in_(meeting_ids))
    )
    session.execute(delete(Meeting).where(Meeting.id.in_(meeting_ids)))  # tasks cascade
    # Tasks they typed in go with them (spec 4); other people's tasks they held become unassigned
    # by the foreign key.
    session.execute(delete(Task).where(Task.created_by_user_id == user_id, Task.source == "manual"))
    session.execute(delete(VocabularyTerm).where(VocabularyTerm.owner_user_id == user_id))
    # A template a kept meeting was written with stays, taken out of use (`deleted_at`): the
    # meeting still names it. Every other template goes.
    mine = select(Template.id).where(Template.owner_user_id == user_id)
    in_use = select(Meeting.template_id).where(Meeting.template_id.in_(mine))
    session.execute(
        update(Template)
        .where(
            Template.owner_user_id == user_id,
            Template.deleted_at.is_(None),
            Template.id.in_(in_use),
        )
        .values(deleted_at=now)
    )
    session.execute(
        delete(Template).where(Template.owner_user_id == user_id, Template.id.not_in(in_use))
    )
    teams.remove_users(session, [user_id])
    # A meeting a team owns outlives its recorder: erase the person but keep the row it points to.
    # (or a template a surviving meeting still names: nothing else holds the row).
    kept = session.scalar(
        select(func.count()).select_from(Meeting).where(Meeting.owner_user_id == user_id)
    ) or session.scalar(
        select(func.count()).select_from(Template).where(Template.owner_user_id == user_id)
    )
    if kept:
        erase_account_row(session, user, now)
    else:
        session.execute(delete(User).where(User.id == user_id))
    session.commit()


def _give_up(session: Session, user: User, now: datetime) -> None:
    """Stop waiting for Vexa: log, tell every admin in the app, delete our rows anyway."""
    meeting_ids = [meeting.id for meeting in meetings_to_delete(session, user.id)]
    logger.error(
        "user %s: gave up waiting for Vexa after %s; deleting our rows, the Vexa copy of meetings "
        "%s could not be confirmed deleted",
        user.id,
        GIVE_UP_AFTER,
        meeting_ids,
    )
    body = (
        f"The deletion of user {user.id} waited a day for Vexa and then deleted our records "
        f"anyway. Check Vexa for the recordings of meeting ids {meeting_ids}: they could not be "
        "confirmed deleted."
    )
    for admin_id in session.scalars(
        select(User.id).where(User.is_admin.is_(True), User.status == "active")
    ):
        session.add(
            Notification(
                user_id=admin_id,
                kind="admin.purge_forced",
                severity="error",
                title="A deleted account's recordings could not be confirmed gone at Vexa",
                body=body,
                dedupe_key=f"purge_forced:{user.id}",
                created_at=now,
            )
        )
    session.commit()
    purge_user(session, user, None, now, force=True)


def purge_deleted(session: Session, vexa) -> int:
    """Purge every user marked `deleted`; returns how many went. Any trouble with one user (Vexa
    down or not settled, a bad value, a database error) is logged by type only and leaves that
    user marked for the next pass, until a day has passed (see the module docstring); it never
    stops the others and never reaches the worker's loop."""
    ids = list(
        session.scalars(
            select(User.id)
            .where(User.status == "deleted", User.purged_at.is_(None))
            .order_by(User.id)
        )
    )
    purged = 0
    for user_id in ids:
        now = datetime.now(UTC)
        try:
            user = session.get(User, user_id)
            if user is None:
                continue
            try:
                purge_user(session, user, vexa, now)
            except Exception as exc:
                session.rollback()
                logger.warning("user %s: purge waits (%s)", user_id, type(exc).__name__)
                user = session.get(User, user_id)
                if user is None:
                    continue
                if user.deleted_at is None:  # marked by hand: the clock starts now
                    user.deleted_at = now
                    session.commit()
                elif now - user.deleted_at >= GIVE_UP_AFTER:
                    _give_up(session, user, now)
                    purged += 1
                continue
        except Exception as exc:  # the bookkeeping itself failed: still only this user
            session.rollback()
            logger.warning("user %s: purge bookkeeping failed (%s)", user_id, type(exc).__name__)
            continue
        purged += 1
    return purged
