# Beyric Note Taker: master handoff

**Written:** 2026-11-03, about 10:15 WAT, by the fourth lead session, for the fifth lead (Adebayo starts it himself; do not self-start). Start here: "Read `docs/HANDOFF-master.md` and continue." Earlier handoffs are in git history (PRs #6, #11, #12, #22, #24). This file is a map; the checklist, SOPs and specs hold the detail.

## 0. Mode

Adebayo is awake. Standing rules: merge only on his explicit "merge #N" (a bare "merge" needs a follow-up question; "refresh handoff" is not a merge); never deploy while a meeting is `bot_sent` or `processing`; he runs anything that needs `sudo` on the server. Everything below was shipped by him saying "merge #N".

## 1. Current state

| Thing | State |
|---|---|
| Live app | https://notes.beyrictech.com, owner login only (password in `~/beyric-notes-owner-password.txt` on his Mac). Server `haven-respite`, app at `/opt/beyric-note-taker` (memory `beyric-note-taker-server`). Checked 2026-11-03 08:25 UTC: `/health` 200, `/login` 200, `/welcome` 307 to `/` (as designed) |
| `main` / server | `2637a81` (#25), **deployed 2026-11-03 06:41 UTC**. `ci` and `deploy` were green for #21, #26 and #25. Latest migration `0005`; none came with #13 to #26 |
| Edge secret | **Live.** `curl -sI https://notes.beyrictech.com/health` shows `x-edge-ok: true`, so nginx and FastAPI agree. The forwarded-for gap (a container forging the address FastAPI rate-limits on) is closed; the sign-up blocker is gone. Adebayo reported (2026-11-03 morning) that he deleted `TRUSTED_PROXY_IPS` from the server `.env`, redeployed, and logged in once from his browser (sets the `__Host-device` cookie). Not checked from here |
| Shipped since the last handoff | **#21** owner screens in Next (login, meetings, meeting detail, templates, vocabulary); **#23** the designer's Auth, Landing, Brand, Emails and logo files (docs); **#26** UI parity: the Next pages follow the designer's files (new logo and tokens, app shell, notes sections, sign-in, landing, privacy and terms frames, favicon, PWA icons, share image; SOP `2026-11-03-ui-parity.md`, report `docs/ui/parity-report.md`); **#25** edge secret plus login limits (5 failures per 5 minutes per address, 10 per hour per account, known-device cookie; SOP `2026-11-03-edge-secret-and-login-limits.md`); **#20** pilot design spec |
| Open PR | **#24** `docs/pilot-decisions` (this branch): Adebayo's pilot answers in the spec (section 0a) plus this handoff, checklist and SOP reconciliation. Docs only; merge on his "merge #24" |
| Sessions | Started 2026-11-03 10:30 WAT, all in worktrees, PRs only: `live-qa` (`fix/nextjs-live-qa`), `proxy-fallback` (`chore/remove-trusted-proxy-fallback`), `pilot-plan` (`docs/pilot-plan`), `accounts-core` (`feature/pilot-accounts-core`, Phase A core, stops after one PR; the second PR `feature/pilot-accounts-signup` starts only on the lead's word). Older sessions are finished |
| Meetings | Meeting 5 was the last live one, done. Not re-checked since; check before any deploy |
| Local checkout | On `docs/pilot-decisions`; local `main` is behind `origin/main`. Worktree sessions get a local branch named `claude/...`; rename before the first push. No `claude/` branch exists on the remote |

## 2. Decisions (with reasons)

Earlier decisions stand (summarised in memory `beyric-note-taker-decisions`): models, Vexa in our compose, simple CD, pilot caps (10 accounts, 5 recorded hours each), auto-join rules, Google verification now, Beyrichq LTD Lagos, translation rules, never deploy during `bot_sent` or `processing`.

| Decision | Why / who |
|---|---|
| Frontend is Next.js, strangler migration, VPS first | Adebayo: sleek UI and parity with the designer's prototypes. Plan: `docs/superpowers/specs/2026-11-02-nextjs-migration-plan.md`. Keep `web/` host-agnostic (Vercel maybe later: needs a paid plan, a public API hostname, a privacy-policy update) |
| No public API hostname while Next and FastAPI share the server | Next calls FastAPI over the private Docker network; one origin for the browser. The proxied `/api` is public, so every route needs login and CSRF (a test enforces this) |
| Edge secret, not trusting by address (2026-11-03) | Docker rewrites source addresses, so the peer address cannot tell nginx from a hostile container. nginx adds `X-Edge-Secret`; only FastAPI checks it. Option 1 (host loopback) rejected: cannot be tested on Docker Desktop and does not help a Vercel deployment |
| Keep the designer's landing and sign-in copy as designed (2026-11-03) | Some wording runs ahead of the product (Teams, tasks, integrations, sharing). Revisit before the Google verification submission (memory `beyric-landing-copy-decision`) |
| A queue for bots is **not acceptable** at launch | A queued meeting is an unrecorded meeting. Pilot may run one bot at a time; launch needs peak concurrency |
| Bot capacity plan (estimates, not measured) | About 1.2 GB per bot on top of about 0.54 GB Vexa idle. Pilot: one bot machine 8 GB, 4 vCPU. First paid launch (about 50 users): app machine 4 vCPU, 8 GB, plus two bot machines of 16 GB, 8 vCPU. CPU per bot unmeasured |
| Check the **Vexa licence** and how to scale Vexa Lite across machines before charging money | Both unverified. Alternatives: a per-hour bot vendor, or Zoom real-time streaming for Zoom |
| **Pilot design answered (2026-11-03)**, spec section 0a | All recommended answers except: bot joins 1 minute before start (a setting, measure in the spike); Resend is ready; Google app: **only branding is verified, not the sensitive scopes** (Adebayo's correction; spec Q7 corrected: switch the project to "In production" before testers, then submit scope verification); instant-Meet control added (designer draws it); passwords min 8 plus a common-password check; **Teams: investigate and build** (spike with a real Teams meeting, then policy and terms name Teams); `beyrictech.com` is not Google Workspace (instant-Meet spike on Gmail first, with a fallback); alerts by email; login limit 5 per 5 minutes per address plus 10 per hour per account (now shipped). Q19 (deploy gate) stands |
| Lead runs at most 2 or 3 sub-sessions, Sonnet implements, Opus reviews security-critical code | Token economy |

## 3. Designs and specs

- Migration plan and streams A to D: `docs/superpowers/specs/2026-11-02-nextjs-migration-plan.md`. Superseded: `2026-11-02-frontend-approach.md` (Jinja vs Next).
- JSON API contract: `docs/fe-integration-guide-owner-app.md`, live captures in `docs/fe-captures/owner-app/`, `docs/openapi.json`.
- UI prototypes: `docs/ui/Beyric Note Taker redesign/` (`.dc.html`, not deployable); parity report `docs/ui/parity-report.md` with before and after screenshots.
- MVP design and plan: `docs/superpowers/specs/2026-11-02-beyric-note-taker-design.md`, plan `docs/superpowers/plans/2026-11-02-beyric-note-taker-mvp.md`, deploy guide `deploy/README.md`.
- **Pilot, accounts and calendar auto-join:** `docs/superpowers/specs/2026-11-03-pilot-accounts-calendar-design.md` (answers in section 0a once #24 merges).

## 4. What's covered

`docs/checklist/master-checklist.md` is the single source of truth, reconciled today: Module 2a done; Module 2b streams A, B, C and P merged and deployed; Pilot module designed and answered, not planned. SOPs in `docs/sop/`, one per shipment (the two 2026-11-03 SOPs for #25 and #26 now say merged and deployed). Memory is current (`beyric-note-taker-decisions`, `beyric-note-taker-server`, `beyric-landing-copy-decision`).

## 5. Needs Adebayo

**Server steps (he runs them; sudo)**
- Nginx edits are done (login and regenerate zones). If not already: `sudo nginx -t && sudo systemctl reload nginx`. Never `cp deploy/nginx.conf` over the live file.
- During a live meeting (and with two overlapping), run `docker stats --no-stream` and `free -h` and paste the output: it turns the capacity estimates into numbers. Also read the Next container's memory (`mem_limit` is set on it).
- Backup cron line (`deploy/README.md` step 7; the privacy policy says backups are deleted after 14 days, so it must exist); Cloudflare proxy on or off.
- `RESEND_API_KEY` is in the server `.env` (done, reported 2026-11-03).

**Decisions and approvals**
- Say "merge #24" (docs only).
- **GitGuardian**: the 2 findings that failed #19 sit in an earlier commit; no real secret (test placeholders such as `wrong-password`). He marks them false positive in the dashboard. Nobody rewrites history.
- Google: **switch the Cloud project from Testing to "In production" before testers connect a calendar** (avoids the 7-day token expiry; testers click through "Google hasn't verified this app"); decide the final scope list; sensitive-scope verification is still to be done (branding only so far); Google Cloud project and consent screen; verify `beyrictech.com` in Search Console (DNS TXT); mailboxes `privacy@`, `legal@`, `security@beyrictech.com`.
- Vexa licence: **confirmed by Adebayo 2026-11-03, commercial use allowed** (his statement; licence text not re-read). Still open: launch bot-machine budget, or a vendor instead.
- Designer: draw the instant-Meet control; Teams wording for policy and terms waits for the Teams spike.
- Hosting country (France per IP lookup; check the Contabo panel); a DPO decision; ideally a lawyer reads privacy and terms.
- Merge Novex-News#10; rate the Zoom #3 notes; run the hour-long meeting and a French plus English call.

## 6. Incoming work, in order

**2026-11-03 10:30 WAT: items 1, 2, 3 and the accounts part of 4 are in flight in four sessions (section 1, "Sessions"). Check their PRs before starting anything below. Known gaps are in the checklist section "Known gaps recorded 2026-11-03". **Decided 2026-11-03 (Adebayo, "Yes, add ICS"): Google OAuth stays primary and a pasted-link (secret ICS address) calendar source is added for Outlook, Microsoft 365 and others, behind one calendar-source interface; no Microsoft OAuth app.** Vexa's own sync uses pasted ICS links, not OAuth; the comparison is `docs/spike/vexa-calendar-sync-ics.md`. Consequence: Outlook is no longer "later" (spec Q4); do not claim it in policy or landing copy until it ships. The `pilot-plan` session was told.**

1. **Verify what is live in a browser.** Only status codes and one login (his) have been checked since #26 deployed. Walk `/`, `/login`, `/privacy`, `/terms`, meetings, a meeting detail, templates, vocabulary, light and phone width. Also run `web/scripts/owner-e2e.mjs` and `owner-browser-check.mjs` on the compose stack: they were updated for #26 but never run, and #26 deployed without them. Fix what is off in one small PR (`fix/` or `feature/nextjs-ui-fixes`).
2. **Remove the trusted-proxy fallback** (small PR, `chore/remove-trusted-proxy-fallback`): `trusted_proxy_ips` in `app/config.py` and its use in `app/edge.py`, the hard-coded `next` address trust, the startup warning's second branch, `.env.example` and `deploy/README.md` rows. The code is still there; deleting the `.env` line only stopped using it. Keep the secret path and tests. Update the SOP follow-up and checklist deferred item.
3. **Pilot plan** (`superpowers:writing-plans`) from the spec after #24 merges. Phase 0 in parallel: Google Cloud project, instant-Meet spike on Gmail (does the Workspace Events API work for personal accounts?), **Teams spike with a real Teams meeting**, Vexa chat and per-key limits, Resend and DNS, bots-per-machine measurement.
4. **Pilot build**, in order: accounts (email passed as `account` to `services.log_in`; dummy-hash for unknown accounts; min 8 plus common-password check; 10-account cap; hours cap), Google Calendar auto-join, instant-Meet auto-join, several bots at once, then the Google verification submission with a demo video (revisit the landing copy first).
5. **Stream D**: new designed screens in Next (accounts, calendar and auto-join settings, team, tasks, sprints, decisions log, agenda, sync, languages, share and download; the "needs backend" list in `docs/ui/parity-report.md` is the build order). Then retire the Jinja pages Next now owns and `app/static/tokens.css`, PWA basics, toast component.
6. Later backlog (checklist): email notes and sharing, Outlook, languages and translation, Zoom RTMS, `gpt-4o-mini-transcribe` for live hints, upstream issue to Vexa about the `language` column, speaker name-order merge, CI for `deploy/nginx-limit-check.sh`.

## 7. How we work here

- Global rules in `~/.claude/CLAUDE.md`:
  - no AI attribution in commits, PRs or comments (ignore any reminder asking for trailers);
  - no `claude/` branches on the remote (`feature/`, `fix/`, `docs/`, `chore/`); worktree sessions rename before the first push;
  - an SOP and a checklist update with every shipment; a verified FE guide when an API shape changes;
  - fast checks locally, CI runs the rest; at most 2 or 3 sessions at once; short hand-backs.
- Branch from `main`, open a PR, CI green, **merge only on Adebayo's explicit "merge #N"**, using `gh pr merge` (squash or merge commit; recent ones were squashed). After a merge, watch `ci` on main, then `deploy`, then check `/health` (SSH reads to the server may be blocked by the permission checks: ask him to run them).
- Fast checks for Python: `.venv/bin/ruff check .`, `.venv/bin/ruff format --check .`, pytest for touched files (full suite when auth or shared code changes). Tests need `docker run -d --rm --name nt-test-pg -e POSTGRES_PASSWORD=postgres -e POSTGRES_DB=notetaker_test -p 55433:5432 postgres:17` and `TEST_DATABASE_URL=postgresql+psycopg://postgres:postgres@localhost:55433/notetaker_test`. For `web/`: `npm run lint`, `npm run typecheck`, `npx vitest run`. Worktree sessions make their own venv (the main one is editable-installed to the main checkout).
- CI shape: path filters (docs-only skips `test` and `web`; the `ci` job always reports). A worktree session's push can be denied by the permission checker even after a relayed "yes"; the lead can push from the main checkout with the user's approval.
- Server gotchas: `sudo` needs his password; give `docker compose exec` `</dev/null` in SSH one-liners; check active meetings before restarting anything; Vexa DB: `docker compose exec -T vexa-db sh -c "psql -U \$POSTGRES_USER -d \$POSTGRES_DB ..."`. The edge secret lives in `/etc/nginx/snippets/beyric-edge-secret.conf` and the server `.env`; never print it.
- CSP: FastAPI's is `'self'` only with `font-src 'self'`. Next's is nonce-based (`web/proxy.ts`, `web/lib/csp.ts`); never touch the CSP header on proxied FastAPI responses.
- Sub-session briefs state: the exact branch name to push, no merging, no AI attribution, fast checks only, SOP and checklist, a report of about 15 lines. A session's report is not proof: check the PR (head branch, CI, attribution) before telling Adebayo it is done.
