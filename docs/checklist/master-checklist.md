# Beyric Note Taker: master checklist

**Status (2026-11-02, evening):** MVP live at notes.beyrictech.com (PRs #1–#8 merged and deployed). Task 8 acceptance is under way (meeting 5, a Meet call with speech). The next module, Pilot (accounts plus calendar auto-join), is being designed; its public pages are built (`feature/public-pages`).

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

## Module 2: Pilot, accounts plus calendar auto-join (designing, 2026-11-02)

Agreed with Adebayo: open sign-up capped at 10 accounts; 5 recorded hours per account for the whole pilot (admin uncapped); the frontend stays server-rendered Jinja (decided 2026-11-02; UI parity and UX flexibility are conditions, see Module 2a); company is Beyrichq LTD, Lagos, Nigeria. Design: `docs/ui/Beyric Note Taker redesign/` (Calendar card, Upcoming list, Settings → Calendar, legal pages).

- [ ] Public pages for Google verification
  - [x] Landing, Privacy and Terms built and tested, text true to the app (`feature/public-pages`, 2026-11-02; see SOP)
  - [x] Merged and live (PR #10, 2026-11-02): `/`, `/privacy`, `/terms` return 200
  - [ ] Checked in a browser, light and dark
  - [ ] Adebayo: Google Cloud project, OAuth consent screen, verify beyrictech.com in Search Console, mailboxes privacy@/legal@/security@
  - [ ] Submit for verification (needs the working calendar flow and a demo video)
- [x] Frontend approach decided: stay on Jinja, with parity and flexibility as conditions (`docs/superpowers/specs/2026-11-02-frontend-approach.md`, 2026-11-02)
- [ ] Written design (`docs/superpowers/specs/`), builder check, Adebayo's approval
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

- [ ] Test paid Zoom accounts
- [ ] Check Vexa licence exceptions before any commercial use
- [ ] Choose a recording notice for meetings
- [x] Decide how long to keep audio: 7 days (`audio_retention_days`), stated in the privacy policy (2026-11-02)
- [x] Create a private GitHub repo for this app (not a Vexa fork) so CI runs (2026-11-02: innocent98/beyric-note-taker, PR #1, CI green)
- [ ] Vexa upgrade routine: read release notes, bump the pinned image tag, re-run one Meet and one Zoom call
- [x] Raise the worker heartbeat warning from 5 to 20 minutes (2026-11-02)
