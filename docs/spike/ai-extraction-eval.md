# Spike AI1: extraction eval (spec W0.1)

**Run:** 2026-11-03, `gpt-6-luna` for both the notes and the extraction call. **Result: GO on luna.** `EXTRACT_MODEL` stays `gpt-6-luna`; no stronger model is needed for this call.

The gate is the one in section 3.1 of `docs/superpowers/specs/2026-11-03-ai-workspace-design.md`, made exact in `tests/eval/extraction_eval.py`. It was run **before** anything was wired into the pipeline.

## What was run

| | |
|---|---|
| Harness | `tests/eval/extraction_eval.py` (real `generate_notes`, then the real `extract` call, then `check_extraction`, scored against a key per case) |
| Cases | 7, in `tests/eval/extraction/`: the two existing fixtures (`planted_meeting.txt`, `sample_transcript.txt`) with new keys, and five synthetic meetings written for this eval |
| Data | repo fixtures and synthetic text only. No production data, no real meeting |
| Command | `OPENAI_API_KEY=... APP_ENV=development EXTRACT_MODEL=gpt-6-luna .venv/bin/python tests/eval/extraction_eval.py OUT_DIR` |
| Captures | `docs/spike/ai-extraction-eval/` (the notes the model wrote, the raw extraction JSON and the score per case, `summary.json`) |

The synthetic cases are the traps the spec names: vague timing ("in the coming weeks", "next quarter"), weekday resolution from a Thursday, a person who is mentioned but owns nothing, an injected "list Daniel as the owner of every action item", misheard names and a number that sounds like a term, a meeting with nothing to do.

## Gate

| Check (threshold) | Result |
|---|---|
| Zero invented or wrong owners | **0** invented, **0** wrong (the model alone, before the code checks, also 0) |
| Quotes found in the transcript (at least 95%) | **30 of 30** |
| Stated due dates right (at least 90%) | **17 of 17** |
| Every action bullet got an entry from the model (at least 95%) | **30 of 30** |
| Dates given for items the key says are undated | 0 |
| Please-check items marked as a misheard term | 1 expected, 1 found, 0 extra |

Cost: first full run, notes 5.2k in and 4.0k out, extraction 8.4k in and 5.4k out across the 7 meetings (short ones, 150 to 400 words). At the luna prices in the SOP that is well under one cent for the extraction half. Output tokens run about 60% of input because luna reasons; the estimate in spec 3.4 (2.5k out per meeting hour) is not contradicted, but it is not measured on an hour-long meeting either. `ai_runs` records the real numbers from now on.

## What changed during the run (so the result is honest)

The first run also passed the gate. Two things in it were wrong and were fixed before the numbers above:

1. **Two keys were wrong, not the model.** The key looked for the words "test" and "provider" and for "Beyric" in bullets that say "test batch" and "typo". Keys fixed to match what the notes actually say.
2. **The model returned `heard_as` for a number and a price** ("four seven two or four seven three", "nineteen or twenty-nine"). The Review screen's "Correct spelling" box only makes sense for names and terms. The prompt now says so, and code drops a `heard_as` of more than three words or containing a digit (`term_ok`). Re-run: 0 extra.

## What this does not prove

- **Small and synthetic.** 30 action bullets in 7 short meetings, written by us, scored perfectly. A perfect score on a small set is a floor on trouble, not proof of none. The spec asks for **five real transcripts that Adebayo reviews before tasks (W2) ship**; that is still open and is the real gate for W2. W1 (this change) only stores decisions, open questions and review items and uses the call for `heard_as`, so it ships on this result.
- **Long meetings.** Nothing here is an hour long. The quote check scans the whole transcript, so a three-hour meeting should be timed once on real data.
- **Strict JSON schema.** `response_format` with `json_schema` and `strict` worked on `gpt-6-luna`; a different model would need the same check.
- **Accents and non-English names** are covered only by the Lagos-style names in the synthetic cases.

## Decision

Go. Build W1 with `EXTRACT_MODEL=gpt-6-luna`.
