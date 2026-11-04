# Beyric Note Taker: master checklist

**Status (2026-11-03, evening):** MVP live at notes.beyrictech.com. Merged and deployed today: Next.js owner screens and parity, accounts core and sign-up (API and the account pages), join-refusal handling, live bot state and End now, Phase C foundations (C1, C2, C6, C7, C20), the AI workspace spec (approved, W0 to W10) and the per-screen Stream D list. Open PRs waiting for review and merge: #47 (deploy rollback and `TOKEN_ENC_KEY` notes), #49 (AI extraction W0.2, AI1 eval, W1), #48 (PDF and Word export W5), #46 (draft, instant-Meet spike Part 1). Phase C from C3 on, W0.3 to W0.5, W2 and later, and most Stream D screens are not started. Everything designed in `docs/ui/` is pilot work (Adebayo, 2026-11-03). The next steps and Adebayo's queued items are in `docs/HANDOFF-master.md`.

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

- [ ] **Fail fast when Google refuses the bot's join request** (found live 2026-11-03, meeting 8, an admin of another organisation hosting). The bot clicked "Ask to join" at 10:07:29 UTC and Meet's page immediately faulted (HTTP 403, `DisconnectedError`, `EndCause = 72, StartupCode = 200`); the host never saw a request, the bot polled a dead lobby for about 8 minutes (left 10:15:21) and our meeting stayed `bot_sent` the whole time. Vexa moved to `needs_help` at 10:09:25 (about 2 minutes in), which our worker ignores. Meetings 4 and 5 (hosted from Adebayo's own account) joined with none of these errors; the logs for the cancelled meetings 6 and 7 were not saved, so whether they hit the same fault is unknown. **Update 10:30 UTC, same day: the control test failed, so it is not only the other host's settings.** Meeting 10 (zgr-mdsj-gqx, hosted by Adebayo himself) got the same Google 403 ("The caller does not have permission", `startupCode: 200`) at call startup and then landed on the Meet marketing page ("Could not locate join button"). Meeting 9's saved screenshot shows Google's own "You can't join this video call / No one can join a meeting unless invited or admitted by the host" screen, which the bot did not recognise (it kept polling for a lobby). Meetings 4 and 5 (2026-11-02) used the same URL form and had no 403. Unchanged since then: the Vexa image (`vexaai/vexa-lite:v012`, container up 25 h) and the server; the deploys of 09:48 and 09:50 UTC restarted only web, next and worker. Cause still unproven; candidates: Google now refusing anonymous guests from this server's address or browser fingerprint (five join attempts in 25 minutes may not have helped), or a Google-side change on 2026-11-03. Discriminator done 10:35 UTC: Adebayo opened a fresh meeting of his own in a signed-out Safari private window and could ask to join (only a name was asked), so the host allows anonymous guests. The bot sent to that meeting (11, `ihd-wacc-wxw`) did everything right in its screenshots (name field found, "Ask to join" located and clicked, so Google did NOT rename the button) and then Google answered "You can't join this video call / No one can join a meeting unless invited or admitted by the host"; Vexa went `awaiting_admission` 10:35:43, `needs_help` 10:37:36. The same pre-join page says "System info will be sent to confirm you're not a bot". Reading: Meet's bot detection is refusing this automated browser's knock (the 403 "caller does not have permission"), not a host setting and not a changed label. Vexa's own tracker records the class: #540 (Google killing synthetic input; "block-vs-denial conflation remains"), #316, a hosted anonymous-join failure on 2026-07-30 in #540's comments, #1725 (authenticated Meet bots launch Chromium `--incognito` and ignore the session, open since 2026-11-02), #724 (authenticated bots, a dormant seam as of July). Platform risk for the whole product: anonymous bots can be refused at any time, so auto-join must fail visibly and fast and we need a second way in (see the spike item below).
  - [x] Worker: treat Vexa `needs_help` (and a bot log with `DisconnectedError` right after "Ask to join", if Vexa exposes it) as a failed join after a short grace, with a plain reason on the meeting page, e.g. "Google would not let the bot ask to join. The host's Meet settings may block guests who are not signed in or are outside their organisation." The bot must be stopped so it does not hold a slot for 8 minutes **Done 2026-11-03** (branch `fix/join-refusal-handling`; SOP `docs/sop/2026-11-03-join-refusal-handling.md`): the v0.12.29 typed refusal (reason starts `google_meet_join_refused:`) and the v0.12.28 "Could not locate join button ... workspace.google.com/products/meet" error stop the bot and fail with a plain reason; `needs_help` (a consent or verification prompt, not a refusal) fails after 2 minutes with its own reason. Not verified against a real Google refusal.
  - [x] Refusal pacing (2026-11-03, same branch): at most 2 bots per meeting for a refusal, the second 4 to 5 minutes after the refusal, a manual send no sooner than 5 minutes after it; the wait is shown through `retry_block_message`, with `refresh_after_seconds` while it counts down, and the pause before the second bot through the new `join_retry_at`
  - [ ] Circuit breaker for refusals per egress address (3 in 30 minutes pauses auto-sending, one admin alert): needs calendar auto-join and the admin alert (C11b); TODO left in the SOP
  - [ ] Show the same reason on the Upcoming and "Not recorded" lists once auto-join exists, with a retry
  - [ ] Help text for hosts in other organisations (what to allow in Meet guest access); check Vexa for a bot that joins signed in to a Google account, which would avoid guest rules (investigate, do not promise it)
  - [ ] Keep Vexa's `botlog.txt` and signal tape for failed joins (they were only kept for meetings that ran to the end) so the cause can be read after a cancel
  - [x] Control test: a meeting hosted by Adebayo himself failed the same way (2026-11-03), so the fault is not specific to the other organisation
  - [x] Signed-out private-window test (2026-11-03): a human guest can ask to join; the bot's knock is refused, so the block is on the bot's side (address, fingerprint or Meet bot detection)
  - [x] **Server Vexa upgraded to v0.12.29** (2026-11-03 ~13:00 WAT, Adebayo's go; `VEXA_IMAGE_TAG=v0.12.29` in the server `.env`, image built 2026-10-30, pulled and `vexa` recreated, healthy; `vexa-schema-fix` said language is already varchar(32); worker reaches the Vexa gateway; no meeting was active). Not yet proven: that a join now succeeds. Rollback: set `VEXA_IMAGE_TAG=v012`, `docker compose up -d vexa`, then `docker compose up -d vexa-schema-fix`
  - [x] One bot sent from the app to a meeting Adebayo hosted, on the upgraded server (2026-11-03 ~13:57 WAT, meeting 12): Vexa went `joining` -> `awaiting_admission` -> `active`, so the bot got in. One sample only; the Mac run was 3 of 3 on v0.12.29
  - [x] **First real capacity numbers** (2026-11-03 14:00 WAT, `docker stats --no-stream` and `free -h` over ssh, one bot active in meeting 12, server `haven-respite`, 4 vCPU, 7.8 GiB shared with other projects): `vexa` container 1.95 GiB of its 3 GiB cap and 138% CPU (about 1.4 cores for one bot plus Vexa itself), `next` 58 MiB, `web` 92 MiB, `worker` 74 MiB, `db` 79 MiB, `vexa-db` 50 MiB; host load average 4.1 on 4 cores, 3.2 GiB available. Reading: a second concurrent bot would need about 1.4 more cores (the box is already at its CPU limit with one) and about 1.4 GiB more memory (over the 3 GiB cap). Replaces the old estimate "CPU per bot unmeasured"; for Phase D / P0.8, not a full measurement (one sample, bot idle in a meeting, no transcription burst)
  - [ ] **Spike: a reliable way into Meet** (decide before calendar auto-join relies on it): (a) Vexa upgrade (we run `vexaai/vexa-lite:v012`; latest release v0.12.28, 2026-10-09; read the join-related notes), (b) authenticated bot: a dedicated Google account the bot signs in with (Vexa #724 and #1725; privacy policy and bot name implications), (c) the bot's account invited to the event, which skips the knock (the join page itself offers "sign in with the Google account your host invited"), (d) a different outbound address (Mac on a home connection as the discriminator; a proxy later), (e) pacing: no more than a couple of attempts per few minutes
    - E2 run 2026-11-03 (Mac, home connection; `docs/spike/meet-join-experiment.md`): v0.12.28 reached the lobby 0 of 5 (refused before or at the click), v0.12.29 3 of 3 (stopped early; one rescued by the re-knock). Upgrade the server to the pinned `v0.12.29`; do not change the address yet, re-measure on the server after the upgrade
  - [ ] Recognise Google's "You can't join this video call" screen in the worker's log or screenshot reading, and fail at once with that reason instead of polling a dead page (Partly done 2026-11-03: Vexa v0.12.29 recognises the screen and reports `google_meet_join_refused`, and the worker maps that and `needs_help` to a fast failure; v0.12.28 reads as `needs_help`. Left open until production runs v0.12.29 and a real refusal is seen.)
  - [x] Show what the bot is doing during the call (2026-11-03, branch `feature/bot-live-state`; SOP `docs/sop/2026-11-03-bot-live-state.md`): the worker records Vexa's `joining`, `awaiting_admission`, `active` and `needs_help` while the meeting is `bot_sent`, and `status_text` says it on the Jinja and Next page and list ("Recording" only when segments or a recording id exist). No new column or API field. Not verified against a live Vexa call.

- [ ] **The bot stays in the call about 10 minutes after everyone else leaves** (found live 2026-11-03, meeting 12): the bot was admitted at 12:58:38 UTC, the last remote audio was at 13:05:49, the bot left itself at 13:15:51 with reason `left_alone` from Vexa's silence rule (default window 600,000 ms, `BOT_ALONE_SILENCE_WINDOW_MS`; log line "aloneness: silence verdict"). Notes only start once the bot has left, so the owner waits those 10 minutes. The Vexa bot request accepts `automatic_leave` with `max_time_left_alone` (read in `meeting_api/bot_spawn/router.py`, positive integer, milliseconds as the bot's other timeouts)
  - [x] Send `automatic_leave` from `app/vexa.py` with a configurable default (`BOT_LEFT_ALONE_SECONDS`, default 300, 0 = send nothing; the rule is silence, not headcount, so too short a window can cut a quiet meeting such as a long screen-share: measure) (2026-11-03, branch `feature/bot-end-controls`; SOP `docs/sop/2026-11-03-bot-end-controls.md`; field name, unit and 422 rule read in Vexa's `router.py`; **not verified against a real bot request**)
  - [x] An **"End now and write the notes"** button (2026-11-03, `feature/bot-end-controls`): `POST /api/meetings/{id}/end`, Jinja and Next buttons, marker `end_requested`, "nothing recorded" ends as the usual `no_audio` failure; Vexa's `DELETE` on an `active` bot ends `completed` / `stopped` (source read). **Not verified on a real call: how long the bot takes to leave and whether the recording is ready then**
  - [x] Follow-ups from the review of #40 (same branch): the live state carries `bot_state_seen_at` (migration 0009) and reads "Checking on the bot..." when old, a 2-miss hysteresis, words for `requested`, `stopping` and a call that ended, list polling, `worker_stale` on the meeting, `needs_help` with segments, live-region badge, long state as a paragraph
  - [ ] Watch one real call end to end: press End and time it to `ready`, check Vexa takes `automatic_leave`, measure a few silence windows; update the SOP
  - [ ] Cost and capacity note recorded: `docs/spike/cost-and-capacity-2026-11-03.md` (OpenAI USD 1.54 since 11-01, Contabo about USD 70 a year, one bot about 1.4 cores; self-host the pilot, own bot machine soon, vendor as fallback)
  - [ ] OpenAI "group by line item" export to split audio from chat cost; re-run the cost note after a month of real use

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
  - [ ] Not built, waiting on the API (stream D): Download menu (shipped with W5, 2026-11-03), Share dialog, translation, template delete, participants and duration
  - [ ] Not verified: real Vexa bot and real OpenAI rewrite through the screens, Safari and Firefox, the live server
- [ ] Stream P, UI parity with the designer's files (`feature/nextjs-ui-parity`, PR #26, merged and deployed 2026-11-03; SOP `docs/sop/2026-11-03-ui-parity.md`; report `docs/ui/parity-report.md`)
  - [x] Gap check of every designer file against the Next pages at 1440, 768 and 390 px; report with before and after screenshots
  - [x] Brand tokens from `Brand.dc.html` (light only: the dark overrides are gone), new "b" logo, favicon, PWA and Apple icons, share image
  - [x] App shell, Meetings, meeting page (notes split into decisions, questions, "Please check"), Templates, Vocabulary at parity
  - [x] Sign-in screen (form plus teal panel), landing page, privacy and terms frames with "On this page", 404 and error pages
  - [x] Guard tests kept; new tests for notes sections, legal contents list, logo, landing links, Show / Hide
  - [x] CI green (PR merged and deployed 2026-11-03)
  - [x] Decided 2026-11-03 (Adebayo): keep the designer's landing and sign-in words even though some run ahead of the product (Teams, tasks, integrations, sharing); revisit before the Google verification submission
  - [x] Run `owner-e2e.mjs` and `owner-browser-check.mjs` on the compose stack (2026-11-03: 41/41 and all pass, also with `--poll-check` and `--send-check`; SOP `2026-11-03-ui-parity.md`, "Live check"). Live public pages read in a browser; favicon link fixed. Still open: a live authenticated pass by Adebayo, and the designed 404 for unknown URLs (FastAPI JSON shows)
  - [ ] Needs backend, not built (listed in the report): sign-up, forgot password, SSO, accounts and sidebar identity, calendar and auto-join card, series, tasks, projects, team, settings, download, share, translation, agenda, template delete, Emails
- [ ] Stream D, new designed screens, one line per screen. **Decided 2026-11-03 (Adebayo): every designed screen is pilot work, and the AI workspace spec is approved ("all recommended" for D1 to D19).** The AI workspace screens follow `docs/superpowers/specs/2026-11-03-ai-workspace-design.md` (phases W0 to W10; "Needs" names the backend phase that must merge first; "Designer gap" is a control the prototype does not draw). Each screen is built after its API merges, against live captures in the FE guide, and ticked only when verified in a real browser.
  - [ ] D1, accounts and pilot (needs Module 2 phase A, shipped on the backend): sign up, confirm your email (`/verify-email`), forgot password, check your email, new password (`/reset-password`), waitlist form and "pilot full" (`/pilot-full`, designer gap), accept invite (`/signup?invite=`), Google sign-in buttons and the `/login?error=` messages, sidebar identity (name and email from `GET /api/session`), usage bar and "hours used up" (designer gap), notification bell (designer gap), admin screens: users, waitlist, pilot settings (designer gap)
    - Shipped on the frontend so far (from main, PR #44); the rest of D1 is still open:
      - [ ] Accounts and pilot screens (branch `feature/stream-d-auth-pages`, 2026-11-03; SOP `2026-11-03-accounts-auth-pages.md`; against `web/scripts/mock-api.mjs` replaying the captures, not against real FastAPI or a real mail)
        - [x] `/signup`: the form (invitation token read in the browser and removed from the address, name, email, password with Show / Hide, Terms checkbox, Google when `google_signin`), the "check your email" screen with a 60 s resend, the `closed` and `full` screens with the waiting list, `?invite=` (form even while closed or full), API refusals switch the screen
        - [x] `/verify-email?token=`: password form (a GET never confirms), "Email confirmed", dead-link screen with "Send a new link", `pilot_full` screen that keeps the link
        - [x] `/forgot-password` and its "check your email" state (60 s resend)
        - [x] `/reset-password?token=`: new password and confirm, "Password updated", dead-link screen
        - [x] `/pilot-full`: waiting list form pre-filled from `prefill_email`
        - [x] `/login`: "Forgot password?", Google button only when `GET /api/pilot` says `google_signin`, "Create account" in the header, `?error=<code>` messages, the "confirm your email" line under a wrong-password answer
        - [x] Client: `pilot`, `signup`, `resendVerification`, `verifyEmail`, `forgotPassword`, `resetPassword`, `joinWaitlist`; nginx's non-JSON 429 becomes `too_many_requests`
        - [x] Tests: 85 new (`accounts-api`, `accounts-pages`, `accounts-server`, `accounts-guards`); mock API extended from the captures
        - [ ] Designed and not built (no backend): workspace setup, team invite acceptance, Microsoft sign-in, "Keep me signed in", the 6-digit email code (the API confirms with a link and the password)
        - [ ] Not checked: CI on the pushed head, a real mail, a real Google round trip, Safari and Firefox, the live site. Sign-up stays closed (`signups_open` is false)
  - [ ] D2, calendar and auto-join (needs Module 2 phase C): Meetings page calendar card and Upcoming list with per-meeting switches, "Not recorded" rows with Send now (designer gap), "Calendar disconnected" banner and Reconnect (designer gap), Settings, Calendar (rules, template), the instant-Meet switch (designer gap)
  - [x] D3, Decisions (API shipped in W1; see `docs/fe-integration-guide-ai-workspace.md`): search, groups by meeting, Open notes, Remove from log (designer gap). Built 2026-11-03 on branch `feature/stream-d-decisions-review`, SOP `2026-11-03-stream-d-decisions-review.md`; verified in a real browser against a real local FastAPI and Postgres (search by code and by words, empty search, Remove from log with its ask, phone width), not against the live site
    - [x] `/decisions`: groups by meeting with the day, title and Open notes; search through the API (300 ms pause, stale answers dropped); "Show older meetings"; "No decisions yet."; "No decisions match your search."; Searching..., search failed with Try again; Remove from log asks first and treats a 404 as removed
    - [x] Tests: `decisions-view.test.tsx` (15, against the captures)
  - [x] D4, Review (API shipped in W1; see `docs/fe-integration-guide-ai-workspace.md`): open and checked lists, Add to vocabulary, Open transcript, Mark as checked, Undo, sidebar badge, "Rewrite notes with the corrected spelling" offer. Built 2026-11-03 (same branch and SOP); verified in a real browser against a real local FastAPI and Postgres, **except** a successful rewrite (needs OpenAI) and luna's own `heard_as` (a stand-in reply was used)
    - [x] `/review`: open items with meeting and day, the spelling box only when `heard_as` is set, Add to vocabulary (201, 400 and 409 handled), Open transcript (`/meetings/<id>?tab=transcript`), Mark as checked, collapsed Checked list with Undo, "All caught up" and "Nothing to review yet.", 200-item notice, item gone (404) notice
    - [x] Sidebar: Decisions and Review links, the Review count from `badges.review_open`, refreshed after every change
    - [x] Rewrite offer after an add or a duplicate: reads the meeting's template, calls `regenerate`, warns that removed and checked items may come back
    - [x] Meeting page: "Decisions and review" card from `extraction_status` (pending and running with 5 s polling for 2 minutes, stalled, done, failed, skipped), the line near Rewrite notes
    - [x] Tests: `review-view.test.tsx` (20), `meeting-extraction.test.tsx` (11), `decisions-review-shell.test.tsx` (7); `npm run gen:api` gave no difference
    - [ ] Not verified: a successful rewrite from the offer, the real worker filling the lists, Safari and Firefox, the live site, 200 items
  - [ ] D5, Tasks (needs W2; owners need W3; projects need W4; sprints need W9): filter chips, project tabs, Board and Backlog, drag and drop, task drawer, "needs attention" banner, New task, sprint header and Close sprint, milestone card, meeting page Action items as tasks; states for extraction pending and failed (designer gap)
  - [ ] D6, Projects (needs W4): list, name, colour, recurring meetings, keywords, sprint length, milestone (proposal chip is a designer gap), delete
  - [ ] D7, Team (needs W3; W0.4 first): members, roles, aliases (editing is a designer gap), invite dialog (name field is a designer gap), Resend, remove, "Share with team" switch on the meeting page (designer gap)
  - [ ] D8, Series (needs W8, and Module 2 phase C for recurring ids): cards on Meetings, series page (unfinished, finished since, carried questions, agenda, past meetings, attendees), "On next agenda" toggles, Send agenda, Details row on the meeting page, "Add to recurring meeting" and "Possibly resolved" chips (designer gaps)
  - [x] D9, Download (W5, 2026-11-03): menu with PDF and Word, transcript tick, the states for preparing, done, failed and not ready; **the language note waits for W7** (translated downloads)
  - [ ] D10, Share (needs W6, and W3 for participants): dialog, recipients, language, attachment, transcript, message, "Shared with ..." note, "Add email" on a participant (designer gap), share-limit message (designer gap)
  - [ ] D11, Translate (needs W7): language select, "Translating ..." and "Translated from ..." banners, Show original, default language in Settings, failed state (designer gap)
  - [ ] D12, Settings (needs W2, W7, W8, W9, W10): Tasks card, default language, sprints and reminders card, Slack and tracker cards
  - [ ] D13, meeting page details (needs W0.4 and W8): duration (`recorded_seconds`), participants (names from speaker labels), series, milestone
  - [ ] D14, Emails in the designer's layout (W0.3 done 2026-11-04 for verify, reset, already registered, invite and Notes ready; open: Notes shared (W6), New tasks (W2), Team invite (W3), reminders (W8), Could not join (C14))
  - [ ] D15, template delete (needs `DELETE /api/templates/{id}`, not yet built, and a `template_in_use` refusal for a template still used by a meeting or the calendar setting; both are planned in the accounts spec, neither exists in code yet)
  - [ ] D16, shell leftovers: toast component, PWA basics
- [x] W5, Download and export (AI workspace spec section 9; branch `feature/notes-export-w5`, 2026-11-03; SOP `docs/sop/2026-11-03-notes-export-w5.md`; FE guide `docs/fe-integration-guide-export.md` with 18 live captures; PR #48 open, not merged)
  - [x] Spike EX1: WeasyPrint fits the image (Pango already comes with ffmpeg; one 103 MB pip layer) and the memory (renderer peak 91 MiB for a 1-hour transcript, 120 MiB for 40,000 words; 1.9 s and 9.0 s on a loaded arm64 VM): `docs/spike/export-pdf-memory.md`
  - [x] `NotesDocument` render model from the stored notes and, when asked, the transcript (`app/notes_document.py`)
  - [x] PDF (WeasyPrint, Geist, A4, footer, page numbers) and Word (python-docx: headings, nested bullets, transcript after a page break)
  - [x] `GET /api/meetings/{id}/export?format=pdf|docx&transcript=true|false`: owner-scoped (404 for another account's meeting), 409 `not_ready`, never stores the file, child process per export, one at a time (503 `export_busy`), 45 s limit (504 `export_timeout`), cleaned file name from the title with RFC 5987 `Content-Disposition`
  - [x] Route named in the route-enumeration test; `docs/openapi.json` and `web/lib/api/schema.d.ts` regenerated
  - [x] Download menu in the Next meeting page (parity with the prototype) and its states; privacy row "files you export are built when you ask and are not stored" in the Jinja templates and the Next copies
  - [x] SOP, FE guide with live captures, `scripts/capture_export_api.py`
  - [ ] Not verified: a file opened in Microsoft Word, Google Docs or Pages; the browser writing the file to disk; Safari and Firefox; the production CSP and the live site; timings on the real server
  - [x] `mem_limit: 768m` on the `web` service (it had none)
  - [ ] Suggested, not done: a per-account export limit
- [ ] Retire Jinja and the Module 2a CSS once every page is migrated
- [ ] Measure Next.js memory on the server (Adebayo runs the SSH steps)

## Module 2: Pilot, accounts plus calendar auto-join (designed and planned 2026-11-03; build in progress)

Agreed with Adebayo: open sign-up capped at 10 accounts; 5 recorded hours per account for the whole pilot (admin uncapped); the frontend moves to Next.js (decided 2026-11-02 evening, superseding the Jinja decision; see Module 2b); company is Beyrichq LTD, Lagos, Nigeria. Design: `docs/ui/Beyric Note Taker redesign/` (Calendar card, Upcoming list, Settings → Calendar, legal pages).

- [ ] Public pages for Google verification
  - [x] Landing, Privacy and Terms built and tested, text true to the app (`feature/public-pages`, 2026-11-02; see SOP)
  - [x] Merged and live (PR #10, 2026-11-02): `/`, `/privacy`, `/terms` return 200
  - [ ] Checked in a browser, light and dark
  - [ ] Adebayo: Google Cloud project, OAuth consent screen, verify beyrictech.com in Search Console, mailboxes privacy@/legal@/security@
  - [ ] Submit for verification (needs the working calendar flow and a demo video)
- [x] Frontend approach decided: stay on Jinja, with parity and flexibility as conditions (`docs/superpowers/specs/2026-11-02-frontend-approach.md`, 2026-11-02)
- [x] Written design (`docs/superpowers/specs/2026-11-03-pilot-accounts-calendar-design.md`): drafted, builder-checked and **answered by Adebayo 2026-11-03** (section 0a overrides the older question table)
- [x] Implementation plan: `docs/superpowers/plans/2026-11-03-pilot-accounts-calendar.md` (2026-11-03; open as `docs/pilot-plan`). Per-task files, tests, reviewer, dependencies and the single "Needs Adebayo" table are there; the lists below mirror its task IDs
- [x] **Operator scripts in the image (2026-11-04, `fix/docker-copy-scripts`):** the Dockerfile copies `scripts/`, so `docker compose exec web python scripts/...` works (the extraction backfill had failed on the server for want of it; run with `python -c` instead); test `tests/test_dockerfile_scripts.py`; SOP `docs/sop/2026-11-04-docker-image-scripts.md`. Check on the server after the first deploy: `docker compose exec -T web ls scripts`
- [x] **Deploy gate (2026-11-03, after the #45 outage; `fix/deploy-rollback-and-token-key`):** every PR that adds a required environment variable lists it under "Server steps before merge" in its body and the step is done on the server before merging (the app refuses to start without it, by design); `deploy/deploy.sh` now rolls back when `docker compose up` itself fails, restores the checkout on a failed build and starts containers stuck in `Created` (`tests/test_deploy_script.py`); `TOKEN_ENC_KEY` is documented in `deploy/README.md`, `.env.example` and the Phase C foundations SOP. Not yet exercised on the real server
- [ ] Phase 0, prerequisites and spikes (parallel, no app code)
  - [ ] P0.1 Google console state: **branding verified only; the sensitive scopes are NOT verified** (Adebayo's correction of spec 0a Q7). Record Audience, Data Access and Verification Center; declare the scopes (`docs/spike/pilot-google-console.md`)
  - [ ] P0.2 Instant-Meet spike S1 to S7 and S9 on a personal Gmail first (gate outcome G1 to G4 decides Phase I) **PARKED 2026-11-03 (Adebayo could not set up Google Cloud billing; draft PR #46 stays open; reconsider when billing exists)**
  - [ ] P0.3 Vexa checks: chat posting, per-key limit, bot listing, health endpoint
  - [ ] P0.4 Resend state: domain, SPF/DKIM/DMARC, a delivered test mail, `ADMIN_ALERT_EMAIL`
  - [ ] P0.5 Bots per machine (M1): `docker stats` during live meetings (Adebayo)
  - [ ] P0.6 Second bot machine rented, private link up (Adebayo)
  - [ ] P0.7 Teams spike with a real Teams meeting (Q17)
  - [ ] P0.8 Join-time measurement fixes `AUTOJOIN_LEAD_SECONDS` (default 60)
  - [ ] P0.9 Cloud project already In production (Adebayo, 2026-11-03); remaining: Adebayo sets `GOOGLE_OAUTH_TESTING=false`; session records the unverified-app screen and the day-8 token check
  - [ ] P0.10 "Start an instant meeting" test, separate from P0.2 (does `spaces.create` work for a personal Gmail, and does the bot get in: knock, admit, or an `OPEN` space without knocking; decides whether I6 builds the button and whether `meetings.space.created` joins the Phase V scope list). Runbook, three scripts and result template: `docs/spike/start-instant-meeting.md`, SOP `docs/sop/2026-11-03-start-instant-meeting-test.md`
    - [x] Scripts and runbook written; parsing, error text, secret redaction, the localhost-only Vexa guard and the status-timeline reader unit-tested offline (45 tests, `tests/test_spike_start_instant_meeting.py`); `send_bot.py` run against a fake local Vexa (2026-11-03)
    - [ ] Throwaway Google project, Meet API enabled (record whether billing is demanded), consent screen with the Gmail as test user, Desktop OAuth client (Adebayo; a session can click it in his Chrome while he signs in and clicks Allow)
    - [ ] `spaces.create` result for personal Gmail (and Workspace if he has one)
    - [ ] Bot into a default-access space: got in? had to knock? did he have to admit?
    - [ ] `config.accessType` read for default and `OPEN`; did the bot get in to `OPEN` without knocking?
    - [ ] Result and decision line filled in `docs/spike/start-instant-meeting.md`
- [ ] Phase A, accounts: **in progress, owned by other sessions** (`feature/pilot-accounts-core`, then `feature/pilot-accounts-signup`; tasks A1 to A20 in spec section 15, not re-planned): sign-up (email+password, Google sign-in), verification email, password reset, per-user data, 10-account cap with waitlist, hours cap, admin API
  - [x] Phase A core (PR `feature/pilot-accounts-core`, 2026-11-03; SOP `docs/sop/2026-11-03-pilot-accounts-core.md`; open, not merged): A1 migration 0006 (users, tokens, oauth flows, waitlist, notifications, mail outbox, settings, admin from `OWNER_EMAIL`), A2 argon2id and password rules, A4 session cookie `{uid, csrf, iat, epoch, gepoch}` and guard, A5 email login with per-email limiter, A6 migration 0007 (ownership, usage ledger), A7/A8 meetings, templates, vocabulary scoped by user (foreign id 404), A9 hours formula, gate and `usage` in `/api/session`
  - [ ] **Deploy step for Adebayo:** add `OWNER_EMAIL` to the server `.env` before merging (the owner then signs in with email plus the same password)
  - [x] Phase A sign-up (branch `feature/pilot-accounts-signup`, 2026-11-03; SOP `docs/sop/2026-11-03-pilot-accounts-signup.md`; FE guide `docs/fe-integration-guide-pilot-accounts.md` with 119 live captures; open, not merged; **sign-up stays closed**, `signups_open` false):
    - [x] A3 `Mailer` (Resend, console refused in production), outbox sender with retries at 0, 1, 6 and 36 minutes and scrubbing
    - [x] A10 sign-up with the advisory lock (ten parallel at nine accounts make one), `pilot_full`/`pilot_closed`, `GET /api/pilot`, terms version recorded, 202 for every outcome
    - [x] A11 email verification (each link confirms only its own submission's password, typed again; 5 wrong tries kill it), resend limits, 48 h sweep, `password_signup_enabled` switch
    - [x] A12 password reset (30 minutes, bumps the user's epoch, no enumeration)
    - [x] A13/A14 Google sign-in (PKCE, ID-token checks, matching order, unverified password account loses its password, `pilot_full` cookie), off unless `GOOGLE_CLIENT_ID` and `GOOGLE_CLIENT_SECRET` are set
    - [x] A15 waitlist and invitations (reserve a place 7 days; redeemed by password sign-up or by the Google address)
    - [x] A16/A17 admin API (users, pilot settings, waitlist, reset link, mail failures; last-admin guard under a lock) and `scripts/delete_user.py` with the worker's purge of deleted accounts
    - [x] A18 notifications API and `unread_notifications` on the session
    - [x] A19 nginx zones for the account routes and the waitlist (proved with a throwaway nginx), deploy notes (`deploy/README.md` section 10)
    - [x] A20 privacy and terms rows for accounts (Jinja pages and the Next copies), SOP, checklist, FE guide
    - [x] From the accounts-core review: `bot_already_active` is per user and a neutral `bot_busy` covers a full note taker (`MAX_ACTIVE_BOTS`, default 1); usage committed before transcription; Jinja IDOR tests
    - [x] Changes after the Opus review of the sign-up PR (2026-11-03; SOP table): deletion finds Vexa's recording by meeting id and cannot stall the worker, row lock between sign-up and confirmation, sign-in answers an unconfirmed account like a wrong password (no `email_not_verified`), unconfirmed accounts hold no place (confirmation re-checks room, cap of 100 unconfirmed, sweep from the last submission), no token or mail row without a mail provider and unsent mail scrubbed after an hour, Resend and administrator access in the privacy policy
    - [x] Changes after the second Opus review of the sign-up PR (2026-11-03; SOP table): the pipeline leaves a deleted owner's meetings alone, the purge defines "settled" for Vexa and gives up after 24 h with an admin alert (`users.deleted_at`), an invitation helps someone who already has an unconfirmed account, lock order in confirmation fixed, hourly jobs isolated, `delete_user.py` takes the admin lock, Resend's mails listed in the privacy policy
    - [ ] **Before sign-up is switched on for anyone:** a sign-up flood can become an outage (about 20 addresses x 5 an hour keep the 100-unconfirmed cap full and everyone gets 429; the at-cap clearing can evict a real person's pending account after an hour; no global cap on outgoing mail): add a global mail budget and a better flood defence
    - [ ] **Before sign-up opens:** the frontend pages the mail links open (`/verify-email`, `/reset-password`, `/signup?invite=`, `/pilot-full`, `/login?error=`) now exist on `feature/stream-d-auth-pages` (merge and deploy them first); Adebayo confirms a real verification mail is delivered, then turns `password_signup_enabled` on; a real Google round trip with a test account
- [ ] Phase C, Google Calendar auto-join plus the ICS source (needs A6, A9, A13, A18 for most tasks)
  - [x] C1 migration for connections, events, scheduled joins (2026-11-03, `feature/phase-c-foundations`; migration `0010`, SOP `docs/sop/2026-11-03-phase-c-foundations.md`; open, not merged)
  - [x] C2 token vault (AES-GCM, Opus review) (2026-11-03, same PR #45; Opus reviewed, no Critical or High, its fixes applied; the privacy "Security" row waits for C3)
  - [ ] C3 connect flow with incremental consent (Opus review) (2026-11-03, PR `feature/c3-google-connect`, open, not merged; SOP `docs/sop/2026-11-03-phase-c-google-connect.md`, FE guide `docs/fe-integration-guide-calendar-connect.md`). Built and tested, routes dark until `CALENDAR_ENABLED=true`: not ticked until the **live step** (a real Google consent with Adebayo's Gmail test account) is done
    - [x] connect URL (offline, consent prompt, PKCE S256, login hint, granted scopes carried), single-use state bound to user and browser, callback classified public-but-state-checked, granted scopes from Google, refresh token only through the vault with the user id as owner, reconnect replaces, `return_to` allow-list, redirect-URI allow-list, no secrets in logs (71 tests, mutation-checked; a different Google account is refused while a connection works; captured against the running app with Google faked)
    - [x] privacy "Security" row (Jinja and Next copies)
    - [ ] live check with a real Google account: consent screen, stored row (token is bytes), real scope names and `hd`, unticked box
  - [ ] C4 disconnect flow
  - [ ] C5 calendar client and `CalendarSource` protocol; live check that `calendar.events.readonly` covers `events.list` and `events.get`
    - [x] Protocol, `SyncBatch`, errors and `GoogleSource` / `AccessTokenCache` (2026-11-03, `feature/c5-calendar-source`; SOP `docs/sop/2026-11-03-phase-c-calendar-source.md`; 101 tests against synthetic Google bodies; open, not merged)
    - [ ] **Live step, needs C3 and Adebayo:** one real `events.list` and one `events.get` with only `calendar.events.readonly`, redacted captures in `docs/spike/pilot-calendar-captures/`, fixtures and fake switched to them. If `events.get` refuses the scope, stop: the re-check would need `calendar.readonly`
  - [x] C6 event parsing (2026-11-03, same PR; synthetic fixtures until C5's live captures exist)
  - [x] C7 `will_join` rule function (2026-11-03, same PR; full rule x organiser x response x platform x override matrix; an unanswered invite from outside your organisation does not auto-join under `all`: **decision for Adebayo**, see the SOP)
  - [x] C8 materialiser (events to rows) and C8b sync job (2026-11-04, `feature/c8-calendar-materialiser-sync`; `app/gcal_sync.py`, migration `0012` adds `calendar_connections.last_full_sync_at`; SOP `docs/sop/2026-11-04-phase-c-materialiser-sync.md`; 78 new tests on a real Postgres, mutation-checked). The sync job is registered on the scheduler only while `CALENDAR_ENABLED` is true (false in production): no bot is sent, a scheduled join is only a row until C11 and C12. Not verified against a real Google calendar (needs the C5 live step). Open, not merged
    - [x] `apply_batch`: idempotent replay, moves before dispatch only, cancel and switch-off, revive on the same row, late join inside the deadline, no cancellations on an incomplete batch, complete full listing cancels missing events inside the window, per-user scoping, nothing stored beyond the plan, hostile input skipped
    - [x] `sync_connection`: first sync full, then incremental; 410 falls back to full; token written only after a complete batch committed; full resync every 6 h; errors recorded in `last_error`, never raised
    - [ ] Follow-ups: C13 acts on a recorded `AuthRevoked` / 30 minutes of errors; C12 owns rows past their deadline; C15a re-runs `_reconcile` on a rule or template change
  - [ ] C9 key re-encryption script
  - [x] C10 scheduler skeleton (2026-11-03, `feature/c10-scheduler`; leader advisory lock on its own connection, dispatch and sync loops, injected clock, heartbeat, compose service, `deploy.sh` heartbeat wait; no migration, no required setting; SOP `docs/sop/2026-11-03-phase-c-scheduler.md`). Verified on a real Postgres and with two real processes (failover, clean stop). Not yet run on the server; four `deploy.sh` stub tests added to `tests/test_deploy_script.py`
  - [ ] C10b periodic sweeps and mail tick
  - [ ] C11 `place_bot()` single host (Opus review); C11b alerts helper (admin email via Resend)
  - [ ] C12 join runner (dispatch at start minus `AUTOJOIN_LEAD_SECONDS`, no-capacity retry, catch-up)
  - [ ] C13 connection states and refresh failures; C14 disconnected notifications and email
  - [ ] C15a calendar API part 1 (state, settings, upcoming, override); C15b send now and not recorded
  - [ ] C16 deploy gate (Q19)
  - [ ] C17 close-out: policy rows, SOP, FE guide with live captures, phase Opus review
  - [ ] C18 recording notice (chat line if P0.3 allows)
  - [ ] Paste-a-link (ICS) calendar source for Outlook, Microsoft 365 and others (**in**, Adebayo 2026-11-03; no Microsoft OAuth app): C19 staleness spike (Google secret feed and Outlook publish), C20 SSRF-safe fetcher (Opus review), C21 parser with our own recurrence expansion, C22 connection, encrypted masked link and first-sync result, C23 source and API with the "updates can take a while" label, C24 close-out. No Outlook wording anywhere until C24 is live and verified (list in the plan)
    - [x] C20 SSRF-safe fetcher (2026-11-03, PR #45; Opus reviewed, no Critical or High, its fixes applied (hard time limit, `webcal://`, URL cap); the three default hosts in `ICS_ALLOWED_HOSTS` are unconfirmed until C19; TLS by pinned IP and SNI checked against the three real hosts)
- [ ] Phase C-T, Teams (Q17, gated by P0.7): T1 pasted Teams link, T2 Teams events from the calendar, T3 Teams wording in policy, terms, landing
- [ ] Phase I, instant-Meet auto-join (gated by P0.2; the Settings switch is drawn by the designer)
  - [ ] I1 incremental Meet consent, mode resolution, switch; I2 `pubsub_inbox` and the push endpoint (Opus review); I3 Meet client and subscription manager
  - [ ] I4 inbox processor and ownership rule; I5 `poll` mode (only if the spike says so); I6 manual routes and PWA share-target note; I7 close-out
- [ ] Phase D, several bots at once (do not invite more than one or two testers before D9b)
  - [ ] D1 `bot_hosts` migration; D2 `vexa_for(host)`; D3 health checker; D4 `place_bot()` generalised (Opus review); D5 per-user limit across hosts
  - [ ] D6 forecast and capacity endpoint; D7 admin host endpoints; D8 second host files; D9a worker concurrency; D9b load test (M2) and `max_bots`; D10 close-out
- [ ] Phase V, Google verification: **a real sensitive-scope submission** (review up to 10 days; scope changes after approval need a new review). V0 revisit the landing and sign-in copy (decision from 2026-11-03 ends here), V0a freeze the final scope list (Meet only if the instant-Meet spike passes); `meetings.space.created` only if the P0.10 start-an-instant-meeting test says build the button (`docs/spike/start-instant-meeting.md`), V1 consent screen versus policy and app, V2 demo video (Adebayo records), V3 scope justifications, V4 submit as early as the flow works and answer the reviewer; the pilot runs meanwhile on the unverified-app screen (100-user cap)
- [ ] Meet join plan B (plan: `docs/superpowers/plans/2026-11-03-meet-signed-in-bot-and-vendor.md`, PR `docs/meet-join-plan-b`; research done 2026-11-03, nothing built). Order and triggers are in its section 4; start B1 regardless, the rest by the Mac experiment and v0.12.29 results
  - [ ] B1 `BotProvider` seam behind `VexaClient`, a fake provider and contract tests, no behaviour change (also the Phase D seam)
  - [ ] B2 `RecallProvider` behind a flag, one live bot on Adebayo's own Meet (needs his Recall account)
  - [ ] B3 fallback placement, spend guard and policy text (needs Adebayo's yes on Recall as a sub-processor)
  - [ ] A1 measure the full Vexa stack with a bot built from PR #1873 on the Mac, and settle two questions (Zoom on an authenticated deployment, a signed-in bot on an invited event)
  - [ ] A2 to A7, only if A1 passes: signed-in host files, `auth_session_missing` handling and alert, provisioning runbook, keep-warm job, invite path (`bot_invited`, Upcoming row, Settings address), policy and terms rows
- [ ] Clear "calendar disconnected" state (built in C13 and C14; the 7-day expiry path stays dormant while the Google app is verified)

## PWA share target (Android share sheet; built 2026-11-03, branch `feature/pwa-share-target`; SOP `docs/sop/2026-11-03-pwa-share-target.md`)
- [x] Link extraction from shared text (Meet, Zoom with passcode, bare Meet code, two links, hostile text), Vitest
- [x] `share_target` in the manifest (POST to `/share-target`), route handler, `/share` page with the login round trip and every refusal, Vitest
- [x] Service worker (network-only, caches nothing), checked against Chrome's documentation; manifest parses and no installability errors in headless Chrome on a Mac
- [x] FE guide section 5.9 with captures, SOP
- [ ] CI green on the PR; merge only on Adebayo's word
- [ ] Phone test by Adebayo: install from Chrome's menu, share a Meet link from the Meet app, check a meeting appears (the real share sheet is not verified)
- [ ] Decide whether a share may send without the tap (needs a way to tell a real share from a forged post)

## AI workspace backend (spec `docs/superpowers/specs/2026-11-03-ai-workspace-design.md`, phases W0 to W10; approved 2026-11-03, D1 to D19 as recommended)

Each phase ends with its policy rows, SOP, checklist and FE guide with live captures. Opus reviews the phases marked in spec section 15.

- [ ] W0.1 Spikes
  - [x] AI1 extraction eval: **go on `gpt-6-luna`** (2026-11-03: 7 synthetic cases, 30 bullets, 0 invented owners, 30/30 quotes, 17/17 dates; `docs/spike/ai-extraction-eval.md`). Five real transcripts reviewed by Adebayo are still owed before W2
  - [ ] EX1 PDF memory and time (W5)
  - [ ] ML1 Resend attachments and limits (W6)
  - [ ] TR1 French and Spanish with names (W7)
- [x] W0.2 AI ledger and settings: `ai_runs`, `EXTRACT_MODEL`, prompt versions (2026-11-03, SOP `2026-11-03-ai-workspace-w1-extraction.md`)
- [x] W0.3 Mail upgrade (branch `feature/w03-mail-upgrade`, 2026-11-04; migration `0013`; SOP `2026-11-04-w03-mail-upgrade.md`; spike `docs/spike/resend-limits-2026-11-04.md`; PR open, not merged; no mail was sent live)
  - [x] Designer layout for every mail, escaped blocks, http(s)-only buttons
  - [x] `Message`: attachments (10 MB, PDF/Word/text), reply-to, headers, injection-proof fields
  - [x] Ids-only payloads for notes templates, notes read at send time, nothing copied into `mail_outbox`
  - [x] `mail_suppressions` (hash only) honoured at send time; `scripts/suppress_email.py`
  - [x] Send-time checks (meeting, owner, setting), `skipped` status; per-recipient and daily caps
  - [x] "Notes ready" mail queued with the notes, owner setting checked
  - [ ] Real send with a PDF and Word attachment from the production domain; read the Resend plan (spike ML1 follow-up)
  - [ ] Resend webhook (bounce, complaint) into `mail_suppressions`
  - [ ] A setting that turns `notify_email` off (Settings screen) and an "Email preferences" link in the mails
- [ ] W0.4 Team foundation (`visible_meetings()` replaces the owner filter in `app/workspace.py`)
- [ ] W0.5 Meeting delete
- [x] W1 Extraction, Decisions, Review (2026-11-03; migration `0011`; SOP `2026-11-03-ai-workspace-w1-extraction.md`; FE guide `docs/fe-integration-guide-ai-workspace.md`)
  - [x] Worker job with states `pending`, `running`, `done`, `failed`, `skipped`, 3 attempts, crash recovery
  - [x] Parsers for Decisions, Open questions, Please check; reconcile by fingerprint (hidden and checked survive a re-run that keeps the words)
  - [x] `decisions`, `open_questions`, `review_items`, `ai_runs`; deleted with the meeting (ledger kept)
  - [x] `GET /api/decisions`, `POST /api/decisions/{id}/hide`, `GET /api/review`, `check`, `uncheck`, `vocabulary`, `badges.review_open`, `extraction_status` on the meeting
  - [x] Backfill `scripts/backfill_extraction.py` (decisions for all, the rest for 14 days). **Adebayo or the lead runs it once after deploy**
  - [x] Privacy rows for W1 (Jinja and Next, `legal.test.tsx` green)
  - [x] FE guide with live captures (`docs/fe-captures/ai-workspace-w1/`)
  - [ ] Open questions endpoint (with the series screens, W8)
  - [ ] Regenerate `web/lib/api/schema.d.ts` (`npm run gen:api`) with the first screen
- [ ] W2 Tasks (needs W1; Opus)
- [ ] W5 Export, W3 Team, W4 Projects, W6 Share, W7 Translate, W8 Series and reminders, W9 Sprints, W10 Integrations (spec section 15)

## Backlog

- [ ] Outlook calendar (after Google)
- [ ] Microsoft Teams links (Vexa supports them; about a day plus a test call)
- [ ] (Now W7 of the AI workspace spec) Languages: a default notes language, translation as a separate view (original stays the main version), a language for each share, names and vocabulary kept unchanged (designed in docs/ui). First test a French and English call
- [ ] Mac app for capture without a bot
- [ ] (Now W6 of the AI workspace spec) Sharing notes by email (designed in docs/ui)

## Deferred follow-ups

- [x] Remove the default trusted-proxy fallback (`172.28.0.1`, `trusted_proxy_ips`, hard-coded `next` trust) once `EDGE_SHARED_SECRET` is live on the server (done 2026-11-03, `chore/remove-trusted-proxy-fallback`; Opus review finding 6; `docs/sop/2026-11-03-edge-secret-and-login-limits.md`)
- [ ] Run `deploy/nginx-limit-check.sh` (and, if cheap, `docs/spike/edge-secret/e2e.sh`) in CI
- [ ] Alert the owner on an account lockout (today a log line only). The device cookie is per account (email) since the accounts core PR
- [ ] Accounts core review follow-ups (`docs/sop/2026-11-03-pilot-accounts-core.md`): (3), (4) and (6) fixed in `feature/pilot-accounts-signup` (2026-11-03); still open: (5) cap checked only at meeting start (overshoot up to `max_poll_hours`); (7) non-ASCII `OWNER_EMAIL` can fail `ck_users_email_lower`
- [ ] Sign-up PR follow-ups (`docs/sop/2026-11-03-pilot-accounts-signup.md`): a signed ID token is not checked (received over TLS from Google's token endpoint, which OIDC allows); a rate-limited second sign-up for one address within a minute is dropped silently (an attacker could delay a real owner by a minute at a time); notifications are written for two kinds only; resend cannot take a new password; the worker's mail send has no metrics; mail templates are plain (the designer's `Emails.dc.html` is not applied); `slots_taken` has no admin screen yet
- [ ] Replace `app/data/common-passwords.txt` (384 entries) with the 10,000-entry list; add a breached-password lookup
- [x] Neutral `bot_busy` message instead of the global `bot_already_active` (2026-11-03, `feature/pilot-accounts-signup`; the real per-host placement is still Phase C/D)
- [ ] Run the updated `web/scripts/owner-e2e.mjs`, `owner-browser-check.mjs` and `docs/spike/edge-secret/e2e.sh` once against a compose stack (changed for email login, not run)
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
- [x] Google project is **In production** (Adebayo, 2026-11-03, so no 7-day token expiry) and the calendar scope is added to the consent screen, but only branding is verified and the sensitive-scope review has NOT been submitted (nothing to demo until the calendar flow exists)
- [ ] Fix the final scope list (`calendar.events.readonly`; Meet only if the instant-Meet spike passes), then submit the sensitive-scope verification once the calendar flow works (Phase V); Search Console TXT for `beyrictech.com`

Legal and business
- [ ] Mailboxes `privacy@`, `legal@`, `security@beyrictech.com` confirmed; hosting country (France per IP lookup; check the Contabo panel); a DPO decision; a lawyer reads privacy and terms
- [ ] Hour-long meeting rated against Fireflies; a French plus English call; rate the Zoom #3 notes; merge Novex-News#10
- [ ] Launch capacity: bot-machine budget, or a per-hour vendor instead
- [ ] Revisit the designer's landing and sign-in copy before the Google verification submission (decided 2026-11-03)
