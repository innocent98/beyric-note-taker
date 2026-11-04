# SOP: Phase C sweeps, the mail tick and the alerts helper (C10b, C11b)

## What shipped
Two commits in one PR (branch `feature/c10b-c11b-sweeps-alerts`; number in the PR list).
- **C10b, `app/sweeps.py`:** the scheduler now runs the hourly clean-up sweeps (one job each, on the sync loop) and sends the mail outbox on every dispatch tick (10 s).
- **C11b, `app/alerts.py`:** `notify_user`, `notify_admins` and `bucket_key`, the one way code tells a user or the admins something: a bell entry, and an email when asked, deduped by key, never raising a mail problem into the caller.

No API route, no screen and no migration: the FE guides are unchanged. Nothing calls the alerts helper yet; C12, C13 and C14 will.

## Why
The calendar design (spec 9.7) puts every time-critical and periodic job on the scheduler, not the worker. C10 built the empty scheduler; C10b gives it its first jobs. C11b exists because the join runner (C12) must tell people when a meeting could not be recorded, and must do that without a mail outage ever stopping a bot from being placed, and without alerting every ten seconds.

## How
| Decision | Why |
|---|---|
| **No new sweep logic: register what exists.** `maintenance.py`'s stale-row delete is split into `sweep_oauth_flows`, `sweep_auth_tokens`, `sweep_notifications`; `accounts.sweep_unverified` and `mail.sweep_outbox` are registered as they are; only `sweep_calendar_events` is new | The worker already ran all of these (`maintenance.run_hourly`) and W0.3 already deletes sent and skipped mail after 30 days, which is the 30-day mail log the privacy policy promises. A second copy of a retention rule would drift |
| One job per sweep (`sweep_*`), each hourly on the **sync** loop | A failing sweep is isolated by the scheduler and the other five still run; a slow delete never delays a dispatch tick or the heartbeat |
| Mail sender `mail_outbox_send` on the **dispatch** loop, every tick | The plan says so, and a verification mail waits 10 s at most. Registered only when a mail provider is configured, as the worker does (production without one: mail waits in the outbox until the sweep fails it, and a warning is logged) |
| Sweeps and mail are **not** calendar-gated (`CALENDAR_ENABLED`) | The tables exist either way; an empty `calendar_events` costs one cheap delete an hour |
| Token sweep keeps spent or expired tokens **7 days** (`maintenance.TOKEN_KEEP`), not "deleted the moment they expire" | That is the behaviour already shipped and documented (debugging window); the plan's wording ("expired and used") is met at that boundary |
| Calendar events go when `ends_at < now - 2 days`; a `scheduled_join` and its meeting stay (`calendar_event_id` goes to null by the foreign key) | Spec 12.2; the event row can hold a Zoom passcode in its link |
| **Alerts: the notification row is written first with `INSERT ... ON CONFLICT DO NOTHING` on `(user_id, dedupe_key)`, and the mail is queued only by the call that wrote the row** | Dedupe at the database, so two processes racing write one row and send one mail. A repeated alert is neither a second bell entry nor a second email |
| `bucket_key(kind, now, minutes=30)` = `kind:<epoch bucket>` | Every process agrees on the bucket; an alert that lasts an hour repeats twice. Naive time is refused (a naive `timestamp()` uses the local zone) |
| Mail part runs in a **savepoint**, any exception is logged by type only and undoes only the mail | Alerts are sent from code doing something more important; a provider message can hold an address |
| Email respects the person: account `active`, `user_settings.notify_email` (on when no row), address not suppressed (`mail.suppression_reason`: bounce, complaint, unsubscribe) and a provider configured. The bell rings regardless | `notify_email` and suppression are Adebayo's pilot rules. Unsubscribe is checked when the mail is queued, because the `alert` template is account-style mail (the sender itself stops only a bounce or complaint) |
| `notify_admins`: a row for every **active** admin; **one** mail per new key to `ADMIN_ALERT_EMAIL`, or, when it is empty, one to each admin who has not switched `notify_email` off | Spec 14. The mail goes only when this call wrote at least one new row, which is what makes it once per bucket |
| **Admin text never carries a meeting link:** web addresses (`http(s)://`, `www.`, and Zoom, Meet and Teams hosts written without a scheme) become `[link removed]` before the text is stored or mailed; the caller names the user and the time | A meeting link can hold a Zoom passcode. Enforced in the helper, not left to each caller |
| New mail template `alert` in `app/mail_templates.py` (a link-style template whose payload is `{"title", "body"}`); every string is escaped by the layout, the subject is one line (`clean_line`) | A text-only mail needs a template; a link-style row is scrubbed once sent and failed after an hour unsent (an old alert is no use) |

Rejected: removing the mail send and the hourly housekeeping from the worker in this PR (it would rewrite tests in `test_admin`, `test_mail`, `test_mail_upgrade`, `test_workspace` that the parallel mail and sign-up hardening may also touch, and it would make mail depend on the scheduler leader before anyone has seen the scheduler do it in production); one combined `run_hourly` job (one failure would hide behind another); an `alerts` table of its own (the notifications table already has the unique key).

## What's involved
| Path | Change |
|---|---|
| `app/sweeps.py` | new: `register_default_sweeps(scheduler, settings=None, *, mailer=None)`, `HOURLY`, `EVERY_TICK` |
| `app/maintenance.py` | `sweep_oauth_flows`, `sweep_auth_tokens`, `sweep_notifications` (split out, same behaviour), `sweep_calendar_events` (new), `CALENDAR_EVENT_KEEP` |
| `app/scheduler.py` | `register_jobs` always calls `register_default_sweeps`, then the calendar sync only if `CALENDAR_ENABLED` |
| `app/alerts.py` | new: `notify_user`, `notify_admins`, `bucket_key`, `ALERT_TEMPLATE` |
| `app/mail_templates.py` | `alert` template (additive; no existing template changed) |
| `app/config.py`, `.env.example` | `admin_alert_email` (`ADMIN_ALERT_EMAIL`, optional, empty) |
| `tests/test_sweeps.py` (18), `tests/test_alerts.py` (33), `tests/test_gcal_sync_job.py` | the two "nothing registered" tests now look at calendar jobs only |
| `app/signup.py`, `app/mail.py`, the legal templates | **not touched** (parallel hardening work) |

## Verification
- Tests first, watched failing (missing modules), then green on a real Postgres 17 (`nt-test-pg-c10b`, port 54312).
- Sweeps: one test each with rows on both sides of the boundary second (calendar events, OAuth flows, tokens used and expired, notifications, sent mail, unverified accounts at 48 h), the slot freed by the unverified sweep (`slots_taken`, `has_room`), a join surviving its deleted event, all six run twice with identical counts, hourly cadence on a fake clock (first run immediate, nothing at 59 min, again at 60), the mail sender on every dispatch tick, one failing sweep leaving the others running and its message out of the log, no mail job without a provider, registration with the calendar flag on and off.
- Alerts: all six plan tests, plus suppression, inactive user, no provider, mail failure leaving the caller's transaction usable, the savepoint rolling back only the mail, a vanished user, links removed from admin text (and the rendered mail), the mail rendered and scrubbed once sent, a header-injection title, the `admin.` prefix, no admin at all.
- `ruff check .` and `ruff format --check .` clean (the project's pinned ruff 0.17.3 in the worktree venv); `tests/test_sweeps.py`, `test_alerts.py`, `test_maintenance.py`, `test_scheduler.py`, `test_gcal_sync_job.py`, `test_mail.py`, `test_mail_upgrade.py`, `test_admin.py` pass; the whole suite was run too because `config.py` changed.
- **Not verified:** the scheduler running these jobs against the production database and a real Resend send; the `alert` mail's HTML in a real mail client.

## Operate / roll back
- **Server steps before merge: none.** `ADMIN_ALERT_EMAIL` is optional (empty = each admin's own address); nothing needs a value on deploy. Set it when C12 ships so one ops mailbox gets admin alerts.
- Logs: `docker compose logs --tail 30 scheduler`; a failed sweep prints `job sweep_<name> failed (<ExceptionType>)`, a missing provider prints `no mail provider is configured: mail stays in the outbox`.
- **The worker still runs the same mail send and hourly housekeeping.** Two processes doing them is safe: sweeps are idempotent deletes, and the sender takes each row under `FOR UPDATE SKIP LOCKED` and re-checks `pending`. The one cosmetic effect: the day's mail quota is counted at the start of each pass, so two senders can overshoot `MAIL_DAILY_CAP` by at most one batch. Stop the worker's copy in a follow-up once the scheduler has been seen working.
- A slow Resend call delays the dispatch tick (up to 25 mails at 15 s each in the worst case); see Follow-ups.
- Roll back: revert the PR; no migration, nothing to undo. The worker keeps doing the work either way.

## Follow-ups
- **Remove the worker's copies** (`worker.send_mail` in `background_jobs`, `maintenance.run_hourly` in `hourly_jobs`) once the scheduler is seen sending mail and sweeping in production; adapt `test_admin`, `test_mail`, `test_mail_upgrade`, `test_workspace`, `test_maintenance`.
- **Mail on the dispatch loop can stall a join.** With C12 on that loop, a hung provider could hold a tick for minutes (`BATCH` 25 mails, 15 s timeout each). Decide before C12 whether the mail job moves to a third loop or a thread, or the batch shrinks on the scheduler.
- `pubsub_inbox` (7 days) registers in I2.
- C12, C13 and C14 are the first callers of `notify_user` and `notify_admins`; the user-facing alert wording belongs to them.
- The "circuit breaker for refusals per egress address" (checklist, join-refusal handling) needs `notify_admins` and is now unblocked.
