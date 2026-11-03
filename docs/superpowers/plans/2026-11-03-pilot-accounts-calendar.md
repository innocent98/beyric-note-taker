# Pilot (accounts, calendar auto-join, instant-Meet, several bots, Google verification) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking. **One task = one session = one PR** (spikes: one note in `docs/spike/`). Read the spec's section 0a first: it overrides the older text.

**Goal:** Ten pilot users each own their meetings and calendar, and the note taker joins their Google Calendar meetings (and Outlook or other calendars through a pasted ICS link), the Meet calls they start, and (if the spike passes) Teams meetings, several at a time, with no unrecorded meeting caused by capacity.

**Architecture:** A new `scheduler` process (own container, leader-locked) syncs calendars and dispatches joins; a single function `place_bot()` is the only place a bot is sent, first against the one local Vexa and later against a registry of bot hosts. Google refresh tokens live encrypted (AES-GCM) in Postgres; the key lives only in the server `.env`.

**Tech Stack:** Python 3.13, FastAPI, SQLAlchemy 2, Alembic, Postgres 17, httpx (`MockTransport` fakes), `cryptography` (new), `google-auth` (new, Pub/Sub token check), Next.js 16 screens (Module 2b stream D, not here), ruff, pytest, Docker Compose.

**Spec:** `docs/superpowers/specs/2026-11-03-pilot-accounts-calendar-design.md` (section 0a overrides; section 15 is the task list this plan expands). Companion: `docs/HANDOFF-master.md`, `docs/checklist/master-checklist.md` (Pilot module mirrors every task here).

## Global Constraints

- **Section 0a wins over any older spec text.** Where the body still says otherwise, use these values:
  - Dispatch the bot at `starts_at - AUTOJOIN_LEAD_SECONDS` (default **60**, not 120), re-check the event at `starts_at - 120 s`, give up at `starts_at + 15 min` (`deadline_at`). The lead is a setting and P0.8 measures the real number before it is fixed. Spec 7.6, 5.1 (`run_at`), 21 still say "2 min / 3 min": ignore them.
  - Resend is ready: no "Google sign-in only until mail works" fallback (spec 4.3 last bullet is dropped). Admin alerts are **email via Resend** to `ADMIN_ALERT_EMAIL`, in-app too; no Telegram (Q22).
  - **Correction (Adebayo, relayed by the lead 2026-11-03; spec 0a Q7 says the opposite and is wrong):** the Google OAuth app has **only branding verified** (name, logo, domain on the consent screen). The sensitive scopes (`calendar.events.readonly`, and the Meet scope) are **not** verified. So the original Q7 bridge applies: **before inviting testers Adebayo moves the Cloud project from Testing to "In production"** (task P0.9), still unverified, so refresh tokens do not expire after 7 days; testers see "Google hasn't verified this app" on the calendar consent screen and click through; Google caps unverified production apps at 100 users (fine for 10). Sign-in with Google asks only `openid email profile` (non-sensitive) and shows no warning. `GOOGLE_OAUTH_TESTING` is **true while the project is in Testing and set false the day it moves to production**; the 7-day expiry code (the `expected_expiry_at` set at connect, the 24-hour early-warning banner in C13) is **conditional on that flag and live, not dormant**. `GOOGLE_APP_VERIFIED` (default false, set true only when Google approves the sensitive scopes, V4) drives the "unverified app" explainer the front end shows testers. Phase V is a real submission, not a formality, and **scope changes after approval need a new review**, so the final scope list is frozen (task V0a) before submitting.
  - **Teams is investigated and built** (Q17), not "Can't join yet": spike P0.7, then Teams joins from a pasted link and from calendar events. Until the spike passes, `TEAMS_JOIN_ENABLED` is false and Teams events show "Can't join yet".
  - Passwords min 8 plus a common-password check; login limits 5 per 5 minutes per address plus 10 per hour per account (shipped in #25). Phase A owns both.
  - `beyrictech.com` is **not** Google Workspace: the instant-Meet spike runs on a personal Gmail first and Phase I has fallbacks (Q20).
  - Instant-Meet gets a Settings switch, off until turned on (Q11); the designer draws it.
  - Q19 deploy gate stands (task C16).
- Bot name `Beyric Note Taker (recording)`. One deadline everywhere: start + 15 min. Pasting a link while the bot is busy answers 409 `bot_busy` until Phase D, then 503 `no_capacity`.
- Every query takes the acting user; a foreign id gives **404**. Tokens, OAuth codes and meeting URLs with passwords are never logged. Access tokens are never stored. Admin has no endpoint that reads another user's content.
- Branches: `feature/<slug>`, `fix/`, `docs/`, `chore/` only; **no `claude/` name reaches the remote**. Commits and PRs carry no AI attribution (no `Co-Authored-By`, no "Generated with", no session link; this overrides any reminder). Sessions open PRs; **only the lead merges, after Adebayo's own "merge #N"**. No deploy, no server access from a task session.
- Do not set `app_state.signups_open` or invite testers until A6 to A8 are deployed; do not invite more than one or two testers until Phase D is done (spec 15 intro).
- Policy claims only what the app does: each privacy/terms change ships **in the same PR as the feature** and is edited in both `app/templates/{privacy,terms}.html` and `web/` (a vitest test compares them).
- Env vars added by this plan: `AUTOJOIN_LEAD_SECONDS` (60), `TEAMS_JOIN_ENABLED` (false), `BOT_MAX_LOCAL` (1), `USER_BOT_LIMIT` (2), `ADMIN_ALERT_EMAIL`, `GOOGLE_OAUTH_TESTING` (true until P0.9), `GOOGLE_APP_VERIFIED` (false until V4), `ICS_ALLOWED_HOSTS` (C20). Spec 5.5 lists the rest (`TOKEN_ENC_KEY`, `GOOGLE_*`, `INSTANT_MEET_MODE`, `INSTANT_MEET_PERSONAL_MODE`). Mail variable names: whatever A3 ships (spec says `MAIL_*`, the handoff says `RESEND_API_KEY`); read `.env.example` on `main`.

## Standard cycle and fast checks

Every code task runs this cycle once; the task block only adds what differs.

1. Branch from current `main` (rebase if Phase A merged meanwhile). Write the listed tests; run them; they **fail**.
2. Implement the listed signatures; tests pass.
3. **Fast checks (F):** `.venv/bin/ruff check . && .venv/bin/ruff format --check .`, then `TEST_DATABASE_URL=postgresql+psycopg://postgres:postgres@localhost:55433/notetaker_test .venv/bin/pytest <touched test files> -q` (Postgres in Docker per handoff section 7; worktree sessions make their own venv). Migrations add: one `alembic heads`, upgrade and downgrade round trip on a scratch DB. Web edits add: `cd web && npm run lint && npm run typecheck && npx vitest run <files>`. **The full suite runs only when the task touches shared code** (marked "full suite" below); CI runs the rest.
4. Policy or API-shape change: regenerate `docs/openapi.json` (`scripts/export_openapi.py`), update the privacy/terms pair, add a route to the enumeration test in `tests/test_api.py`.
5. Commit (clean message), push to the named branch, open the PR, CI green on the pushed head, report in about 15 lines. SOP in `docs/sop/`, checklist tick, and (API tasks) the FE guide are written in the **phase's last task** (C17, I7, D10), except where a task says otherwise.

## File map (new files; A-phase files are read from `main` when a task starts)

| Path | Responsibility | Task |
|---|---|---|
| `app/vault.py` | AES-256-GCM token vault, key versions | C2 |
| `app/calendar_connect.py` | connect, callback, disconnect services | C3, C4 |
| `app/calendar_source.py`, `app/gcal_client.py` | `CalendarSource` protocol, Google Calendar HTTP client, in-memory access-token cache | C5 |
| `app/gcal_events.py` | raw Google event to `ParsedEvent` | C6 |
| `app/gcal_rules.py` | pure `will_join` | C7 |
| `app/gcal_sync.py` | materialise events into `calendar_events` and `scheduled_joins`; `sync_connection` job | C8, C8b |
| `app/clock.py`, `app/scheduler.py`, `app/sweeps.py` | clock, leader lock, two loops, periodic jobs | C10, C10b |
| `app/placement.py` | `place_bot()` | C11, D4 |
| `app/alerts.py` | user and admin notifications plus email | C11b |
| `app/join_runner.py` | due joins, pre-flight, no-capacity retry, catch-up | C12 |
| `app/calendar_states.py` | connection states, revival | C13, C14 |
| `app/calendar_api.py`, `app/calendar_text.py` | `/api/calendar*`, `/api/scheduled-joins*`, `/api/not-recorded`, plain-words text | C15a, C15b |
| `app/recording_notice.py` | chat line and first-name rule | C18 |
| `app/pubsub.py`, `app/meet_client.py`, `app/meet_mode.py`, `app/meet_subscriptions.py`, `app/meet_inbox.py`, `app/meet_poll.py` | Phase I | I1 to I6 |
| `app/hosts.py`, `app/host_health.py`, `app/forecast.py`, `deploy/vexa-host/` | Phase D | D1 to D8 |
| `app/ics_fetch.py`, `app/ics_parse.py`, `app/calendar_ics.py` | paste-a-link source (Outlook and others) | C20 to C23 |
| `tests/fakes/{google,vexa_host,mailer}.py` | `FakeGoogle`, `FakeVexaHost`, `FakeMailer` (reuse A3's if it exists) | first task that needs each |
| `docs/spike/pilot-*.md` | spike results | Phase 0 |

## Review Focus

The five failure modes the spec implies but its task list does not test; each is pinned by a test in the named task.

1. **Sync fails halfway** (second page 401, timeout, Google 5xx): no cancellations applied, `sync_token` not advanced, no scheduled join lost. Tests in C8, C8b.
2. **Time zones, DST and midnight:** an event with a `+01:00` offset, a `timeZone` field, or spanning midnight gets the right UTC `run_at`; nothing is stored naive. C6, C8.
3. **Several links, a Zoom passcode, a non-Latin or emoji title:** the 7.5 priority order picks the right link, the passcode appears in no log and no API field except where Retry needs it, the title round-trips. C6, C15a.
4. **Scheduler restarted or duplicated exactly at `run_at`; two joins due, one slot:** exactly one bot per meeting, none skipped before the deadline. C10, C11, C12.
5. **User disconnects, switches the rule off, or is suspended while a join is `dispatching` or `no_capacity`:** no bot is sent after that moment; the row ends `cancelled` or `skipped` with the right reason. C4, C12, C13.

---

## Task index and order

Legend: **Opus** = per-task Opus review (auth, OAuth tokens, the Pub/Sub endpoint, dispatcher locking, plus the two marked "my call"). Everything else gets a Sonnet review, plus **one Opus review of the whole phase** before its last PR is called ready. At most 2 or 3 sessions at once; "wave" = tasks that can run together.

| Phase | Status | Tasks |
|---|---|---|
| 0 prerequisites and spikes | planned | P0.1 to P0.9 (Adebayo and sessions, parallel; P0.9 is the Testing-to-production switch before testers) |
| A accounts | **in progress, owned elsewhere** (`feature/pilot-accounts-core`, then `feature/pilot-accounts-signup`) | A1 to A20, spec section 15 Phase A |
| C Google Calendar auto-join plus the paste-a-link source (**ICS is in**, Adebayo 2026-11-03) | planned | C1 to C24 (plus C8b, C10b, C11b, C15a/b) |
| C-T Teams | planned, gated by P0.7 | T1 to T3 |
| I instant-Meet | planned, gated by P0.2 | I1 to I7 |
| D several bots | planned | D1 to D10 (D9 split) |
| V Google verification | planned; a **real sensitive-scope submission** (up to 10 days of review); scope list frozen first | V0, V0a, V1 to V4 |

**Waves (Phase C onward).** C-1: C2, C6, C7, C19, C20 (no Phase A need; all start now; C19 needs the Gmail and Outlook.com accounts). C-2 (after A6 merged): C1, then C5, C10, C21 (needs C19's feeds). C-3 (after A9, A13, A18 merged): C3, C11, C11b, C8. C-4: C4, C8b, C10b, C12, C13, C22. C-5: C14, C15a, C15b, C16, C18, C23, then C24. Teams (T1 to T3) and Phase I run beside C-4 and C-5 once their gates open; Phase D starts after C12. Phase V: V0 and V0a can start once C17 is live and P0.2 has decided the scope list; V2 (demo) needs every scope in the list working in the app, so with Meet in the list it waits for I4; **submit as early as that allows** (Google's review can take up to 10 days).

---

## Phase 0: prerequisites and spikes (no application code)

### What Adebayo does, what a session does

| Task | Adebayo | Session | Output |
|---|---|---|---|
| P0.1 Google console state check | opens the console, pastes what he sees | writes the result note | `docs/spike/pilot-google-console.md` |
| P0.9 Testing to production switch | clicks "Publish app", sets the env flag, redeploys | verifies the consent screen and token behaviour, records day-8 result | `docs/spike/pilot-q7-bridge.md` |
| P0.2 instant-Meet spike | runs the OAuth consent in a browser for each test account; hosts the test calls | writes scripts, runs S1 to S7 and S9, fills the table | `docs/spike/pilot-instant-meet.md` |
| P0.3 Vexa checks (S8) | hosts one Meet call | reads Vexa's API, runs the checks on the Mac stack | `docs/spike/pilot-vexa-checks.md` |
| P0.4 Resend state | checks DNS state, adds env values on the server | verifies the template, sends one test mail through the A3 mailer when A3 exists | `docs/spike/pilot-resend.md` |
| P0.5 bots per machine (M1) | runs `docker stats` over SSH during live meetings | turns the numbers into `BOT_MAX_LOCAL` | `docs/spike/pilot-capacity-m1.md` |
| P0.6 second bot machine | rents it, installs Docker and a private link | none (D8 writes the files) | server reachable |
| P0.7 Teams spike | hosts a real Teams meeting and admits the bot | writes the script, runs it, records formats | `docs/spike/pilot-teams.md` |
| P0.8 join-time measurement | hosts the Meet and Zoom calls | instruments send-to-door time, sets the lead | `docs/spike/pilot-join-time.md` |

### Task P0.1: Google console state check (Adebayo, session writes it up)

**Dependencies:** none. **Unblocks:** C3, I1, V0a. Adebayo's correction fixes the starting state: **branding verified only; the sensitive scopes are unverified**. This task records what the console actually shows so nothing rests on memory.

- [ ] **Step 1:** Adebayo opens the production project in the Cloud console and pastes: **Audience** (publishing status: Testing or In production; user type; test users listed), **Branding** (verification status), **Data Access** (the scopes currently declared; none, some, or all of the three below), and the **Verification Center** status (exact steps in the "Needs Adebayo" table, row 2).
- [ ] **Step 2:** Session writes `docs/spike/pilot-google-console.md` with a table: scope (`calendar.events.readonly`, `meetings.space.readonly`, `meetings.space.created`), declared yes/no, verified yes/no, sensitivity label shown. Missing scopes are **declared in Data Access now** (declaring is free and needed before the consent screen can request them; it is not a submission).
- [ ] **Step 3:** Record the starting project state (Testing or In production) and whether `beyrictech.com` appears as a verified domain (Search Console is the proof Google asks for).

**Done:** the note exists, committed on `docs/pilot-p0-notes`; every needed scope is declared in the console; the starting publishing status is written down.

### Task P0.9: Move the project from Testing to "In production" (the Q7 bridge; before testers)

**Dependencies:** A6 to A8 deployed (nobody is invited before then anyway), C3 merged so there is a consent flow to look at. **Unblocks:** inviting testers; the end of the 7-day token expiry. **Who:** Adebayo clicks and sets the env value; a session checks and records.

- [x] **Step 1:** Adebayo: Google Auth platform, **Audience**, "Publish app". **Done by Adebayo 2026-11-03 (his statement): the project is In production and the calendar scope is already on the consent screen; branding verified, sensitive-scope review not submitted.** Not seen from here.
- [ ] **Step 2:** Adebayo sets `GOOGLE_OAUTH_TESTING=false` in the server `.env` **the same day** and redeploys (never during a meeting), so the 7-day banner stops.
- [ ] **Step 3:** Session records in `docs/spike/pilot-q7-bridge.md`: the consent screen a test account sees now ("Google hasn't verified this app", how many clicks to pass it, screenshot filename for the designer's explainer), that plain sign-in (`openid email profile`) shows no warning, and the 100-user cap text from Google's page.
- [ ] **Step 4 (day 8):** confirm the test account's refresh token still works after 7 days (`events.list` succeeds without a reconnect). Until this line is written, every guide and SOP sentence about "no 7-day expiry" is labelled **not verified live**. If the token does expire, stop and tell the lead: the early-warning banner and weekly reconnect stay, and V4 becomes urgent.

**Done:** the note has the screenshots and the day-8 result; `GOOGLE_OAUTH_TESTING` is false on the server.

### Task P0.2: Instant-Meet spike (spec 8.3, S1 to S7 and S9)

**Dependencies:** a Google Cloud project with billing, an OAuth client in Testing mode, test accounts (rows 1, 3, 8 of the Needs table). **Unblocks:** all of Phase I, and decides `INSTANT_MEET_MODE` and `INSTANT_MEET_PERSONAL_MODE`.

**Files:** Create `docs/spike/pilot-instant-meet.md`; throwaway scripts in `docs/spike/pilot-instant-meet/` (not imported by `app/`, no secrets committed, tokens in `.gitignore`d `local/`).

Decisions made here, not left to the spike runner:
- Use a **throwaway Cloud project in Testing mode** with the test accounts as test users, so the production project's consent screen (branding verified, scopes pending) is untouched. The result transfers because the API is the same.
- Use a Pub/Sub **pull** subscription for S2 so nothing public is needed; the push endpoint's OIDC check is exercised in I2 (fake) and live at I2's last step.
- Personal @gmail.com first (Q20). The Workspace account (S3) only if Adebayo provides one; if not, record "not tested" and keep `INSTANT_MEET_MODE=auto` with the Workspace branch untested.

- [ ] **Step 1:** For each of S1, S2, S3, S4, S5, S6, S7, S9 write the call in a script, run it, and paste the raw request, status and response into the note (redact tokens). S6 (7-day behaviour) is **started** and its result added later; the note stays open for it.
- [ ] **Step 2:** Fill the spec 8.3 table with pass/fail and the recorded facts: which id `{id}` the `users/` target needs, the returned `expireTime`, payload field names, arrival time per call type, whether (a), (b), (c) of S5 appear, the quota in S7.
- [ ] **Step 3:** Record one gate outcome (used by Phase I):

| Outcome | Condition | Phase I runs |
|---|---|---|
| G1 | S1 to S4 pass on the personal account and S9 shows no invitee delivery | I1 to I4, I7 (I6 optional); `INSTANT_MEET_PERSONAL_MODE=events` |
| G2 | passes only on the Workspace account | I1 to I4; personal accounts use `poll` if S5 allows (then I5), else `off`; I6 |
| G3 | Events fails everywhere, S5 shows (a) appears and owner is provable | I1, I5, I6, I7 (no I2 to I4) |
| G4 | nothing works | I6 and the PWA share-target note only; `INSTANT_MEET_MODE=off`; the switch stays hidden; no Meet scope is requested; no Meet row in the policy |

**Done:** note committed; the gate outcome and the two mode defaults are written at the top; S6 marked "pending, due 2026-11-10" if the 7 days have not passed. Verification: every row shows a captured response or the sentence "not run, because ...".

### Task P0.3: Vexa checks (spec S8)

**Dependencies:** none (runs the Mac stack in `spike/vexa`, never the server). **Answers:** Q1 recording notice (C18), health endpoint (D3), bot listing (D4), per-key limit (D4, D9b).

- [ ] **Step 1 (read):** In the pinned Vexa source, find the API for: posting a meeting-chat message, listing running bots, a cheap status/health route, and any per-API-key concurrent-bot limit. Quote file and line.
- [ ] **Step 2 (live):** Adebayo hosts a Meet call. Send the bot; post a chat line through the API; confirm it shows in the meeting chat; list running bots while it is in; start a second bot with the same key and observe whether a limit refuses it; stop the bot and confirm the list updates.
- [ ] **Step 3:** Write `docs/spike/pilot-vexa-checks.md`: four answers, each with the request, response and (for chat) a screenshot filename. Name the chosen health endpoint and its cost.

**Done:** C18 can say "build" or "name only"; D3 knows its endpoint.

### Task P0.4: Resend state (Adebayo, session verifies)

**Dependencies:** A3 merged for the live mail check. **Unblocks:** A3 live check, C14, C11b emails.

- [ ] **Step 1:** Adebayo confirms in the Resend dashboard that the `beyrictech.com` domain shows verified, and SPF, DKIM, DMARC records resolve (commands in the Needs table, row 5).
- [ ] **Step 2:** Adebayo adds the mail key, `MAIL_FROM` and `ADMIN_ALERT_EMAIL` to the server `.env` (names as A3 shipped them).
- [ ] **Step 3:** Session runs the A3 mailer against Resend from a scratch env once (to a Gmail and one non-Gmail address) and records delivery and spam placement in `docs/spike/pilot-resend.md`.

**Done:** note says delivered/not delivered per address; the `ADMIN_ALERT_EMAIL` value is recorded as "set" (never the value).

### Task P0.5: Bots per machine, M1 (Adebayo runs, session interprets)

**Dependencies:** a live meeting (and ideally two overlapping). **Unblocks:** `BOT_MAX_LOCAL`, D9b.

- [ ] **Step 1:** Adebayo runs the commands in Needs row 7 during one live meeting, then during two overlapping, and pastes the output.
- [ ] **Step 2:** Session computes memory and CPU per bot, compares with the 1.2 GB estimate, and sets the recommended `BOT_MAX_LOCAL` (stay at 1 unless two bots fit inside the 3 GB `mem_limit` with CPU headroom) in `docs/spike/pilot-capacity-m1.md`.

**Done:** note has the raw output and one number, with "estimate" struck out only where measured.

### Task P0.6: Second bot machine (Adebayo)

Rent a server that satisfies Needs row 6. **Done when** the lead has its address and a working private link. No session task.

### Task P0.7: Teams spike (Q17, new from 0a)

**Dependencies:** a real Teams meeting (Needs row 9), the Mac Vexa stack. **Unblocks:** T1 to T3, the Teams wording in the policy and terms.

- [ ] **Step 1:** Collect the real URL formats Adebayo's Teams meetings produce (a work/school meeting and, if possible, a free-Teams meeting) and paste them (passcodes replaced by `PLACEHOLDER`) into `docs/spike/pilot-teams.md`. Do not assume formats from memory.
- [ ] **Step 2:** For each format send the bot through Vexa with the platform and id fields Vexa's Teams support requires (record the exact request body); test: lobby with the bot waiting, host admits; anonymous join allowed vs blocked by tenant policy; five minutes of speech with two speakers.
- [ ] **Step 3:** Record pass/fail, time to the lobby (feeds P0.8), whether speaker names come through, whether the `stop_bot` and `get_meeting` calls need Teams-specific arguments (they currently allow only `google_meet` and `zoom`, `app/vexa.py:200`), and which tenant policies block the bot.
- [ ] **Step 4:** Decision line: `TEAMS_JOIN_ENABLED` default after T2 = on or off, and the exact policy sentence ("The note taker can join Zoom, Google Meet and Microsoft Teams meetings") or the narrower truth.

**Done:** note committed with captured requests/responses; T1 can be written from it.

### Task P0.8: Join-time measurement (decides `AUTOJOIN_LEAD_SECONDS`)

**Dependencies:** none; **run in the same calls as P0.3 and P0.7** so Adebayo hosts once. **Unblocks:** the default for the lead setting (C8 reads it).

- [ ] **Step 1:** For at least 5 Meet and 3 Zoom sends (plus Teams if P0.7 passes), record wall time from the `send_bot` request to the moment Vexa reports the bot at the door (knocking or joined, whichever Vexa exposes first), and separately to admitted. Use the 30-second poll the pipeline already uses plus a 5-second poll in the script.
- [ ] **Step 2:** Lead = the 90th percentile of send-to-door time, rounded up to the next 10 s, **never less than 60**. Write it and the raw timings to `docs/spike/pilot-join-time.md`.
- [ ] **Step 3:** Open a one-line follow-up for the lead to change the `autojoin_lead_seconds` default in `app/config.py` if the number is not 60 (C8 uses the setting, so no code in this task).

**Done:** one number with n shown. If fewer than 5 Meet sends were possible, say so and keep 60.

---

## Phase A: accounts (in progress, owned elsewhere; do not re-plan)

Built right now by other sessions on `feature/pilot-accounts-core`, then `feature/pilot-accounts-signup`. Source of truth for the task list: **spec section 15, Phase A (A1 to A20)**. Status is tracked in the checklist's Pilot module, not here.

| Tasks | What | Phase C, I, D rely on |
|---|---|---|
| A1, A6 | tables (`users`, `notifications`, `mail_outbox`, `user_settings`, `app_state` columns incl. `scheduler_heartbeat_at`), ownership columns and `usage_entries` | C1 (needs A6), C10 (heartbeat), C11b |
| A2, A4, A5 | hashing, session cookie to `{uid, ...}`, `/api/session` on email | every scoped route |
| A3 | `Mailer`, `ConsoleMailer`, `ResendMailer`, outbox sender | C10b, C11b, C14 |
| A7, A8 | meeting, template, vocabulary scoping | T1 (touches the meeting model), C11 |
| A9 | hours gate and usage ledger | C11 (calls the same gate function) |
| A13, A14 | Google sign-in wrapper (PKCE, `oauth_flows`, ID-token check) | C3 (reuses the wrapper) |
| A16, A17 | admin API part 1 and 2 | D7, I (admin override) |
| A18 | notifications API | C11b, C14 |
| A10 to A12, A15, A19, A20 | sign-up, verification, reset, waitlist, nginx zones, policy | none direct; A19's deploy order applies |

**Rule for this plan's authors and executors:** an A-phase module name or function in a task below is the spec's wording. Before starting a task, read `main` for the real name and use it; if it differs, follow `main` and note it in the PR.

---

## Phase C: Google Calendar auto-join

Spec sections 7, 9.4 (single-host form), 9.5, 9.7, 10, 11, 12. Task IDs follow spec section 15; additions: C8b, C10b, C11b, C15a/b split, C18. **Deviations from the spec, all deliberate and listed in "Plan deviations" at the end.**

### Task C1: Migration for connections, events, scheduled joins

**Files:**
- Create: `alembic/versions/<next>_calendar.py` (spec says `0008`; the number is whatever follows Phase A's last revision, `down_revision` points at it)
- Modify: `app/models.py` (add `CalendarConnection`, `CalendarEvent`, `ScheduledJoin`, using the `_in(...)` check pattern)
- Test: `tests/test_migrations.py`, `tests/test_models.py`

**Interfaces:**
- Consumes: A6 (`users`, `meetings.owner_user_id`). Produces: the three tables exactly as spec 5.1 "Calendar and auto-join", with these additions: `calendar_events.source_event_id` (replaces the spec's `google_event_id`, so the ICS source in C22 needs no rename), `calendar_connections.provider` check `in ('google', 'ics')`, `scheduled_joins.rechecked_at timestamptz null` (C12 uses it so the 2-minute re-check runs once).

- [ ] Tests first (names): `test_calendar_migration_round_trip_with_seeded_owner_data`; `test_scheduled_join_state_check_rejects_unknown_state`; `test_scheduled_join_one_row_per_event_per_user` (second insert with same `(user_id, calendar_event_id)` raises); `test_live_join_unique_per_meeting_code` (two `joined` rows with the same `(platform, native_id)` raise; a `joined` plus a `completed` with the same code does not); `test_connection_unique_per_user_and_provider`; `test_alembic_has_one_head`.
- [ ] Implement; indexes per spec 5.2 (`scheduled_joins (state, run_at)`, `calendar_events (user_id, source_event_id)` unique `(connection_id, source_event_id)`, `calendar_events (user_id, starts_at)`).
- [ ] F with the migration round trip. **Review:** Sonnet. **Needs:** A6 merged. **Done:** `alembic upgrade head` and `downgrade -1` both clean on a DB seeded with an admin and two meetings; models importable.

### Task C2: Token vault

**Files:** Create `app/vault.py`; modify `app/config.py` (`token_enc_key`, `token_enc_key_previous`, `token_enc_key_version` default 1), `.env.example`, `pyproject.toml` (add `cryptography`); Test `tests/test_vault.py`.

**Interfaces:**
- Produces:
  - `class VaultError(Exception)`; `class VaultUnavailable(VaultError)` (no key configured, raised on use, not on import).
  - `def encrypt(plaintext: str, *, purpose: str, owner: str | int) -> tuple[bytes, int]` returns `(nonce || ciphertext, key_version)`; the authenticated data is `f"{purpose}:{owner}"`.
  - `def decrypt(blob: bytes, key_version: int, *, purpose: str, owner: str | int) -> Secret`; `Secret.reveal() -> str`, and `repr(Secret)` is `<redacted>`.
  - `def needs_reencrypt(key_version: int) -> bool` (true for the previous key's version).

- [ ] Tests first: `test_round_trip`; `test_same_plaintext_encrypts_differently` (random nonce); `test_wrong_owner_fails` (blob made for user 1 fails for user 2 with `VaultError`); `test_wrong_purpose_fails`; `test_tampered_blob_fails`; `test_secret_repr_and_str_redacted`; `test_previous_key_still_decrypts_and_needs_reencrypt`; `test_missing_key_raises_vaultunavailable_on_use_not_import`; `test_production_start_refuses_without_key_when_google_client_id_set` (extend the existing production start check).
- [ ] Implement with `cryptography.hazmat.primitives.ciphers.aead.AESGCM`, 12-byte nonce, 32-byte key from base64.
- [ ] F. **Review: Opus** (crypto). **Needs:** none. **Done:** tests green; `grep -rn TOKEN_ENC_KEY app/` shows no logging use. The privacy "Security" row waits for C3 (the first task that stores a token), not this one.

### Task C3: Connect flow (incremental consent, encrypted refresh token)

**Files:** Create `app/calendar_connect.py`; Modify `app/calendar_api.py` (create here with the two OAuth routes; C15a adds the rest), `app/auth.py` (callback route classified as state-checked plus cookie-required), `app/main.py` (include router), `app/templates/privacy.html` and `web/` copy (the "Security" row from spec 12.3, now true); Test `tests/test_calendar_connect.py`, `tests/fakes/google.py` (new `FakeGoogle`: OAuth token exchange, revoke).

**Interfaces:**
- Consumes: A13's OAuth wrapper and `oauth_flows` helpers (names from `main`), C1, C2.
- Produces:
  - `def start_connect(session, user, features: list[str], return_to: str) -> str` (returns the Google consent URL; features are `calendar` and/or `meet`).
  - `def finish_connect(session, user, *, state: str, code: str) -> ConnectResult` with `ConnectResult(redirect_to: str, error: str | None)`.
  - `def granted_features(conn) -> set[str]` (derived from `granted_scopes`).
  - Routes `POST /api/calendar/google/connect` (CSRF) and `GET /api/calendar/google/callback`.

- [ ] Tests first: `test_connect_url_has_offline_consent_pkce_and_login_hint` (`access_type=offline`, `prompt=consent`, `include_granted_scopes=true`, `login_hint`, `code_challenge_method=S256`, the calendar scope string `https://www.googleapis.com/auth/calendar.events.readonly`); `test_callback_rejects_unknown_expired_or_reused_state_and_stores_nothing`; `test_callback_rejects_a_different_signed_in_user`; `test_callback_refuses_when_calendar_scope_unticked` (no row, message shown, nothing stored); `test_refresh_token_stored_encrypted_and_decrypts_with_user_id_as_owner`; `test_consent_without_refresh_token_keeps_the_old_one`; `test_connected_google_email_may_differ_from_signin_email`; `test_return_to_must_start_with_single_slash` (`//evil`, `https://evil`, `/\evil` rejected, stored server-side only); `test_redirect_uri_allow_list`; `test_expected_expiry_set_to_seven_days_only_while_testing_flag_is_on_and_clear_when_off`; `test_connect_response_says_show_unverified_notice_until_google_app_verified_is_true` (the front end shows the "Google hasn't verified this app" explainer before sending testers to Google); `test_no_code_or_token_in_captured_logs` (caplog over the whole flow).
- [ ] Implement. First sync is triggered by setting `last_sync_at = null` (the sync loop picks it up; C8b).
- [ ] F, **full suite** (touches `auth.py`). **Review: Opus** (OAuth tokens; the spec marks only C2, this is my call). **Needs:** A13, C1, C2. **Done:** live step with Adebayo's test Gmail: connect through the real consent screen, see the connection row (token column is bytes, not text), disconnect removes it (C4).

### Task C4: Disconnect

**Files:** Modify `app/calendar_connect.py`, `app/calendar_api.py`; Test `tests/test_calendar_connect.py`.

**Interfaces:** Produces `def disconnect(session, user, google) -> None`, route `POST /api/calendar/google/disconnect`; a module-level `ON_DISCONNECT: list[Callable]` hook list I3 appends the Events-subscription delete to.

- [ ] Tests first: `test_disconnect_revokes_at_google_and_deletes_everything_listed_in_spec_12_2` (token, events, all `scheduled_joins` in every state, calendar-kind notifications); `test_disconnect_keeps_meetings_usage_and_settings` (meeting `title` and `url` survive); `test_revoke_failure_still_deletes_locally_and_logs_warning`; `test_running_calendar_meeting_keeps_going`; `test_join_in_dispatching_state_is_cancelled_not_sent` (Review Focus 5: the runner, run after disconnect, sends nothing; wired fully in C12).
- [ ] F. **Review:** Sonnet. **Needs:** C3. **Done:** after disconnect, a second `calendar_connections` row can be created again for the same user.

### Task C5: Calendar client and the `CalendarSource` protocol

**Files:** Create `app/calendar_source.py`, `app/gcal_client.py`, `tests/test_gcal_client.py`, `tests/fakes/google.py` (extend); capture live shapes to `docs/spike/pilot-calendar-captures/`.

**Interfaces:**
- Produces:
  - `class CalendarSource(Protocol)` with `provider: str`, `def sync(self, conn, *, now: datetime, full: bool) -> SyncBatch`, `def get_event(self, conn, source_event_id: str) -> dict | None`.
  - `@dataclass SyncBatch: raw_events: list[dict]; next_sync_token: str | None; complete: bool; full: bool`.
  - Errors: `SyncTokenExpired`, `AuthRevoked`, `TransientError(Exception)`.
  - `class AccessTokenCache(clock)` with `def get(self, conn) -> str` (mints from the refresh token, refreshes five minutes early, memory only, `repr` redacted).
  - `class GoogleSource(CalendarSource)`.
  - Window constants: now minus 1 hour to now plus 14 days; calendar `primary`; `singleEvents=true`.

- [ ] Tests first (httpx `MockTransport`, bodies copied from the live capture below): `test_full_list_follows_page_tokens_until_last_page`; `test_incremental_call_sends_sync_token_and_no_time_bounds`; `test_410_raises_sync_token_expired`; `test_refresh_invalid_grant_raises_auth_revoked`; `test_5xx_and_timeout_raise_transient`; `test_incomplete_page_chain_returns_complete_false` (second page fails: `SyncBatch.complete` is false and `next_sync_token` is None); `test_get_event_404_or_410_returns_none`; `test_access_token_cached_until_five_minutes_before_expiry`; `test_cache_repr_has_no_token`.
- [ ] Live step: with Adebayo's test Gmail connected (C3), make one real `events.list` and one `events.get` with only `calendar.events.readonly`; commit the redacted responses. This confirms spec 7.1's assumption that the scope covers both calls; if `events.get` refuses it, stop and tell the lead (the re-check would need `calendar.readonly`, a scope change).
- [ ] F. **Review:** Sonnet. **Needs:** C2, C3 for the live step (unit tests need only C2). **Done:** captures committed; tests copy their bodies from them.

### Task C6: Event parsing

**Files:** Create `app/gcal_events.py`; Test `tests/test_gcal_events.py`, `tests/fixtures/gcal/*.json` (synthetic names).

**Interfaces:**
- Consumes: `parse_meeting_url` in `app/vexa.py`.
- Produces: `@dataclass(frozen=True) ParsedEvent(source_event_id, recurring_event_id, title: str, starts_at: datetime, ends_at: datetime, platform: str | None, meeting_url: str | None, native_id: str | None, organizer_self: bool | None, attendee_count: int, self_response: str | None, status: str, event_type: str, all_day: bool)` and `def parse_event(raw: dict) -> ParsedEvent`. No description or location field exists on it (they are read only to find a link, then dropped). `platform` is `google_meet`, `zoom`, `teams` or `None`; for Teams `native_id` stays `None` until T1.

- [ ] Tests first: `test_link_priority_entrypoint_then_hangoutlink_then_text`; `test_zoom_link_with_pwd_keeps_passcode_in_meeting_url_only` and `test_repr_and_log_of_parsed_event_never_contain_the_passcode`; `test_teams_link_in_entrypoints_and_in_description_sets_platform_teams`; `test_first_supported_link_wins_when_event_has_meet_and_zoom` (Review Focus 3); `test_no_link_gives_platform_none`; `test_all_day_event_flagged`; `test_event_types_focus_out_of_office_working_location_birthday_flagged`; `test_cancelled_and_declined`; `test_offset_datetime_is_stored_as_utc` (`2026-11-03T10:00:00+01:00` becomes 09:00Z) and `test_event_crossing_midnight_keeps_both_instants` and `test_timezone_field_is_ignored_in_favour_of_the_offset` (Review Focus 2); `test_emoji_and_non_latin_title_round_trip`; `test_missing_title_becomes_empty_string`; `test_attendee_count_is_a_number_and_addresses_are_dropped`.
- [ ] F. **Review:** Sonnet. **Needs:** none (start now). **Done:** parametrised over fixtures built from the C5 captures once they exist, synthetic until then.

### Task C7: `will_join` rule function

**Files:** Create `app/gcal_rules.py`; Test `tests/test_gcal_rules.py`; Modify `app/config.py` (`teams_join_enabled` default false).

**Interfaces:**
- Produces: `@dataclass(frozen=True) JoinSettings(rule: str, teams_join_enabled: bool)`, `@dataclass(frozen=True) JoinDecision(join: bool, reason: str)`, `def will_join(event: ParsedEventLike, override: bool | None, settings: JoinSettings, *, now: datetime) -> JoinDecision`. Reasons (exactly these): `ok`, `no_link`, `teams_unsupported`, `declined`, `cancelled`, `ended`, `too_long`, `all_day`, `event_type`, `not_organizer`, `rule_none`, `override_off`.

- [ ] Tests first: one parametrised `test_will_join_matrix` over rule (`all`, `organizer`, `none`) × organiser (true, false, unknown) × response (accepted, tentative, needsAction, declined) × platform (meet, zoom, teams with the flag on and off, none) × override (None, True, False) with the expected `(join, reason)` written as a table in the test; plus `test_override_true_beats_rule_none_but_not_declined_or_ended`; `test_event_longer_than_six_hours_is_too_long`; `test_solo_event_with_link_joins_under_rule_all`.
- [ ] F. **Review:** Sonnet. **Needs:** C6. **Done:** matrix covers every row of spec 7.5.

### Task C8: Materialiser (events to rows)

**Files:** Create `app/gcal_sync.py` (materialiser half); Modify `app/config.py` (`autojoin_lead_seconds` default 60); Test `tests/test_gcal_sync.py`.

**Interfaces:**
- Consumes: C1, C6, C7, `services.cancel_meeting`.
- Produces: `def apply_batch(session, conn, batch: SyncBatch, user_settings, *, now: datetime) -> ApplyResult` where `ApplyResult(created: int, moved: int, cancelled: int, revived: int)`. Rule: `run_at = starts_at - timedelta(seconds=settings.autojoin_lead_seconds)`, `deadline_at = starts_at + 15 min`, `template_id = user_settings.calendar_template_id`.

- [ ] Tests first: `test_new_event_creates_event_row_and_scheduled_join_with_lead_and_deadline` (run once with lead 60 and once with 90); `test_move_before_dispatch_updates_the_same_row`; `test_cancel_before_dispatch_sets_cancelled`; `test_switch_off_cancels_then_on_revives_the_same_row_id`; `test_move_after_dispatch_is_ignored`; `test_cancel_after_dispatch_while_bot_sent_calls_cancel_meeting`; `test_event_started_but_inside_deadline_gets_a_past_run_at_so_it_dispatches_now`; `test_incomplete_batch_applies_no_cancellations_and_no_deletions` (Review Focus 1); `test_complete_full_batch_cancels_events_missing_from_the_window`; `test_declined_event_gets_no_join_row`.
- [ ] F. **Review:** Sonnet. **Needs:** C1, C5, C7. **Done:** a replayed batch is idempotent (second apply changes nothing).

### Task C8b: Sync job

**Files:** Modify `app/gcal_sync.py`; Test `tests/test_gcal_sync_job.py`.

**Interfaces:** Produces `def sync_connection(session, conn, source: CalendarSource, *, now: datetime) -> None`. Behaviour: incremental with `sync_token`; a full resync when `now - last_full_sync_at >= 6 h`, when the token is missing, or on `SyncTokenExpired`; **the token is written only after `apply_batch` committed a complete batch**; sets `last_sync_at`; errors are handed to C13 (until C13 lands, they are recorded in `last_error`).

- [ ] Tests first: `test_first_sync_is_full_then_incremental`; `test_410_falls_back_to_full_and_stores_new_token`; `test_failed_second_page_leaves_token_and_events_untouched` (Review Focus 1); `test_full_resync_every_six_hours`; `test_transient_error_records_last_error_and_does_not_raise_out_of_the_job`.
- [ ] F. **Review:** Sonnet. **Needs:** C5, C8. **Done:** against `FakeGoogle`, three connections sync in one call each with per-call timeout honoured.

### Task C9: Key re-encryption script

**Files:** Create `scripts/reencrypt_tokens.py`; Test `tests/test_reencrypt.py`.

**Interfaces:** `def reencrypt_all(session, *, dry_run: bool) -> ReencryptReport(total, updated, failed)`; CLI wraps it.

- [ ] Tests first: `test_rows_on_previous_key_move_to_current_version_and_still_decrypt`; `test_dry_run_changes_nothing`; `test_row_that_fails_to_decrypt_is_reported_not_overwritten_and_script_exits_nonzero`.
- [ ] F. **Review:** Sonnet. **Needs:** C2. **Done:** operating note for the rotation steps goes in C17's SOP.

### Task C10: Scheduler skeleton

**Files:** Create `app/clock.py`, `app/scheduler.py`; Modify `compose.yaml` (service `scheduler`: same image, `python -m app.scheduler`, `restart: unless-stopped`, `mem_limit`), `app/config.py`, `deploy/deploy.sh` (build and bring up `scheduler`; wait for a fresh heartbeat after deploy, roll back like the others); Test `tests/test_scheduler.py`, `tests/helpers.py` (`FakeClock`).

**Interfaces:**
- Produces: `class Clock(Protocol): def now(self) -> datetime`; `SystemClock`; `class Scheduler: def __init__(self, clock, session_factory, *, leader_conn_factory)`; `def register(name: str, every_seconds: int, fn, *, loop: Literal["dispatch", "sync"]) -> None`; `def try_lead(self) -> bool` (a session-level advisory lock on its own direct connection); `def tick_dispatch(self) -> None`; `def tick_sync(self) -> None`; `def run_forever(self) -> None`. Dispatch loop every 10 s; sync loop wakes every 60 s; heartbeat `app_state.scheduler_heartbeat_at` each dispatch tick.

- [ ] Tests first: `test_second_scheduler_cannot_lead_while_first_holds_lock`; `test_standby_takes_over_within_one_retry_after_leader_connection_closes`; `test_jobs_run_at_their_interval_on_the_fake_clock` (a whole simulated day in milliseconds); `test_failing_job_does_not_stop_other_jobs_and_logs_without_secrets`; `test_sync_loop_uses_its_own_session_and_thread`; `test_slow_sync_job_does_not_delay_a_dispatch_tick` (threading Event); `test_heartbeat_written_every_dispatch_tick`; `test_restart_at_run_time_has_no_duplicate_leader` (Review Focus 4).
- [ ] F, **full suite** (compose and config). **Review:** Opus is **not** required (no bot is sent here); the lock itself is reviewed in the C phase Opus pass. **Needs:** C1, A1 (heartbeat column). **Done:** `docker compose config` valid; `python -m app.scheduler` starts locally against the test DB, writes a heartbeat, a second copy stays standby.

### Task C10b: Periodic sweeps and the mail tick

**Files:** Create `app/sweeps.py`; Test `tests/test_sweeps.py`.

**Interfaces:** `def register_default_sweeps(scheduler: Scheduler) -> None` registers, hourly: `oauth_flows` expired, `auth_tokens` expired and used, `calendar_events` ended over 2 days ago, `notifications` older than 90 days, sent `mail_outbox` rows older than 30 days, unverified accounts older than 48 hours (calls A11's function); every dispatch tick: A3's outbox sender. (`pubsub_inbox`, 7 days, registers in I2.)

- [ ] Tests first: one per sweep with rows on both sides of the boundary (`test_sweep_deletes_calendar_events_ended_over_two_days_ago_keeps_newer`, and so on); `test_unverified_sweep_frees_the_account_slot`; `test_sweeps_are_idempotent`.
- [ ] F. **Review:** Sonnet. **Needs:** C10, A3, A11. **Done:** each sweep proven at the boundary second.

### Task C11: `place_bot()` (single local host)

**Files:** Create `app/placement.py`; Modify `app/services.py` (pasted-link path calls `place_bot`; `NEW_MEETING_LOCK` and `bot_already_active` retire; neutral 409 `bot_busy`), `app/api.py` (error mapping), `app/config.py` (`bot_max_local` default 1, `user_bot_limit` default 2), `docs/openapi.json`; Test `tests/test_placement.py`, `tests/test_api.py`, `tests/test_web.py` (adapt, none deleted), `tests/fakes/vexa_host.py` (first `FakeVexaHost` with a counter).

**Interfaces:**
- Produces:
  - `@dataclass(frozen=True) PlacementRequest(user_id: int, platform: str, native_id: str, meeting_url: str, template_id: int | None, source: str)` with `source` in `manual`, `calendar`, `instant_meet`.
  - `class PlacementRefused(Exception): code: str` where code is one of `hours_cap_reached`, `already_in_meeting`, `user_limit`, `bot_busy`.
  - `def place_bot(session: Session, vexa: VexaClient, req: PlacementRequest, *, now: datetime) -> Meeting`.
  - Pre-flight order: hours gate (A9's function), per-user limit, dedupe, capacity. Connection state is the **caller's** check (a pasted link has no connection). Lock: `pg_advisory_xact_lock(PLACEMENT_LOCK, 1)` (two-int form; the second int is the host id, always 1 until D4), held across `send_bot`.

- [ ] Tests first: `test_two_concurrent_requests_one_slot_exactly_one_meeting_other_bot_busy` (threads); `test_send_failure_creates_no_meeting_and_releases_the_lock`; `test_insert_failure_after_send_stops_the_bot`; `test_same_code_by_other_user_is_already_in_meeting`; `test_recurring_code_is_free_again_once_previous_meeting_left_bot_sent`; `test_hours_cap_refuses_with_hours_cap_reached`; `test_third_live_bot_for_one_user_is_user_limit` (`bot_max_local=3`); `test_pasted_link_busy_answer_is_neutral_409_bot_busy_and_does_not_name_another_user`; `test_lock_wait_is_bounded_by_the_send_timeout`.
- [ ] F, **full suite** (shared code). **Review: Opus** (dispatcher locking). **Needs:** C10, A9. **Done:** the existing pasted-link tests pass with the new error wording; FE guide note for `bot_busy` queued for C17.

### Task C11b: Alerts helper

**Files:** Create `app/alerts.py`; Test `tests/test_alerts.py`; `tests/fakes/mailer.py` if A3 has none.

**Interfaces:** `def notify_user(session, user_id: int, *, kind: str, severity: str, title: str, body: str, dedupe_key: str, meeting_id: int | None = None, email_template: str | None = None) -> None`; `def notify_admins(session, *, kind: str, title: str, body: str, dedupe_key: str) -> None` (kind starts `admin.`; a row for every admin plus one `mail_outbox` row to `ADMIN_ALERT_EMAIL`, falling back to each admin's own email); `def bucket_key(kind: str, now: datetime, minutes: int = 30) -> str`.

- [ ] Tests first: `test_same_dedupe_key_writes_one_row`; `test_thirty_minute_bucket_dedupes_then_alerts_again_in_the_next_bucket`; `test_every_admin_gets_a_row_and_one_email_goes_out_per_bucket`; `test_mail_failure_never_raises_into_the_caller`; `test_admin_text_names_user_and_time_but_not_the_meeting_url`; `test_user_notify_respects_notify_email_false`.
- [ ] F. **Review:** Sonnet. **Needs:** A3, A18. **Done:** used by C12, C13, C14.

### Task C12: Join runner (dispatch, no-capacity retry, catch-up)

**Files:** Create `app/join_runner.py`; Modify `app/pipeline.py` (a meeting leaving `bot_sent` moves its join `joined` to `completed` in the same transaction), `app/scheduler.py` registration; Test `tests/test_join_runner.py`.

**Interfaces:**
- Consumes: C8, C11, C11b, C13's state helpers (a stub returning "connected" until C13).
- Produces: `def run_due_joins(session, vexa, calendar: CalendarSource, *, now: datetime) -> list[JoinOutcome]`, registered on the dispatch loop (10 s). Logic: select due rows (`run_at <= now <= deadline_at`, states `scheduled`, `no_capacity`, and `skipped` with a retryable reason) with `FOR UPDATE SKIP LOCKED`; one re-check at `starts_at - 120 s` (`rechecked_at`) via `calendar.get_event`; pre-flight: connection state, suspended user, then `place_bot`; map refusals: `hours_cap_reached` to `skipped/hours_cap` plus one notification; `already_in_meeting` to `skipped/duplicate_meeting`; `user_limit` to `skipped/user_limit` (both retried until the deadline); `bot_busy` to `no_capacity` with an owner notification, an admin alert, and a retry every 15 s using `updated_at`; past `deadline_at` any non-terminal row becomes `failed` with "Not recorded" text and an email. Success creates the `Meeting` with `source='calendar'`, `title`, the calendar template, sets `meeting_id` and `joined`.

- [ ] Tests first (injected clock): `test_dispatches_at_start_minus_lead`; `test_recheck_at_two_minutes_before_picks_up_a_move_and_a_cancel`; `test_scheduler_down_from_0955_to_1005_dispatches_at_restart_as_late_join`; `test_two_joins_one_slot_places_one_then_retries_the_other_every_15_s_until_deadline`; `test_no_capacity_alerts_owner_and_admin_once_per_bucket`; `test_deadline_makes_failed_row_with_not_recorded_text`; `test_runner_run_twice_at_the_same_instant_places_one_bot` (Review Focus 4); `test_join_for_a_connection_disconnected_a_moment_ago_sends_no_bot` and `test_suspended_user_join_becomes_skipped_account_suspended` and `test_rule_switched_off_while_dispatching_sends_nothing` (Review Focus 5); `test_joined_becomes_completed_in_the_same_transaction_as_the_meeting_leaving_bot_sent`; `test_completed_frees_the_meeting_code_for_the_next_weeks_event`.
- [ ] F, **full suite** (touches `pipeline.py`). **Review:** Sonnet per task, covered by the phase Opus pass. **Needs:** C8, C11, C11b. **Done:** an end-to-end test against `FakeGoogle`, `FakeVexaHost`, the fake clock runs a calendar meeting from sync to `completed`.

### Task C13: Connection states and refresh failures

**Files:** Create `app/calendar_states.py`; Modify `app/gcal_sync.py` (call it on errors); Test `tests/test_calendar_states.py`.

**Interfaces:** `def mark_needs_reconnect(session, conn, reason: str, now) -> int` (returns the count of that user's joins in the next 24 hours that are now skipped); `def mark_degraded(session, conn, now)`; `def mark_healthy(session, conn, now)`; `def revive_on_reconnect(session, conn, now) -> int`; `def expiry_warning(conn, *, testing: bool, now) -> datetime | None`.

- [ ] Tests first: `test_invalid_grant_sets_needs_reconnect_and_skips_calendar_joins_with_reason`; `test_reason_is_expired_when_expected_expiry_passed_else_revoked`; `test_transient_errors_under_thirty_minutes_change_nothing`; `test_thirty_minutes_of_errors_sets_degraded_and_one_warning_without_pausing_joins`; `test_reconnect_revives_joins_whose_deadline_has_not_passed_and_not_older_ones`; `test_decrypt_failure_sets_needs_reconnect_error_and_alerts_admin_without_crashing`; `test_expiry_warning_shows_under_24_hours_while_testing_flag_on` and `test_no_banner_and_no_expected_expiry_once_flag_off_even_for_connections_made_earlier` (the flag mirrors the project state; it is live while the project is in Testing).
- [ ] F. **Review:** Sonnet. **Needs:** C3, C10, C11b. **Done:** a synced `FakeGoogle` run that starts failing walks `connected` to `degraded` to `needs_reconnect` and back.

### Task C14: Disconnected notifications, banner data, email

**Files:** Modify `app/calendar_states.py`, `app/alerts.py` templates; Create the `calendar_disconnected` mail template beside the others from `Emails.dc.html`; Test `tests/test_calendar_states.py`.

- [ ] Tests first: `test_one_notification_and_one_email_per_disconnect` (dedupe `cal_disc:{connection_id}:{connected_at}`); `test_banner_text_counts_next_24_hour_meetings`; `test_admin_told_when_more_than_one_user_disconnects_within_an_hour`; `test_email_skipped_when_notify_email_false`.
- [ ] F. **Review:** Sonnet. **Needs:** C13, A18, A3. **Done:** `FakeMailer` shows exactly one email.

### Task C15a: Calendar API, part 1 (state, settings, upcoming, override)

**Files:** Modify `app/calendar_api.py`; Create `app/calendar_text.py`; Modify `app/auth.py` route classification, `docs/openapi.json`; Test `tests/test_calendar_api.py`, `tests/test_calendar_text.py`, `tests/test_api.py` (enumeration).

**Interfaces:** Routes per spec 13: `GET /api/calendar`, `PUT /api/calendar/settings` (rule, `calendar_template_id`, `join_instant_meets`; 409 `consent_required` with `authorize_url`), `GET /api/calendar/upcoming`, `PUT /api/calendar/events/{id}/override` (409 `not_joinable`). `def state_text(state: str, reason: str | None, ctx: TextContext) -> str` in `app/calendar_text.py` (the **server owns the words**; moved here from C8, see deviations). States: `will_join`, `off`, `cannot_join`, `declined`, `hours_used`, `duplicate`, `disconnected`, `waiting_for_capacity`, `in_progress`, `done`, `not_recorded`.

- [ ] Tests first: `test_every_state_and_reason_has_plain_words_and_no_codes`; `test_will_join_text_carries_the_tell_participants_reminder`; `test_upcoming_window_is_next_seven_days_plus_last_24_hours`; `test_foreign_event_id_is_404_for_override` and the enumeration test covers every new id route; `test_rule_change_clears_overrides_on_events_not_started`; `test_override_on_declined_or_ended_or_teams_flag_off_is_409_not_joinable`; `test_emoji_title_round_trips_through_upcoming` (Review Focus 3); `test_upcoming_never_returns_attendee_addresses_description_or_zoom_passcode`; `test_turning_on_instant_meets_without_meet_scope_returns_consent_required` (the Meet part lights up in I1; the code path for calendar rule without Calendar scope lands here).
- [ ] F, run `scripts/export_openapi.py`. **Review:** Sonnet. **Needs:** C8, C12, C13. **Done:** captured by the Phase C capture script in C17.

### Task C15b: Calendar API, part 2 (send now, not recorded)

**Files:** Modify `app/calendar_api.py`; Test `tests/test_calendar_api.py`.

**Interfaces:** `POST /api/scheduled-joins/{id}/send-now`, `GET /api/not-recorded` (last 7 days). Send-now rules per spec 7.7: allowed while `now <= ends_at` in states `failed`, `no_capacity`, `skipped` except `account_suspended`; re-runs the **full** pre-flight; sets `deadline_at = now + 15 min`.

- [ ] Tests first: `test_send_now_reruns_hours_cap_dedupe_and_capacity_never_bypasses`; `test_send_now_after_meeting_end_is_409`; `test_send_now_on_suspended_is_refused`; `test_not_recorded_lists_failed_no_capacity_and_the_listed_skip_reasons_for_seven_days_only`; `test_foreign_join_id_is_404`; `test_dispatched_then_failed_is_a_meeting_row_not_a_not_recorded_row`.
- [ ] F. **Review:** Sonnet. **Needs:** C12, C15a. **Done:** both routes in the enumeration test.

### Task C16: Deploy gate

**Files:** Create `deploy/gate.sh` (function `join_due_soon`), `deploy/deploy-gate-check.sh` (test, same style as `deploy/nginx-limit-check.sh`); Modify `deploy/deploy.sh`, `deploy/README.md`.

**Interfaces:** `join_due_soon` exits 0 when no scheduled, `no_capacity` or `dispatching` join has `run_at <= now()+10 min` and `deadline_at >= now()`, nonzero otherwise; `deploy.sh` refuses with a clear line unless the environment variable `ALLOW_DEPLOY_WITH_JOINS_DUE=1` is set on the server (the CD key uses a forced command, so a flag cannot be passed from CI). The existing `bot_sent`/`processing` refusal stays.

- [ ] Test first: the check script seeds a scratch Postgres with joins at -1 h, +5 min, +20 min and asserts the exit code for each, and for the override variable.
- [ ] F: run the check script locally; `shellcheck` if available. **Review:** Sonnet, plus a read of the diff by the lead (CD runs this file). **Needs:** C10. **Done:** a dry-run of `deploy.sh` on a Mac compose stack with a due join refuses, then passes with the override.

### Task C17: Phase C close-out (policy, SOP, checklist, FE guide, live capture)

**Files:** Modify `app/templates/privacy.html` and the `web/` copy (spec 12.3 rows "Google user data, Calendar" and "How long we keep, calendar"); Create `scripts/capture_pilot_calendar.py`, `docs/fe-captures/pilot-accounts-calendar/` (calendar captures), `docs/fe-integration-guide-pilot-accounts-calendar.md` (calendar section; Phase A's section may already exist: extend it), `docs/sop/2026-11-xx-pilot-calendar-auto-join.md`; update the checklist.

- [ ] Run the capture script against the running app with Adebayo's test Gmail: connect state, upcoming, override, send-now, not-recorded, disconnected state (revoke at Google), `bot_busy`. Paste real bodies; label anything not exercised ("7-day expiry: not verified live" until P0.9 step 4 has a day-8 result; the unverified-app screen comes from P0.9's screenshots).
- [ ] SOP covers the vault key backup warning (losing `TOKEN_ENC_KEY` forces every user to reconnect) and rotation with C9.
- [ ] Opus reviews the whole phase diff once. **Done:** guide verification table filled, CI green, lead told what Adebayo must do on the server (Needs rows 14 and 15).

### Task C18: Recording notice

**Files:** Create `app/recording_notice.py`; Modify `app/join_runner.py`, privacy and terms (the "Recording and participant notice" row of 12.3); Test `tests/test_recording_notice.py`.

**Interfaces:** `def first_name(full_name: str) -> str` (first word, letters, spaces and hyphens only, cut at 30 characters, empty gives "the meeting host"); `def notice_line(full_name: str) -> str` ("Beyric Note Taker is recording and transcribing this meeting for {first name}. If you do not agree, ask them to remove the bot."); the runner posts it after a successful dispatch **only if P0.3 says Vexa can post chat**; a post failure never fails the join.

- [ ] Tests first: `test_first_name_rule_table` (emoji, digits, empty, 60 characters, hyphenated name); `test_chat_line_posted_once_after_dispatch`; `test_chat_failure_does_not_fail_the_join_and_is_not_retried_endlessly`; `test_no_chat_when_capability_off`.
- [ ] If P0.3 says chat is impossible: the task shrinks to the policy sentence "the person whose account sent it is responsible for telling participants" plus the in-app reminder (already in C15a), and nothing claims a chat line.
- [ ] F. **Review:** Sonnet. **Needs:** P0.3, C12. **Done:** the policy sentence matches what runs. (Spec 12.3 maps this row to I7; it moves here because calendar joins are the first auto-joins.)

---

## Phase C-T: Teams (Q17, new from 0a)

Gate: P0.7 passed. If it did not, T1 to T3 are dropped, `TEAMS_JOIN_ENABLED` stays false, and the policy and terms stay as they are.

### Task T1: Teams links from a pasted URL

**Files:** Modify `app/vexa.py` (`_parse` handles the Teams hosts and formats recorded in `docs/spike/pilot-teams.md`; `stop_bot` platform allow-list at `app/vexa.py:200`; request body for Teams per the spike); Test `tests/test_vexa.py`, `tests/test_api.py`.

**Interfaces:** `parse_meeting_url(url) -> (platform, native_id)` returns `("teams", <id>)` for each recorded Teams format; canonical URL keeps the passcode only as the spike requires; unsupported Teams shapes still raise `UnsupportedMeetingUrl`.

- [ ] Tests first: one case per recorded format (copied from the spike, passcode placeholders); `test_teams_url_with_userinfo_or_port_is_rejected`; `test_stop_bot_accepts_teams`; `test_pasted_teams_link_creates_a_meeting_with_platform_teams_and_calls_send_bot_with_the_spike_body`; `test_two_spellings_of_one_teams_meeting_give_one_native_id`; `test_passcode_never_in_logs`.
- [ ] F, **full suite** (shared `app/vexa.py`). **Review:** Sonnet. **Needs:** P0.7, A7. **Done:** pasted Teams link joins the Adebayo-hosted meeting live.

### Task T2: Teams events from the calendar

**Files:** Modify `app/gcal_events.py` (fill `native_id` for Teams using T1's parser), `app/config.py` (default `teams_join_enabled` per P0.7's decision line), `app/calendar_text.py` (the "Can't join yet" wording applies only when the flag is off); Test `tests/test_gcal_events.py`, `tests/test_gcal_rules.py`, `tests/test_calendar_text.py`.

- [ ] Tests first: `test_teams_event_native_id_filled`; `test_teams_event_joinable_only_when_flag_on` (reuses the C7 matrix rows); `test_cant_join_yet_text_only_when_flag_off`; `test_calendar_teams_join_dispatches_with_platform_teams` (runner with `FakeVexaHost`).
- [ ] F. **Review:** Sonnet. **Needs:** T1, C7, C12, C15a. **Done:** live: a calendar event with a Teams link is joined at start minus the lead.

### Task T3: Teams wording in policy, terms, landing

**Files:** Modify `app/templates/privacy.html`, `terms.html`, and the `web/` copies, plus the landing text per V0; Test `web` legal-text equality test.

- [ ] Add the sentence P0.7's decision line fixed ("Zoom, Google Meet and Microsoft Teams") to the places that list platforms; Microsoft is **not** added to "who we share with" unless a Microsoft service receives data (Vexa's bot joins a Microsoft meeting; no Microsoft API is called by us). Outlook wording follows C24, never T3.
- [ ] F with the web checks. **Review:** Sonnet. **Needs:** T2 live. **Done:** policy diff is only claims the app now meets.

---

## Phase C (continued): paste-a-link calendar source, C19 to C24

**IN for the pilot (Adebayo, 2026-11-03: "Yes, add ICS").** Google OAuth (C1 to C18) stays the primary source. This block adds a second **source** behind the same `CalendarSource` protocol (C5), so rules, materialiser, scheduler, runner and API (everything after parsing) are shared. It is how **Outlook, Microsoft 365 and any other calendar that can publish an ICS link** reach the pilot: no Microsoft OAuth app is built (do not add one). The user pastes a secret address (Google: "Secret address in iCal format"; Outlook on the web: "Publish a calendar", "Can view all details", ICS link). It needs **no Google review**, so it also serves testers who refuse the unverified-app screen. It is slower than OAuth, and the UI says so: **"Updates can take a while; meetings made just before they start may be missed."** Background and Vexa's own behaviour (hosted poll 60 s, self-hosted 5 min, next occurrence only for a recurring series, 2 MB feed cap): `docs/spike/vexa-calendar-sync-ics.md` on PR #27 (`docs/followups-and-vexa-calendar-note`). We do **not** use Vexa's calendar API: our scheduler owns the hours cap, capacity, ownership and rules, so Vexa stays the bot layer only.

**Rules for this block:** one calendar source per user in v1 (a Google connection that has only the Meet scope may coexist with an ICS calendar, because it is not a calendar source); ICS carries no instant-Meet (those need OAuth, Phase I); the user must give their own email so the organiser and reply rules can work; a recurring series is **expanded by us** inside the standard window, not "next occurrence only". Earlier drafts called these tasks `C-ICS.0` to `C-ICS.5`.

**Consequences for Q4 and for claims:** see "Outlook is no longer 'later'" after Phase V; no Outlook wording may reach the app, the policy or the landing page before C24 is live and verified.

### Task C19: Staleness spike (30 minutes)

**Dependencies:** a test Gmail **and an Outlook.com account** (Needs rows 8 and 11); the user's own Google calendar is enough for the Google half. **Unblocks:** C20 (allowlist from the real hosts), C21 (real fixtures), and the exact wording of the freshness label. **Output:** `docs/spike/pilot-ics-staleness.md`.

- [ ] **Step 1 (Google secret feed):** copy the "Secret address in iCal format" URL into a local, git-ignored file. Poll it every 30 s with a script recording HTTP status, `ETag`/`Last-Modified` if sent, size, and whether a given UID is present.
- [ ] **Step 2:** create an event with a Meet link (note the wall-clock second) and record how long until its UID appears. Repeat for: move an event by 15 minutes, delete an event, add a Meet link to an existing event. Three runs each.
- [ ] **Step 3 (Outlook publish):** publish the Outlook.com calendar (ICS link, "Can view all details") and repeat step 2. Record the real property Outlook uses for a Teams link, the host name of the feed, the feed size for a busy calendar, and whether a Microsoft 365 work tenant offers the Publish option at all (ask one work user; record "blocked", "allowed" or "not asked").
- [ ] **Step 4:** write the table (source, event change, run 1, run 2, run 3, max) and one plain conclusion per source: the **latest** an event may be created or moved before its start and still be caught at dispatch (start minus lead). Fix the exact label text from it.

**Done:** note committed with the poll logs summarised and the real feed hosts listed; no secret URL in git. If an Outlook account was not available, say "Outlook: not measured" and C20 keeps its default hosts flagged unverified.

### Task C20: SSRF-safe fetcher

**Files:** Create `app/ics_fetch.py`; Modify `app/config.py` (`ics_allowed_hosts`, default `calendar.google.com,outlook.office365.com,outlook.live.com`, replaced by the hosts C19 recorded); Test `tests/test_ics_fetch.py`.

**Interfaces:** `def fetch_ics(url: str, *, resolver=socket.getaddrinfo, transport=None) -> bytes`; raises `IcsRejected(reason: str)` with reasons `scheme`, `host_not_allowed`, `private_address`, `redirect`, `too_large`, `timeout`, `bad_status`, `not_calendar`; `def reason_text(reason: str) -> str` gives the plain words the user sees (used by C22's first-sync result).

Rules: `https` only; host must be in the allowlist (exact or listed suffix; an admin adds hosts through `ICS_ALLOWED_HOSTS`, never a user); resolve once, reject loopback, private, link-local, multicast, reserved and the cloud metadata address (IPv4 and IPv6), then **connect to the resolved IP** with the original `Host` and SNI so a second DNS answer cannot redirect it; `follow_redirects=False`, any 3xx is `redirect`; response streamed and aborted at **2 MB**; connect timeout 3 s, total 10 s; the body must start with `BEGIN:VCALENDAR`.

- [ ] Tests first (fake resolver and `MockTransport`): `test_http_scheme_rejected`; `test_host_outside_allowlist_rejected`; `test_allowlisted_host_resolving_to_127_0_0_1_10_x_169_254_169_254_and_ipv6_loopback_rejected`; `test_dns_rebinding_second_answer_is_not_used` (resolver returns public then private; the connection uses the first); `test_redirect_to_internal_host_not_followed`; `test_body_over_two_megabytes_aborts_early`; `test_slow_response_times_out`; `test_non_calendar_body_rejected`; `test_url_never_appears_in_error_or_log` (a secret URL is redacted to its host); `test_every_reason_has_plain_words_and_no_code`.
- [ ] F. **Review: Opus** (my call: the URL is a bearer secret and this is an SSRF surface). **Needs:** none (can start with C2). **Done:** every rejection reason has a test.

### Task C21: ICS parser with our own recurrence expansion

**Files:** Create `app/ics_parse.py`; `pyproject.toml` (add `icalendar` and `recurring-ical-events`, pinned; check licences); Test `tests/test_ics_parse.py`, `tests/fixtures/ics/*.ics` (from the real feeds in C19, names and addresses replaced).

**Interfaces:** `def parse_feed(data: bytes, *, user_email: str, now: datetime) -> list[ParsedEvent]` (the C6 dataclass). Expands every recurring series into instances inside the standard window (now minus 1 hour to now plus 14 days), applying `EXDATE` and per-instance overrides (`RECURRENCE-ID`); link priority: a conference property if the feed has one (Google's `X-GOOGLE-CONFERENCE`, Outlook's Teams property found in C19), then a Meet, Zoom or Teams URL in `LOCATION` or `DESCRIPTION`; `UID` plus `RECURRENCE-ID` becomes `source_event_id`; organiser and the user's reply come from `ORGANIZER` and the `ATTENDEE` whose address equals `user_email` (no match gives `None`, and `will_join` under rule `organizer` treats `organizer_self=None` as not joined with a plain reason); floating times and `TZID` convert to UTC.

- [ ] Tests first: `test_weekly_series_yields_each_instance_in_window_not_just_the_next` (the Vexa difference); `test_exdate_and_cancelled_instance_dropped`; `test_moved_single_instance_overrides_the_series_slot`; `test_tzid_and_floating_times_become_utc` (Review Focus 2) with a DST-crossing series; `test_meet_zoom_and_teams_links_found_in_property_location_and_description`; `test_organizer_and_self_response_from_user_email`; `test_unknown_user_email_gives_none_not_false`; `test_malformed_feed_raises_a_named_error_not_a_crash`; `test_description_and_attendee_addresses_not_kept_on_the_parsed_event`; `test_ten_year_daily_series_is_capped_by_the_window`.
- [ ] F. **Review:** Sonnet. **Needs:** C6, C19 (real fixtures; synthetic until then). **Done:** fixtures from at least one real Google feed and one Outlook feed (or "Outlook: synthetic" stated).

### Task C22: Connection model, service and first-sync result

**Files:** Create `alembic/versions/<next>_calendar_ics.py` (adds `calendar_connections.ics_url_enc bytea`, `ics_url_hint text`, `ics_account_email text`; the `provider` check already allows `ics` from C1), `app/calendar_ics.py`; Test `tests/test_calendar_ics.py`, `tests/test_migrations.py`.

**Interfaces:**
- `@dataclass(frozen=True) FirstSyncResult(ok: bool, events_seen: int, joinable: int, error: str | None, error_text: str | None)` (counts on success; on failure the **named** reason from C20/C21 and its plain words; Vexa shows the same on connect, and paste mistakes are the main support cost).
- `def connect_ics(session, user, url: str, account_email: str, *, fetch=fetch_ics) -> tuple[CalendarConnection | None, FirstSyncResult]` (fetches and parses once; **stores nothing on failure**; on success stores `vault.encrypt(url, purpose="ics_url", owner=user.id)`, and `ics_url_hint` is the **host only**).
- `def disconnect_ics(session, user) -> None` (same deletion list as C4); `def reveal_url(conn) -> Secret`.

- [ ] Tests first: `test_url_stored_encrypted_and_hint_is_host_only`; `test_api_never_returns_the_url_or_its_tail` (masked on read); `test_failed_first_sync_stores_nothing_and_names_the_error` (one case per C20 reason plus a malformed feed); `test_first_sync_counts_events_and_joinable`; `test_disconnect_deletes_events_joins_and_url`; `test_url_for_other_user_fails_to_decrypt`; `test_second_calendar_source_for_the_same_user_is_refused_but_a_meet_only_google_connection_may_coexist`; `test_url_not_in_captured_logs`; `test_changing_the_link_replaces_the_old_one_and_resyncs` (a leaked link is rotated at the provider and re-pasted).
- [ ] F with the migration round trip. **Review:** Sonnet (the vault was Opus-reviewed in C2). **Needs:** C2, C20, C21.

### Task C23: ICS source in the sync loop and the API

**Files:** Modify `app/calendar_source.py` (add `IcsSource`), `app/gcal_sync.py` (pick the source by `conn.provider`), `app/calendar_api.py`, `app/calendar_text.py`; Test `tests/test_calendar_api.py`, `tests/test_gcal_sync_job.py`, `tests/test_calendar_text.py`.

**Interfaces:** `class IcsSource(CalendarSource)` (full fetch every sync, 5 minutes; `get_event` re-reads the cached feed and refetches at most once per connection per 60 s); routes `POST /api/calendar/ics` (body `url`, `account_email`; CSRF; answers with the `FirstSyncResult`), `PUT /api/calendar/ics` (change the link), `DELETE /api/calendar/ics`; `GET /api/calendar` gains `source` (`google` or `ics`), `ics_host`, and `freshness_note` (the label text from `calendar_text.py`: "Updates can take a while; meetings made just before they start may be missed.", present for `ics` only).

- [ ] Tests first: `test_ics_sync_materialises_joins_exactly_like_google` (same fixtures through both sources, same `scheduled_joins`); `test_feed_error_marks_degraded_not_disconnected_and_keeps_joins`; `test_recheck_does_not_refetch_more_than_once_a_minute`; `test_freshness_note_only_for_ics`; `test_post_with_a_bad_link_returns_the_named_error_and_no_connection`; `test_routes_in_enumeration_and_foreign_ids_404`; `test_ics_user_cannot_turn_on_instant_meets_without_a_google_connection` (consent route used instead).
- [ ] F, run `scripts/export_openapi.py`. **Review:** Sonnet. **Needs:** C22, C8b, C12, C15a.

### Task C24: Phase C ICS close-out

**Files:** privacy and terms pairs (new sentence: "If you connect a calendar by pasting its private link, we store that link encrypted, read only the event details described above, and delete it when you disconnect"; the "Meeting and calendar platforms" line names a calendar provider only if its host receives data from us, which a pasted link to Outlook does not, so **no Microsoft entry is added to "who we share with"**); SOP; checklist; FE guide section (live captures from a real Google feed and an Outlook feed, or the missing one labelled "not verified live"); a designer request for a "paste a link" card on Settings → Calendar (Needs row 13).

- [ ] **Done:** captures from a real feed; the privacy diff contains only what the app does; the Outlook wording below is changed in the same PR **only** if an Outlook feed was verified live, else Outlook stays out of every claim and the designer card says "Add a calendar link".

---

## Phase I: instant-Meet auto-join

Spec section 8. **Gate: P0.2's outcome G1 to G4** (table in P0.2). Which tasks run is decided by that outcome, not here. The Settings switch (Q11) is drawn by the designer; the API is built first.

### Task I1: Incremental Meet consent, mode resolution, switch

**Files:** Create `app/meet_mode.py`; Modify `app/calendar_connect.py` (feature `meet` requests `https://www.googleapis.com/auth/meetings.space.readonly` with `include_granted_scopes=true`), `app/calendar_api.py` (`PUT /api/calendar/settings` `join_instant_meets`, 409 `consent_required`), `app/config.py` (`instant_meet_mode`, `instant_meet_personal_mode` per P0.2); Test `tests/test_meet_mode.py`, `tests/test_calendar_api.py`.

**Interfaces:** `def resolve_instant_mode(*, global_mode: str, personal_mode: str, hosted_domain: str | None, override: str | None, granted_features: set[str]) -> str` returning `events`, `poll` or `off`; the switch is hidden (`mode == "off"`) in `GET /api/calendar`.

- [ ] Tests first: a table `test_mode_resolution_matrix` over global (`events`, `poll`, `off`, `auto`) × hosted domain (present, absent) × override × meet-granted; `test_meet_only_connection_works_without_calendar_scope`; `test_turning_on_without_scope_returns_consent_required_with_authorize_url`; `test_switch_stays_on_once_granted`; `test_mode_off_hides_the_switch_and_refuses_enabling`.
- [ ] F. **Review:** Sonnet. **Needs:** C3, P0.2. **Done:** live with the personal Gmail: toggle triggers the real incremental consent; scopes shown on Google's screen equal exactly the Meet scope.

### Task I2: Pub/Sub inbox and push endpoint

**Files:** Create `alembic/versions/<next>_pubsub_inbox.py`, `app/pubsub.py`; Modify `app/api.py` (route `POST /api/webhooks/google/pubsub`), `app/auth.py` (own-authentication set), `app/sweeps.py` (7-day sweep), `deploy/nginx.conf` (zone 20 r/s, burst 40; 1 MB body limit on this location), `deploy/nginx-limit-check.sh`, `pyproject.toml` (`google-auth`); Test `tests/test_pubsub.py`, `tests/test_api.py` (enumeration), `tests/test_migrations.py`.

**Interfaces:** `def verify_push_token(authorization: str | None, *, audience: str, service_account: str, certs_fetcher=...) -> None` (raises `PushAuthError`); the route verifies, inserts `pubsub_inbox(message_id, payload, outcome=null)` with `ON CONFLICT DO NOTHING`, returns 204, and does nothing else.

- [ ] Tests first: `test_missing_malformed_expired_wrong_audience_wrong_email_unverified_email_tokens_all_401_and_insert_nothing`; `test_valid_token_inserts_and_returns_204`; `test_duplicate_message_id_is_one_row`; `test_body_over_one_megabyte_rejected_before_parsing`; `test_authenticated_but_malformed_body_is_stored_as_error_and_still_204` (so Pub/Sub does not retry forever); `test_identity_is_never_read_from_the_body`; `test_no_token_or_payload_in_logs`; `test_route_is_listed_as_own_auth_in_the_enumeration_test_and_nothing_else_is_public_by_accident`; `test_nginx_limit_check_covers_the_new_zone`.
- [ ] F, **full suite** (auth classification, nginx). **Review: Opus** (the most exposed new route). **Needs:** P0.2, C10b. **Done:** live: with Adebayo's `sudo nginx -t && sudo systemctl reload nginx` done, a real push from a test topic to the production URL lands one `pubsub_inbox` row; a forged request gets 401.

### Task I3: Meet client and subscription manager

**Files:** Create `app/meet_client.py`, `app/meet_subscriptions.py`; Modify `app/calendar_connect.py` (`ON_DISCONNECT` hook deletes the subscription), `tests/fakes/google.py` (Events and Meet endpoints copied from P0.2 captures); Test `tests/test_meet_subscriptions.py`.

**Interfaces:** `MeetClient`: `create_subscription(token, user_id_for_target)`, `list_subscriptions`, `delete_subscription`, `get_conference_record(name)`, `get_space(name)`, `list_conference_records(filter)`. Manager: `def ensure_subscription(session, conn, *, now)`, `def reconcile(session, *, now)` (every 6 hours: renew when within 24 hours of `expireTime`, recreate missing ones, set `meet_subscription_state` to `needs_reconnect` on suspension).

- [ ] Tests first: `test_turning_switch_on_creates_one_subscription_and_stores_name_and_expiry`; `test_reconcile_renews_inside_24_hours_and_recreates_missing`; `test_suspension_sets_needs_reconnect_and_raises_the_same_banner_as_calendar`; `test_disconnect_deletes_the_subscription`; `test_meet_failure_does_not_change_calendar_status` (separate flag).
- [ ] F. **Review:** Sonnet. **Needs:** I1, P0.2 (target id format, expiry). **Done:** uses the lifetime S6 found, or the 6-hour reconcile covers it.

### Task I4: Inbox processor and ownership rule

**Files:** Create `app/meet_inbox.py`; Modify `app/scheduler.py` registration; Test `tests/test_meet_inbox.py`.

**Interfaces:** `def process_inbox(session, meet: MeetClient, *, now) -> list[InboxOutcome]` (dispatch loop). For each unprocessed row (`FOR UPDATE SKIP LOCKED`): resolve the user from the **stored subscription name**, look up conference then space (meeting code), then: calendar-first rule (same code in the user's cached events within a day: outcome `ignored`, the calendar path governs); owner rule (drop anything the subscription cannot prove the user owns, per S9); live-dedupe; create `scheduled_joins` with `source='instant_meet'`, `run_at=now`, `deadline_at=start+15 min`; set `outcome` and `processed_at`.

- [ ] Tests first: `test_unknown_subscription_is_unmatched_and_counted`; `test_calendar_meeting_code_is_ignored_instant_path_does_nothing`; `test_meeting_user_does_not_own_is_ignored` (an invitee-style event); `test_already_live_code_is_ignored`; `test_matched_event_creates_one_join_that_the_runner_dispatches_now`; `test_two_processors_one_row_one_join`; `test_crash_after_insert_before_processed_reprocesses_without_a_second_join`; `test_user_without_instant_meets_switch_ignored`.
- [ ] F. **Review:** Sonnet, in the phase Opus pass. **Needs:** I2, I3, C12. **Done:** live: meet.new from the test Gmail gets a bot within about a minute, recorded in the P0.2 note's terms.

### Task I5: `poll` mode (only if P0.2 outcome G2 or G3)

**Files:** Create `app/meet_poll.py`; Modify scheduler registration; Test `tests/test_meet_poll.py`.

**Interfaces:** `def poll_instant_meets(session, meet, *, now)` every 30 s per connected user with the switch on and resolved mode `poll`: `conferenceRecords.list` with `end_time IS NULL`; a record becomes a join only if the owner is provable under the S5 finding; otherwise the user's mode resolves to `off`.

- [ ] Tests first: `test_new_record_for_owner_creates_a_join`; `test_record_the_user_only_joined_is_ignored_per_s5`; `test_polling_stops_when_switch_turned_off_or_connection_needs_reconnect`; `test_same_record_polled_twice_is_one_join`; `test_per_minute_quota_respected_for_ten_users`.
- [ ] F. **Review:** Sonnet. **Needs:** I1, C12, P0.2 S5 and S7. **Done:** the policy row for `poll` wording (spec 12.3) is in I7.

### Task I6: Manual routes

**Files:** Create `app/meet_manual.py`; Modify `app/calendar_api.py` (`POST /api/meetings/start-instant`, only if `spaces.create` works per P0.2); `docs/` note for the PWA share target (the Next screen is stream D); Test `tests/test_meet_manual.py`.

**Interfaces:** `def start_instant_meeting(session, meet, user) -> Meeting` creates a Meet space (`meetings.space.created`, only if P0.1 says approved), places the bot via `place_bot`, and returns the Meet link; refused with a plain message when creation is not available for the account type.

- [ ] Tests first: `test_creates_space_and_places_bot_in_one_call`; `test_creation_unavailable_returns_plain_message_not_a_crash`; `test_scope_missing_returns_consent_required`; `test_hours_cap_and_busy_refusals_apply`.
- [ ] Write `docs/pilot-pwa-share-target.md` (manifest `share_target` fields, the existing `POST /api/meetings` it calls) for the designer and stream D.
- [ ] F. **Review:** Sonnet. **Needs:** I1, C11. **Done:** in outcome G4 only the note is delivered.

### Task I7: Phase I close-out

Policy rows "Meet" (spec 12.3: events wording or poll wording) and "Who we share with" (Google Cloud Pub/Sub, events mode only); SOP; checklist; FE guide section with real captures (consent, switch, subscription state, a triggered join); Opus review of the phase diff. **Done:** policy names only what the live mode does; a claim for a mode that is not running is absent.

---

## Phase D: several bots at once

Spec section 9. Phase C already built `place_bot()` and the whole no-capacity path for one host; Phase D generalises it. Do not invite more than one or two testers until D9b passes.

### Task D1: Migration for `bot_hosts`

**Files:** Create `alembic/versions/<next>_bot_hosts.py` (seeds host `local` from `VEXA_BASE_URL`, `api_key_enc` null, `max_bots` from `BOT_MAX_LOCAL`; adds `meetings.bot_host_id` backfilled to `local`), `app/models.py`; Test `tests/test_migrations.py`.

- [ ] Tests first: `test_seed_row_local_has_null_key_and_max_bots_from_setting`; `test_every_existing_meeting_backfilled_to_local`; `test_round_trip`; `test_host_with_meetings_cannot_be_deleted` (FK restrict).
- [ ] F with the migration round trip. **Review:** Sonnet. **Needs:** C11. **Done:** upgrade on a DB with meetings keeps all of them.

### Task D2: `vexa_for(host)` and per-host keys on every pipeline path

**Files:** Create `app/hosts.py`; Modify `app/services.py` (`get_vexa` becomes `vexa_for`), `app/worker.py`, `app/pipeline.py`, `app/placement.py`; Test `tests/test_hosts.py`, `tests/test_pipeline.py`.

**Interfaces:** `def vexa_for(host: BotHost) -> VexaClient` (null key uses the env key; otherwise `vault.decrypt(host.api_key_enc, host.key_version, purpose="bot_host_key", owner=host.name)`).

- [ ] Tests first: `test_meeting_on_host_b_polls_downloads_deletes_and_stops_through_host_b` (two `FakeVexaHost`); `test_local_host_uses_env_key`; `test_decrypt_failure_marks_host_unhealthy_not_crash`; `test_retry_goes_to_the_same_host_as_the_meeting`.
- [ ] F, **full suite** (pipeline). **Review:** Sonnet. **Needs:** D1. **Done:** one-host behaviour unchanged (all existing pipeline tests pass).

### Task D3: Health checker

**Files:** Create `app/host_health.py`; Modify scheduler registration; Test `tests/test_host_health.py`.

**Interfaces:** `def check_hosts(session, clients, *, now)` every 30 s with a 3-s timeout against the endpoint P0.3 chose; sets `last_health_at`, `last_health_ok`; an unhealthy host raises one admin alert (bucketed).

- [ ] Tests first: `test_healthy_host_updated`; `test_timeout_marks_unhealthy_and_alerts_admin_once_per_bucket`; `test_slow_host_does_not_delay_the_others`; `test_host_counts_as_healthy_only_if_seen_in_last_two_minutes`.
- [ ] F. **Review:** Sonnet. **Needs:** D1, P0.3. **Done:** works against `FakeVexaHost` with an injected failure.

### Task D4: `place_bot()` generalised

**Files:** Modify `app/placement.py`, `app/api.py` (pasted link at capacity now 503 `no_capacity` with the plain text; `bot_busy` retired), `docs/openapi.json`; Test `tests/test_placement.py`.

**Interfaces:** same `place_bot` signature; the lock is `pg_advisory_xact_lock(PLACEMENT_LOCK, host.id)`; candidates are `enabled`, not `draining`, healthy within 2 minutes; active bots on a host = meetings with that `bot_host_id` and `status='bot_sent'`, cross-checked with Vexa's bot list if P0.3 found one (trust the larger); the host with the most free slots wins (ties: lowest id); a failed send marks the host unhealthy for 2 minutes and tries the next; up to 3 tries 15 s apart across hosts before the join fails.

- [ ] Tests first: `test_picks_host_with_most_free_slots_ties_lowest_id`; `test_draining_and_disabled_hosts_get_no_new_bots_but_keep_existing_meetings`; `test_unhealthy_host_skipped`; `test_failover_to_next_host_when_send_fails`; `test_per_host_lock_one_slow_host_does_not_block_another` (threads); `test_two_requests_one_free_slot_across_two_hosts_exactly_one_per_slot`; `test_larger_of_db_count_and_vexa_list_wins`; `test_all_hosts_fail_creates_no_meeting`; `test_pasted_link_at_capacity_is_503_no_capacity`.
- [ ] F, **full suite**. **Review: Opus** (dispatcher locking). **Needs:** D2, D3. **Done:** the C12 no-capacity tests still pass unchanged.

### Task D5: Per-user limit across hosts

**Files:** Modify `app/placement.py`; Test `tests/test_placement.py`, `tests/test_join_runner.py`.

- [ ] Tests first: `test_user_with_two_live_bots_on_two_hosts_gets_user_limit_for_the_third`; `test_limit_counts_all_hosts`; `test_user_limit_join_retries_until_deadline_then_fails_with_plain_text`; `test_limit_is_the_setting_not_a_literal`.
- [ ] F. **Review:** Sonnet. **Needs:** D4. **Done:** `USER_BOT_LIMIT=2` exercised.

### Task D6: Forecast job and capacity endpoint

**Files:** Create `app/forecast.py`; Modify `app/calendar_api.py` or the admin router from A16 (`GET /api/admin/capacity`); Test `tests/test_forecast.py`.

**Interfaces:** `def forecast(session, *, now) -> list[ForecastWarning]` every 5 minutes: for each of the next 60 minutes, joins live at once against the sum of `max_bots` of healthy hosts; a peak above capacity raises one admin alert 60 minutes ahead ("At 10:00 you will have 4 bots and capacity for 2"), bucketed. `GET /api/admin/capacity` returns hosts, active bots, free slots, the forecast, the scheduler heartbeat, and the count of `unmatched` instant-Meet events.

- [ ] Tests first: `test_overlapping_joins_beyond_capacity_warn_once`; `test_back_to_back_meetings_do_not_overlap`; `test_unhealthy_hosts_reduce_capacity`; `test_capacity_endpoint_admin_only_403_for_users`; `test_heartbeat_older_than_five_minutes_flagged`.
- [ ] F. **Review:** Sonnet. **Needs:** D4, A16. **Done:** a seeded day shows the warning at the right minute on the fake clock.

### Task D7: Admin host endpoints

**Files:** Modify the admin router (A16); Test `tests/test_admin_hosts.py`, `tests/test_api.py`.

**Interfaces:** `POST /api/admin/hosts` (name, base_url, api_key, max_bots; the key is encrypted at insert with the host name as owner), `PATCH /api/admin/hosts/{id}` (`max_bots`, `enabled`, `draining`, key rotation); `DELETE` refused with 409 while meetings reference it. Keys are never returned.

- [ ] Tests first: `test_non_admin_403_on_every_host_route`; `test_key_stored_encrypted_and_never_returned`; `test_draining_stops_new_placements`; `test_delete_with_meetings_is_409`; `test_base_url_must_be_https_or_private_address` (reject public plain http).
- [ ] F. **Review:** Sonnet. **Needs:** D1, A16. **Done:** routes in the enumeration test.

### Task D8: Second host files

**Files:** Create `deploy/vexa-host/compose.yaml` (the `vexa`, `vexa-db`, `vexa-storage` and init services copied from `compose.yaml`, no public ports, bound to the private-link address), `deploy/vexa-host/.env.example`, `deploy/vexa-host/README.md` (install steps, WireGuard or Tailscale, firewall, API key creation with the existing `vexa-api-key.sh`, how to register it with `POST /api/admin/hosts`, how to drain).

- [ ] Verify locally: `docker compose -f deploy/vexa-host/compose.yaml config` is valid; a local Docker run of the stack serves the health endpoint chosen in P0.3. Adebayo does the SSH steps (Needs row 6, 15).
- [ ] **Review:** Sonnet. **Needs:** P0.6, P0.3. **Done:** README steps were run once by a second pair of eyes on a scratch machine or marked "not run on a real machine".

### Task D9a: Worker concurrency

**Files:** Modify `app/worker.py` and `app/pipeline.py` (release the DB session between pipeline steps; address the note in `run_pass` about holding the connection during OpenAI calls), `compose.yaml` (`worker` `deploy.replicas: 2`); Test `tests/test_worker.py` (new) and `tests/test_pipeline.py`.

- [ ] Tests first: `test_two_workers_never_take_the_same_meeting` (`skip_locked`); `test_session_released_between_steps` (assert no open transaction during a fake slow OpenAI call); `test_worker_restart_mid_job_resumes_without_duplicate_notes`.
- [ ] F, **full suite**. **Review:** Sonnet. **Needs:** D2. **Done:** the compose file validates with two replicas.

### Task D9b: Load test and `max_bots` (M2)

**Dependencies:** D8 on a real second machine, P0.5, P0.6. Adebayo runs SSH steps; a session writes the protocol and the note.

- [ ] Protocol: host three overlapping calls (Meet, Zoom, Meet), one bot per host then two on one; record memory, CPU, live-transcript lag and audio quality per bot count (`docker stats` and the Vexa transcript timestamps). Output `docs/spike/pilot-capacity-m2.md`.
- [ ] Set each host's `max_bots` through `PATCH /api/admin/hosts/{id}` (admin) from the numbers; write them into the note. Raise `BOT_MAX_LOCAL` only if M1 and M2 agree.

**Done:** note with raw numbers and the chosen limits; the lead may now say "invite more testers".

### Task D10: Phase D close-out

SOP; checklist; FE guide (capacity states `waiting_for_capacity`, `no_capacity` 503, the admin capacity screen); live capture with `FakeVexaHost`-free real hosts; Opus review of the phase diff. **Done:** guide verification table filled; the 2-bot-per-user and failover rows marked verified-live or labelled not.

---

## Phase V: Google verification submission

Spec section 15 Phase V, corrected: **the sensitive scopes are not verified** (only branding is), so this is a real submission with a review of up to 10 days. The pilot does not wait for it (P0.9 lets up to 100 testers through the unverified-app screen), but the submission should go in **as early as the flow it must demonstrate works**, and it can only go in once, cleanly: Google reviews a scope change after approval as a new review, so the scope list is frozen first.

| Scope | In the list when |
|---|---|
| `openid email profile` | always (non-sensitive, no review) |
| `calendar.events.readonly` | always |
| `meetings.space.readonly` | P0.2 outcome G1, G2 or G3 (Meet detection works in some mode) |
| `meetings.space.created` | only if I6's "start a meeting with the note taker" button is built (needs `spaces.create` to work) |

**Timing choice for Adebayo (open question 3 in the closing list):** with Meet in the list, the demo video must show Meet detection working (needs I4), so the submission waits for Phase I. Alternative: submit Calendar alone as soon as C17 is live and submit the Meet scope later as a second review. Recommendation: if P0.2 finishes in week one, build I1 to I4 right after C and submit once; if the Meet build slips more than about a week, submit Calendar alone and accept the second review.

### Task V0: Revisit the landing and sign-in copy (decided to be revisited here)

**Why now:** the decision "keep the designer's copy as designed" (memory `beyric-landing-copy-decision`, handoff section 2) ends at this task. Google compares the policy and home page to the app.

**Files:** Create `docs/superpowers/specs/2026-11-xx-landing-copy-audit.md`; edits (after Adebayo's decision) to `web/app/` landing and sign-in text, `app/templates/privacy.html`, `terms.html`, and the `web/` copies.

- [ ] **Step 1:** List every product claim on `/`, the sign-in panel, `/privacy` and `/terms`, and mark each: true now, true after Phase C/I/T, or ahead of the product (known: tasks, integrations, sharing; Teams only until T2/T3; **Outlook** until C24 is live, see the list in "Outlook is no longer 'later'").
- [ ] **Step 2:** For each "ahead" claim present Adebayo two choices: soften to what exists, or keep it for the brand and accept the risk with the reviewer. Do not choose for him.
- [ ] **Step 3:** Apply his decisions in one PR; legal-text equality test and landing tests stay green.

**Review:** Sonnet. **Needs:** C17, T3 (or T dropped), I7. **Done:** a decision table committed; the PR matches it.

### Task V0a: Freeze the final scope list

**Files:** `docs/spike/pilot-verification-check.md` (section "Scopes").

- [ ] Write the list per the table above from P0.2's gate outcome and I6's state; for each scope, the one feature that uses it and the app screen that shows that use. Declare every listed scope in the console Data Access (P0.1 step 2). **Nothing outside the list is requested in code**: add a test `test_requested_scopes_are_exactly_the_frozen_list` in `tests/test_calendar_connect.py` that compares the scope strings the app can request with a constant `FROZEN_SCOPES`.
- [ ] Adebayo confirms the list in chat before V3. **Needs:** P0.1, P0.2. **Review:** Sonnet. **Done:** the list and the test are merged.

### Task V1: Consent screen versus policy and app

- [ ] Re-read the live consent screen text and scopes; run spec 12.3's "claims versus app" checks: home page and policy on `notes.beyrictech.com`, linked from the consent screen, Limited Use statement verbatim, every scope requested has a sentence, nothing requested that the app does not use. Output `docs/spike/pilot-verification-check.md`.

**Needs:** V0, V0a, C3. **Done:** a table of check, result, fix.

### Task V2: Demo video

**Who:** a session writes the script; **Adebayo records** (screen capture on the live site with the real consent screen) and uploads unlisted to YouTube.

- [ ] Script `docs/spike/pilot-demo-video-script.md`: sign-in with Google; the connect click and the **real consent screen showing each scope**; where each scope is used (Upcoming list for Calendar, a joined meeting, the instant-Meet start for Meet); disconnect and the data deletion. Google asks for a video that "fully demonstrates how a user initiates and grants access to the requested scopes".

**Needs:** C17 live, and I4 live if `meetings.space.readonly` is in the list (otherwise skip the Meet scene and say so in the submission). **Done:** the unlisted link exists and the script was followed. Google's guidance: record the real consent screen in English, show the OAuth client ID in the address bar, and show every scope being granted.

### Task V3: Scope justifications entered

- [ ] Enter the drafts from spec section 15 (adjusted to the live product) in the console for each scope Phase V covers; paste the final text into `docs/spike/pilot-verification-check.md`.

**Needs:** V0a, V1. **Done:** the console fields match the note.

### Task V4: Submit and answer the reviewer

- [ ] Adebayo submits ("can take up to 10 days" per Google). A session drafts each reviewer reply from the note; on approval set `GOOGLE_APP_VERIFIED=true` (the unverified-app explainer disappears) and confirm the status is still **In production**; update the checklist and the policy "last updated" date if any text changed. Until approval, nothing in the app or policy says the scopes are verified.

**Needs:** V2, V3. **Done:** approved status screenshot or the console text in the note. If Google asks for changes to the policy or the video, the answer ships as a normal PR; any request to drop a scope is handled by a reduced-scope resubmission, not by silently narrowing the code.

---

## Outlook is no longer "later" (spec Q4 changes)

Spec Q4 (2026-11-03 answers, "Later. Hide the 'Connect Microsoft Outlook' button until it is built") and section 1 non-goals ("Outlook, Teams, iCal and any non-Google calendar") are changed by Adebayo's decision that the ICS source is in: **Outlook, Microsoft 365 and other ICS publishers join the pilot as a pasted link (C19 to C24), with no Microsoft OAuth app.** The decision does not make any Outlook sentence true yet. Until C24 is merged, live and verified against a real Outlook feed (C19 and C24 captures), **nothing may claim Outlook**. These are the lines that must stay out or be corrected:

| Where | Line today | Required state |
|---|---|---|
| Spec Q4 row (line 36) and section 1 non-goals (line 75) and section 18 follow-ups (line 784) | "Outlook later" / "Outlook, Teams, iCal ... not in this version" | **Not edited by this PR.** The lead amends them: Outlook via ICS is in; Teams is already in per Q17; iCal is in; Microsoft OAuth stays out |
| `web/components/landing/content.ts` line 22 (FAQ "Which meeting platforms") and line 29 (`TOOLS` strip lists "Outlook", plus Slack, Jira, Linear, Asana) | "connect Google Calendar or Outlook", an Outlook logo in the tools strip | Stays as the designer drew it **until V0** (decision 2026-11-03), then V0 chooses; after C24 live it may say "paste an Outlook calendar link", never "connect Outlook" |
| `docs/ui/Beyric Note Taker redesign/Landing.dc.html` (lines about the FAQ and the tools strip) | same as above | design source, edited only by the designer |
| `docs/ui/.../Auth.dc.html` onboarding `calOpts` | offers "Google Calendar" and "Microsoft Outlook" as connect options | the Outlook option must read "Add a calendar link"; a request to the designer (Needs row 13) |
| `docs/ui/.../Beyric Note Taker.dc.html` Settings | a "Connect Microsoft Outlook" button with a connecting state | replaced by the "paste a link" card (Needs row 13); no OAuth button |
| `docs/ui/.../Terms of Service.dc.html` line 118 | names "Microsoft Outlook" among platforms | the live terms (`app/templates/terms.html` line 57, `web/` copy) name only Meet, Calendar and Zoom: leave it that way; C24 adds the pasted-link sentence only |
| `docs/ui/.../Privacy Policy.dc.html` | "If you connect Google Calendar or Microsoft Outlook, we read event titles, times, attendee names and email addresses" | **wrong twice**: no Outlook connection exists, and the app stores an attendee count, not names or addresses (spec 12.3). The live `privacy.html` lines 38 and 66 are already narrower: keep them |
| `docs/ui/parity-report.md` ("Tools strip, FAQ 1 and 4") | records the same gap | updated by V0 |
| Live `app/templates/privacy.html` (lines 38, 49, 66, 81) and `terms.html` (lines 22, 57) | Google only | change only in C17 (Google) and C24 (pasted link); no Microsoft party is added to "who we share with" because we call no Microsoft API |

---

## Plan deviations from spec section 15 (all deliberate)

| Change | Reason |
|---|---|
| New tasks C8b (sync job), C10b (sweeps and mail tick), C11b (alerts), C18 (recording notice), C19 to C24 (the ICS source, Adebayo's "Yes, add ICS"), T1 to T3 (Teams), D9 split into D9a/D9b, C15 split into a and b | glue the spec's task list left unowned; Teams from 0a; ICS decision; session-sized tasks |
| `app/calendar_text.py` moves from C8 to C15a | the states are computed where the Upcoming list is built |
| `calendar_events.source_event_id` instead of `google_event_id`; `provider` check allows `ics`; `scheduled_joins.rechecked_at` | the ICS source (C22) needs no rename migration; the re-check must run once |
| No-capacity retry and failure text live in C12 (the runner), not C11 | C11 stays the placement function; the runner owns state transitions |
| Privacy "Security" row ships in C3, not C2 | no token is stored until C3; the claim must be true when merged |
| Privacy "Recording and participant notice" row moves from I7 to C18 | calendar joins are the first auto-joins |
| C3 gets an Opus review; C11 is named as dispatcher locking; C20 (SSRF fetcher) gets an Opus review | OAuth tokens, locking and an SSRF surface; spec marks only C2, I2, D4 |
| Spec 0a Q7 ("app is verified") is replaced by Adebayo's correction: branding only; P0.9 bridge; `GOOGLE_OAUTH_TESTING` and the C13 banner are live while in Testing; Phase V is a real submission with V0a freezing the scope list | Adebayo's correction relayed 2026-11-03 |
| Migration numbers are "next free revision" | Phase A merges in parallel and the spec's 0008 to 0010 are placeholders |

---

## Needs Adebayo (one table)

Every ask in one place. "Unblocks" names the first task that cannot finish without it.

| # | Ask | Exact steps | Unblocks |
|---|---|---|---|
| 1 | **Google Cloud project and billing** (Events API and Pub/Sub need billing; spend at pilot volume is cents) | console.cloud.google.com, a **new throwaway project** (Testing mode) (e.g. `beyric-meet-spike`) for the spike, billing account attached; enable "Google Workspace Events API", "Google Meet REST API", "Cloud Pub/Sub API", "Google Calendar API". In the **production** project (the one with the verified app) enable the same four and attach billing before I2. Paste both project IDs | P0.2, I2, I3 |
| 2 | **Cloud project is already In production (Adebayo, 2026-11-03); still to do: set `GOOGLE_OAUTH_TESTING=false` in the server `.env` and redeploy if not already, and start the sensitive-scope verification once the calendar flow works** | **Now (P0.1):** production project, Google Auth platform: copy Audience (publishing status, test users), Branding (verified), Data Access (declared scopes) and Verification Center status; declare `.../auth/calendar.events.readonly` (and, once the lead says the spike passed, `.../auth/meetings.space.readonly`, and `.../auth/meetings.space.created` only if the button is built) in Data Access. **Before the first tester (P0.9):** Audience, "Publish app", confirm status "In production"; set `GOOGLE_OAUTH_TESTING=false` in the server `.env` the same day and redeploy outside a meeting; a tester will then see "Google hasn't verified this app" on the calendar consent screen and must click Advanced then continue. **As early as the calendar flow works (V4):** submit for sensitive-scope verification (row 18) | P0.1, P0.9, C3 live test, V0a |
| 3 | **OAuth client for the spike** | In the throwaway project: Google Auth platform, Audience "External", Testing; add the two test accounts under **Test users**; Clients, create "Desktop app" client; give the session the client ID and secret through a local file outside the repo (never in chat or git) | P0.2 |
| 4 | **Search Console** | search.google.com/search-console, confirm `beyrictech.com` shows as a verified property (DNS TXT; Google's domain proof for the submission, and probably done already because branding is verified); if not, add the TXT record Google shows at the domain's DNS and click Verify. Confirm `privacy@`, `legal@`, `security@beyrictech.com` receive mail | V1 (also needed for the consent screen) |
| 5 | **Resend state** | Resend dashboard, Domains: `beyrictech.com` status "Verified". Check records: `dig TXT beyrictech.com +short` (SPF), `dig TXT resend._domainkey.beyrictech.com +short` (DKIM; use the selector Resend shows), `dig TXT _dmarc.beyrictech.com +short`. Send a test mail from the dashboard to a Gmail and check it is in the inbox, not spam | P0.4, A3 live check, C14 |
| 6 | **Second bot machine** | A server with 8 GB RAM and 4 vCPU (the plan's figure; D9b adjusts it), Ubuntu with Docker and Compose, a **private link** to `haven-respite` (WireGuard or Tailscale; the Vexa API must never be public), firewall closed except SSH and the link. Send the lead the private address; `deploy/vexa-host/README.md` (D8) lists the rest | P0.6, D8, D9b |
| 7 | **`docker stats` measurements (M1)** | During a live meeting, on the server: `docker stats --no-stream`, `free -h`, `uptime`; again with two overlapping meetings. Also read the Next container's memory in the same output. Paste all of it | P0.5, D9b |
| 8 | **Test accounts** | One personal @gmail.com account used only for testing (calendar plus Meet), and, **if you can**, one Google Workspace account you control (a free trial of Business Starter on a spare domain works; `beyrictech.com` is not Workspace). Add both as test users (row 3). If you have no Workspace account, say so and S3 is recorded "not tested" | P0.2 (S3), C19, C5 live step |
| 9 | **A real Teams meeting** | Sign in to Teams (work/school if you have it, else a free personal account), create a meeting with a lobby, copy its link to the session through a local file, stay in the meeting and admit the bot when it knocks; repeat for a meeting with the lobby off | P0.7, T1 to T3 |
| 10 | **Host the calls** | For P0.3 and P0.8 host one Meet and one Zoom call (and the Teams one in row 9) while a session sends the bot; one session at a time. 30 minutes covers all three | P0.3, P0.8 |
| 11 | **Outlook.com account and one Microsoft 365 work user** (ICS is IN, decided 2026-11-03) | Give the session an Outlook.com account for C19 (a spare personal one is fine; the session publishes its calendar and creates test events, nothing else). Ask one person who has a Microsoft 365 work account to open Outlook on the web, Settings, Calendar, Shared calendars, and tell the session whether the "Publish a calendar" option appears (it is tenant-controlled) | C19, C21, C24 |
| 12 | **Decision: landing and sign-in copy** | When V0 opens, read its table and choose "soften" or "keep" per claim | V0 |
| 13 | **Designer requests** | Ask the designer for: the "Google hasn't verified this app" explainer for testers (what they will see and how to click through; screenshots come from P0.9), the instant-Meet switch (Q11), "Not recorded" rows with Send now, "Waiting for a free note taker", the notification bell, "Calendar disconnected" banner, "Pilot full"/waitlist, the admin screens, and a "paste a calendar link" card on Settings → Calendar with the first-sync result (counts or a named error) and the note "Updates can take a while; meetings made just before they start may be missed." Also: the designer's "Connect Microsoft Outlook" button and Outlook names must become "Add a calendar link" until an Outlook feed is verified live | stream D screens |
| 14 | **Server `.env` values** | Add when each phase lands: `TOKEN_ENC_KEY` (`openssl rand -base64 32`; **also store it offline**: losing it forces every user to reconnect), `GOOGLE_CLIENT_ID`, `GOOGLE_CLIENT_SECRET`, `GOOGLE_OAUTH_TESTING` (true now, false the day of P0.9), `GOOGLE_APP_VERIFIED` (false until V4), `GOOGLE_PROJECT_ID`, `GOOGLE_PUBSUB_TOPIC`, `GOOGLE_PUBSUB_AUDIENCE`, `GOOGLE_PUBSUB_SERVICE_ACCOUNT`, `INSTANT_MEET_MODE`, `INSTANT_MEET_PERSONAL_MODE`, `ADMIN_ALERT_EMAIL`, `AUTOJOIN_LEAD_SECONDS` if P0.8 says not 60. Never paste values into chat | C3, I2, I3, C11b |
| 15 | **Server steps with sudo** | After the PR that adds them merges: `sudo nginx -t && sudo systemctl reload nginx` (new zones from A19 and I2; never `cp deploy/nginx.conf` over the live file); confirm the `scheduler` container is up (`docker compose ps`) after the deploy of C10; on the second machine, the D8 README steps | C10, I2, D8 |
| 16 | **Lawyer read** (Q21) | After A20 and C17 policy rows are merged and before inviting testers, send the live `/privacy` and `/terms` to a lawyer; ask also whether Beyrichq LTD must register with the Nigeria Data Protection Commission | inviting testers |
| 17 | **Record the demo video** | Follow `docs/spike/pilot-demo-video-script.md` on the live site with the real consent screen; upload unlisted to YouTube; send the link | V2 |
| 18 | **Submit for verification, as early as the demo can be recorded** | Click Submit in the Verification Center with the frozen scope list (V0a), the unlisted video link (row 17) and the scope justifications (V3); answer the reviewer within the time Google gives (review can take up to 10 days) | V4 |

---

## Self-review (run against the spec)

- **Spec coverage:** Phase 0 (P0.1 to P0.6 plus P0.7 Teams and P0.8 join time from 0a); Phase A listed and left to its owners; C1 to C24 all present and extended (C19 to C24 are the ICS source, in per Adebayo); I1 to I7 with the spike gate and the four fallbacks (G1 to G4, manual routes in I6); D1 to D10; V0 to V4; every 0a row is reflected in Global Constraints. 12.3 policy rows each map to a task (A20, C3, C17, C18, T3, I7, C24, V0).
- **Spec bugs found while planning:** spec 0a Q7 says the OAuth app is verified; Adebayo corrected it to branding only (Global Constraints); the spec's 7.6 and 5.1 still say a 2-minute lead (0a replaced it); 4.3 still describes a no-mail fallback; spec 5.5 lists `MAIL_*` while the handoff says `RESEND_API_KEY`; spec 9.7's "no-capacity path in C11" is split here (C11 placement, C12 transitions). None contradicts 0a; the first three are listed under Global Constraints.
- **Type consistency:** `PlacementRequest`, `PlacementRefused.code` values, `ParsedEvent`, `JoinDecision.reason`, `SyncBatch.complete` and `CalendarSource.sync` are named once and reused in C8, C8b, C12, C21 and I4 with the same spellings.
- **Proportion:** the plan is longer than the spec because it carries per-task tests and the Needs table; code bodies are signatures and test names only.

**Open questions this plan could not settle** (for the lead): (1) the mail variable names, which A3 decides; (2) whether Opus review on C3 and C20 is accepted (spec marks only C2, I2, D4); (3) the Meet-scope timing in Phase V: submit once after Phase I (recommended if P0.2 lands in week one) or submit Calendar alone first and accept a second review; (4) whether Adebayo can supply a Workspace test account (otherwise outcome G1 versus G2 cannot be told apart); (5) **one calendar source per user in v1** (Google OAuth or an ICS link, not both): a user with a Google calendar and an Outlook work calendar must pick one; allowing both needs a cross-source duplicate rule (the second source's copy would read as "already in this meeting", which is wrong for one's own meeting); (6) the spec changes listed under "Outlook is no longer later" are **not** edited by this PR.
