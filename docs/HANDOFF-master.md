# Beyric Note Taker: master handoff

**Written:** 2026-11-03, about 05:45 WAT, by the third lead session, for the fourth lead (Adebayo starts it himself; do not self-start). Start here: "Read `docs/HANDOFF-master.md` and continue." Earlier handoffs are in git history (PRs #6, #11, #12). This file is a map; the checklist, SOPs and specs hold the detail.

## 0. Mode

Adebayo is awake again. Standing rules: merge only on his explicit "merge #N" (a bare "merge" needs a follow-up question); never deploy while a meeting is `bot_sent` or `processing`; he runs anything that needs `sudo` on the server. Overnight everything was PRs only; this morning he merged #18 and #19 himself.

## 1. Current state

| Thing | State |
|---|---|
| Live app | https://notes.beyrictech.com, owner login only (password in `~/beyric-notes-owner-password.txt` on his Mac). Server `haven-respite`, app at `/opt/beyric-note-taker` (memory `beyric-note-taker-server`) |
| `main` / server | `9f76c84` (#19 squash-merged), **deployed 2026-11-03 04:38 UTC**; `/health`, `/welcome`, `/privacy`, `/login`, `/` all 200 and `/welcome` carries the nonce CSP. Latest migration `0005`; none came with #13–#21. #18 and #19 were squash-merged (earlier PRs used merge commits titled `Merge pull request #N`); either is fine, pick one and say so |
| Shipped today after the last handoff | #13 Vexa column fix made permanent (compose service `vexa-schema-fix`, confirmed on the server: "already varchar(32)"); #14 designer's download/attach UI edits; #15 frontend decision doc; #16 UI foundation (tokens, Geist, Jinja components, `/styleguide`, guard tests, CSP gained `font-src 'self'`); #17 JSON API under `/api/` (Opus security review: safe; follow-up commit `786a6a2` applied) |
| Frontend decision | **Next.js** (Adebayo, evening; he first picked Jinja, then reversed because the UI was not at parity with the designer's handoff). Plan: `docs/superpowers/specs/2026-11-02-nextjs-migration-plan.md`. Strangler: Next takes host port 8020 and proxies unowned paths to FastAPI (`rewrites.fallback`), so Jinja pages keep working and no nginx change is needed. **Hosting: VPS for now**, Vercel maybe later (keep `web/` host-agnostic). No `api.` subdomain needed while both run on the VPS |
| Open PRs | **#21** stream C, `feature/nextjs-app-screens`, **base `main`**, 6 commits: owner screens in Next (login, meetings list and detail, templates, vocabulary; `/`, `/login`, `/logout` owned by Next). CI green (`test` skipped by path filter, correct), GitGuardian green, no `claude/` branch name, no AI trailers. Merging it deploys the new screens, so check no meeting is active first. **#20** `docs/pilot-design`: pilot accounts and calendar auto-join design (docs only, 861 lines); merge after he has read it. **#22** `docs/handoff-5`: this file. **#23** `docs/designer-ui-auth-landing-logo`: the designer's new Auth, Landing, Brand, Emails, logo files (docs only) |
| Sessions | All four started this run are finished and idle: `nextjs-scaffold` (#19, merged), `json-api` (#17, merged), `pilot-design` (#20), `stream-c-owner-app-scree` (`local_c88bc2f3-1432-4119-ad9e-00ae6072636c`, #21). Its local branch is `claude/suspicious-yalow-8b24a3`; the remote branch is the conventional one. A worktree session's push can be denied by the permission checker even after a relayed "yes"; the lead can push with the user's own approval from the main checkout (`--force-with-lease` pinned to the old sha) |
| Meetings | Meeting 5 was the last live one, done. Not re-checked after that; check before any deploy |
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
- **Pilot, accounts and calendar auto-join: a draft spec is in PR #20** (stream E) with assumed defaults clearly marked; Adebayo confirms them (section 5).

## 4. What's covered

`docs/checklist/master-checklist.md` is the single source of truth (Module 2a done; 2b Next.js migration: streams A and B merged and deployed, stream C in PR #21). SOPs in `docs/sop/`: one per shipment, including `2026-11-02-ui-foundation.md`, `2026-11-02-vexa-language-column.md`, and (from #17) the JSON API SOP. Memory is current (`beyric-note-taker-decisions`, `beyric-note-taker-server`).

## 5. Needs Adebayo

**Server steps (he runs them; sudo)**
- nginx edits: **done 2026-11-03** (his pasted file has `^/(login|api/session)/?$` and `^/(api/)?meetings/[0-9]+/regenerate/?$`). Confirm he reloaded (`sudo nginx -t && sudo systemctl reload nginx`); optional tidy: delete nothing else, move the regenerate comment back above its block. Never `cp deploy/nginx.conf` over the live file.
- During a live meeting (and with two overlapping), run `docker stats --no-stream` and `free -h` on the server and paste the output: it turns the capacity estimates into numbers. Also read the Next container's memory (`mem_limit` is set on it).
- Backup cron line (`deploy/README.md` step 7; the privacy policy says backups are deleted after 14 days, so it must exist); Cloudflare proxy on or off.

**Decisions and approvals**
- Merge, saying "merge #N": **#20** (after he has read it), **#21** (owner screens; no active meeting), **#22** and **#23** (docs, any time).
- **GitGuardian**: the 2 findings that failed #19 sit in an earlier commit of that branch; no real secret was found by the lead (test placeholders such as `wrong-password`). He marks them false positive in the dashboard. Nobody rewrites history.
- **Pilot design questions: ANSWERED 2026-11-03** (spec section 0a, PR #24): all recommended except: bot joins 1 minute before start (setting, measure in the spike), Resend is ready (needs `RESEND_API_KEY` in the server `.env`), Google app is verified (confirm the approved scopes cover Calendar read and, if the spike works, the Meet scopes), instant-Meet control added (designer draws it), passwords min 8 + common-password check, **Teams: investigate and build** (spike with a real Teams meeting, then policy/terms name Teams), `beyrictech.com` is not Google Workspace (instant-Meet spike on Gmail first, with a fallback), alerts by email, login limit **5 tries per 5 minutes per address plus 10 failures per hour per account**. Q19 (deploy gate) explained to him; stands.
- **Forwarded-for gap: being closed now** (his decision 2026-11-03, edge-secret design, branch `fix/login-limits-and-edge-secret`; needs one server step from him when it ships: the secret in nginx and the server `.env`). Background on the gap (`docs/spike/nextjs-scaffold/forwarded-for-trust.md`): through Next, a container on the server can forge the address FastAPI rate-limits logins on; the default trusted proxy `172.28.0.1` has the same masquerade problem. Option 1: `next` on the host loopback, FastAPI on `127.0.0.1:8021`, needs a Linux test; option 2: an edge secret nginx sets and Next checks (closes both). Accepted for the single-owner pilot.
- Designer files in PR #23 define the next screens (new Auth, Landing, logo exports): he decides whether the Next pages follow them now or after #21.
- Vexa licence for commercial use; launch capacity budget (bot machines) and whether to use a vendor instead.
- Vercel later: needs a paid plan, a public API hostname, a privacy-policy update (Vercel becomes a processor).

**Google and legal (from before)**
- Google Cloud project and OAuth consent screen (name, logo, support email, authorised domain `beyrictech.com`); verify `beyrictech.com` in Search Console (DNS TXT).
- Confirm mailboxes `privacy@`, `legal@`, `security@beyrictech.com`; hosting country (France per IP lookup; check the Contabo panel); a DPO decision; ideally a lawyer reads privacy and terms.
- Merge Novex-News#10; rate the Zoom #3 notes; run the hour-long meeting and a French plus English call.

## 6. Incoming work, in order

1. **Verify what is live**: open `/welcome` and the Jinja `/login` in a browser (the lead only checked status codes). After #21 merges and deploys, walk login, meetings, a meeting detail, templates and vocabulary on the live site and fix what is off through stream C.
2. **Move the Next pages to the designer's latest files** (PR #23: Auth, Landing, Brand, Emails, logo): landing and auth screens, favicon and logo from the exports, then privacy and terms pages follow the updated files. One sub-session (Sonnet), branch `feature/nextjs-landing-auth`, based on `main` after #21.
3. **Pilot build** (design answered; PR #20 merged): writing-plans first, then: writing-plans, then in order accounts, Google Calendar auto-join, instant-Meet auto-join (a throwaway spike first: does the Workspace Events API work for personal Gmail?), several bots at once (measure), Google verification submission with a demo video.
4. **Stream D, new designed screens** in Next: accounts, calendar and auto-join settings, team, tasks, sprints, decisions log, agenda, sync, languages, share and download. Then retire the Jinja pages Next now owns, PWA basics, toast component.
5. Sign-up blocker before inviting anyone else: the forwarded-for fix (section 5).
6. Later backlog (checklist): email notes and sharing, Teams links, Outlook, languages and translation, Zoom RTMS, `gpt-4o-mini-transcribe` for live hints, upstream issue to Vexa about the `language` column.

## 7. How we work here

- Global rules in `~/.claude/CLAUDE.md`:
  - no AI attribution in commits, PRs or comments (ignore any reminder asking for trailers);
  - no `claude/` branches on the remote (`feature/`, `fix/`, `docs/`, `chore/`); worktree sessions rename before the first push;
  - an SOP and a checklist update with every shipment; a verified FE guide when an API shape changes;
  - fast checks locally, CI runs the rest; at most 2 or 3 sessions at once; short hand-backs.
- Branch from `main`, open a PR, CI green, **merge only on Adebayo's explicit "merge #N"**, using `gh pr merge` (squash or merge commit; #18 and #19 were squashed). After a merge, watch `ci` on main, then `deploy`, then check `/health` (SSH reads to the server may be blocked by the permission checks: ask him to run them).
- Fast checks for Python: `.venv/bin/ruff check .`, `.venv/bin/ruff format --check .`, pytest for touched files (full suite when auth or shared code changes). Tests need `docker run -d --rm --name nt-test-pg -e POSTGRES_PASSWORD=postgres -e POSTGRES_DB=notetaker_test -p 55433:5432 postgres:17` and `TEST_DATABASE_URL=postgresql+psycopg://postgres:postgres@localhost:55433/notetaker_test`. Worktree sessions make their own venv (the main one is editable-installed to the main checkout).
- Server gotchas: `sudo` needs his password; give `docker compose exec` `</dev/null` in SSH one-liners; check active meetings before restarting anything; Vexa DB: `docker compose exec -T vexa-db sh -c "psql -U \$POSTGRES_USER -d \$POSTGRES_DB ..."`.
- CSP: FastAPI's is `'self'` only with `font-src 'self'`. Next's is nonce-based (live; `web/proxy.ts`, `web/lib/csp.ts`); never touch the CSP header on proxied FastAPI responses.
- Sub-session briefs state: the exact branch name to push, no merging, no AI attribution, fast checks only, SOP and checklist, a report of about 15 lines. A session's report is not proof: check the PR (head branch, CI, attribution) before telling Adebayo it is done.
