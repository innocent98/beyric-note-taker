# Vexa spike report

**Date:** 2026-11-02
**Vexa version:** Lite image `vexaai/vexa-lite:v012`, repo commit `31122f9`
**Decision: go.** Use self-hosted Vexa for joining, recording and speaker timing. Do not use its live transcript for notes.

## Results

| Test | Result | Evidence |
|---|---|---|
| Vexa runs on the Mac | Pass | Gateway, agent API and terminal healthy; about 830 MB idle |
| OpenAI as transcription backend | Pass | `whisper-1` worked through `TRANSCRIPTION_SERVICE_URL=https://api.openai.com` |
| Google Meet | Pass | Reached the door in about 17 s, joined once admitted, separated two real names |
| Zoom, own meeting, no waiting room | Pass | Joined and recorded; both accounts shared a display name, so speakers merged |
| Zoom, another account hosting, waiting room on | Pass | Admitted from the waiting room, recorded, left when the call ended |
| Notes quality | Pass with caveats | Structured notes with decisions and actions, about $0.002 for a 2.5-minute call |
| Real OpenAI spend | Not measured | Needs a check of the OpenAI usage page |

## Transcript quality

`gpt-transcribe` on the saved recording beat Vexa's live `whisper-1` on all three calls.

| Live `whisper-1` | `gpt-transcribe` on the recording |
|---|---|
| "I can't hear you" | "I can hear you" |
| "we can't commercialize it" | "we can commercialize it" |
| "the summary is that. Yes." | "the summary is that we have spike testing at the moment" |
| "trip to London", "deployment in Berlin" | Neither phrase appears |

`gpt-transcribe` is not perfect:

- It heard "Beyric" as "Burek", "Burik" and similar. A keywords list or prompt is untested.
- It lost part of the middle of the first Zoom call.
- It heard "absolutely finished" and "third test sprint". These are unconfirmed and may be wrong, and the notes repeated them.

## Issues found

| Issue | Impact | Handling |
|---|---|---|
| Zoom status shows `active` while the bot is in the waiting room | Status and `start_time` cannot be trusted on Zoom | Do not show "recording" from this status alone |
| Identical display names merge speakers | Attribution is lost | Expected limit |
| `/recordings/{id}/master` returns JSON, not audio | Easy mistake | Use `/recordings/{id}/media/{media_id}/raw` |
| `make lite` mounts `~/.claude/.credentials.json` if present | Credential exposure | File absent here; handle it on any server |
| Lite publishes ports 8056, 3001 and 8100 on all interfaces | Reachable on the local network | Bind or firewall them on a server |
| About 1.2-1.4 GB used with one bot, against 0.55-0.83 GB idle | Sizing | Allow 4 GB for one bot at a time; an estimated 12-16 GB for 10 at once, untested |

## Recommended design

1. Vexa joins, records and provides speaker timing.
2. After the call, download the audio and transcribe it with `gpt-transcribe`.
3. Use Vexa's speaker segments only to work out who said what.
4. Generate notes with a custom template, using a model such as `gpt-5.4-mini`.

## Limits of this evidence

- Three short calls of 2-3 minutes, all on hosts' `us05` Zoom accounts. Paid and enterprise Zoom accounts are untested.
- Zoom joins depend on the web client, so a Zoom page change could break them.
- Calls of an hour or more are untested for memory, stability and audio size.
- Speaker hints worked at summary level, but timestamp-level matching is not built.
- Whether speaker names survive with `transcribe_enabled: false` is untested. If they do, live transcription costs can be avoided.
- Vexa's licensing should be checked in full before any commercial use. The repo is Apache-2.0 but also includes `license-exceptions.json`.
