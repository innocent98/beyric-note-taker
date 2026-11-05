# Notes and speaker-labelling calls in the AI cost ledger

## What shipped
The `ai_runs` ledger now records the two model calls every meeting makes on its way to `ready`: the speaker labelling (`kind = 'label'`) and the notes (`kind = 'notes'`), plus the notes call of "Rewrite notes". Migration **0023** widens `ck_ai_runs_kind`. PR: `feature/notes-cost-tracking`.

## Why
Adebayo asked what the AI costs. The ledger held only extraction and translation calls (3 rows, $0.0089 at $0.10 / $0.50 per million tokens), so it missed the biggest call per meeting, the notes. He chose to track it (2026-11-05).

## How
- `notes._complete(..., usage=list)` appends `(prompt_tokens, completion_tokens)` as soon as a reply comes back, also when the reply is then refused (cut off, filtered, empty): those tokens were paid for. A reply with no `usage` appends `(None, None)`.
- `label_transcript` and `generate_notes` pass `usage` through. Labelling a long transcript makes one call per section; they are summed into **one row per meeting step**.
- `ai_runs.record_usage(session, meeting_id, kind, model, usage, user_id=)` writes that row (nothing when no call was made). `prompt_version` is `NOTES_PROMPT_VERSION` / `LABEL_PROMPT_VERSION`; `model` is `settings.notes_model`; `user_id` is the meeting owner.
- The translation daily limit counts only `kind = 'translate'` (`app/translation.py`), so the new rows never use up anyone's translations.
- Rejected: one row per labelling section (noisy, no extra insight); recording failed steps (the step's transaction rolls back with the failure; see Follow-ups).

## What's involved
| File | Change |
|---|---|
| `app/notes.py` | `usage` on `_complete`, `label_transcript`, `generate_notes` |
| `app/ai_runs.py` | `record_usage`; prompt versions for `notes` and `label` |
| `app/models.py` | `AI_RUN_KINDS` gains `notes`, `label` |
| `app/pipeline.py` | `_step_speakers` and `_step_notes` record their calls |
| `app/services.py` | `regenerate_notes` records its call |
| `alembic/versions/0023_ai_runs_notes_kinds.py` | check constraint widened; downgrade deletes the new kinds' rows first |
| `tests/test_ai_runs_usage.py`, `tests/test_pipeline.py`, `tests/test_api.py`, `tests/test_migrations.py` | ledger rows, summing, refused reply still counted, migration round trip |

## Verification
ruff clean; `tests/test_ai_runs_usage.py`, `test_pipeline.py`, `test_notes.py`, `test_api.py` (256 passed), `test_migrations.py` (36), `test_accounts.py`. Not run live: the next real meeting after deploy is the first live row.

## Operate / roll back
After deploy, the cost per kind (prices per million tokens; change them to the provider's):
```bash
ssh haven-respite 'cd /opt/beyric-note-taker && docker compose exec -T db sh -c "psql -U \$POSTGRES_USER -d \$POSTGRES_DB"' <<< "select kind, model, count(*) as calls, sum(input_tokens) as tokens_in, sum(output_tokens) as tokens_out, round(sum(coalesce(input_tokens,0))*0.10/1e6 + sum(coalesce(output_tokens,0))*0.50/1e6, 4) as usd from ai_runs group by kind, model order by usd desc;"
```
Roll back: revert the PR; `alembic downgrade 0021` deletes the `notes` and `label` rows and restores the narrow check.

## Follow-ups
- Transcription cost (`app/transcribe.py`, priced per audio minute): record minutes per meeting.
- A failed notes or labelling step's tokens are not recorded (its transaction rolls back); record them in a separate commit if the numbers matter.
- `scripts/extraction_review.py` calls the model without the ledger (a few cents per review).
