# SOP: diarization experiment (does an OpenAI diarizing model separate the voices Skribby merged?)

**What shipped.** A read-only diagnostic, `scripts/diarize_experiment.py <meeting_id>`, that sends a meeting's own audio to OpenAI's `gpt-4o-transcribe-diarize` and compares the voices it finds with Skribby's speaker tags. **PR:** `feature/diarize-experiment` (number in the PR). No migration, no env variable, no endpoint, no screen (so no FE guide). Not a product change.

## Why

Meeting 32 (2026-11-10 evening, Google Meet, about 46 minutes) had three people: Adebayo Tosin, Paul Iyanu, Daniel. Skribby reported only two tags: Daniel's speech sits inside Paul Iyanu's tag (around 6:50 to 7:20 two different people give two different emails under one tag). Adebayo approved this experiment before choosing a fix (another Skribby model, our own diarization, or time-based alignment).

## How to run it

**Run it in the `worker` container, not `web`:** `compose.yaml` mounts the audio volume (`audiodata:/srv/data/audio`) only on `worker`; `web` cannot see the audio. Within 7 days of the meeting (audio and Skribby evidence are both deleted after 7 days):

```
docker compose exec -T worker python scripts/diarize_experiment.py 32 --dry-run
docker compose exec -T worker python scripts/diarize_experiment.py 32
docker compose exec -T worker python scripts/diarize_experiment.py 32 --show-text 400-440
```

| Flag | What it does |
|---|---|
| `--dry-run` | Lists chunks, starts, durations, sizes and the estimated cost; calls nothing at OpenAI. (If the chunks are gone it re-cuts the recording first, which takes about a minute.) |
| `--max-minutes N` | Default 90. Refuses (exit 2, nothing sent) when the audio is longer; the cost is printed first. |
| `--show-text FROM-TO` | Seconds, e.g. `400-440`. Prints the model's turns in that window (speaker label, text) and Skribby's items in it (tag, name, text). **Without it no transcript text is printed at all.** With it, treat the output like the transcript: do not paste it anywhere public. |

Cost: `gpt-4o-transcribe-diarize` is **$0.006 per audio minute** on OpenAI's pricing page (read 2026-11-10), so meeting 32 is about $0.28.

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

Nothing is written to the database, nothing is kept on disk, no text is logged, nothing is deleted except the script's own scratch folder.

## What's involved

`scripts/diarize_experiment.py`; `tests/test_diarize_experiment.py`; `tests/test_dockerfile_scripts.py` (already checks that this documented command's script exists in the image: it accepts `worker`); `docs/checklist/master-checklist.md`.

## Verification

Run on the Mac, bounded: `ruff check`, `ruff format --check`, `tests/test_diarize_experiment.py` and `tests/test_dockerfile_scripts.py`, with a fake OpenAI client and a temp audio folder: the speaker and overlap maths (including partial overlap, items without times or tags), the time offset by chunk, the OpenAI call arguments, `--max-minutes` refusal, `--dry-run` making no call and building no client, no text printed without `--show-text` (and only the window with it), a missing key, no audio, and the scratch re-cut being removed. **Not verified live:** no real OpenAI call was made (no audio or key on the Mac); the first server run is the proof. In particular the response shape (`segments[].speaker/start/end/text`) is taken from OpenAI's guide and the installed SDK's `TranscriptionDiarizedSegment`, not from a real answer.

## Operate / roll back

Server steps before merge: none. Revert the PR to remove it; nothing else depends on it. **Note:** OpenAI's guide marks `gpt-4o-transcribe-diarize` deprecated (shutdown 2027-02-26) and recommends `gpt-transcribe` for ordinary transcription; fine for a one-off diagnostic, not for a product dependency.

## Follow-ups

Run it on meeting 32 and record the result (voice count and the table) in the checklist item. If three voices are found, spec the fix (another Skribby model vs our own diarization vs time-based alignment) as a separate piece of work.
