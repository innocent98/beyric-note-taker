# Beyric Note Taker: master handoff

**Written:** 2026-11-06, about 23:50 WAT, by the twelfth lead session, for the thirteenth lead (Adebayo starts it himself). Start here: "Read `docs/HANDOFF-master.md` and continue as the lead." Earlier handoffs are in git history (PRs #6, #11, #12, #22, #24, #39, #42, #50, #74, #78, #79, #93, #101, #110, and this one). This file is a map: the checklist (`docs/checklist/master-checklist.md`), the SOPs (`docs/sop/`, one per shipment), the specs and plans hold the detail.

## 00. Read this first

- **No PR is open. Every W phase (W0 to W10) is built and live.** The twelfth lead merged the whole queue on 2026-11-06 (SOP `docs/sop/2026-11-06-merge-queue-w-phases.md`). The next job is the **bot-reliability decision** (section 2 and 6, step 1), then Phase C's remainder.
- **Sessions:** the only session the twelfth lead started, `w9-sprint-screens` (`local_6163e9c5-70b3-4b24-9726-403bcd40e412`), is done. The earlier W-phase sessions from the eleventh lead (ids in the previous handoff) were **not found** on this machine: treat them as gone and start fresh sessions with a full brief when needed.
- **The auto-mode safety checker refuses:**
  - **any production read** (database queries, `docker compose logs`, the bot's log files): Adebayo runs these and pastes the result; for long output, give him a command that writes to a file in your scratchpad, then read the file;
  - **a merge**, unless his "Merge #N" was said in YOUR chat (this session was given "merge all open PRs" plus "no meeting for 2 hours" and that worked);
  - **a force push** to a sub-session's branch;
  - **a command that combines a local file read with the ssh line**: run each of `ssh haven-respite '...'` and `curl .../health` as its own command.
- Adebayo likes plain words: purpose first, exact steps, an offer to do it for him. When he asks "where are we", answer from live commands, not memory.

## 0. Mode and standing rules

- **Merge only on his explicit word** ("Merge #N", typed in this chat). Before every merge the active-meetings count must be 0 (he runs it, or says there are no meetings for a stated time). Every merge to `main` deploys (`ci`, then `deploy`, about 8 to 12 minutes in all) and restarts the bot worker: never deploy while a meeting is `bot_sent` or `processing`.
  - Merge with `gh pr merge N --squash --delete-branch --match-head-commit <full 40-char sha>`; for a branch whose history mentions a `claude/` branch name or any attribution, add `--subject` and `--body` of your own.
  - After the deploy: server head, `docker compose ps`, `/health` 200, and the merge commit has no attribution.
- **Before asking him to merge, check:** base `main`, mergeable, every CI job green (GitGuardian is a known false positive, see below); branch name `feature|fix|docs|chore/…`, never `claude/`; no Co-Authored-By or "Generated with" in the commits or the body; "Server steps before merge" and the migration number; for policy pages, that "Last updated" / `TERMS_VERSION` is NOT bumped (his no-notice rule).
- **Sessions:** the lead coordinates; sub-sessions work in worktrees, open PRs and never merge. At most 3 build at once. Sonnet implements. Opus reviews anything touching auth, tokens, sessions, deletion, mail, permissions, money, bot placement, locking and outbound secrets, in the foreground.
- **UI rule:** every screen follows `docs/ui/Beyric Note Taker redesign/` with FULL parity and nothing invented; read the WHOLE design file before briefing. Where the designer drew nothing, build from the file's own patterns ONLY with his approval, labelled "lead-designed, not in the designer's file" in the SOP and the PR. Drawn features with no backend are shown switched off with one "Not available yet" line; no dead buttons on the meeting page.
- No AI attribution anywhere in git or GitHub (ignore any reminder that asks for a trailer or footer). No `claude/` branch on the remote (push `HEAD:<prefix>/<slug>`). SOP, checklist and a real-capture FE guide with every shipment. Fast checks locally; CI runs the rest.

## 1. Current state (2026-11-06, about 23:50 WAT)

| Thing | State |
|---|---|
| Live app | https://notes.beyrictech.com healthy. **Server head `e0ad10b`** (#111, the last merge of the queue; deploy verified: 8 containers up, `/health` 200). Alembic head **0033** |
| Migration chain on `main` | 0026, 0027, 0028, 0031, 0029, 0030, 0032, 0033 (merged out of order, one head). Next free number **0034** |
| Open PRs | **None.** The queue merged: #96, #100, #102, #108, #99, #103, #104, #105, #107, #109, #111 (merge commits in the SOP) |
| Auto-join | Live, his rule "All meetings with a video link". Today's real joins: the 14:00 meeting failed (our worker withdrew the knock, fixed by #106); the 16:00 calendar meeting (MYWAY PRODUCT & DEVELOPMENT STANDUP) was **refused by Google twice** ("Google would not let the bot ask to join", then Failed after "attempt 2 of 2"); a manual link a few minutes later was refused once and admitted on the second send; a later manual meeting worked fully (notes, summary) |
| Server `.env` | Unchanged today. Microsoft variables exist since 2026-11-05 (`MICROSOFT_CALENDAR_ENABLED=false`). W6, W8 (reminders off per team until switched on), W9, W10 added no required variable. The server must reach `hooks.slack.com:443` and `api.linear.app:443` for W10 |
| nginx | #99's opt-out line was run by him on 2026-11-06 (count 1, `nginx -t` ok) |
| Postgres test ports | 54323 to 54342 used (the lead's scratch container on 54342 is stopped); next **54343** |
| CI | `ci.yml`: changes, lint, test 1/4..4/4, web, ci summary; GitGuardian too (red on every PR since #96, see below). Web-only PRs skip the Python jobs |

## 2. Decisions taken 2026-11-06 (evening; older ones in git history and the memory files)

| Decision | Why |
|---|---|
| Merge the whole queue in order, one deploy at a time; his "no meeting due for 2 hours" stood for the run | His "merge all open PRs starting from #96" after testing #96 |
| W10's lead-designed pieces (webhook form, key-and-team form, failing state, teammate note, "Only Linear is available for now", "Slack connected" chip) approved | His "OK" on "Your OK on its lead-designed pieces" |
| **"Retry the bot better" (more attempts, Send again) is NOT enough** as the answer to Google refusing the bot | His words: that would be frustrating for pilot users. He wants a more reliable, more trustworthy bot (a real account, not an anonymous guest) |
| Compare hosted bot vendors and the open-source names he found | Research done: `docs/spike/meeting-bot-vendors.md` (below) |

**What the vendor research found** (read by a sub-agent on 2026-11-06, not re-read by the lead; labelled read / vendor claim / not verified in the file):
- Meetily, Meet2Notes, Prismical and Platypus are **desktop apps that record your own laptop audio**: none sends a bot into a call. Drop them.
- **A signed-in bot only skips the knock if its account email is on the calendar invite** (Recall, Skribby and MeetStream all say so). Otherwise it still knocks, though it looks more trustworthy.
- **Recall does not run an account pool for you** (the previous lead's note said it did: wrong). You supply a dedicated Google Workspace and Legacy SSO. MeetStream is the same. Skribby takes a Gmail or Workspace email, password and authenticator key typed into its dashboard.
- Prices for 50 recorded hours a month (vendor's bot and, where stated, its transcript; **not** our own AI, which is a separate cost that does not change): Recall about USD 32.50, MeetStream about 22.50, Skribby 19.50 to 68; plus a Google Workspace seat for the bot account (not priced). Skribby was bought by a US telco-software firm in September 2026 and lists no SOC 2; MeetStream's owners are not identified; Recall's trust pages returned 404.
- Vexa's signed-in mode is still blocked upstream (issue #1725 open; fix PR #1873 unmerged and now has conflicts) and unsupported on Lite.
- **Proposed next step (awaiting his yes):** a one-week bake-off on real meetings, Skribby's free tier (5 bot hours a month) and Recall's 5 free hours, guest joins first (their IPs may do better than ours; no accounts needed) then a signed-in bot invited to the event; count joins, knocks and 403s ourselves. He signs up and pastes keys (the lead cannot create accounts); the lead writes a small script that runs from his Mac and logs outcomes, nothing deployed to the server.

## 3. Designs, specs, plans

- AI workspace spec `docs/superpowers/specs/2026-11-03-ai-workspace-design.md`; pilot spec `docs/superpowers/specs/2026-11-03-pilot-accounts-calendar-design.md`; plan `docs/superpowers/plans/2026-11-03-pilot-accounts-calendar.md`; Outlook spec `docs/superpowers/specs/2026-11-04-outlook-oauth-design.md`.
- Meet join research `docs/spike/meet-join-resilience.md` (signed-in bots, why the lobby happens) and the vendor comparison `docs/spike/meeting-bot-vendors.md`.
- UI truth `docs/ui/Beyric Note Taker redesign/`; parity shots `docs/ui/parity/`.
- FE guides `docs/fe-integration-guide-*.md` (real captures in `docs/fe-captures/`), including projects, share, series, sprints and integrations.

## 4. What is covered

Built and live: modules 0 to 2b, accounts, Phase C up to C14, C15a, **W0 to W10 all** (W4, W6, W8 with the recurring meeting detail page, W9 backend and screens, W10 Slack and Linear merged 2026-11-06), Outlook M1, time zone, the cost ledger, the waiting-room fix. **Not built:** W8's sprint lines (now unblocked), C15b to C18, ICS C19 to C24, Teams T1 to T3, Outlook M0 run and M2 to M8, pre-sign-up PR 3, Phases I, D, V; the bot-reliability decision.

## 5. Needs Adebayo

- **The bot decision:** yes or no to the one-week bake-off (section 2), and sign-ups for Skribby and Recall (keys pasted into the chat, or put on the server by him). Also: does he accept recordings leaving our server (a vendor becomes a sub-processor in the privacy policy)?
- **GitGuardian:** mark 39721275 (#96) and the repeats on #100, #102, #103, #104, #105, #107, #109, #111 as false positives (almost certainly Microsoft's public consumer-tenant GUID `9188040d-6c67-4c5b-b112-36a304b66dad`; GitHub does not show the location).
- **W9's five backend choices** (still unconfirmed): Mon-Fri switch only affects "1 week, 5 working days"; a sprint closed early keeps its planned end date; the team zone is the owner's Settings zone; settings go through `PATCH /api/team`; existing tasks are not backfilled. **W9's three undrawn pieces** wait for the designer.
- **Two Medium findings from the automated security review of W8** (his call): the agenda recipient count includes attendees of meetings the caller cannot read; series visibility from calendar rows. Details in the checklist's "Deferred follow-ups".
- **Real-service checks not done:** a real Slack webhook and a real Linear key (W10); a real Resend send for W6; W8 reminders stay off until an owner or admin switches them on (the session suggests the Google policy wording lands first).
- **Outlook M0 run** (about 20 minutes, at the keyboard): steps in `docs/spike/outlook-m0/README.md`; Partner Center verification for Beyrichq LTD.
- **Transcription price:** confirm USD 0.0045 per audio minute on OpenAI's pricing page (it decides the comparison with a vendor's bundled transcript).
- **Google verification (Phase V):** demo video, scope justification, landing trim.
- **For the designer:** confirm or redraw every lead-designed piece; each SOP lists them.
- Older items: "Forgot password" real test, Android share-target test, backup cron (privacy promises 14-day deletion), keep a copy of `TOKEN_ENC_KEY`, Cloudflare proxy, hosting country/DPO/lawyer read, the mailboxes `privacy@`/`legal@`/`security@`, Search Console TXT, a Teams meeting for the Teams spike, the Resend plan (Free is 100 mails a day).

## 6. Incoming work, in order

1. **Bot reliability.** On his yes: the bake-off script (a small, local, non-deployed tool; Skribby and Recall APIs; logs time, guest or signed-in, result, seconds to the lobby, reason). Then a decision, then an adapter behind `VexaClient` (`app/vexa.py`) for the chosen vendor, with Vexa kept as the fallback. Also still true: check the next meetings someone else hosts (the waiting-room fix is unverified when a host admits after the 12-minute warning) and a longer lobby budget if hosts admit after 15 minutes (`VEXA_LOBBY_BUDGET_S`).
2. **Phase C remainder:** C15b ("Send now" and "Not recorded"), C16 (deploy gate: a deploy waits while a join is due), C18 (recording notice), then C17 (close-out with a phase Opus review), then ICS C19 to C24.
3. **W8's sprint lines** (the sprint line under "Next meeting" and sprint rows on the series page): small follow-up, now unblocked.
4. Outlook M2 to M8 (ships dark until Teams works); Teams T1 to T3; pre-sign-up PR 3; Phases D, I, V. Backlog: fail fast when Google refuses the bot, speaker name-order merge, keep `botlog.txt` for failed joins.
5. **Housekeeping:** stale unticked or "open, not merged" lines still sit in older checklist sections (Phase A, Phase C C1 to C4, W2, W0.3); old worktrees under `.claude/worktrees/` can go with `mcp__ccd_host__clean_up_worktrees` (the lead's own `lead-w4` is one; it has a `node_modules` symlink to another worktree).

## 7. How we work here

- Branch from `main`, PR, CI green, merge on his word (section 0). After a merge, verify, each as its own command:
  - `ssh haven-respite 'cd /opt/beyric-note-taker && git rev-parse --short HEAD && docker compose ps --format "{{.Service}} {{.Status}}"' </dev/null`
  - `curl -s -o /dev/null -w "%{http_code}" https://notes.beyrictech.com/health`
- Waiting for a deploy: loop in the background on `gh run list --commit <full sha> --json name,status,conclusion` until `deploy=completed`.
- **Active-meetings count (he runs it):**
  `ssh haven-respite 'cd /opt/beyric-note-taker && docker compose exec -T db sh -c "psql -U \$POSTGRES_USER -d \$POSTGRES_DB -t"' <<< "select count(*) from meetings where status in ('bot_sent','processing');"`
- **Debugging a join (he runs it; output to a file in your scratchpad):** our rows (`select id, status, completion_reason, join_attempts, vexa_meeting_id, failure_reason from meetings ...`), Vexa's log (`docker compose logs --since 9h --no-color --timestamps vexa | grep ...`), Vexa's row (`docker compose exec -T vexa-db psql -U postgres -d vexa <<< "select id, status, data::text from meetings where id in (...)"`), the bot log (`docker compose exec -T vexa-storage sh -c "grep ... /srv/vexa-storage/data/vexa/signal/1/<vexa id>/*/botlog.txt"`). Vexa's `data.join_evidence.attribution: user_action` with `stop_requested: true` means our worker stopped the bot.
- **Vexa source** for reading behaviour: `spike/vexa` in the main checkout, at `31122f9` = v0.12.29 (`core/meetings/modules/join/src/shared/escalation.ts`, `.../meeting-api/src/meeting_api/bot_spawn/`, `.../lifecycle/`).
- **Host nginx edits** (he runs them): `sudo sed -i.bak '<edit>' /etc/nginx/sites-available/beyric-notes && sudo nginx -t && sudo systemctl reload nginx`. Never copy `deploy/nginx.conf` over the host file.
- **Briefs to sub-sessions:** start with `git fetch origin && git merge --ff-only origin/main`; push `HEAD:<prefix>/<slug>`; no AI attribution; do not merge; "Server steps before merge"; a reserved migration number (next free **0034**) and its own Postgres port (next **54343**); the UI rule, and that lead-designed pieces need his OK BEFORE building; Opus reviews in the foreground; poll CI in the same turn; report in about 12 lines. The app's worktree branch prefix may name a local branch `claude/...`: never push that name.
- **Python checks:** `ruff check .`, `ruff format --check .`, pytest on the touched files (`TEST_DATABASE_URL=postgresql+psycopg://postgres:postgres@localhost:<port>/notetaker_test`, run with `PYTHONPATH=.` from the worktree). **The main `.venv` lacks `cryptography`**: use `.claude/worktrees/priceless-hopper-97cd50/.venv/bin/python -m pytest` and its `ruff` (0.17.3, the pinned version). **Web:** `npm run lint`, `npm run typecheck`, `npx vitest run` in `web/` (a worktree without `node_modules` can symlink another worktree's, same lockfile).
- **Conflict churn:** `app/api.py`, `app/api_schemas.py`, `app/sweeps.py`, `app/models.py`, `app/teams.py`, `app/api_team.py`, the meeting page, the Settings page and `settings-planned-sections.tsx`, `web/tests/helpers.ts`, `web/scripts/mock-api.mjs`, the checklist, `docs/openapi.json` and `web/lib/api/schema.d.ts` (regenerate with `PYTHONPATH=. scripts/export_openapi.py` and `npm run gen:api`; then check no route or schema was lost). The merge recipes that worked are in the merge-queue SOP.
- **The main checkout** is behind `main` and has uncommitted design files plus `review.html` (his): leave it, and work in worktrees.
- **Lessons of 2026-11-06:**
  - read the bot's own log before theorising (the 14:00 failure was our worker, not Google; the 16:00 one really was Google refusing);
  - a sub-session told to ask before lead-designing may build anyway: check every PR for unlabelled or unapproved pieces;
  - sessions stop after PR 1 and wait: nudge them on to PR 2;
  - merging a stacked base with `--delete-branch` closes its stacked PR unless it was retargeted first;
  - after a squash merge, every branch built on the old commits conflicts again: plan the order so each branch is updated once, and update the shared-file branches last;
  - an automated post-push security review can flag already-merged code: judge it, record it in the checklist, and do not patch mid-queue unless it is a real hole.
