# SOP: Phase C scheduler skeleton (C10)

## What shipped
The `scheduler` service: a separate process (`python -m app.scheduler`) with a leader lock, a dispatch loop and a sync loop, an injected clock and a heartbeat, plus the compose service and the deploy step that waits for its first heartbeat. Branch `feature/c10-scheduler`, one PR (number in the PR list). **It registers no jobs yet** (C10b adds the sweeps and mail tick, C8b the calendar sync, C12 the join runner), so for a user nothing changes: the process starts, takes the lock and writes a heartbeat.

## Why
Everything time-critical in the calendar design (sync, re-checks, dispatch, mail, host health) cannot run in `app.worker`, which can be blocked for minutes inside one transcription pass, and a join due at 10:00 cannot wait behind that. It also must not run twice at once (two schedulers would send two bots), and a deploy must never leave the site down because of it.

## How
| Decision | Why |
|---|---|
| **Leader = session-level `pg_try_advisory_lock(6010)` on a dedicated connection** (`direct_connection_factory`: `NullPool`, `AUTOCOMMIT`, TCP keepalives) | The server frees the lock when the connection dies, so there is no lease to expire. `NullPool` matters: a pooled connection goes back to the pool still holding the lock. AUTOCOMMIT keeps it from sitting "idle in transaction". Keepalives (about a minute) catch a peer that vanished without closing. This stack has no pooler; if one is added this connection must bypass it |
| A tick pings the leader connection (`select 1`) before it works; a dead one means "not leader", then it tries for the lock again | The same tick can win the lock back if nobody else took it, so a blip costs nothing. A job already running when the connection dies is not interrupted: **jobs must be safe to run twice** |
| Standby retries on every dispatch tick (10 s) | Failover is at most one tick. Only the dispatch thread touches the leader connection; the sync thread just reads `is_leader` |
| Jobs are `fn(session, now)`, one fresh session per run | A job that leaves a transaction broken cannot poison the next one; closing the session rolls back what it left |
| A failing job logs `job <name> failed (<ExceptionType>)` and nothing else; it is tried again at its **next interval**, not every tick | An exception message can hold an address, a link or a token. No `exc_info`, no DSN anywhere |
| First run of a job is immediate, the next is `interval` later | A restart (or an hour of downtime) is one catch-up run, never 360. Jobs that must not miss an item (joins past `run_at`) select by `run_at <= now` themselves |
| Heartbeat written by the leader only, before the jobs, each dispatch tick, from the injected clock; a heartbeat failure is logged and does not stop the jobs | A standby that wrote it would hide a dead leader |
| Compose: nothing `depends_on` the scheduler; it waits for `db` and `migrate` like `worker`; `restart: unless-stopped`; `mem_limit: 256m`; `stop_grace_period: 40s` | A scheduler that crash-loops cannot fail `up` for `web` or `next`, so it cannot break their health check. 40 s covers the 30 s the sync thread is given to finish on SIGTERM |
| **No new required setting.** Two optional ones: `SCHEDULER_DISPATCH_SECONDS` (10), `SCHEDULER_SYNC_SECONDS` (60) | Per the deploy gate after the #45 outage |
| `deploy.sh`: after `wait_healthy` passes, `wait_scheduler_beat` needs a heartbeat newer than the moment `up` returned (read with `psql` in `db`; 15 tries 4 s apart = 60 s); no beat means the same rollback as an unhealthy `web` | The old scheduler is already stopped when `up` returns, so an old beat cannot count (`+1 s` because `date +%s` truncates). It is **skipped when the checked-out compose file has no `scheduler`** (`docker compose config --services`), and the rollback only waits for `web` and `next`, so rolling back to a commit without a scheduler is never blocked by it |

Rejected: a lease row with an expiry (needs clock agreement and a renewal race the connection-bound lock does not have); `pg_advisory_xact_lock` (ends with the transaction); a compose `healthcheck` on the scheduler (a standby writes no heartbeat, so it would always look unhealthy); making the scheduler call `check_start` (it would crash-loop on a missing setting for no benefit, and the deploy rollback already covers a scheduler that never beats).

## What's involved
| Path | Change |
|---|---|
| `app/clock.py` | `Clock` protocol, `SystemClock` |
| `app/scheduler.py` | `Scheduler` (`register`, `try_lead`, `release`, `tick_dispatch`, `tick_sync`, `run_forever`), `direct_connection_factory`, `SCHEDULER_LOCK`, `main` |
| `app/config.py` | `scheduler_dispatch_seconds`, `scheduler_sync_seconds` |
| `compose.yaml` | service `scheduler` |
| `deploy/deploy.sh`, `deploy/README.md` | `wait_scheduler_beat`, operating note, the two settings |
| `tests/helpers.py`, `tests/test_scheduler.py` | `FakeClock`; 16 tests |
| `app_state.scheduler_heartbeat_at` | already in migration 0006: **no migration** |

## Verification
- Tests first (watched failing on the missing modules), then green: `tests/test_scheduler.py`, 16 tests on a real Postgres 17: second scheduler cannot lead; standby takes over after the leader closes and after the server kills its connection; restart has no duplicate leader and runs a missed job once; a whole simulated day on `FakeClock` (8,640 / 24 / 288 runs); a failing job does not stop others and its secret never reaches the log; a broken transaction does not poison the next job; sync loop on its own thread and session; a blocked sync job does not delay a dispatch tick or its heartbeat; heartbeat each tick; standby writes none; a closed leader connection really frees the lock.
- Real processes, two copies of `python -m app.scheduler` against a migrated scratch database: the first led and beat (age under 10 s), the second stayed standby; `kill -9` on the leader and the standby led within one tick and kept beating; SIGTERM stopped the survivor in about 4 s and left no advisory lock.
- `docker compose config` valid (with a throwaway `.env`).
- `deploy.sh` extension: four stub tests in `tests/test_deploy_script.py` (beat passes; no beat rolls back after exactly 15 tries; the rollback does not wait for a scheduler; a commit with no scheduler is never asked), next to #47's eight; all twelve pass.
- **Not verified:** a real `docker compose up` of the new service and the deploy on the haven-respite server (Docker's disk on the build machine is full); the 40 s stop grace against a real long sync job (there are none yet).

## Operate / roll back
- Logs: `docker compose logs --tail 20 scheduler` (`this scheduler is now the leader` once; `job <name> failed (<Type>)` per failure). Heartbeat age: see `deploy/README.md` section 10 notes.
- Stop it: `docker compose stop scheduler` (no job exists yet that depends on it). A second copy (`docker compose up -d --scale scheduler=2`) is a standby.
- Roll back: revert the PR; `--remove-orphans` removes the container. No migration, nothing to undo.
- Server steps before merge: none (no required variable).

## Follow-ups
- C10b registers the sweeps and the mail tick; C8b, C12 register their jobs. Every job must be idempotent (a leader can lose its lock mid-job).
- The admin capacity screen alerts after 5 minutes without a heartbeat (spec 9.7): not built yet.
- Opus lock review is part of the phase C pass (plan C10 Review).
