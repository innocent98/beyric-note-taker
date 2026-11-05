# Beyric Note Taker: master handoff

**Written:** 2026-11-05, about 17:00 WAT, by the ninth lead session, for the next lead. Start here: "Read `docs/HANDOFF-master.md` and continue as the lead." Earlier handoffs are in git history (PRs #6, #11, #12, #22, #24, #39, #42, #50, #74, #78, #79, and this one). This file is a map: the checklist (`docs/checklist/master-checklist.md`), the SOPs (`docs/sop/`, one per shipment), the specs and plans hold the detail.

## 00. Read this first

- **Usage:** Adebayo was near his weekly limit on 2026-11-04; on 2026-11-05 he said "we need to move fast" and allowed sessions again. Several sub-sessions hit their **usage limit** mid-task on 2026-11-05 afternoon. Keep at most 3 running, Sonnet to build, Opus only for security-class reviews, and do small fixes yourself.
- **What does not carry over to a different account:** the memory files (`~/.claude/projects/-Users-adebayovictor-Documents-dev-beyric-note-taker/memory/`) and `~/.claude/CLAUDE.md` live on this Mac; section 7 restates the rules. The read-only ssh allow rules are in `.claude/settings.local.json` (git-ignored).
- **The auto-mode safety checker:** it refuses (a) any query of the production database: **Adebayo runs those and pastes the result** (copy-paste blocks in section 7); (b) a merge unless his "Merge #N" was said in YOUR chat; (c) a **force push to a sub-session's branch** (2026-11-05): push an ordinary new commit on top, or push a rebased branch under a new name. Server head and `docker compose ps` over the allow-listed ssh are fine, **run as their own command** (chaining them with other commands was refused).
- **Stacked PRs:** `gh pr merge --delete-branch` on a PR that another PR is stacked on makes GitHub CLOSE the stacked one (happened to #91, replaced by #92). Retarget the stacked PR to `main` first (`gh pr edit N --base main`).
- **Migration numbers collide** when parallel sessions add migrations. The lead re-points `down_revision` at merge time (done for 0020 and 0021 today). Check `alembic heads` shows one head.
- Adebayo likes plain words: purpose first, exact steps, an offer to do it for him. When he asks "where are we", answer from live commands, not memory.

## 0. Mode and standing rules

- **Merge only on his explicit word** ("Merge #N", typed in this chat). Before every merge the active-meetings count must be 0 (he runs it, or has said there are no meetings that day); every merge to `main` deploys (`ci`, then `deploy`) and never while a meeting is `bot_sent` or `processing`. After a deploy verify server head, `docker compose ps`, `/health` 200.
- **Deploy gate:** read the PR body "Server steps before merge" and have him apply them before (or right after, when the PR says so) the merge.
- Lead coordinates; sub-sessions work in worktrees, open PRs, never merge; at most 3 running; Sonnet implements; Opus reviews anything touching auth, tokens, sessions, deletion, mail, permissions, money, bot placement and locking.
- **UI rule:** every screen follows `docs/ui/Beyric Note Taker redesign/` with FULL parity and nothing invented. **Read the whole design file, not just the latest diff** (the lead got this wrong on 2026-11-05: the meeting page's Details, Participants, Action items, Open questions and the recurring page ARE drawn). **Exception (2026-11-05):** where the designer drew nothing and Adebayo says "do it ourselves", build from the file's existing patterns only and label it "lead-designed, not in the designer's file" in the SOP and PR (done: disconnect banner and email, meeting delete button). Ask him before inventing anything else.
- No AI attribution anywhere in git or GitHub; no `claude/` branch on the remote (push `HEAD:<feature|fix|docs|chore|ci>/<slug>`); SOP, checklist and real-capture FE guide with every shipment; fast checks locally, CI runs the rest.

## 1. Current state (2026-11-05, about 17:00 WAT)

| Thing | State |
|---|---|
| Live app | https://notes.beyrictech.com healthy. Server `haven-respite`, app at `/opt/beyric-note-taker`. Server head moves with each merge: check it. Alembic head **0024** once #90's deploy finishes |
| Merged and deployed 2026-11-05 | #80 designer update; #82 Decisions/Review states + stored time zone (0018); #81 meeting page parity part 1 (duration, Participants, Decisions-and-review card); #83 extraction output cap; #84 **W0.4 team foundation** (0019); #85 **C13 + C14 backend** connection states, skipped joins, bell, admin alert (0020); #87 disconnect banner and email (lead-designed); #86 **W0.5 meeting delete** API (0021); #89 delete button (lead-designed); #88 notes and labelling calls in the cost ledger (0023); #90 **W2 Tasks backend** (0024) |
| **Open PRs** | **#92** W2 Tasks screens (board, drawer, meeting task rows, Settings switches). Replaces #91. Merge on his word once CI is green |
| **His step after #90 deploys** | Run once (idempotent, mails nobody): `ssh haven-respite 'cd /opt/beyric-note-taker && docker compose exec -T web python scripts/backfill_extraction.py </dev/null'` |
| Running sessions | None building. Finished: `meeting-page-parity`, `states-and-timezone`, `w04-team-foundation`, `w2-extraction-cap`, `c13-c14-calendar-health`, `w05-meeting-delete`, `w2-tasks`. Archive them on his word; remove old worktrees with `mcp__ccd_host__clean_up_worktrees` |
| Migration numbers | main at 0024 (0022 was never used). Next free **0025** |
| Auto-join | Live. His rule: **All meetings with a video link**. First real auto-joined meeting expected **2026-11-06** |
| Server `.env` | Unchanged today (no new required variables). Optional: `EXTRACT_MAX_OUTPUT_TOKENS` (default 32000, #83) |
| Vexa | v0.12.29; rollback `VEXA_IMAGE_TAG=v012`. Bot joins as a guest and waits to be admitted |
| CI | `ci.yml`: changes, lint, test 1/4..4/4, web, ci summary; docs-only PRs skip code jobs |

## 2. Decisions taken 2026-11-05 (older ones: git history of this file and the memory files)

| Decision | Why |
|---|---|
| Keep going in the recommended order: W0.4, W0.5, W2, then W3, W4, W6, W7 done, W8, W9, W10 | Data dependencies: Team (W3) needs tasks; Projects (W4) need tasks and team; Series (W8) needs W2-W4; Sprints (W9) need tasks and projects |
| **W2 gate passed** on his real meetings 5 and 3 (17 items, all RIGHT, none missed); stay on `gpt-6-luna` | Only two real transcripts exist; he gave the go ("100% correct"). Re-check on new auto-joined meetings |
| Build C13 + C14 without designer art, then **design the banner and email ourselves**; same for the **meeting delete button** | He asked ("can we do this ourselves? proceed"; "add it") |
| Hours stay counted after a meeting delete; AI cost rows (`ai_runs`) are kept, unlinked | He agreed ("good"): otherwise the hours cap is gameable |
| Track notes and speaker-labelling calls in the cost ledger | He asked what the AI costs; the ledger missed the biggest call (#88) |
| Privacy/terms "Last updated" date not bumped by #90 | Bumping it changes `signup.TERMS_VERSION`; policy changes carry no notice (his rule) |

## 3. Designs, specs, plans

- AI workspace spec `docs/superpowers/specs/2026-11-03-ai-workspace-design.md` (phases W0.1 to W10 in section 15); pilot spec `docs/superpowers/specs/2026-11-03-pilot-accounts-calendar-design.md`; plan `docs/superpowers/plans/2026-11-03-pilot-accounts-calendar.md`; Outlook spec `docs/superpowers/specs/2026-11-04-outlook-oauth-design.md`.
- UI truth `docs/ui/Beyric Note Taker redesign/`; parity shots `docs/ui/parity/`.
- FE guides `docs/fe-integration-guide-*.md` (new today: settings-time-zone, meeting-delete, tasks), captures in `docs/fe-captures/`.

## 4. What is covered

See the checklist status line. Built: modules 0 to 2b, accounts, Phase C to C14 (backend and lead-designed banner/email), C15a, W0.2 to W0.5, W1, W2 backend (screens in #92), W5, W7, time zone, notes cost ledger. **Not built:** recurring meeting page and meeting-page parts waiting for W3/W4/W8/W9 (see `docs/sop/2026-11-05-meeting-page-parity.md` and `docs/sop/2026-11-05-w2-tasks-ui.md`), C15b to C18, ICS C19 to C24, W3, W4, W6, W8, W9, W10, phase O, pre-sign-up PR 3, Phases I, D, V.

## 5. Needs Adebayo

- **Merge #92** (W2 screens) when green; then **run the backfill command** in section 1 (after #90's deploy) and look at the Tasks page and a meeting page (the screens were only checked against mock data).
- **2026-11-06, first auto-joined meeting:** check the meeting row, notes, mail, tasks, any "Not recorded" notice with him. Optional real test of the disconnect banner/email: remove the app at myaccount.google.com/permissions, wait one sync (5 min), then reconnect.
- **AI cost:** after a few meetings, rerun the cost query (section 7) to see notes and label rows. Transcription cost is not tracked yet (follow-up).
- **Outlook:** which work/school account owns the tenant; Partner Center verification for Beyrichq LTD; a spare Outlook.com account.
- **GitGuardian:** mark the old findings false positives (ids in git history of this file, PR #79 version).
- **For the designer:** confirm or redraw the lead-designed disconnect banner, disconnect email and meeting delete confirm; "Share with team" switch; task count line in the delete confirm (follow-up below); the rest of the earlier gap list (see SOPs).
- Older items still open: "Forgot password" real test, Android share-target test, backup cron (privacy promises 14-day deletion), keep a copy of `TOKEN_ENC_KEY`, Cloudflare proxy, hosting country/DPO/lawyer read, mailboxes `privacy@`/`legal@`/`security@`, Search Console TXT, a Teams meeting and a Workspace account for spikes.

## 6. Incoming work, in order

1. **Merge #92**, backfill, check Tasks with him.
2. **Small follow-ups:** delete confirmation names the task count now that tasks exist; transcription minutes in the cost ledger; record a failed notes step's tokens (optional).
3. **W3 Team** (Opus: permissions, invites, slot reservation): invites, roles, aliases, shared meetings (the "Share with team" switch is a designer gap: ask him whether to lead-design it), matching owners against real members, Team screen. Unblocks teammate chips on Tasks.
4. **W4 Projects and milestones**, then **W6 Share** (Opus), **W8 Series** (recurring page, carry-over, agenda, reminders; Opus on mail), **W9 Sprints**, **W10** integrations last.
5. **Phase C remainder:** C15b send-now and not-recorded routes, C16 deploy gate script, C17 close-out, C18 recording notice; ICS C19 to C24.
6. **Outlook build** (phase O, ships dark), pre-sign-up PR 3, Phase D, I, V.

## 7. How we work here

- Branch from `main`, PR, CI green, merge on his word with `gh pr merge N --squash --delete-branch` (retarget stacked PRs first). After a merge verify, each as its own command:
  - `ssh haven-respite 'cd /opt/beyric-note-taker && git rev-parse --short HEAD && docker compose ps --format "{{.Service}} {{.Status}}"' </dev/null`
  - `curl -s -o /dev/null -w "%{http_code}" https://notes.beyrictech.com/health`
- **Active-meetings count (he runs it):**
  `ssh haven-respite 'cd /opt/beyric-note-taker && docker compose exec -T db sh -c "psql -U \$POSTGRES_USER -d \$POSTGRES_DB -t"' <<< "select count(*) from meetings where status in ('bot_sent','processing');"`
- **AI cost (he runs it; prices per million tokens):**
  `ssh haven-respite 'cd /opt/beyric-note-taker && docker compose exec -T db sh -c "psql -U \$POSTGRES_USER -d \$POSTGRES_DB"' <<< "select kind, model, count(*) as calls, sum(input_tokens) as tokens_in, sum(output_tokens) as tokens_out, round(sum(coalesce(input_tokens,0))*0.10/1e6 + sum(coalesce(output_tokens,0))*0.50/1e6, 4) as usd from ai_runs group by kind, model order by usd desc;"`
- **Extraction review (he runs it):** `--list 40` to find ids, then `--meeting <id> ...` (see `docs/sop/2026-11-04-extraction-review-tool.md`).
- Briefs to sub-sessions: start with `git fetch origin && git merge --ff-only origin/main` (`git reset --hard` is refused); push `HEAD:<prefix>/<slug>`; no AI attribution; do not merge; "Server steps before merge"; own Postgres container port (54323 to 54329 used; next **54330**); the UI rule; Opus reviews in the **foreground** (a backgrounded review left a session idle); poll CI in the same turn; report in about 12 lines; if the main `.venv` lacks a package, use a worktree venv (`.claude/worktrees/c12-join-runner-a5d31b/.venv` works).
- Python checks: `.venv/bin/ruff check .`, `.venv/bin/ruff format --check .`, pytest for touched files (test DB via `TEST_DATABASE_URL=postgresql+psycopg://postgres:postgres@localhost:<port>/notetaker_test`); web: `npm run lint`, `npm run typecheck`, `npx vitest run` in `web/`.
- Conflict churn: `app/api.py`, `app/services.py`, the meeting page, `web/tests/helpers.ts`, the checklist, `docs/openapi.json` and `web/lib/api/schema.d.ts` (regenerate with `scripts/export_openapi.py` and `npm run gen:api`).
- Lessons of 2026-11-05: date-dependent tests break when the day changes (`tests/test_alerts.py` used `NOW + 1 day`); a test calling an internal function breaks when its signature changes (grep `tests/` for direct calls); test engines in migration tests must be disposed or the scratch DB cannot be dropped.
