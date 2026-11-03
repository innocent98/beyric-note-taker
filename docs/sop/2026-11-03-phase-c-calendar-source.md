# SOP: Calendar client and the `CalendarSource` protocol (C5)

## What shipped
Task **C5** of `docs/superpowers/plans/2026-11-03-pilot-accounts-calendar.md`: the protocol every calendar source implements (`app/calendar_source.py`) and its Google implementation (`app/gcal_client.py`): `GoogleSource` (`sync`, `get_event`) and `AccessTokenCache`. Branch `feature/c5-calendar-source`, one PR (number in the PR list). Nothing calls it yet: no route, no scheduler, no behaviour change for a user. **Not verified against real Google:** every response body in the tests is synthetic (shaped from Google's documented fields), and the plan's live step has not been done (see Follow-ups).

## Why
The sync loop (C8, C8b), the join runner and the 3-minute re-check all need "give me this connection's events" and "give me this one event now", and they must keep working when the second source (a pasted ICS link, C21 to C23) arrives. A protocol with three typed errors lets the loop treat Google and ICS the same way, and lets the failure cases that lose meetings (a half-read calendar, an expired token, a revoked grant) be decided once, here, and tested.

## How
| Decision | Why |
|---|---|
| `SyncBatch(raw_events, next_sync_token, complete, full)` with `complete=False` implying `next_sync_token=None` (enforced in `__post_init__`) | A partly read calendar must not cancel meetings (Review Focus 1) and the token must not move. Making the invalid state unconstructible means a source cannot get it wrong |
| A page that fails after the first returns the events read so far as an **incomplete** batch; a failure on the first page raises | The first page has nothing worth returning. Later pages hold valid events the caller may still upsert, but it must apply no cancellations and keep its token |
| 410 raises `SyncTokenExpired` only for an incremental call; on a full list it is `TransientError` | A full list has no token to expire; "fall back to a full sync" on it would loop |
| 401: drop the cached access token, mint once more, retry once; a second 401 is `AuthRevoked` | An access token can die early (Google revokes, clock skew); one retry is cheap, two means the grant is bad |
| 403 `insufficientPermissions` is `AuthRevoked`; every other 403 (rate limit, quota, `accessNotConfigured`) and 429, 5xx, 3xx, 400 are `TransientError` | Reconnecting only helps when the user's grant is the problem. Project-level problems retry with backoff and surface after the loop's 30 minutes of failure |
| Refresh `invalid_grant` is `AuthRevoked`; `invalid_client` and other token-endpoint errors are `TransientError`; a vault that cannot decrypt is `AuthRevoked`, a vault with **no key** is `TransientError` | A missing server key must not make every user reconnect |
| `AccessTokenCache`: memory only, per connection id, refresh five minutes before expiry, one mint per connection under a lock, tokens held as `vault.Secret`, repr `<redacted>`, pickling refused. A transient refresh failure inside the early window reuses the old token while it has more than 30 s left | The five-minute margin should not turn a Google blip into an outage |
| Hosts: two URL constants (`oauth2.googleapis.com/token`, `www.googleapis.com/calendar/v3/calendars/primary/events`); event ids are quoted into one path segment (`.`, `..` and empty refused); a `nextPageToken` is only a query value; redirects not followed; `trust_env=False` | No URL is taken from event data or Google's answers, so the client cannot be steered at another host. The ICS fetcher's IP pinning is not needed because the hosts are fixed |
| Timeouts: connect 5 s, read 15 s, write 10 s, pool 5 s, on every call | A hung Google call frees the worker; the loop retries |
| Failure messages carry an HTTP status or an exception type only (`from None`), never a body, URL, token or event data | Event titles and Zoom passcodes live in bodies; httpx exception text can hold the URL |
| Page cap `MAX_PAGES = 40` of 250 events: past it the batch is incomplete | A runaway chain cannot loop forever |

Rejected: Google's `fields` parameter to ask for fewer attributes (it would save bandwidth and keep descriptions out of memory, but it cannot be checked until the live step and a wrong field list would break `parse_event` silently; do it after the captures); a `Retry-After` sleep inside the client (the scheduler owns backoff); passing a callable clock to `GoogleSource` (only the cache has expiry logic).

## What's involved
| Path | Change |
|---|---|
| `app/calendar_source.py` | `CalendarSource` (runtime-checkable Protocol), `SyncBatch`, `CalendarSourceError` base, `SyncTokenExpired`, `AuthRevoked`, `TransientError` |
| `app/gcal_client.py` | `GoogleSource`, `AccessTokenCache`, constants `WINDOW_BEFORE` (1 h), `WINDOW_AFTER` (14 days), `PAGE_SIZE`, `MAX_PAGES`, `REFRESH_EARLY`, `TIMEOUT` |
| `tests/fakes/google.py`, `tests/fakes/__init__.py` | `FakeGoogleCalendar` (token refresh, `events.list` with page and sync tokens, `events.get`, scripted failures), in its own section so C3's fake can sit beside it; **synthetic** bodies |
| `tests/test_gcal_client.py` | 101 tests |
| `docs/checklist/master-checklist.md` | C5 sub-items |

Uses the existing `app/vault.py` (decrypt with purpose `refresh_token`, owner = user id), `google_oauth.TOKEN_URL`, `CalendarConnection` and the C6 fixtures. No new settings, no migration, no `compose.yaml` change. Uses `google_client_id` and `google_client_secret` (already present).

## Verification
- Tests first; the suite then passed, and mutations were run against it to prove it can fail: five-minute margin removed, incomplete batch given a token, 410 mapped on a full list, `trust_env` on, event id left unquoted, `invalid_grant` mapped to transient. Each broke the test named for it.
- Covers the plan's list (full list with page tokens, incremental without time bounds, 410, `invalid_grant`, 5xx and timeouts, incomplete chain, `events.get` 404 and 410 giving `None`, cache expiry, redacted repr) plus: only Google hosts over https, a timeout on all four stages of every call, no redirect followed, a hostile `nextPageToken` and event ids, no access token, refresh token or client secret in `caplog` (DEBUG, success and seven failure paths), in exception text or in a traceback chain, raw events parsing with `parse_event`, eight threads minting once.
- Local checks: ruff check and format on the pinned 0.17.3, `pytest tests/test_gcal_client.py`. The full suite runs on CI (no shared code was changed).
- **Not verified:** a real `events.list` or `events.get`, that `calendar.events.readonly` covers both calls (spec 7.1's assumption), real 410, 403 and refresh error bodies. The reason strings (`fullSyncRequired`, `insufficientPermissions`, `rateLimitExceeded`) are from Google's documentation.

## Operate / roll back
Nothing is wired in, so there is nothing to operate. Reverting the PR removes two modules and their tests; no data, migration or setting depends on it.

## Follow-ups
- **Live step (needs C3 and Adebayo):** with the Gmail test account connected, one real `events.list` and one `events.get` using only `calendar.events.readonly`; commit redacted bodies to `docs/spike/pilot-calendar-captures/`; replace the synthetic bodies in `tests/fakes/google.py` and the fixtures with them. If `events.get` refuses the scope, stop and tell the lead (re-check would need `calendar.readonly`, a scope change).
- After the captures, consider the `fields` parameter to keep descriptions and attendee addresses out of memory.
- C3 (connect) and C4 (disconnect) should call `AccessTokenCache.forget(conn.id)` on disconnect; C8b should turn `AuthRevoked` into `needs_reconnect` and `TransientError` into `last_error`, and treat an incomplete batch as "upsert, no cancellations, keep the token".
- The cache is per process. The scheduler runs one process, so one cache; a second process would mint its own tokens (harmless).
- `tests/fakes/google.py` is also touched by C3: expect an add/add merge conflict on that file and keep both sections.
