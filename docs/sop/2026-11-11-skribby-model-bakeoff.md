# SOP: Skribby model bake-off on meeting 32's stored audio

**What shipped.** A read-only diagnostic, `scripts/skribby_model_bakeoff.py <meeting_id>`, that cuts a slice out of a meeting's own audio, uploads it to Skribby once per transcription model (`POST /recording`), and scores which model separates the voices. **PR:** `feature/skribby-recording-bakeoff` (number in the PR). No migration, no env variable, no endpoint, no screen (so no FE guide). Not a product change; nothing under `app/` changed.

## Why

In meeting 32 (three people: Adebayo, Paul Iyanu, Daniel) Skribby's model (`soniox/stt-async-v5`, now deprecated) gave only two tags: Daniel's speech sat inside Paul's tag. Before choosing a fix, Adebayo wants to compare Skribby's other models on the **same audio, through Skribby** (no vendor keys). The OpenAI diarizing experiment is the sibling check (`docs/sop/2026-11-10-diarize-experiment.md`).

## How to run it

Run it in the **`worker`** container (it has the audio volume and the Skribby key; `web` has no audio). Within 7 days of the meeting (audio and the old Skribby evidence are both deleted after 7 days).

1. Dry run (calls nothing, needs no key; prints the slice, the cost per model and the old evidence count):

```
docker compose exec -T worker python scripts/skribby_model_bakeoff.py 32 --dry-run
```

2. **First live run: ONE model.** The upload's request and answer are read from Skribby's docs, not proven live, so run a single model first and look at the answer before spending on four:

```
docker compose exec -T worker python scripts/skribby_model_bakeoff.py 32 --models soniox/stt-async-v6
```

3. If that worked, all four default models, detached (a run can take many minutes; the file is the output):

```
docker compose exec -T -d worker sh -c "python scripts/skribby_model_bakeoff.py 32 > /tmp/bakeoff32.txt 2>&1"
```

Read it with `docker compose exec -T worker cat /tmp/bakeoff32.txt` (the file lives in the container; it holds no transcript text unless you used `--show-text`, and goes away with the container). The run is over when the file ends with a `Summary:` block or a `Stopped:` line.

| Option | What it does |
|---|---|
| `--window FROM-TO` | Seconds into the meeting; default **400-1000** (10 minutes around the moment the voices merge; cut at the audio's end if shorter). The slice is cut with the same code as `diarize_experiment.py` (re-encoded Opus WebM, one file even across two chunks) in a scratch folder that is removed afterwards. |
| `--models a,b,c` | Default: `soniox/stt-async-v6`, `assemblyai/universal-3-5-pro`, `elevenlabs/scribe-v2`, `deepgram/nova-3-multilingual`. `gladia/async` can be added by hand. `soniox/stt-async-v5` is refused (deprecated, already measured). |
| `--price X` | Per-hour rate for a model not in the price table; without it an unknown model is refused. |
| `--max-minutes N` | Default 15, per model: refuses a longer slice before anything is sent. |
| `--poll-timeout S` | Default 1200: how long to wait for each model to finish. |
| `--lang CODE` | Optional language for Skribby; not sent by default. |
| `--checkpoints FILE.json` | Replaces the built-in meeting 32 checkpoints (format below). |
| `--show-text FROM-TO` | Prints each model's turns and the old Skribby items in that window (seconds in the meeting). **Without it no transcript text is printed at all.** With it, treat the output like the transcript. |
| `--dry-run` | Everything except uploading. |

## Cost

Skribby bills a recording upload as **base processing $0.10/hour plus the model's own rate** (billed per minute of audio, only on successful transcription; recordings upload is not on the Free plan), from skribby.io/docs/guides/billing and /docs/guides/recordings-upload, read 2026-11-11. Model rates per hour: soniox v6 0.10, assemblyai universal-3.5 pro 0.23, elevenlabs scribe v2 0.20, deepgram nova-3 0.32 (gladia 0.32). The default 10-minute slice is about $0.03 (soniox) to $0.07 (deepgram) per model, about $0.2 for all four; the dry run prints the exact figures from the slice length. The base fee is why the estimate is higher than the model rate alone.

## How it works

For each model, in order: **one** `POST /recording` (multipart: `recording_file` = the slice, `transcription_model` = the id; no retry), then `GET /recording/{id}` every 10 s until `status` is `finished` (a `failed` or `invalid_api_key` status, a non-2xx answer, or the poll timeout stops the run), then `DELETE /recording/{id}` **always**, in a `finally` (after success, failure, timeout or Ctrl-C). A heartbeat line goes to stderr every 30 s while it waits. The first error stops the run and says which models were already sent. `custom_vocabulary` is **not sent**: Skribby's docs do not say which models accept it. The key is `SKRIBBY_API_KEY` from the app settings (region from `SKRIBBY_REGION`) and is never printed. Nothing is written to the database or kept on disk.

**Deleting:** Skribby documents `DELETE /recording/{id}` ("deletes all data related to the recording, including transcription and recording", permanent, 200 `{"message": "Recording deleted successfully."}`). The script prints `deleted recording <id> on Skribby`; if the delete fails it prints `COULD NOT DELETE recording <id>` to stderr with the id: delete it by hand with that id. Without a delete, a recording would stay on Skribby for 1 day by default (the audio) and the transcript indefinitely; it is Adebayo's audio, so look for that line.

## How to read the output

Per model: "The model found N distinct speaker(s)" with turns and seconds per speaker (Skribby's tag numbers), the table **speaker x old Skribby tag** (seconds of overlap with `skribby_segment_evidence`, whose times are meeting seconds; the model's times are moved by the window start), then the **checkpoint score**.

**Checkpoints** (meeting seconds, from Adebayo; built-in default, no spoken words stored): Daniel at 427-428, 431-436 and 678-679; Adebayo at 675-677. The **Adebayo voice** of a model is its speaker with the most overlap with old Skribby tag 1 (Adebayo, reliable for long stretches). For each checkpoint the script finds the model speaker with the most overlap and prints PASS/FAIL:

- Adebayo's checkpoint: PASS if it is in the Adebayo voice.
- Daniel's checkpoints: each PASS if it is in a speaker that is **not** the Adebayo voice, and a separate line says whether all three are in **one** speaker.
- A model **PASSES** only when every line passes. A checkpoint where nobody speaks is a FAIL.

| Outcome | Meaning | Next step |
|---|---|---|
| One or more models PASS | Skribby can separate Daniel with that model on this audio. | Run it on another stretch or meeting (`--checkpoints`) before switching `SKRIBBY_TRANSCRIPTION_MODEL`; check the model is allowed in `TRANSCRIPT_SKRIBBY_MODELS` (`app/config.py`). |
| All FAIL, and the OpenAI diarizing experiment found 3 voices | Skribby's models merge them; a diarizing model of our own could fix it. | Spec our own diarization. |
| All FAIL and the OpenAI experiment found 2 voices | The audio itself merges them. | Look at how the recording is captured, not at the model. |
| A model passes but with a very different speaker count (for example 6) | It over-splits. | Check with `--show-text` on a known window before trusting it. |

Checkpoints file format (`--checkpoints FILE.json`):

```
{"reference_tag": "1", "reference_person": "Adebayo",
 "checkpoints": [{"person": "Daniel", "start": 427, "end": 428},
                 {"person": "Adebayo", "start": 675, "end": 677}]}
```

`reference_tag` is the old Skribby tag that is reliably the reference person. Every checkpoint must lie inside the window.

## What's involved

`scripts/skribby_model_bakeoff.py` (imports the slice, speaker-table and heartbeat helpers from `scripts/diarize_experiment.py`); `tests/test_skribby_model_bakeoff.py`; `tests/test_dockerfile_scripts.py` (checks that the documented commands' scripts are in the image). Skribby pages read (2026-11-11): `skribby.io/docs/guides/recordings-upload`, `/docs/rest-api`, `/docs/rest-api/recording-operations/createrecording`, `getrecording`, `deleterecording`, `/docs/guides/transcription-models`, `/docs/guides/billing`.

## Verification

On the Mac, bounded: `ruff check`, `ruff format --check`, `tests/test_skribby_model_bakeoff.py` and `tests/test_dockerfile_scripts.py`, with a pretend Skribby (`httpx.MockTransport`), a temp audio folder and a fake ffmpeg: the checkpoint maths (pass, Daniel split over two speakers, Daniel inside the Adebayo voice, Adebayo outside his voice, nobody speaking), times moved to meeting time, price table and refusals (unknown model, deprecated v5, `--max-minutes`, checkpoints outside the window), the dry run making no call and reading no key, one upload per model with no retry, the delete after success, failure, a failed status and a poll timeout, a failed delete said loudly, no text without `--show-text`, no key in an error. **Not verified live:** no real Skribby call (no key or audio on the Mac). Unproven until the first run: that the multipart upload (`recording_file`, `transcription_model`) is accepted, that the answer's `transcript` items carry `speaker`, `start`, `end`, `transcript` as the docs' example shows (the docs do not define `TranscriptSegment`), the **units of `start` and `end` (seconds, per the docs)**, and that `DELETE` removes the transcript too.

## Operate / roll back

Server steps before merge: none. Revert the PR to remove it. Cost: see above; each run costs real money on Adebayo's Skribby account.

## Follow-ups

Run `--models soniox/stt-async-v6` first and fix any schema surprise; then all four; record the verdicts in the checklist item. A passing model should be re-checked on another meeting before it changes the pipeline's model.
