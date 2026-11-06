# SOP: W4 Projects and milestones, screens (PR 2 of 2)

**Stacked on PR 1** (`feature/w4-projects-backend`, #100): this branch contains its commits, so its diff shows both until #100 merges; retarget this PR to `main` before merging #100 with `--delete-branch`.

## What shipped
The Projects screen (list, detail with autosave, colour swatches, keyword chips, milestone line, stats, delete), the project tabs, milestone card, project badge and "Check project" on the Tasks board, the Project and Milestone selects and the "Check project" confirm line in the task drawer (also on the meeting page's drawer), the Milestone row in the meeting page's Details, and the meeting page's **"Set milestone: <name>, <date>?" with Confirm** (lead-designed, below). A Projects item in the sidebar (between Tasks and Decisions). No API change: the screens use the routes of #100 (`docs/fe-integration-guide-projects.md`).

## Why
Spec 6.1 to 6.3 and D14; PR 1 made projects and milestone proposals but nothing could show or confirm them.

## How: the decisions
| Topic | Decision |
|---|---|
| Autosave | name: PATCH after a 600 ms pause and when the field is left (one field per request); colour and each keyword: at once. The name typed stays on screen while it saves (`drafts`); a refused change shows the API message |
| Who may change | `can_manage` from `GET /api/projects`. A member sees the same screen with the fields read-only, no New project, no Delete, no keyword box, and the line "Only an owner or admin of your team can change projects." (added; the designer drew none) |
| Colours | the six swatches live in `lib/brand.ts` (`PROJECT_COLOURS`) and `--swatch-1..6` in `tokens.css` (a test keeps them equal); a project colour is a class (`sw-1`..`sw-6`, grey for any other value) because the CSP forbids inline styles and the design guard forbids colour literals. The progress bar is a `<progress>` for the same reason |
| Switched off (no feature yet) | Recurring meetings (W8): title and help line, then "Not available yet." Sprint length (W9): the select, disabled, "Workspace default" only, then "Not available yet." The two sprint figures ("In current sprint", "In backlog"): shown as "–" with one line "The sprint figures are not available yet." |
| Board tabs | drawn only when the projects could be read and there is at least one; "All projects" shows a project badge ("No project" for none), a project tab hides it, as the design's `showProject` |
| Milestone card | the next confirmed milestone not yet past (within the open tab; a milestone belongs to its project's tab); days left, name, date, progress "x of y launch tasks done · Set in <meeting>, <day>", "Show launch tasks" / "Show all tasks" (a separate filter from the person chips; the design shared one state) |
| New task | filed under the open project tab, none on "All projects" |
| Drawer | Milestone (None + confirmed milestones, "Name, 8 Dec") and Project rows after Due; the "Check project" line with `Confirm <project>` sends `check_project: false`; picking a project clears the check on the server. Rows are off (and the Confirm button hidden) unless the task's `can_edit` is true (owner, admin, or the maker of a manual task: the Opus review of #100 took filing away from an assignee member) and left out when the lists could not be read |

## Lead-designed, not in the designer's file (Adebayo said yes, 2026-11-06)
1. **"Set milestone: <name>, <date>?" with Confirm** in the meeting page's Details card, built from the amber notice of the task drawer, with the words said under it as plain text. Shown only to someone who can confirm (owner or admin), so no button is ever refused. The designer drew only the confirmed row (Details > Milestone).
Added words that are not in the design: the empty list text "No projects yet.", the member line above, and the "Not available yet." lines (the Settings pattern); also a "No project" option in the drawer's Project select (the design has none, so a task could never be un-filed) and the sidebar's Projects item is the design's own.

## What's involved
- New: `web/app/projects/page.tsx`, `web/components/app/projects-view.tsx`, `web/components/app/meeting-milestones.tsx`, `web/tests/projects-view.test.tsx`, `web/tests/tasks-projects.test.tsx`, `web/tests/meeting-milestones.test.tsx`.
- Changed: `web/components/app/{shell,tasks-view,task-card,task-drawer,action-tasks,meeting-view}.tsx`, `web/app/tasks/page.tsx`, `web/lib/{api/types,api/client,tasks,brand,routes}.ts`, `web/proxy.ts`, `web/styles/{tokens,pages}.css`, `web/scripts/{mock-api,parity-shots}.mjs`, `web/tests/{helpers,guards,task-drawer,...}` (mocks of `api.milestones`).
- `/projects` is a Next page (`NEXT_PAGES`, the proxy matcher); nothing in nginx or FastAPI lists pages.

## Verification
- Local: `npm run lint`, `npm run typecheck`, `npx vitest run` (all). The new tests are fed the bodies captured from the running API (`docs/fe-captures/projects-w4/`).
- Parity: `web/scripts/parity-shots.mjs` against `mock-api.mjs`, 1440, 768 and 390, `--tz Africa/Lagos --now 2026-11-02T10:00:00Z`, shots under `docs/ui/parity/after/` (`app-projects`, `app-projects-second`, `app-tasks`, `app-tasks-project`, `app-tasks-check-project`, `app-detail`, `app-detail-milestone-offer`; design on the left, page on the right; the last has no design).

## Parity table
| Screen | Matches the design | Differences (all deliberate) |
|---|---|---|
| Projects | list, stats, Name, Colour, Keywords, Milestone, Delete, "Changes save automatically", page text | Recurring meetings rows and Sprint length values are "Not available yet"; the sprint figures show "–"; the list line says "N open tasks" without "· M recurring meetings" (no series yet) |
| Tasks board | project tabs, milestone card, project badge, Check project, banner line | no sprint header or Board / Backlog (W9); the card sits alone in its row |
| Drawer | Milestone and Project rows, Check project line and Confirm | "No project" option added; Sprint row absent (W9) |
| Meeting page | Details > Milestone row | the offer is lead-designed (above) |

## Operate / roll back
- **Server steps before merge:** none (the API is #100). Merge #100 first, or this one carries it.
- Roll back: revert the PR; the API keeps working.

## Follow-ups
- Designer: a confirmation before "Delete project" (the design deletes at once); the "Set milestone" offer; the member's read-only Projects screen; the empty list.
- W8 fills Recurring meetings and "· M recurring meetings"; W9 the Sprint length and the two figures.
- Visual check of the member screens (they are covered by tests, not by parity shots).
