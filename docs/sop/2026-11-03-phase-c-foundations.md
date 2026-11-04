# SOP: Phase C foundations (calendar tables, token vault, event parsing, join rule, link fetcher)

## What shipped
The first Phase C wave, five tasks from `docs/superpowers/plans/2026-11-03-pilot-accounts-calendar.md` that need no live Google account: **C1** migration `0010` and models for calendar connections, events and scheduled joins; **C2** the AES-256-GCM token vault; **C6** Google event parsing; **C7** the `will_join` rule function; **C20** the SSRF-safe fetcher for pasted calendar links. Branch `feature/phase-c-foundations`, one PR (number in the PR list). Nothing here is reachable from a route yet: no endpoint, no scheduler, no behaviour change for a user. **C2 and C20 are security-critical** (token encryption, SSRF) and want the Opus review before merge.

## Why
Phase C joins the user's calendar meetings. These five pieces are the parts the later tasks (connect flow, calendar client, materialiser, scheduler, ICS source) stand on, and none of them needs Phase A's routes or a Google account, so they can be built and tested first.

## How
| Task | Decision |
|---|---|
| C1 | Additive only, so it is safe while the pilot is up. Three tables as spec 5.1. `scheduled_joins` has a **partial unique index** on `(user_id, platform, native_id)` where state is `dispatching`, `no_capacity` or `joined`: one live join per user per meeting code, and a recurring Meet code is free again the moment its join is `completed`. It is per user on purpose (a global index would let one account block another's join by registering the same link first), so the index itself would let two users each hold a live join on one code (the bot placement refuses the second, see `docs/sop/2026-11-04-phase-c-place-bot.md`) (`BOT_MAX_LOCAL`, formerly `MAX_ACTIVE_BOTS`, still caps total bots; **C11 found that this is not true with one shared Vexa key** (Vexa keeps one bot per meeting code), so a second account in a live meeting gets 409 `meeting_has_note_taker`; sharing waits for C12 or per-user keys); C12 must decide it. `(user_id, calendar_event_id)` is unique (one row per event, revived not duplicated). Deleting a user cascades to all three; deleting a calendar event row leaves its join (for "Not recorded"); deleting a meeting or template nulls the join's link |
| C2 | `nonce(12) \|\| ciphertext+tag`, authenticated data `purpose:owner`, key from `TOKEN_ENC_KEY` (base64, 32 bytes). The authenticated data is `json([purpose, owner])` with `purpose` from the fixed `PURPOSES` tuple (`refresh_token`, `ics_url`, `host_api_key`; a new kind is added there on purpose) and `owner` an int user id or a str name, so no pair of values collides and an int owner differs from the same text as a str. Rotation without a re-encrypt run is impossible by design: `TOKEN_ENC_KEY_VERSION` names the current key, `version - 1` is `TOKEN_ENC_KEY_PREVIOUS`. `VaultUnavailable` is raised on use, never on import. `Secret` has `repr`/`str` `<redacted>` and refuses pickling. `check_start` also refuses, in every environment, a key that is set but not 32 bytes of base64, a version below 1, and a previous key that equals the current one or is set at version 1; in production it refuses no key when `GOOGLE_CLIENT_ID` is set **or `CALENDAR_ENABLED` is true** (new flag, default false, so today's deploy is unaffected) |
| C6 | Link order is spec 7.5: video entry points, `hangoutLink`, then the location, then the description; the first link that `parse_meeting_url` (or the Teams recogniser) accepts wins. The offset in `dateTime` is the truth; `timeZone` is used only when a time has no offset, and a time with neither is an `EventParseError` (a guessed hour would send the bot at the wrong time). `meeting_url` is the canonical link from `vexa.canonical_meeting_url` (https, only the Zoom `pwd` kept, no tracking parameters), is the only field that may hold a Zoom passcode, and is `repr=False`. `organizer_same_org` is the one fact kept from the organiser's address (`parse_event(raw, hosted_domain=...)`). A cancelled stub with no times parses to `NO_TIME` (epoch) for both ends: only its id and status matter |
| C7 | Event facts first (cancelled, all-day, not a normal event, ended, too long, declined, no usable link), none of which an override can change; then the user's override (beats the rule both ways); then the rule. `organizer` treats an unknown organiser as not joined (`not_organizer`). **Under `all`, an unanswered invite (`needsAction`) joins only when the user is the organiser or the organiser is in the user's own Workspace domain; otherwise `not_accepted`** (later shown as "Not joining: you haven't accepted", with the one-click override). `accepted` and `tentative` join as before. "Same domain" is the connection's `google_hosted_domain` (exact match, case-insensitive, never a subdomain), so a personal Google account or a pasted link never has one and `gmail.com` is never "the same organisation". `now` and the Teams switch are arguments, so a rule change can re-run it over stored rows |
| C20 | Defences in order: https only (`webcal://` is read as https; surrounding whitespace trimmed; at most 2,048 characters) and strict DNS-name host, host on `ICS_ALLOWED_HOSTS`, resolve **once** and refuse the whole answer if any address is not public, connect to the resolved IP with the original `Host` and SNI, no redirects, no proxy from the environment, 2 MB cap measured after decompression, 3 s connect, and a hard 10 s wall clock for everything after the URL check (the name lookup included): the fetch runs in a worker thread the caller waits on for at most 10 s, so a hung lookup or a server that never sends headers frees the caller at the limit. Body must start `BEGIN:VCALENDAR`. Any unexpected error inside the fetch becomes `unreachable` with only the exception type logged. The reason and the host are the only things logged. httpx logs the full request URL at INFO, so a thread-local filter drops its records while a fetch runs |

Rejected: resolving twice (once to validate, once inside httpx) because the second answer can differ; `follow_redirects=True` with a per-hop check, because a redirect to a pinned-IP host would need the whole check again for no benefit (any 3xx is `redirect`); trusting `ipaddress.is_global` alone, which calls multicast global.

## What's involved
| Path | Change |
|---|---|
| `alembic/versions/0010_calendar.py`, `app/models.py` | `CalendarConnection`, `CalendarEvent`, `ScheduledJoin` and their enum tuples (`JOIN_STATES`, `LIVE_JOIN_STATES`, ...) |
| `app/vault.py`, `app/config.py`, `app/auth.py` (`check_start`), `.env.example`, `pyproject.toml` | the vault, `token_enc_key`, `token_enc_key_previous`, `token_enc_key_version`, `cryptography` dependency |
| `app/gcal_events.py` | `ParsedEvent`, `parse_event`, `EventParseError`, `NO_TIME` |
| `app/gcal_rules.py`, `app/config.py` | `JoinSettings`, `JoinDecision`, `will_join`, `teams_join_enabled` (default false) |
| `app/ics_fetch.py`, `app/config.py`, `.env.example` | `fetch_ics`, `IcsRejected`, `REASONS`, `reason_text`, `ics_allowed_hosts` |
| `tests/test_vault.py`, `test_gcal_events.py`, `test_gcal_rules.py`, `test_ics_fetch.py`, `tests/fixtures/gcal/*.json`, `tests/test_models.py`, `tests/test_migrations.py` | tests |

Differences from the plan text, all deliberate:
- `ParsedEvent.organizer_same_org` and `calendar_events.organizer_same_org` (the unanswered-invite rule needs it).
- `calendar_events.source_updated_at` replaces the spec's `google_updated_at` (same reason as `source_event_id`: the ICS source needs no rename).
- `calendar_events.all_day` and `event_type` are stored (not in spec 5.1) so a rule change can re-run `will_join` from the row alone.
- `calendar_connections.refresh_token_enc` and `key_version` are nullable: an ICS connection has no refresh token (C22 adds its own encrypted link column and can reuse `key_version`).
- `scheduled_joins.skip_reason` has no check constraint: the runner (C12) owns the vocabulary, and `cancelled` rows carry reasons the spec's table does not list.
- No separate `calendar_events (user_id, source_event_id)` index: the unique `(connection_id, source_event_id)` covers every lookup the plan names.
- C20 has a ninth reason, `unreachable` (DNS failure, refused connection, TLS error), because none of the eight in the plan fits them. C22's per-reason tests should iterate `app.ics_fetch.REASONS`.
- `test_alembic_has_one_head` is the existing `test_single_head`.

## Verification
- Tests first, watched failing (missing modules or models), then green. Local fast checks: ruff check and format on the pinned 0.17.3; `test_models`, `test_migrations` (including `test_migration_matches_models`, which compares the migration to the models), `test_vault`, `test_gcal_events` (45), `test_gcal_rules` (692, the full decision matrix), `test_ics_fetch` (104); `test_web` and `test_edge` because `check_start` changed. The full suite runs on CI.
- Migration: one head; upgrade and downgrade round trip on a scratch Postgres 17 container seeded with the admin and two meetings (the meetings survive both ways).
- C20 against the real network, one-off script (not a test): fetching a made-up path on `calendar.google.com`, `outlook.live.com` and `outlook.office365.com` connected to the pinned IP, passed TLS verification against the real name through SNI, and got the real server's 404 / 400 back (`bad_status`). That proves the IP-pinning, `Host` and SNI approach works with the real certificates; it does not prove that those three hosts are where real private links live (that is C19, which needs Adebayo's accounts). No log line carried the path.
- **Not verified:** a real Google event payload (fixtures are synthetic, shaped from Google's documented fields; C5's live capture will replace them), a real ICS feed, `will_join` against stored rows (C8).

## Review changes (Opus review of the first push, head `1e2b928`)
| Finding | Change |
|---|---|
| A stranger's invite with a link could send the bot under rule `all` | `not_accepted` rule above. **Decision for Adebayo:** the default is now that an invite you have not answered does not auto-join unless you organise it or it comes from your own Workspace domain; you can still switch it on per meeting. The alternative is the old behaviour (join every unanswered invite), which lets anyone who can invite you take the only bot slot |
| Global live-code index let one user block another | index includes `user_id` (see C1 above) |
| `TOTAL_TIMEOUT` was only checked between chunks; the lookup had none | hard wall clock in a worker thread; tests with a server that never sends headers and a resolver that hangs |
| `InvalidURL` could escape `fetch_ics` | `build_request` is inside the `try`; URL length capped at 2,048 |
| Raw URL stored | canonical URL stored |
| Ambiguous vault AAD; key rules | JSON AAD with a fixed purpose list; version and previous-key checks; `CALENDAR_ENABLED` |
| `webcal://` | read as https |

## Operate / roll back
- Migration `0010` is additive. Roll back with `alembic downgrade 0009`: it drops the three tables and any connection, event or scheduled join in them; meetings are untouched.
- **Before this deploys, a production server with `GOOGLE_CLIENT_ID` set needs `TOKEN_ENC_KEY` in `.env`** (`openssl rand -base64 32`; keep a copy, losing it means users reconnect their calendars). Without it `web` refuses to start. Append it without printing it, then recreate the services:
  ```bash
  printf 'TOKEN_ENC_KEY=%s\n' "$(openssl rand -base64 32)" >> .env
  docker compose up -d --force-recreate web worker
  ```
- New environment variables: `TOKEN_ENC_KEY` (as above), `TOKEN_ENC_KEY_PREVIOUS`, `TOKEN_ENC_KEY_VERSION` (1), `TEAMS_JOIN_ENABLED` (false), `ICS_ALLOWED_HOSTS` (the three defaults). The key must live only in the server `.env`, not in the database or a backup.
- Rotation: new key into `TOKEN_ENC_KEY`, old into `TOKEN_ENC_KEY_PREVIOUS`, raise `TOKEN_ENC_KEY_VERSION` by one, run the re-encrypt script (C9, not written yet), then drop the previous key.

## Incident: the #45 deploy (2026-11-03)
The server `.env` had `GOOGLE_CLIENT_ID` and no `TOKEN_ENC_KEY`, so `vault.check_settings` correctly refused to start `web` and it crash-looped; the site was down about 12 minutes until Adebayo added the key by hand. The PR was merged without a deploy note. A second fault made it worse: **`deploy/deploy.sh` did not roll back.** `docker compose up -d` exits non-zero when a dependency is unhealthy, and under `set -e` the script ended there, before the health check and the rollback, leaving `next` in the `Created` state. Fixed in `fix/deploy-rollback-and-token-key`: `up` no longer decides on its own (the health check does), the rollback's own `up` and build are tolerant, stuck `Created` containers are started, and a failed build restores the previous checkout. Proved by `tests/test_deploy_script.py` (stand-ins for `docker`, `git`, `curl`). Rule from now on: a PR that adds a required environment variable lists it under "Server steps before merge" in its body.

## Follow-ups
- **C4 (disconnect) must delete `calendar_events` and `scheduled_joins` rows explicitly.** `meeting_url` carries the Zoom `pwd` in plaintext in both tables, and `scheduled_joins` outlives its event (`ON DELETE SET NULL`), so the cascade from the connection does not reach it. **C12 should null `meeting_url` once a join is terminal** (`completed`, `failed`, `cancelled`, `skipped`).
- **Phase I (instant-Meet) joins have no event id**, and `uq_scheduled_joins_user_event` ignores nulls, so they need their own idempotency key (for example the Meet space or conference record name) before I4 inserts them.
- **C12 must decide how two users in one meeting share a bot** (see the C1 row).
- C9 (re-encrypt script) uses `vault.needs_reencrypt`.
- C19 must confirm the real feed hosts; `ICS_ALLOWED_HOSTS` defaults are unconfirmed.
- C8 must catch `EventParseError` per event so one odd event does not fail a sync, and treat a `NO_TIME` event only as a cancellation.
- When Teams (T1) lands, `ParsedEvent.native_id` for Teams stops being `None` and `scheduled_joins.native_id` (NOT NULL) gets its value there.
- The privacy "Security" row waits for C3, the first task that stores a token.
