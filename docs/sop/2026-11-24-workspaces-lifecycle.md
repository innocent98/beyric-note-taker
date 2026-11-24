# SOP: Workspaces lifecycle (slice W4: plan limit, delete and leave, classes, the gate rule)

**What shipped (Tasks 10, 11 and 12).** (T10) How many team workspaces a person may OWN now comes from their live plan (`Plan.max_owned_teams`: Free 0, Starter 3, Pro 10, Scale unlimited), not a constant. (T11) An owner can delete a workspace once nobody else is in it; a class is a team with `kind = class` (a school's words, sharing on by default); the personal space cannot be deleted or left. (T12) `ask` and `make` pass for a free member on a workspace whose OWNER has a plan. Branch `feature/workspaces-lifecycle`, base `feature/workspaces`. **No migration** (W1's `0070` already holds `plans.max_owned_teams` and `teams.kind`).

Spec: `docs/superpowers/specs/2026-11-24-workspaces-design.md` sections 5, 6, 12.3, 12.6. Plan: `docs/superpowers/plans/2026-11-24-workspaces.md` Tasks 10 to 12. FE guide: `docs/fe-integration-guide-workspaces-lifecycle.md` (captures in `docs/fe-captures/workspaces/`).

## Why

A constant of three owned teams ignored billing, a team could not be deleted at all, a lecturer had no class, and a free student on a lecturer's paid class could read notes but not Ask or Make from them (the point of joining).

## How, and what was decided

| Decision | Choice |
|---|---|
| Which plan a person is on | `entitlements.live_plan`: the plan of their subscription while it is `active` or in its 3 grace days (`past_due`), judged by the clock (`states.effective_status`), so a renewal pass that has not run yet is still right. No subscription, a lapsed or ended one, a trial, a pilot grant, a pack or a gift = Free. |
| Admin | Never limited (`max_owned_teams` None) and always passes the gates, as before. |
| Lapse or downgrade | Nothing is deleted or locked. `GET /api/teams` shows `owned_teams` above `max_owned_teams`; only creating another is refused (409 `team_limit`). |
| The refusal's words | One sentence from `teams._limit_message`: Free "Owning a team needs a paid plan. Upgrade to the 20 hours a month plan to own up to 3 teams."; at the limit "Your plan owns up to 3 teams and you have 3. Upgrade to the Pro plan to own up to 10 teams." The plan named is the first ACTIVE plan (by `sort_order`) with a bigger allowance, so a price change needs no code. `GET /api/teams` carries the same sentence as `limit_message` (null while there is room), so the screen says it before anyone presses the button. |
| Ownership moves | `transfer_owner` refuses (409 `team_limit`, "That person can't take another team: ...") when the new owner has no room. The account-purge heir (`remove_users`, which must never be blocked) prefers the first member with room, else the first member anyway: a team is never destroyed over billing. |
| Delete a workspace | `DELETE /api/teams/{id}` (`app/team_delete.py`): 404 for a non-member, 409 `personal_team`, 403 `team_forbidden` (not the owner; an admin cannot), 409 `team_has_members`. Its tasks, projects, series, integrations, invitations, calendar rows and preferences go by the foreign keys. Its meetings go back to their recorders (`deletion.give_back_meeting`, the rule account deletion uses): private, in the recorder's personal workspace, tasks re-filed there; a meeting whose recorder's account is gone is deleted with its recording. Those deletions run one at a time, each re-checked under the team lock, so someone who joins mid-way stops the rest. A template a kept meeting was written with moves to that meeting's workspace (`teams.dissolve_team`, shared with the purge path). No plan is needed to delete. |
| Leaving, removal | Unchanged from W1 and the privacy page: access ends at once; what the person recorded and kept goes to their personal workspace, private; a meeting MOVED to the team stays with it. The owner cannot leave (message now says "delete it once you are its only member"); the personal space cannot be left (409 `personal_team`, checked first). The calendar connection and scheduled joins of a leaver are W3's. |
| Classes | `kind` on `POST /api/teams` (`team` default, `class`); a class starts with `share_new_meetings` on; it counts toward the limit; `kind` is on `TeamSummary` and `TeamOut`. One web helper, `web/lib/workspace-words.ts` (`workspaceWords`): Class, Students, Lecturer. |
| The gate rule | `entitlements.gate(..., workspace=)`: `ask` and `make` (`WORKSPACE_FEATURES`) pass when the caller's own state passes OR the caller is a member of `workspace` and `owner_has_live_plan(team)`. `translate` and `rewrite` ignore the workspace. Daily caps are per caller (`ask.daily_count` is by user id: proved by a test). `feature_gates.require(..., workspace=)` takes a `Team`, the active workspace or a team id. `FEATURE_GATES_MODE` off, shadow, enforce all behave (`tests/test_workspace_gate.py`). |
| Who counts as an owner with a plan | `active` (hours spent or not), the 3 `past_due` grace days, or an admin. **A trial or a pilot grant does not cover members** (a trial is a free taste, not a plan), nor do packs or gifts. |
| `make` and the recipes release | `MAKE` is not on this branch's base. `entitlements.MAKE`, `FEATURES` and `feature_gates._WORDS[MAKE]` were added with the SAME lines the recipes branch has, so the two merge cleanly. **When recipes lands, `recipes/service.py` `request_draft` must pass `workspace=` the meeting's workspace to `feature_gates.require(session, user, entitlements.MAKE, ...)`** (one line; not done here because the file is not on this base). |
| Rejected | Refusing the account-purge heir hand-over (a purge must never block); deleting a workspace's former-member meetings while a recorder exists (Review focus 5); letting a trial owner cover members (free trials would multiply). |

## What's involved

| Path | Change |
|---|---|
| `app/entitlements.py` | `live_plan`, `max_owned_teams`, `owner_has_live_plan`, `WORKSPACE_FEATURES`, `MAKE`; `gate` and `can` take `workspace` |
| `app/feature_gates.py` | `require(..., workspace=)`, the words for `make` |
| `app/ask.py` | passes the active workspace to the gate |
| `app/teams.py` | `owned_team_count`, `limit_message`, `has_room_for_another_team`, `create_owned_team(kind)`, `transfer_owner` check, `_heir`, `dissolve_team`; `MAX_OWNED_TEAMS` removed |
| `app/team_delete.py` (new), `app/deletion.py` (`give_back_meeting`), `app/team_admin.py` (leave order and message) | delete and leave |
| `app/api_teams.py`, `app/api_team.py`, `app/api_schemas.py` | `DELETE /api/teams/{id}`; `TeamList` gains `owned_teams`, `max_owned_teams`, `plan_name`, `limit_message` and `can_create_more` may be null; `kind` on `TeamSummary`, `TeamOut`, `TeamCreate` |
| `web/lib/workspace-words.ts`, `web/components/app/teams-view.tsx`, `team-view.tsx`, `team-switcher.tsx`, `web/lib/api/client.ts` | the screens below |
| `scripts/capture_workspaces_lifecycle.py`, `docs/fe-captures/workspaces/` | the live captures |
| `tests/test_workspace_limits.py`, `tests/test_workspace_lifecycle.py`, `tests/test_workspace_gate.py`, `tests/workspace_plans.py`; `web/tests/workspace-lifecycle.test.tsx`, `workspace-words.test.ts`, `teams-view.test.tsx` | the tests |
| `docs/openapi.json`, `web/lib/api/schema.d.ts` | regenerated |

**Lead-designed UI (not in the designer's files; for the designer to review), all built from existing components and tokens:** (1) on the Teams screen's "Make a team" card: the plan allowance line ("You own 1 of the 3 teams your plan allows. You can make 2 more." / "Your plan has no limit."), the amber upgrade message with a "See plans" link at the limit (Alert, AppLink), and the "This is a class" tick (Checkbox) that turns the button into "Make class"; (2) a "Class" badge and "Lecturer"/"Student" words in the Teams list; (3) the "Delete this team" card at the foot of a team's page for its owner (off with its reason while others are in the team; an inline question first, built from the removal question); (4) a class's words (Class, Students, Lecturer) on the Team screen; (5) "(class)" in the switcher's option text. The switcher's unread dots are W1-web's (PR B, not on this base): not touched.

## Verification

Scratch Postgres 17 (Docker, removed afterwards), only named files, never the full suite or a shard. Python: `tests/test_workspace_limits.py` (28: each plan, Free, trial, grace, clock-lapsed, admin, a lapse and a downgrade keeping every team, hand-overs, the limit sentence), `tests/test_workspace_lifecycle.py` (23: delete rules, give-back and delete, template kept, mid-delete join, leave and removal, personal space, classes), `tests/test_workspace_gate.py` (32: the rule per owner state, personal workspace, other workspaces, non-members, caps per caller, three modes, Ask end to end), plus the existing `tests/test_team.py`, `test_accounts.py` (route tables), `test_multi_team_api.py`, `test_multi_team_guard.py`, `test_workspaces_migration.py`, `test_workspace_leaks.py` and the named parts of `test_feature_gates.py`. Web: the new and touched vitest files, `tsc`, `eslint`. **Live:** `scripts/capture_workspaces_lifecycle.py` ran the real migrations and the real app and recorded 36 exchanges (found and fixed that the owner account is an admin, so a capture needs an ordinary person). **Real browser** (the built-in browser pane against a real local FastAPI and Postgres) at 1280 and 390 px: the Teams screen (allowance, limit alert, class option, list), a class's page, the delete question and a real delete, no horizontal overflow at 390.

**Not verified:** CI (the full suite and shards run there); the `make` gate (recipes not on this base); a plan lapsing by the clock in production (the capture sets the status as the renewal pass would); deleting a workspace that holds a meeting whose recorder is gone against a real provider (tested with the fake one).

## Operate / roll back

No migration, no config. Roll back by reverting the PR: `Plan.max_owned_teams` stays seeded (harmless); the old constant (3 for everyone) returns. **A person with no plan who owned teams before keeps them; a pilot or trial person can no longer MAKE a new one** (Free 0): a deliberate effect of the spec, flagged for Adebayo.

## Follow-ups

- `scripts/capture_teams_api.py` (an older capture) now meets the limit (Pat is on Free); give it a subscription before re-running it. Several older capture scripts also still mention the removed multi-team flag (W1's note).
- When the recipes release merges: pass `workspace=` in `recipes/service.py` (see above).
- A member refused because the OWNER's plan lapsed reads the ordinary "needs an active plan or trial" sentence; a sentence about the owner's plan would be kinder (a copy decision).
- The switcher's unread dots (W1 web, PR B) and this slice's "(class)" tag touch the same option text; whichever merges second keeps both.
- Privacy and terms wording for classes, former members and deletion is W5's lawyer list.
