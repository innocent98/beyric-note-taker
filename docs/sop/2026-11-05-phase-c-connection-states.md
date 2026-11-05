# C13 and C14 (backend): calendar connection states and the disconnect notice

**What shipped:** a calendar connection now moves `connected` -> `degraded` -> `needs_reconnect` and back, driven by sync failures; a dead connection skips the user's pending calendar joins with a recorded reason and tells the user once in the bell. Branch `feature/c13-c14-calendar-health` (PR in the lead's queue). Migration `0020`.

## Why
After C12 the bot joins calendar meetings by itself. A revoked or broken Google connection stopped joins silently: the sync only wrote `last_error`.

## How
| Situation | Result |
|---|---|
| Sync fails with `AuthRevoked` (`invalid_grant`, missing scope) | `needs_reconnect`, reason `revoked`, or `expired` when the Testing 7-day expiry has passed and `GOOGLE_OAUTH_TESTING` is on |
| Stored refresh token cannot be decrypted (`RefreshTokenUnreadable`, a subclass of `AuthRevoked`) | `needs_reconnect`, reason `error`, plus an admin alert (`admin.calendar_token_unreadable`, once per connection) |
| Any other failure (5xx, network, partial read) | `error_since` set on the first one; after 30 minutes `degraded` + one bell entry. Joins are NOT paused |
| A sync works | `error_since` cleared; `degraded` -> `connected` |
| Re-check in the join runner gets `AuthRevoked` | same as a failed sync; the row waits one tick and the gate skips it |
| Connect again (`calendar_connect`, a new refresh token) | `revive_on_reconnect`: this connection's joins that are `skipped/needs_reconnect` with `deadline_at` in the future go back to `scheduled`; older ones stay skipped |

On `needs_reconnect`: this connection's `scheduled`/`no_capacity` joins with a live deadline become `skipped` with `skip_reason='needs_reconnect'` (the reason the runner already uses); the function returns the count starting within 24 hours. One bell entry (`calendar.disconnected`, dedupe `cal_disc:{connection_id}:{connected_at}`) using the Settings card's existing sentence from `calendar_text.RECONNECT_TEXT`; degraded uses `DEGRADED_TEXT`. More than one user needing a reconnect within an hour sends one admin alert per hour (`admin.calendar_disconnects`).

Decision: the 30-minute clock needs the first failure's time, hence `calendar_connections.error_since` (migration 0020), not `last_error_at`, which moves on every attempt.

## Involved
`app/calendar_states.py` (new: `mark_needs_reconnect`, `mark_degraded`, `mark_healthy`, `on_sync_failure`, `revive_on_reconnect`, `expiry_warning`, `reason_for`); `app/gcal_sync.py` (`_record_error`, success path); `app/join_runner.py` (re-check); `app/calendar_connect.py` (revive, clear `error_since`); `app/calendar_source.py` + `app/gcal_client.py` (`RefreshTokenUnreadable`); `app/models.py`; `alembic/versions/0020_calendar_error_since.py` (down_revision `0018`: 0019 is W0.4, renumber when it merges). No API shape change, so `docs/openapi.json` and the FE types are unchanged.

## Designer gaps (nothing invented; decided by Adebayo 2026-11-05 "build without them")
- The "calendar disconnected" **banner** and its Reconnect action are not in `docs/ui/`. Not built. The Settings card already shows `needs_reconnect` with "Connect again".
- The **disconnect email** is not drawn in `Emails.dc.html` (only "Could not join" is). **No email is sent**; the user hears through the bell only. The plan's `calendar_disconnected` template and `notify_email` test are therefore deferred.
- The 24-hour **expiry warning** (`expiry_warning`) exists and is tested but is wired to nothing: it needs the banner.
- Banner text "counts next-24-hour meetings": the count is returned by `mark_needs_reconnect` and not shown anywhere yet.

## Verification
`tests/test_calendar_states.py` (12 tests, real Postgres, `FakeGoogleCalendar`): revoked grant, expired vs revoked reason, transient under 30 minutes, degraded after 30 with one bell and joins kept, connected -> degraded -> needs_reconnect -> revive, revive only live deadlines, decrypt failure + admin alert, one notification per disconnect and no mail row, 24-hour count, admin wave, expiry warning with the flag on and off. Touched suites (sync, client, runner, connect, API, accounts, migrations): 600 passed. Not tested live: a real Google revoke.

## Operate / roll back
Deploy runs migration 0020 (nullable column, no backfill). Roll back with `alembic downgrade 0018`; the code before this PR ignores the column.

## Follow-ups
Banner and Reconnect action; the disconnect email (both need the designer); wire `expiry_warning`; a connect-flow integration test for revive.
