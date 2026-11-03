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

from app import mail
from app.config import get_settings
from app.db import SessionLocal
from app.models import AppState, Meeting
from app.pipeline import advance_meeting, delete_expired_audio
from app.vexa import VexaClient

logger = logging.getLogger("app.worker")
CLEANUP_EVERY_SECONDS = 3600


def next_meeting_id(session: Session, after_id: int) -> int | None:
    """Lock the next unfinished meeting after `after_id`, skipping ones another worker holds.

    Walking forward by id (rather than always taking the oldest) means a meeting that is still
    waiting for its bot cannot starve the ones behind it.
    """
    return session.scalars(
        select(Meeting.id)
        .where(Meeting.status.in_(("bot_sent", "processing")), Meeting.id > after_id)
        .order_by(Meeting.id)
        .limit(1)
        .with_for_update(skip_locked=True)
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


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")
    mailer = mail.build_mailer(get_settings())
    if mailer is None:
        logger.warning("no mail provider is configured: mail stays in the outbox")
    stop = threading.Event()
    signal.signal(signal.SIGTERM, lambda *_: stop.set())
    signal.signal(signal.SIGINT, lambda *_: stop.set())
    interval = get_settings().poll_interval_seconds
    last_cleanup = 0.0
    cursor = 0
    logger.info("worker started")
    while not stop.is_set():
        try:
            with SessionLocal() as session:
                record_heartbeat(session, datetime.now(UTC))
                send_mail(session, mailer, datetime.now(UTC))
                found = run_pass(session, cursor)
                if time.monotonic() - last_cleanup >= CLEANUP_EVERY_SECONDS:
                    removed = delete_expired_audio(session, datetime.now(UTC), vexa=VexaClient())
                    mail.sweep_outbox(session, datetime.now(UTC))
                    last_cleanup = time.monotonic()
                    if removed:
                        logger.info("deleted audio for %s meetings", removed)
        except Exception as exc:  # e.g. database briefly down
            logger.error("pass failed: %s", type(exc).__name__)
            found = None
        if found is None:
            cursor = 0
            stop.wait(interval)  # swept every unfinished meeting; rest before the next sweep
        else:
            cursor = found
    logger.info("worker stopped")


if __name__ == "__main__":
    main()
