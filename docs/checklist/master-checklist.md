# Beyric Note Taker: master checklist

**Status:** Spike finished with a go decision. Module 1 is planned and awaiting approval.

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
- [ ] Task 1: Vexa client
- [ ] Task 2: Audio preparation
- [ ] Task 3: Transcription with custom vocabulary
- [ ] Task 4: Speakers and notes
- [ ] Task 5: Worker pipeline
- [ ] Task 6: Web pages
- [ ] Task 7: Deploy
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
