# SOP: Documents, Recipes, Ask and Notifications follow the active workspace

**What shipped.** `/documents`, `/recipes`, `/ask` and `/notifications` are now team pages: the sidebar links carry `?team=<id>`, the server render reads the workspace in the address, and each view is rebuilt when the workspace changes. A draft opened by its link moves the tab to the workspace it was made in. Plus a walkthrough for the owner (`docs/guides/recipes-and-documents-walkthrough.md`) and a hint under the recipe list. PR: branch `fix/workspace-follows-documents-and-recipes` (number in the checklist entry). No backend change, no migration.

## Why

Adebayo, after the Workspaces release (#282): the Documents page showed a board he made in one workspace under "Mine" in **every** workspace; the Recipes page showed a Team brief and could not be told apart from "where are My recipes and Team recipes", and Output, Made from, Use the team brief and Word choice were greyed out.

**Root cause (reproduced, hypothesis confirmed).** The backend scopes by `X-Team-Id` correctly (`app/api_recipes.py::list_documents`, `app/recipes/visibility.py::made_in_clause`). The web app has two places that hold the active workspace: the address (`?team=`, the only one a server render can see) and this tab's `sessionStorage` (what browser calls send). `web/lib/active-team.ts` `TEAM_PAGES` listed Meetings, Team, Tasks, Projects, Decisions, Review, Settings, Templates and Vocabulary but **not** Documents, Recipes, Ask or Notifications, so `withTeam` never added `?team=` to their sidebar links. Their server render therefore always used the default workspace, while the browser's calls (the "Shared with me" tab, "Load more", the Make window) used the chosen one: two answers on one page. `/documents` also used `requireOwner()` (no workspace at all); `/recipes` already used `requireTeamOwner`, which was defeated by the missing `?team=`.

Reproduced live (local API on a scratch Postgres, Next dev server, two workspaces with a draft each): with the switcher on "My team (personal)", the sidebar links were `/recipes` and `/documents` with no `?team=`, `sessionStorage` held the personal team, and Documents "Mine" showed the Platform team's draft while Recipes showed the Platform team's brief. Screenshots: `docs/sop/img/2026-11-25-before-documents-personal.jpg`, `docs/sop/img/2026-11-25-before-recipes-personal.jpg`.

The greyed fields were not a fault: they are the read-only view of a **starter** (`RecipeEditor` mode `view`), and "My recipes" / "Team recipes" are left out while empty and sit at the very bottom of a list of about twenty starters.

## How, and what was decided

| Decision | Choice |
|---|---|
| Where the fix lives | The existing mechanism, not a new one: add the four pages to `TEAM_PAGES` and use `requireTeamOwner(path, searchParams)` on them, exactly like Tasks and Templates. `ActiveTeamSync` then also "follows" a remembered workspace when one of these pages is opened without `?team=` (a bookmark, a link inside a page). |
| Ask and Notifications | Same bug class (their routes are workspace-scoped: `app/api_ask.py`, `app/notifications.py`; the old comment said they were "not one team's" and the server read the default workspace's scope and list). Included because the cause and the fix are identical. |
| Stale state | Following a remembered workspace is a client-side `router.replace`, which keeps component state. `DocumentsView`, `RecipesView`, `AskView` and `NotificationsView` keep their rows in `useState(initial)`, so each is keyed by the workspace (`key={teamId ?? "default"}`). Found in the live run: without it the URL changed but the old rows stayed. The normal switch (a full page load) never needed it. |
| A draft by id | `/documents/[id]` is not a team page (it opens by its link). It mounts the existing `MeetingWorkspaceSync` with the draft's `team`: the tab moves to the draft's workspace and toasts "Switched to <name>", so Make again, Share and the sidebar name the right workspace. Rejected: making it a team page (a link would need the workspace in it). |
| Make window, document detail calls | Already followed `sessionStorage` through the browser client; nothing to change beyond the sync above. |
| Recipe list hint | One sentence at the bottom of the list when a group is empty ("My recipes appear here, under the starters, once you copy a starter ("Make a copy to change") or press New recipe."; for a non-personal workspace also "Team recipes appear here once an owner or admin of <name> saves one for everyone."). The Make a copy button already exists. Not done (suggestions below): reorder the list, show empty groups. |

## What's involved

| Path | Change |
|---|---|
| `web/lib/active-team.ts` | `TEAM_PAGES` += `/recipes`, `/documents`, `/ask`, `/notifications`; comments |
| `web/app/documents/page.tsx`, `ask/page.tsx`, `notifications/page.tsx`, `recipes/page.tsx` | `requireTeamOwner(path, searchParams)`; views keyed by workspace; Ask and Notifications take `searchParams` |
| `web/app/documents/[id]/page.tsx` | `MeetingWorkspaceSync` with the draft's workspace |
| `web/components/app/shell.tsx` | Ask and Notifications links are `scoped(...)` (the Notifications unread badge is kept) |
| `web/components/app/recipes-view.tsx`, `web/styles/pages.css` | hint under the list, `.pick-hint` |
| `web/tests/workspace-follows-pages.test.tsx` (new), `active-team`, `team-switcher`, `team-scoped-screens`, `ask-page`, `recipes-view` tests | new and updated (the old tests asserted Ask and Notifications were not team pages) |
| `docs/guides/recipes-and-documents-walkthrough.md`, `docs/guides/img/` | owner walkthrough, 10 screenshots from the live run |

## Verification

- Failing tests first (`workspace-follows-pages.test.tsx`: 11 failed), then green. Touched files' tests only, as the repo rule says: `workspace-follows-pages`, `active-team`, `team-switcher`, `team-scoped-screens`, `ask-page`, `ask`, `recipes-view`, `recipes-nav`, `documents-page`, `team-server`, `team-proxy`, `workspace-switch`. `npm run typecheck` clean; `eslint` on the touched folders: no errors (warnings are old).
- Live, same stack as the reproduction, after the fix: on "My team (personal)" Documents Mine shows only the personal draft and Recipes shows the personal brief; on Platform team the other two; Ask's scope counts the active workspace's meetings; opening `/documents/1` from another workspace showed "Switched to Platform team" and the switcher followed; the Make window listed the active workspace's recipes and brief; the new hint rendered for a member. Screenshots in `docs/guides/img/`.
- **Not verified:** the full web suite and the production build (CI runs them); the deployed app behind nginx; the "Shared with me" tab with a real teammate share (the share path is the backend's, untouched here); saving a recipe as "Everyone on the workspace" from a fresh copy; a finished model-written draft in this run (the writer was not running).

## Operate / roll back

Web only. Revert the PR; nothing is stored. A tab with an old remembered workspace opened on `/documents` is sent to `/documents?team=<id>` by `ActiveTeamSync`.

## Follow-ups (suggestions, not done)

- ~~The recipe list is long and the person's own recipes sit at the bottom.~~ Done in `docs/sop/2026-11-25-recipes-page-simple.md`.
- ~~The "Team brief" box is titled "Team brief" even in a personal workspace.~~ Done in the same SOP ("My brief" / "Workspace brief").
- Series pages (`/series/[id]`) are opened by id like a meeting; they were not changed here and their server read is not workspace-aware either. Check whether its first read needs a workspace sync.
- The page-level state in Tasks, Projects, Templates and Vocabulary views may also survive a "follow" without a key; not checked.
