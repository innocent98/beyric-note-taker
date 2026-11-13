# SOP: Skribby model bake-off on a meeting's stored audio (meeting 32 checkpoints; meeting 34 against Meet's timeline)

**What shipped.** A read-only diagnostic, `scripts/skribby_model_bakeoff.py <meeting_id>`, that cuts a slice out of a meeting's own audio, uploads it to Skribby once per transcription model (`POST /recording`), and scores which model separates the voices. **PR:** `feature/skribby-recording-bakeoff` (#169). **Extended 2026-11-13** (branch `feature/skribby-bakeoff-meet-score`, PR number in the PR): a second scoring mode that uses **Meet's own speaker timeline** as the reference (`--score-against-meet`), a window helper (`--list-windows`), and a fix for the delete's HTTP 204 (see the new section below). No migration, no env variable, no endpoint, no screen (so no FE guide). Not a product change; nothing under `app/` changed.

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
| `--score-against-meet`, `--list-windows`, `--window-length`, `--top`, `--min-speech`, `--meet-offset` | Meet-timeline scoring and the window helper: see the section above. |

## Scoring against Meet's own speaker timeline (meeting 34, 2026-11-13)

**Why.** Meeting 34 (Friday 2026-11-13) had **7 people speaking**, but Skribby sent 5 tags (3 named, 2 "Unknown speaker"): small speakers were merged into others' tags. The checkpoint mode above only knows meeting 32's facts (2 voices), so it cannot rank models on 34. The reference here is Meet's own "who is speaking" timeline, kept in `skribby_segment_evidence.events` (read and clock-placed by the same code as `scripts/skribby_segments_report.py`: `speaking_intervals`, `_anchor`, `clock_fit`; nothing copied). The audio of meeting 34 stays on the worker until about **2026-11-20** (7-day retention), and so does the evidence row.

**Run it (server, `worker` container; each command is read-only on our side).**

1. Pick the windows. This reads only the database (no audio, no Skribby key, no cost) and prints the 10-minute windows where the most distinct Meet participants spoke, best first, none overlapping:

```
docker compose exec -T worker python scripts/skribby_model_bakeoff.py 34 --list-windows
```

Pick 1 to 2 windows. Read the `Clock check:` line at the end: if it says `WARNING ... --meet-offset N`, add that `--meet-offset N` to every command below (Meet's clock and the transcript's clock are placed from the bot's `recording` status, which is only close to the audio's own start).

2. Dry run on a chosen window (replace `FROM-TO` with the window's `--window` value, e.g. `1200-1800`; no upload, no key; prints the cost per model and how many participants spoke in the slice):

```
docker compose exec -T worker python scripts/skribby_model_bakeoff.py 34 --score-against-meet --window FROM-TO --dry-run
```

3. The live run on the four default models (`soniox/stt-async-v6` is the baseline: it is Skribby's current model and the first of the four), detached, output to a file in the container:

```
docker compose exec -T -d worker sh -c "python scripts/skribby_model_bakeoff.py 34 --score-against-meet --window FROM-TO > /tmp/bakeoff34a.txt 2>&1"
```

Read it with `docker compose exec -T worker cat /tmp/bakeoff34a.txt`; it is over when the file ends with a `Summary:` block or a `Stopped:` line. For a second window, repeat step 3 with the other `FROM-TO` and `/tmp/bakeoff34b.txt`. **Cost:** about **$0.21 per 10-minute window** for the four models (soniox v6 $0.033, assemblyai $0.055, elevenlabs $0.050, deepgram $0.070; the dry run prints the exact figures), so about $0.42 for two windows.

**What it prints per model** (participants' names appear in the output, but no transcript text unless `--show-text FROM-TO` is passed, as before):

| Part | Meaning |
|---|---|
| Header | `Meet says N participant(s) spoke in this slice; the model found M voice(s).` A participant counts when they spoke at least `--min-speech` seconds (default 2) inside the slice by Meet's timeline. |
| Table | Seconds of overlap, model speaker (rows) x Meet participant (columns). |
| Per participant | The model speaker holding most of that participant's speech, how many seconds and what share of their speech it holds, and **purity**: the share of that speaker's overlap that belongs to this participant. `SHARED with X` means another participant's most-held speaker is the same one. |
| Verdict | One line: voices found for participants; **separated** (a participant alone in their voice); **merged into another's voice** (`Cy into Ann's voice`: Ann holds that voice most, Cy is also most in it); the keepers of shared voices; participants **not heard** (no model speaker was talking while they spoke). |
| Summary | The verdicts ranked best first: most participants separated, then fewest merged, then mean purity. |

**How to read it.** The best model separates the most participants with high purity. A model with many more voices than participants over-splits (a participant's speech spread over several speakers shows as a low "holds ... %"). The baseline `soniox/stt-async-v6` is the number to beat. Meet's timeline is itself a signal (who the platform highlighted), not perfect truth: overlapping talk and the clock placement blur seconds, so judge by the pattern across the table, not by one cell.

Options added: `--window-length S` (default 600) and `--top N` (default 3) for `--list-windows`; `--min-speech S`; `--meet-offset S` (seconds **added** to Meet's times, same meaning as `--offset` in the segments report). `--score-against-meet` **requires** `--window` (the default 400-1000 was picked for meeting 32) and refuses, before cutting or sending anything, a meeting with no kept events, events it cannot read or place, or a window where fewer than two participants spoke. It ignores `--checkpoints`. The checkpoint mode (no `--score-against-meet`) is unchanged.

## Cost

Skribby bills a recording upload as **base processing $0.10/hour plus the model's own rate** (billed per minute of audio, only on successful transcription; recordings upload is not on the Free plan), from skribby.io/docs/guides/billing and /docs/guides/recordings-upload, read 2026-11-11. Model rates per hour: soniox v6 0.10, assemblyai universal-3.5 pro 0.23, elevenlabs scribe v2 0.20, deepgram nova-3 0.32 (gladia 0.32). The default 10-minute slice is about $0.03 (soniox) to $0.07 (deepgram) per model, about $0.2 for all four; the dry run prints the exact figures from the slice length. The base fee is why the estimate is higher than the model rate alone.

## How it works

For each model, in order: **one** `POST /recording` (multipart: `recording_file` = the slice, `transcription_model` = the id; no retry), then `GET /recording/{id}` every 10 s until `status` is `finished` (a `failed` or `invalid_api_key` status, a non-2xx answer, or the poll timeout stops the run), then `DELETE /recording/{id}` **always**, in a `finally` (after success, failure, timeout or Ctrl-C). A heartbeat line goes to stderr every 30 s while it waits. The first error stops the run and says which models were already sent. `custom_vocabulary` is **not sent**: Skribby's docs do not say which models accept it. The key is `SKRIBBY_API_KEY` from the app settings (region from `SKRIBBY_REGION`) and is never printed. Nothing is written to the database or kept on disk.

**Deleting:** Skribby documents `DELETE /recording/{id}` ("deletes all data related to the recording, including transcription and recording", permanent). The docs show 200 with a message, but the real answer is **HTTP 204 with no body** (a known bug: the script printed `COULD NOT DELETE ... (HTTP 204)` although the recording was deleted); **any 2xx now counts as deleted**. The script prints `deleted recording <id> on Skribby`; if the delete fails it prints `COULD NOT DELETE recording <id>` to stderr with the id: delete it by hand with that id. Without a delete, a recording would stay on Skribby for 1 day by default (the audio) and the transcript indefinitely; it is Adebayo's audio, so look for that line.

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

`scripts/skribby_model_bakeoff.py` (imports the slice, speaker-table and heartbeat helpers from `scripts/diarize_experiment.py`, and Meet's timeline reading and clock fit from `scripts/skribby_segments_report.py`; that script's clock-fit maths moved into two shared functions, `heard_by_tag` and `clock_fit`, with its output unchanged); `tests/test_skribby_model_bakeoff.py`; `tests/test_dockerfile_scripts.py` (checks that the documented commands' scripts are in the image). Skribby pages read (2026-11-11): `skribby.io/docs/guides/recordings-upload`, `/docs/rest-api`, `/docs/rest-api/recording-operations/createrecording`, `getrecording`, `deleterecording`, `/docs/guides/transcription-models`, `/docs/guides/billing`.

## Verification

On the Mac, bounded: `ruff check`, `ruff format --check`, `tests/test_skribby_model_bakeoff.py` and `tests/test_dockerfile_scripts.py`, with a pretend Skribby (`httpx.MockTransport`), a temp audio folder and a fake ffmpeg: the checkpoint maths (pass, Daniel split over two speakers, Daniel inside the Adebayo voice, Adebayo outside his voice, nobody speaking), times moved to meeting time, price table and refusals (unknown model, deprecated v5, `--max-minutes`, checkpoints outside the window), the dry run making no call and reading no key, one upload per model with no retry, the delete after success, failure, a failed status and a poll timeout, a failed delete said loudly, no text without `--show-text`, no key in an error. **Added 2026-11-13** (same bounded set, plus `tests/test_skribby_evidence.py` for the report's refactor, run against a scratch Postgres that was removed afterwards): a synthetic Meet timeline (seconds, and Unix milliseconds placed from the `recording` status time with an offset), the participant threshold, every voice apart (purity 1.0), one participant inside another's voice, a participant nobody was talking for, a split voice, ranking, the window ranking (non-overlapping, ties by speech), `--list-windows` calling nothing, the clock check flagging a 5 s shift with the offset to use, the Meet run end to end against the pretend Skribby (two models ranked, no text, scratch removed), the refusals (no window, fewer than two speakers, no events), a 200, 202 and 204 delete counted as deleted while 301 and 404 are not. **Not verified live:** no real Skribby call (no key or audio on the Mac). Unproven until the first run: that the multipart upload (`recording_file`, `transcription_model`) is accepted, that the answer's `transcript` items carry `speaker`, `start`, `end`, `transcript` as the docs' example shows (the docs do not define `TranscriptSegment`), the **units of `start` and `end` (seconds, per the docs)**, and that `DELETE` removes the transcript too.

## Operate / roll back

Server steps before merge: none. Revert the PR to remove it. Cost: see above; each run costs real money on Adebayo's Skribby account.

## Follow-ups

Run `--models soniox/stt-async-v6` first and fix any schema surprise; then all four; record the verdicts in the checklist item. **Meeting 34:** run the Meet-timeline steps before about 2026-11-20 and record each model's verdict line in the checklist. **Unproven until that run:** Meet's event times being Unix milliseconds placed from the `recording` status time (the clock check line says if they are off by seconds). A passing model should be re-checked on another meeting before it changes the pipeline's model.
