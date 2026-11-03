# Beyric Note Taker: master checklist

**Status (2026-11-03):** MVP live at notes.beyrictech.com (PRs #1–#23, #25 and #26 merged and deployed 2026-11-03; #24 docs open). The UI foundation (Module 2a) and the JSON API are shipped; the frontend is moving to Next.js (Module 2b: the front door, the owner screens and the UI parity pass are merged and live). The Pilot module (accounts plus calendar auto-join) has an approved design (Adebayo answered the questions 2026-11-03); the plan and the Phase 0 spikes are next. Next steps and Adebayo's queued items are in `docs/HANDOFF-master.md`.

Legend: `[x]` done and verified, `[ ]` not done.

## Module 0: Vexa spike (done 2026-11-02, spend check open)

Goal: decide whether self-hosted Vexa can be the bot layer.

- [x] Create project and `chore/vexa-spike` branch
- [x] Clone Vexa at commit `31122f9` into `spike/vexa` (git-ignored)
- [x] Confirm from Vexa's config docs that OpenAI is a supported transcription backend
- [x] Vexa Lite runs on the Mac (2026-11-02: gateway, agent API and terminal healthy; about 830 MB idle)
- [x] OpenAI key added to `spike/vexa/.env` by Adebayo
- [x] Bot joins and transcribes a Google Meet call (2026-11-02: 2-minute call, real speaker names, recording saved)
- [x] Bot joins and transcribes a Zoom call Adebayo hosts (2026-11-02: joined and recorded; speaker labels and second-speaker audio need review)
- [x] Bot joins a Zoom call someone else hosts, including a waiting room (2026-11-02: second account hosted)
- [ ] Measure real OpenAI spend per meeting hour
- [x] Summary quality checked on a real transcript (2026-11-02: usable, transcript errors carry through)
- [x] Spike report and go / no-go decision: go, see `docs/spike/vexa-spike-report.md`

## Module 1: MVP (planned, see `docs/superpowers/plans/2026-11-02-beyric-note-taker-mvp.md`)

- [x] Task 0: Vexa check and project setup (2026-11-02: settings, models, first migration with seeded templates, CI)
- [x] Task 1: Vexa client (2026-11-02: send bot, poll by id, download audio, delete recording, stop bot; malformed fields rejected)
- [x] Task 2: Audio preparation (2026-11-02: mono 32 kbps Opus chunks up to 15 min, silence-aware cuts, ffmpeg in Docker and CI)
- [x] Task 3: Transcription with custom vocabulary (2026-11-02: terms sent as a capped `keywords` list; live-checked with two terms)
- [x] Task 4: Speakers and notes (2026-11-02: label_speakers, generate_notes, shared retry; live-evaluated)
- [x] Task 5: Worker pipeline (2026-11-02)
- [x] Task 6: Web pages (2026-11-02: login, meetings, retry/regenerate, templates, vocabulary, `web` service; checked at 375 px)
- [ ] Task 7: Deploy (server: Contabo `haven-respite`, shared with Haven and Novex; domain `notes.beyrictech.com`)
  - [x] Deploy files for a shared server: Vexa inside compose on localhost, app on `127.0.0.1:8020`, prefixed nginx zones (2026-11-02, validated locally)
  - [x] CD: `deploy.yml` after `ci` on `main`, forced-command SSH key, `deploy/deploy.sh` with rollback (2026-11-02, not yet run)
  - [x] Server: repo (read-only deploy key), `.env` and `.env.vexa`, Vexa up, key minted, app up on `127.0.0.1:8020` (2026-11-02; only 22/80/443 open from outside; Haven and Novex still healthy)
  - [x] Cloudflare: works proxied or DNS only; nginx restores the real address from `CF-Connecting-IP` (2026-11-02, still proxied at last check)
  - [x] HTTPS: `sites-available/beyric-notes` + `certbot --nginx`, Let's Encrypt cert to 2027-01-31, HTTP redirects (2026-11-02)
  - [x] Login limit spoof test: forged `X-Forwarded-For` and `CF-Connecting-IP` ignored, app logged the real address, nginx answered 429 (2026-11-02)
  - [x] One backup and a scratch restore on the server (2026-11-02: 4 templates restored)
  - [ ] Nightly backup cron (Adebayo)
  - [x] CD key (forced command `deploy.sh`) and four GitHub secrets (2026-11-02)
  - [x] One deploy through CD: PR #1 merged as `b0eaea8`, CI then `deploy` succeeded, backup taken first (2026-11-02)
  - [ ] Step 9 verification, including Haven and Novex still healthy
- [ ] Task 8: Real-world acceptance
  - [x] First live Zoom meeting processed end to end: about 6 minutes, full speaker names, notes (2026-11-02, after the bigint fix, PR #3)
  - [x] Meet bot joins and records; a muted call correctly fails as "No speech was found" (2026-11-02)
  - [x] Meet call with speech: meeting 5, about 56 min, ready 6 min after the call; 4 named speakers, all six note sections, useful "Please check" items; Adebayo: "it went well" (2026-11-02). Live speaker hints were fixed mid-call (see the Vexa language-column SOP)
  - [ ] The same person can appear under two name orders ("Godstime America" / "America Godstime"); merge name variants in speaker labels
  - [ ] An hour-long meeting, rated against the old tool (meeting 5 was 56 min and went well; a formal rating against Fireflies is still open)
  - [ ] Real OpenAI spend per meeting hour

## Next, after deploy (agreed 2026-11-02)

- [ ] Email the notes to the owner when a meeting is ready
  - [ ] Choose a sending service (Resend or Postmark) and verify the domain
  - [ ] Send summary, action items and a link to the meeting page; never the audio
  - [ ] A failed email must not fail the meeting; show it on the meeting page
- [x] Notes model switched to `gpt-6-luna` after a side-by-side eval (2026-11-02, see SOP)
- [ ] Try `gpt-4o-mini-transcribe` for Vexa's live speaker hints on one acceptance call; switch if speaker names hold up (saves about $0.18/hour)

## Found in live use (2026-11-02)

- [x] Zoom sometimes blocks the bot's browser (`zoom_requires_rtms`; 2 of 3 joins): retry automatically (up to 3 bots) and show Vexa's reason instead of the generic message (2026-11-02; not yet seen live)
- [ ] Longer term, if Zoom keeps blocking: Zoom RTMS (needs a Zoom Marketplace app)
- [x] Log the database error code in the worker, not just the exception type (it hid the bigint bug) (2026-11-02)
- [x] Vexa dropped live segments: whisper's language names ("haitian creole") overflow Vexa's `varchar(10)`. Widened to 32 live on the server (2026-11-02, meeting 5)
- [x] Make the Vexa column fix permanent: one-shot `vexa-schema-fix` compose service, runs on every deploy (branch `fix/vexa-language-column`, 2026-11-02)
- [ ] Report the narrow `language` column upstream to Vexa

## Module 2a: UI foundation (before accounts; decided 2026-11-02)

Goal: one look everywhere (the logged-in app is still blue and system-ui while the public pages are teal), and parts every later screen reuses. Plan in the frontend-approach spec.

- [x] `tokens.css`: brand colours, type, spacing, radius, shadow, light and dark, from the designer's prototypes; `body.public` split removed (`feature/ui-foundation`, 2026-11-02)
- [x] Self-host Geist in `/static/fonts/`; CSP gained `font-src 'self'` only
- [x] Core Jinja macros plus `components.css`: button, badge, alert, card, fields, segmented control, menu, dialog, tabs, list row, empty state, page head
  - [ ] Toast (with the first feature that needs it)
- [x] Owner-only `noindex` `/styleguide` showing every component and state
- [x] Guard tests: no colour literal outside `tokens.css`, no inline `style=`/`on*=`, every macro in the style guide, shared head (`tests/test_ui_guards.py`)
- [x] Move the existing 10 templates onto the foundation
  - [x] Checked by eye: login, Meetings, ready and processing meeting, style guide, landing; light and dark, desktop and phone
  - [ ] Still to check by eye: Templates, Vocabulary, failed meeting, Privacy and Terms on a phone
- [ ] Spike: htmx and Alpine (CSP build) under the strict CSP, self-hosted
- [ ] PWA basics: manifest, icons, service worker

## Module 2b: Next.js migration (decided 2026-11-02; plan in `docs/superpowers/specs/2026-11-02-nextjs-migration-plan.md`)

Strangler pattern: Next.js takes host port 8020 and proxies any path it does not own to FastAPI, so pages move one at a time with no nginx change and the pilot stays up. Jinja screens and Module 2a's CSS remain until each page is replaced.

- [ ] Stream A, scaffold and spike (`feature/nextjs-scaffold`; SOP `docs/sop/2026-11-02-nextjs-scaffold.md`)
  - [x] `web/` scaffold: Next.js 16, App Router, TypeScript, npm, Node 22, standalone output, ESLint, typecheck, Vitest
  - [x] Docker: multi-stage non-root image, compose service `next` (127.0.0.1:8020, `mem_limit`, healthcheck), FastAPI moved to 127.0.0.1:8021, `deploy.sh` builds and checks both and still rolls back, `deploy/README.md`
  - [x] Fallback proxy proven for the Jinja pages: cookies, `Set-Cookie`, 303, POST bodies, CSRF, headers, long requests, the visitor's address (`web/scripts/proxy-check.mjs`)
  - [x] Nonce CSP for Next-rendered pages, no `unsafe-inline`, zero console errors in a real browser (`web/scripts/browser-check.mjs`; 2026-11-02)
  - [x] Tokens, fonts, `components.css` ported; React components for every Jinja macro; `/styleguide` (non-production); guard tests ported
  - [x] Public pages in Next: `/privacy`, `/terms` live (text equal to the Jinja templates, test-enforced); the landing moved from `/welcome` to `/` in stream C
  - [x] SEO basics: metadata, canonical, Open Graph, JSON-LD, `sitemap.ts`, `robots.ts`, `llms.txt`, favicon, manifest and icons
  - [x] Container memory measured on a Mac: 37 MiB idle, 65 MiB peak after 240 requests (not yet on the server); `mem_limit` stays 512m as a ceiling
  - [ ] CI: `web` job with path filters (docs-only skips both, Python-only skips Node, web-only skips Python), always-reporting `ci` job
  - [ ] Adebayo's server steps (SOP "Operate"): subnet and memory check, deploy outside meetings, post-deploy checks, send back `docker stats` for `next`
  - [x] Forwarded-for trust gap closed (2026-11-03, PR #25 merged and deployed; live `/health` shows `x-edge-ok: true`): edge secret (`X-Edge-Secret` set by nginx, passed by Next, checked by FastAPI in `app/edge.py`); login limits are now 5 failures per 5 min per address plus 10 per hour per account. SOP `docs/sop/2026-11-03-edge-secret-and-login-limits.md`
  - [x] Opus review fixes in the same PR (2026-11-03): known-device cookie so strangers cannot lock the owner out, lockout log, missing-secret warning and `X-Edge-Ok` on `/health`, IPv6 `/64` keys, bounded limiter, nginx location guard test, README never prints the secret
  - [x] Server steps for the edge secret done by Adebayo (secret, nginx include, `.env`, redeploy; `x-edge-ok: true` seen live 2026-11-03). Reported by him, not checked from here: `TRUSTED_PROXY_IPS` deleted from the server `.env`, and one browser login done (sets the device cookie)
- [x] Stream B, JSON API (`feature/json-api`, 2026-11-02, PR open, not merged): `/api/` for session, meetings, templates, vocabulary with a CSRF header; tests; captured responses; `docs/fe-integration-guide-*.md`
  - [x] Shared rules extracted to `app/services.py`; Jinja routes and all 338 earlier tests unchanged
  - [x] `/api/session` (get, login, logout), meetings (list, detail, create, cancel, retry, regenerate), templates (list, create, update), vocabulary (list, create, delete)
  - [x] 401 JSON never a redirect, `X-CSRF-Token` on every write (403 JSON), one error shape, strict bodies, `notes` plus `notes_html`
  - [x] Test that enumerates every `/api` route and fails if one is public by accident (`tests/test_api.py`, 106 tests)
  - [x] `docs/openapi.json` plus `scripts/export_openapi.py`; docs and `/openapi.json` stay off in the app
  - [x] Live captures (`scripts/capture_owner_api.py`, 81 files) and `docs/fe-integration-guide-owner-app.md`; SOP `docs/sop/2026-11-02-json-api.md`
  - [ ] Opus review of the auth path before merge (lead)
  - [ ] Follow-ups for stream A and the lead: nginx `/api/` locations (regenerate 300 s timeout, login limit zone), real visitor address through the Next proxy, TypeScript type generation
- [x] Stream C, owner app screens (`feature/nextjs-app-screens`, PR #21, merged 2026-11-03; parity pass in stream P below; SOP `docs/sop/2026-11-03-nextjs-owner-screens.md`)
  - [x] Typed API client generated from `docs/openapi.json`; one error shape, CSRF on every write, 401 to `/login`; tested against the captured responses
  - [x] Next owns `/`, `/login`, `/logout`, `/meetings/[id]`, `/templates`, `/vocabulary`; `/styleguide` owner-only; `/welcome` redirects to `/`; the rest still falls through to FastAPI (rollback: SOP "Operate")
  - [x] App shell (sidebar, phone top bar), Meetings (new meeting, list, paging, stale-worker alert, empty state), meeting page (notes, transcript, copy, rewrite, cancel with confirm, retry, notices, 30 s polling), Templates, Vocabulary, login; light and dark
  - [x] 89 vitest tests, 41-check end-to-end through Next on a compose stack, real-browser pass at 375 and 1280 px in both themes
  - [x] CI green (PR merged and deployed 2026-11-03)
  - [ ] Not built, waiting on the API (stream D): Download menu, Share dialog, translation, template delete, participants and duration
  - [ ] Not verified: real Vexa bot and real OpenAI rewrite through the screens, Safari and Firefox, the live server
- [ ] Stream P, UI parity with the designer's files (`feature/nextjs-ui-parity`, PR #26, merged and deployed 2026-11-03; SOP `docs/sop/2026-11-03-ui-parity.md`; report `docs/ui/parity-report.md`)
  - [x] Gap check of every designer file against the Next pages at 1440, 768 and 390 px; report with before and after screenshots
  - [x] Brand tokens from `Brand.dc.html` (light only: the dark overrides are gone), new "b" logo, favicon, PWA and Apple icons, share image
  - [x] App shell, Meetings, meeting page (notes split into decisions, questions, "Please check"), Templates, Vocabulary at parity
  - [x] Sign-in screen (form plus teal panel), landing page, privacy and terms frames with "On this page", 404 and error pages
  - [x] Guard tests kept; new tests for notes sections, legal contents list, logo, landing links, Show / Hide
  - [x] CI green (PR merged and deployed 2026-11-03)
  - [x] Decided 2026-11-03 (Adebayo): keep the designer's landing and sign-in words even though some run ahead of the product (Teams, tasks, integrations, sharing); revisit before the Google verification submission
  - [ ] Run `owner-e2e.mjs` and `owner-browser-check.mjs` on the compose stack (PR #26 deployed without them; no record that they ran)
  - [ ] Needs backend, not built (listed in the report): sign-up, forgot password, SSO, accounts and sidebar identity, calendar and auto-join card, series, tasks, projects, decisions, review, team, settings, download, share, translation, agenda, template delete, Emails
- [ ] Stream D, new designed screens: accounts and pilot, calendar and auto-join, team, tasks, sprints, share and download, translation
- [ ] Retire Jinja and the Module 2a CSS once every page is migrated
- [ ] Measure Next.js memory on the server (Adebayo runs the SSH steps)

## Module 2: Pilot, accounts plus calendar auto-join (designing, 2026-11-02)

Agreed with Adebayo: open sign-up capped at 10 accounts; 5 recorded hours per account for the whole pilot (admin uncapped); the frontend moves to Next.js (decided 2026-11-02 evening, superseding the Jinja decision; see Module 2b); company is Beyrichq LTD, Lagos, Nigeria. Design: `docs/ui/Beyric Note Taker redesign/` (Calendar card, Upcoming list, Settings → Calendar, legal pages).

- [ ] Public pages for Google verification
  - [x] Landing, Privacy and Terms built and tested, text true to the app (`feature/public-pages`, 2026-11-02; see SOP)
  - [x] Merged and live (PR #10, 2026-11-02): `/`, `/privacy`, `/terms` return 200
  - [ ] Checked in a browser, light and dark
  - [ ] Adebayo: Google Cloud project, OAuth consent screen, verify beyrictech.com in Search Console, mailboxes privacy@/legal@/security@
  - [ ] Submit for verification (needs the working calendar flow and a demo video)
- [x] Frontend approach decided: stay on Jinja, with parity and flexibility as conditions (`docs/superpowers/specs/2026-11-02-frontend-approach.md`, 2026-11-02)
- [ ] Written design (`docs/superpowers/specs/2026-11-03-pilot-accounts-calendar-design.md`): **draft written and builder-checked 2026-11-03, awaiting Adebayo's approval** (23 open questions with recommended answers at the top of the spec; personal-Gmail support for instant-Meet events is undocumented, spike P0.2 decides)
  - [ ] Adebayo answers Q1 to Q23
  - [ ] Phase 0 (parallel): Google Cloud project and billing; instant-Meet spike S1 to S7; Vexa chat and per-key limit check; mail provider and DNS; bots-per-machine measurement
- [ ] Plan (superpowers:writing-plans)
- [ ] Accounts: sign-up (email+password, Google sign-in), verification email, password reset, per-user data, 10-account cap with waitlist, hours cap
- [ ] Calendar auto-join (Google): connect, upcoming list with per-meeting toggle, rules (all / only ones I organise / only when I turn it on), template for calendar meetings, scheduler in the worker
- [ ] Instant Meet auto-join: Workspace Events subscription to the user's Meet spaces via Pub/Sub; first a throwaway spike, including whether personal Gmail accounts work
- [ ] More than one bot at a time (overlapping meetings across users); measure how many the server can run
- [ ] Clear "calendar disconnected" state (7-day expiry while Google is unverified)

## Backlog

- [ ] Outlook calendar (after Google)
- [ ] Microsoft Teams links (Vexa supports them; about a day plus a test call)
- [ ] Languages: a default notes language, translation as a separate view (original stays the main version), a language for each share, names and vocabulary kept unchanged (designed in docs/ui). First test a French and English call
- [ ] Mac app for capture without a bot
- [ ] Sharing notes by email (designed in docs/ui)

## Deferred follow-ups

- [ ] Remove the default trusted-proxy fallback (`172.28.0.1`, `trusted_proxy_ips`, hard-coded `next` trust) once `EDGE_SHARED_SECRET` is live on the server (Opus review finding 6; `docs/sop/2026-11-03-edge-secret-and-login-limits.md`)
- [ ] Run `deploy/nginx-limit-check.sh` (and, if cheap, `docs/spike/edge-secret/e2e.sh`) in CI
- [ ] Alert the owner on an account lockout (today a log line only); per-account device cookie with the accounts work
- [ ] Test paid Zoom accounts
- [x] Vexa licence for commercial use: Adebayo confirmed 2026-11-03 that Vexa allows it (his statement; the licence text itself was not re-read here)
- [ ] Choose a recording notice for meetings
- [x] Decide how long to keep audio: 7 days (`audio_retention_days`), stated in the privacy policy (2026-11-02)
- [x] Create a private GitHub repo for this app (not a Vexa fork) so CI runs (2026-11-02: innocent98/beyric-note-taker, PR #1, CI green)
- [ ] Vexa upgrade routine: read release notes, bump the pinned image tag, re-run one Meet and one Zoom call
- [x] Raise the worker heartbeat warning from 5 to 20 minutes (2026-11-02)

## Known gaps recorded 2026-11-03 (found while reconciling the handoff; none block the pilot work, each has an owner)

Code and product
- [ ] Live authenticated pass over the Next screens (meetings, meeting detail, templates, vocabulary): sessions must not enter the owner's production password, so Adebayo signs in once in the Browser pane and a session drives it read-only (stream C "not verified")
- [ ] Real Vexa bot plus real OpenAI rewrite through the new screens; Safari and Firefox; long titles and many rows (stream C and P "not verified")
- [ ] Speaker names appear under two name orders ("Godstime America" / "America Godstime"): merge variants in speaker labels
- [ ] Measure real OpenAI spend per meeting hour (spike and Task 8 both open)
- [ ] Stream D "needs backend, not built" list in `docs/ui/parity-report.md` (sign-up, forgot password, SSO, calendar card, team, tasks, share, download, translation, agenda, template delete, Emails) is the screen build order; also pending: toast component, PWA basics, retiring Jinja and `app/static/tokens.css`
- [ ] Login lockout alert to the owner (log line only today); optionally log the resolved client address on failed logins
- [ ] In-memory login counters reset on restart and are per process: move to the database or Redis with the accounts work

Adebayo (server and accounts)
- [x] `RESEND_API_KEY` added to the server `.env` (2026-11-03, reported by Adebayo)
- [ ] Nightly backup cron (privacy policy promises 14-day deletion, so it must exist); Cloudflare proxy on or off
- [ ] `docker stats --no-stream` and `free -h` during a live meeting, and during two overlapping ones (turns the capacity estimates into numbers; also the `next` container's memory)
- [ ] Mark the 2 GitGuardian findings from #19 as false positives (test placeholders; nobody rewrites history)
- [ ] Google: only branding is verified (Adebayo, 2026-11-03), NOT the sensitive scopes. Switch the Cloud project to "In production" before testers connect a calendar (else tokens lapse after 7 days); fix the final scope list (`calendar.events.readonly`, Meet only if the spike passes); then submit sensitive-scope verification; Search Console TXT for `beyrictech.com`

Legal and business
- [ ] Mailboxes `privacy@`, `legal@`, `security@beyrictech.com` confirmed; hosting country (France per IP lookup; check the Contabo panel); a DPO decision; a lawyer reads privacy and terms
- [ ] Hour-long meeting rated against Fireflies; a French plus English call; rate the Zoom #3 notes; merge Novex-News#10
- [ ] Launch capacity: bot-machine budget, or a per-hour vendor instead
- [ ] Revisit the designer's landing and sign-in copy before the Google verification submission (decided 2026-11-03)
