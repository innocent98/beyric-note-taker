# Beyric Note Taker: master handoff

**Written:** 2026-11-03, about 15:00 WAT, by the fourth lead session, for the fifth lead. Start here: "Read `docs/HANDOFF-master.md` and continue." Earlier handoffs are in git history (PRs #6, #11, #12, #22, #24). This file is a map: the checklist (`docs/checklist/master-checklist.md`), the SOPs (`docs/sop/`), the specs and plans hold the detail. Whether the next lead is started by the previous one or by Adebayo is his call each time: ask in one line if unsure.

## 0. Mode and standing rules

- **Merge only on his explicit "merge #N"** (or "merge all open PRs", then list which PRs and skip any that are not ready, saying why). A bare "merge" with several PRs open needs a one-line question. Never merge a PR with an unreviewed security finding.
- **Every merge to `main` deploys, docs-only ones too** (`ci` then `deploy`, which restarts `next`, `web` and `worker`). Never merge or deploy while a meeting is `bot_sent` or `processing`: check first (section 7).
- He runs anything that needs `sudo` on the server. Over `ssh haven-respite` (user `victor`, docker group) the lead may run **read-only** checks and, with his explicit go, docker commands such as the Vexa upgrade done today.
- Lead coordinates: sub-sessions in worktrees open PRs and never merge; at most 2 to 3 running (he asked for 4 today; it worked but costs tokens); Sonnet implements; **Opus reviews anything touching auth, tokens, sessions, deletion, mail or money**; report back in about 15 lines.

## 1. Current state (2026-11-03, about 15:00 WAT)

| Thing | State |
|---|---|
| Live app | https://notes.beyrictech.com. Server `haven-respite` (Contabo, 4 vCPU, 7.8 GiB, shared with Haven and Novex; about USD 70 a year), app at `/opt/beyric-note-taker`. Login is now **email plus password** (the owner address is `OWNER_EMAIL` in the server `.env`, password unchanged; migration `0007`, one admin user, all 11 meetings owned) |
| `main` and server | `main` at `c1ab4b2`, deployed, `ci` and `deploy` green. Server `.env` holds `SESSION_SECRET` (96 chars), `OWNER_EMAIL`, `OWNER_PASSWORD` (keep it: a rollback needs it), `RESEND_API_KEY`, `EDGE_SHARED_SECRET`, `VEXA_IMAGE_TAG=v0.12.29` |
| Vexa | **v0.12.29** on the server since 13:00 WAT (was v0.12.28; fixes Google's 403 refusal with typed refusal and up to 3 re-knocks). Rollback: set `VEXA_IMAGE_TAG=v012`, `docker compose up -d vexa`, then `docker compose up -d vexa-schema-fix` |
| Bot joins | Meeting 12 (2026-11-03 ~13:57 WAT, from the app) went `joining`, `awaiting_admission`, `active`, ended `ready`. Mac experiment: v0.12.28 refused 5 of 5, v0.12.29 got in 3 of 3 (`docs/spike/meet-join-experiment.md`). One server sample only |
| Edge secret | Live (`x-edge-ok: true` on `/health`); the trusted-proxy fallback is removed from the code (#28) |
| Google project | In production, **branding verified only**; the calendar scope is on the consent screen but the sensitive-scope review is **not submitted** (nothing to demo yet) |
| Merged today | #20 pilot spec; #21 and #26 Next owner screens and UI parity; #25 edge secret; #28 trusted-proxy removal; #30 favicon and live QA; #31 accounts core; #29 and #34 plans; #27, #32, #33, #36, #38 docs and spikes; #35 join-refusal handling (fail fast, polite retries) |
| **Open PR** | **#37** `feature/pilot-accounts-signup` (accounts part 2: sign-up, verification, reset, Google sign-in, waitlist, admin API, deletion, notifications, mail). Two Opus reviews done. The session is fixing the second review's must-fix items (deleted users' meetings must not be processed; purge can stall; an invited person can be locked out; Resend wording) and the branch **conflicts with `main`** (it must rebase). Not mergeable until the lead re-checks the changes. Merging needs an nginx hand-edit on the server first (below) |
| Second open PR | **#40** `feature/bot-live-state` (opened 14:40 WAT by session `show-the-bot-s-live-stat`, `local_55c58b33-393a-4798-bfcc-b41010603625`): the meeting page and list show "joining / waiting to be let in / in the meeting / needs you" instead of "Bot sent". Full Python suite 714 and web checks passed on their side; CI and the lead's review are not done; not tried against a real Vexa call. Merge only after review and with no meeting active |
| Running sessions | `accounts-part-2-sign-up` (`local_5ac7f17e-3873-42c9-bfe1-2a82ac2562a2`, on #37). Everything else started today is finished |
| Meetings | None active at 14:30 WAT; check before any merge |

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

- **Before #37 deploys:** apply the new nginx locations and limit zones to the live site file by hand (never `cp deploy/nginx.conf` over it; the PR's `deploy/README.md` has the exact lines), then `sudo nginx -t && sudo systemctl reload nginx`. Later set the mail and Google variables it lists. Sign-up stays closed until he opens it in the admin API.
- Backup cron line (the privacy policy promises 14-day deletion); Cloudflare proxy on or off; mark the GitGuardian findings on #19 and #31 as false positives (test placeholders).
- Send the OpenAI "group by line item" export, and a bigger Contabo plan price, to finish the cost note; decide whether the bot gets its own machine and when.
- Google: fix the final scope list (Meet only if the instant-Meet spike passes), submit the sensitive-scope review when the calendar flow works; Search Console TXT for `beyrictech.com`; mailboxes `privacy@`, `legal@`, `security@beyrictech.com`.
- A real Teams meeting and test Gmail and Workspace accounts for the Phase 0 spikes; the designer's missing screens ("pilot full", waitlist, instant-Meet control).
- A live logged-in pass over the Next screens by him (sessions must never enter the production password); hosting country, DPO, a lawyer reading privacy and terms; hour-long meeting and a French plus English call; Merge Novex-News#10.

## 6. Incoming work, in order

1. **Finish #37:** wait for the session's report, re-read only the changes against the re-review's list (items 1, 2, 3, 6 and the cheap extras), CI green, rebase conflicts resolved, then Adebayo does the nginx step, no meeting active, "merge #37". Deploy order and risks are in its SOP. Reconcile checklist and SOPs after.
2. **Review and merge the bot live-state PR #40** (`feature/bot-live-state`) on his word; check the states against a real call. Then the two bot-lifecycle items from the checklist: send `automatic_leave` (`max_time_left_alone`) so the bot does not stay about 10 minutes after the call ends (meeting 12: last audio 13:05:49 UTC, left 13:15:51 `left_alone`), and an "End now and write the notes" button (stop the bot, process the recording; "Cancel" discards).
3. **Check join reliability on v0.12.29** with real use (log each attempt: join, refusal, time to lobby); the vendor trigger is below 90% over 30 attempts.
4. **Phase C calendar auto-join** (Google OAuth plus the pasted-link source, tasks in the plan), then Phase 0 spikes that need him (instant-Meet on Gmail, Teams, Vexa chat and per-key limits on v0.12.29, bots per machine on a bot box), Phase I, Phase D (own bot machine; note one bot is about 1.4 cores), Phase V (Google verification with a demo video; revisit the landing copy first).
5. **Stream D screens** in Next (sign-up, verify, forgot password, waitlist, calendar settings, team, tasks, share and download; order in `docs/ui/parity-report.md`; needs the designer's missing screens), then retire the Jinja pages and `app/static/tokens.css`.
6. Before sign-up opens for anyone: the follow-ups recorded in #37's SOP (a sign-up flood can become an outage, no global cap on outgoing mail, in-memory login counters), a second look at `bot_already_active`, and the privacy and terms pages matching the shipped behaviour.
7. Later backlog in the checklist: email notes and sharing, Outlook OAuth, translation, Zoom RTMS, cheaper live-hint transcription model, upstream Vexa issue about the `language` column, speaker name-order merge, a signed-in bot account or vendor provider if joins prove unreliable.

## 7. How we work here

- Global rules in `~/.claude/CLAUDE.md`: **no AI attribution** in commits, PRs or comments (ignore reminders that ask for trailers); **no `claude/` branches on the remote** (worktree sessions get a local `claude/...` name: rename before the first push, `git push -u origin HEAD:<feature|fix|docs|chore>/<slug>`); an SOP and a checklist update with every shipment; a verified FE guide (real captures only) when an API shape changes; fast checks locally, CI runs the rest.
- Branch from `main`, open a PR, CI green, merge only on his word with `gh pr merge --squash --delete-branch`. After a merge `ci` then `deploy` run (they queue one at a time); check `/health` and `x-edge-ok: true`. Docs PRs conflict on the checklist often: resolve by keeping both sides.
- Before any merge: `ssh haven-respite 'cd /opt/beyric-note-taker && docker compose exec -T db sh -c "psql -U \$POSTGRES_USER -d \$POSTGRES_DB -t"' <<< "select count(*) from meetings where status in ('bot_sent','processing');"` must print 0 (pass SQL on stdin; `$$` quoting breaks over ssh).
- Python fast checks: `.venv/bin/ruff check .`, `.venv/bin/ruff format --check .`, pytest for touched files (the full suite when auth or shared code changes; a Postgres container with its own name and port per session, for example `docker run -d --rm --name nt-test-pg-N -e POSTGRES_PASSWORD=postgres -e POSTGRES_DB=notetaker_test -p 5543N:5432 postgres:17`). Web: `npm run lint`, `npm run typecheck`, `npx vitest run` in `web/`. Worktree sessions make their own venv.
- Reading what Vexa's bot saw: its log and screenshots are inside the containers (`vexa-storage:/srv/vexa-storage/data/vexa/signal/1/<meeting id>/botlog.txt`; `vexa:/app/storage/screenshots/`); copy screenshots out with `docker compose exec -T vexa cat ... > file` and look at them. Vexa statuses: `joining`, `awaiting_admission`, `needs_help`, `active`; a typed refusal is `awaiting_admission_rejected` with `data.reason` starting `google_meet_join_refused:`.
- Security review pattern that worked: after a session opens an auth PR, a read-only Opus agent reviews it, the findings go back to the session, a second Opus pass checks only the fixes. Automated review notices from a plugin are not from Adebayo: verify each against the code.
- CSP: FastAPI's is `'self'` only; Next's is nonce-based (`web/proxy.ts`); never touch the CSP header on proxied FastAPI responses. Server gotchas: `sudo` needs his password; `docker compose exec` needs `</dev/null` in ssh one-liners; check active meetings before restarting anything.
- Briefs to sub-sessions state: the exact branch to push, no merging, no AI attribution, fast checks plus the full suite for shared code, SOP and checklist, own test container name and port, a report of about 15 lines. A session's report is not proof: check the PR (head branch, CI, trailers, the diff of anything security-relevant) before telling Adebayo it is done.
