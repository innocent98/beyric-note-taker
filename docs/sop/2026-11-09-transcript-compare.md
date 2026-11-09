# SOP: transcript comparison tool (Skribby's transcript vs ours)

**What shipped:** `scripts/transcript_compare.py`, a tool Adebayo runs by hand on his Mac. For one meeting kept at Skribby it writes one web page (and a text copy) with Skribby's transcript (speaker names) and ours side by side. **PR:** opened 2026-11-09 from `feature/transcript-compare`. **No pipeline change, no source switch, no migration, no endpoint, no screen.** The one change outside the new files: `OPENAI_API_KEY` joins the key names `scripts/skribby_live_check.py` knows how to read from the keys file and hide, so it can never be printed.

## Why

Transcription is about $0.70 of the roughly $1.17 an hour a meeting costs us (`docs/HANDOFF-master.md`, cost row). Skribby also makes a transcript (model `soniox/stt-async-v5`, $0.10 an hour). Today the app uses Skribby's only for speaker names and then deletes it (`docs/sop/2026-11-08-skribby-provider.md` section 1.4). Adebayo wants to read both on real meetings before deciding whether Skribby's could replace ours. The switch itself is built only after he decides.

## How to run it (plain words)

You need: this branch checked out (the commands below run from this folder), `ffmpeg` installed (it already is on your Mac), and your keys in `~/.config/beyric-bakeoff/keys.env`: `SKRIBBY_API_KEY`, `SKRIBBY_REGION`, and one new line `OPENAI_API_KEY=...` (your OpenAI key, the same one the app uses). The tool only reads from Skribby; it never deletes anything there.

**Step 0, the catch.** The app deletes Skribby's copy of a meeting once it has saved the speaker names (a few minutes after the meeting ends). Once deleted, there is nothing to compare. So for the meeting you want to compare, use one of these:

1. **Fastest, no setup:** as soon as the meeting ends, open Skribby's dashboard, copy the bot's id (the long `xxxxxxxx-xxxx-...` code of that bot), and run step 1 below within a few minutes. If it says "Skribby has no such bot", the app got there first; use way 2 next time.
2. **Safe:** keep the app out of it for that meeting (turn auto-join off for it) and send a bot yourself, kept at Skribby (there is no `--delete`, so the data stays 7 days):

```bash
PYTHONPATH=. ../skribby-check/.venv/bin/python scripts/skribby_live_check.py --meet-url https://meet.google.com/abc-defg-hij --model soniox/stt-async-v5 --stay 3600 --max-minutes 120
```

It prints `Bot created: <id>`. That id is what step 1 needs. (`--model` makes Skribby use the same transcription model as the app's setting, so you judge the one you would switch to; the page header shows which model made the transcript. The live check stops the bot after `--stay` seconds once admitted, so set `--stay` to the meeting's length.)

**Step 1, compare** (replace the id; `--vocabulary` is optional, a text file with one term per line, like your vocabulary list):

```bash
cd /Users/adebayovictor/Documents/dev/beyric-note-taker/.claude/worktrees/transcript-compare-bee078
PYTHONPATH=. ../skribby-check/.venv/bin/python scripts/transcript_compare.py --bot-id <the bot id> --vocabulary ~/vocabulary.txt
```

It downloads the recording, runs our transcription on it (this costs OpenAI money, about $0.70 for a one-hour meeting), and prints where the page is: `~/transcript-compare/<first 8 characters of the id>/compare.html`. Open that in your browser. `compare.txt` in the same folder is the same thing as plain text.

**Step 2, read it again later, free and offline.** Everything is saved in that folder (`skribby.json`, `recording`, `ours.txt`), so this needs no Skribby and no OpenAI:

```bash
PYTHONPATH=. ../skribby-check/.venv/bin/python scripts/transcript_compare.py --audio ~/transcript-compare/<id8>/recording --skribby-json ~/transcript-compare/<id8>/skribby.json --our-text ~/transcript-compare/<id8>/ours.txt
```

Leave out `--our-text` to transcribe the audio again (for example after changing the vocabulary file). Re-running step 1 with the same id also reuses the saved files and does not ask Skribby again.

## How to read the page

- **Top cards:** recording length; word count of each; estimated cost an hour of the two paths (see below); Skribby's speakers and how many turns each has; our model and how many vocabulary terms were given; how many rows were lined up.
- **Rows:** one per Skribby *turn* (one speaker's run of segments). Left, Skribby: speaker name, time range, text. Right, ours: the stretch of our words that goes with it. Every word of both transcripts appears exactly once; the tool writes no text of its own.
- **The tag on each row:** `matched` means the first words of Skribby's turn were found in our text near where they should be, so our stretch starts there. `placed by position` (yellow) means they were not found (the two transcripts word it differently there), so the start was worked out from the rows around it: read that row with care, the cut may be a few words off. "N% of words shared" is how many words the two sides have in common in that row: 100% is identical words, a low number is a real disagreement worth reading.
- **What to look for:** names and jargon (does Skribby spell what we spell with the vocabulary?), missing sentences, wrong speakers (`Speaker 2` or `(no name)` means Skribby had no name), and anything one side invented. Word counts far apart usually mean one side dropped speech.
- **Our side has no times** (the app's transcript is plain text), which is why the alignment is by matching words, not by the clock. The times shown are Skribby's. Skribby's page does not say whether they are seconds or milliseconds; the tool reads them as seconds unless they are far larger than the recording.

## Cost numbers (estimates, not measured)

| Path | $ an hour | Built from |
|---|---|---|
| Today: Skribby bot and names (groq) + our OpenAI transcription | 1.09 | 0.35 bot + 0.04 groq (SOP 1.4) + 0.70 ours (handoff). The other ~$0.08 (notes, extraction) is the same on both paths and left out. |
| Switch: Skribby bot and its own transcript (Soniox) | 0.45 | 0.35 bot + 0.10 Soniox (SOP 1.4) |

Difference about $0.64 an hour. The constants are at the top of `scripts/transcript_compare.py` if the prices change.

## What's involved

| File | What |
|---|---|
| `scripts/transcript_compare.py` | the tool: fetch (reuses `scripts/skribby_live_check.py` for keys, key hiding and the Skribby client; `app.skribby` for the download with its safety checks), our transcription (`app.audio.prepare_recording`, `app.transcribe.transcribe_chunks`, the `TRANSCRIPTION_MODEL` setting), alignment, HTML and text output |
| `scripts/skribby_live_check.py` | one line: `OPENAI_API_KEY` added to `KEY_NAMES` |
| `tests/test_transcript_compare.py` | 26 tests (a few parametrised), a fake Skribby that has only `get_bot` and `download` (so a delete would fail them), a fake transcriber, no network |
| `docs/checklist/master-checklist.md` | two lines under B2 |

Decisions: the recording link is never saved or printed (the saved `skribby.json` keeps id, status, stop reason, model and transcript only); the HTML escapes every name and word (a participant chooses their own name) and loads nothing from outside; output goes to `~/transcript-compare/`, outside the repo, so a real meeting's text cannot be committed by accident. Rejected: lining up by the clock (our transcript has none; chunk times are not kept) and a full word-by-word diff (slow on an hour of speech and harder to read than the rows).

## Verification

- `ruff check .` and `ruff format --check .` clean; `tests/test_transcript_compare.py` and `tests/test_skribby_live_check.py` pass (38 tests).
- A real run of the offline mode on a generated 70-second audio file with a made-up Skribby transcript, with the real `ffprobe`, in the venv Adebayo uses (`.claude/worktrees/skribby-check/.venv`): correct length, 3 of 4 rows matched, one-word turn placed by position.
- **Not verified:** a real Skribby answer (the field names `start`, `end`, `speaker_name`, `transcript` come from Skribby's Get Bot page; their time unit is not stated there), a real OpenAI call, a real meeting, and the page was not looked at in a browser by the builder. The first live run is the real check; if the times look wrong, say so and the unit guess is a one-line change.

## Operate / roll back

Nothing runs by itself and nothing changes in the app: to remove it, delete the three files and the one line in the live check. Output folders under `~/transcript-compare/` hold meeting audio and text: delete them when done.

## Follow-ups

| Gap | What it needs |
|---|---|
| The decision | Adebayo reads a few meetings. If Skribby's transcript wins, the transcript-source switch is a pipeline change (skip audio and our transcription, move the hours metering and `ai_runs` rows with it); see the last row of section 8 in `docs/sop/2026-11-08-skribby-provider.md`. |
| The app deletes the copy before you can compare | Optional later: a per-meeting "keep Skribby's copy" flag, only if comparing becomes routine. |
| Skribby's own page for the time unit | Check on the first real answer. |
