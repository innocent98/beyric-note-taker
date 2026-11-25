# SOP: The Recipes page, understandable at first sight

**What shipped.** The Recipes page and the Make window were reordered and simplified so a non-technical owner can use them: the person's own recipes first, the ready-made recipes tucked under one searchable heading, a plain description with one button instead of a greyed-out form, and a plainly named brief. PR: `feature/recipes-page-simple` (number in the PR). Web only: no backend, no API change, no migration. Branched from `origin/main` with `fix/workspace-follows-documents-and-recipes` (#286) merged in, so the two PRs do not conflict (merge #286 first; this PR then shows only its own files).

## Why

Adebayo tried Recipes in production: "bad UX, a child could not grab a thing". What he saw (`docs/guides/img/before-recipes-1-top.jpg`, `before-recipes-2-starter.jpg`): the Team brief first (a big empty box), then a list of about twenty General and School starters, "Team recipes" and "My recipes" out of sight below them, and, on clicking a starter, Output, Made from, Use the team brief and Choices greyed out with "A starter cannot be changed". No sentence said what a recipe is, and "Team" was wrong in a personal workspace.

## How, and what was decided

Ideas were taken one by one; the smallest that fixes the first-sight problem won.

| Idea | Decision |
|---|---|
| 1. Lead with the person's own things | **Done.** Left list: My recipes, Team recipes (a team workspace, or once there is one), then one "Ready-made recipes" block. Empty states say what the block is. My recipes' empty state has the one button "Browse ready-made recipes" (it opens the ready-made block). The brief moved under the recipes. |
| Ready-made list: collapse or search | **Both, cheaply.** Shut by default ("Show the 18 ready-made recipes"; the button is the only thing shown), a search box once open (name or kind, e.g. "email"), General and School as sub-labels. Always shut by default, not "open when you have nothing": one simple rule, and the empty state's button opens it. Rejected: a tab strip or a second page (more UI, more to learn). |
| 2. One plain action per ready-made recipe | **Done.** `RecipeEditor` mode `view` (a starter, or a team recipe read by a member) no longer draws disabled inputs. It shows What it does (first sentence of the instruction), What you get ("Makes an email from one meeting."), Beyric will ask you (the choices), Facts it can use, and "Read the whole instruction" (a `<details>`). Primary button **Use this recipe**: `POST /api/recipes` with the starter's fields, named "My <name>", `visibility: personal`; the copy opens in the editor with a note. Named "My ..." so it is told apart from the ready-made one in the Make window. Quiet button **Change it before saving** keeps the old unsaved-copy path: it is the only way to make a copy the team's, and a capability must not go. No per-starter hand-written description exists in the data and there is no backend change, so the instruction's first sentence stands in (`firstSentence` in `web/lib/recipes.ts`). |
| 3. One-line explanation, plain label for the brief | **Done.** Page subtitle: "A recipe tells Beyric what to write from a meeting. Pick one, or make your own." Brief is "Workspace brief" in a team and "My brief" in the person's own space (`briefWords`, from `team.is_personal`); the line says "Facts about your business that Beyric can use when it writes: services, prices, terms..."; and "Only you can read this" (personal) or "Everyone on <workspace> can read this". The editor checkbox follows ("Use the workspace brief" / "Use my brief"). The server's refusal sentences still say "team"; they are the server's words and were left. The rest of the app (sidebar, Make window select) still says "Team"; not changed here. |
| 4. Make window | **Done, small.** Order: My recipes, Team recipes, then "Ready-made recipes (18)" behind a link, shut as soon as the person has their own or their team's recipes (open otherwise, and open when the chosen one is ready-made). It opens on the recipe made last in this browser (`localStorage`, `beyric.make.last-recipe`, wrapped in try/catch; the first of My recipes, Team, then ready-made when none). Per-browser, not stored on the server: no new backend. |
| Nothing selected on arrival | The page used to open on the first starter. It now opens on a short "Pick a recipe" note (`recipe-intro`) so a starter is not shown as if it were the person's. After archiving or a "recipe is gone" the note comes back (the error sentence is shown in it). |

## What's involved

| Path | Change |
|---|---|
| `web/components/app/recipes-view.tsx` | list order, empty states, shut/open ready-made block with search, intro pane, brief moved below |
| `web/components/app/recipe-editor.tsx` | mode `view` is a description plus "Use this recipe" (`makeMine`) and "Change it before saving"; read-only code paths of the form removed; `onSaved(recipe, note?)` |
| `web/components/app/brief-editor.tsx` | "My brief" / "Workspace brief", plain line |
| `web/components/app/make-dialog.tsx` | order, shut ready-made group, last-made recipe remembered |
| `web/lib/recipes.ts` | `briefWords`, `makesSentence`, `firstSentence`, `recipeMatches` |
| `web/styles/pages.css` | `.pick-sub`, `.pick-search`, `.pick-empty`, `.recipe-facts`, `.recipe-use`, `.mk-toggle`... |
| `web/tests/recipes-view.test.tsx`, `make-dialog.test.tsx`, `brief-editor.test.tsx` | updated deliberately, new cases for each decision above |
| `docs/guides/recipes-and-documents-walkthrough.md`, `docs/guides/img/` | walkthrough rewritten for the new page; `before-*` and `after-*` screenshots; the six obsolete screenshots removed |

## Verification

- Touched files' vitest only (repo rule): `recipes-view`, `make-dialog`, `brief-editor`, `meeting-make`, `recipes-nav`, `workspace-follows-pages`: 103 passed. `npm run typecheck` clean; `eslint` on the touched folders: no errors.
- Live, on the real API (scratch Postgres on migration 0064, uvicorn) behind the Next dev server, as an owner of "Platform team" and of "My team (personal)": the page as above; Use this recipe made `POST /api/recipes` (copy named "My Follow-up email"), the copy appeared under My recipes and, on a ready meeting, first in the Make window with the ready-made group behind "Ready-made recipes (18)"; the brief title was "Workspace brief" in the team and "My brief" in the personal space; at 390 px no sideways scroll (`scrollWidth` 390). Screenshots: `docs/guides/img/before-recipes-1-top.jpg`, `before-recipes-2-starter.jpg`, `before-make-dialog.jpg`, `after-recipes-1-top.jpg` to `after-recipes-6-phone.jpg`, `after-make-window.jpg`.
- **Not verified:** the full web suite and the production build (CI runs them); a member's view of a team recipe live (covered by a vitest case only); saving a copy as "Everyone on the workspace" live; the remembered recipe across two real browsers.

## Operate / roll back

Web only. Revert the PR. The only thing stored is the optional `localStorage` key, which a reverted build ignores.

## Follow-ups

- Hand-written one-line descriptions for the 18 starters (needs a backend field); today the instruction's first sentence is shown.
- "Recently used" per person on the server (so it follows them across browsers).
- The rest of the app still says "Team" where Recipes now says "Workspace" (sidebar switcher, Make window select).
