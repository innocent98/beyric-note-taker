SOURCE: https://docs.vexa.ai/quickstart.md
Fetched 2026-11-07 with curl -sL -A 'Mozilla/5.0'

> ## Documentation Index
> Fetch the complete documentation index at: https://docs.vexa.ai/llms.txt
> Use this file to discover all available pages before exploring further.

# Quickstart

> From zero to your first transcript and your first agent — hosted in one API call, or self-hosted in about five minutes.

## Just want a bot in a meeting?

Use the hosted service — no install. Sign in at [vexa.ai/signin](https://vexa.ai/signin) with a
Google account, copy your API key from [your account page](https://vexa.ai/account), and send a bot:

```bash theme={null}
curl -X POST "https://api.cloud.vexa.ai/bots" \
  -H "X-API-Key: $API_KEY" -H "Content-Type: application/json" \
  -d '{"platform":"google_meet","native_meeting_id":"abc-defg-hij","bot_name":"Vexa"}'
```

`native_meeting_id` is the id inside the join URL — `abc-defg-hij` in
`https://meet.google.com/abc-defg-hij`. New accounts get \*\*$2 of free bot credit and no credit card
is required** — about 6 hours of bot time at $0.30/hr ([pricing](https://vexa.ai/pricing)). More
calls: [Send a bot](https://docs.vexa.ai/how-to/send-a-bot.md).

> [!TIP]
>
> **Hosted, no install:** [connect your agent](https://vexa.ai/connect?utm_source=docs\&utm_medium=tip\&utm_campaign=quickstart) (Claude Code, Codex, Cursor) or [get an API key](https://vexa.ai/start?utm_source=docs\&utm_medium=tip\&utm_campaign=quickstart) (free credit, no card required). Self-hosting: [Deployment](https://docs.vexa.ai/deployment.md).

The rest of this page self-hosts the whole stack, which is how you get the agent plane as well.

***

This walkthrough takes you from a clean machine to **two first wins**: a bot capturing a live
meeting transcript, and an agent answering from your workspace. You'll self-host the whole stack
with one command.

## Prerequisites

* A Linux host (Ubuntu 24.04 recommended for production), `git`, **`make`**, and **Docker engine ≥ v26**
  (`make all` checks and refuses older engines). A Mac with Docker Desktop works for a local
  evaluation — everything runs in containers either way.
* `make all` **pulls the published, release-validated images** — no build, any modest machine works;
  `make lite` pulls the single-container all-in-one image and is lighter still. Building from
  source (`make dev`) is for contributors and wants **8 vCPUs / 16 GB RAM**.
* On Apple Silicon: arm64 images are published best-effort (not yet CI-validated) — see
  [Deployment](https://docs.vexa.ai/deployment.md#quick-start-docker-compose) for details.
* A **transcription (STT) token** — get one free at [`vexa.ai/account`](https://vexa.ai/account), or
  [self-host transcription](https://docs.vexa.ai/deployment.md#no-egress) on a GPU. Default spawns **require** STT and
  answer `503` when it is unset; capture-only is `{"transcribe_enabled": false}` on `POST /bots`
  (or `TRANSCRIBE_ENABLED=false` for the whole deployment). See
  [Configuration](https://docs.vexa.ai/configuration.md#transcription-stt).

## 1. Install and start

```bash theme={null}
curl -fsSL https://get.docker.com | sh
git clone https://github.com/Vexa-ai/vexa.git && cd vexa
make all          # full Docker Compose stack — pulls the published images (bot included), prints your API key
                  # contributors: `make dev` builds everything from this checkout instead (:dev tags)
```

`make all` brings up the whole stack and **prints an API key + your URLs** when it finishes:

```text theme={null}
  Vexa is up.
    Terminal UI : http://localhost:13000
    API gateway : http://localhost:18056
    API key     : vxa_…
```

| Surface | URL |
| - | - |
| **Terminal** (web workbench UI) | `http://localhost:13000` |
| **API gateway** | `http://localhost:18056` |

> [!NOTE]
>
> `make all` seeds `.env` from `.env.example` on first run and mints a self-host API key for you
> (via `deploy/compose/bin/provision-token`). **The seeded `.env` ships with every credential empty**
> — `make all` prints a warning box (and ⚠ lines in the banner) until you fill them: the STT pair for
> win 1 (transcripts), one model credential for win 2 (agent chat) — either in `.env` or under
> **Settings → Models** in the Terminal. To mint additional scoped keys yourself, see
> [Authentication](https://docs.vexa.ai/authentication.md).

## 2. First win — capture a meeting (UI)

**1. Open the terminal**

Go to `http://localhost:13000`. You're already signed in to a self-host account.

**2. Send a bot to a meeting**

Start a Google Meet, Zoom, or Teams call, copy the join URL, and add it from the UI. A bot joins
the call as a participant — no plugin, no host setup.

**3. Watch the transcript stream**

Speak. Speaker-attributed segments appear live, draft-then-confirmed, as the meeting runs.

## 2′. Same thing over the API

Use the key `make all` printed, then send a bot and read the transcript:

```bash theme={null}
# the key make all printed (or: make -s provision-token ADMIN_TOKEN=<your ADMIN_TOKEN>)
export API_KEY=vxa_...
export API_BASE=http://localhost:18056

# send a bot — native_meeting_id is the id from the join URL (e.g. abc-defg-hij)
curl -X POST "$API_BASE/bots" \
  -H "X-API-Key: $API_KEY" -H "Content-Type: application/json" \
  -d '{"platform":"google_meet","native_meeting_id":"abc-defg-hij","bot_name":"Vexa"}'

# read the transcript (poll while the meeting runs)
curl -H "X-API-Key: $API_KEY" \
  "$API_BASE/transcripts/google_meet/abc-defg-hij"
```

Full surface — translation, WebSocket streaming, recordings, stopping the bot — in the
[Meetings API](https://docs.vexa.ai/api/meetings.md).

## 3. Second win — ask your workspace a question

Every captured meeting compiles into your **workspace** — a Markdown knowledge base an agent reads
and writes like a developer in a repo. Chat with it:

```bash theme={null}
curl -N -X POST "$API_BASE/agent/chat" \
  -H "X-API-Key: $API_KEY" -H "Content-Type: application/json" \
  -d '{"prompt":"What did we decide in my last meeting?"}'
```

The response streams back as Server-Sent Events — `message-delta` frames carry the reply, `commit`
frames mark anything the agent recorded. See the [Agent API](https://docs.vexa.ai/api/agent.md).

## 4. Third win — connect your calendar

Stop pasting links: give Vexa your calendar's **secret ICS address** (Google Calendar → Settings →
your calendar → *Integrate calendar* → **Secret address in iCal format**, the `…/private-…/basic.ics`
one — *not* the public address above it). Every upcoming event imports; Meet/Zoom/Teams events are
armed for auto-join and link-less events remain visible until you add a link. On a Google
**Workspace** domain the secret-address field may need a one-time admin unlock
([how](https://docs.vexa.ai/how-to/calendar-sync.md#1-find-your-secret-ics-address)).

```bash theme={null}
curl -X POST "$API_BASE/user/calendars" \
  -H "X-API-Key: $API_KEY" -H "Content-Type: application/json" \
  -d '{"name":"Work","ics_url":"https://calendar.google.com/calendar/ical/…/basic.ics","auto_join":true,"bot_name":"Work Notes"}'
```

Run `POST /user/calendars/{calendar_id}/sync` for an immediate result, or wait for the background
cycle. Full walkthrough: [Calendar sync](https://docs.vexa.ai/how-to/calendar-sync.md). Complete endpoint reference:
[Calendar API](https://docs.vexa.ai/api/calendar.md).

## Next steps

> [card] [Plan and share a meeting](https://docs.vexa.ai/how-to/plan-a-meeting.md)
>
> Prepare a shared knowledge space and invite the people you're meeting.

> [card] [Get a report after every meeting](https://docs.vexa.ai/how-to/post-meeting-report.md)
>
> A routine that writes notes, decisions, and follow-ups when a call ends.

> [card] [Brief me every morning](https://docs.vexa.ai/how-to/daily-brief.md)
>
> An unattended agent on a cron schedule.

> [card] [Authentication](https://docs.vexa.ai/authentication.md)
>
> Mint, scope, and rotate API keys for the full stack.

> [card] [Deploy to production](https://docs.vexa.ai/deployment.md)
>
> Docker Compose self-host, configuration, and no-egress transcription.


This documentation is built and hosted on [Mintlify](https://mintlify.com), a developer documentation platform.