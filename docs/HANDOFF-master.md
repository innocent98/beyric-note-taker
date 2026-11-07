# Beyric Note Taker: master handoff

**Written:** 2026-11-07, about 12:00 WAT (updated 14:10 WAT by the thirteenth lead: see the Update block below), by the twelfth lead session (a second handoff in this session, after the first at 23:50 WAT on 2026-11-06), for the thirteenth lead (the twelfth starts it itself and then caps; Adebayo may also start it). Start here: "Read `docs/HANDOFF-master.md` and continue as the lead." Earlier handoffs are in git history (PRs #6, #11, #12, #22, #24, #39, #42, #50, #74, #78, #79, #93, #101, #110, #112, and this one). This file is a map: the checklist (`docs/checklist/master-checklist.md`), the SOPs (`docs/sop/`, one per shipment), the specs and plans hold the detail.

## Update 2026-11-07, 14:10 WAT (thirteenth lead): read this first, it supersedes the state in sections 00 to 6 where they differ

**Live:** server head **`f9ed40b`** (#117), 8 containers up, `/health` 200 (verified after each merge). Alembic head still **0033** (nothing today added a migration; next free **0034**). Merged today, each on Adebayo's word and a stated meeting window ("No meetings, 1 hour" at 13:35 WAT): #113 docs, #114 vendor research (second-hand Skribby notes plus the lead's raw-page read), #115 UI batch 2 PR A (side drawer, sidebar identity, wording, Outlook row off), #116 PR B (setup, join your team, sign-in errors, landing buttons), #118 bake-off tool, #117 mails parity (Opus-reviewed; two Medium findings fixed by sending the plain alert again). Squash messages were written by the lead (no attribution).

**Open PRs / sessions:** #119 `ci/apt-cache-key-image` (CI only, see below), awaiting CI then his "Merge #119". Running session `mails-reminder-summary-b` = `local_e0a2d45b-3e15-43d6-a2ec-d0211a88d1f6` (summary box and "Sprint N opens" in the 1-hour reminder; rules approved by him; branch `feature/mails-summary-box`; Opus review of its visibility logic needs his OK). Done and idle: `ui-parity-batch-2`, `mails-parity`, `bot-bakeoff-tool`.

**Decisions today (Adebayo):** yes to the bot **bake-off** (guest and signed-in arms, one bot per meeting, free tiers); yes to recordings leaving our server for the bake-off; yes to the five summary-box and Sprint-line rules (14 days; 1-hour mail only; auto-join sentence only for people whose bot will join); Opus review of the mails PR allowed (done); "merge all" with a one-hour window. The "Send it again" sentence stays without its button until C15b (his question "what does it do?" was answered; no objection).

**Bot account:** `notes@beyrictech.com` is a Workspace ADMIN created by his unpaid Workspace sign-up (payment wall), so `beyrictech.com` is attached to an unpaid Workspace organisation; do NOT use it as the bot, do not pay or delete anything unless he decides. Plan: a plain free Gmail named "Beyric Note Taker (recording)", 2-step with an authenticator, setup key typed by him only into Skribby's dashboard. The tool needs only Skribby's API key and account id and Recall's key and region, in `~/.config/beyric-bakeoff/keys.env` on his Mac (never in chat). Steps: `tools/bakeoff/README.md`; SOP `docs/sop/2026-11-07-bot-bakeoff-tool.md`. **Not done:** his sign-ups, the Gmail account, the first live check; nothing has run against a live vendor. Verified on raw pages (2026-11-07, copies in `docs/spike/primary-pages-2026-11-07/`): Skribby Free plan 5 bot hours a month and 3 concurrent bots; Recall first 5 hours free; Skribby bots join as guests first unless `always_authenticate`. Not stated anywhere: whether Skribby's Free plan allows authenticated accounts; whether a Google account on a custom address works.

**CI regression found and worked around:** about 12:44 UTC `ubuntu-latest` moved to a newer Ubuntu; the cached ffmpeg/Pango .debs (key without the image version because `env.ImageVersion` is empty in expressions) failed to install and every Python test shard failed before any test. The lead deleted the stale cache entry (`gh cache delete 10011029062`) and re-ran; #119 fixes the key properly (SOP `docs/sop/2026-11-07-ci-apt-cache-key.md`). If a CI job fails at `apt-get` or `dpkg`, it is this, not the code.

**Needs Adebayo:**
- Merge #119 when green.
- Bake-off sign-ups and the first live check (above).
- **Decision: send the designer's "Could not join" mail when Google refuses the bot?** It is built and tested (`mail_templates.NOT_JOINED`) but sent by nothing: wiring it to the pipeline's not-let-in failure (`app/pipeline.py` about 545-554, `bot_failure_reason` awaiting_admission_rejected/timeout) is a new notification (new bell kind, per-meeting dedupe, also for manual sends) and needs his OK and an Opus review.
- Designer items: the new drawer and the Outlook row on the setup step (lead-designed), the three undrawn W9 pieces, "Join your team" now rebuilt to the file (terms checkbox kept because the API needs `accept_terms`, Google button hidden on that page).
- GitGuardian false positives: #96 and #100 to #111 (Microsoft GUID), and #118 (a fake `sk_live_` test value, removed in the tip; it stays in the branch history, the squash commit is clean).
- Unchanged from section 5: W9 backend choices, two Medium findings from W8's automated review, real Slack/Linear/Resend checks, the AES-256 landing line, Outlook M0 run, Google verification, older items.

**Incoming, in order:** (1) follow the summary-box session; (2) wiring "Could not join" on his decision; (3) the backend gaps from the audit (series project endpoint and the Projects "Recurring meetings" list, sprint line under "Next meeting", transcript turn times); (4) bake-off runs once he has keys, then the join-rate decision with his rule (a signed-in, invited bot near 95% where Vexa is well short is worth adopting, else stay on Vexa); (5) Phase C remainder (C15b first: it also turns on the "Send it again" button), then the rest of section 6; (6) housekeeping: parity shots reshoot, stale checklist lines, old worktrees.

**New lessons:** (a) a start of a sub-session is refused while the turn comes from another session's message or a background notification: propose it as a card or start it when he types; (b) sub-sessions report CI "not polled" because their app forbids polling: the lead reads `gh pr checks` itself and, for a red run, reads `gh run view <id> --log-failed` before blaming the code; (c) after every squash merge the next branches conflict on the checklist: ask the owning session to `git merge origin/main` (they did, twice); (d) write squash `--subject` and `--body` yourself; (e) Opus review found real issues in the join-failure path that the author's tests missed: keep doing it for mail, auth, locking.

## 00. Read this first

- **Every W phase (W0 to W10) is built and live** (merge-queue SOP `docs/sop/2026-11-06-merge-queue-w-phases.md`). Today's work (2026-11-07) was research and a UI audit; the open items are in section 6.
- **Open PRs when this was written:** **#113** (docs: UI audit, vendor and Recall research, checklist reconciliation, this handoff), waiting for Adebayo's "Merge #N" plus a clear meeting count. The UI session below will open one or two more.
- **Running session (started by the twelfth lead):** `ui-parity-batch-2` = `local_c723bc11-cea3-42bc-99ff-ab0bbb934d1e` (Sonnet; PR A: mobile and tablet side drawer, sidebar identity, wording, Outlook switched-off row; PR B: "Set up your workspace", "Join your team", sign-in errors, landing buttons; branches `feature/ui-parity-batch-2a` and `-2b`). Its end-of-turn notes go to the twelfth lead, not to you: **find it with `mcp__ccd_session_mgmt__get_session` / `list_events` and message it with `SendMessage` to that id**. Done: `w9-sprint-screens` (`local_6163e9c5-70b3-4b24-9726-403bcd40e412`). The earlier W-phase sessions are gone from this machine.
- **The auto-mode safety checker refuses:**
  - **any production read** (database queries, `docker compose logs`, the bot's log files): Adebayo runs these and pastes the result; for long output, give him a command that writes to a file in your scratchpad, then read the file;
  - **a merge**, unless his "Merge #N" was said in YOUR chat; his earlier "no meeting for N hours" window must still be open (the twelfth lead asked again when it had lapsed);
  - **a force push** to a sub-session's branch;
  - **a command that combines a local file read with the ssh line**: run each `ssh haven-respite '...'` and `curl .../health` as its own command.
- Adebayo likes plain words: purpose first, exact steps, an offer to do it for him. **He challenges where facts come from**: before telling him a vendor or price fact, read the primary page yourself and say which pages you read (sub-agent summaries and the fetch tool's extracts are second-hand). When he asks "where are we", answer from live commands, not memory.

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
| Live app | https://notes.beyrictech.com healthy. **Server head `4159b74`** (#112, docs), 8 containers up, `/health` 200 (verified 2026-11-06 23:43 WAT). Alembic head **0033** |
| Migration chain on `main` | 0026, 0027, 0028, 0031, 0029, 0030, 0032, 0033 (merged out of order, one head). Next free number **0034** |
| Open PRs | The docs PR (see section 00) and, soon, the UI session's PR A and PR B |
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

**Decisions and answers 2026-11-07** (Adebayo, in this chat):

| Decision or answer | Why |
|---|---|
| Audit every designed screen against the live app; then fix the quick items and merge the audit as docs ("Yes, audit", "Yes, both") | The checklist was stale; he asked whether any UI build is pending (answer: no whole screen missing, but items remain, see section 6) |
| **Mobile and tablet navigation becomes a professional side drawer** (hamburger, slide-over); today it is stacked at the top | His request. The designer drew desktop only, so this is **lead-designed**: label it so in the SOP. Inside the UI session's PR A |
| Pasted answer claiming Recall needs no Workspace was checked and corrected | Recall bills no seats, but a **signed-in** Meet bot needs a dedicated Google Workspace with Legacy SSO that we create (Recall guide, read 2026-11-07); the pasted text mixed the calendar OAuth with the bot's own sign-in. Details in `docs/spike/meeting-bot-vendors.md` ("Lead verification") |
| **Lead's recommendation on the bot (NOT yet his decision):** stay on Vexa as the engine for the pilot; make the bot layer swappable and add ONE hosted vendor as a second backend chosen by a one-week bake-off (Skribby free tier, Recall 5 free hours, guest first, then signed-in); move to hosted-first when any trigger fires: a vendor's measured join rate is materially better than ours, we need more than 2 concurrent bots (each Vexa bot uses about 1.4 cores on a shared VPS: Phase D, a bigger server), or server work keeps costing time | Cash is about equal at pilot size (50 recorded hours a month: self-hosted USD 22 to 40, hosted USD 25 to 41, plus the bot's Google account); hosting alone does not fix Google refusing anonymous guests; concurrency is where self-hosting gets costly. "Retry better" alone was rejected by him |
| Bot account without a paid Workspace: three routes, none yet tried | (a) Workspace on `beyrictech.com` (hello@ becomes a Workspace user; about EUR 6.80 per user a month; works for Recall's SSO). (b) A free Google Account created with `hello@beyrictech.com` as its sign-in address ("use my existing email"; the lead's general knowledge, NOT verified on a page): a consumer account, no seat cost; Skribby accepts typed Gmail/Workspace logins (ask Skribby that this kind works) and Vexa's signed-in mode would too once #1725 is fixed and we leave Lite; **Recall cannot use it** (needs Workspace SSO). (c) A plain Gmail. Risks for (b) and (c): Google's terms on automated use of a consumer account are unverified; sign-ins from a datacenter may be challenged |

## 3. Designs, specs, plans

- AI workspace spec `docs/superpowers/specs/2026-11-03-ai-workspace-design.md`; pilot spec `docs/superpowers/specs/2026-11-03-pilot-accounts-calendar-design.md`; plan `docs/superpowers/plans/2026-11-03-pilot-accounts-calendar.md`; Outlook spec `docs/superpowers/specs/2026-11-04-outlook-oauth-design.md`.
- Meet join research `docs/spike/meet-join-resilience.md` (signed-in bots, why the lobby happens) and the vendor comparison `docs/spike/meeting-bot-vendors.md`.
- UI truth `docs/ui/Beyric Note Taker redesign/`; parity shots `docs/ui/parity/`.
- FE guides `docs/fe-integration-guide-*.md` (real captures in `docs/fe-captures/`), including projects, share, series, sprints and integrations.

## 4. What is covered

Built and live: modules 0 to 2b, accounts, Phase C up to C14, C15a, **W0 to W10 all** (W4, W6, W8 with the recurring meeting detail page, W9 backend and screens, W10 Slack and Linear merged 2026-11-06), Outlook M1, time zone, the cost ledger, the waiting-room fix. **Not built:** W8's sprint lines (now unblocked), C15b to C18, ICS C19 to C24, Teams T1 to T3, Outlook M0 run and M2 to M8, pre-sign-up PR 3, Phases I, D, V; the bot-reliability decision.

## 5. Needs Adebayo

- **Merge the docs PR** (his "Merge #N", with a stated clear window: a merge deploys and restarts the worker).
- **The bot decision:** the lead's recommendation is in section 2; he has not said yes to the bake-off. It needs his sign-ups (Skribby, Recall; keys pasted or put on the server by him) and a choice of the bot's Google account (section 2, last row). Also: does he accept recordings leaving our server (a vendor becomes a sub-processor)?
- **Read the UI audit** (`docs/ui/audit-2026-11-07-app.md`, `...-pages-emails.md`) and the designer items: the three undrawn W9 pieces, every lead-designed piece (including the new side drawer) to confirm or redraw, and "Join your team" (the built page differs from the file).
- **GitGuardian:** mark 39721275 (#96) and the repeats on #100 to #111 as false positives (Microsoft's public consumer-tenant GUID; inferred).
- **W9's five backend choices** (still unconfirmed): Mon-Fri switch only affects "1 week, 5 working days"; a sprint closed early keeps its planned end date; the team zone is the owner's Settings zone; settings go through `PATCH /api/team`; existing tasks are not backfilled.
- **Two Medium findings from the automated security review of W8:** agenda recipient count; series visibility from calendar rows (checklist "Deferred follow-ups").
- **Real-service checks not done:** a real Slack webhook and a real Linear key (W10); a real Resend send for W6; W8 reminders stay off until switched on.
- **The landing line "at rest with AES-256"** conflicts with an SOP saying no disk encryption was set up: decide before Google verification.
- **Outlook M0 run** (about 20 minutes): `docs/spike/outlook-m0/README.md`; Partner Center verification for Beyrichq LTD.
- **Transcription price:** USD 0.0045 per audio minute is confirmed on OpenAI's pricing page (read by a research agent 2026-11-06).
- **Google verification (Phase V):** demo video, scope justification, landing trim.
- Older items: "Forgot password" real test, Android share-target test, backup cron (privacy promises 14-day deletion), keep a copy of `TOKEN_ENC_KEY`, Cloudflare proxy, hosting country/DPO/lawyer read, the mailboxes `privacy@`/`legal@`/`security@`, Search Console TXT, a Teams meeting for the Teams spike, the Resend plan (Free is 100 mails a day).

## 6. Incoming work, in order

1. **Follow the UI session `ui-parity-batch-2`** (section 00): check each PR (base `main`, CI green, no attribution, no `claude/` branch, the drawer labelled lead-designed, parity table and shots at 1440/768/390), then ask Adebayo for "Merge #N" and a clear window. Opus review only if he allows the spend (PR B touches auth screens: logic must be unchanged).
2. **Mails parity session** (not started; brief it, Sonnet, Opus review of mail on his OK): reminder mails (teal pill, uppercase small grey labels, "Sprint 13 opens" and grey summary box, "Read the full notes" in the 30-minute mail), verify and reset wording ("finish setting up", "This link expires in...", "your password will stay the same"), the notes-shared footer, and the **"Could not join" email** (`Emails.dc.html:137-149`; its "Send it again" button needs C15b). Audit rows are in `docs/ui/audit-2026-11-07-pages-emails.md`.
3. **Backend gaps found by the audit:** wire `filing.series_project()` and an endpoint that writes `Series.project_id`, then the Projects "Recurring meetings" list and "· M recurring meetings"; the sprint line under "Next meeting" (needs an API field and a rule); transcript turn times (not stored).
4. **Bot reliability:** on his yes, the bake-off script (a small local tool run from his Mac, nothing deployed; Skribby and Recall APIs; logs time, guest or signed-in, result, seconds to the lobby, reason). Facts you need are in `docs/spike/recall-api-reference-read.md` (create bot, statuses and Google Meet fatal codes, transcript JSON, BYO transcription providers, retention, chat message for the recording notice, `join_at` at least 10 minutes ahead), `docs/spike/hosted-pivot-gladia-recall-cost.md` (cost table, Gladia is speech-to-text only) and `docs/spike/meeting-bot-vendors.md`. Still true: check the next meetings someone else hosts and a longer lobby budget (`VEXA_LOBBY_BUDGET_S`).
5. **Phase C remainder:** C15b ("Send now" and "Not recorded"), C16 (deploy gate), C18 (recording notice; Recall's bot can post a Meet chat message), C17 (close-out with a phase Opus review), then ICS C19 to C24.
6. Outlook M2 to M8 (ships dark until Teams works); Teams T1 to T3; pre-sign-up PR 3; Phases D, I, V. Backlog: fail fast when Google refuses the bot, speaker name-order merge, keep `botlog.txt` for failed joins.
7. **Housekeeping:** older checklist sections still hold stale lines (Phase A, Phase C C1 to C4, W2, W0.3); old worktrees under `.claude/worktrees/` can go with `mcp__ccd_host__clean_up_worktrees` (the twelfth lead's `lead-audit` worktree is one); the parity shots need a reshoot after the W-phase merges.

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
  - **Copy research into the repo at once.** Scratchpad files (including two research reports) vanished during the session; one was recovered from the sub-agent's recorded transcript at `~/.claude/projects/<project>/<session>/subagents/agent-<id>.jsonl` (parse the `Write` tool_use inputs with Python; never print the transcript). The fetch tool returns a small model's extract of a page, not the raw page: say so.
  - **Lead worktrees can be cleaned up by the app after merges**: recreate with `git worktree add --detach .claude/worktrees/<name> origin/main`.
  - A combined `cat <file>; ssh ...` command is refused by the safety checker: one command, one purpose.
  - The designer's main file is committed (#80); the main checkout's modified copy was identical to `origin/main`. It has no `@media` rules: phone and tablet layouts are ours.

