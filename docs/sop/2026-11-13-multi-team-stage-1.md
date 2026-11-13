# Multi-team, stage 1: schema and rules (dark)

**What shipped.** Stage 1 of the multi-team design
(`docs/superpowers/specs/2026-11-13-multi-team-design.md`, PR #178; stage plan in its section 7):
one person may now belong to several teams **in the database and in the rules**, with a permanent
personal team each. It ships **dark**: `MULTI_TEAM_ENABLED` defaults to `false`, no route, payload
or screen changed, and with the flag off and one membership per person the behaviour is today's.
PR: see the branch `feature/multi-team-stage1-schema-and-rules` (number added to the checklist when
opened). No FE integration guide in this stage (no payload changed); stage 2 writes
`docs/fe-integration-guide-teams.md` from live captures.

## Why

Today `team_members.user_id` is unique, so a consultant cannot be in two teams, and accepting an
invitation dissolves the personal team. Stage 1 lays the base for the later stages (explicit team
per route, team-owned meetings and Move, the switcher) without a flag day: the schema, the one
visibility rule and every single-team lookup had to stop assuming one membership first.

## How (and what was decided on the way)

| Piece | What it does | Why / alternative rejected |
|---|---|---|
| Migration `0043` | `teams.is_personal` + partial unique index (one personal team per owner); `UNIQUE(team_id, user_id)` then drop `UNIQUE(user_id)`; index `team_members(user_id)`; `meetings.owner_team_id` (nullable FK, `ON DELETE SET NULL`, partial index; unused until stage 2) | `owner_team_id` rather than a flag: a flag could not say which team. SET NULL = the meeting goes back to its recorder if the team is deleted (stage 2 may refine) |
| Backfill | A team with exactly one member (its owner) and no pending invitation, integration, project, sprint or milestone becomes that person's personal team. Any other team stays as it is; everybody still without a personal team (owners of busier solo teams, people in teams with others) gets a new one, as an owner-only team of one | The spec's rule. "Pending" = not accepted and not revoked, expired included (it still shows in the Team list and can be resent). Nobody's membership is removed and no content moves |
| Downgrade | First deletes the personal teams that are plainly empty (one member, no tasks, projects, sprints, milestones, series, integrations, invitations) of people who have another membership, i.e. the ones 0043 made; then **refuses** with `Cannot downgrade 0043: N account(s) still belong to more than one team...` if anyone still has two memberships. Nothing is changed on a refusal (one transaction) | Defined only while nobody has two memberships, as asked. The real roll-back is to revert the code and leave the schema |
| `app/visibility.py` | `can_read_clause` = own, or `shared_team_id IN (all my teams)` (live-owner guard as before), or `owner_team_id IN (all my teams)`. New helpers `via_team`, `in_team`, `in_team_clause`, `meeting_team_ids`, `is_shared` | The only module that reads `shared_team_id`/`owner_team_id` to decide access. Three places that compared `shared_team_id` outside it (`integrations.py` twice, `topics.py`, `series.py`, `mail_templates.py`, `api.py`/`ask.py` display flags) now ask the helpers |
| Default team | `teams.default_membership` / `team_of` / `ensure_team`: the person's only membership; else their only ordinary team; else their personal team. Never raises | The compatibility rule for team-implicit routes, in ONE helper. "Personal" is relative to the person (`is_personal_of`: flagged AND owned by them): a team flagged personal that someone else joined (old fixtures, a flag-off join of a team of one) is simply a team to those members |
| Flag off | `join` is today's code: dissolve the team of one (tasks, projects, milestones, series move), `409 already_on_team` for a team with others. One addition: a personal team that someone joins stops being personal (the flag is cleared; nothing else changes, not even the number of teams) | Today people invite into their team of one; without this the flag would lie and the member could never leave. Making the owner a new personal team at that moment was tried and rejected: it changes team counts for flag-off users (an existing test caught it) |
| Flag on | `join` adds a membership, moves nothing, makes the personal team if a flag-off join dissolved it, refuses a personal team (`409 personal_team`); `team_invites.create` refuses a personal team (`409 personal_team`) | Section 6.2 of the spec |
| Leave / remove | `leave(session, user, now, team_id=None)` takes the team, creates **no** team of one (returns the personal team, made if missing), and refuses to leave one's own personal team while nobody else is in it (`409 personal_team`; a hand-built team flagged personal with other members, as in old fixtures, can still be left by its owner as before). `team_admin.remove_member` passes the target's team | Section 6.8; the personal-owner case already answers `owner_cannot_leave` before it |
| Tasks and mail | `tasks.visible` uses `team_id IN (my teams)`; permissions are read from the membership **of the task's team** (`tasks.membership(session, user, team_id)`, `tasks.memberships`); the assigned-task mail and the share mail's "Open notes" button ask `visibility.via_team` | A scalar subquery over `team_members` raised `MultipleResultsFound` with two memberships |
| `shares._team_members_by_email` | members of **any** team of the sender | Only links a recipient to an account; what they may read is decided by the rule at send time |

## What's involved

- Migration: `alembic/versions/0043_multi_team_schema.py` (head was `0042`); models in `app/models.py`.
- Rules: `app/visibility.py`, `app/teams.py`, `app/team_admin.py`, `app/team_invites.py`, `app/tasks.py`, `app/api_tasks.py`, `app/shares.py`, `app/series.py`, `app/series_mail.py`, `app/mail_templates.py`, `app/integrations.py`, `app/topics.py`, `app/api.py`, `app/ask.py`, `app/workspace.py` (comment only: `ensure_team` already returns the default team).
- Config: `MULTI_TEAM_ENABLED` in `app/config.py`, `.env.example`, `deploy/README.md`.
- Tests: `tests/test_multi_team_leaks.py`, `tests/test_multi_team_rules.py`, `tests/test_migrations.py` (`test_0043_*`); `pyproject.toml` lets the two new test files reuse fixtures (`F811`, as the other files do).
- One existing test changed: `tests/test_team_visibility.py::test_a_user_cannot_be_in_two_teams` asserted the constraint this stage removes; it now asserts one membership per (team, person).

## Verification

All on a scratch Postgres 17 on this Mac (not the CI container), with the project's own `ruff`
(0.17.3): `ruff check .` and `ruff format --check .` clean.

- New tests, all passing: `tests/test_multi_team_leaks.py` (17), `tests/test_multi_team_rules.py`
  (29), `tests/test_multi_team_derived.py` (9: Ask, decision search, series page, topics, suggested
  agenda with two memberships), `tests/test_multi_team_flag_off.py` (4: the post-migration shape
  with the switch off, and two accepts at once), `tests/test_migrations.py -k 0043` (4: follows 0042; backfill on seven shapes; upgrade,
  downgrade, upgrade; the refusals) plus `test_migration_matches_models` and `test_single_head`.
- The leak tests were written before the code (they could not even import `ensure_personal_team`).
  A mutation check (the multi-team subqueries put back to the old single-team ones) makes 15 of the
  17 leak tests fail, so they do guard the rule.
- Existing tests of the touched modules with the flag off: `test_team_visibility`, `test_team`,
  `test_api_tasks`, `test_tasks`, `test_shares`, `test_series`, `test_series_mail`, `test_workspace`,
  `test_integrations`, `test_integrations_linear`, `test_topics`, `test_accounts`,
  `test_meeting_delete`, `test_suggested_tasks`, `test_suggested_exclusions`, `test_projects_api`,
  `test_sprints_api`: 635 tests. The first run had 3 failures: two were real regressions of mine
  (graduating a team of one created a team for its owner, which changed team counts; the personal
  guard on `leave` refused the owner of a hand-built team), both fixed in the code; the third is
  the one test that asserted the removed constraint (replaced, see above). The affected files were
  rerun after the fixes (220 passed).
- **Not run**: the full suite, a shard, e2e, coverage (CI does); the 20-odd other test files that
  import `teams` indirectly. **Not proven**: nothing was run against a real server, a real browser
  or real production rows, so the backfill has only seen the fixtures above.

## Operate / roll back

- Deploy: migration `0043` runs with the usual `alembic upgrade head`; the app behaves as before.
  Keep `MULTI_TEAM_ENABLED` unset or `false` until stage 3 (the switcher) is live; **do not set it
  to `true` on the server in stage 1.**
- Before the deploy, look at what the backfill will do (read-only): the number of solo teams that
  will become personal and of accounts that will gain a new personal team.
- Roll back: revert the code and leave the schema (safe). `alembic downgrade 0042` works only while
  nobody has two memberships (see above); the message names how many do.

## Prerequisites recorded by the Opus review (not fixed in this PR on purpose)

None of these can happen while the switch is off and `owner_team_id` is empty; each must be done
before the stage named. The checklist repeats them.

1. **Stage 2, team-owned meetings.** Today `can_read_clause` always lets the recorder read their
   own meeting (`owner_user_id == user.id`) and `owns()` is recorder-only. Spec 6.4 and 9.1 need
   the opposite for a moved meeting: the recorder reads it only while a member of the owning team,
   the team's owner and admins may change it, and it survives the recorder's account deletion
   (shown as "Former member"), but `app/deletion.py` (about line 172) still deletes every meeting
   the account owns, moved ones included. Scenario: Ada moves a meeting to X, leaves X, and still
   reads and deletes it; or deletes her account and the team loses the meeting. Change
   `can_read_clause`, `owns()` and `deletion.py` together, with leak tests for each. The
   visibility docstring now says so instead of claiming the rule already honours team ownership.
2. **Stage 2, the share route.** `POST /api/meetings/{id}/share` uses `teams.ensure_team`, the
   default team. With two ordinary teams and the switch on, Share answers 200 but goes to the
   personal team (readable by nobody else). `tests/test_multi_team_leaks.py::
   test_a_person_with_two_ordinary_teams_can_share_a_meeting_and_the_default_team_is_used`
   asserts exactly this and must be replaced when the route takes a team (spec 6.3: the team is in
   the request, the recorder must be a member, not personal).
3. **Stage 4, switching on.** While the switch is off the one-personal-team rule drifts: someone
   who accepts an invitation has their personal team dissolved; the owner of a team of one that
   somebody joins keeps the team but has no personal team; and an invitation made INTO a team of
   one while off (still allowed, as today) can no longer be redeemed once the switch is on, because
   by then that team may be flagged personal again or `join` refuses it (`personal_team`). Before
   the switch-on: a second backfill (every live account gets a personal team; the check query is
   under Follow-ups) and a plan for pending invitations (list the live invitations whose team is a
   team of one or flagged personal; revoke and re-send them from an ordinary team, or tell their
   senders).

## Follow-ups

- Flag-off joiners (people who accept an invitation after this ships) still have their personal
  team dissolved, as today, and the owner of a team of one that grows into a team has none: it is
  made by `teams.ensure_personal_team` on a flag-on join, on a leave, and (stage 2) on `GET
  /api/teams`. Before stage 4 switches the flag on, check that every live account has one
  (`select count(*) from users u where u.status <> 'deleted' and not exists (select 1 from teams t
  where t.owner_user_id = u.id and t.is_personal)` must be 0, or run the helper for the rest).
- The listed routes still act on the default team; stage 2 adds an explicit team per route and the
  rest of the spec (team-owned meetings, Move, tasks following the meeting, series and mail per team).
  A person with two ordinary teams reads the personal team's series, not X's, until then (a 404,
  never a leak: `tests/test_multi_team_derived.py`).
- `tasks`, `/api/tasks` and `/api/decisions` already read across all of a person's teams (through
  the one rule); a team-scoped board is stage 2.
- Handing a team on (`remove_users`, `transfer_owner`) now clears `is_personal`, so the one-personal-
  team index can never block an account deletion (found in review, tested).
