# Workspaces Implementation Plan

> **For agentic workers:** the lead runs this as sessions, one per slice, each following its tasks with TDD. Steps use checkbox syntax. Slice PRs target the integration branch `feature/workspaces`; nothing deploys until W5 is done and the lead opens one PR to `main`.

**Goal:** A team becomes the container of everything: every meeting, Ask answer, task, decision, template, vocabulary list, notification, calendar connection and per-person setting belongs to one workspace, switching workspace switches all of it, plan limits cap owned workspaces, and class workspaces serve schools.

**Architecture:** a required workspace context on every route (the existing `X-Team-Id` header, default the personal workspace) feeding one visibility helper; a new `meetings.workspace_team_id` container column beside the existing share columns; per-workspace templates, vocabulary, notifications and per-member-per-workspace preferences; calendar connections keyed by `(user, workspace, provider)` with an arbitration rule over the existing one-live-join-per-meeting-code guarantee; `Plan.max_owned_teams`; one cutover migration with backfill.

**Tech Stack:** FastAPI + SQLAlchemy + Alembic, Next.js 16 + vitest; no new dependency.

**Spec:** `docs/superpowers/specs/2026-11-24-workspaces-design.md` (approved 2026-11-24; read all of it, especially sections 3, 4, 8 and 12). Facts below come from a code audit of `main` `48e026b8`; verify a signature before relying on it.

## Global Constraints

- One cutover: all slices merge into `feature/workspaces`, then one PR to `main`; nothing deploys before W5 is done. The integration branch takes `origin/main` every day. A concurrent recipes release (`feature/recipes`) will also reach `main`; whichever merges second resolves conflicts in `app/api.py`, the checklist and the generated openapi.
- Branches `feature/workspaces` and `feature/workspaces-<slice>` only; never `claude/`. No AI attribution anywhere. Locally only lint, format check, type-check and the tests of files you touched; never the full suite, a shard or e2e (CI does).
- Migrations: placeholder ids `0070`, `0071`, ... chained from the head at branch time; renumbered into one chain when the integration branch rebases onto `main` (heads on `main` today: `0056`; PR #262 adds `0057`, PR #263 adds `0058`, the recipes branch uses `0060+`).
- **A leak is the worst failure.** Every list, count, search, Ask answer and notification query narrows by the active workspace through ONE helper (`app/visibility.py`), and a guard test enumerates every API route and fails if one that returns workspace data ignores the header (model it on `tests/test_accounts.py:test_every_api_route_with_an_id_returns_404_for_another_users_row` and `tests/test_multi_team_leaks.py`). Detail routes by id authorise by readability only, as today, and return the meeting's workspace.
- Existing meetings keep their visibility exactly (spec 12.1): backfill never changes `shared_team_id`.
- Billing stays per person; `Plan.max_owned_teams`: Free 0, Starter 3, Pro 10, Scale null (unlimited); a trial counts as Free; joining never counts; a lapsed plan never deletes or locks a workspace.
- Feature gate (`app/feature_gates.py`): `ask` and `make` pass when the caller OR the workspace's owner has a live plan; daily caps stay per caller; `translate` and `rewrite` stay caller-only.
- Errors use `{"error": {"code","message"}}` via `services.Refused`; another workspace's data is 404 never 403; every writer to workspace settings is owner or admin only unless it is a per-person preference.
- Every new user-visible element is lead-designed (the designer's files draw none): list each in the slice's SOP. Each slice writes `docs/sop/2026-11-24-workspaces-<slice>.md`, ticks its checklist lines, and new or changed endpoints get FE-guide text from REAL captured responses.

## Review Focus

1. Switch to workspace B and find workspace A's data through any list, search, Ask, count, badge, notification, export, or mail link (W1 guard test; W5 manual pass).
2. Cutover on production-shaped data: no meeting disappears, none becomes visible to people who could not see it, none lands in the wrong workspace (W1 migration rehearsal).
3. The same calendar in two workspaces records a meeting exactly once and the right workspace gets it (W3).
4. A member who leaves keeps no calendar join, no access, and their recorded meetings stay (W3, W4).
5. A workspace deleted by its owner never deletes a former member's meeting that has a recorder to return it to (W4).
6. A free student passes the gate only for Ask and recipes on a workspace whose owner has a live plan, never for anything else, and never on a workspace whose owner's plan lapsed (W4).

---

## Slice W1: foundation (first; everything else waits for T1 to T3 to merge into the integration branch)

### Task 1: Columns, models and backfill migration
**Files:** Create `alembic/versions/0070_workspaces_core.py`, `tests/test_workspaces_migration.py`; Modify `app/models.py`.
**Interfaces:** Produces `Meeting.workspace_team_id` (BigInteger FK teams, NOT NULL after backfill), `Notification.team_id`, `Template.team_id`, `VocabularyTerm.team_id`, `CalendarConnection.team_id`, `ScheduledJoin.team_id` (all nullable FK to start, NOT NULL where the backfill covers every row), `Team.kind` in ("team","class") default "team", `Plan.max_owned_teams` (int, null = unlimited), and `WorkspacePrefs(team_id, user_id, bot_name, auto_join_rule, join_unanswered, skip_one_on_ones, calendar_template_id)` unique `(team_id, user_id)`.
- [ ] Write the migration test (model it on `tests/test_skip_one_on_ones_migration.py`): upgrade from the previous head with seeded rows for each backfill rule below; assert every row; downgrade then upgrade to head.
- [ ] Backfill rules (spec section 8 and 12): `workspace_team_id` = `owner_team_id`, else `shared_team_id`, else the recorder's personal team; `shared_team_id` untouched; calendar connection `team_id` = the person's current default workspace (the rule in `app/teams.py:78-96` `default_membership`: the only membership, else the only ordinary team, else personal); templates and vocabulary: existing rows keep `team_id` = the owner's personal workspace AND are copied once into the connection's workspace when that is not personal; notifications get their meeting's workspace else the person's personal one; `WorkspacePrefs` rows are created from `user_settings` for each (user, workspace the connection lives in) and for the personal workspace; `Plan.max_owned_teams` seeded Free/none 0, Starter 3, Pro 10, Scale null.
- [ ] Implement the migration and models; run the test: PASS. Commit.

### Task 2: One workspace context
**Files:** Create `app/workspace.py`, `tests/test_workspace_context.py`; Modify `app/team_admin.py` (`active_team`, `requested_team`, `my_membership`), `app/config.py` (remove `multi_team_enabled` and its branches; `app/teams.py:349-359` personal-team dissolving on join goes with it), `app/visibility.py`.
**Interfaces:** Produces `workspace.require(session, user, header_value: str | None) -> Team` (404 `not_found` for a team the person is not in; default the personal workspace; never `None`); `visibility.in_workspace(user, team) -> ColumnElement` (readable AND `workspace_team_id == team.id`); `Depends(workspace.current)` for routes.
- [ ] Tests: header names a team the person is in; a team they are not in is 404; no header gives the personal workspace; the personal workspace lists only meetings with `workspace_team_id` = personal; a member sees a teammate's shared meeting in the team workspace and not in personal; removing the flag keeps `GET /api/session` working.
- [ ] Implement; remove the flag from `.env.example`, `deploy/README.md`, `scripts/clean_env.py` (the clean-env test registers settings); run the touched tests: PASS. Commit.

### Task 3: Scope every list, search, Ask, count and badge
**Files:** Modify `app/api.py` (meetings list), `app/services.py:list_meetings`, `app/ask.py` (`candidates`, `scope`, task answers), `app/workspace.py` (decisions, review items, counts), `app/api_workspace.py`, `app/api_tasks.py`, `app/notifications.py`, `app/api_series.py`, `app/calendar_recurring.py`, `app/meeting_search.py` if present on `main`; Create `tests/test_workspace_leaks.py`.
**Interfaces:** Consumes `workspace.current`, `visibility.in_workspace`. Produces: every route in the table of spec section 3 narrows by the active workspace; `GET /api/meetings/{id}` returns `workspace_team_id`; `GET /api/session` returns the active workspace and, for each other workspace, `unread_notifications`.
- [ ] Guard test `test_every_workspace_data_route_honours_the_header`: enumerate `api_routes()`; for each GET that returns meetings, tasks, decisions, review items, notifications, series, search or counts, call it as a person in workspaces A and B with the header of A and assert no B data appears; the test fails when a new route is not classified (model it on the two existing route-enumerating tests).
- [ ] Tests: Ask answers only from the active workspace even though the person can read both; the bell counts only the active workspace and the session lists unread per other workspace; a meeting opened by id from another workspace answers 200 with its `workspace_team_id` (the web will switch).
- [ ] Implement route by route, reusing `visibility.in_workspace`; run the touched tests: PASS. Commit.

### Task 4: Web: required header everywhere, auto-switch, switcher
**Files:** Modify `web/lib/api/client.ts` (`isTeamScoped` becomes "everything except the personal-only routes": session, login, logout, settings/profile, billing, calendar-connect callbacks), `web/lib/active-team.ts`, `web/components/app/team-switcher.tsx`, `web/components/app/meeting-view.tsx` (detail from another workspace), tests `web/tests/workspace-switch.test.tsx`; regenerate `docs/openapi.json` and `web/lib/api/schema.d.ts`.
- [ ] Tests (vitest, fixtures from captured responses): the header is sent on Ask, templates, vocabulary, notifications, calendar and settings routes; opening a meeting whose `workspace_team_id` differs switches the stored workspace and shows the toast "Switched to <name>"; the switcher shows an unread dot for another workspace with unread items; switching reloads data.
- [ ] Implement with existing components and tokens; look at it in a real browser at 390 and 1280 px; run: PASS. Commit.

---

## Slice W2: templates, vocabulary, notifications, settings (after W1 T1 to T3)

### Task 5: Templates and vocabulary per workspace
**Files:** Modify `app/services.py` (template and vocabulary queries), `app/api.py` (templates, vocabulary routes), `app/accounts.py` (seeded defaults go to each new workspace), `app/teams.py` (`create_owned_team` seeds defaults), `web/components/app/templates-editor.tsx`, `web/components/app/vocabulary-manager.tsx`; Create `tests/test_workspace_templates.py`.
**Interfaces:** `GET/POST/PUT/DELETE /api/templates` and `/api/vocabulary` act on the active workspace; writes need owner or admin (members read and use); `POST /api/templates/copy-from-personal` and `POST /api/vocabulary/copy-from-personal` copy the person's personal-workspace rows into the active workspace without overwriting names.
- [ ] Tests: member can list but not write; a new workspace starts with the seeded defaults; copy-from-personal skips existing names; deleting a template used by a meeting keeps the row marked deleted (as today).
- [ ] Implement; run: PASS. Commit.

### Task 6: The pipeline reads the meeting's workspace
**Files:** Modify `app/pipeline.py` (`_terms`, template choice at :1390), `app/services.py:regenerate_notes`, `app/translation.py:686`, `app/placement.py` (`_resolve`, `calendar_template_id`), `app/send_now.py`, mails (`app/mail_templates.py`, `app/alerts.py`, `app/mail_summary.py`), `app/series_mail.py`; Create `tests/test_workspace_pipeline.py`.
**Interfaces:** Produces `workspace_prefs.for_meeting(session, meeting) -> Prefs` (vocabulary terms and default template from `meeting.workspace_team_id`; mail behaviour: the person's own preference AND the workspace setting, spec 12.7).
- [ ] Tests: a meeting recorded by a member into workspace W uses W's vocabulary and template, not the recorder's personal ones; it still works after the recorder left (a hollow row); a team admin rewriting a moved meeting chooses among the WORKSPACE's templates (fixes the recorder-only quirk); reminder and ready mails read the meeting's workspace.
- [ ] Implement; run: PASS. Commit.

### Task 7: Per-person-per-workspace preferences and the settings split
**Files:** Create `app/workspace_prefs.py`; Modify `app/api_settings.py`, `app/calendar_api.py`, `app/gcal_rules.py` (`JoinSettings.for_user` reads `WorkspacePrefs`), `web/components/app/settings-*.tsx`, `web/app/settings/page.tsx`.
**Interfaces:** `GET/PUT /api/workspace-prefs` (active workspace: `bot_name`, `auto_join_rule`, `join_unanswered`, `skip_one_on_ones`, `calendar_template_id`); personal settings (name, time zone, email preferences, language) stay on `/api/settings`; per-workspace team settings stay on `PATCH /api/team`.
- [ ] Tests: two workspaces hold different auto-join rules for the same person and `JoinSettings.for_user(prefs, workspace)` returns each; changing one does not touch the other; bot name falls back to the person's default; the Settings screen shows a "Personal" section and a "This workspace" section.
- [ ] Implement; run: PASS; browser check. Commit.

---

## Slice W3: calendar per workspace (after W1 T1 to T3; parallel with W2)

### Task 8: Connections keyed by workspace
**Files:** Modify `app/calendar_connect.py` (`already_connected`, `different_account`, connect, callback, disconnect), `app/models.py` (unique `(user_id, team_id, provider)`), `app/gcal_sync.py`, `app/graph_*.py`, `app/calendar_api.py`; tests `tests/test_calendar_workspaces.py`.
- [ ] Tests: a person connects Google in workspace A and the same account in B; a second Google in the same workspace is refused (`already_connected`); disconnecting A revokes the provider grant only when no other connection of theirs uses the same account (reference count); the callback returns to the workspace that started the connect (carry it in the OAuth state).
- [ ] Implement; run: PASS. Commit.

### Task 9: Arbitration and recurring per workspace
**Files:** Create `app/calendar_arbitration.py`; Modify `app/gcal_sync.py`, `app/join_runner.py`, `app/placement.py`, `app/calendar_recurring.py`, `app/series.py` (`SeriesIgnored` already per team scope); tests `tests/test_calendar_arbitration.py`.
**Interfaces:** `choose_workspace(candidates: list[JoinCandidate]) -> JoinCandidate | None` implementing spec 12.4: (a) a workspace where the person switched the meeting on wins; (b) among workspaces whose rules would join it the first-connected wins; (c) none join if none would. `ScheduledJoin.team_id` is set from the winner; the loser's Upcoming row reports `recorded_in` (workspace name).
- [ ] Tests: same event in A and B, neither switched: first-connected records, the other shows `recorded_in`; A's rule skips it and B's joins: B records; switching it on in B moves the join with a visible note and cancels A's if not yet started; a running bot is never moved; the meeting is born in the winner's workspace; "remove recurring" in A leaves it in B; the one-live-join-per-code index still holds under a race (two syncs at once).
- [ ] Implement; web: Upcoming row shows "Recorded in <workspace>" with a switch; run: PASS. Commit.

---

## Slice W4: limits, workspace lifecycle, classes, gate (after W1; parallel with W2 and W3)

### Task 10: Plan limit
**Files:** Modify `app/teams.py` (`create_owned_team` reads `Plan.max_owned_teams` through the person's live plan, replacing `MAX_OWNED_TEAMS`), `app/entitlements.py` (a `max_owned_teams(session, user)` reader), `app/api_teams.py`, web create-team dialog; tests `tests/test_workspace_limits.py`.
- [ ] Tests: Free 0, Starter 3, Pro 10, Scale unlimited; trial counts as Free; being a member never counts; a lapsed plan keeps existing workspaces and blocks new ones with a plain 409 `team_limit` message naming the plan to upgrade; ownership transfer and heir hand-over are checked against the new owner's limit or refused with a clear error.
- [ ] Implement; run: PASS. Commit.

### Task 11: Delete workspace, leave behaviour, classes, switcher
**Files:** Modify `app/teams.py` (`delete_team`, `leave`, removal disconnects the member's calendar connection and cancels their scheduled joins in that workspace), `app/api_teams.py` (`DELETE /api/teams/{id}`), `app/accounts.py`, `web/components/app/team-view.tsx`, `team-switcher.tsx`; tests `tests/test_workspace_lifecycle.py`.
- [ ] Tests: owner deletes only when no other member remains; its tasks, projects, series and integrations go with it; meetings with a living recorder return to that recorder's personal workspace, meetings whose recorder is gone are deleted; a removed member's connection and joins in that workspace are gone and their recorded meetings stay as "Former member"; creating a team with `kind = "class"` changes wording only and sets the share default on; a personal workspace cannot be deleted or left.
- [ ] Implement; web: class label and wording ("Class", "Students", "Lecturer" via one `workspaceWords(kind)` helper), School recipe pack first when the recipes release is present (a prop, nothing breaks without it); run: PASS. Commit.

### Task 12: The feature-gate rule for members
**Files:** Modify `app/feature_gates.py` (`require(session, user, feature, *, workspace=None, ...)`), `app/ask.py` and the recipes entry points (pass the workspace of the meeting or the active workspace), `app/entitlements.py` (`owner_has_live_plan(session, team)`); tests `tests/test_workspace_gate.py` extending `tests/test_feature_gates.py`.
- [ ] Tests: free member + workspace owner on a live plan: `ask` and `make` pass, `translate` and `rewrite` still refuse; owner's plan lapsed: refuse; free member on their own personal workspace: refuse; daily caps still count per caller; `FEATURE_GATES_MODE` off, shadow and enforce all behave.
- [ ] Implement; run: PASS. Commit.

---

## Slice W5: integration

### Task 13: Rehearsal, review, docs
- [ ] Run the cutover migration on a copy of production-shaped data (a script that builds several users with personal and ordinary teams, shared and unshared meetings, calendar connections, templates, vocabulary, notifications); assert counts before and after, no visibility change, no lost rows; record results in the SOP.
- [ ] Privacy and terms wording check (workspaces, former members, class use) flagged for Adebayo's lawyer, not blocking; consent banner unchanged.
- [ ] Opus review of every permission path (W1 T2 and T3, W3 T9, W4 T11 and T12) and a delta review of fixes.
- [ ] FE-guide `docs/fe-integration-guide-workspaces.md` from real captures (`scripts/capture_workspaces.py`); SOPs and checklist reconciled; `docs/HANDOFF-master.md` block.
- [ ] Rebase the integration branch on `origin/main`, renumber migrations, CI green on the final head, open ONE PR `feature/workspaces` into `main`. Never merge: the lead merges on Adebayo's typed word. Server step for Adebayo: none beyond the normal deploy, but the migration is long; run a backup first (`deploy.sh` already takes one).

---

## Self-review notes

- Spec coverage: section 3 table maps to W1 T3, W2 T5 to T7, W3 T8 and T9; section 4 to W3; section 5 to W4 T10; section 6 to W4 T11 and T12; section 7 to W1 T2 to T4 and W4 T11; section 8 to W1 T1 and W5 T13; section 12 points 1 to 8 to W1 T1, T3 and T4, W4 T11, W3 T9, W1 T1, W4 T10 and T12, W2 T6, W1 T3.
- Order and parallelism: W1 T1 to T3 first (they define the columns and the context); then W2, W3 and W4 can run at once (three sessions); W1 T4 (web) alongside them; W5 last. This is the 8 to 12 working day estimate's shape.
