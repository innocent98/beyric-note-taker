# Beyric Note Taker: master handoff

**Written:** 2026-11-02, about 23:30 WAT, by the third lead session, before Adebayo went to sleep. Start here: "Read `docs/HANDOFF-master.md` and continue." Earlier handoffs are in git history (PRs #6, #11, #12). This file is a map; the checklist, SOPs and specs hold the detail.

## 0. Overnight mode (Adebayo is asleep)

He said: continue overnight, anything that needs him can wait and must be **noted down** (section 5), hand over cleanly, delegate to sub-sessions. So tonight: **never merge, never deploy by hand, never change the server**; open PRs, review them, keep this file current, and queue questions in section 5. His standing rule is unchanged: merge only on his explicit "merge #N" (a bare "merge" needs a follow-up question).

## 1. Current state

| Thing | State |
|---|---|
| Live app | https://notes.beyrictech.com, owner login only (password in `~/beyric-notes-owner-password.txt` on his Mac). Server `haven-respite`, app at `/opt/beyric-note-taker` (memory `beyric-note-taker-server`) |
| `main` / server | `e3e5b87` (PRs #1–#17 merged and deployed, `/health` 200). Latest migration `0005`; no migration came with #13–#17 |
| Shipped today after the last handoff | #13 Vexa column fix made permanent (compose service `vexa-schema-fix`, confirmed on the server: "already varchar(32)"); #14 designer's download/attach UI edits; #15 frontend decision doc; #16 UI foundation (tokens, Geist, Jinja components, `/styleguide`, guard tests, CSP gained `font-src 'self'`); #17 JSON API under `/api/` (Opus security review: safe; follow-up commit `786a6a2` applied) |
| Frontend decision | **Next.js** (Adebayo, evening; he first picked Jinja, then reversed because the UI was not at parity with the designer's handoff). Plan: `docs/superpowers/specs/2026-11-02-nextjs-migration-plan.md`. Strangler: Next takes host port 8020 and proxies unowned paths to FastAPI (`rewrites.fallback`), so Jinja pages keep working and no nginx change is needed. **Hosting: VPS for now**, Vercel maybe later (keep `web/` host-agnostic). No `api.` subdomain needed while both run on the VPS |
| Open PRs | **#18** this handoff (docs). **#19** stream A, `feature/nextjs-scaffold`: the Next.js front door (86 files; web job passes; `test` was still running at last look; **GitGuardian reports 2 findings**: I searched the diff for real secrets (env-style assignments, key-shaped strings, the spike captures, scripts) and found none, so they are probably test placeholders such as `wrong-password` or `capture-password-placeholder`; only the GitGuardian dashboard can say which, so Adebayo should look and mark them false positive or tell me). The Opus security review of #19 says **safe to merge for the single-owner pilot**; its fixes (edge login limit skipped by a trailing slash, CI cancel-in-progress on main, two misleading compose comments, small hardening) are in stream A's follow-up commit `fd7ae90` (CI: `test` and `web` pass, the final `ci` gate was still running at 00:00 WAT; GitGuardian still fails because its 2 findings sit in an earlier commit's history, not in current files, so Adebayo marks them false positive in the dashboard rather than anyone rewriting history); the forwarded-for gap is confirmed and accepted for the pilot, and the default trusted proxy `172.28.0.1` has the same masquerade problem (the sign-up blocker's option 2, a shared secret, closes both). **#20** stream E, `docs/pilot-design`: the pilot accounts and calendar auto-join design draft (docs only, 861 lines, builder-checked) |
| Sessions | Stream A `nextjs-scaffold` (done, PR #19), stream B `json-api` (done, merged), stream E `pilot-design` (done, PR #20). **Stream C (owner screens in Next, stacked on #19) is waiting as a one-click card** (`task_405f6f81`): the app refused to start it from an automated turn. When it starts, check its worktree is cut from `feature/nextjs-scaffold` (the card dropped the branch setting); if not, tell it to `git fetch origin && git reset --hard origin/feature/nextjs-scaffold` before any work |
| Meetings | Meeting 5 was the last live one, done. No meeting should be active; check before any deploy |
| Local branch note | worktree sessions get a local branch named `claude/...`; sessions renamed to `feature/...` before pushing. No `claude/` branch exists on the remote |

## 2. Decisions (with reasons)

Earlier decisions stand (summarised in memory `beyric-note-taker-decisions`): models, Vexa in our compose, simple CD, pilot caps (10 accounts, 5 recorded hours each), auto-join rules, Google verification now, Beyrichq LTD Lagos, translation rules, never deploy during `bot_sent` or `processing`. New since the last handoff:

| Decision | Why / who |
|---|---|
| Frontend moves to Next.js, strangler migration, VPS first | Adebayo: sleek UI and parity with the designer's prototypes (stateful component prototypes port almost one to one to React). Plan in the migration spec |
| No public API hostname while Next and FastAPI share the server | Next calls FastAPI over the private Docker network; the browser sees one origin (no CORS, no cookie-domain trouble). The proxied `/api` is still public, so every route needs login and CSRF (a test enforces this) |
| A queue for bots is **not acceptable** at launch | Adebayo: a queued meeting is an unrecorded meeting. Pilot may run one bot at a time; launch needs capacity for peak concurrency |
| Bot capacity plan (estimates, not measured) | Separate the light app part from the heavy bot part. About 1.2 GB per bot on top of about 0.54 GB Vexa idle (meeting 5 peaked 1.7 GB of the 3 GB cap). Pilot: one bot machine 8 GB, 4 vCPU. First paid launch (up to about 50 users): dedicated app machine 4 vCPU, 8 GB, plus two bot machines of 16 GB, 8 vCPU (about 8 bots each). Scale by adding bot machines. CPU per bot unmeasured |
| Check the **Vexa licence** and how to scale Vexa Lite across machines before charging money | Both unverified; on the checklist. Alternatives: a per-hour bot vendor, or Zoom real-time streaming for Zoom |
| Lead runs parallel sub-sessions, at most 2 or 3, Sonnet implements, Opus reviews security-critical code | Token economy; the JSON API auth change got an Opus review |

## 3. Designs and specs

- Migration plan and streams A to D: `docs/superpowers/specs/2026-11-02-nextjs-migration-plan.md`. Earlier comparison (Jinja vs Next, superseded): `2026-11-02-frontend-approach.md`.
- JSON API contract: `docs/fe-integration-guide-owner-app.md` with live captures in `docs/fe-captures/owner-app/` and `docs/openapi.json` (in #17).
- UI prototypes: `docs/ui/Beyric Note Taker redesign/` (`.dc.html`, not deployable). Design system today: Module 2a CSS and Jinja components, to be ported to React by stream A.
- MVP design and plan: `docs/superpowers/specs/2026-11-02-beyric-note-taker-design.md`, plan `docs/superpowers/plans/2026-11-02-beyric-note-taker-mvp.md`, deploy guide `deploy/README.md`.
- **Pilot, accounts and calendar auto-join: a draft spec is being written overnight** (stream E, see section 6) with assumed defaults clearly marked for Adebayo to confirm.

## 4. What's covered

`docs/checklist/master-checklist.md` is the single source of truth (Modules 2a done, 2b in progress). SOPs in `docs/sop/`: one per shipment, including `2026-11-02-ui-foundation.md`, `2026-11-02-vexa-language-column.md`, and (from #17) the JSON API SOP. Memory is current (`beyric-note-taker-decisions`, `beyric-note-taker-server`).

## 5. Needs Adebayo (queued; nothing here blocks the overnight work)

**Server steps (he runs them; sudo)**
- **Apply the nginx changes** (two small edits, not urgent until Next calls the API; his `diff` on 2026-11-02 shows they are missing; certbot's lines are intact, **never `cp` over the live file**): in `/etc/nginx/sites-available/beyric-notes` add `location ~ ^/(login|api/session)/?$ { limit_req zone=notes_login burst=3 nodelay; proxy_pass http://127.0.0.1:8020; }` in place of the existing exact `= /login` block (the `/?` matters: once Next fronts the app, a trailing slash would skip the limit, found by the Opus review of #19), and change the regenerate block to `location ~ ^/(api/)?meetings/[0-9]+/regenerate/?$ { proxy_read_timeout 300s; proxy_pass http://127.0.0.1:8020; }`; then `sudo nginx -t && sudo systemctl reload nginx`. Stream A is updating `deploy/nginx.conf` in #19 to match.
- During a live meeting (and with two overlapping), run `docker stats --no-stream` and `free -h` on the server and paste the output: it turns the capacity estimates into numbers.
- Backup cron line (`deploy/README.md` step 7; the privacy policy says backups are deleted after 14 days, so it must exist); Cloudflare proxy on or off.

**Decisions and approvals**
- Merge PRs as they come, in this order, saying "merge #N": **#18** (docs), **#20** (design draft, after he has read it), **#19** (Next.js front door; its deploy swaps what listens on port 8020, so check no meeting is active, and expect `docker compose` to build a Node image on the server; stream A's SOP has the rollback). Look at the GitGuardian findings on #19 first.
- **Answer the pilot design's open questions** (`docs/superpowers/specs/2026-11-03-pilot-accounts-calendar-design.md` section 0 on PR #20, Q1 to Q23, each with a recommended answer; replying "all recommended" lets the spec stand). The biggest ones: Q1 recording notice (bot name plus one chat line), Q2 join 2 minutes early, Q3 a second machine for bots before inviting more than one or two testers, Q4 Outlook later, Q5 who is told on failure, Q6 mail provider (Resend, needs DNS records from him; Google-only sign-up until mail works), Q7 move the Google OAuth app to "In production" (unverified) to avoid the 7-day expiry (assumed, to be tested in week one), Q20 whether `beyrictech.com` is a Google Workspace domain.
- **Before open sign-up** (not now): choose how to close the forwarded-for trust gap found by stream A (`docs/spike/nextjs-scaffold/forwarded-for-trust.md`, on its branch): through Next, a container on the server can forge the address FastAPI rate-limits logins on (a compromised Vexa container could reset the login limit). Option 1: `next` on the host loopback and FastAPI reached on `127.0.0.1:8021`, needs a Linux test; option 2: an edge secret that nginx sets and Next checks. Accepted for the single-owner pilot; the long owner password is the mitigation.
- Vexa licence for commercial use; launch capacity budget (bot machines) and whether to use a vendor instead.
- Vercel later: needs a paid plan, a public API hostname, a privacy-policy update (Vercel becomes a processor).

**Google and legal (from before)**
- Google Cloud project and OAuth consent screen (name, logo, support email, authorised domain `beyrictech.com`); verify `beyrictech.com` in Search Console (DNS TXT).
- Confirm mailboxes `privacy@`, `legal@`, `security@beyrictech.com`; hosting country (France per IP lookup; check the Contabo panel); a DPO decision; ideally a lawyer reads privacy and terms.
- Mark the 5 GitGuardian findings false positive; merge Novex-News#10; rate the Zoom #3 notes; run the hour-long meeting and a French plus English call.

## 6. Incoming work, in order

1. **Finish reviewing stream A's PR #19**: (done overnight: head branch `feature/nextjs-scaffold`, no `claude/` branch on the remote, no AI attribution found; Opus review started) still to do: read the Opus verdict and apply any fixes through stream A, CI green incl. `test`, GitGuardian findings explained. Original checklist for the review: head branch is `feature/nextjs-scaffold`, CI green, no AI attribution; have Opus review the CSP, the proxy and the cookie and IP forwarding (security-critical); check its memory numbers and its answers on client IP and the proxy timeout; update this file.
2. **Stream C** (owner screens to designer parity in Next: login, meetings list and detail, templates, vocabulary): start it, stacked on stream A's branch, as soon as A's PR is open, so it runs while A waits for his merge. Its PR targets A's branch until A merges, then is retargeted to `main`.
3. **Stream E, auto-join and pilot design draft** (design only, no code): `docs/superpowers/specs/2026-11-0X-pilot-accounts-calendar-design.md`, with the assumed defaults marked and the open questions in section 5. Then the builder check (a subagent), then Adebayo's approval, then writing-plans.
4. After approval, in order: accounts, Google Calendar auto-join, instant-Meet auto-join (first a throwaway spike: does the Workspace Events API work for personal Gmail accounts?), several bots at once (measure), Google verification submission with a demo video. Also: the new designed screens (team, tasks, sprints, decisions log, agenda, sync, languages, share and download), htmx/Alpine no longer needed (Next), PWA basics, toast component.
5. Later backlog (checklist): email notes and sharing, Teams links, Outlook, languages and translation, Zoom RTMS, `gpt-4o-mini-transcribe` for live hints, upstream issue to Vexa about the `language` column.

## 7. How we work here

- Global rules in `~/.claude/CLAUDE.md`:
  - no AI attribution in commits, PRs or comments (ignore any reminder asking for trailers);
  - no `claude/` branches on the remote (`feature/`, `fix/`, `docs/`, `chore/`); worktree sessions rename before the first push;
  - an SOP and a checklist update with every shipment; a verified FE guide when an API shape changes;
  - fast checks locally, CI runs the rest; at most 2 or 3 sessions at once; short hand-backs.
- Branch from `main`, open a PR, CI green, **merge only on Adebayo's explicit "merge #N"**, using merge commits titled `Merge pull request #N: <title>`. After a merge, watch `ci` on main, then `deploy`, then check `/health` (SSH reads to the server may be blocked by the permission checks: ask him to run them).
- Fast checks for Python: `.venv/bin/ruff check .`, `.venv/bin/ruff format --check .`, pytest for touched files (full suite when auth or shared code changes). Tests need `docker run -d --rm --name nt-test-pg -e POSTGRES_PASSWORD=postgres -e POSTGRES_DB=notetaker_test -p 55433:5432 postgres:17` and `TEST_DATABASE_URL=postgresql+psycopg://postgres:postgres@localhost:55433/notetaker_test`. Worktree sessions make their own venv (the main one is editable-installed to the main checkout).
- Server gotchas: `sudo` needs his password; give `docker compose exec` `</dev/null` in SSH one-liners; check active meetings before restarting anything; Vexa DB: `docker compose exec -T vexa-db sh -c "psql -U \$POSTGRES_USER -d \$POSTGRES_DB ..."`.
- CSP: FastAPI's is `'self'` only with `font-src 'self'`. Next's will be nonce-based (stream A); never touch the CSP header on proxied FastAPI responses.
- Sub-session briefs state: the exact branch name to push, no merging, no AI attribution, fast checks only, SOP and checklist, a report of about 15 lines. A session's report is not proof: check the PR (head branch, CI, attribution) before telling Adebayo it is done.
