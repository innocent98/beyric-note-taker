# SOP: The Workspaces release (slice W5: final integration into `main`)

**What shipped.** The four workspace slices (W1 foundation, W2 settings, W3 calendar, W4 lifecycle; PRs #270, #274, #278, #279, #277, #276 into `feature/workspaces`) joined with the recipes release that reached `main` first (`main` `910b4fce`, migrations to `0061`): one merge, one migration chain, a rehearsal of the cutover on production-shaped data, a permission review of every workspace path, privacy and terms made true, and the places where recipes and workspaces meet fixed. One PR, `feature/workspaces-final` into `main`; PR number and head are in the checklist entry.

Spec: `docs/superpowers/specs/2026-11-24-workspaces-design.md` (sections 3, 4, 8, 12). Slice SOPs: `2026-11-24-workspaces-foundation.md`, `-settings.md`, `-calendar.md`, `-lifecycle.md`. FE guide: `docs/fe-integration-guide-workspaces.md` (section 10 is new here). Captures: `docs/fe-captures/workspaces-final/` (32 live exchanges), `docs/fe-captures/workspaces-cutover/cutover-rehearsal.txt`.

## Why

Nothing deploys until all slices are one thing: a person who switches workspace sees only that workspace, and nobody loses a meeting or a setting at the cutover. The slices were built on a base that did not have the recipes release, and `main` moved under them. This slice makes the union true, proves the migration on data shaped like production, and reviews what could leak.

## How, and what was decided

| Decision | Choice |
|---|---|
| Merge | `origin/main` into the branch with a merge commit. Conflicts: `app/models.py`, `app/api_schemas.py`, `app/entitlements.py`, `app/feature_gates.py` (both sides kept: they added different things in the same place), `web/lib/api/client.ts`, `web/tests/helpers.ts`, `web/tests/team-header.test.ts` (both sides' imports and helpers; the workspaces rule that **every** `/api/*` call but the person's own names its workspace replaces main's per-route list), and the two generated files, regenerated from this branch's code (see Verification: the export script reads the `app` package of the checkout it is run from only with `PYTHONPATH=.`). |
| Migrations | Re-chained after `0061`: `0070` to `0062` (workspaces core), `0071` to `0063` (settings), `0075` to `0064` (calendar). The placeholder ids were never deployed, so they were renamed outright (files, revision ids, tests, SOPs, checklist). One head. No test asserts a head by number: the only number asserted is the parent of a named migration. |
| Cutover rehearsal | Two layers. (1) `tests/test_workspaces_migration.py`: a seeded cast of 15 awkward people (deleted accounts, hollow rows, a person with no team, shared, moved and private meetings, calendar connections, templates, vocabulary, notifications, joins) now run from `0061` and with the recipes release's tables filled (recipes, drafts, a share, team briefs). (2) `scripts/rehearse_workspaces_cutover.py`: 401 users in the shape of the real data, migrated `0061` to head, the downgrade to `0061`, and the upgrade again; counts below. |
| Recipes meet workspaces | (a) The meeting picker's sprint scope used `visibility.active_team_clause`, which the workspaces rewrite removed: it now filters on `meetings.workspace_team_id` (the container). (b) `request_draft` files a draft in the workspace its meetings live in (the header's when any chosen meeting is there, else the one they all live in, else 404) and passes that workspace to the Make gate, so a free member can Make from a workspace whose owner has a plan (W4's open item) and never mixes two workspaces' content in one draft that could then be shared (review finding 2). (c) `GET /api/documents` is now narrowed to the active workspace (own drafts made in it; with `shared=true` the drafts shared to it), and the leak guard classifies it, `/api/recipes` and `/api/briefs` as workspace data with markers per workspace. `?meeting_id=` stays un-narrowed: it is the meeting page asking about one thing. (d) Tests that used the removed `multi_team_on` fixture, and two share tests that flipped a team to personal after a personal team existed, were updated. |
| Notifications follow the meeting | A notification used to keep the workspace it was born in. `teams.refile_notifications` now re-files the notifications about a meeting in the meeting's CURRENT workspace for every person who is a member of it, and in their own personal workspace for anyone who is not. Called after share, move and move back (`meeting_teams._touched`), when a team gives a meeting back (`deletion.give_back_meeting`), when a workspace is deleted (`teams.dissolve_team`, so the notes survive in the recorders' workspaces instead of going with the team), and on leave or removal (`teams.leave`: the leaver's notes about the workspace's meetings go to their personal workspace, and the notes others hold about what the leaver took home go to those people's personal workspaces). Notifications about no meeting never move. No migration. "Keep private" leaves the meeting, and so its notes, in the team workspace. Tests: `tests/test_workspace_review_fixes.py` (shared, moved, moved back; leave). A person who can no longer READ a meeting but is still a member of its workspace keeps the note in that workspace's bell (a cosmetic label, as before). |
| Personal recipes | A recipe a person wrote for themselves (`team_id` null) is shown in every workspace of theirs. Decided by reading the code, not by the spec (which is silent): it is the person's own text, not another workspace's data. Open for Adebayo (Follow-ups). |
| Privacy and terms | See the table below. Both the Jinja pages (`app/templates/`) and the web components (`web/components/legal/`) say the same, tested equal; `TERMS_VERSION` is `2026-11-25`. No promise to notify anyone of a change (his decision of 2026-11-04). |

### Privacy and terms: each new claim and what proves it

| Claim in the text | Proved by |
|---|---|
| A personal workspace only you can use; each workspace keeps its own meetings, tasks, notifications, templates, vocabulary, calendar connection and settings; the app shows only the workspace you are in | `tests/test_workspace_leaks.py` (every GET route classified; per-workspace markers) |
| Ask Beyric searches only that workspace's meetings you may read | `test_ask_answers_only_from_the_active_workspace...`, `test_ask_inside_a_team_workspace_still_obeys_readability` |
| Meetings recorded in a team workspace are shared from the start when the team's share setting is on (on for new teams); Keep private makes a meeting readable by its recorder only; older meetings unchanged | `tests/test_workspace_birth_share.py`, `tests/test_workspaces_migration.py` (visibility columns identical) |
| Leaving or removal ends access at once, disconnects the calendar, cancels planned joins; what you kept goes back to your personal workspace; a moved meeting stays | `tests/test_workspace_membership.py`, `tests/test_calendar_workspaces.py`, `tests/test_workspace_lifecycle.py` |
| The owner can delete a team workspace when nobody else is in it; its meetings go back to their recorders, private | `tests/test_workspace_lifecycle.py` |
| One calendar per workspace; the same meeting on several is joined once; Google access is revoked only when no other workspace of yours uses the account | `tests/test_calendar_arbitration.py`, `tests/test_calendar_workspaces.py` |
| A member of a workspace whose owner has a paid plan may use Ask and make documents without a plan of their own; if the owner's plan ends, that stops | `tests/test_workspace_gate.py`, and the captures `26` to `28` for Make |
| The text of the pages | `tests/test_legal.py` (including `test_the_pages_say_what_a_workspace_holds...`), `web/tests/legal.test.tsx` |

## What's involved

| Path | Change |
|---|---|
| `alembic/versions/0062_workspaces_core.py`, `0063_workspaces_settings.py`, `0064_calendar_per_workspace.py` | renamed and re-chained |
| `app/recipes/picker.py`, `app/recipes/service.py`, `app/recipes/visibility.py`, `app/api_recipes.py` | the workspace container in the picker, the gate's workspace, the narrowed drafts list (`made_in_clause`, `shared_clause(user, team_id)`) |
| `app/templates/privacy.html`, `terms.html`, `web/components/legal/*`, `app/signup.py` | the wording and `TERMS_VERSION` |
| `scripts/rehearse_workspaces_cutover.py`, `scripts/capture_workspaces_final.py` | the rehearsal and the live captures |
| `tests/test_workspaces_migration.py`, `tests/test_recipes_migration.py`, `tests/test_workspace_leaks.py`, `tests/test_recipe_visibility.py`, `tests/test_recipe_library.py`, `tests/test_recipe_briefs.py`, `tests/test_legal.py`; `web/tests/recipes-api.test.ts`, `team-header.test.ts` | updated and extended |
| `docs/openapi.json`, `web/lib/api/schema.d.ts` | regenerated from this branch |

## The cutover rehearsal, with the row counts

Scratch Postgres 17 in Docker (removed afterwards), real migrations, `PYTHONPATH=. python scripts/rehearse_workspaces_cutover.py`. Seed fixed, so the counts repeat. Evidence: `docs/fe-captures/workspaces-cutover/cutover-rehearsal.txt` (32 checks, all `ok`).

| Table | Before (`0061`) | After (head) |
|---|---:|---:|
| `users` | 401 | 401 |
| `teams` | 314 | 441 |
| `team_members` | 443 | 570 |
| `meetings` | 4800 | 4800 |
| `notifications` | 2000 | 2000 |
| `templates` | 804 | 938 |
| `vocabulary_terms` | 1200 | 1328 |
| `calendar_connections` | 280 | 280 |
| `calendar_events` | 280 | 280 |
| `scheduled_joins` | 280 | 280 |
| `tasks` | 0 | 0 |
| `recipes` | 117 | 117 |
| `documents` | 123 | 123 |
| `document_shares` | 78 | 78 |
| `team_briefs` | 40 | 40 |

What it proves: every meeting row kept and the checksum over `(id, owner_user_id, shared_team_id, owner_team_id)` is identical before, after and after the round trip (`622624a997245407`), so nobody can read anything they could not read before; every user who can still sign in owns exactly one personal workspace (274 personal teams before, 401 after: the migration made 127); no meeting, notification, template, term, connection or join is left without a workspace; every calendar connection sits in the default workspace of its person (computed again in Python from the memberships, independently of the SQL); every join is in its connection's workspace; `templates` +134 and `vocabulary_terms` +128 are the copies made into the workspace a person's calendar moved to; downgrade to `0061` loses no row and the upgrade after it is clean. Time: about one second for 4,800 meetings.

**Not rehearsed:** the real production data (a copy of it was not available to this session), the real server's migration run through the deploy script, and locks under live traffic (the migration backfills with plain `UPDATE`s, which lock the rows they change; the time above is on an idle database).

## Verification

Run locally, bounded, on a scratch Postgres: `test_workspaces_migration`, `test_workspaces_settings_migration`, `test_workspaces_calendar_migration`, `test_recipes_migration`, `test_migrations`, `test_workspace_leaks`, `test_api_recipes`, `test_api_recipe_picker`, `test_recipe_picker`, `test_recipe_visibility`, `test_recipe_jobs`, `test_recipe_library`, `test_recipe_briefs`, `test_api_recipe_board`, `test_recipe_integration`, `test_recipe_cross_meeting`, `test_recipe_exports`, `test_workspace_gate`, `test_workspace_review_fixes`, `test_legal`; web: `tsc`, `eslint`, `legal`, `team-header`, `recipes-api`, and the other recipes and documents component tests. CI runs everything else. The permission review, its findings and their fixes are in the next section.

## Permission review

An Opus-level reviewer read the whole diff against `main` (`app/`, `alembic/`) with the contract in hand, tried to break each path with two accounts, and ran two ad-hoc tests to confirm findings. It checked: the required `X-Team-Id` context, members, ex-members and non-members, per-meeting keep-private, shared drafts, the Ask and Make gate through the owner's plan, calendar arbitration, delete and leave, plan limits and classes, and the migrations.

| # | Severity | Finding | Fix and proof |
|---|---|---|---|
| 1 | High (confirmed) | "Keep private" on a meeting filed in a team workspace left its AI tasks assigned to teammates (a task is visible to its assignee on the team even where the meeting is not) and its proposed dates with the team. A regression: on `main` an unshare re-filed them to the recorder's personal team. | `teams.keep_private_tasks`, called by `meeting_teams.unshare` after the re-homing: tasks assigned to anyone but the recorder are unassigned, unconfirmed date proposals go to the recorder's personal team. `tests/test_workspace_review_fixes.py::test_keeping_a_meeting_private...` (fails without the fix). |
| 2 | Medium (confirmed) | A draft made in workspace A from a meeting of workspace B was filed in A and could be shared with A's team, so A's members read text drafted from B's meeting (sources hidden, words not). The meeting picker's series scope had the same gap. | `request_draft` files a draft in the workspace its meetings live in (header's when any chosen meeting is there, else the one they all live in, else 404) and leaves other workspaces' meetings out and counted; the picker narrows every scope to the active workspace; Regenerate in the web sends the draft's own team. `test_a_draft_is_filed_where_its_meetings_live...`; the recipes tests that made drafts from personal meetings under a team header now put the meeting in the team's workspace. |
| 3 | Medium (reasoned, then tested) | Replacing a dead calendar connection with a different account revoked the old account's grant at Google even when another workspace still used it. | `ReleasedConnection.keep_grant` set from `grant_still_used` in `_drop_old_account`; `_release_at_google` then runs only the hooks. `test_replacing_a_dead_connection_does_not_revoke...` (fails without the fix) and its opposite case. |
| 4 | Low | Deleting a workspace skipped the owner's calendar clean-up (no revoke, no hooks, Meet subscription stayed). | `team_delete` calls `calendar_connect.detach_workspace` before dissolving; the route calls `settle_released` after the commit. Two tests (grant revoked; grant kept while another workspace uses it). |
| 5 | Low | A leave could commit while the OAuth code was being exchanged, and `_store` then filed a connection in a workspace the person had left. | `_store` (Google and Microsoft) takes the user's row first, then a SHARED lock on the team row (waits for a leave in flight), and re-checks membership. An exclusive team lock taken first deadlocked with the foreign key of a competing insert in `test_the_one_calendar_check_runs_under_the_users_row_lock` (found by CI, fixed). Not covered by a test: it needs a real race; the pre-exchange check has one. Bounded impact: recordings of a non-member go to their personal workspace (`placement.workspace_for`). |
| 6 | Needs Adebayo | A saved `auto_share_team_id` publishes a meeting recorded in the PERSONAL workspace to that team and moves its container (`tests/test_workspace_scope.py` asserts it on purpose). D4 says the personal workspace stays private. | Not changed: it is the person's own earlier choice ("auto-share my meetings with team X"). Decide whether that choice should now be limited to team workspaces. |
| 7 | Needs Adebayo | At cutover a plain member's calendar connection moves to their default workspace, a meeting their calendar later joins is born shared when that team's share setting is on (D4 and spec 12.1/12.5), and the migration copies a member's private templates and vocabulary into that team where every member can read them. | Not changed: it follows the spec. Worth telling pilot users before the release. |
| 8 | Low | A template of a dissolved team can land in an ex-member's personal list; an Ask task answer can cite a readable meeting of another workspace; a notification stayed in its birth workspace after its meeting moved. | The notification case is **fixed** (see "Notifications follow the meeting" above: share, move, move back, give-back, leave, removal and workspace delete re-file it). **Still open, cosmetic:** the template and the Ask citation; and a note about a meeting its holder can no longer READ, while still a member of the meeting's workspace (a teammate after "keep private"), stays in that workspace's bell. |

Found sound by the same review: `workspace_context.require` (404 for a non-member; the header only narrows); lists, search, Ask, decisions and review through `in_workspace`; notifications per workspace; templates and vocabulary writes manager-only; the feature gate (membership checked again, never covers the caller's own workspace, translate and rewrite excluded, caps per caller); leave and removal; delete-team checks; plan limits and ownership transfer; OAuth state carries the workspace; arbitration under the per-person lock; migration `0062` never touches `shared_team_id` or `owner_team_id`.

## The leaver and calendar auto-joins (confirmed)

Leaving or removal deletes the person's calendar connection in that workspace and every scheduled join filed there, in every state (`calendar_connect.detach_workspace`, called by `teams.leave`); a meeting already recorded or running stays. `tests/test_workspace_review_fixes.py::test_a_leaver_loses_the_calendar_connection_and_every_join_in_the_workspace` proves it and that the person's other workspaces' calendars are untouched; `tests/test_calendar_workspaces.py` has the provider-grant side.

## Operate / roll back

Server steps before merge are in the checklist entry and the PR description. Roll back: `alembic downgrade 0061` drops the workspaces columns and tables (copies of templates and vocabulary made at cutover are removed or taken out of use; the oldest calendar connection per provider is kept), and the code goes back with the previous image. After the downgrade, a person's meetings stay readable exactly as before, because `shared_team_id` and `owner_team_id` were never touched.

## Follow-ups

- Personal recipes appear in every workspace: confirm that is wanted, or give recipes a workspace as templates have.
- The Settings calendar card does not say which workspace it belongs to (needs a design line).
- The Make dialog's team switch now only picks the brief and team recipes of a workspace the meeting is in: if it names another workspace the draft is still made in the meeting's own (consider removing the switch when the meeting page is the only entry).
- Review findings 6, 7 and 8 above are decisions or cosmetic.
- A real Google and Microsoft calendar in two workspaces, a real production copy, and a real model-written draft for a member of a paid workspace have not been exercised.
