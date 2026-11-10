# SOP: diarization experiment (does an OpenAI diarizing model separate the voices Skribby merged?)

**What shipped.** A read-only diagnostic, `scripts/diarize_experiment.py <meeting_id>`, that sends a meeting's own audio to OpenAI's `gpt-4o-transcribe-diarize` and compares the voices it finds with Skribby's speaker tags. **PR:** `feature/diarize-experiment` (number in the PR). No migration, no env variable, no endpoint, no screen (so no FE guide). Not a product change.

## Why

Meeting 32 (2026-11-10 evening, Google Meet, about 46 minutes) had three people: Adebayo Tosin, Paul Iyanu, Daniel. Skribby reported only two tags: Daniel's speech sits inside Paul Iyanu's tag (around 6:50 to 7:20 two different people give two different emails under one tag). Adebayo approved this experiment before choosing a fix (another Skribby model, our own diarization, or time-based alignment).

## How to run it

**Run it in the `worker` container, not `web`:** `compose.yaml` mounts the audio volume (`audiodata:/srv/data/audio`) only on `worker`; `web` cannot see the audio. Within 7 days of the meeting (audio and Skribby evidence are both deleted after 7 days):

**What went wrong on 2026-11-10.** The first live run sent the whole of meeting 32 (4 chunks of about 15 minutes). It timed out on chunk 1 with `APITimeoutError after 3 attempt(s)`: the request timeout was 300 s and the shared retry helper tries 3 times, each time re-sending the whole 15-minute chunk. Every attempt may be billed, so Adebayo's OpenAI balance dropped by more than expected, and we had no answer. So the script now **starts with a short slice, tries once, and says what it can have cost**.

**Start here: a short slice around the moment the voices merge** (Paul and Daniel give two different emails around 6:50 to 7:20, so seconds 330 to 510 cover it; 3 minutes, about $0.018):

```
docker compose exec -T worker python scripts/diarize_experiment.py 32 --window 330-510 --dry-run
docker compose exec -T worker python scripts/diarize_experiment.py 32 --window 330-510
docker compose exec -T worker python scripts/diarize_experiment.py 32 --window 330-510 --show-text 400-440
```

Only after a slice worked, and only if you want the whole meeting (about $0.28, and speaker labels are then per chunk, see below):

```
docker compose exec -T worker python scripts/diarize_experiment.py 32
```

| Flag | What it does |
|---|---|
| `--window FROM-TO` | Seconds into the meeting, e.g. `330-510`. Cuts **only that slice** out of the prepared chunks (re-encoded as `app/audio.py` encodes; a window spanning two chunks is joined into one file, so the model sees one recording and its labels stay consistent), sends only it, and moves the returned times by FROM so the table and `--show-text` stay in meeting time. A window ending past the audio is cut at the end. Refused over 600 s unless `--allow-long`. |
| `--attempts N` | Default **1**: a timeout or error stops at once, never a silent retry. It prints the most the failed attempt(s) can have cost (`At most $0.018 was spent: 1 attempt(s), 3.0 audio minutes sent`). `--attempts 2` or more retries (each retry re-sends the audio and may be billed again). |
| `--timeout SECONDS` | Default **600**: the request timeout. |
| `--dry-run` | Lists what would be sent (the slice, or the chunks), starts, durations, sizes and the estimated cost, computed from the slice minutes; calls nothing at OpenAI. (The slice is still cut, so ffmpeg is exercised. If the chunks are gone it re-cuts the recording first, which takes a while.) (If the chunks are gone it re-cuts the recording first, which takes a while.) |
| `--max-minutes N` | Default 90. Refuses (exit 2, nothing sent) when the audio to send (the slice, or all chunks) is longer; the cost is printed first. |
| `--show-text FROM-TO` | Seconds, e.g. `400-440`. Prints the model's turns in that window (speaker label, text) and Skribby's items in it (tag, name, text). **Without it no transcript text is printed at all.** With it, treat the output like the transcript: do not paste it anywhere public. |

Cost: `gpt-4o-transcribe-diarize` is **$0.006 per audio minute** on OpenAI's pricing page (read 2026-11-10), so a 3-minute slice is about $0.018 and meeting 32 about $0.28. The estimate is printed before sending, from the audio minutes to be sent; at the end the audio minutes actually sent (every attempt counted) are printed. While a request is in flight a line goes to stderr every 30 seconds (`still waiting for slice.webm: 60s`), so a quiet connection does not look dead.

## How to read the output

1. "The model found N distinct speaker(s)" with turns and seconds per speaker (labels `A`, `B`, ...).
2. A table: rows are the model's speakers, columns are Skribby tags (with the name Skribby gave), cells are seconds of overlap. **One Skribby column with large numbers in two rows** means that tag holds two of the model's voices: exactly the meeting 32 symptom.

| Result | Meaning | Next step |
|---|---|---|
| **3 voices**, and one Skribby tag overlaps two of them | The audio holds three separable voices and a diarizing model finds them; Skribby's model is what merged them. | A diarizing model could fix it: try another Skribby model, or label speakers from our own diarization. |
| **2 voices** | The audio itself merges two people (for example one microphone, or a shared room). | Our own diarization cannot fix it either; look at how the recording was captured (per-participant audio, or the bot's source) rather than at the model. |
| Many voices (4 or more) | The model over-splits. | Check with `--show-text` on a known window before trusting the count. |

## How it works

Audio: the chunks the pipeline cut (`<audio_dir>/<id>/chunks/chunk_*.webm`, `app/pipeline.py::_step_transcribing` writes them) if they are still there; otherwise `source.webm` is re-cut the same way (`audio.prepare_recording`) into a `diarize-experiment-*` folder next to it, removed afterwards (also on failure). Each chunk's duration comes from ffprobe (`audio._duration`) and its times are offset by the chunks before it. The call follows OpenAI's guide (`developers.openai.com/api/docs/guides/speech-to-text`): `response_format="diarized_json"`, `chunking_strategy="auto"` (needed above 30 s), no prompt, uploads at most 25 MB (checked before sending). It uses `app/retry.py::call_with_retry` and the `OpenAI(...)` construction of `app/transcribe.py`; the key is `OPENAI_API_KEY` from the app settings and is never printed. Skribby's items come from `skribby_segment_evidence` (read only).

Slice: `cut_slice` runs one ffmpeg command with the same encode arguments as `app/audio.py` (`audio.ENCODE_ARGS`) into a `diarize-experiment-slice-*` folder next to the audio, joining the pieces with ffmpeg's `concat` filter when the window spans two chunks; the folder is removed on exit, also after a failure. Retries: `app/retry.py::call_with_retry` gained an optional `attempts` argument (default unchanged: 3); this script passes 1 unless `--attempts` says otherwise. The OpenAI client is built with `max_retries=0` (no hidden SDK retries) and the `--timeout`.

Nothing is written to the database, nothing is kept on disk, no text is logged, nothing is deleted except the script's own scratch folder.

## What's involved

`scripts/diarize_experiment.py`; `app/retry.py` (the optional `attempts` argument); `tests/test_diarize_experiment.py`; `tests/test_dockerfile_scripts.py` (already checks that this documented command's script exists in the image: it accepts `worker`); `docs/checklist/master-checklist.md`.

## Verification

Run on the Mac, bounded: `ruff check`, `ruff format --check`, `tests/test_diarize_experiment.py` and `tests/test_dockerfile_scripts.py`, with a fake OpenAI client and a temp audio folder: the speaker and overlap maths (including partial overlap, items without times or tags), the time offset by chunk, the OpenAI call arguments, `--max-minutes` refusal, `--dry-run` making no call and building no client, no text printed without `--show-text` (and only the window with it), a missing key, no audio, and the scratch re-cut being removed. **Not verified live:** no real OpenAI call was made (no audio or key on the Mac); the first server run is the proof. In particular the response shape (`segments[].speaker/start/end/text`) is taken from OpenAI's guide and the installed SDK's `TranscriptionDiarizedSegment`, not from a real answer.

## Operate / roll back

Server steps before merge: none. Revert the PR to remove it; nothing else depends on it. **Note:** OpenAI's guide marks `gpt-4o-transcribe-diarize` deprecated (shutdown 2027-02-26) and recommends `gpt-transcribe` for ordinary transcription; fine for a one-off diagnostic, not for a product dependency.

## Slice follow-up (2026-11-11, branch `feature/diarize-slice`)

Added `--window`, `--attempts`, `--timeout`, `--allow-long`, the 30-second heartbeat and the sent-minutes line after the live timeout above. Verified with the fake client (window cut and time offset, a window across a chunk boundary, exactly one call on a timeout with the default, the refusal over 600 s, the estimate from the slice, the heartbeat) and once by hand with real ffmpeg on two synthetic 20-second Opus files (a 15-27 s window spanning both came out as one 12.0 s file and the scratch folder was gone). **Not verified live:** no OpenAI call was made.

**Caveat when sending the whole meeting:** the model labels speakers `A`, `B`, ... per request, so the same letter in two chunks need not be the same person. That is why a window is one file, and why the whole-meeting run prints a note about it.

## Follow-ups

Run it on meeting 32 and record the result (voice count and the table) in the checklist item. If three voices are found, spec the fix (another Skribby model vs our own diarization vs time-based alignment) as a separate piece of work.
