# Multi-team, stage 3: the frontend (ships dark)

**What shipped.** The screens for several teams, behind the server's `MULTI_TEAM_ENABLED` switch: a
**team switcher** in the app shell, a **Teams** screen (list and make a team), and the meeting page's
**team card** (share to a team, **Move** with its confirmation, **Move back**, "Former member"). Plus the
small backend parts the stage-2b review left for this stage: Slack and Linear are refused on a
personal team, and one read-only boolean, `multi_team`, in the session. PR: see the checklist
(`docs/checklist/master-checklist.md`, "Multi-team"). **No migration** (head stays 0046).

**Dark.** With `MULTI_TEAM_ENABLED` off (the default, and nothing here touches `.env.example` or any
deploy file) the app looks and behaves exactly as before: no switcher, no Teams link, no `/teams` page
(404), no Move, the old share switch, no header sent. **Do not switch it on** before Adebayo's lawyer has read
the privacy policy and terms (a moved meeting survives its recorder's account; spec 9.1). The wording
of those pages is not touched here.

Spec: `docs/superpowers/specs/2026-11-13-multi-team-design.md` (6.3, 6.4, 6.7, 9). Contract:
`docs/fe-integration-guide-teams.md` (new sections 14 to 16; captures `docs/fe-captures/teams/` 71 to 75 and the
session bodies of 02 and 03, made by `scripts/capture_teams_api.py`).

## Why

Stages 1 and 2 made several memberships, team-owned meetings and an explicit team per request
possible on the server, but no screen could use them: a person could not choose a team, make one, or move
a meeting. The switch could only go on once the screens understood two teams.

## How, and the choices made

| Question | Choice | Why / rejected |
|---|---|---|
| How does the frontend learn the switch is on? | **`multi_team: bool` in `GET /api/session`** (and the login and logout answers, same shape), required, a server setting | `GET /api/teams` cannot say it plainly (`can_create_more` is also 0 at the limit), `can_move` is false for unfinished meetings, and `POST /api/teams` answering `multi_team_disabled` is a write. One boolean with a test and a capture. |
| Where does the active team live? | **Per tab.** `?team=<id>` in the URL on Team, Tasks, Projects, Settings (the server render can read only that); `sessionStorage` for the pages without it (the meeting page). The default team is "no choice": no parameter, no header | A cookie or `localStorage` is shared by tabs, so two tabs would fight; a server-side "current team" is forbidden by the spec. |
| How is it sent? | **One interceptor** (`web/lib/api/client.ts`, `isTeamScoped`, `teamId` option, per-call `team` override), used by the browser client and by `serverApi({ teamId })` | The header goes on exactly the routes that read it (guide section 16 lists them) and on no other. |
| A stale or removed team | Server render: `?team=` not in `GET /api/teams` redirects to the same page without it plus `?notice=team-gone`. Browser: a team-scoped call that carried the header and got **404 `No such team.`** resets the tab to the default team and reloads with the same notice. A remembered team no longer in the list is dropped with the notice | The 404 shares `code: not_found` with "no such task", so the frontend recognises it by the exact message and by having sent the header (documented in the guide, section 16). |
| What does the switcher look like? | **A native `<select>`** with the app's own `.select` (sidebar on desktop, the slim top bar on phones), the personal space labelled "(personal)", plus a "Teams" sidebar link | Native select = keyboard, screen reader and phone picker for free; no new visual language. A custom listbox was rejected as more code and more a11y risk for no gain. |
| Which screens carry the team? | The sidebar's Tasks, Projects, Team and Settings links carry `?team=` (`TeamNavLink`); a team page opened without it while a team is remembered is replaced by that team's (`ActiveTeamSync`) | So a person never reads the default team's data believing it is another's. |
| The meeting page | A **team card** replaces the share switch while the switch is on: choose one of your non-personal teams, **Share** / **Stop sharing** / share to another team, **Move to team** (finished meetings the recorder owns: `can_move`), **Move back** (`can_move_back`, hidden when the recorder's account is gone). Series and milestones use the **meeting's** team | `can_share`, `can_move`, `can_move_back` and `shared_by: "Former member"` come from the API; the page never decides who may. |
| The Move confirmation | A native `<dialog>` with his exact words: "This meeting will belong to the team. It stays with the team even if you leave or delete your account." plus the consequences the guide asks to surface (tasks move, an assignee who is not in the team is unassigned, Slack posts already made stay) | His decision (spec 9.1). |
| Integrations on the personal space | Server: **409 `personal_team`** on `PUT /slack`, `POST /linear/teams`, `PUT /linear` while the switch is on (not with it off: the team of one is the only team). UI: no Connect on a personal space; a row is drawn only for an integration that is ALREADY connected (Disconnect and the reason), so an old connection stays manageable. The owner has not confirmed that integrations stay off personal teams: the spec rule is kept and he may reverse it | Spec 6.9. Reading and disconnecting stay allowed. |
| CORS, proxy | **Nothing to change**, checked: the browser calls the same origin; `web/next.config.ts` proxies `/api/*` to FastAPI with all headers; `deploy/nginx.conf` has no header allow-list (`X-Team-Id` has no underscore, so nginx passes it); FastAPI has no CORS middleware | The stage-2b review could not verify this. |

## What's involved

| Area | Files |
|---|---|
| Backend | `app/api_schemas.py` (`SessionState.multi_team`), `app/api.py` (`_session_state`), `app/api_integrations.py` (`refuse_personal_team`), `docs/openapi.json` (regenerated), `tests/test_multi_team_stage3.py` |
| API client | `web/lib/api/client.ts` (`isTeamScoped`, `teamId`, `onTeamGone`, `teams.*`, `meetings.share/move/moveBack`, per-call `team`), `browser.ts`, `server.ts` (`getMultiTeam`, `getTeams`, `getShellTeams`, `requireTeamOwner`), `schema.d.ts` (regenerated with `npm run gen:api`), `types.ts` |
| Active team | `web/lib/active-team.ts`, `components/app/use-active-team.ts`, `active-team-sync.tsx`, `team-nav-link.tsx`, `team-switcher.tsx` (lead-designed) |
| Shell | `components/app/shell.tsx` (`teams` prop, mark, links), `shell-nav.tsx` (`topbar` slot), `styles/components.css` |
| Screens | `app/teams/page.tsx`, `components/app/teams-view.tsx` (lead-designed), `meeting-team-card.tsx` (lead-designed), `meeting-view.tsx`, `team-view.tsx` (personal-space note), `settings-planned-sections.tsx` (`integrations`), `series-picker.tsx`, `meeting-milestones.tsx`, the team pages (`team`, `tasks`, `projects`, `settings`, `meetings/[id]`), `lib/routes.ts` and `proxy.ts` (`/teams`) |
| Mock and captures | `web/scripts/mock-api.mjs` (`--multi-team on`), `scripts/capture_teams_api.py`, `docs/fe-captures/teams/` |
| Evidence | `web/scripts/parity-multi-team.mjs`, `docs/ui/parity/after/app-multi-team-*`, `docs/ui/parity-report.md` |

## Lead-designed (the designer has nothing for these; for the designer to review)

1. **The team switcher** (sidebar and phone top bar). 2. **The Teams screen** (list, Open, Make a team).
3. **The meeting page's team card** and its **Move** and **Move back** confirmation dialogs.
4. **The personal-space note** on the Team screen (and "People on {team} ..." subtitle). All are built from
`web/components/ui` and the existing styles; the only new CSS is spacing (`.team-switch*`, `.teams-*`, `.mt-*`).
Marked "lead-designed" in the code comments, the PR body and `docs/ui/parity-report.md`.

## Verification

- **Backend** (scratch Postgres 17): `tests/test_multi_team_stage3.py` (7: the flag on and off, logged in and out; Slack and
  Linear refused on a personal team, on the default personal team of a person with two teams, allowed on an
  ordinary team, read still allowed, allowed with the switch off), plus `tests/test_integrations.py` and
  `tests/test_integrations_linear.py` (98 passed together). `ruff check` and `ruff format --check` clean;
  `scripts/export_openapi.py --check` clean.
- **Frontend**: `npm run lint`, `npm run typecheck`, `next build` pass. New vitest files: `team-header` (40: which routes
  carry the header and which do not, per tab, per call, the team-gone hook), `active-team` (22), `team-switcher`
  (15: switcher, shell, sync), `team-server` (10: `requireTeamOwner`, the flag), `teams-view` (9),
  `meeting-team-card` (25: Move dialog words, errors in the guide's strings, Former member, flag-off page) and additions to
  `settings-page` and `team-view`. The 26 test files that touch the shell, the client or the meeting page
  pass (a named set; CI runs everything).
- **Live captures** (guide section 14 to 16): the session flag on and off, the integrations refusal.
- **Parity**: see `docs/ui/parity-report.md`, "Multi-team (stage 3)": the screens the design covers, shot with the switch off, match as
  before; the lead-designed ones are shot alone at 1440, 768 and 390 px.

**Not verified.** Real FastAPI with a browser (the screens were run against `web/scripts/mock-api.mjs --multi-team on`, which replays
the captured shapes); Safari and Firefox; a screen reader (the switcher is a native labelled select and the dialogs
are native `<dialog>`s, tested for names and roles, not heard); Playwright e2e (the project keeps none; `scripts/owner-e2e.mjs` was not run);
`MULTI_TEAM_ENABLED` on a real server; the invitation flow into a second team from the UI.

## Operate / roll back

Nothing to deploy specially: no migration, no new setting. To roll back, revert the PR; the server keeps working with the switch off or
on. To try the screens locally: `MULTI_TEAM_ENABLED=true` in your own dev `.env`, **never** in `.env.example` or deploy files.

## Follow-ups

- Switch-on (stage 4) still waits for the privacy policy and terms to be read by his lawyer and the second personal-team backfill (checklist).
- There is still no route to delete a team or hand one on, so "three owned teams" never frees up by itself (stage 2b follow-up).
- The Meetings list is not filtered by team (a person's meetings are theirs); a "moved to team X" tag on a list row is not built.
- A designer pass over the four lead-designed pieces.

## Review fixes (lead's review of the first push, same PR)

| Finding | Fix |
|---|---|
| Task drawer on the meeting page used the tab's team | `ActionTasks` takes the meeting's team; `tasks.list`, `projects.list`, `milestones.list`, `sprints.list` and `loadSprintBook` accept a per-call team (a shadowed variable in `loadSprintBook` was found by the new test). Test: `action-tasks.test.tsx`. `/api/meetings/N/series` and `/recurring` asserted not team-scoped |
| Remembered team survived logout and login | Stored with a hash of the signed-in address (the shell's mark carries it) so another person never reads it; cleared on login and logout through the client (`onSessionChange`) |
| Personal space with Slack or Linear already connected became unmanageable | Shown with Disconnect and a plain reason when connected; no Connect (see the table above) |
| Endless reload when the team in a 404 cannot be changed | `onTeamGone` fires only for the tab's own team, never for a call's explicit one (a meeting's); never reloads twice within 15 s |
| `parseTeamId` took ids the server rejects | Capped at 2147483647 |
| Teams list failed with the switch on: old share switch (422 `team_required`) | A plain error with Try again on the meeting page |
| Parity claimed a Move back dialog shot | `meeting-move-back-dialog-*` taken |

**Known limits and records (not fixed here):**
- The **Decisions** and **Series** pages open the same task drawer, but a decision or a series carries no team id, so the drawer reads the tab's team there: a task of another team opened from those pages can show the tab's people and projects. Fixing it needs a team on those payloads (a backend change).
- The switcher navigates on every `change` (arrow keys through a closed select fire one navigation each in some browsers).
- Choosing a team on a page that is not one team's (Meetings, a meeting) shows no feedback; it is only remembered.
- A team page opened without `?team=` while a team is remembered shows the default team's data for a moment before `ActiveTeamSync` replaces the URL.
- Moving or moving back remounts the meeting's task list (so its cached pickers are read again); other open tabs keep theirs.
- The switch-off parity images come from the same build as the switch-on ones, not from `main`'s.
- `/teams` with the switch off is a Next 404 for a signed-in person and a /login redirect for a signed-out one (it was a FastAPI 404 before).
