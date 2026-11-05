# SOP: extraction output cap (long meetings no longer fail with `length`)

## What shipped
The extraction call (`app.extraction.extract`, both the W1 review call and the full `extract-v1` call W2 will use) now sizes its output budget to the number of bullets, retries **once** with more room when the reply is cut off, and fails with a named reason when it is cut off again. Branch `fix/extraction-output-cap`, PR to `main` (number in the PR list). No migration. One optional setting, `EXTRACT_MAX_OUTPUT_TOKENS` (default 32000).

## Why
Adebayo's first real run of the full extraction (meeting 5, 2026-11-04) ended `finish_reason=length` at the fixed `MAX_OUTPUT_TOKENS = 2000`. That budget pays for the model's **hidden reasoning as well as the visible JSON**, and both grow with the meeting: the schema asks 7 fields per action bullet (about 70 to 85 visible tokens each: `title` repeats the bullet, `quote` copies up to 300 characters) and the eval measured about 150 tokens per bullet with reasoning, so 2,000 runs out at about 13 bullets. The seven synthetic eval meetings had 4 to 5 bullets, which is why it never showed. The W1 review call (only Please check items) was always small and unaffected.

## How
| Piece | Decision |
|---|---|
| First cap | `output_budget(items)` = 1,500 + 200 per bullet asked about (action bullets for the full call, plus Please check bullets), clamped to 4,000 to 16,000. A ceiling, not a spend: the model stops when done |
| On `length` | One retry at four times the cap, at least 8,000, at most `EXTRACT_MAX_OUTPUT_TOKENS` (`next_budget`). The cut-off reply is never parsed or kept. Both calls' tokens are added up so `ai_runs` shows what was really spent |
| Still cut off | `ExtractionTruncated` (a subclass of `ExtractionFailed`) with "the answer was cut off by the output limit (length, tried 4,000, 16,000 tokens)". The ledger keeps class names only, so `ai_runs.error = 'ExtractionTruncated'` is the reason, queryable. The worker then follows its usual rule (3 passes, then `failed`); lists already written stay. No retry when the first cap is already at the ceiling |
| Time limit | Grows with the cap: 1 second per 100 tokens, 60 to 180 seconds (`_timeout_for`). A bigger budget lets the model think longer, so a flat 60 seconds would turn this fix into a timeout. Worst case a single call blocks the worker loop about 6 minutes (180 + 180); only for a pathological meeting |
| Schema | **Unchanged** (no prompt version bump, no eval re-run needed). The only repeated text is `title` (about 12 tokens an item) and the 300-character quote the spec asks for; reasoning, not JSON, is what overflows, so trimming the schema would not have fixed it. Dropping `title` and shorter quotes remain a later saving if cost matters |
| Rejected: chunking by bullet groups | An hour is about 14,000 input tokens, far inside the context; splitting would separate an owner's promise from its quote and multiply input cost. Revisit only for multi-hour meetings, if the 32,000 ceiling is ever hit |
| Review script | `scripts/extraction_review.py` now just calls `extract` (its own copy of the retry is gone, so the page shows exactly what the product does). `--max-output-tokens N` still sets the first cap; the default is the sized one. Several `--meeting` ids already produce one HTML page |

### Cost per meeting hour (against spec 3.4: about $0.003, $0.008 at 5x output)
`gpt-6-luna`, $0.10 in and $0.50 out per million tokens. An hour is about 14,000 in ($0.0014). Typical output 4,000 to 5,000 tokens ($0.0025): **about $0.004**. Retry case: 6,500 cap cut off, then 26,000: input twice, output at most 32,500 tokens, **at most about $0.02**. Estimates, not measured on an hour-long real meeting; `ai_runs` (`select kind, status, error, sum(input_tokens), sum(output_tokens) from ai_runs group by 1,2,3`) gives the real numbers.

## What's involved
- `app/extraction.py`: `output_budget`, `next_budget`, `_timeout_for`, the loop in `extract`, `ExtractionTruncated`, `ExtractionCall.budgets`.
- `app/config.py`: `extract_max_output_tokens`.
- `scripts/extraction_review.py`: retry removed, spy only records.
- `tests/eval/planted_meeting.txt`, `planted_meeting_key.md`, `tests/eval/extraction/planted.json`: the spec's gate extension: an explicit date (deposit by 19 February), a vague timing ("coming weeks", no date), a mentioned-not-owner ("Daniel", forbidden), owners and due phrases in a key table.
- Tests: `tests/test_extraction_call.py` (sizing, one retry, second cut-off, ceiling, a 25-bullet meeting that needs more than the old 2,000), `tests/test_workspace.py` (cut-off recovered; three cut-offs end `failed` with `ExtractionTruncated`, nothing from the partial reply stored), `tests/test_extraction_review.py` (updated for the sized cap). All with a mocked model.
- **Not re-run live:** the eval on the extended planted case (it needs the key; run `tests/eval/extraction_eval.py` as in `docs/spike/ai-extraction-eval.md`).

## How Adebayo runs the five-transcript review (after deploy)
1. List the newest meetings and pick the five real, long ones by date and transcript length:
```bash
ssh haven-respite 'cd /opt/beyric-note-taker && docker compose exec -T web python scripts/extraction_review.py --list 40 </dev/null'
```
2. One page for all five (replace the ids; it makes one model call per meeting, so a few seconds to a few minutes each):
```bash
ssh haven-respite 'cd /opt/beyric-note-taker && docker compose exec -T web python scripts/extraction_review.py --meeting A --meeting B --meeting C --meeting D --meeting E --price-in 0.10 --price-out 0.50 </dev/null' > review.html
```
3. `open review.html`, mark each item, then **Copy my verdicts** and paste into chat. The gate: zero invented owners, quotes verified at least 95%, due dates right on at least 90% of stated ones. The page shows the token counts and cost per meeting; the retry (if any) is stated on it.

## Verification
Tests first, watched failing, then green (`test_extraction_call`, `test_workspace`, `test_extraction_review`, `test_extraction_eval`, `test_accounts`, the OpenAPI tests, `test_dockerfile_scripts`); `ruff check` and `ruff format --check` clean. **Not verified:** a live call on a real long meeting (needs the server key; Adebayo's run above is that proof), and the real reasoning/visible split.

## Operate / roll back
Deploy needs nothing. Optional: `EXTRACT_MAX_OUTPUT_TOKENS` in the server `.env` to lower or raise the retry ceiling (default 32000). Roll back by reverting the PR; no data changed. Watch `ai_runs` for `error = 'ExtractionTruncated'`: any rows mean a meeting still did not fit at the ceiling.

## Follow-ups
- Drop `title` and shorten quotes (about a quarter of the visible JSON) and try a lower reasoning effort, only if the real cost needs it (would bump `EXTRACT_PROMPT_VERSION` and need an eval run).
- When W2 wires the full call into the worker, consider running it off the polling loop: the worst case holds it for minutes.
