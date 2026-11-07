# SOP: UI parity batch 3, front end only (Series score tiles, overdue wording, Decisions row)

PR: branch `feature/ui-parity-batch-3-fe` (number in the PR list). From the 2026-11-07 audit of the designer's new main file (`docs/ui/audit-2026-11-07-main-v2.md`, rows **B10, B11, B34**). No backend change, no migration.

## What shipped
1. **B10, Series page score tiles.** Three tiles above the first block: **Done since last meeting** (teal), **Still open**, **Overdue** (red when above 0, plain ink at 0).
2. **B11, overdue wording.** A task whose day is before today and that is not done says **"Overdue, Fri 30 Oct"** in red; the Group-by-Due-date heading says **"Overdue, was due Fri 30 Oct"**.
3. **B34, Decisions.** "Remove from log" now lives inside the open row (a quiet button at the bottom of the row's panel, red on hover) with the same confirmation words; the empty search line is exactly **"No decisions match."**.

## Why
The designer's new file draws all three (`D:695-697` and script `D:1886-1896`; `D:1511-1512`, `D:1779`, `D:2039`; `D:812-870`). The standing UI rule is full parity, nothing invented, wording exactly as in the file. The data was already in the API, so no backend work was needed.

## How: the decisions
| Topic | Decision |
|---|---|
| Counts | Done = `page.finished.length` (the list the page already draws as "Finished since last meeting"). Overdue = open (`unfinished`) tasks with `due_date` before today. Still open = the other open tasks. Same arithmetic as the design (`open.length - over.length`) |
| "Today" | `useToday()`, the person's zone (the stored Settings zone, else the browser's); dates are `YYYY-MM-DD` strings so overdue is a string compare (`isOverdue` in `web/lib/tasks.ts`) |
| Where the wording applies | The design computes the label once (`D:2039`) and uses it on the board card, the Backlog row, the Series row and the meeting page's Action items (`D:260`, `D:732`, `D:1042`, `D:1072`). One helper, `taskDueText(task, today)`, now feeds all four, so they cannot drift. The brief named the card; the other three are the same string in the file. A finished task keeps its plain grey day (design: `status !== 'done'`) |
| Group heading | `groupLabel` in `lib/tasks.ts`. The key decides (as the design's does), so the heading follows the earliest-first sort that already put past days first |
| Not built (as told) | A past day in the **Due select** (`D:2039` area): the audit says it is sample data, a real date field is a designer question (U8). B9 (Ask Beyric link) and B26 (Decisions subtitle) wait for their backend pages |
| Decisions row | Rows are now flush with a 1 px `--border-softer` rule, a 16 px check, the text and a chevron (the design's grid minus the status pill, which needs B28). One row open at a time (design: `decOpen`); opening or closing a row cancels an open ask (design: `confirmRemove: null`). The panel is the design's (`#FAFBFA`, 1 px `--border-soft`, radius 10, padding 14). After a removal the row closes and the toast says "Decision removed from the log". The old per-row "Remove from log: {text}" button is gone; the accessible name is now "Remove from log" (only one panel is open) |
| Not drawn | Status pills, tabs, Status / Revisit / Decided by, evidence, related tasks (B27 to B33 need a backend and a spec): the panel holds only what exists |
| Tokens | No new colour. The ask box keeps the app's `--danger-tint` / `--danger-line` (`#FDECEA` / `#F5C2BD`) where the design's literal is `#FDF3F2` / `#F5D0CC`: a known, recorded difference that Decisions already had (see `docs/sop/2026-11-05-w05-meeting-delete-button.md`) |
| Empty line | `No decisions match.` as plain grey text in the dashed box (design: padding 24, centred, `#6E7772`), not the bold `EmptyState` title |

## What's involved
- `web/lib/tasks.ts`: `isOverdue`, `taskDueText`, `groupLabel` (overdue heading).
- `web/components/app/series-view.tsx` (tiles, row label), `task-card.tsx`, `backlog-view.tsx`, `action-tasks.tsx` (label and red), `decisions-view.tsx` (row, panel, empty line).
- `web/styles/pages.css`: `.sr-score`, `.sr-tile*`, `.tk-due-overdue`, `.dec-row`, `.dec-toggle`, `.dec-chev*`, `.dec-panel`, `.dec-remove`, `.dec-nomatch`; removed the old `.dec-item*`, `.dec-body`, `.dec-confirm`.
- Tests: new `web/tests/overdue-wording.test.tsx`; changed `decisions-view.test.tsx` (open the row first, new line, two new tests), `series-view.test.tsx` (three tile tests).
- Evidence tooling: `web/scripts/parity-batch-3-fe.mjs` (new), `parity-shots.mjs` (Decisions screens now open a row first; new `app-decisions-open`, `app-decisions-nomatch`), `mock-api.mjs` (new `--overdue` flag; search "zebra" answers the captured no-match list).

## Verification
- `npm run lint` (0 errors, the 3 old warnings in `parity-meeting-delete.mjs`), `npm run typecheck`: clean. `npx vitest run` on the touched areas (decisions, series, overdue wording, action tasks, tasks view, task drawer, sprints board, tasks and projects, parity, screens): 183 tests green.
- Unit tests prove: overdue is strictly before today; the label and heading words for overdue, today, tomorrow, later, none; the overdue group sorts first; a done task is never overdue; the red class on the card and not on a done one; the tiles' numbers and the red Overdue only above 0; "Remove from log" hidden until the row is open and inside its panel; one row open at a time.
- Parity (built app, standalone server against `mock-api.mjs --overdue`, `--tz Africa/Lagos --now 2026-11-02T10:00:00Z`, design left and page right, 1440 / 768 / 390): `docs/ui/parity/after/app-series-*`, `app-tasks-due-*`, `app-tasks-*`, `app-tasks-backlog-*`, `app-decisions-*`, `app-decisions-open-*`, `app-decisions-confirm-*`, `app-decisions-removed-*`, `app-decisions-nomatch-*`.
- Computed styles, design against page: `docs/ui/parity/after/batch-3-fe-styles.json` (`web/scripts/parity-batch-3-fe.mjs`): **14 of 14 identical** (tile box, labels, teal and ink numbers, red Overdue number, red card label, group heading, Remove from log resting and hover, the panel box, "No decisions match.").

## Parity table
| Item | Design | Page | Difference |
|---|---|---|---|
| Series tiles | 3 tiles, `#F6F7F6`, radius 10, 22 px / 650 numbers, 12 px labels | same (styles identical) | none; the page's counts come from the API, the design's from sample data |
| Overdue number | red `#B42318` above 0 | same | none |
| Card due label | "Overdue, Fri 30 Oct", red | same | none |
| Group heading | "Overdue, was due Fri 30 Oct" | same | none |
| Remove from log | quiet, inside the open row, red on hover | same (styles identical) | none |
| Ask words and Cancel / Remove | same words | same | ask box tint is the app token (recorded above) |
| Empty search line | "No decisions match." | same (styles identical) | none |
| Decision row | status pill, meta line, panel with Status, Revisit, evidence | check mark, text, chevron, panel with only the button | the rest needs B27 to B33 (backend) |
| Due select past day | "Fri 30 Oct" | not built | sample data; designer question U8 |

## Operate / roll back
Server steps before merge: none. Revert the PR to roll back; nothing is stored.

## Follow-ups
- B26 (Decisions subtitle) and B9 (Ask Beyric link) wait for the backend pages (lead decision).
- A task due today shows "Today" in amber, a day before shows red: if the designer wants a done-late task marked, say so (the file greys it).
- The `parity-shots.mjs --now` run prints a React #418 hydration warning on `/tasks` (the server draws the real date, the page a faked one). It does not appear without `--now`; it is an artifact of the fixed clock.
- Playwright is not a committed test suite in this repo: the browser proof is the two scripts above, run by hand.
