# SOP: the 2026-11-06 merge queue (W4, W6, W8, W9, W10 and Outlook M1 on `main`)

## What shipped
Ten open PRs merged and deployed one at a time on 2026-11-06 (21:31 to 23:45 WAT), on Adebayo's word ("merge all open PRs, starting from #96"; "no meeting due for 2 hours"; his "OK" on W10's lead-designed pieces). Server head after the last deploy: `e0ad10b`, Alembic head **0033**. **Every W phase of the AI workspace spec (W0 to W10) is now built and live.**

| Order | PR | What | Merge commit | Migration |
|---|---|---|---|---|
| 1 | #96 | Outlook M1: provider registry, neutral account columns, `{provider}` routes | `b239e12` | 0027 |
| 2 | #100 | W4 Projects and milestones backend | `a1488fa` | 0028 (re-pointed to 0027) |
| 3 | #102 | W4 screens | `964e5b9` | none |
| 4 | #108 | W9 Sprints backend | `32dee7c` | 0031 (on 0028) |
| 5 | #99 | W6 Share notes by email backend | `6319dbe` | 0029 (re-pointed to 0031) |
| 6 | #103 | W6 screens | `5ba2305` | none |
| 7 | #104 | W8 Series backend | `9471c2c` | 0030 (re-pointed to 0029) |
| 8 | #105 | W8 screens incl. the recurring meeting detail page | `4b1e26d` | none |
| 9 | #107 | W10 Slack webhook | `e3b7324` | 0032 (re-pointed to 0030) |
| 10 | #109 | W10 Linear tracker | `ee3ec88` | 0033 (on 0032) |
| 11 | #111 | W9 sprint screens | `e0ad10b` | none |

(#111 is the eleventh merge; the "ten PRs" of the handoff were #96 to #109 without it.) The chain on `main` is **0026, 0027, 0028, 0031, 0029, 0030, 0032, 0033**, one head. Numbers were merged out of order on purpose: each PR had been built in parallel from 0026 and only `down_revision` was re-pointed. Next free number: **0034**.

## Why
Eleven sessions had built features in parallel on branches from the same base. Merging them one by one, each re-pointed and tested against the newest `main`, was the cheapest safe way to ship them without a long freeze.

## How (and what to repeat next time)
- **One at a time, verified.** Each merge: CI green on the exact head, `gh pr merge --squash --delete-branch --match-head-commit <40-char sha>`, wait for `ci` then `deploy`, then `git rev-parse --short HEAD` and `docker compose ps` on the server (each its own command) and `/health` 200, then the next.
- **Retarget stacked PRs first** (`gh pr edit N --base main`) before merging their base with `--delete-branch`.
- **After a squash merge, a branch built on the old commits conflicts with `main`** even where the content is the same. Two fixes that worked, neither needs a force-push: (a) where the branch only has a few own commits, check out `origin/main`, `git cherry-pick -n` its own commits (or `git diff --binary <old base> <branch> | git apply --3way --index`), resolve, then `git commit-tree <tree> -p <old branch head> -p origin/main` and push that as an ordinary fast-forward; (b) ask the owning session to `git merge origin/main`.
- **Real overlaps** (the same lists in `app/api.py`, `app/api_schemas.py`, `app/sweeps.py`, `app/models.py`, `app/teams.py`, the Settings page, the meeting page, `web/tests/helpers.ts`, the checklist) were additive: keep both sides. Generated files (`docs/openapi.json`, `web/lib/api/schema.d.ts`) were taken from `main` and regenerated with `PYTHONPATH=. python scripts/export_openapi.py` and `npm run gen:api`, then checked that no route or schema was lost.
- **Tests that count things changed:** the hourly sweep count in `tests/test_sweeps.py` (7 to 10), the "only head" migration test (moved up each time), and test mocks of `@/lib/api/browser` that did not know the new `milestones.forMeeting` call (three web test files).
- **Squash messages:** two PRs (#105, #111) had commit messages naming a `claude/` branch in their history, so they were merged with `--subject` and `--body` of the lead's own to keep that text off `main`.
- **Server step:** #99's nginx line (the public `/api/mail/opt-out` route in the rate-limit location) was run by Adebayo after its merge; `grep -c` printed 1 and `nginx -t` passed.

## What's involved
Everything in the ten PRs' SOPs (`docs/sop/2026-11-05-w3-*`, `...-w4-*`, `...-w6-*`, `2026-11-06-w4-*`, `-w6-*`, `-w8-*`, `-w9-*`, `-w10-*`). This pass touched only merge resolutions: no new feature code. Resolution changes beyond union-of-both: `alembic/versions/0028_*`, `0029_*`, `0030_*`, `0032_*` (`down_revision` and docstring `Revises`), `tests/test_migrations.py`, `tests/test_sweeps.py`, `web/tests/share-dialog.test.tsx`, `meeting-series.test.tsx`, `series-lead-designed.test.tsx` (milestones stub), and this PR's fix to `web/tests/team-view.test.tsx`.

## Verification
| Check | Result |
|---|---|
| Server head and containers after each deploy | `b239e12`, `a1488fa`, `964e5b9`, `32dee7c`, `6319dbe`, `5ba2305`, `9471c2c`, `4b1e26d`, `e3b7324`, `ee3ec88`, `e0ad10b` each seen live with 8 containers up and `/health` 200 |
| Local checks before each push | `ruff check`, `ruff format --check`, `alembic heads` one head, migration tests, accounts (route enumeration), sweeps, the touched modules; web `lint`, `typecheck`, full `vitest` (692 to 754 tests) |
| CI on every merged head | `ci` green; `GitGuardian` red on all of them after #96 (see below) |
| The migrations on the server | Run by the deploy (`alembic upgrade head`); the database itself was not read (production reads are Adebayo's) |
| Not verified | Real Slack webhook, real Linear key, a real Resend share, W9 and W8 screens against the real backend in a browser |

**GitGuardian.** Red on #96 and then on every PR that re-included #96's diff in a merge commit. Inferred cause (GitHub does not show the location): `9188040d-6c67-4c5b-b112-36a304b66dad`, Microsoft's public consumer-tenant GUID, in #96's code and tests. Adebayo marks the findings false positives on the dashboard.

**Flaky test.** `web/tests/team-view.test.tsx` "opens by itself with the spoken name" failed once on CI (the dialog showed before its Name field was filled), passed on rerun and in three local runs; this PR waits for the value with `waitFor`.

## Operate / roll back
Revert one feature by reverting its squash commit and re-pointing the later migrations; a migration after it still needs its own downgrade. Vexa and the bot worker were restarted by each deploy: do not deploy while a meeting is `bot_sent` or `processing`.

## Follow-ups
Listed in the checklist section "Deferred follow-ups from the 2026-11-06 merge queue": two Medium findings from the automated security review of W8 (agenda recipient count; series visibility from calendar rows), the W9 undrawn pieces and unconfirmed choices, the meeting-page parity reshoot, real Slack and Linear checks, and the bot-reliability bake-off (`docs/spike/meeting-bot-vendors.md`).
