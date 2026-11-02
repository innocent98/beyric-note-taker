# SOP: Notes model switched to gpt-6-luna

## What shipped
`NOTES_MODEL` default changed from `gpt-5.4-mini` to `gpt-6-luna`, the model that labels speakers and writes the notes. The live server was switched first by editing its `.env` (2026-11-02). Branch `chore/notes-model-luna`.

## Why
Adebayo asked whether the cheaper `gpt-6-luna` could replace `gpt-5.4-mini`. Prices from the OpenAI pricing page (2026-11-02), per million tokens:

| Model | Input | Output |
|---|---|---|
| `gpt-5.4-mini` | $0.75 | $4.50 |
| `gpt-6-luna` | $0.10 | $0.50 |

For an hour-long meeting (estimated ~45k input and ~17k output tokens across labelling and notes), that is roughly $0.11 versus $0.02, so the total drops from about $0.65 to about $0.56 per meeting hour. Estimate, not measured on a real hour.

## How it was judged
`tests/eval/compare_notes_models.py` ran both models on:

1. `tests/eval/planted_meeting.txt`, a made-up meeting with an answer key (`planted_meeting_key.md`): an undecided provider choice, an unowned VAT task, a misspoken quantity, a mishearing.
2. Speaker labelling on a real Zoom call from the spike (kept out of git).

| | `gpt-5.4-mini` | `gpt-6-luna` |
|---|---|---|
| Traps (provider, VAT owner, 1,500 packs) | All correct | All correct |
| Missed facts | Grace's travel | Amaka "books" the venue became "looks at" |
| "Please check" | Restated clear facts | "None noted", as instructed |
| Speaker labels on the real call | Changed 3.1% of words, so the drift check discarded them | Labelled, twice in two runs |
| Time | 6 s notes, 2 s labels | 13 s notes, 7–10 s labels (it reasons) |

Decision: switch. Accuracy matched, instructions were followed better, and labelling worked where mini's failed, at about a ninth of the cost. The slower calls run after the meeting, so nobody waits on them.

## What's involved
`app/config.py` (`notes_model`), `.env.example`, `tests/test_notes.py` (default assertion), `tests/eval/` (script, planted meeting, key), `docs/sop/2026-11-02-speakers-and-notes.md`.

## Verification
`tests/test_notes.py` passes, ruff clean. The server runs `gpt-6-luna` (checked in the worker's settings) and `/health` is 200.

## Operate / roll back
On the server, set `NOTES_MODEL=gpt-5.4-mini` in `/opt/beyric-note-taker/.env`, then `docker compose up -d --force-recreate web worker`. No redeploy needed. To compare again, run the eval script with each model.

## Follow-ups
- Two short inputs are not an hour-long meeting. Judge luna on the Task 8 acceptance call and keep mini as the fallback.
- Measure real spend per meeting hour on the OpenAI usage page.
