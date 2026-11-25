# SOP: Make the auto-share rule visible ("Shared with <team> by your auto-share rule")

**What shipped.** The saved "Share my new meetings with <team>" rule keeps working exactly as before, but it is now said plainly in two places: the Settings card says it also applies to meetings recorded in the **Personal** workspace, and a meeting the rule shared says so on its page with the existing Stop sharing button and a link to Settings. One additive API field (`shared_by_auto_share`), one additive column (migration `0066`). PR: `feature/auto-share-visible-label` into `main` (number in the checklist entry). Not merged: waits for Adebayo's typed word.

Spec context: `docs/sop/2026-11-25-workspaces-release.md` and `docs/superpowers/specs/2026-11-24-workspaces-design.md` (the rule is the person's explicit choice and a test asserts it on purpose: `tests/test_workspace_scope.py`).

## Why

The rule publishes a meeting recorded in a person's Personal workspace to the chosen team (`teams.apply_share_default`: it files the meeting in that team's workspace). People read "Personal" as private, so the result can surprise them. Adebayo decided: keep the rule, make it obvious.

## How, and what was decided

| Decision | Choice |
|---|---|
| Who the rule shares with | **Unchanged.** No change to `apply_share_default`'s choice of team, the unshare behaviour, e-mail or notification wording. |
| Knowing "why is it shared" | The server never recorded it. A meeting in a team workspace shared with that team looks the same whether the rule, the Share button or the workspace default did it, so deriving it from today's data would label hand-shared meetings wrongly. A stored flag was chosen: `meetings.shared_by_auto_rule` (bool, default false, not null). Rejected: guessing from the saved setting (wrong after the person changes it or shares by hand); a free-text "reason" column (more than needed). |
| Who sets and clears the flag | `teams.apply_share_default` sets it, only when the rule itself changed the share (not when the workspace's default had already shared it to the same team). A SQLAlchemy `set` listener on `Meeting.shared_team_id` (`app/models.py`, `_forget_auto_rule`) resets it on **every** other write of the share (share, unshare, move, move back, scripts, tests), so no writer can forget. |
| Backfill | None. Existing rows are false and show no label: the label appears only when it is certain. |
| API | `MeetingDetail.shared_by_auto_share: bool = False`, true only for the recorder, only while shared (`app/api.py` `_detail`). Teammates get false; the list rows do not carry it. Additive, so no client breaks. |
| Settings text | A new paragraph in `web/components/app/settings-auto-share.tsx`: "This includes meetings you record in your Personal workspace. Personal is only yours until you choose a team here: after that, those meetings are shared with the team too and move into its workspace." The existing sentences are untouched. |
| Meeting page | A line in the team card (`web/components/app/meeting-team-card.tsx`) when shared by the rule: "Shared with <team> by your auto-share rule. Stop sharing makes this meeting private; to change the rule for new meetings, go to Settings." The Stop sharing button and the Settings page already existed: no new flow. |

## What's involved

| Area | Files |
|---|---|
| Model and migration | `app/models.py` (`Meeting.shared_by_auto_rule`, listener), `alembic/versions/0066_meeting_shared_by_auto_rule.py` (down revision `0065`; downgrade drops the column) |
| Rule | `app/teams.py` `apply_share_default` |
| API | `app/api_schemas.py` (`MeetingDetail`), `app/api.py` (`_detail`), `docs/openapi.json`, `web/lib/api/schema.d.ts` (both regenerated: `PYTHONPATH=. python scripts/export_openapi.py`, `npm run gen:api`) |
| Web | `settings-auto-share.tsx`, `meeting-team-card.tsx`, `web/tests/helpers.ts` (older captures default the field to false) |
| Tests | `tests/test_auto_share_label.py` (9), `tests/test_auto_share_label_migration.py` (2), `web/tests/meeting-auto-share-label.test.tsx` (6), `web/tests/settings-auto-share.test.tsx` (+1) |
| Captures and guide | `scripts/capture_auto_share_label.py`, `docs/fe-captures/workspaces/auto-share-*.json` (17 live exchanges), `docs/fe-integration-guide-workspaces.md` section 13 |

## Verification

Run locally: `ruff check .`, `ruff format --check .` (the pinned 0.17.3), `export_openapi.py --check`, `npm run typecheck`, `npm run lint` (4 pre-existing warnings, none in these files), the touched test files above plus `tests/test_workspace_scope.py` (the test that asserts the rule on purpose) and `web/tests/meeting-team-card.test.tsx`; the migration on a scratch Postgres (upgrade to `0066`, downgrade to `0066`, upgrade to head). Not run locally, left to CI: the full suites. The API shapes in the guide and in the web test come from the live capture against the running app (stand-in bot, no model: the notes step is stood in for by the one call the pipeline makes).

## Operate / roll back

Deploy applies `0066` (a nullable-free column with a server default: instant). Roll back by reverting the PR and `alembic downgrade 0065`; nothing else depends on the column and no access rule reads it (`app/visibility.py` never does).

## Follow-ups (suggestions, not changed here)

- Mail and bell wording: the "notes are ready" mail and notification for a meeting the rule shared could say "shared with <team> by your auto-share rule" so the first surprise is not on the team's side. Not changed (out of scope).
- The Share dialog or a toast on "Share new meetings with <team>" could remind that Personal meetings are included; the Settings sentence is the only place now.
- The option "No team (keep private)" could read "No team (new meetings stay private)"; the current wording is accurate but short.
- Meetings shared by the rule before this ships carry no label (the server did not record why).
