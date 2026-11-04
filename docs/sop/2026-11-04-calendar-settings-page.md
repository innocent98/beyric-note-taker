# SOP: Calendar settings page and `GET /api/calendar` (C15a slice 1, Stream D2 partial)

## What shipped
The first slice of plan task **C15a** and the first screen that calls any of Phase C: a read-only `GET /api/calendar` and the page `/settings/calendar` (the page Google's connect callback already redirects to), with a **Settings** link in the sidebar. Adebayo can now connect his Google calendar, see what was synced and disconnect, so the first real Google test can run. Branch `feature/calendar-settings-page`, one PR (see the PR list). No migration, no new setting, no nginx or compose change. **Nothing is switched on:** every calendar route still answers 404 `calendar_unavailable` while `CALENDAR_ENABLED` is false (production), and no bot is sent by anything here.

## Why
After C3 (connect), C4 (disconnect), C5 (client) and C8/C8b (materialiser, sync) were merged dark, no page called any of it. The connect callback redirected to a page that did not exist.

## How
| Piece | Decision | Why |
|---|---|---|
| One read answers the page | `GET /api/calendar` returns `connected`, `state`, `state_text`, `google_email`, `last_sync_at`, `last_error` and `upcoming` (7 days). `CalendarOut` is the shape the rest of C15a (rule, template, instant-Meet switch) extends; the plan's separate `GET /api/calendar/upcoming` (7 days plus the last 24 hours, for the Meetings page) is not built | The settings page needs everything at once; fewer round trips. The Meetings page's list is a different slice |
| The server owns the words (C15a's rule) | New `app/calendar_text.py`: `join_text(decision)` maps every reason `gcal_rules.will_join` can give to a sentence (`Will join`, `No video link`, ...), `state_text`, `error_text`, `platform_label`. `last_error` (`ExcType: message` as `gcal_sync` records it) becomes one of two plain sentences; no type, status or message ever reaches the browser. An unknown reason is `Will not join`, never a code | The frontend never invents reasons. This is the first slice of the module: the full `state_text(state, reason, ctx)` for scheduled joins (hours used, duplicate, not recorded...) comes with the rest of C15a |
| What an upcoming item carries | `id`, `title`, `starts_at`, `ends_at`, `platform`, `platform_label`, `will_join`, `will_join_text`. No attendees, description, location, **no meeting link** (a Zoom link can hold the passcode), no token | A pydantic model with those fields only: there is nothing to leak into. A test pins the key set and searches the text |
| The answer comes from the stored rows | `will_join` runs over the `calendar_events` row with the user's rule (`user_settings.auto_join_rule`, default `all` when there is no row), the row's `override` and `TEAMS_JOIN_ENABLED`, at request time. It is not read from `scheduled_joins` | The page shows what the rules say now, also for events with no join row (no link, declined) |
| Window | events that have not ended and start within 7 days, soonest first, at most 100, cancelled left out, own connection and user only | An in-progress meeting is still "upcoming" |
| Connection states | `not_connected` (no row, `disconnected`, or a Meet-only connection with no calendar permission), `connected`, `degraded`, `needs_reconnect`. `connected` is true for the first two. `google_email` is returned also for `needs_reconnect` | The person must know which account to fix. Nothing sets `degraded` / `needs_reconnect` yet (C13) |
| Dark like the others | `_enabled(...)` first: 404 `calendar_unavailable` when the flag is off or there is no Google client. The page answers that 404 itself ("Google Calendar is not available yet.") instead of the not-found page | The sidebar link is visible to every user, so the page must not look broken while the feature is off |
| The page | `web/app/settings/calendar/page.tsx` (server: `requireOwner`, the read, the callback's query) and `web/components/app/calendar-settings.tsx` (client). Designer's Settings screen, calendar section only, in the existing components. Connect posts through `lib/api/client.ts` and navigates to `authorize_url`; if `show_unverified_notice` it explains Google's warning first. Disconnect asks inline and says what is deleted (spec 12.2). The callback's `?calendar=` / `?calendar_error=` is read **server side in `lib/calendar-notice.ts`** (a function in a `"use client"` file cannot be called from a server page: the first browser run failed that way, and a test now guards it), shown once, and the query is removed from the address | Pages import values only from plain modules |
| Sidebar | The designer's shell has "Settings" under Configure, so the shell has a **Settings** link to `/settings/calendar` (the only settings page that exists) | Only link that was designed. The link is shown while the calendar is off too |
| Rejected | Polling for the first sync (a reload shows it); local time (see the designer gaps); the switch per meeting (needs the override route); unifying the Meetings-page list | Each is a later slice |

## What's involved
| Path | Change |
|---|---|
| `app/calendar_api.py` | `GET /api/calendar` (`calendar_state`), `CalendarOut`, `UpcomingEvent`, `_calendar_connection`, `_upcoming`, `UPCOMING_DAYS`, `UPCOMING_LIMIT` |
| `app/calendar_text.py` | new: the plain words |
| `docs/openapi.json`, `web/lib/api/schema.d.ts` | regenerated (`scripts/export_openapi.py`, `npm run gen:api`); the schema also gains the connect and disconnect shapes that earlier PRs never generated |
| `web/app/settings/calendar/page.tsx`, `web/components/app/calendar-settings.tsx`, `web/lib/calendar-notice.ts` | new page, view, query reader |
| `web/lib/{routes,format}.ts`, `web/proxy.ts`, `web/components/app/shell.tsx`, `web/components/ui/icon.tsx`, `web/styles/pages.css` | `/settings/calendar` in `NEXT_PAGES` and the proxy matcher, `/settings` in `APP_PATHS` (noindex, disallowed in robots), time helpers, Settings link, calendar icon, `cal-*` styles |
| `web/lib/api/{client,types}.ts` | `api.calendar.get/connect/disconnect` |
| `scripts/capture_calendar_state.py`, `docs/fe-captures/calendar-state/` (19 files) | captures (the real sync job over `FakeGoogleCalendar`) |
| `tests/test_calendar_api.py` (35), `web/tests/{calendar-settings,calendar-page}.test.tsx` (45), `web/tests/{api-client,helpers}` | tests |
| `docs/fe-integration-guide-calendar-settings.md`, this file, `docs/checklist/master-checklist.md` | docs |

The enumeration tests in `tests/test_api.py` pick the new route up by themselves (every route needs a login, reads need no CSRF header, the OpenAPI file matches the routes); no list there needed a change.

## Verification
| Check | Result |
|---|---|
| `tests/test_calendar_api.py` | 35 tests on a real Postgres 17 container, written first; the module import failed first (no `calendar_text`). Mutation checks (each broke a test): cancelled events not excluded, the `ends_at > now` bound, the 7-day bound, the stored `override` ignored. Two mutations survived by design: dropping the `connection_id` filter (the `user_id` filter beside it already isolates users: a second guard) and dropping the `order_by` (Postgres reads the `(user_id, starts_at)` index in order for rows this few; the ordering test still guards the contract) |
| `tests/test_api.py`, `tests/test_calendar_connect.py`, `tests/test_calendar_api.py` | 261 pass (the OpenAPI file test failed until `export_openapi.py` ran, as designed) |
| `ruff check .`, `ruff format --check .` (pinned 0.17.3, own venv) | clean |
| `npm run lint`, `npm run typecheck`, `npx vitest run` in `web/` | clean; 387 tests (45 new: every state, every redirect message, disconnect ask and failure, double press, connect with and without the unverified notice, the server half, routes, sidebar). Mutation checks on the view: five of six broke a test; the sixth (the `busy` ref on Disconnect) survives because the disabled button already stops a second press |
| Capture against the running app | `scripts/capture_calendar_state.py` (19 files); the guide's bodies are copied from them |
| Real browser (Browser pane), `next dev` in front of a **real local FastAPI**, real migrations and a Postgres 17 container, Google faked | Passed: connected page with the Settings link current and the callback message removed from the address; Disconnect with its ask, then the real call, then the not-connected page with the permissions note; Connect showing the unverified-app notice and "Continue to Google"; an error redirect; phone width (375 px). This run **found a real bug** the unit tests could not (the server page called a function from a client file), fixed with a plain module |
| **Not verified** | A real Google calendar, a real consent screen and Google's own unverified-app wording (the page describes it, it was not seen); the built production image and nonce CSP for this page (dev server only); Safari and Firefox; the live site; `degraded` and `needs_reconnect` reached by the app itself (C13); a real bot (none is sent) |

## Operate / roll back
- **Server steps before merge: none.** No new variable, no nginx edit, no migration. Merging deploys the web image; the page exists but the API answers 404 `calendar_unavailable` until `CALENDAR_ENABLED=true`, and then the page says "Google Calendar is not available yet."
- **To run the first real test (the lead, with Adebayo, after merge):** `CALENDAR_ENABLED=true` in the server `.env` (the Google client id and secret, `TOKEN_ENC_KEY` and the Google redirect URI `https://notes.beyrictech.com/api/calendar/google/callback` already authorised are required, see `docs/sop/2026-11-03-phase-c-google-connect.md`), recreate `web` and `scheduler`; then open `/settings/calendar`, press Connect, press Allow, wait about a minute, reload. Beware: **with the flag on every signed-in user sees the page, and "Will join" is only the rules' answer: no bot is sent from the calendar until C11 and C12 exist.** Turn the flag on only for the test, or accept that for the pilot users.
- Roll back: revert the PR; the page and route disappear, nothing is migrated. Calendar rows already synced stay (a disconnect deletes them).

## Designer gaps (for the designer to review)
| Gap | What was built |
|---|---|
| Settings is one long screen in the design (calendar, sprints, tasks, language) | Only the calendar section exists. `/settings/calendar` is the Settings link's target until the other cards have backends |
| "Join automatically" rules and the template select | Not built (they are C15a part 2). The page shows no rule and no switch |
| The per-meeting switch in the Upcoming list ("Will join" / "Off" with a toggle) | A read-only answer in a badge (`Will join`, `No video link`, ...); no toggle until the override route exists |
| Upcoming lives on the Meetings page in the design, with "Connect calendar" and the account chip | Built on the settings page under the calendar card, with the account chip. The Meetings-page card and list are not built |
| Connect "Microsoft Outlook" button | Not built (the paste-a-link source is C19 to C24) |
| The calendar line mentions Microsoft Teams | "Beyric reads your calendar and joins meetings that have a Google Meet or Zoom link." until Teams can be joined |
| Connect, disconnect and error states | The designer drew only "Connecting..." and the connected card. Built: the permissions note ("What Google will be asked for": read the events, read only, nothing is written; what is kept: title, time, video link and the number of guests, not guest addresses or descriptions), the unverified-app explainer, the inline "Disconnect Google Calendar?" ask, the "connect again" state, the last failure line, the messages for every redirect error |
| Times | Shown in UTC (a server render and a browser must agree); local time needs a decision |
| Empty and waiting states | "Waiting for the first update. This usually takes a minute.", "Nothing in the next 7 days.", "Your meetings appear here after the first update." |
| The recording reminder | The designed line "Tell participants that this meeting will be recorded." under the list when a meeting will join |

## Follow-ups
- **The rest of C15a:** `PUT /api/calendar/settings` (rule, template, instant-Meet switch with the 409 `consent_required`), the override route (409 `not_joinable`), `GET /api/calendar/upcoming` for the Meetings page, the full `calendar_text.state_text` for scheduled-join states, and `_reconcile` on a rule or template change (from the sync SOP). The plan's `test_will_join_text_carries_the_tell_participants_reminder` belongs there: here the reminder is a page line, not part of `will_join_text`.
- **C13** must set `degraded` and `needs_reconnect`; until then a revoked grant shows only the `last_error` sentence while `state` stays `connected`.
- **The privacy sentence** ("you can disconnect in the app") the C4 SOP and the checklist say goes back with this button: the legal pages are outside this slice (other sessions are in them), so the lead should add it when merging, and re-check the Security row wording.
- Auto-refresh after connecting (the first sync takes about a minute), local time, and the Teams wording once Teams works.
- The first real Google run (Adebayo's Gmail) will show whether the unverified-app explainer matches what Google really says, and whether real events (conference data, all-day events, recurring series) read the way the synthetic ones did.
