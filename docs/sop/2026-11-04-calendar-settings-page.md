# SOP: Calendar on Settings and Meetings, and `GET /api/calendar` (C15a slice 1, Stream D2 partial)

## What shipped
The first slice of plan task **C15a** and the first screens that call any of Phase C, built to the designer's `Beyric Note Taker.dc.html`: a read-only `GET /api/calendar`; the **Settings** screen at `/settings` with its Calendar section (connect, connected row, Disconnect, the rule shown read only) and the sidebar **Settings** entry; and on the **Meetings** screen the "Join meetings automatically" band (not connected) and the **Upcoming** list (connected). The connect callback now returns to `/settings`. Adebayo can connect his Google calendar, see what was synced and disconnect, so the first real Google test can run. Branch `feature/calendar-settings-page`, PR #66. No migration, no new setting, no nginx or compose change. **Nothing is switched on:** every calendar route still answers 404 `calendar_unavailable` while `CALENDAR_ENABLED` is false (production), the Meetings page then draws no calendar part at all, and no bot is sent by anything here.

## Why
After C3 (connect), C4 (disconnect), C5 (client) and C8/C8b (materialiser, sync) were merged dark, no page called any of it, and the connect callback redirected to a page that did not exist.

## How
| Piece | Decision | Why |
|---|---|---|
| One read feeds both screens | `GET /api/calendar` returns `connected`, `state`, `state_text`, `google_email`, `last_sync_at`, `last_error`, `rule`, `rule_text` and `upcoming` (7 days). `CalendarOut` is the shape the rest of C15a (rule and template changes, the instant-Meet switch) extends; the plan's separate `GET /api/calendar/upcoming` (7 days plus the last 24 hours) is not built | Settings and Meetings need the same state; fewer round trips |
| The server owns the words (C15a's rule) | New `app/calendar_text.py`. Each upcoming item has `will_join_text`, **always the designer's three words** (`Will join`, `Off`, `Cannot join`: Off when the rule or the person's own switch says no and they could change it, Cannot join when nothing they switch changes it) and `reason_text`, the plain sentence behind a no (the designer's own hint for an event without a link). `rule_text` is the designer's label ("Joining all meetings"...). `last_error` (`ExcType: message` as `gcal_sync` records it) becomes one of two plain sentences; no type, status or message reaches the browser. An unknown reason is `Will not join`, never a code | The frontend never invents reasons, and the row's words are the design's |
| What an upcoming item carries | `id`, `title`, `starts_at`, `ends_at`, `platform`, `platform_label`, `attendee_count` (a number), `will_join`, `will_join_text`, `reason_text`. No attendees, description, location, **no meeting link** (a Zoom link can hold the passcode), no token | A pydantic model with those fields only: there is nothing to leak into. A test pins the key set and searches the text |
| The answer comes from the stored rows | `will_join` runs over the `calendar_events` row with the user's rule (`user_settings.auto_join_rule`, `all` when there is no row), the row's `override` and `TEAMS_JOIN_ENABLED`, at request time, not from `scheduled_joins` | Also right for events with no join row (no link, declined) |
| Window | events that have not ended and start within 7 days, soonest first, at most 100, cancelled left out, own connection and user only | An in-progress meeting is still "upcoming" |
| Connection states | `not_connected` (no row, `disconnected`, or a Meet-only connection), `connected`, `degraded`, `needs_reconnect`. `google_email` is returned also for `needs_reconnect` | The person must know which account to fix. Nothing sets the last two yet (C13) |
| Dark like the others | `_enabled(...)` first: 404 `calendar_unavailable`. Settings answers that 404 itself ("Google Calendar is not available yet."); the Meetings page treats it, and any other failure of the read, as "no calendar" and looks as it did before | The Settings link is visible to every user; the Meetings page must never break because of the calendar |
| Settings, Calendar section | `web/app/settings/page.tsx` is thin (the frame, title, subtitle); the section is its own file, `web/components/app/settings-calendar.tsx`, so other sessions can add sections to the page without colliding. Connect posts through `lib/api/client.ts` and navigates to `authorize_url`; if `show_unverified_notice` it explains Google's warning first. Disconnect asks inline and says what is deleted (spec 12.2). The callback's `?calendar=` / `?calendar_error=` is read **server side in `lib/calendar-notice.ts`** (a function in a `"use client"` file cannot be called from a server page: the first browser run failed that way, and a test guards it), shown once, and removed from the address | Pages import values only from plain modules |
| Meetings, calendar part | `web/components/app/calendar-upcoming.tsx` (`CalendarBand`): the band while not connected (a link to `/settings`), the Upcoming list when connected. Days and times are the viewer's own ("Today", "Tomorrow", "Wed 4 Nov", "14:00"), written in the browser after load (`useSyncExternalStore`, empty cells until then, so server and browser render the same HTML) | The design shows local times; the server does not know the zone |
| Callback return path | `DEFAULT_RETURN_TO` is `/settings` (`app/calendar_connect.py`, its tests, both capture scripts, the connect SOP and guide) | Adebayo's instruction: the page is `/settings`, not `/settings/calendar` |
| Sidebar | The designer's `navConfig` is Templates, Vocabulary, Settings, so the shell has **Settings** (to `/settings`) after Vocabulary | Designed |
| Rejected | The Outlook button and every Outlook word (not shipped); the template select and the rule radios as controls (the settings route does not exist); the per-meeting switch (no override route); polling for the first sync (a reload shows it) | Each is a later slice |

## What's involved
| Path | Change |
|---|---|
| `app/calendar_api.py` | `GET /api/calendar` (`calendar_state`), `CalendarOut`, `UpcomingEvent`, `_calendar_connection`, `_upcoming`, `UPCOMING_DAYS`, `UPCOMING_LIMIT` |
| `app/calendar_text.py` | new: `join_label`, `join_reason`, `rule_text`, `state_text`, `error_text`, `platform_label` |
| `app/calendar_connect.py` | `DEFAULT_RETURN_TO = "/settings"` |
| `docs/openapi.json`, `web/lib/api/schema.d.ts` | regenerated (`scripts/export_openapi.py`, `npm run gen:api`); the schema also gains the connect and disconnect shapes that earlier PRs never generated |
| `web/app/settings/page.tsx`, `web/components/app/settings-calendar.tsx`, `web/components/app/calendar-upcoming.tsx`, `web/lib/calendar-notice.ts` | the screens |
| `web/app/page.tsx` | the calendar read (optional) and `CalendarBand` between the new-meeting form and the list |
| `web/lib/{routes,format}.ts`, `web/proxy.ts`, `web/components/app/shell.tsx`, `web/components/ui/{icon,button}.tsx`, `web/styles/{pages,components}.css` | `/settings` in `NEXT_PAGES` and the proxy matcher and `APP_PATHS` (noindex, robots), event day/time/duration helpers, Settings link, calendar icon, the `tight` button size (the design's 16 px sides), `set-*` and `cal-*` styles |
| `web/lib/api/{client,types}.ts` | `api.calendar.get/connect/disconnect` |
| `web/scripts/{mock-api,parity-shots}.mjs` | the mock replays the calendar captures (`--calendar not_connected|connected|off`); the parity script has the calendar screens and no longer waits for the network to idle (the Meetings list polls) |
| `scripts/capture_calendar_state.py`, `docs/fe-captures/calendar-state/` (21 files) | captures (the real sync job over `FakeGoogleCalendar`); `calendar-connect` captures regenerated for the new return path |
| `docs/ui/parity/after/app-{settings,settings-connected,meetings,meetings-calendar}-{1440,768,390}.jpg` | design on the left, the built page on the right |
| `tests/test_calendar_api.py` (40), `web/tests/{settings-calendar,calendar-upcoming,settings-page,meetings-calendar}.test.tsx`, `web/tests/{api-client,helpers}` | tests |
| `docs/fe-integration-guide-calendar-settings.md`, this file, `docs/checklist/master-checklist.md` | docs |

The enumeration tests in `tests/test_api.py` pick the new route up by themselves; no list there needed a change.

## Parity with the designer's file
Checked as `docs/ui/parity-report.md` describes: the built app (`next build`, standalone server) against a mock API replaying the captures, next to `Beyric Note Taker.dc.html` in Chromium at 1440, 768 and 390 px (`web/scripts/parity-shots.mjs`; images in `docs/ui/parity/after/`), then positions and computed styles of the same labels and boxes diffed.

| Element (1440 px) | Design | Built | Result |
|---|---|---|---|
| Settings title and subtitle, "Calendar" title and description | position, size, weight, colour | same | identical |
| Calendar card | 720 wide, 12 px radius, 1 px border `--border`, header padding 22/22/18 | same | identical |
| "Connect Google Calendar" button | 40 high, 16 px sides, 1 px `--border-strong`, 8 px radius, 14/550 | same after the new `tight` size (18 px sides before) | identical but the text node's `line-height` (`normal` in the design's button, inherited 21 px here: no visible effect) and 2 px of width |
| Connected provider row | 674×65, padding 12/14, 10 px radius, 34 px tile | same | identical box |
| "Disconnect" | red 13/500 text button | same | identical |
| "Join automatically" label and three rule cards | 674×65 each, 12/14 padding, teal border and `--teal-wash` when chosen | same | identical boxes, colours, radius; text lines within 1 px |
| Meetings "Join meetings automatically" band | 1096×80, padding 18/20, 40 px tile, 36 px button | same | identical sizes; the page is lower by the New meeting card's own height difference (see below) |
| Upcoming card header and rule link | 15/600 title, chip, teal 13/550 rule label | same | positions within 1 px |
| Upcoming row | 66 high, padding 12/20, columns 76 px / 1fr / auto, gap 14, 1 px `--border-soft` | same | identical (66 px, same padding, gap, border) |
| Row text lines | day 12, time mono 14/500, title 550, meta 13 with `·` separators, state 12 | same | identical styles; the meta separators are drawn by the existing `meta-line` |
| State text | followed by the 36 px switch | **the switch is not drawn** (no override route): the state text sits 46 px further right | gap listed below |
| Sidebar Settings entry | under Templates and Vocabulary | same | identical |
| 768 and 390 px | the prototype's main app is not responsive (it crushes its own content, see `parity-report.md`) | the engineered phone layout of the app (stacked rows, the date and time on one line above the title) | not comparable, as for every screen |

Seen while comparing, **not caused here:** in the mock the New meeting card is 58 px taller than the design's because the mock has a bot on a meeting ("A bot is already on a meeting..." notice). The Upcoming band sits 83 px below the reminder line in both.

## Verification
| Check | Result |
|---|---|
| `tests/test_calendar_api.py` | 40 tests on a real Postgres 17 container, written first (the module import failed first). Mutation checks (each broke a test): cancelled events not excluded, the `ends_at > now` bound, the 7-day bound, the stored `override` ignored. Two mutations survived by design: dropping the `connection_id` filter (the `user_id` filter beside it already isolates users) and dropping the `order_by` (Postgres reads the `(user_id, starts_at)` index in order for rows this few; the ordering test still guards the contract) |
| `tests/test_api.py`, `test_calendar_connect.py`, `test_calendar_api.py`, `test_gcal_sync*.py` | 344 pass (the OpenAPI file test failed until `export_openapi.py` ran, as designed) |
| `ruff check .`, `ruff format --check .` (pinned 0.17.3, own venv), `export_openapi.py --check` | clean |
| `npm run lint`, `npm run typecheck`, `npx vitest run` in `web/` | clean; 406 tests (the new ones: every state and redirect message, the read-only rules, Disconnect and its ask, double press, connect with and without the unverified notice, the band and Upcoming rows (counts only, no addresses), the day and time words in a chosen zone, the Meetings page with the calendar on, off, and failing, the sidebar order, routes). Mutation checks on the Settings view: five of six broke a test; the sixth (the `busy` ref on Disconnect) survives because the disabled button already stops a second press |
| Capture against the running app | `scripts/capture_calendar_state.py` (21 files); the guide's bodies are copied from them |
| Parity (built app, mock API, Chromium at 1440/768/390 beside the design) | see the table above |
| Real browser, the built app in front of a **real local FastAPI**, real migrations, Postgres 17, Google faked | Passed: Meetings with the Upcoming list (and the worker-stale notice, the band after disconnect); Settings connected with the callback message removed from the address, Disconnect with its ask, then the real call, then the not-connected Settings and the band on Meetings. An earlier run on the first layout found a real bug (a server page called a function from a client file), now guarded by a test |
| **Not verified** | A real Google calendar, a real consent screen and Google's own unverified-app wording (the page describes it, it was not seen); Safari and Firefox; the live site; `degraded` and `needs_reconnect` reached by the app itself (C13); the rule as set through the API (no route); a real bot (none is sent) |

## Operate / roll back
- **Server steps before merge: none.** No new variable, no nginx edit, no migration. Merging deploys the web image; the screens exist, the API answers 404 `calendar_unavailable` until `CALENDAR_ENABLED=true`, the Meetings page then draws no calendar part and Settings says "Google Calendar is not available yet."
- **To run the first real test (the lead, with Adebayo, after merge):** `CALENDAR_ENABLED=true` in the server `.env` (the Google client id and secret, `TOKEN_ENC_KEY` and the redirect URI `https://notes.beyrictech.com/api/calendar/google/callback` already authorised are required, see `docs/sop/2026-11-03-phase-c-google-connect.md`), recreate `web` and `scheduler`; then open `/settings`, press Connect Google Calendar, press Allow, wait about a minute, reload. Beware: **with the flag on every signed-in user sees the band and Settings, and "Will join" is only the rules' answer: no bot is sent from the calendar until C11 and C12 exist.** Turn the flag on only for the test, or accept that for the pilot users.
- Roll back: revert the PR; the screens and the route disappear, nothing is migrated. Calendar rows already synced stay (a disconnect deletes them).

## Designer gaps (for the designer to review)
| Gap | What was built |
|---|---|
| The switch on each Upcoming row | Not drawn (its route is C15a part 2): the state text only, its reason on hover |
| "Join automatically" rules and the "Template for calendar meetings" select | The three rule cards are drawn read only (disabled radios) with the stored rule chosen; the template select is not drawn |
| "Connect Microsoft Outlook" | Not built, no Outlook wording anywhere (the paste-a-link source is C19 to C24) |
| The Calendar sentence names Microsoft Teams, but Teams cannot be joined yet (an event row says "Cannot join: Microsoft Teams meetings cannot be joined yet") | The designer's sentence as designed; **decide whether to name Teams until it works** |
| Not-connected card: what Google is asked for | One paragraph under the button (read-only access to the events, nothing is written, what is kept and not kept) |
| Connecting, errors and what comes back from Google | The designer drew only "Connecting..." and the connected card. Built from the existing components: the unverified-app explainer with "Continue to Google", a message for every redirect error, the "connect again" state with its button, the last failure line, "Waiting for the first update. This usually takes a minute." and the "Last updated" line |
| Disconnect | An inline ask in a red-tinted box (what is deleted, what stays) with Disconnect and Keep connected; the designed text button opens it |
| Upcoming: empty states | "Nothing in the next 7 days." and "Your meetings appear here after the first update." |
| The calendar needing a reconnect | The Meetings screen shows the "Connect calendar" band (the design has no banner: listed as "Calendar disconnected banner" in the checklist) |
| Row without a link | "No meeting link" as the platform (the design's words), "Cannot join" with the design's hint "Add a meeting link to this event to record it" on hover |
| Guest count | "N people" from a number only; left out when the calendar gives none |
| Times | The viewer's own zone on Meetings; "Last updated" on Settings is UTC and says so |
| Recurring cards, Tasks, Sprints, language on Settings | Not part of this slice |

## Follow-ups
- **The rest of C15a:** `PUT /api/calendar/settings` (rule, template, instant-Meet switch with the 409 `consent_required`), the override route (409 `not_joinable`) and the switch on each row, `GET /api/calendar/upcoming`, the full `state_text` for scheduled-join states, and `_reconcile` on a rule or template change (from the sync SOP). The plan's `test_will_join_text_carries_the_tell_participants_reminder` belongs there: here the reminder is the New meeting card's own line.
- **C13** must set `degraded` and `needs_reconnect`; until then a revoked grant shows only the `last_error` sentence while `state` stays `connected`.
- **The privacy sentence** ("you can disconnect in the app") the C4 SOP and the checklist say goes back with this button: the legal pages are outside this slice (other sessions are in them), so the lead should add it when merging, and re-check the Security row wording.
- Another session adds an "Email preferences" section to the same Settings page: it goes in its own component beside `SettingsCalendar` in `web/app/settings/page.tsx`.
- Auto-refresh after connecting (the first sync takes about a minute), and the Teams wording once Teams works.
- The first real Google run (Adebayo's Gmail) will show whether the unverified-app explainer matches what Google really says, and whether real events (conference data, all-day events, recurring series) read the way the synthetic ones did.
