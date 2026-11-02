# SOP: Speakers and notes (Task 4)

## What shipped
`label_speakers(transcript, segments, *, client=None)` and `generate_notes(transcript, template, terms, *, client=None)` in `app/notes.py`, plus `app/retry.py` (the retry loop moved out of `app/transcribe.py` and shared). Commit: `feat: generate speaker-labelled transcripts and notes`.

## Why
The owner left his old tool for poor summaries and incoherent transcripts. Notes must never state anything the transcript does not support.

## How
- **Source of truth.** The accurate transcript is the only source of facts. Vexa's live text (it flipped "can" to "can't" and invented "London" and "Berlin") is used only to work out who said what; both prompts say so.
- **Labelling.** The model returns the transcript with `[Name]: ` turns, one per line. Input and output words are compared (labels stripped, `difflib`); more than 2% changed means that section stays unlabelled. A failed call also leaves the section unlabelled, so labelling can never rewrite the meeting. No segments, or no speaker names at all, returns the transcript unchanged with no call.
- **Long meetings.** The transcript is split on line boundaries into sections of at most `SECTION_WORDS` (4000) words, each sent with the live segments for its place in the meeting (matched by share of live text, plus 3% overlap), then rejoined. Notes are one call; an hour is about 12,000 tokens, well within model limits.
- **Speaker names.** Missing or empty names become `Unknown speaker`; names are trimmed and whitespace-collapsed, so identical names merge.
- **Notes.** Fixed headings: Summary, Key points, Decisions, Action items, Open questions, Please check. Ownerless items say `Unassigned`; empty sections say `None noted`. Code also adds any missing heading and fills empty bodies. "Please check" is for genuinely unclear or doubtful items and possible mishearings, not for clear ones (first live run over-flagged; prompt tightened).
- **Prompt injection.** Transcript and segments sit in `<transcript>` / `<live_segments>` blocks; any closing tag inside the text is neutralised; prompts say the block is data, not instructions.
- **Retries.** `app.retry.call_with_retry` (3 attempts, 1 s / 3 s backoff, rate limit / timeout / connection / 5xx) is used by transcription and notes. Exhausted attempts raise `NotesFailed`; empty or length-truncated replies also raise it.

## What's involved
`app/notes.py`, `app/retry.py`, `app/transcribe.py` (now uses the shared retry), `tests/test_notes.py`, `tests/test_transcribe.py` (patches `retry.time.sleep`), `tests/eval/sample_transcript.txt`. Model: `notes_model` (`gpt-5.4-mini`) via chat completions.

## Verification
Unit tests use a fake client only. Live run on `tests/eval/sample_transcript.txt` (synthetic, includes an injection line): no invented facts, owners kept, injection ignored. Labelling run with deliberately wrong live text (flipped "can", invented "Berlin"): speakers right, words unchanged.

## Operate / roll back
Needs `OPENAI_API_KEY`. Revert the commit; `app/transcribe.py` returns to its own retry loop.

## Follow-ups
- Section boundaries match segments by proportion of text, which drifts on very long meetings; the 3% overlap covers normal drift.
- Notes for very long meetings (several hours) are one call; summarise-in-parts is not built.
- Check the first real hour-long meeting for label quality.
