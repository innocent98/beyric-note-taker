"""Background worker: takes one unfinished meeting per pass and moves it forward.

`python -m app.worker` runs until SIGTERM, finishing the current pass first.
"""

import logging
import signal
import threading
import time
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app import deletion, mail, maintenance, topics, translation, workspace
from app.bot_provider import build_provider
from app.config import get_settings
from app.db import SessionLocal
from app.models import AppState, Meeting, User
from app.pipeline import advance_meeting, delete_expired_audio, delete_fetched_copies

logger = logging.getLogger("app.worker")
CLEANUP_EVERY_SECONDS = 3600


def next_meeting_id(session: Session, after_id: int) -> int | None:
    """Lock the next unfinished meeting after `after_id`, skipping ones another worker holds.

    Walking forward by id (rather than always taking the oldest) means a meeting that is still
    waiting for its bot cannot starve the ones behind it.
    """
    return session.scalars(
        select(Meeting.id)
        .where(
            Meeting.status.in_(("bot_sent", "processing")),
            Meeting.id > after_id,
            # a meeting of an account being deleted is not worked on (app/deletion.py purges it)
            Meeting.owner_user_id.not_in(select(User.id).where(User.status == "deleted")),
        )
        .order_by(Meeting.id)
        .limit(1)
        .with_for_update(skip_locked=True, of=Meeting)
    ).first()


def record_heartbeat(session: Session, now: datetime) -> None:
    """Stamp the time on the app_state row so the home page can tell the worker is alive."""
    state = session.get(AppState, 1)
    if state is None:
        state = AppState(id=1)
        session.add(state)
    state.worker_heartbeat_at = now
    session.commit()


def run_pass(session: Session, after_id: int) -> int | None:
    """Advance the next meeting after `after_id`. Returns its id, or None if there is none.

    NOTE: advance_meeting holds the row lock and this DB connection through long OpenAI calls
    (minutes for a long recording). That is fine for a single bot working one meeting at a time;
    revisit it if several workers are run.
    """
    meeting_id = next_meeting_id(session, after_id)
    if meeting_id is None:
        session.rollback()
        return None
    try:
        advance_meeting(session, meeting_id)
    except Exception as exc:  # one meeting must never stop the loop
        session.rollback()
        logger.error("meeting %s: unexpected %s", meeting_id, type(exc).__name__)
    return meeting_id


def send_mail(session: Session, mailer: mail.Mailer | None, now: datetime) -> int:
    """Send the mail that is due. With no mailer (production without a provider) mail waits in
    the outbox until the next sweep fails it."""
    if mailer is None:
        return 0
    return mail.deliver_pending(session, mailer, get_settings(), now)


def purge_deleted(session: Session, vexa=None) -> int:
    """Remove the data of accounts the admin deleted (`app/deletion.py`)."""
    return deletion.purge_deleted(session, vexa or build_provider())


def extract_pending(session: Session, now: datetime) -> int | None:
    """Read the notes of one meeting into decisions, open questions and review items
    (`app/workspace.py`)."""
    return workspace.process_next(session, now)


def find_topics(session: Session, now: datetime) -> int | None:
    """Look for the topics that keep coming up in a series whose notes were just read, one model
    call per series (`app/topics.py`)."""
    return topics.process_next(session, now)


def background_jobs(session: Session, mailer: mail.Mailer | None, now: datetime) -> None:
    """The jobs that run before each meeting pass: send due mail, purge deleted accounts, read a
    finished meeting's notes into lists, look for a series' repeat topics, retry the deletion of a
    hosted provider's copy. Each is on its own, and
    whatever one throws is logged by type only (an exception can hold an address or a link) and
    never reaches the loop, so a mail, purge, extraction or topics problem cannot stall meetings."""
    for name, job in (
        ("mail", lambda: send_mail(session, mailer, now)),
        ("purge", lambda: purge_deleted(session)),
        ("extraction", lambda: extract_pending(session, now)),
        ("topics", lambda: find_topics(session, now)),
        ("provider copies", lambda: delete_fetched_copies(session, now)),
    ):
        try:
            job()
        except Exception as exc:
            session.rollback()
            logger.warning("%s job failed (%s)", name, type(exc).__name__)


def hourly_jobs(session: Session, now: datetime, vexa=None) -> None:
    """The hourly jobs, each on its own: deleting expired audio, and the housekeeping of
    `app/maintenance.py` (the unverified-account sweep, the mail outbox scrub and the rest). One
    that fails every time (Vexa down for the audio cleanup, say) must not stop the other, so each
    is caught and logged by type only."""

    def audio() -> None:
        removed = delete_expired_audio(session, now, vexa=vexa or build_provider())
        if removed:
            logger.info("deleted audio for %s meetings", removed)

    for name, job in (
        ("audio cleanup", audio),
        ("maintenance", lambda: maintenance.run_hourly(session, now)),
    ):
        try:
            job()
        except Exception as exc:
            session.rollback()
            logger.warning("hourly %s failed (%s)", name, type(exc).__name__)


def start_translation_thread(stop: threading.Event) -> threading.Thread:
    """Translations run in a thread of their own (`app/translation.py`): one is many model calls
    and takes minutes on a long meeting, which must not hold up the polling of live meetings. The
    thread ends when `stop` is set, after its current call."""
    thread = threading.Thread(
        target=translation.run_loop, args=(stop,), name="translation", daemon=True
    )
    thread.start()
    return thread


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")
    mailer = mail.build_mailer(get_settings())
    if mailer is None:
        logger.warning("no mail provider is configured: mail stays in the outbox")
    stop = threading.Event()
    signal.signal(signal.SIGTERM, lambda *_: stop.set())
    signal.signal(signal.SIGINT, lambda *_: stop.set())
    interval = get_settings().poll_interval_seconds
    translator = start_translation_thread(stop)
    last_cleanup = 0.0
    cursor = 0
    logger.info("worker started")
    while not stop.is_set():
        try:
            with SessionLocal() as session:
                record_heartbeat(session, datetime.now(UTC))
                background_jobs(session, mailer, datetime.now(UTC))
                found = run_pass(session, cursor)
                if time.monotonic() - last_cleanup >= CLEANUP_EVERY_SECONDS:
                    hourly_jobs(session, datetime.now(UTC))
                    last_cleanup = time.monotonic()
        except Exception as exc:  # e.g. database briefly down
            logger.error("pass failed: %s", type(exc).__name__)
            found = None
        if found is None:
            cursor = 0
            stop.wait(interval)  # swept every unfinished meeting; rest before the next sweep
        else:
            cursor = found
    translator.join(timeout=30)
    logger.info("worker stopped")


if __name__ == "__main__":
    main()
