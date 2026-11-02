# Beyric Note Taker: design brief

**Date:** 2026-11-02
**Evidence:** [Vexa spike report](../../spike/vexa-spike-report.md)

## Why

Adebayo's current note taker fails in four ways:

1. Its summaries are very poor.
2. Well-formatted AI summaries sit behind a paid plan.
3. The free trial did not show enough value.
4. Its transcripts are incoherent and full of misspellings.

The product wins only if the transcript is accurate and the notes can be trusted without replaying the meeting.

## Goals

- Paste a Google Meet or Zoom link from a phone or computer, and a visible bot joins.
- After the meeting, get an accurate transcript with speaker names where available.
- Get professional notes: summary, key points, decisions, action items and open questions.
- Choose or edit note templates.
- Teach it names and terms, such as "Beyric", so they are spelled correctly.
- Re-generate notes from a saved transcript without another transcription charge.

## Non-goals for this version

- Calendar auto-join
- Live transcripts during a meeting
- Multiple users, teams, billing or signup
- Mac app or Chrome extension
- Microsoft Teams
- In-person meetings

## Anti-goals

- Notes must never state something the transcript does not support.
- A failure must never look like success. If there is no recording, say why.
- The app must not say "recording" based only on Vexa's Zoom status, which was wrong in the spike.

## Design

1. The owner logs in and pastes a meeting link.
2. The app asks Vexa to send a bot named `Beyric Note Taker (recording)`.
3. A background worker checks Vexa every 30 seconds until the meeting ends. Polling is used because it needs no public callback and was proven in the spike.
4. The worker downloads the recording.
5. `ffmpeg` converts it to mono 32 kbps Opus and splits it into chunks of at most 15 minutes, near silence. This is required: spike audio was about 56 MB an hour, over OpenAI's 25 MB limit.
6. `gpt-transcribe` transcribes each chunk, using the custom vocabulary.
7. `gpt-5.4-mini` adds speaker names using Vexa's segments as hints only.
8. `gpt-5.4-mini` writes notes from the accurate transcript and chosen template. Unclear items go in a "Please check" section.
9. Audio is deleted after 7 days. Transcripts and notes stay until deleted.

## Decisions made by Claude

Adebayo asked for sensible defaults, so these can be overruled.

| Area | Default |
|---|---|
| Backend | Python, FastAPI, SQLAlchemy, Alembic, Postgres |
| Pages | Server-rendered Jinja templates, phone-friendly, no separate frontend |
| Background work | One worker process using a Postgres-backed job table |
| Login | One owner account with a password from configuration |
| Templates | General, Client call, Team update, Interview |
| Hosting | One Ubuntu 24.04 server with at least 4 GB of memory, 2 vCPUs |
| Deployment | Docker Compose for the app, Vexa Lite beside it, nginx with Let's Encrypt |
| Audio retention | 7 days |

## Constraints

- Keep Vexa's live transcription on with `whisper-1`. It is needed for speaker names, confirmed by a live test.
- Limit Vexa to one bot at a time for this version.
- Only ports 22, 80 and 443 may be open, using the provider's firewall because Docker can bypass `ufw`.
- Never place `~/.claude/.credentials.json` on the server, because Vexa Lite mounts it if present.
- Secrets stay in server environment files, never in git.
- Commits and PRs carry no AI attribution. Branches never use a `claude/` prefix.
- Run fast checks locally. CI runs the rest.

## Open risks

| Risk | Handling |
|---|---|
| Long calls are untested | Task 8 requires a real call of an hour or more |
| Paid Zoom accounts are untested | Task 8 tests one if available |
| A Zoom web change breaks joining | Failures are shown clearly. Recall.ai remains the fallback |
| Consent | The bot's name announces recording. Adebayo tells participants |
| Commercial use | Out of scope. Vexa's licence exceptions need checking first |

## Success

On a real hour-long meeting, Adebayo can read the notes, trust them without replaying the call, and rate them better than his current tool. Each meeting should cost about $0.65 an hour in OpenAI fees, excluding the server.

## Timeline

About 1–2 weeks to a deployed version.
