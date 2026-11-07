# SOP: link a recurring meeting to a project (A43, A41) and the Series page's sprint line (A45)

PR: branch `feature/series-project-link-and-sprint-line` (number in the PR list). From the 2026-11-07 audit (`docs/ui/audit-2026-11-07-app.md`, rows A41, A43, A45). **No migration** (`series.project_id` already existed, without a foreign key). FE guide: `docs/fe-integration-guide-series-project-link.md`; captures `docs/fe-captures/series-project-link/` (`scripts/capture_series_project_api.py`).

## What shipped
1. **`PUT /api/series/{id}/project`** (`{"project_id": int | null}`): links, moves or unlinks a recurring meeting; the answer is the series card, which now has `project` (`{id, name}` or null) on `GET /api/series` too.
2. **Filing rule step 1 is live:** `filing.series_project()` returns the project the meeting's series is linked to (it returned `None` since W4). Tasks and milestone proposals from such a meeting go to that project, confident, no "Check project".
3. **Projects screen, Recurring meetings checklist** (designer's `pjSeries`): a row per recurring meeting (name, cadence), ticked for this project, "Linked to {project}" on the right when another project has it; clicking a row links or unlinks. The list sub-line is now **"N open tasks · M recurring meetings"** (A41; "meeting" for 1). `ProjectOut.recurring_meetings` carries M.
4. **`GET /api/series/{id}` has `sprint_note`**, drawn under "Next meeting" as **"Opens Sprint N"** (teal, 12px, as the designer's `srSprintNote`).
5. Deleting a project now unlinks its recurring meetings (`projects.delete`; no foreign key to cascade).

## Why
The designer draws the checklist and the sprint line; the audit found the UI off or missing only because no API wrote `Series.project_id` and the Series page had no sprint field.

## How: decisions
| Topic | Decision |
|---|---|
| **Who may link (Opus review point)** | Owner or admin of the series' team only, else **403 `team_forbidden`**. Same rule as creating, changing and deleting a project: linking decides where the whole team's tasks are filed. Not the series' creator, not any member who can read it (a series is readable by a member who made it or can read one of its meetings). Order: the series is looked up first (404 when the caller cannot see it, as for reading it), then the role (403), then the project (404 when not on the series' team) |
| One project per series | Linking to a second project **moves** it (the designer's checklist does the same: ticking removes it from the other project and the other row said "Linked to ..."). One column, so no extra table |
| Concurrency | The project row is locked `FOR SHARE` while the link is written, so a concurrent project delete cannot leave a link to a gone project. Stray ids are harmless anyway: `filing.series_project` joins to a project of the same team, and `GET /api/series` ignores a project not on the team |
| What the checklist lists | `GET /api/series` (existing): only the recurring meetings the caller may see (private recurring calls of teammates are not listed). `recurring_meetings` counts the same set, so the list line and the ticks agree. A manager cannot unlink a series they cannot see (404): they can unlink by deleting the project |
| Count in the list | The screen counts from the checklist rows it holds, so ticks update both lists without a reload; the API field is the same number at load |
| **Sprint line rule** | **Reused, not new:** `mail_summary.opening_sprint(session, viewer, series, next_meeting_start, viewer_zone)`, the function behind the one-hour mail's "Sprint 13 opens". A sprint opens on its start date or the first working day after the previous sprint's end; the next meeting must be on that day in the viewer's zone; no earlier meeting of the series since the sprint began (viewer's readable meetings and own calendar). The page's capture and the mail's capture agree (captures 35 and 39) and a test checks both |
| **"Closes Sprint N" not built** | The designer's Friday review shows "Closes Sprint 12". **No rule exists to reuse:** sprints close when their end date passes (`sprints.rollover_all`) or by hand, not at a meeting, and `opening_sprint` has no mirror. Inventing one was refused; `sprint_note` is null for such a meeting. Proposal for Adebayo: "Closes Sprint N" when the next meeting is on the day the sprint's end date falls (the Friday before a Monday opening) and it is the last meeting of the series in that sprint |
| Sprint line failures | A failing sprint read gives `null` and the page is still served (savepoint, one warning with the error type), like the mail's extras |
| Designer gating | The design shows the line only when sprints are aligned and 5-day; the rule here is the mail's, based on the real sprint dates, so it also shows for other lengths when a meeting really opens a sprint |

## Lead-designed pieces (nothing drawn by the designer; to be approved)
* Nothing is drawn when a team has **no recurring meeting** (the designer drew none for that case): title and help line only. An empty-state line would be new wording: ask first.
* A member sees the ticks with no pointer and `aria-disabled`; the design has no member view (the screen's other fields are switched off the same way).
* A failed load of the recurring meetings is the screen's existing "Could not load projects. Try again" state.
* A refused or failed tick shows the API's message in the screen's existing red alert.

## What's involved
| Area | Files |
|---|---|
| Link rule | `app/series.py` (`link_project`), `app/api_series.py` (`put_series_project`, `_sprint_note`, `ProjectLink` on cards), `app/series_schemas.py` |
| Filing | `app/filing.py` (`series_project`), `app/projects.py` (`delete`) |
| Project list | `app/api_projects.py`, `app/api_schemas.py` (`recurring_meetings`) |
| Web | `web/components/app/projects-view.tsx`, `series-view.tsx`, `web/app/projects/page.tsx`, `web/lib/api/client.ts` (`series.linkProject`), `web/styles/pages.css`, generated `web/lib/api/schema.d.ts`, `docs/openapi.json` |
| Mock and shots | `web/scripts/mock-api.mjs`, `web/scripts/parity-shots.mjs`, `docs/ui/parity/after/app-{projects,projects-second,series,series-friday}-*.jpg` |

## Verification
* pytest (Postgres 16 on port 54344): `tests/test_series_project_link.py` (new, 15), `tests/test_projects_filing.py` (3 new), plus `test_series`, `test_projects_api`, `test_mail_summary`, `test_series_mail`, `test_api`, `test_team_visibility`: 316 passed. `ruff check` and `ruff format --check` clean.
* web: `npm run typecheck`, `npm run lint` (no new warnings), vitest on the projects, series, tasks-projects and meeting-series tests: 80 passed (new tests are fed the captures).
* openapi regenerated; only one route added (`/api/series/{series_id}/project`), none lost.
* Real HTTP captures (guide); parity shots at 1440, 768, 390 against the designer's file. Not live: the real extraction run filing into the linked project (needs the model; unit-tested).

## Operate / roll back
No migration, no config. Revert the PR; links already written stay in `series.project_id` and are ignored until the code returns. Nothing needs the server restarted beyond the normal deploy.

## Follow-ups
* "Closes Sprint N" needs a decision (proposal above).
* Empty-state line for no recurring meetings, if wanted.
* Parity gap that is not this PR's: the new designer file also draws "Keeps coming up", "Suggested agenda" and the past-meetings block on the Series page (audit B rows).
