# SOP: the cost report script (`scripts/ai_cost_report.py`)

**What shipped.** A read-only script that prints what a meeting hour really costs: hours from the database, AI spend from `ai_runs`, and a labelled estimate for the vendors that bill outside the database. PR: branch `chore/ai-cost-report` (see the PR for its number). No migration, no `.env` change.

## Why

Pricing is being designed on a stale estimate ($0.56 to $0.85 an hour). The real number needs the live ledger, and two facts were not in the repo: `ai_runs` has **no cost column** (only `input_tokens`, `output_tokens`, `audio_seconds`), and ElevenLabs' price was never written down.

## How

| Piece | What and why |
|---|---|
| Hours | `meetings.recorded_seconds` (the prepared recording's length, the hours-cap unit), meetings created in the window. `usage_entries.seconds` printed beside it as a cross-check (it also counts meetings deleted later). |
| AI spend | Each `ai_runs` row priced from tokens or audio x the constants at the top of the file, split by kind and model. Failed rows count (the tokens were paid). A model with no price is listed as UNPRICED and left out, never guessed; the total is then marked a floor. |
| Estimate block | Skribby bot time and its transcription model line, from constants with the source and date in a comment each. |
| Total per hour | A range: low and high of the AI audio price (0.0045 to 0.006 a minute) and of the billed-bot-hours factor (1.0 to 1.25). Also shown for Skribby-bot meetings only, because Vexa-era hours cost no vendor money and would dilute the average. |
| Counts only | "user 1, user 2" ranked by hours, and team-size buckets (1, 2-3, 4-9, 10 or more). No names, emails, titles or transcripts are selected at all: the queries pick ids and numbers only. |
| Long recordings | Count and hours over 3 h, and the per-hour figure without them (a runaway bot would distort the average). |
| Read-only | The transaction starts with `SET TRANSACTION READ ONLY` (Postgres refuses any write) and only SELECTs run; a test proves an insert fails. |

**Prices and where each comes from** (all in constants at the top of the script):

| Constant | Value | Source |
|---|---|---|
| ElevenLabs `scribe-v2` | $0.20 an hour | ElevenLabs' own page `elevenlabs.io/pricing/api`, "Price per hour", fetched 2026-11-14 (the page opened). Keyterm prompting is a separate $0.05 an hour, not used. Skribby's billing page lists "ElevenLabs v2" at the same $0.20. |
| Skribby bot | $0.35 an hour, billed per minute | `skribby.io/docs/guides/billing`, fetched 2026-11-14 |
| Skribby old model (Soniox v5) | $0.10 an hour | `docs/sop/2026-11-08-skribby-provider.md` |
| `gpt-6-luna` | $0.10 in, $0.50 out per million tokens | OpenAI page as read in `docs/sop/2026-11-02-notes-model-gpt-6-luna.md` |
| `gpt-transcribe` | $0.0045 a minute (low), $0.006 (high) | secondary sources, not confirmed on OpenAI's page (`docs/sop/2026-11-05-notes-cost-ledger.md`); high is `gpt-4o-transcribe` as read 2026-11-10 |
| Billed-bot-hours factor | 1.0 to 1.25 | **a judgement with no source**; replace with the real Skribby invoice |

**One thing to know when reading it.** We reach ElevenLabs only through Skribby, so ElevenLabs' $0.20 is the model line of Skribby's bill, not a second charge. The report prints it that way. Meetings created from 2026-11-13 17:31 UTC (the 18:31 WAT flip) use it; earlier Skribby meetings use the $0.10 model. With `TRANSCRIPT_SOURCE=own` our own OpenAI transcription runs as well, and that one is in `ai_runs`.

## What's involved

| File | Change |
|---|---|
| `scripts/ai_cost_report.py` | the report |
| `tests/test_ai_cost_report.py` | arithmetic on a small fixture, plus a scratch-database run |
| `docs/checklist/master-checklist.md` | one line under "Measure real OpenAI spend per meeting hour" |

## Verification

Locally, bounded: `ruff check`, `ruff format --check`, `tests/test_ai_cost_report.py` (20 tests, scratch Postgres removed afterwards): pricing of token, audio, unknown-model and empty rows; split by kind and model with failed and unpriced counted; the bot estimate split at the flip with Vexa hours ignored; an unknown ElevenLabs price flagged and the total a floor; the range arithmetic; no hours gives n/a; exactly 3 h not long and 3 h 1 s long; without-long; user and team-size grouping; the window; and an end-to-end render that holds none of a planted title, transcript, name, email or team name; an insert after `open_read_only` fails. Also run once against an empty scratch database. **Not run against the real database**: the first real run is the server run below.

## Operate

```bash
ssh haven-respite 'cd /opt/beyric-note-taker && docker compose exec -T web python scripts/ai_cost_report.py </dev/null'
```

`--days N` changes the window (default 30). It changes nothing and prints no private text. For the number that matters now, use a window that starts after the ElevenLabs flip (`--days 1` on 2026-11-15), because older meetings were priced with the earlier model. Roll back: delete the script; nothing else depends on it.

## Follow-ups

- Replace the 1.25 billed-bot-hours factor with real Skribby invoice hours.
- Confirm `gpt-transcribe` on OpenAI's own pricing page (still secondary sources).
- `ai_runs` has no row for Skribby, ElevenLabs or the bot, so those stay estimates until a vendor invoice is compared.
- `ai_runs` rows are counted by their own date and meetings by theirs: a small mismatch at the edges of a short window.
