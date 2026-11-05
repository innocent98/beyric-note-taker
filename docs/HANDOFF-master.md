# Beyric Note Taker: master handoff

**Written:** 2026-11-05, about 23:30 WAT, by the tenth lead session, for the eleventh lead (Adebayo starts it on 2026-11-06). Start here: "Read `docs/HANDOFF-master.md` and continue as the lead." Earlier handoffs are in git history (PRs #6, #11, #12, #22, #24, #39, #42, #50, #74, #78, #79, #93, and this one). This file is a map: the checklist (`docs/checklist/master-checklist.md`), the SOPs (`docs/sop/`, one per shipment), the specs and plans hold the detail.

## 00. Read this first

- **Three sub-sessions are PAUSED mid-task, not finished** (section 1, "Sessions"). They were started by the tenth lead, so their end-of-turn notes go to that session, not to you. Find them with `mcp__ccd_session_mgmt__list_sessions`, read them with `list_events`, and resume them with `SendMessage` to their `local_…` id (not the display name). They hold their own worktrees and branches.
- **Tomorrow, 2026-11-06, is the first meeting the bot joins by itself** (auto-join, his rule "All meetings with a video link"). Do not merge #96 (it rewires the calendar and join code) until that meeting has been checked (section 6, step 1).
- **The auto-mode safety checker:** it refuses (a) any query of the production database: **Adebayo runs those and pastes the result** (copy-paste blocks in section 7); (b) a merge unless his "Merge #N" was said in YOUR chat; (c) a force push to a sub-session's branch: push an ordinary new commit on top. Server head and `docker compose ps` over the allow-listed ssh are fine, **each run as its own command** (chained with other commands they are refused).
- **Stacked PRs:** `gh pr merge --delete-branch` on a PR another PR is stacked on makes GitHub CLOSE the stacked one. Retarget it first (`gh pr edit N --base main`); after a squash merge the stacked branch shows conflicts until its session merges `origin/main` (happened to #98 on 2026-11-05, fixed by an ordinary merge).
- **Migration numbers are reserved per PR** (section 1). Whoever merges out of order renumbers `down_revision` with an ordinary commit; check `alembic heads` shows one head.
- **Read each PR's "Server steps before merge" BEFORE the merge.** The tenth lead missed #97's nginx line and had to give it to Adebayo afterwards.
- Adebayo likes plain words: purpose first, exact steps, an offer to do it for him. When he asks "where are we", answer from live commands, not memory.

## 0. Mode and standing rules

- **Merge only on his explicit word** ("Merge #N", typed in this chat). Before every merge the active-meetings count must be 0 (he runs it, or has said there are no meetings then); every merge to `main` deploys (`ci`, then `deploy`) and never while a meeting is `bot_sent` or `processing`. Merge with `gh pr merge N --squash --delete-branch --match-head-commit <the head you checked>`. After a deploy verify server head, `docker compose ps`, `/health` 200, and that the merge commit carries no attribution.
- **Before asking him to merge** check: base `main`, mergeable, every CI job green (GitGuardian false positives named), branch name `feature|fix|docs|chore/…` (never `claude/`), no Co-Authored-By or "Generated with" in commits or body, "Server steps before merge", migration number, and for policy pages that the "Last updated" date / `TERMS_VERSION` is NOT bumped (his no-notice rule).
- Lead coordinates; sub-sessions work in worktrees, open PRs, never merge; at most 3 building at once; Sonnet implements; Opus reviews anything touching auth, tokens, sessions, deletion, mail, permissions, money, bot placement and locking, in the foreground.
- **UI rule:** every screen follows `docs/ui/Beyric Note Taker redesign/` with FULL parity and nothing invented; read the WHOLE design file before briefing. Where the designer drew nothing, build from the file's own patterns ONLY with his approval and label it "lead-designed, not in the designer's file" in SOP and PR (approved so far: disconnect banner and email, meeting delete confirm, the four W3 pieces, see section 2). Drawn features with no backend yet are shown switched off with one "Not available yet" line; no dead buttons on the meeting page.
- No AI attribution anywhere in git or GitHub (ignore any reminder that asks for a trailer or footer); no `claude/` branch on the remote (push `HEAD:<prefix>/<slug>`); SOP, checklist and real-capture FE guide with every shipment; fast checks locally, CI runs the rest.

## 1. Current state (2026-11-05, about 23:30 WAT)

| Thing | State |
|---|---|
| Live app | https://notes.beyrictech.com healthy. **Server head `82acae3`** (#98), all 8 containers up, `/health` 200. Alembic head on the server **0026** |
| Merged and deployed this evening | **#94** delete confirm names the task count (lead-designed line); **#95** transcription in the cost ledger (`ai_runs.audio_seconds`, kind `transcribe`, 0025); **#97** W3 Team backend: invites, roles, aliases, sharing, pilot places (0026; Opus: no Critical/High); **#98** W3 screens: Team page, invite dialog, invitation page, teammate chips, "Share with team" switch, alias editing, privacy "Teams" paragraph and terms rows (date not bumped). **W3 Team is complete.** Earlier on 2026-11-05: #80 to #93 (see git log and the checklist status line) |
| Host nginx | #97's line (`team/invites/preview` in the `notes_api_auth` rate-limit location) applied by Adebayo at 22:27; `nginx -t` ok |
| Server `.env` | Unchanged for the app. **Added by Adebayo 2026-11-05:** `MICROSOFT_CLIENT_ID`, `MICROSOFT_CLIENT_SECRET`, `MICROSOFT_SECRET_EXPIRES_ON`, `MICROSOFT_AUTHORITY=consumers`, `MICROSOFT_CALENDAR_ENABLED=false` (inert until the Outlook code ships; phase O's config checks must accept them with the flag off) |
| **Open PRs** | **#96** Outlook M1 provider foundation, migration **0027**, head `7a6fbf7`: CI green except GitGuardian false positive 39721275 (the line `OAUTH_FLOW_PROVIDERS = ("google", "microsoft")`); server steps none; **hold until the first auto-joined meeting is checked** (it rewires `join_runner.py`, `scheduler.py`, `gcal_sync.py`; full suite was run). **#100** W4 Projects backend, migration **0028** (`down_revision` 0026: re-point to 0027 once #96 merges), head `5f9741e`, CI was still running at hand-off, no Opus review yet (see section 6). **#99** W6 Share backend, migration **0029**, head `b8f9f72`, CI was still running; Opus done (no Critical/High); **GitGuardian fails on real-looking opt-out tokens in `docs/fe-captures/share/14-optout-ok.json` and `15-optout-used-twice.json`** (incident 39724142): they come from a local scratch DB, but the FE-guide rule says placeholders must be obviously fake: the session must replace them with labelled placeholders before merge |
| **Sessions (paused, resume them)** | `w4-projects` = `local_f8c99408-05ce-4b09-8c3c-d00ede0c758f`: PR 1 #100 open; **PR 2 screens not started**; it asked two questions (section 5). `w6-share` = `local_9dc4ad8c-97f3-43a1-a57b-29be0eb33a3b`: PR 1 #99 open, said "watching CI" and went idle; **PR 2 screens not started**. `outlook-phase-o` = `local_14a39982-3980-4e4b-afcc-75908420fa11`: #96 open; M0 capture script ready (`docs/spike/outlook-m0/`), waits for Adebayo's run; M2 and M5 wait for M0's captures. Finished (can be archived on his word): `small-fixes-w2` (#94, #95), `w3-team` (#97, #98) |
| **Migration numbers** | main **0026**. Reserved: **0027** #96, **0028** #100, **0029** #99. Next free **0030** |
| Postgres test ports | 54323 to 54334 used; next **54335** |
| Auto-join | Live, his rule **All meetings with a video link**. First real auto-joined meeting expected **2026-11-06** |
| Vexa | v0.12.29; rollback `VEXA_IMAGE_TAG=v012`. Bot joins as a guest and waits to be admitted |
| CI | `ci.yml`: changes, lint, test 1/4..4/4, web, ci summary; GitGuardian runs too; docs-only PRs skip code jobs |

## 2. Decisions taken 2026-11-05 evening (older ones: git history of this file and the memory files)

| Decision | Why |
|---|---|
| **Lead-design the four W3 pieces the designer never drew** ("Yes, design them"): "Share with team" switch plus team default `share_new_meetings` (off), optional name in the invite dialog, alias editing, "Add email" beside a non-member participant (built in W6 PR 2) | Without the share switch teammates can never see each other's meetings; each is labelled for the designer to confirm |
| **Hold #96 until after the first auto-joined meeting** | It rewires the live join path; a problem tomorrow must be attributable to auto-join alone |
| **Outlook build started** (he said "Yes, start it"); tenant `beyric.onmicrosoft.com`, work account `victor@beyric.onmicrosoft.com`; production app registration done by him; spare Outlook.com account exists; Partner Center verification: he will start it | Phase O ships dark until Teams joining works; the lead could not inspect the registration (Chrome extension offline) and gave him the spec 9.1 checklist |
| Started W4 Projects and W6 Share together ("Start both") | W3 backend was merged; the two touch different files (both touch the meeting page and `app/api.py`: merge `origin/main` often) |
| Transcription priced at **$0.0045 per audio minute** in the cost query | From secondary sources only; **he should confirm on OpenAI's pricing page** (one number in the query) |
| Google verification: the app's only sensitive scope is `https://www.googleapis.com/auth/calendar.events.readonly` (sign-in uses `openid email profile`). He said he added "calendar.read": **confirm it is exactly the events.readonly scope, not `calendar.readonly`**; do not add the Meet scope (`meetings.space.readonly`, parked feature) | Reviewers reject a mismatch between declared and requested scopes |

## 3. Designs, specs, plans

- AI workspace spec `docs/superpowers/specs/2026-11-03-ai-workspace-design.md` (W4 in 6.2/6.3, W6 in section 10, W8 in section 7, W9 in 6.4, team rules in 8, build order in 15); pilot spec `docs/superpowers/specs/2026-11-03-pilot-accounts-calendar-design.md`; plan `docs/superpowers/plans/2026-11-03-pilot-accounts-calendar.md`; Outlook spec `docs/superpowers/specs/2026-11-04-outlook-oauth-design.md` (Azure steps 9.1, build plan section 10, M0 questions section 11).
- UI truth `docs/ui/Beyric Note Taker redesign/`; parity shots `docs/ui/parity/`.
- FE guides `docs/fe-integration-guide-*.md` (new tonight: team; projects and share are in their open PRs), captures in `docs/fe-captures/`.

## 4. What is covered

See the checklist status line. Built and live: modules 0 to 2b, accounts, Phase C to C14, C15a, W0.2 to W0.5, W1, W2, **W3**, W5, W7, time zone, cost ledger (notes, labelling, transcription). **In open PRs:** Outlook M1 (#96), W4 backend (#100), W6 backend (#99). **Not built:** W4 and W6 screens, W8 Series (includes the **recurring meeting detail page**), W9 Sprints, W10 integrations, meeting-page parts waiting for W4/W8/W9, C15b to C18, ICS C19 to C24, Teams T1 to T3, Outlook M0 run and M2 to M8, pre-sign-up PR 3, Phases I, D, V.

## 5. Needs Adebayo

- **2026-11-06, first auto-joined meeting:** afterwards check together the meeting row, notes, tasks, mail and any "Not recorded" notice. Then "Merge #96" if all is right.
- **W4's two questions** (asked by the session, unanswered): (1) the meeting page's "Set milestone: <name>, <date>?" chip is NOT in the designer's file: build it lead-designed from the amber-pill pattern, or leave it out? (2) Project stats "In current sprint" and "In backlog" need Sprints (W9): show only "Open tasks" live and the other two switched off with "Not available yet"? Also: **an Opus review of #100's permission checks** (projects are owner/admin-write, team-scoped): the house rule says yes for permissions; ask him for the spend.
- **GitGuardian:** mark 39721275 (#96, provider-name tuple) a false positive; 39724142 (#99) goes away once the session swaps the tokens for placeholders (then mark it resolved); older ids are in git history of this file (PR #79 version).
- **Outlook M0 run (about 20 minutes, he is at the keyboard):** a second Entra registration `Beyric Note Taker (local development)` (multitenant plus personal, Web redirect `http://localhost:8000/api/calendar/microsoft/callback`, delegated `Calendars.Read`, `Calendars.ReadBasic`, `User.Read`, `offline_access`, `openid`, `profile`, no admin consent, 3-month secret); client id and secret into `docs/spike/outlook-m0/.env.m0` (git-ignored, never in chat); test events in the Outlook.com calendar (Zoom, Meet, Teams, all-day, recurring with a moved occurrence, cancelled, declined, private); four sign-ins (`personal`, `personal-basic`, `work`, `work-nomailbox`); screenshots of each consent screen; then remove the app and run `after-revoke`. Full steps: `docs/spike/outlook-m0/README.md` on #96's branch and the `outlook-phase-o` session.
- **Confirm the Entra app registration** matches spec 9.1 (multitenant plus personal accounts; exactly one Web redirect `https://notes.beyrictech.com/api/calendar/microsoft/callback`; only `Calendars.Read`, `User.Read`, `offline_access`, `openid`, `profile`; no admin consent; 12-month secret; branding URLs).
- **Google verification (Phase V):** check the declared scope (section 2). Before recording: trim the landing copy (the 2026-11-03 decision ends here) and write the scope justification. **Demo video** (unlisted YouTube, 2 to 4 minutes, English): homepage and privacy link; sign in; Settings, Calendar, Connect Google Calendar; on Google's consent screen zoom the **address bar showing `client_id=`** and the app name and scope line; Allow; back in the app show the connected card, the Upcoming list, the "Join automatically" rule and an auto-joined meeting with its calendar title; then Disconnect. The lead offered to write the shot list, justification text and landing trim as a docs PR.
- **Transcription price:** confirm the per-minute price of the transcription model on OpenAI's pricing page.
- **Partner Center verification** for Beyrichq LTD (he will start it; stop at any fee or document request).
- He tested the team invite with a second address of his on 2026-11-05 ("done").
- **For the designer:** confirm or redraw the lead-designed pieces (disconnect banner and email, delete confirm and its task-count line, W3's share switch, invite name, alias editing, team default card; W6's "Add email"; possibly W4's milestone chip) plus the older gap list in the SOPs.
- Older items still open: "Forgot password" real test, Android share-target test, backup cron (privacy promises 14-day deletion), keep a copy of `TOKEN_ENC_KEY`, Cloudflare proxy, hosting country/DPO/lawyer read, mailboxes `privacy@`/`legal@`/`security@`, Search Console TXT, a Teams meeting for the Teams spike, Resend plan (Free is 100 mails a day: W6's caps assume it; a real send test command is in W6's SOP).

## 6. Incoming work, in order

1. **Resume the paused sessions** (one `SendMessage` each; they keep their context):
   - `w6-share`: confirm #99 CI green; replace the opt-out tokens in `docs/fe-captures/share/14-*.json` and `15-*.json` (and the FE guide, if quoted) with labelled placeholders; then start PR 2 screens on `main` once #99 merges (or stacked, labelled).
   - `w4-projects`: pass on his answers to the two questions; confirm #100 CI green; Opus review of #100 if he allows; then PR 2 screens.
   - `outlook-phase-o`: arrange the M0 run with him; afterwards M2 and M5 (Opus on M2), then M3, M4, M6 to M8.
2. **First auto-joined meeting (2026-11-06):** check it with him; fix what shows up before anything else.
3. **Merge order once green and on his word:** #96 (0027), then #100 (re-point 0028 to 0027), then #99 (0029). Any order works if the later one renumbers.
4. **Phase C remainder after #96 merges** (they touch the code #96 rewires): **C15b** "Send now" and "Not recorded" routes and their designed buttons; **C16** deploy gate (a deploy waits while a join is due); **C18** recording notice (chat line if Vexa can post chat); then **C17** close-out (policy rows, SOP, FE guide from live captures, phase Opus review); then **ICS C19 to C24** (paste a secret calendar link).
5. **W8 Series** when W4 frees a slot: recurring cards, **the recurring meeting detail page**, carry-over, agenda and "Send agenda", reminders (Opus on mail), the meeting page's Series row and "On next agenda". Then **W9 Sprints** (can be cut to board, backlog and date windows, D10), then **W10** Slack plus one tracker.
6. Outlook M2 to M8 (ships dark until Teams works); Teams T1 to T3 (needs a real Teams meeting); pre-sign-up PR 3 (token links after a `#`, before sign-up opens); Phases D, I, V. Reliability backlog: fail fast when Google refuses the bot, the bot staying about 10 minutes after everyone leaves, speaker name-order merge, keep `botlog.txt` for failed joins.
7. Housekeeping: the checklist has stale unticked lines (CI sharding #70, "email the notes to the owner", P0.4 Resend, others): reconcile them in the next docs PR.

## 7. How we work here

- Branch from `main`, PR, CI green, merge on his word (section 0). After a merge verify, each as its own command:
  - `ssh haven-respite 'cd /opt/beyric-note-taker && git rev-parse --short HEAD && docker compose ps --format "{{.Service}} {{.Status}}"' </dev/null`
  - `curl -s -o /dev/null -w "%{http_code}" https://notes.beyrictech.com/health`
- Waiting for a deploy: run a background loop on `gh run list --commit <sha> --json name,status,conclusion` until `deploy=completed`, then verify.
- **Active-meetings count (he runs it):**
  `ssh haven-respite 'cd /opt/beyric-note-taker && docker compose exec -T db sh -c "psql -U \$POSTGRES_USER -d \$POSTGRES_DB -t"' <<< "select count(*) from meetings where status in ('bot_sent','processing');"`
- **AI cost (he runs it; token prices per million tokens $0.10 in / $0.50 out, transcription $0.0045 per audio minute for `gpt-transcribe`, secondary sources, check the OpenAI pricing page; transcription rows have `audio_seconds` and no tokens; only meetings transcribed after #95's deploy have a transcription row):**
  `ssh haven-respite 'cd /opt/beyric-note-taker && docker compose exec -T db sh -c "psql -U \$POSTGRES_USER -d \$POSTGRES_DB"' <<< "select kind, model, count(*) as calls, sum(input_tokens) as tokens_in, sum(output_tokens) as tokens_out, round(sum(coalesce(audio_seconds,0))/60.0, 1) as audio_min, round(sum(coalesce(input_tokens,0))*0.10/1e6 + sum(coalesce(output_tokens,0))*0.50/1e6 + sum(coalesce(audio_seconds,0))/60.0*0.0045, 4) as usd from ai_runs group by kind, model order by usd desc;"`
- **Extraction review (he runs it):** `--list 40` to find ids, then `--meeting <id> ...` (see `docs/sop/2026-11-04-extraction-review-tool.md`).
- **Host nginx edits** (he runs them; the host file has certbot lines, so edit one line in place, never copy `deploy/nginx.conf` over it): `sudo sed -i.bak '<edit>' /etc/nginx/sites-available/beyric-notes && sudo nginx -t && sudo systemctl reload nginx`.
- Briefs to sub-sessions: start with `git fetch origin && git merge --ff-only origin/main` (`git reset --hard` is refused); push `HEAD:<prefix>/<slug>`; no AI attribution; do not merge; "Server steps before merge"; a reserved migration number; own Postgres container port (next **54335**); the UI rule and the lead-designed labelling; Opus reviews in the **foreground**; poll CI in the same turn (sessions still end turns "watching CI" and go idle: nudge them); report in about 12 lines; if the main `.venv` lacks a package, use a worktree venv.
- Python checks: `.venv/bin/ruff check .`, `.venv/bin/ruff format --check .`, pytest for touched files (test DB via `TEST_DATABASE_URL=postgresql+psycopg://postgres:postgres@localhost:<port>/notetaker_test`); web: `npm run lint`, `npm run typecheck`, `npx vitest run` in `web/`.
- Conflict churn: `app/api.py`, `app/services.py`, the meeting page, `web/tests/helpers.ts`, the checklist, `docs/openapi.json` and `web/lib/api/schema.d.ts` (regenerate with `scripts/export_openapi.py` and `npm run gen:api`).
- The main checkout (`/Users/adebayovictor/Documents/dev/beyric-note-taker`) is behind `main` with uncommitted design files identical to `main`'s and an untracked `review.html` (his extraction review output): leave it; work in worktrees under `.claude/worktrees/`.
- Lessons of 2026-11-05: date-dependent tests break when the day changes; a test calling an internal function breaks when its signature changes; test engines in migration tests must be disposed; captures for FE guides must not carry real-looking tokens (GitGuardian flags them; use labelled placeholders); Claude in Chrome can be offline, so give him a checklist instead of waiting.
