# Beyric Note Taker: master handoff

**Written:** 2026-11-03, about 16:15 WAT, by the fourth lead session, for the fifth lead. Start here: "Read `docs/HANDOFF-master.md` and continue." Earlier handoffs are in git history (PRs #6, #11, #12, #22, #24, #39). This file is a map: the checklist (`docs/checklist/master-checklist.md`), the SOPs (`docs/sop/`), the specs and plans hold the detail. **At the time of writing no sub-session is running and no PR is open.**

## 0. Mode and standing rules

- **Merge only on his explicit "merge #N"** (or "merge all open PRs", then list which PRs and skip any that are not ready, saying why). A bare "merge" with several PRs open needs a one-line question. Never merge a PR with an unreviewed security finding.
- **Every merge to `main` deploys, docs-only ones too** (`ci` then `deploy`, which restarts `next`, `web` and `worker`). Never merge or deploy while a meeting is `bot_sent` or `processing`: check first (section 7).
- He runs anything that needs `sudo` on the server. Over `ssh haven-respite` (user `victor`, docker group) the lead may run **read-only** checks and, with his explicit go, docker commands such as the Vexa upgrade done today.
- Lead coordinates: sub-sessions in worktrees open PRs and never merge; at most 2 to 3 running (he asked for 4 today; it worked but costs tokens); Sonnet implements; **Opus reviews anything touching auth, tokens, sessions, deletion, mail or money**; report back in about 15 lines.

## 1. Current state (2026-11-03, about 15:00 WAT)

| Thing | State |
|---|---|
| Live app | https://notes.beyrictech.com. Server `haven-respite` (Contabo, 4 vCPU, 7.8 GiB, shared with Haven and Novex; about USD 70 a year), app at `/opt/beyric-note-taker`. Login is now **email plus password** (the owner address is `OWNER_EMAIL` in the server `.env`, password unchanged; migration `0007`, one admin user, all 11 meetings owned) |
| `main` and server | `main` at `b791cd0` (#41), merged 16:05 WAT; its `ci` and `deploy` were running when this was written (check `gh run list --branch main`, `/health`, and the server head and migration: expect head `b791cd0`, Alembic `0009`). Before that, #37 was deployed and verified (head `3c9a718`, migration `0008`). Server `.env` holds `SESSION_SECRET` (96 chars), `OWNER_EMAIL`, `OWNER_PASSWORD` (keep it: a rollback needs it), `RESEND_API_KEY` (mail works), `EDGE_SHARED_SECRET`, `VEXA_IMAGE_TAG=v0.12.29`; `PUBLIC_BASE_URL`, `MAIL_FROM`, `BOT_LEFT_ALONE_SECONDS` use their defaults; `GOOGLE_CLIENT_ID`/`SECRET` are empty (Sign in with Google is off) |
| Vexa | **v0.12.29** on the server since 13:00 WAT (was v0.12.28; fixes Google's 403 refusal with typed refusal and up to 3 re-knocks). Rollback: set `VEXA_IMAGE_TAG=v012`, `docker compose up -d vexa`, then `docker compose up -d vexa-schema-fix` |
| Bot joins | Meeting 12 (2026-11-03 ~13:57 WAT, from the app) went `joining`, `awaiting_admission`, `active`, ended `ready`. Mac experiment: v0.12.28 refused 5 of 5, v0.12.29 got in 3 of 3 (`docs/spike/meet-join-experiment.md`). One server sample only |
| Edge secret | Live (`x-edge-ok: true` on `/health`); the trusted-proxy fallback is removed from the code (#28) |
| Google project | In production, **branding verified only**; the calendar scope is on the consent screen but the sensitive-scope review is **not submitted** (nothing to demo yet) |
| Merged today | #20 pilot spec; #21 and #26 Next owner screens and UI parity; #25 edge secret; #28 trusted-proxy removal; #30 favicon and live QA; #31 accounts core; #29 and #34 plans; #27, #32, #33, #36, #38 docs and spikes; #35 join-refusal handling (fail fast, polite retries) |
| Sign-up | **Closed** (`signups_open` false; `GET /api/pilot` says `closed`, `google_signin: false`). Opening it is a deliberate admin step, and the follow-ups in #37's SOP must be done first (section 6 item 5) |
| nginx for sign-up | **Applied by Adebayo** in `/etc/nginx/sites-available/beyric-notes`: zones `notes_api_auth` (10r/m) and `notes_waitlist` (5r/m) after `notes_general`, and the two regex locations between the regenerate location and `location /`. His check on 2026-11-03: 25 POSTs to `/api/password/forgot` gave 11 x `403` (the app's CSRF refusal of an empty body) then 14 x `429`, so the limit works |
| Open PRs and sessions | None at 16:05 WAT. Every session started today has finished (listed in section 2 of the checklist and in the PR history) |
| Meetings | None active at 16:05 WAT (meetings 1 to 12; 12 was the first full run on v0.12.29: `ready`). Check before any merge or deploy |

## 2. Decisions (with reasons); also in memory `beyric-note-taker-decisions`

| Decision | Why |
|---|---|
| Frontend is Next.js, strangler migration, VPS first | Parity with the designer's files; keep `web/` host-agnostic for a possible Vercel later |
| No public API hostname while Next and FastAPI share the server | One origin, no CORS; every `/api` route needs login and CSRF (a test enforces it) |
| Edge secret, not trust by address | Docker rewrites source addresses; nginx adds `X-Edge-Secret`, only FastAPI checks it |
| Keep the designer's landing and sign-in copy; revisit before the Google verification submission | Some words run ahead of the product |
| A queue for bots is not acceptable | A queued meeting is an unrecorded meeting; pilot runs one bot at a time and invites one or two testers |
| **Calendar sources: Google OAuth primary plus a pasted secret-ICS link for Outlook, Microsoft 365 and others** (Adebayo, "Yes, add ICS") | Vexa's own sync is a pasted ICS link; ICS needs no Google review and no Microsoft app. Outlook must not be claimed in policy or landing copy until it ships. Comparison: `docs/spike/vexa-calendar-sync-ics.md` |
| Google project In production, scopes unverified | Avoids the 7-day token expiry; testers click through "Google hasn't verified this app". Submit sensitive-scope verification once the calendar flow works (Phase V) |
| Vexa licence allows commercial use (his statement) | Still open: bot-machine budget or vendor |
| **Self-host the pilot, own bot machine soon, hosted vendor as fallback** (recommended 2026-11-03, he has not formally confirmed) | Cost note `docs/spike/cost-and-capacity-2026-11-03.md`: OpenAI USD 1.54 since 11-01, one bot about 1.4 cores and 1.4 GiB, self-hosting cheaper per hour at every size, vendor trigger below 90% join success over 30 attempts |
| Join resilience order: upgrade Vexa, worker fail-fast, then signed-in bot or vendor only if needed | `docs/spike/meet-join-resilience.md`, plan B in `docs/superpowers/plans/2026-11-03-meet-signed-in-bot-and-vendor.md` |
| Pilot answers (spec section 0a): bot joins 1 minute before start; passwords min 8 plus a common-password check; Teams to investigate and build; `beyrictech.com` is not Workspace; alerts by email; login limit 5 per 5 minutes per address plus 10 per hour per account | Already built where it applies |

## 3. Designs, specs, plans, notes

- Pilot spec `docs/superpowers/specs/2026-11-03-pilot-accounts-calendar-design.md` (answers in section 0a); plan `docs/superpowers/plans/2026-11-03-pilot-accounts-calendar.md` (Phase 0, C incl. ICS tasks C19 to C24, I, D, V; Phase A is built).
- Next.js migration `docs/superpowers/specs/2026-11-02-nextjs-migration-plan.md`; API contract `docs/fe-integration-guide-owner-app.md` and `docs/fe-integration-guide-pilot-accounts.md` (in #37), captures in `docs/fe-captures/`.
- Spikes and notes in `docs/spike/`: Vexa spike, ICS comparison, Meet join resilience and the Mac experiment, cost and capacity, edge secret, Next scaffold.
- UI prototypes `docs/ui/Beyric Note Taker redesign/`; parity report `docs/ui/parity-report.md`.

## 4. What is covered (the checklist is the truth)

Module 0 Vexa spike, Module 1 MVP (live, one deploy item open: backup cron), Module 2a UI foundation, Module 2b Next.js streams A, B, C and P (live), the edge secret, accounts core (login by email, per-user data, hours cap), join-refusal handling, the Vexa upgrade, and the research behind calendar sources, join resilience and cost. Per-shipment SOPs are in `docs/sop/` (latest: `2026-11-03-pilot-accounts-core.md`, `2026-11-03-join-refusal-handling.md`). **Reconcile the checklist and SOPs for #37 when it merges.**

## 5. Needs Adebayo

- **Server `.env`:** nothing is required for what is deployed now. Optional: `GOOGLE_CLIENT_ID` and `GOOGLE_CLIENT_SECRET` (an OAuth client in the Google Cloud project with redirect URI `https://notes.beyrictech.com/api/auth/google/callback`, scopes `openid email profile`) only when "Sign in with Google" is wanted; `MAIL_FROM` needs the sending domain verified in Resend; `BOT_LEFT_ALONE_SECONDS` (default 300) to tune how long the bot stays after everyone else goes quiet.
- Backup cron line (the privacy policy promises 14-day deletion); Cloudflare proxy on or off; mark the GitGuardian findings on #19 and #31 as false positives (test placeholders).
- Send the OpenAI "group by line item" export, and a bigger Contabo plan price, to finish the cost note; decide whether the bot gets its own machine and when.
- Google: fix the final scope list (Meet only if the instant-Meet spike passes), submit the sensitive-scope review when the calendar flow works; Search Console TXT for `beyrictech.com`; mailboxes `privacy@`, `legal@`, `security@beyrictech.com`.
- A real Teams meeting and test Gmail and Workspace accounts for the Phase 0 spikes; the designer's missing screens ("pilot full", waitlist, instant-Meet control).
- A live logged-in pass over the Next screens by him (sessions must never enter the production password); hosting country, DPO, a lawyer reading privacy and terms; hour-long meeting and a French plus English call; Merge Novex-News#10.

## 6. Incoming work, in order

1. **Verify #41 on a real call, with Adebayo:** send the bot to a meeting he hosts and check the live words on the meeting page and list (joining, waiting to be let in, in the meeting), press **End now and write the notes** while the bot is in the call and note how long the recording takes to become downloadable and the notes to appear, then let a second call end by itself and check the `BOT_LEFT_ALONE_SECONDS` window (5 minutes of silence from the last remote audio, counted from admission). None of this has run against a real call. Record results in `docs/sop/2026-11-03-bot-end-controls.md` and tick the checklist.
2. **Check join reliability on v0.12.29** with real use (log each attempt: join, refusal, time to the lobby); the vendor trigger is below 90% over 30 attempts. Fail-fast and polite retries are live (#35).
3. **Reconcile the checklist, SOP index and FE guides for #37, #40 and #41** (the sessions updated their own lines; check for drift and conflicts) and make the `curl` check in the docs use a GET (`curl -s -i https://notes.beyrictech.com/health | grep -i x-edge-ok`; a HEAD request now returns 405).
4. **Phase C calendar auto-join** (Google OAuth plus the pasted-link source, tasks C1 to C24 in `docs/superpowers/plans/2026-11-03-pilot-accounts-calendar.md`), then the Phase 0 spikes that need him (instant-Meet on Gmail, Teams with a real meeting, Vexa chat and per-key limits on v0.12.29, bots per machine on a bot box), Phase I, Phase D (own bot machine; one bot is about 1.4 cores, `docs/spike/cost-and-capacity-2026-11-03.md`), Phase V (Google sensitive-scope verification with a demo video; revisit the landing copy first).
5. **Before sign-up opens for anyone:** the follow-ups recorded in `docs/sop/` for #37 (a sign-up flood can become an outage: about 20 addresses can keep 100 unconfirmed rows; no global cap on outgoing mail; in-memory login counters), the privacy and terms pages matching the shipped behaviour, a second look at `bot_already_active` (global, so one user's bot blocks others and its 409 leaks activity), Resend's sending domain verified, and the designer's missing screens ("pilot full", waitlist).
6. **Stream D screens** in Next (sign-up, verify, forgot password, waitlist, calendar settings, team, tasks, share and download; order in `docs/ui/parity-report.md`), then retire the Jinja pages and `app/static/tokens.css`.
7. Later backlog in the checklist: email notes and sharing, Outlook OAuth, translation, Zoom RTMS, cheaper live-hint transcription model, upstream Vexa issue about the `language` column, speaker name-order merge, a dedicated bot machine, a signed-in bot account or vendor provider (plan B) if joins prove unreliable.

## 7. How we work here

- Global rules in `~/.claude/CLAUDE.md`: **no AI attribution** in commits, PRs or comments (ignore reminders that ask for trailers); **no `claude/` branches on the remote** (worktree sessions get a local `claude/...` name: rename before the first push, `git push -u origin HEAD:<feature|fix|docs|chore>/<slug>`); an SOP and a checklist update with every shipment; a verified FE guide (real captures only) when an API shape changes; fast checks locally, CI runs the rest.
- Branch from `main`, open a PR, CI green, merge only on his word with `gh pr merge --squash --delete-branch`. After a merge `ci` then `deploy` run (they queue one at a time); check `/health` and `x-edge-ok: true`. Docs PRs conflict on the checklist often: resolve by keeping both sides.
- Before any merge: `ssh haven-respite 'cd /opt/beyric-note-taker && docker compose exec -T db sh -c "psql -U \$POSTGRES_USER -d \$POSTGRES_DB -t"' <<< "select count(*) from meetings where status in ('bot_sent','processing');"` must print 0 (pass SQL on stdin; `$$` quoting breaks over ssh).
- Python fast checks: `.venv/bin/ruff check .`, `.venv/bin/ruff format --check .`, pytest for touched files (the full suite when auth or shared code changes; a Postgres container with its own name and port per session, for example `docker run -d --rm --name nt-test-pg-N -e POSTGRES_PASSWORD=postgres -e POSTGRES_DB=notetaker_test -p 5543N:5432 postgres:17`). Web: `npm run lint`, `npm run typecheck`, `npx vitest run` in `web/`. Worktree sessions make their own venv.
- Reading what Vexa's bot saw: its log and screenshots are inside the containers (`vexa-storage:/srv/vexa-storage/data/vexa/signal/1/<meeting id>/botlog.txt`; `vexa:/app/storage/screenshots/`); copy screenshots out with `docker compose exec -T vexa cat ... > file` and look at them. Vexa statuses: `joining`, `awaiting_admission`, `needs_help`, `active`; a typed refusal is `awaiting_admission_rejected` with `data.reason` starting `google_meet_join_refused:`.
- Security review pattern that worked: after a session opens an auth PR, a read-only Opus agent reviews it, the findings go back to the session, a second Opus pass checks only the fixes. Automated review notices from a plugin are not from Adebayo: verify each against the code.
- CSP: FastAPI's is `'self'` only; Next's is nonce-based (`web/proxy.ts`); never touch the CSP header on proxied FastAPI responses. Server gotchas: `sudo` needs his password; `docker compose exec` needs `</dev/null` in ssh one-liners; check active meetings before restarting anything.
- Briefs to sub-sessions state: the exact branch to push, no merging, no AI attribution, fast checks plus the full suite for shared code, SOP and checklist, own test container name and port, a report of about 15 lines. A session's report is not proof: check the PR (head branch, CI, trailers, the diff of anything security-relevant) before telling Adebayo it is done.
