# SOP: Settings sections below Calendar (drawn, off) and the Recurring cards on Meetings

## What shipped
Adebayo compared the live pages with the designer's file and asked for the missing designed parts to be drawn now, before their features exist. Two things, branch `feature/settings-sections-recurring`, PR #69 (against `main`, not merged):

- **(A) Settings:** the sections the designer drew below Calendar: **Sprints and reminders**, **Tasks**, **Default notes language**. Exact layout, words, tokens and order. **Every control is off**: a real `disabled` on each button and select, the switches `disabled` and `aria-disabled` and showing off. Nothing is stored and nothing is called (plain server markup, no state, no network). Each section carries one plain line saying it is not available yet.
- **(B) Meetings:** the **Recurring** cards, from real data, through a new read-only `GET /api/calendar/recurring`. Name, cadence (worked out from the stored event times, or just "Recurring"), and the "Next:" chip. Not clickable. Nothing is drawn when no calendar is connected or nothing recurs.

No migration, no new setting, no `.env` variable, no nginx or compose change. The calendar flag is unchanged: with `CALENDAR_ENABLED` false (production) the route answers 404 `calendar_unavailable` and the Meetings page draws no Recurring part. The Settings sections show whatever the flag says (they need no data).

## Why
The designer's Settings screen and Meetings screen had parts the live pages lacked. Adebayo chose to see them now instead of waiting for W2 (tasks), W7 (language), W8 (series, reminders), W9 (sprints), W10 (tool sync, Slack). The risk of drawing a feature that does not exist is that someone thinks it works: so each control is visibly disabled, shows an off state, never a connected one or an invented number, and says so in words.

## How
| Piece | Decision | Why |
|---|---|---|
| Disabled look | The designer's own: opacity 0.55 and the default cursor (his Upcoming switch uses `opacity 0.55` for a switch that cannot be changed). Real `disabled` on buttons and selects; the four switches are `<button role="switch" aria-checked="false" aria-disabled="true" disabled>` | A real `disabled` cannot be pressed or tabbed to and screen readers say so; no switch component existed in `web/components/ui` |
| Values on disabled controls | Switches **off** (the design draws them on), selects at the designer's own first choice: "1 week, 5 working days", "1 hour and 30 minutes before", "Jira", "Original (no translation)" | An "on" switch for a feature that does nothing would be a false statement; a select needs a value |
| Never shown | "Current sprint: Sprint 12, ..." (the sprint-length preview), "Sprints open at Beyric Monday planning...", "Syncing with Jira", "#beyric-updates", Disconnect buttons, reminder-off states | No data behind them; a test searches the markup for each |
| Server markup only | `web/components/app/settings-planned-sections.tsx` has no `"use client"`, state, effect, fetch or storage (a test reads the source for that) | The strongest way to guarantee it stores nothing and calls nothing |
| Own files, tiny page edits | Settings: one import and one line in `web/app/settings/page.tsx`. Meetings: one import and one line in `web/app/page.tsx`. The Recurring cards are `web/components/app/recurring-cards.tsx`; CSS is appended at the end of `web/styles/pages.css` (`pl-*`, `rec-*`) | The calendar session edits `settings-calendar.tsx`, `calendar-upcoming.tsx`, `app/calendar_api.py`: a rebase stays trivial |
| New module for the route | `app/calendar_recurring.py`, included in `app/api.py` (`router.include_router(calendar_recurring.router)`); `app/calendar_api.py` untouched. It repeats the 3-line flag check and the connection lookup instead of importing the other module's private helpers | The other session is editing that file |
| Grouping | The user's own connection's events with a `recurring_event_id`, not cancelled, not all-day, `event_type = default` (so not out-of-office or focus time). A series is listed only while an occurrence has not started yet | Meeting cards, and a series with nothing ahead has no "Next" |
| Name | The title of the next occurrence (a blank title: no card, there is no name to draw) | A one-off rename of a past occurrence must not name the card |
| Cadence | Only what the times prove, in the viewer's zone: all occurrences at the same wall-clock time on different days; **Daily** needs three or more on consecutive days; **Weekly on Mondays[ and ...]** needs every weekday seen at least twice and the occurrences to be an unbroken run of that weekly pattern (the window may cut the first and last week, never the middle); anything else, including one occurrence, two days in a row, two dates 14 days apart, a moved occurrence, a different time of day, is just **Recurring** | "Never a guess you cannot back with the data". The data window is two weeks, so weekly is what it can prove; "every 2 weeks" is not claimed |
| Zone | `?tz=<IANA name>` (the browser's), UTC if absent (the answer says which in `timezone`); an unknown name is 422 `invalid_timezone` (no zone is ever guessed); max 64 characters. The browser asks after load in `RecurringCards` (a client component) because the server cannot know the zone | The server has no per-user zone (none is stored). The weekday and time of day depend on it. `tzdata` is in the server image (checked with `python:3.13-slim`) |
| Words from the server | `cadence` and `next_text` ("Mon 9 Nov, 14:00", English whatever the locale) are written by the route | The calendar slice's rule: the frontend never invents text |
| Chips | **Only "Next: ..."** is drawn. The unfinished-tasks chip waits for W2. The open-questions chip is left out: a past meeting's link to its series goes when the event row is deleted two days after it ends (`scheduled_joins.calendar_event_id` is SET NULL), so a count would shrink silently; W8 owns carry-over | Counts only where real |
| Not clickable | No chevron, no hover colour, no link, no button, nothing focusable (a test checks) | The Series page is W8; a hover would look like a link |
| Draws nothing | The component renders nothing while loading, on an empty list, on 404 (calendar off) and on any failure | The Meetings page must look as before whenever the calendar is off or unreadable |
| Rejected | Importing `calendar_api._calendar_connection`; a server-side fetch in the Meetings page (UTC texts); a series key or the Google recurring id in the answer (React uses the index; the id stays on the server); returning the last occurrences' meetings | Coupling to the other session; wrong zone; no need |

## What's involved
| Path | Change |
|---|---|
| `app/calendar_recurring.py` (new), `app/api.py` | the route and the cadence logic (`describe_cadence`, `next_text`); two lines in `api.py` |
| `docs/openapi.json`, `web/lib/api/schema.d.ts`, `web/lib/api/{client,types}.ts` | regenerated; `api.calendar.recurring(timeZone?)`, `RecurringState`, `RecurringSeries` |
| `web/components/app/settings-planned-sections.tsx` (new), `web/app/settings/page.tsx` | the three sections; one line |
| `web/components/app/recurring-cards.tsx` (new), `web/app/page.tsx` | the cards; one line |
| `web/styles/pages.css` | `pl-*` and `rec-*`, appended |
| `tests/test_calendar_recurring.py` (55) | pytest on a real Postgres 17 |
| `web/tests/{settings-sections,recurring-cards,meetings-recurring}.test.tsx`, additions to `settings-page.test.tsx` and `api-client.test.ts` | vitest |
| `scripts/capture_calendar_recurring.py`, `docs/fe-captures/calendar-recurring/` (18 files), `docs/fe-integration-guide-calendar-settings.md` section 7 | live captures and the guide |
| `web/scripts/{mock-api,parity-shots}.mjs`, `web/scripts/parity-diff.mjs` (new) | the mock replays the Recurring captures (`--recurring series|two|none`); the diff script measures positions and computed styles |
| `docs/ui/parity/after/app-{settings,settings-connected,meetings-calendar}-{1440,768,390}.jpg`, `settings-sections-recurring-diff-1440.txt` | design left, built page right |

## Section order on the Settings page
Calendar, Sprints and reminders, Tasks, Default notes language (the designer's order), then **Email** (added by PR #64; the design has no Email section, so it stays where #64 put it, after the designer's sections). `web/app/settings/page.tsx` renders them in that order and `settings-page.test.tsx` checks it. Parity was re-checked after merging `main` (the Settings page at 1440, 768 and 390 px, `parity-diff.mjs` unchanged: 40 compared, the same differences).

## Parity with the designer's file
Method as `docs/ui/parity-report.md`: the built app (`next build`, standalone) against the mock API (connected, `--recurring two`, the two weekly series the designer drew), the designer's file served beside it, Chromium at 1440, 768 and 390 px (`parity-shots.mjs`), then `parity-diff.mjs` measured 40 labels and boxes (positions inside each section's own box, sizes, 14 computed styles). The full output is `docs/ui/parity/after/settings-sections-recurring-diff-1440.txt`. Every remaining difference is listed; there are no others.

| Element (1440 px) | Design | Built | Result |
|---|---|---|---|
| Section boxes, widths, borders, radius 12, background | 674 wide, 1 px `--border` | same | identical |
| Section titles, descriptions, "Owners and admins" pill (11/600, `#4A524E` on `#EEF1F0`) | as drawn | same | identical (size, weight, colour, padding) |
| Row titles and descriptions (14/550, 12 `#6E7772`) | as drawn | same | identical; x identical |
| Selects (36 / 34 / 40 high, padding, 13/14 px, chevron) | as drawn | same | identical boxes; only `opacity` differs (0.55, the disabled look) |
| Buttons "Connect", "Connect Slack" (34 high, 14 sides, 13/550, border) | as drawn | same | identical boxes; opacity 0.55 |
| Switches (36x20 pill, 16 px knob) | teal, knob right | same box, grey track `#D5DBD8`, knob left, opacity 0.55 | **state differs on purpose: off and disabled** |
| Default notes language: layout, globe icon, 260 px select | as drawn | same | identical except the box is 25 px taller (the added line) |
| Section heights | Sprints 303, Tasks 353, Language 113 | 326, 379, 138 | **+23, +26, +25: the one "not available yet" line** (Sprints is 3 px shorter than that sum, because its first row loses the preview line, see gaps); every later row is shifted by exactly that amount |
| Recurring: card box (padding 16/18, radius 12, border, gap 10), name 14/600, cadence 12 `#6E7772`, "Next:" chip (12/550, `#0A5248` on `#E6F1EE`, pill) | 105 high | same | identical box (105) and styles; label boxes differ only because the design's labels are inline spans |
| Recurring: heading "Recurring" 15/600, grid `repeat(auto-fit, minmax(280px, 1fr))`, gap 14 | as drawn | same | identical (two cards fill two columns, as in the design) |
| Recurring: chevron, hover border, the two grey chips | drawn | **not drawn** | listed as gaps |
| 768 and 390 px | the prototype's main app is not responsive | the engineered phone layout (rows wrap, the cards stack to one column) | not comparable, as for every screen |

Seen while comparing, not caused here: in the mock the New meeting card is taller (a bot-busy notice in the mock data), as the calendar SOP noted.

## Verification
| Check | Result |
|---|---|
| `tests/test_calendar_recurring.py` | 55 tests, real Postgres 17: every cadence case (weekly, several weekdays, every weekday, daily, and eleven cases that must stay "Recurring": one occurrence, two days, 14 days apart, a skipped week, a moved occurrence, a weekday seen once, two on one day, a changed time, a daily gap), order independence, Auckland and Los Angeles boundaries (Monday 23:30 UTC), a clock change (New York), next-occurrence rules (cancelled, in progress, past), blank titles, all-day and out-of-office left out, 50 cap, emoji and non-Latin titles, isolation (the same recurring id on another user's calendar does not merge), no attendee, link, passcode or source id, a read changes nothing, bad zones (`Mars/Olympus`, `../etc/passwd`, empty, 200 characters, `UTC+1`, `Etc/../UTC`) |
| API enumeration (`tests/test_api.py`), `test_calendar_api.py`, OpenAPI file test | pass (221 with the new file); `scripts/export_openapi.py --check`, `ruff check .`, `ruff format --check .` (0.17.3, own venv) clean |
| Web: `npm run lint`, `npm run typecheck`, `npx vitest run` | clean; 434 tests (new: sections in the designer's words, all four switches off, 10 controls all disabled, one line per section, nothing invented, clicks, keys and select changes make no request and write no cookie or storage and change no HTML, source has no client code; cards present and absent for every failure, no unfinished or open-question chip, not a link, the browser's zone is sent, unmount safety; the Meetings order; the Settings order; the client call). Mutation checks: an enabled button, a switch left on, an extra chip each broke tests |
| Live capture | `scripts/capture_calendar_recurring.py` (18 files, real app, real sync job over a fake Google) |
| Parity | table above |
| **Not verified** | A real Google calendar and real recurring events (series ids, instance times, moved or cancelled instances as Google sends them); Safari and Firefox; the live site; the pages against a real FastAPI in a browser (only the mock, with real captures); a clock change inside a live window (a unit test only) |

## Operate / roll back
- **Server steps before merge: none** (no variable, no nginx edit, no migration).
- Merging deploys the web image and the API. With the calendar flag off nothing new appears on Meetings; Settings shows the three drawn sections to every user. They do nothing: each says it is not available yet.
- Roll back: revert the PR; nothing is migrated.

## Designer gaps (for the designer to review)
| Gap | Where | What was built |
|---|---|---|
| A "not available yet" line | one in each of Sprints and reminders, Tasks, Default notes language (under the section description) | Added words, nothing else is: "These settings are not available yet." (twice) and "This setting is not available yet." |
| A disabled state | every control | The designer's own opacity 0.55 (his Upcoming switch); switches show off, not on, because they do nothing |
| Sprint length preview line (`Current sprint: ...`) | Sprints and reminders, first row | **Not drawn**: it needs a current sprint. The design has no empty state for it |
| The reminder timing select is hidden by the design when the reminder is off | Sprints and reminders | Drawn disabled beside the off switch (the brief asked for it); the design never shows that combination |
| Description of "Open and close sprints at recurring meetings" | same | The design's own other line, "Available for 1 week, 5 working day sprints." (the one with series names would be invented) |
| Sprint "Custom days" input | same | Not drawn (it shows only for the Custom choice, which cannot be chosen) |
| Connected states: "Syncing with {tool}" with Disconnect, "#channel" with Disconnect | Tasks | Not drawn (no connection exists) |
| Recurring: chevron, hover border, unfinished-tasks chip, open-questions chip | Meetings | Not drawn: not clickable until the Series page (W8); chips until W2 and W8 |
| Recurring when nothing recurs or no calendar | Meetings | Nothing, heading included (the design always shows it with sample data) |
| Cadence for series the data cannot describe | Recurring card | The word "Recurring" (the designer's examples are only weekly) |
| Daily and several-weekday cadence wording | Recurring card | "Daily, 09:30"; "Weekly on Mondays and Wednesdays, 14:00" (follows the designer's example form) |
| A series with a blank title, all-day series | Recurring | Not drawn |
| Owners and admins pill | Sprints and reminders | Drawn, but there are no roles yet: it describes who will be able to change the setting |

## Follow-ups
- Each control becomes real in the PR that builds its feature: Tasks and Email assignees (W2), sprints (W9), reminders (W8), language (W7), tool sync and Slack (W9, W10). Tick them off in the checklist then, and drop the "not available yet" line with them.
- Recurring: the unfinished-tasks chip (W2), the open-questions chip and the card as a link to the Series page (W8, when carry-over can be tied to a series).
- A per-user time zone setting would let the server render cadence without the browser's zone (mail reminders in W8 need one anyway).
- Expect a rebase on the calendar session's PR (`settings-calendar.tsx`, `calendar-upcoming.tsx`, `calendar_api.py`, and CSS next to mine at the end of `pages.css`): keep both sides.
