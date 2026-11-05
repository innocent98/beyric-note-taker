# SOP: extraction review tool (the W2 gate on a real meeting)

> Update 2026-11-05: the product's call now sizes its budget and retries on `length` itself (`docs/sop/2026-11-05-extraction-output-cap.md`), and this tool just calls it. The recommendations in "Finding" below that are done: 1 (size the budget), 4 (time limit grows with it), 6 (a failure is named). `--max-output-tokens` now defaults to the sized budget, not 2,000.

## What shipped
(Update 2026-11-04, same day: the first real run, meeting 5, failed with a length cut-off. See "Finding: the full call ran out of output budget" below; the tool now explains a failure, retries once, has `--max-output-tokens`, `--timeout` and `--list`.)

`scripts/extraction_review.py`: a read-only operator script that runs the **full** extraction (`extract(..., full=True)`, prompt `extract-v1`, exactly what W2 Tasks will run) on a real meeting and writes one self-contained HTML page to stdout, so Adebayo can judge it with Right / Wrong / Partly buttons. Branch `feature/extraction-review-tool`, PR to `main` (number in the PR list). No migration, no new setting, no server step.

## Why
The AI1 eval of the full call (`docs/spike/ai-extraction-eval.md`) used 7 synthetic meetings. The failure to avoid in W2 is a **wrong owner**, and only a person who was in the meeting can tell. Reading raw JSON beside a transcript is slow, so the tool puts each extracted item next to the words it relies on.

## How
| Piece | Decision |
|---|---|
| Same call as W2 | Imports `app.extraction.extract` and `check_extraction`; no copy of the prompt (a test asserts the system prompt sent is `extraction.EXTRACT_SYSTEM_FULL`). Date, timezone (`UTC`) and the transcript choice (`speaker_transcript`, else `transcript`) follow `workspace.run_extraction` |
| Read-only | Every database read runs in a session that starts with `SET TRANSACTION READ ONLY` (Postgres refuses any write) and is rolled back, never committed. The session is closed **before** the model is asked, so no connection is held across the call. The answer is not saved, not even in `ai_runs` (the page shows the token counts instead). Proof: a test compares row counts and an md5 of every row of every table before and after search, a normal run, a run with a failed call and a two-meeting run; two more tests show the session rejects an `UPDATE` and a failed `commit` |
| What the page shows | Left: the transcript, each line with its **line number** and speaker. Stored transcripts carry **no timestamps** (the speaker labeller keeps text only), so the number stands in for them and the page says so. Right: each **action item** as the model returned it (title, owner exactly as named, due date and phrase, quote, confidence) beside the notes bullet it was made from; **decisions** (read from the notes by code, no model); **misheard terms** (`heard_as`); **milestones**; a **Missing** box. There is no people list because the `extract-v1` schema has none: the owner on each action item is the person data |
| Checks shown, not hidden | The app's own checks (`check_extraction`) run on the answer and any item they would downgrade is flagged in orange: owner not in the transcript, quote not in the transcript, due date rejected, title too far from the bullet, no entry from the model for a bullet. He sees what the model said and what the app would keep |
| Clicking an item | If the item has a quote (or `heard_as`) that is found in the transcript, clicking the card scrolls to that line and highlights it. Decisions have no quote, so they do not scroll |
| Verdicts | Three buttons per item and an optional note. A sticky footer counts Right / Wrong / Partly / Not judged. **Copy my verdicts** builds a plain-text block (meeting id and title, each item, verdict, note, the Missing text) to paste into chat; if the browser refuses the clipboard, the text appears in a box to copy by hand. Saved in `localStorage` (try/catch, falls back to memory), keyed by meeting, item and a hash of the item text, so a re-run with different output never attaches an old verdict to a new item |
| Safe page | Everything from the transcript, notes and model goes through `html.escape` (attributes too). No `src`, no external `href`, no `@import`, no `url(`, system fonts. A `Content-Security-Policy` meta (`default-src 'none'`, a per-run nonce for the one inline script) blocks any request, and would block an injected script even if escaping failed. No inline `on...` handlers (click handling is delegated). Tested with `<script>`, `</textarea>`, `</style>` and quotes in the transcript, the notes, the title and every model field |
| Failures | No such meeting, no transcript (no model call is made, nothing is spent), no notes (a warning: action items come from the notes), a failed or unfinished model call (a clear red banner with the error class name only, plus the transcript still on the page, token counts if reported), an unexpected exception (class name only, never the text, a path or the key). Exit code 1 if any meeting failed, so a shell sees it; the page is still written. One failed meeting does not stop the others |
| Long transcripts | The pipeline **never shortens a transcript** for extraction, so neither does this: it is sent whole, and the page says "sent whole, the pipeline does not shorten transcripts". Above 200,000 characters (about 50,000 tokens) a warning says the 60 second call limit may bite, which is what Tasks would meet too |
| Cost | The app has no price table, so only token counts are shown. `--price-in` and `--price-out` (dollars per million tokens) add an estimate. For `gpt-6-luna` (prices in `2026-11-02-notes-model-gpt-6-luna.md`) use `--price-in 0.10 --price-out 0.50` |
| Rejected | A web route in the app (it would need auth, a template and a deploy for a one-off review); writing the result to `ai_runs` or a table (breaks read-only); truncating the transcript (the product does not) |

## What's involved
- New: `scripts/extraction_review.py`, `tests/test_extraction_review.py` (41 tests, real Postgres, the model faked with the same `FakeChat` the extraction tests use).
- Reused, not changed: `app/extraction.py`, `app/workspace.py` (`DEFAULT_TIMEZONE`, the ILIKE escaping helper), `app/services.py` (`meeting_title`), `app/db.py` (`SessionLocal`), `app/ai_versions.py`.
- The Dockerfile already copies `scripts/` (PR #57, checked by `tests/test_dockerfile_scripts.py`, which also checks the commands in this SOP point at real files).
- Checklist: the "W2 gate" item in `docs/checklist/master-checklist.md`.

## How to use it (Adebayo)
Nothing here needs a deploy beyond this PR being merged and deployed once.

1. **Find the meeting.** A meeting pasted by link has **no title**, so a title search cannot find it (`--search myway` found nothing for that reason). List the newest ones instead (`--list 30` for more) and pick by date and transcript length:
```bash
ssh haven-respite 'cd /opt/beyric-note-taker && docker compose exec -T web python scripts/extraction_review.py --list </dev/null'
```
`--search` still works for meetings that have a title or whose code or link you remember. On the server (the `</dev/null` is needed in an ssh one-liner, otherwise ssh swallows stdin):
```bash
ssh haven-respite 'cd /opt/beyric-note-taker && docker compose exec -T web python scripts/extraction_review.py --search myway </dev/null'
```
It prints a table: id, date, transcript length in characters, status, label (title, or "Zoom 123456"), platform and code. `--search` matches the title, the meeting code and the link, ignoring case, newest first. If two rows look right, take the one with a transcript length of a real meeting.
2. **Make the page** (replace `12` with the id; add more `--meeting N` to review several in one page):
```bash
ssh haven-respite 'cd /opt/beyric-note-taker && docker compose exec -T web python scripts/extraction_review.py --meeting 12 </dev/null' > review.html
```
It takes a few seconds to a minute (one model call per meeting). If it ends with an error, `review.html` is still written and says what failed; nothing was changed.
3. **Open the file:** `open review.html`. Click an item to jump to the words it relies on. Mark each item Right, Wrong or Partly, add a note where it helps, and write anything the meeting agreed that the AI missed in the **Missing** box.
4. **Press "Copy my verdicts"** and paste the text into chat. Reloading the page keeps your verdicts (same browser, same file path).

What to look at first: **owner** (did that person really say they would do it?), the **quote** (is that where it was said?), the **due date**, and the orange warnings.

## Privacy
The transcript leaves the server only through the model call the product already makes for every meeting (notes, and the extraction for each new meeting). The tool adds one more such call per meeting reviewed, to the same provider. `review.html` is created on Adebayo's machine by the redirect and never uploaded; the page makes no requests and keeps verdicts only in his browser. Delete the file when done.

## Finding: the full call ran out of output budget (meeting 5, 2026-11-04)
Adebayo's first real run printed `extraction: the model did not finish normally (length)`. `finish_reason = length` means the reply hit `max_completion_tokens`, which `app/extraction.py` sets to **2,000** (`MAX_OUTPUT_TOKENS`, chosen for the review-only call and shared by the full call). It was not a tool bug: the tool made the call the product will make.

**Why it fits in 7 synthetic meetings and not in a real one.** For a reasoning model such as `gpt-6-luna` that budget pays for the **hidden reasoning as well as the visible JSON**. Two things grow with a real meeting and neither is capped:
- *Visible JSON.* Every action bullet gets an entry of seven fields with a quote of up to 300 characters. A realistic entry is about 280 characters, roughly **70 to 85 tokens** (measured by character count, not by the tokenizer). 25 bullets are about 1,750 to 2,100 tokens before any reasoning.
- *Reasoning.* The eval's 30 bullets across 7 short meetings used 4.5k output tokens in all (`docs/spike/ai-extraction-eval.md`), about **150 per bullet including reasoning**, on 150 to 400 word transcripts. Reasoning on an hour of speech will not be smaller. At 150 per bullet the 2,000 cap is gone at about **13 bullets**.

What I could not do: split meeting 5's real usage into reasoning and visible tokens (production is not readable from here, and I did not spend the live key). The page now prints it, so the next run answers it. The behaviour is reproduced in `test_a_one_hour_meeting_cut_off_at_the_apps_cap_is_recovered_by_the_retry` (a long transcript, 25 bullets, a fake that answers `length` at 2,000 and succeeds at 8,000).

**What the tool does now (tool only; the product's cap is unchanged in this PR).**
- Every call is wrapped by a recorder that sets the budget and notes `finish_reason`, prompt, completion and **reasoning** tokens. A failure page shows a table of each call (budget, how it ended, tokens), the transcript length, the plain-words cause and the command to re-run, and still shows the transcript.
- `--max-output-tokens N` sets the first call's budget (default the app's 2,000). If the answer is cut off by `length` (only that, not a refusal or a network error) the tool retries **once** with four times the budget, at least 8,000 and at most 32,000, and says so on the page. The retry is a second paid call; both are in the token totals.
- `--timeout S` (default **180** seconds; the product's call allows 60). A bigger budget lets the model think longer, and a 60 second limit would turn the fix into a timeout.
- `--list [N]` prints the newest N meetings (default 20, at most 200).

**What the product's W2 extraction must do** (recommendation, in order; none of it is built):
1. **Size the budget to the work**, not a constant: for example `1,500 + 200 x action bullets`, clamped to 4,000 to 16,000 (25 bullets: 6,500). It is a ceiling, so a short meeting does not pay more; the model stops when it is done.
2. **Make the answer smaller.** Drop `title` (code already falls back to the bullet when the title strays, and it is about 12 tokens an item) and cap quotes at about 160 characters. Saves roughly a quarter of the visible JSON. Changes the schema, so bump `EXTRACT_PROMPT_VERSION` and re-run the eval.
3. **Try a lower reasoning effort** (`reasoning_effort`) on the call. It is the biggest lever on hidden tokens, but I have not verified that `gpt-6-luna` accepts it; check in the live eval first.
4. **Raise the call's time limit** (60 seconds now) in step with the budget, and keep one attempt in the worker (a cut-off is a deterministic failure for that size, so retrying at the same size is waste; the worker should retry with a larger budget or not at all).
5. **Chunking and continuation: not needed yet.** An hour is about 14,000 input tokens, far inside the context. Revisit only for meetings of several hours; chunking also splits an owner's promise from its quote.
6. **Never silently drop to nothing**: a `length` failure should show as "extraction failed" in the Tasks screen, with the meeting still readable, as W1 does.

**Cost per meeting at the new settings** (`gpt-6-luna`, $0.10 per million input, $0.50 per million output): an hour is about 14,000 input tokens, $0.0014. Output at an 8,000 budget costs at most $0.004. So **about half a cent worst case, about a third of a cent typical** (4,000 to 5,000 output tokens), and about a cent if the tool's retry doubles the call. Estimates from the eval's ratios, not measured on an hour-long meeting; `ai_runs` and the tool's own page give the real numbers once meeting 5 runs.

## Verification
- Tests first (watched failing on the missing module), then green: `tests/test_extraction_review.py` 41 passed (28 at first ship, 13 added with the length fix, each seen failing before the code), plus `test_dockerfile_scripts.py` and `test_backfill_script.py`; `ruff check .` and `ruff format --check .` clean (ruff pinned 0.17.3 in a venv of the worktree). Postgres 17 in container `nt-test-pg-ex` (port 54315).
- The page's JavaScript was driven in a real browser (built-in browser pane, page served from a local static server) on a page built from the fake model's answer, with a hostile second meeting: buttons set and cleared verdicts, counts updated, a click on a quoted item scrolled to and highlighted the right transcript line, **Copy my verdicts** produced the text block (clipboard was refused in that pane, so the fallback box was shown and read), a reload kept the verdicts, notes and Missing text, and the injected `<script>` text appeared as text with exactly one `<script>` element on the page.
- **Not verified:** a run against the real model and a real meeting (needs the server's `OPENAI_API_KEY`; that is the point of handing it to Adebayo); a run through `docker compose exec` on the server (the image copy of `scripts/` is covered by the existing Dockerfile test); the clipboard button on a browser that allows it (only the fallback path was seen); other browsers than the pane's Chromium.

## Operate / roll back
Nothing runs by itself and nothing is stored, so there is nothing to undo: delete `scripts/extraction_review.py` and its test to remove it. Cost is one extraction call per meeting reviewed (about 1,100 input tokens per short meeting; more for a long one, a few cents at most).

## Follow-ups
- Transcripts have no timestamps. If the review shows they would help, W2 could keep the Vexa segment times next to the labelled text.
- The spec asks for five real meetings. This tool takes any number of `--meeting` flags, so the other four can go through it the same way.
- A people list (names mentioned) is not in `extract-v1`. If W2/W3 want one it needs a schema change and a new prompt version.
