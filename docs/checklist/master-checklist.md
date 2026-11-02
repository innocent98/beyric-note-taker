# Beyric Note Taker: master checklist

**Status:** Spike finished with a go decision. Module 1 (MVP): Tasks 0 to 6 are built and tested, and the final fix wave (2026-11-02) is applied. Task 7 (deploy) has files ready but no server run yet; Task 8 (real-world acceptance) is not started.

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

## Next, after deploy (agreed 2026-11-02)

- [ ] Email the notes to the owner when a meeting is ready
  - [ ] Choose a sending service (Resend or Postmark) and verify the domain
  - [ ] Send summary, action items and a link to the meeting page; never the audio
  - [ ] A failed email must not fail the meeting; show it on the meeting page
- [x] Notes model switched to `gpt-6-luna` after a side-by-side eval (2026-11-02, see SOP)
- [ ] Try `gpt-4o-mini-transcribe` for Vexa's live speaker hints on one acceptance call; switch if speaker names hold up (saves about $0.18/hour)

## Backlog

- [ ] Calendar auto-join
- [ ] Mac app for capture without a bot
- [ ] Sign-up, email verification and per-user meetings (only if others will use it)

## Deferred follow-ups

- [ ] Test paid Zoom accounts
- [ ] Check Vexa licence exceptions before any commercial use
- [ ] Choose a recording notice for meetings
- [ ] Decide how long to keep audio
- [x] Create a private GitHub repo for this app (not a Vexa fork) so CI runs (2026-11-02: innocent98/beyric-note-taker, PR #1, CI green)
- [ ] Vexa upgrade routine: read release notes, bump the pinned image tag, re-run one Meet and one Zoom call
- [x] Raise the worker heartbeat warning from 5 to 20 minutes (2026-11-02)
