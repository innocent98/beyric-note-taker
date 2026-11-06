# SOP: W9 Sprints, screens (PR 2 of 2)

**Stacked on PR 1** (`feature/w9-sprints-backend`, #108, which is itself stacked on #100): this branch also contains the W4 screens branch (`feature/w4-projects-screens`, #102), merged in because the Tasks and Projects screens it edits are theirs. Its diff shows those commits too until they merge; retarget before merging a base with `--delete-branch`.

## What shipped
The sprint screens the designer drew, on the API of #108 (`docs/fe-integration-guide-sprints.md`, no API change):
- **Tasks:** a sprint selector (Board only), the Board / Backlog tabs, the sprint card (name, dates, time left, progress, "x of y done", the Monday to Friday note, Close sprint), the Backlog view (next sprint and Backlog cards, a Sprint select on every row), the "Carried over" badge on cards and rows.
- **Task drawer:** the Sprint row after Project, also on the meeting page's drawer.
- **Settings > Sprints and reminders:** Sprint length (with the current sprint and its dates under the title, and the days box for Custom) and "Open and close sprints at recurring meetings".
- **Projects:** the Sprint length select ("Workspace default (...)" first) and the live "In current sprint" and "In backlog" figures.
- The mock API (`web/scripts/mock-api.mjs`) serves the designer's sprints (Sprint 11 closed, 12 current, 13 next).

## Why
Spec 6.4, D10, D12. PR 1 made the data; nothing could show, plan or close a sprint.

## How: the decisions
| Topic | Decision |
|---|---|
| Which tasks a sprint shows | All tasks are loaded once (as before); the board filters them by `sprint_id` in the browser. With "All projects" the current sprint stands for the team's and each project's own current sprint (ids from `GET /api/sprints` for the team and for each project with its own length, read together on page load). A project tab uses that project's list: its own pair, else the team's pair counting only its tasks. Counts in the card are worked out from the loaded tasks, so a drag to Done moves the bar at once |
| The banner and the milestone card | Unchanged: they count every task, not the sprint shown (the designer's `s.tasks`) |
| Close sprint | One click, no confirmation (the designer closes at once). Toast in the designer's words from the API's `moved` and sprint names. A 409 `sprint_not_current` (the hourly job got there first) reads the sprints again and says nothing. Only for the current sprint and only when `can_manage` |
| Refresh | Tasks and sprints are read again after Close sprint and when the tab becomes visible (the hourly job may have rolled sprints over) |
| Planning a task | Backlog row select and drawer select send one `PATCH {sprint_id}`; off unless `can_edit` (owner, admin, maker of a manual task: the backend rule). Options are Backlog plus the current and next sprint of the series of the task's project |
| New task | Board: the sprint shown (a closed one means Backlog, sent as `sprint_id: null`); Backlog view: `null`; no sprints loaded: the field is left out |
| Settings | One `PATCH /api/team` per change (not the spec's `PUT /api/team/settings`); the preview line is read again from `GET /api/sprints` after every change; the days box saves when left or on Enter, clamped to 1 to 365; the switch off "1 week, 5 working days" answers with the designer's toast and sends nothing; a refusal puts the control back and shows the message |
| Project length | `PATCH {sprint_length}` (`null` for the workspace default); no Custom option, as drawn |
| Failure states | Sprints unreadable: the board shows every task as before plus "Could not load sprints." with Try again; Settings and Projects fall back to the previous off state or plain "Workspace default" |

### The five choices of #108, as the UI shows them (Adebayo has not confirmed them)
1. **Mon-Fri switch only for "1 week, 5 working days".** The switch is drawn off, and the toast "Only available for 1 week, 5 working day sprints" answers a click, for every other length. The Settings sub-line says "Available for 1 week, 5 working day sprints." there.
2. **An early-closed sprint keeps its planned end date.** Visible: a closed sprint's range can run past the day the next one starts (shown as each sprint's own range). The card reads "Closed".
3. **The team zone is the owner's Settings zone.** Not exposed; the card's "4 days left" uses the viewer's own zone (`useToday`), which can differ by a day from the team's zone when the viewer's zone is not the owner's.
4. **Settings go through `PATCH /api/team`.** Not visible.
5. **Existing tasks are not backfilled.** Visible: until someone plans them or a meeting makes new ones, old tasks sit in the Backlog and the Board's current sprint is empty on day one.

## Words the designer's file does not have (added; none is a new feature)
- Settings: for the working sprint rows, the one-line "Remind attendees before recurring meetings is not available yet." (reminders stay off, W8) replaces "These settings are not available yet."; members see "Only an owner or admin of your team can change the sprint settings." (same pattern as W4's member line).
- "Could not load sprints." with Try again.
- The designer's week-5 sub-line says "Sprints open at **Beyric** Monday planning and close at **Beyric** Friday review." Beyric is the sample meeting's name, so it reads "Sprints open at Monday planning and close at Friday review." (the board's own note says the same).
- A task left in a **closed** sprint (a finished task) shows that sprint as an extra option in its select, so the select does not claim "Backlog". The design's select had no case for it.
- Narrow screens: a Backlog row stacks (the select drops under the title) at 640 px and below; the designer's grid is desktop only.

## Not built, needs the lead (nothing blocking)
- **A sentence about what changes when a project gets or loses its own length** (its tasks move into a new pair of sprints and back). The FE guide asks for one; the designer drew none, so no text was added.
- **A confirmation for "Close sprint"** (it cannot be undone and moves tasks). The designer closes at once; the same gap as "Delete project".
- **"All projects" Close sprint** closes only the team's current sprint; a project with its own length is closed from its own tab. Designer drew one sprint only.
- W8's **sprint line under "Next meeting"** on the series page (`srSprintNote`): waits for #104/#105 to be in main (not on this branch).

## What's involved
- New: `web/lib/sprints.ts`, `web/components/app/{sprint-header,backlog-view,settings-sprints}.tsx`, tests `sprints-lib.test.ts`, `sprints-board.test.tsx`, `settings-sprints.test.tsx` (fed the bodies in `docs/fe-captures/sprints-w9/`).
- Changed: `components/app/{tasks-view,task-card,task-drawer,action-tasks,projects-view,settings-planned-sections}.tsx`, `app/{tasks,settings,projects}/page.tsx`, `lib/api/{client,types}.ts`, `styles/pages.css`, `scripts/{mock-api,parity-shots}.mjs`, `tests/{helpers,projects-view,settings-page}`.

## Verification
- Local in `web/`: `npm run lint` (0 errors; 3 old warnings in `parity-meeting-delete.mjs`), `npm run typecheck`, `npx vitest run` (all green).
- Parity: `web/scripts/parity-shots.mjs` against `mock-api.mjs`, 1440, 768 and 390, `--tz Africa/Lagos --now 2026-11-02T10:00:00Z`; shots in `docs/ui/parity/after/` (`app-tasks`, `app-tasks-backlog`, `app-tasks-sprint-next`, `app-tasks-close-sprint`, `app-tasks-drawer`, `app-tasks-project`, `app-settings`, `app-settings-sprint-custom`, `app-projects`, `app-projects-second`, `app-projects-sprint-length`; design left, page right). The React #418 the script prints on `/tasks` is the harness: the server renders with the real clock and the browser has the fixed one.
- Not run against the real backend in a browser: the mock stands in. The API behaviour is the 68 live captures of PR 1.

## Parity table
| Screen | Matches the design | Differences |
|---|---|---|
| Tasks board | tabs row, selector, Board / Backlog, sprint card (name, dates, pill, bar, "0 of 8 done · note", Close sprint), milestone card beside it, columns, Carried over | task order within a column follows the mock |
| Tasks, next sprint | card "Starts Mon 9 Nov", empty columns, no Close sprint | |
| Backlog | two cards, titles, sub-lines, counts, rows with badges, due, owner dot, Sprint select | rows stack at 640 px and below |
| Close sprint | toast "Sprint 12 closed. N unfinished tasks moved to Sprint 13", selector moves to Sprint 13 | |
| Drawer | Sprint row after Project with Backlog, current, next | closed-sprint option for a task left there |
| Settings | Sprint length, preview line, Custom with days box, the switch and its sub-lines | reminders row still off with its own line; "Beyric" dropped from the sub-line |
| Projects | the two figures, Sprint length with "Workspace default (...)" | Recurring meetings still "Not available yet" (W8) |

## Operate / roll back
- **Server steps before merge: none.** Merge #100, #102 and #108 first, or retarget this PR to `main` once they are in. Revert the PR to roll back; the API keeps working.
- FE guide: no new shapes, so `docs/fe-integration-guide-sprints.md` is unchanged (nothing captured here could add to it).

## Follow-ups
- The lead-designed list above for the designer or Adebayo.
- W8's sprint line on the series page after #104 and #105 merge.
- A visual check of the member views (covered by tests, not by parity shots).
