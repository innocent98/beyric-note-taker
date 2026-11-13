# SOP: the server's Skribby model flipped to `elevenlabs/scribe-v2`, and the text comparison that backed the one-transcript plan

**What shipped.** A server config change, not code: `SKRIBBY_TRANSCRIPTION_MODEL` in `/opt/beyric-note-taker/.env` went from `soniox/stt-async-v6` to `elevenlabs/scribe-v2` at 18:31 WAT on 2026-11-13, then `web`, `worker` and `scheduler` were restarted by Adebayo. `TRANSCRIPT_SOURCE` stays `own` and `TRANSCRIPT_SHADOW_PERCENT` stays 0, so our own `gpt-transcribe` still writes every transcript; ElevenLabs only supplies Skribby's voice tags. The code that makes ElevenLabs usable as the only transcript is #172 (`032774c`) and #173 (`a829d00`); see `docs/sop/2026-11-13-skribby-elevenlabs-single-transcript.md` and `docs/sop/2026-11-13-skribby-vocabulary-pass.md`.

## Why

Meeting 34 (2026-11-13, Meet, 7 people spoke by Meet's own timeline) came back with 5 Skribby tags, 3 named, 2 "Unknown speaker", on `soniox/stt-async-v6`. The bake-off (#171, `docs/sop/2026-11-11-skribby-model-bakeoff.md`) scored each Skribby model against Meet's own speaking timeline on window 21:00-31:00 of meeting 34 (6 Meet speakers): `elevenlabs/scribe-v2` 6 voices and separated 4; `assemblyai/universal-3-5-pro` 5 and 2; `soniox/stt-async-v6` 4 and 1; `deepgram/nova-3` 3 and 0. One window, small speakers of 5 to 16 s: a strong hint, not a proof.

## How (the flip)

| Step | Command (run by Adebayo; lead server reads are blocked) |
|---|---|
| Only when no meeting is on (a restart cuts a recording) | `ssh haven-respite 'cd /opt/beyric-note-taker && sed -i "s#^SKRIBBY_TRANSCRIPTION_MODEL=.*#SKRIBBY_TRANSCRIPTION_MODEL=elevenlabs/scribe-v2#" .env && grep SKRIBBY_TRANSCRIPTION_MODEL .env && docker compose up -d web worker scheduler' </dev/null` |
| Verify | `ssh haven-respite 'cd /opt/beyric-note-taker && docker compose exec -T worker printenv \| grep SKRIBBY_TRANSCRIPTION_MODEL' </dev/null` printed `SKRIBBY_TRANSCRIPTION_MODEL=elevenlabs/scribe-v2` |
| Roll back | the same `sed` with `soniox/stt-async-v6`, same restart |

In `own` mode the startup check on the model name does not run (it applies only when `TRANSCRIPT_SOURCE=skribby`), so the restart could not fail on it.

## The text comparison (one-off, not in the repo)

A scratch script ran in the worker container (script on stdin: `docker compose exec -T worker python - 34 <FROM-TO> < compare34.py`). For one 10-minute window it produced three texts: ours (`gpt-transcribe` on the slice with the owner's vocabulary), ElevenLabs (a Skribby recording upload, deleted afterwards), and Soniox v6 as the live bot kept it (kept evidence, no cost). Cost about 0.10 USD a window. It wrote nothing to the database.

| | Window 21:00-31:00 | Window 9:00-19:00 |
|---|---|---|
| Words: ours / ElevenLabs / Soniox v6 | 1213 / 1507 / 1646 | 1148 / 1382 / 1445 |
| Dropped passages marked by an ellipsis: ours / ElevenLabs / Soniox | 37 / 1 / 0 | 23 / 2 / 0 |
| Words shared, ours with ElevenLabs | 83% of ours | 84% of ours |

Read by eye (no ground truth): ElevenLabs is more complete; ours lost whole sentences (for example "il y a aussi Orange qui ne passait pas sur tous les sous"); every model spelled his product names inconsistently (ours "Jomp" x10 for "Jump" and three spellings of one product, ElevenLabs "Blockwork" x13 in one window and "Blog Walk" in the other); the owner's vocabulary list holds one term and it appears in no text, so vocabulary was **not** tested; Soniox v6's words were as complete as ElevenLabs', so its failing was the voice split, not the words. One meeting, mostly French. A first count of the gaps (7) missed the single-character ellipsis and was corrected to 37.

Facts read from Skribby (2026-11-13): custom vocabulary is not listed for `elevenlabs/scribe-v2` on `skribby.io/pricing` or `/docs/guides/billing`; `soniox/stt-async-v5` is deprecated; neither page says anything about speaker names per model.

## Verification

Flip: `printenv` in the worker. Comparison: the output files were read in full by the lead (`cmp34.txt`, `cmp34b.txt` in the session scratchpad, not kept). **Not proven:** that speaker NAMES survive under ElevenLabs in a live bot meeting (the bake-off and comparison were recording uploads); alignment (`label_transcript`) on meeting 34.

## Operate / roll back

Roll back the model as in the table. The comparison needs the stored audio, kept 7 days: meeting 34's until about 2026-11-20.

## Follow-ups

1. Read the next live meeting's evidence (`scripts/skribby_segments_report.py <id>`): names still given?
2. If yes, the one-transcript flip (`TRANSCRIPT_SOURCE=skribby`, shadow 10 to 25 for a week) per `docs/HANDOFF-master.md`.
3. Adebayo adds his product names to his vocabulary list.
4. Optionally the same comparison on a mostly-English meeting.
