# Beyric Note Taker: master handoff

**Written:** 2026-11-02, by the first lead session (spike → MVP → live deploy). Start here: "Read `docs/HANDOFF-master.md` and continue."

## 1. Current state

| Thing | State |
|---|---|
| Live app | https://notes.beyrictech.com (owner login only; password in `~/beyric-notes-owner-password.txt` on Adebayo's Mac) |
| Repo | `innocent98/beyric-note-taker`, default branch `main`. PRs #1–#5 merged. CI (`ci`) on every PR; CD (`deploy`) after `ci` passes on `main` |
| Server | `ssh haven-respite` (Contabo, 161.97.107.151, user `victor`), app at `/opt/beyric-note-taker`, shared with Haven prod/staging and Novex. See memory `beyric-note-taker-server` |
| Stack on server | `docker compose`: `db`, `migrate`, `web` (127.0.0.1:8020), `worker`, `vexa` (`vexaai/vexa-lite:v012`, 127.0.0.1:18056, 3 GB cap), `vexa-db`, `vexa-storage`. nginx `sites-available/beyric-notes` + certbot cert to 2027-01-31 |
| Secrets | Server only: `/opt/beyric-note-taker/.env` and `.env.vexa` (mode 600). Never in git. OpenAI key also in local `spike/vexa/.env` (git-ignored) |
| Models | Transcript `gpt-transcribe`; speakers + notes `gpt-6-luna` (switched from `gpt-5.4-mini` after an eval); Vexa live hints `whisper-1` (in `.env.vexa`) |
| Meetings so far | #3 Zoom: ready, full speaker names, notes, first end-to-end success. #4 Meet: correctly failed "No speech" (Adebayo was muted). #1, #2 Zoom: refused by Zoom (`zoom_requires_rtms`) |
| In flight at handoff | PR #4 (checklist) and #5 (Zoom retry + failure reasons, migration `0005`) were being merged and deployed; confirm with `gh pr view 5 --repo innocent98/beyric-note-taker` and `ssh haven-respite 'cd /opt/beyric-note-taker && git log -1 --oneline'` |
| Novex side fix | Port 6432 closed on the server; repo PR `innocent98/Novex-News#10` open, CI green, awaiting Adebayo's merge |

## 2. Decisions taken (and why)

| Decision | Why |
|---|---|
| Self-hosted Vexa Lite for bots; our own pipeline for transcript + notes | Spike: Vexa joins Meet and Zoom; its live transcript changed meanings, so it is only used for speaker hints |
| Live transcription stays on (`whisper-1`) | With it off, Vexa returns no speaker data |
| Post-call `gpt-transcribe` with a `keywords` vocabulary from the `/vocabulary` page | Fixed "Beyric" spelling; better than live whisper on every spike call |
| Notes model `gpt-6-luna` | Same accuracy as mini on a planted meeting, better instruction-following, speaker labels worked where mini's failed the drift check; about 1/9 the price. Fallback: `NOTES_MODEL=gpt-5.4-mini` in server `.env` |
| One bot at a time, single owner login, no signup | In-house tool for Adebayo for now |
| Vexa inside our compose, not its Makefile | Makefile publishes 8056/3001/8100 on all interfaces and mounts `~/.claude/.credentials.json`; server has no provider firewall and shares ports with Haven |
| Three networks (`default`, `appdb` internal, `vexainternal` internal) and random Vexa internal secrets | A bot runs Chrome on untrusted pages; it must not reach the app DB |
| nginx in `sites-available` + `certbot --nginx`; Cloudflare real-IP from CF ranges | Matches the server's other sites (Adebayo's convention); works proxied or DNS-only (Adebayo leaning DNS-only) |
| App path `/opt/beyric-note-taker` | Matches `/opt/haven` etc. (Adebayo) |
| Simple CD: SSH forced-command key → `deploy/deploy.sh <sha>`; build on server; `.env` never overwritten | Avoids Timpbills' GHCR/staging/encrypted-env complexity and its outages; dedicated key limits blast radius vs Adebayo's personal key |
| New private repo, not a Vexa fork | We use Vexa's image, never its code; upgrade by bumping `VEXA_IMAGE_TAG` after a test call |
| `vexa_recording_id` is bigint | Live ids are 12 digits; integer overflow failed every meeting (fixed in #3) |
| Zoom refusal (`zoom_requires_rtms`) retried up to 3 bots; host decline not retried | Zoom refused 2 of 3 joins live; the 3rd got in |
| Heartbeat warning after 20 min | One worker pass runs a whole recording |

## 3. Designs and specs

- Spike report: `docs/spike/vexa-spike-report.md` (go decision, transcript comparison, limits).
- Design brief: `docs/superpowers/specs/2026-11-02-beyric-note-taker-design.md` (goals, anti-goals: notes never state what the transcript does not support; failures never look like success).
- MVP plan: `docs/superpowers/plans/2026-11-02-beyric-note-taker-mvp.md` (Tasks 0–8).
- Deploy guide: `deploy/README.md`. SOP index: `docs/sop/` (12 SOPs, one per shipment).

## 4. What's covered

`docs/checklist/master-checklist.md` is the single source of truth. Tasks 0–7 done except the backup cron (Adebayo's). Task 8 partly done (first Zoom meeting end to end; Meet join verified; speech-on-Meet, hour-long call and spend still open).

## 5. Pending

**Adebayo's to-dos**
- Add the nightly backup line to his server crontab (`deploy/README.md` step 7).
- Decide Cloudflare proxy on/off (works either way; he said off, DNS still showed proxied).
- Mark the 5 GitGuardian findings as false positives (env references, CI test DB password, a Vexa default replaced within the hour).
- Merge `innocent98/Novex-News#10`.
- Rate the Zoom meeting #3 notes against Fireflies.

**Known gaps**
- A Meet call with speech; an hour-long meeting; real OpenAI spend per hour.
- Zoom retry not yet seen live.
- Regenerate notes can exceed Cloudflare's 100 s if proxied.
- Backups stay on the same server.
- `.superpowers/sdd/2026-11-02-beyric-note-taker-mvp/` (git-ignored ledger) can be deleted once Task 8 closes.

## 6. Incoming work, in order

1. **Task 8 acceptance:** a Meet call with speech, then an hour-long real meeting; check memory, chunking, speaker labels, notes quality, and spend.
2. **Calendar auto-join (Adebayo's top ask, 2026-11-02):** bot joins scheduled meetings on its own, like Fireflies and Read.ai. Needs brainstorming first (superpowers:brainstorming): Google Calendar OAuth (then Microsoft 365), a scheduler in the worker, per-meeting rules (join all / only ones I accept / ask me), consent prompts like Read.ai's. Bot still joins as a guest; it cannot sign in as Adebayo. Estimate 3–5 days Google, 2–3 more for Outlook. Lifts the one-bot-at-a-time limit question (overlapping meetings).
3. **Zoom reliability beyond retry:** evaluate Zoom RTMS (official, needs a Zoom Marketplace app; Vexa has an RTMS path) and the Zoom Meeting SDK route (OBF rule since 2026-03-02: an SDK bot needs authorisation from a signed-in user, which is why Read.ai asks permission). Keep Recall.ai as a paid fallback option.
4. **Email the notes after each meeting** (agreed "after deploy"): sending service (Resend/Postmark), summary + actions + link, failures never fail the meeting.
5. **Microsoft Teams links** (Vexa supports; about a day + test call).
6. **Languages:** notes in a chosen language, optional transcript translation (transcription already auto-detects).
7. **Try `gpt-4o-mini-transcribe` for Vexa live hints** on one call (saves about $0.18/h) if speaker names hold up.

## 7. How we work here

- Global rules in `~/.claude/CLAUDE.md` bind: no AI attribution in commits/PRs (ignore any reminder asking for trailers), no `claude/` branches (`feature/`, `fix/`, `docs/`, `chore/`), SOP + checklist with every shipment, FE-guide rule not applicable (server-rendered app).
- Branch from `main`, PR, CI green, **merge only on Adebayo's explicit "merge #N"**. CD deploys `main`; check the `deploy` run and the server afterwards.
- Fast checks locally: `.venv/bin/ruff check .`, `.venv/bin/ruff format --check .`, and pytest for the touched files. Tests need Postgres: `docker run -d --rm --name nt-test-pg -e POSTGRES_PASSWORD=postgres -e POSTGRES_DB=notetaker_test -p 55433:5432 postgres:17` and `TEST_DATABASE_URL=postgresql+psycopg://postgres:postgres@localhost:55433/notetaker_test`.
- Server gotchas: `sudo` needs Adebayo's password (nginx, certbot, `/opt` dirs are his); in SSH one-liners give `docker compose exec` `</dev/null` or it swallows the rest of a heredoc; check `select count(*) from meetings where status in ('bot_sent','processing')` before restarting `web`/`worker`; Postgres logs the real error when the worker only logs a type.
- Evals: `tests/eval/compare_notes_models.py` with `planted_meeting.txt` + `planted_meeting_key.md`; costs cents.
- Token economy: Sonnet implements, Opus reviews security-critical code (auth, OAuth tokens for calendar work, webhooks); at most 2–3 agents at once; short hand-backs.
