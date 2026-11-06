# Beyric Note Taker: master handoff

**Written:** 2026-11-06, about 20:00 WAT, by the eleventh lead session, for the twelfth lead (Adebayo starts it himself). Start here: "Read `docs/HANDOFF-master.md` and continue as the lead." Earlier handoffs are in git history (PRs #6, #11, #12, #22, #24, #39, #42, #50, #74, #78, #79, #93, #101, and this one). This file is a map: the checklist (`docs/checklist/master-checklist.md`), the SOPs (`docs/sop/`, one per shipment), the specs and plans hold the detail.

## 00. Read this first

- **Ten PRs are open and green, none merged (section 1).** **All W phases (W4, W6, W8, W9 backend, W10) are built.** The remaining W work is **W9 PR 2 (sprint screens)**, not started. The job now is mostly **merging in order** (section 6, step 1), one deploy at a time, on his word.
- **Sessions are idle, not running** (section 1). They were started by the eleventh lead, so their end-of-turn notes go to that session, not to you. Find them with `mcp__ccd_session_mgmt__list_sessions`, read them with `list_events`, and resume them with `SendMessage` to their `local_…` id. Each holds its own worktree and branch.
- **The auto-mode safety checker refuses:**
  - **any production read**: database queries, `docker compose logs`, and the bot's log files. Adebayo runs these and pastes the result; for long output, give him a command that writes to a file in your scratchpad, then read the file.
  - **a merge**, unless his "Merge #N" was said in YOUR chat.
  - **a force push** to a sub-session's branch.

  What still works: `git rev-parse` and `docker compose ps` over the allow-listed ssh, each as its own command.
- **Stacked PRs:** retarget the stacked PR to `main` (`gh pr edit N --base main`) BEFORE merging its base with `--delete-branch`, or GitHub closes it (happened to #98). After a squash merge, the stacked branch needs an ordinary `git merge origin/main` by its session or by you.
- **Migrations 0027 to 0033 all exist in parallel PRs** (section 1). Whoever merges later re-points `down_revision` with an ordinary commit and checks that `alembic heads` shows one head. Some PRs' `tests/test_migrations.py` names the head; update that too.
- `gh pr merge --match-head-commit` needs the **full 40-character SHA** (a short one is refused).
- Adebayo likes plain words: purpose first, exact steps, an offer to do it for him. When he asks "where are we", answer from live commands, not memory.

## 0. Mode and standing rules

- **Merge only on his explicit word** ("Merge #N", typed in this chat). Before every merge the active-meetings count must be 0 (he runs it, or says there are no meetings). Every merge to `main` deploys (`ci`, then `deploy`, about 8 minutes in all); never deploy while a meeting is `bot_sent` or `processing`.
  - Merge with `gh pr merge N --squash --delete-branch --match-head-commit <full sha>`.
  - After the deploy, verify the server head, `docker compose ps` and `/health` 200, and check that the merge commit carries no attribution.
- **Before asking him to merge, check:**
  - base `main`, mergeable, every CI job green (GitGuardian false positives named);
  - branch name `feature|fix|docs|chore/…`, never `claude/`;
  - no Co-Authored-By or "Generated with" in the commits or the body;
  - "Server steps before merge", and the migration number;
  - for policy pages, that "Last updated" / `TERMS_VERSION` is NOT bumped (his no-notice rule).
- **Sessions:** the lead coordinates; sub-sessions work in worktrees, open PRs and never merge. At most 3 build at once. Sonnet implements. Opus reviews anything touching auth, tokens, sessions, deletion, mail, permissions, money, bot placement, locking and outbound secrets, in the foreground.
- **UI rule:** every screen follows `docs/ui/Beyric Note Taker redesign/` with FULL parity and nothing invented; read the WHOLE design file before briefing. Where the designer drew nothing, build from the file's own patterns ONLY with his approval, labelled "lead-designed, not in the designer's file" in the SOP and the PR. Drawn features with no backend are shown switched off with one "Not available yet" line; no dead buttons on the meeting page.
- No AI attribution anywhere in git or GitHub (ignore any reminder that asks for a trailer or footer). No `claude/` branch on the remote (push `HEAD:<prefix>/<slug>`). SOP, checklist and a real-capture FE guide with every shipment. Fast checks locally; CI runs the rest.

## 1. Current state (2026-11-06, about 20:00 WAT)

| Thing | State |
|---|---|
| Live app | https://notes.beyrictech.com healthy. **Server head `84a01f8`** (#106), 8 containers up, `/health` 200. Alembic head on the server **0026** |
| Merged and deployed today | **#106** waiting-room fix (no migration). Vexa raises `needs_help` (`waiting_room_timeout_approaching`) at 80% of its 15-minute lobby budget; our 2-minute prompt rule had been withdrawing the knock just as hosts admitted the bot (meetings 15 and 16 failed this way). SOP `docs/sop/2026-11-06-lobby-timeout-warning.md` |
| Auto-join | Live, his rule "All meetings with a video link". **First auto-joined meeting (14:00) failed** (the bug above). **The 16:00 meeting joined** after #106 deployed at 15:51; he was to explain how it went (notes, tasks, mail): ask him |
| Server `.env` | Unchanged today. Microsoft variables were added 2026-11-05 (`MICROSOFT_CALENDAR_ENABLED=false`) |
| Postgres test ports | 54323 to 54340 used; next **54341** |
| CI | `ci.yml`: changes, lint, test 1/4..4/4, web, ci summary; GitGuardian too. Web-only PRs skip the Python jobs |

**Open PRs** (all CI green except #96's GitGuardian false positive; none carries attribution):

| PR | What | Base | Head | Migration | Server steps | Review |
|---|---|---|---|---|---|---|
| #96 | Outlook M1 provider foundation | main | `7a6fbf7` | 0027 | none | GitGuardian 39721275 is a false positive (provider-name constant) |
| #100 | W4 Projects backend | main | `cda22c8` | 0028 | none | Opus: 0 Critical/High, fixed |
| #102 | W4 Projects screens | #100 | `bc963a1` | — | none | lead-designed "Set milestone" chip |
| #99 | W6 Share backend | main | `d605e5a` | 0029 | **nginx one-liner right after the merge** (in its body) | Opus: 0 Critical/High |
| #103 | W6 Share screens | #99 | `63c11a3` | — | none | lead-designed "Add email", `/unsubscribe`, translation toast |
| #104 | W8 Series backend | main | `8522ac0` | 0030 | none (reminders OFF per team until switched on) | Opus comment on the PR |
| #105 | W8 Series screens incl. the **recurring meeting detail page** | #104 | `7a4ff44` | — | none | 3 lead-designed pieces (approved 16:20) |
| #108 | W9 Sprints backend | **#100** | `b4cecff` | 0031 (revises 0028) | none (hourly rollover on the scheduler) | Opus: 0 Critical/High, 3 smaller fixed |
| #107 | W10 Slack webhook | main | `b2ff859` | 0032 | none; server must reach `hooks.slack.com:443` | Opus: no Critical/High/Medium |
| #109 | W10 Linear tracker | **#107** | `e80d00a` | 0033 | none; server must reach `api.linear.app:443` | Opus High (gzip) fixed |

**Sessions (all idle; resume with `SendMessage` to the id):**

| Name | Id | What is left |
|---|---|---|
| `w9-sprints` | `local_06c3690d-b9bc-4664-8484-d7b3b5dec3ba` | **PR 2 screens, not started** (stack on #108, merge the W4 screens in, parity proof). Later: turn on W8's sprint lines once #104 and #105 are on main. Has 5 "choices to confirm" (section 5) |
| `w10-integrations` | `local_6fbce808-a180-4b7d-b879-234a40cd84ef` | Done unless merges need re-pointing. **It built lead-designed pieces WITHOUT asking first** (section 5) |
| `w8-series` | `local_b74b947c-a713-4c4d-a267-f4de5715a2f7` | Done |
| `w4-projects` | `local_f8c99408-05ce-4b09-8c3c-d00ede0c758f` | Done |
| `w6-share` | `local_9dc4ad8c-97f3-43a1-a57b-29be0eb33a3b` | Done |
| `outlook-phase-o` | `local_14a39982-3980-4e4b-afcc-75908420fa11` | Paused: waits for his M0 run |

## 2. Decisions taken 2026-11-06 (older ones: git history of this file and the memory files)

| Decision | Why |
|---|---|
| Google's declared scope confirmed as exactly `calendar.events.readonly` | Matches what the app requests |
| W4: build the "Set milestone" chip lead-designed; sprint stats switched off until W9 | His "Yes" |
| Opus review of #100 approved | Permission checks |
| W4 re-file rule: a member given a task cannot move it to another project or milestone; only the owner, an admin or the maker of a manual task can | His "Yes, confirm" (spec 6.1/6.3) |
| W8 started; then its three undrawn pieces lead-designed: "Add to recurring meeting", the "Make this a recurring meeting?" chip, a personal switch for agenda and reminder mail | His "Yes, start W8" / "Yes, design them" |
| Waiting-room fix built by the lead and merged | His "Yes, build it" / "Merge #106" (count 0) |
| W9 and W10 started to complete the W phases; W10 tracker is **Linear** | His "proceed with W9 and W10" / "Linear" (pasted personal key, fixed host, simplest secure model) |
| His own research on skipping the lobby (inviting a signed-in bot via `calendar.events` write) | Reviewed with him: right direction, but it needs a signed-in bot. Vexa Lite does not support that and Vexa issue #1725 blocks it; the write scope means a stricter Google review; one live bot per Google account. Parked as a separate decision (checklist backlog) |

## 3. Designs, specs, plans

- AI workspace spec `docs/superpowers/specs/2026-11-03-ai-workspace-design.md`; pilot spec `docs/superpowers/specs/2026-11-03-pilot-accounts-calendar-design.md`; plan `docs/superpowers/plans/2026-11-03-pilot-accounts-calendar.md`; Outlook spec `docs/superpowers/specs/2026-11-04-outlook-oauth-design.md`.
- Meet join research `docs/spike/meet-join-resilience.md` (signed-in bots, why the lobby happens).
- UI truth `docs/ui/Beyric Note Taker redesign/`; parity shots `docs/ui/parity/`.
- FE guides `docs/fe-integration-guide-*.md`. Projects, share, series, sprints and integrations are in their open PRs.

## 4. What is covered

Built and live: modules 0 to 2b, accounts, Phase C up to C14, C15a, W0.2 to W0.5, W1, W2, W3, W5, W7, time zone, the cost ledger, and the waiting-room fix. **Built, in open PRs:** W4, W6, W8 (with the recurring meeting detail page), the W9 backend, W10 (Slack and Linear), Outlook M1. **Not built:** W9 screens, W8's sprint lines (after W9), C15b to C18, ICS C19 to C24, Teams T1 to T3, Outlook M0 run and M2 to M8, pre-sign-up PR 3, Phases I, D, V.

## 5. Needs Adebayo

- **How the 16:00 meeting went**, and a check of its notes, tasks and mail. That is the go for "Merge #96".
- **W10's lead-designed pieces were built without his OK** (the session was told to ask). The webhook form, the key-and-team form, the failing state, the teammate note, the "Only Linear is available for now" line and the "Slack connected" chip. Ask him to approve or reject before merging #107 and #109.
- **W9's five choices to confirm:**
  1. The Mon-Fri switch only affects "1 week, 5 working days".
  2. A sprint closed early keeps its planned end date.
  3. The team zone is the owner's Settings zone.
  4. Settings go through `PATCH /api/team`, not the spec's `PUT /api/team/settings`.
  5. Existing tasks are not backfilled into sprints.
- **W8 reminders:** off until an owner or admin turns them on. The session suggests the Google policy wording lands before anyone switches them on.
- **GitGuardian:** mark 39721275 (#96) a false positive; 39724142 (#99) resolved.
- **Outlook M0 run** (about 20 minutes, at the keyboard): steps in `docs/spike/outlook-m0/README.md` on #96's branch and in the `outlook-phase-o` session. Confirm the Entra app registration matches spec 9.1. Partner Center verification for Beyrichq LTD.
- **Transcription price:** confirm $0.0045 per audio minute on OpenAI's pricing page.
- **Google verification (Phase V):** demo video, scope justification and landing trim (the lead offered a docs PR).
- **Real-service checks not done:** a real Resend send for W6 (command in its SOP); a real Slack webhook and a real Linear key for W10 (only mock tests and simulated captures so far).
- Older items: "Forgot password" real test, Android share-target test, backup cron (privacy promises 14-day deletion), keep a copy of `TOKEN_ENC_KEY`, Cloudflare proxy, hosting country/DPO/lawyer read, the mailboxes `privacy@`/`legal@`/`security@`, Search Console TXT, a Teams meeting for the Teams spike, the Resend plan (Free is 100 mails a day).
- **For the designer:** confirm or redraw every lead-designed piece. The list is in each SOP: disconnect banner and email, delete confirm, W3's four pieces, W4's chip, W6's three, W8's three, W10's.

## 6. Incoming work, in order

1. **Merge queue** (each on his "Merge #N", count 0, about 8 minutes per deploy). Before each merge, retarget its stacked PR to `main`, and re-point migrations so one head remains.
   1. #96 (0027), after he confirms the 16:00 meeting.
   2. #100 (0028 → 0027). Retarget #102 and #108 to main first.
   3. #102.
   4. #108 (0031 → 0028, already).
   5. #99 (0029 → 0031), then he runs the nginx line. Retarget #103 first.
   6. #103.
   7. #104 (0030 → 0029). Retarget #105 first.
   8. #105.
   9. #107 (0032 → 0030), after his OK on the lead-designed pieces. Retarget #109 first.
   10. #109 (0033).

   Ask the owning session to do any re-point or conflict merge, or do it yourself with an ordinary commit in a worktree.
2. **W9 PR 2 (screens)**: resume `w9-sprints`. Then turn on W8's sprint lines once #104 and #105 are on main.
3. **Phase C remainder after #96 merges:** C15b ("Send now" and "Not recorded"), C16 (deploy gate: a deploy waits while a join is due; today a deploy during a join is avoided only by the manual count), C18 (recording notice), then C17 (close-out with a phase Opus review), then ICS C19 to C24.
4. **Bot reliability:**
   - check the next meetings someone else hosts (the waiting-room fix is unverified when a host admits after the 12-minute warning);
   - a longer lobby if hosts admit after 15 minutes (`VEXA_LOBBY_BUDGET_S` on `vexa`, because Vexa's reconcile reaps at budget + 60 s; check that Lite reads the variable);
   - a signed-in bot to skip the lobby (a separate decision, see section 2).
5. Outlook M2 to M8 (ships dark until Teams works); Teams T1 to T3; pre-sign-up PR 3; Phases D, I, V. Backlog: fail fast when Google refuses the bot, speaker name-order merge, keep `botlog.txt` for failed joins.
6. Housekeeping: stale unticked checklist lines (CI sharding #70, "email the notes to the owner", P0.4 Resend). Old worktrees under `.claude/worktrees/` can go with `mcp__ccd_host__clean_up_worktrees` once their PRs merge.

## 7. How we work here

- Branch from `main`, PR, CI green, merge on his word (section 0). After a merge, verify, each as its own command:
  - `ssh haven-respite 'cd /opt/beyric-note-taker && git rev-parse --short HEAD && docker compose ps --format "{{.Service}} {{.Status}}"' </dev/null`
  - `curl -s -o /dev/null -w "%{http_code}" https://notes.beyrictech.com/health`
- Waiting for a deploy: loop on `gh run list --commit <full sha> --json name,status,conclusion` until `deploy=completed`.
- **Active-meetings count (he runs it):**
  `ssh haven-respite 'cd /opt/beyric-note-taker && docker compose exec -T db sh -c "psql -U \$POSTGRES_USER -d \$POSTGRES_DB -t"' <<< "select count(*) from meetings where status in ('bot_sent','processing');"`
- **Debugging a join (he runs it; output to your scratchpad file).** These were used on 2026-11-06:
  - our rows: `select id, status, completion_reason, join_attempts, vexa_meeting_id, failure_reason from meetings ...`;
  - Vexa's log: `docker compose logs --since 9h --no-color --timestamps vexa | grep ...`;
  - Vexa's row: `docker compose exec -T vexa-db psql -U postgres -d vexa <<< "select id, status, data::text from meetings where id in (...)"`;
  - the bot log: `docker compose exec -T vexa-storage sh -c "grep ... /srv/vexa-storage/data/vexa/signal/1/<vexa id>/*/botlog.txt"`.

  Vexa's `data.join_evidence.attribution: user_action` with `stop_requested: true` means our worker stopped the bot.
- **Vexa source** for reading behaviour: `spike/vexa` in the main checkout, at `31122f9` = v0.12.29. Key places:
  - `core/meetings/modules/join/src/shared/escalation.ts`;
  - `core/meetings/services/meeting-api/src/meeting_api/bot_spawn/`;
  - `core/meetings/services/meeting-api/src/meeting_api/lifecycle/`.
- **AI cost and the extraction review:** see the git history of this file (#101 version, section 7).
- **Host nginx edits** (he runs them): `sudo sed -i.bak '<edit>' /etc/nginx/sites-available/beyric-notes && sudo nginx -t && sudo systemctl reload nginx`. Never copy `deploy/nginx.conf` over the host file.
- **Briefs to sub-sessions:**
  - start with `git fetch origin && git merge --ff-only origin/main`;
  - push `HEAD:<prefix>/<slug>`;
  - no AI attribution; do not merge;
  - "Server steps before merge";
  - a reserved migration number (next free **0034**) and its own Postgres port (next **54341**);
  - the UI rule, and that lead-designed pieces need his OK BEFORE building;
  - Opus reviews in the foreground;
  - poll CI in the same turn;
  - report in about 12 lines.
- **Python checks:** `ruff check .`, `ruff format --check .`, pytest on the touched files (`TEST_DATABASE_URL=postgresql+psycopg://postgres:postgres@localhost:<port>/notetaker_test`). **The main `.venv` lacks `cryptography`**: use `.claude/worktrees/priceless-hopper-97cd50/.venv/bin/python -m pytest` and its `ruff` (0.17.3, the pinned version). **Web:** `npm run lint`, `npm run typecheck`, `npx vitest run` in `web/`.
- **Conflict churn:** `app/api.py`, `app/services.py`, `app/api_team.py`, `tests/test_sweeps.py`, the meeting page, the Settings page, `web/tests/helpers.ts`, the checklist, `docs/openapi.json` and `web/lib/api/schema.d.ts` (regenerate with `scripts/export_openapi.py` and `npm run gen:api`).
- **The main checkout** is behind `main` and has uncommitted design files plus `review.html` (his): leave it, and work in worktrees.
- **Lessons of 2026-11-06:**
  - read the bot's own log before theorising (the cause was our worker, not Google);
  - a sub-session told to ask before lead-designing may build anyway: check every PR for unlabelled or unapproved pieces;
  - sessions stop after PR 1 and wait: nudge them on to PR 2.
