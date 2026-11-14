# Lists and counts follow the active team, one Teams entry, rename, reload on switch

**What shipped.** After multi-team went live, Adebayo walked through it and asked for plain behaviour. One PR (branch `feature/team-scoped-lists-and-nav`; PR number in the checklist, "Multi-team"):

1. The **Meetings list (the home page `/`, which stays at `/`)**, the **Decisions log**, the **Review list** and the **two sidebar badges** (Tasks "new", Review) show **one team at a time**, the active one.
2. **One "Teams" entry** in the sidebar (no separate "Team" link). The switcher at the top is unchanged.
3. **Rename a team**: a Team name field for owners and admins of an ordinary team; the personal team keeps its fixed name.
4. **Switching team reloads the page** (a full load on the new team's address).

**No migration** (head stays 0047). **No new setting.** Contract: `docs/fe-integration-guide-teams.md` section 17 (captures `docs/fe-captures/team-scoped-lists/`, made by `scripts/capture_team_scoped_lists.py`).

## Why

- He found **stale mixed state after switching team**: the Meetings, Decisions and Review lists and the badge counts ignored the active team and showed everything the person can read (`tasks.new_count` counted across all teams: **41 beside Tasks and an empty list** on the personal team).
- **Two sidebar links (Team, Teams)** for one idea.
- **He could not rename a team.** Root cause: `PATCH /api/team` already renamed (owner or admin) but **no screen had a name field**. It was never built; the personal team was not the cause (it only needed a rule of its own, below).

## How, and the choices made

| Question | Choice | Why / rejected |
|---|---|---|
| What is on a team's list? | Ordinary team X: meetings **shared to X or owned by (moved to) X, whoever recorded them**. Personal team: **only meetings the person recorded that are neither shared nor moved**. | His rule. A person in X and Y sees each readable meeting on exactly one list (tested: the three lists do not overlap and lose nothing). |
| Where is the filter? | `visibility.active_team_clause` and **`visibility.readable_in(user, active)`** = `can_read_clause AND` the team clause. Meetings, decisions, review items, `open_review_count` and the counts all go through that one function. | "Keep it going through `visibility.py`, the one place that decides access." The team **only narrows**; it can never widen a read (tested: a team never shows another person's private meeting). Rejected: filtering in each route (counts would drift from lists). |
| How is the team named? | The same `X-Team-Id` header (no server-side "current team"). `team_admin.active_team` resolves it: 404 `No such team.` for a team not the caller's. | Section 10 of the guide. |
| No header? | **Multi-team on: the person's default team.** Off: no narrowing (as before). | Spec 6.7 ("an old client gets its default team"). Needed so the SSR session (read before the page parses `?team=`) and an old client count one team. **One existing behaviour changed with it:** the three tests in `test_multi_team_leaks.py` / `test_multi_team_derived.py` that listed X's meetings with no header now send `X-Team-Id` (they test the read rule, not the scope). `GET /api/tasks` with no header is **unchanged** (every team's tasks). |
| Badges | `badges.tasks_new` = New tasks with `Task.team_id == active` (the same filter the board list applies, so count = list); `badges.review_open` = `open_review_count` with the same scope. **`GET /api/session` never fails over a stale team** (it counts the default team): it is every page's first call. | A 404 there would break the whole app for someone who left a team. |
| Where does the frontend get the team? | `?team=` is now also on **`/`, `/decisions`, `/review`** (`TEAM_PAGES`). **`proxy.ts` hands the address's team to the server render as `x-active-team`** (always rewritten from the address, never believed from the client) so the session call, made first, counts that team. **Every browser and server call names a team, the default included** (`SHELL_DEFAULT_TEAM`, `resolveTeam`), so the list, its counts and the Tasks board agree. | `GET /api/tasks` with no header is every team's, which would not match the badge. |
| A page without `?team=` (meeting page, Ask) | The server draws the badge for the default team; the browser **counts it again** for the tab's remembered team (`TeamNavLink`, `live`). | The server cannot see sessionStorage. |
| Switching | `TeamSwitcher` now calls `window.location.assign` (`pageLoad.to`): team pages get the new `?team=`; other pages load again. | His request: a full load so nothing of the old team is left. |
| One Teams entry | With the switch on the "Team" link is gone; "Teams" is current on `/teams` and `/team`. A team's page has **All teams** and **Make a team** links back to `/teams` (the list has the make-a-team form). Switch off: the "Team" link as before. | His request. |
| Rename | A **Team name** card on the Team screen for `can_manage` on an ordinary team (switch on); `PATCH /api/team {"name"}`; then `router.refresh()` so the switcher shows it. **Backend: 409 `personal_team` for a `name` on a person's own personal team while multi-team is on** (other settings still change; switch off: unchanged). | He asked for the personal team's fixed name. |

## What's involved

| Area | Files |
|---|---|
| Backend | `app/visibility.py` (`ActiveTeam`, `active_team_clause`, `readable_in`), `app/team_admin.py` (`active_team`, `active_team_or_default`), `app/services.py` (`list_meetings`), `app/api.py` (meetings list, session), `app/workspace.py` (`list_decisions`, `decision_counts`, `due_this_week`, `open_review_count`, `list_review`), `app/api_workspace.py` (decisions, review), `app/tasks.py` (`new_count`), `app/api_team.py` (rename guard), `docs/openapi.json` |
| Frontend | `web/lib/active-team.ts` (`TEAM_PAGES`, `SHELL_DEFAULT_TEAM`, `ACTIVE_TEAM_HEADER`, `activeTeamForRequest`), `web/lib/api/client.ts` (`isTeamScoped(path, method)`), `web/lib/api/server.ts` (`resolveTeam`, `getSession`), `web/proxy.ts`, `web/components/app/shell.tsx`, `team-nav-link.tsx`, `team-switcher.tsx`, `team-view.tsx`, `teams-view.tsx`, `meeting-list.tsx` (the date filter keeps the team), `web/app/page.tsx`, `decisions/page.tsx`, `review/page.tsx`, `web/styles/pages.css` |
| Tests | `tests/test_team_scoped_lists.py` (new, written first), `web/tests/team-scoped-screens.test.tsx`, `team-proxy.test.ts` (new); updated `team-header`, `team-server`, `team-switcher`, `team-view`, `active-team`, `meetings-*`, `decisions-review-shell` |
| Evidence | `scripts/capture_team_scoped_lists.py`, `docs/fe-captures/team-scoped-lists/` |

**Lead-designed (the designer has nothing for these; marked in the code):** the single Teams entry, the **Team name** card, the **All teams / Make a team** trail on a team's page.

## Every other place that shows meetings or counts across teams (reported, NOT changed)

| Where | Today |
|---|---|
| **Ask Beyric and search** (`app/ask.py`) | Candidates come from `visible_meetings(user)`: **every team**, plus the person's own. Unchanged, as asked. |
| **Notifications** (`app/notifications.py`; the list, the unread count, the sidebar badge) | Every meeting the person may read, **all teams**. |
| **Recurring / series** (`/api/series`, the page, "Keeps coming up", the agenda) | Unchanged on purpose (he is still deciding how recurring meetings map to teams). The Recurring cards on `/` already read the header (`GET /api/series`), so they show the active team's series. |
| `GET /api/tasks` with no header | Every team's tasks (unchanged; the frontend always sends the header). |
| `tasks_count` on each Meetings row | The tasks of that meeting the person may see. |
| Calendar Upcoming, Not recorded | The person's own calendar. |
| `GET /api/teams` (the Teams list) | Roles and member counts only: **no meeting counts**. |
| `app/web.py` (old server-rendered list) | Unfiltered; not used by the new screens. |

## Verification

- **Backend** (scratch Postgres 17): `tests/test_team_scoped_lists.py` (26 tests: personal and each team's meetings, non-overlap, default with no header, switch off, 404 for another's team, a team never widens a read, a meeting opened by link whatever team is active, paging, unshare moves a row between lists, decisions and counts, review and its count, the badges equal the lists, done and suggested excluded, stale team on the session, rename: owner, member, empty, personal, switch off). The named existing files that read these routes pass (`test_multi_team_*`, `test_team*`, `test_api_workspace`, `test_decisions_record`, `test_meetings_date_filter`, `test_api_tasks`, `test_suggested_tasks_api`). `ruff check .` and `ruff format --check .` clean; `scripts/export_openapi.py` regenerated.
- **Frontend**: `npm run typecheck`, `npm run lint` (only old warnings), and the named vitest files that touch the shell, the client, the proxy and the pages pass (CI runs the whole suite).
- **Live captures**: guide section 17 (captures 07 to 32): the three lists per team, the default, the 404, the opened-by-link read, decisions, review, the badges against the board, the stale session, the switch off, rename.
- **Not verified**: the screens against a real FastAPI in a browser (no running server was available; checked against tests and the captured shapes); the sidebar recount in a real browser tab; Safari and Firefox; a screen reader; the `proxy.ts` header on a real Next server (it is unit-tested only); the phone top bar after a switch; how a very long list feels on reload.

## Operate / roll back

Nothing to deploy specially (no migration, no setting). To roll back, revert the PR. A frontend deployed **before** the backend would send the header on lists the old backend ignores (harmless); a backend deployed **before** the frontend gives the old frontend (no header on the default team) the default team's list, which is the intended behaviour.

## Follow-ups

- Search, Ask and Notifications still cover every team (his call to keep them for now).
- The Decisions and Series pages open the task drawer without a team id on their payload (stage-3 known limit, unchanged).
- A "moved to team X" tag on a list row is still not built.
- Series/recurring mapping to teams (he is deciding).
- A designer pass over the three new lead-designed pieces.
