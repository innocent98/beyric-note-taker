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
- [ ] Task 7: Deploy (2026-11-02: files ready and validated locally, `deploy/` + compose changes; server steps pending, needs an Ubuntu server and a domain)
- [ ] Task 8: Real-world acceptance

## Backlog

- [ ] Calendar auto-join
- [ ] Mac app for capture without a bot
- [ ] Email delivery of notes

## Deferred follow-ups

- [ ] Test paid Zoom accounts
- [ ] Check Vexa licence exceptions before any commercial use
- [ ] Choose a recording notice for meetings
- [ ] Decide how long to keep audio
- [ ] Create a private GitHub repo for this app (not a Vexa fork) so CI runs
- [ ] Vexa upgrade routine: read release notes, bump the pinned image tag, re-run one Meet and one Zoom call
- [x] Raise the worker heartbeat warning from 5 to 20 minutes (2026-11-02)
