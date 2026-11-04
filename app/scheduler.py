"""The scheduler: the process that does everything time-critical (spec section 9.7).

`python -m app.scheduler` runs until SIGTERM. It is its own process, not part of `app.worker`,
because the worker can be blocked for minutes in one transcription pass and a join due at 10:00
cannot wait behind that.

* **One leader.** A session-level Postgres advisory lock on a dedicated, direct connection. The
  server frees the lock the moment that connection dies, and a standby tries again on each
  dispatch tick (10 s), so failover takes about 10 s. This stack has no connection pooler in
  front of Postgres; if one is ever added, this connection must bypass it, because a pooled
  connection can return to the pool still holding the lock.
* **Two loops, two threads, two sessions.** The dispatch loop (every 10 s) writes the heartbeat
  and runs the dispatch jobs; the sync loop (wakes every 60 s) runs the sync jobs. A slow Google
  call therefore never delays a dispatch. Only the dispatch loop touches the leader connection.
* **Jobs** are `fn(session, now)` registered by name with an interval. Each run gets a fresh
  session; whatever a job raises is logged by type only (an exception can hold an address, a
  link or a token) and never reaches the loop or the other jobs. A job that failed is tried again
  at its next interval, not on every tick.
* **A restart never replays missed intervals:** a job's first run after start is immediate and
  its next one is `interval` later, so an hour of downtime means one catch-up run, not 360.
  Jobs that must not miss a due item (joins whose `run_at` passed) select by `run_at <= now`
  themselves, so one run catches up.
* The clock is injected, so a test runs a whole day in milliseconds.

A tick checks it still leads before it starts, but a job already running when the connection
dies is not interrupted, so jobs must be safe to run twice (row locks or idempotent updates).
"""

import logging
import signal
import threading
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Literal

from sqlalchemy import Connection, create_engine, text
from sqlalchemy.orm import Session
from sqlalchemy.pool import NullPool

from app.clock import Clock, SystemClock
from app.config import get_settings
from app.db import SessionLocal
from app.models import AppState

logger = logging.getLogger("app.scheduler")

# Next to PLACEMENT_LOCK (6004), SIGNUP_LOCK (6002) and ADMIN_LOCK (6003). Those are held for one
# transaction; this one is session-level and held for the life of the process.
SCHEDULER_LOCK = 6010

Loop = Literal["dispatch", "sync"]
JobFn = Callable[[Session, datetime], object]


@dataclass
class _Job:
    name: str
    every: timedelta
    fn: JobFn
    next_due: datetime | None = None  # None = never ran in this process: due now


def direct_connection_factory(database_url: str) -> Callable[[], Connection]:
    """A factory of connections that really close, for the leader lock.

    `NullPool`: closing the connection closes the socket, which is what frees a session-level
    advisory lock. AUTOCOMMIT: the connection never sits "idle in transaction" between pings.
    TCP keepalives: a peer that vanished without a FIN is noticed in about a minute, not two hours.
    """

    def connect() -> Connection:
        engine = create_engine(
            database_url,
            poolclass=NullPool,
            isolation_level="AUTOCOMMIT",
            connect_args={
                "connect_timeout": 5,
                "keepalives": 1,
                "keepalives_idle": 20,
                "keepalives_interval": 10,
                "keepalives_count": 3,
            },
        )
        try:
            return engine.connect()
        finally:
            engine.dispose()  # NullPool holds nothing; the connection outlives the engine object

    return connect


class Scheduler:
    def __init__(
        self,
        clock: Clock,
        session_factory: Callable[[], Session],
        *,
        leader_conn_factory: Callable[[], Connection],
        dispatch_every: float = 10,
        sync_every: float = 60,
    ) -> None:
        self._clock = clock
        self._session_factory = session_factory
        self._leader_conn_factory = leader_conn_factory
        self._dispatch_every = dispatch_every
        self._sync_every = sync_every
        self._jobs: dict[Loop, list[_Job]] = {"dispatch": [], "sync": []}
        self._names: set[str] = set()
        self._leader_conn: Connection | None = None
        self._stop = threading.Event()

    # -- registration --------------------------------------------------------------------------

    def register(self, name: str, every_seconds: int, fn: JobFn, *, loop: Loop) -> None:
        if every_seconds <= 0:
            raise ValueError("every_seconds must be positive")
        if loop not in self._jobs:
            raise ValueError(f"loop must be 'dispatch' or 'sync', not {loop!r}")
        if name in self._names:
            raise ValueError(f"a job named {name!r} is already registered")
        self._names.add(name)
        self._jobs[loop].append(_Job(name, timedelta(seconds=every_seconds), fn))

    # -- leadership ----------------------------------------------------------------------------

    @property
    def is_leader(self) -> bool:
        return self._leader_conn is not None

    def try_lead(self) -> bool:
        """Take the lock, or confirm we still hold it. Never raises: a database that is down is
        "not leading", and the next tick tries again. Call from the dispatch loop only."""
        if self._leader_conn is not None:
            try:
                self._leader_conn.execute(text("select 1"))
                return True
            except Exception as exc:
                logger.warning("lost the leader connection (%s); standing down", type(exc).__name__)
                self.release()
        conn = None
        try:
            conn = self._leader_conn_factory()
            got = conn.execute(
                text("select pg_try_advisory_lock(:key)"), {"key": SCHEDULER_LOCK}
            ).scalar()
        except Exception as exc:
            logger.warning("could not try for the leader lock (%s)", type(exc).__name__)
            got = False
        if got:
            self._leader_conn = conn
            logger.info("this scheduler is now the leader")
            return True
        if conn is not None:
            _close_quietly(conn)
        return False

    def release(self) -> None:
        """Close the leader connection; Postgres frees the lock with it."""
        conn, self._leader_conn = self._leader_conn, None
        if conn is not None:
            _close_quietly(conn)

    # -- ticks ---------------------------------------------------------------------------------

    def tick_dispatch(self) -> None:
        if not self.try_lead():
            return
        now = self._clock.now()
        try:
            self._write_heartbeat(now)
        except Exception as exc:  # the heartbeat must not stop the jobs; its absence is the alarm
            logger.warning("heartbeat failed (%s)", type(exc).__name__)
        self._run_due("dispatch", now)

    def tick_sync(self) -> None:
        if not self.is_leader:  # the leader connection belongs to the dispatch thread
            return
        self._run_due("sync", self._clock.now())

    def _write_heartbeat(self, now: datetime) -> None:
        with self._session_factory() as session:
            state = session.get(AppState, 1)
            if state is None:
                state = AppState(id=1)
                session.add(state)
            state.scheduler_heartbeat_at = now
            session.commit()

    def _run_due(self, loop: Loop, now: datetime) -> None:
        for job in self._jobs[loop]:
            if job.next_due is not None and now < job.next_due:
                continue
            job.next_due = now + job.every
            try:
                with self._session_factory() as session:  # closing it rolls back what a job left
                    job.fn(session, now)
            except Exception as exc:
                logger.warning("job %s failed (%s)", job.name, type(exc).__name__)

    # -- running -------------------------------------------------------------------------------

    def stop(self) -> None:
        self._stop.set()

    def run_forever(self, stop: threading.Event | None = None) -> None:
        """Run both loops until `stop` (or `stop()`, or SIGTERM from `main`) is set, then let the
        tick in progress finish and give up the lock."""
        if stop is not None:
            self._stop = stop
        sync_thread = threading.Thread(target=self._sync_loop, name="scheduler-sync", daemon=True)
        sync_thread.start()
        try:
            while not self._stop.is_set():
                try:
                    self.tick_dispatch()
                except Exception as exc:  # nothing a tick does may end the loop
                    logger.error("dispatch tick failed (%s)", type(exc).__name__)
                self._stop.wait(self._dispatch_every)
        finally:
            self._stop.set()
            sync_thread.join(timeout=30)
            self.release()

    def _sync_loop(self) -> None:
        while not self._stop.is_set():
            try:
                self.tick_sync()
            except Exception as exc:
                logger.error("sync tick failed (%s)", type(exc).__name__)
            self._stop.wait(self._sync_every)


def _close_quietly(conn: Connection) -> None:
    try:
        conn.close()
    except Exception as exc:
        logger.warning("closing the leader connection failed (%s)", type(exc).__name__)


def register_jobs(scheduler: Scheduler, settings) -> None:
    """The jobs this process runs. Calendar jobs exist only while `CALENDAR_ENABLED` is true, and
    their modules are not even imported before that."""
    if settings.calendar_enabled:
        from app.gcal_sync import register_calendar_sync

        register_calendar_sync(scheduler, settings)


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")
    settings = get_settings()
    scheduler = Scheduler(
        SystemClock(),
        SessionLocal,
        leader_conn_factory=direct_connection_factory(settings.database_url),
        dispatch_every=settings.scheduler_dispatch_seconds,
        sync_every=settings.scheduler_sync_seconds,
    )
    register_jobs(scheduler, settings)
    signal.signal(signal.SIGTERM, lambda *_: scheduler.stop())
    signal.signal(signal.SIGINT, lambda *_: scheduler.stop())
    logger.info("scheduler started")
    scheduler.run_forever()
    logger.info("scheduler stopped")


if __name__ == "__main__":
    main()
