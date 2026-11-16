# SOP: drop the 70% coverage rule from the Skribby-to-own transcript fallback

**What shipped.** `app/transcript_source.py` no longer sends a meeting to our own transcription because
Skribby's last segment ends early in the recording. `MIN_COVERAGE` (0.70) and its check are gone.
`fallback_reason` keeps the other reasons as they were. PR: branch `fix/drop-transcript-coverage-rule`.
**No migration, no `.env` change, no server step.** It takes effect when the code is deployed.

## Why

Adebayo decided on 2026-11-16: he pays Skribby in full and will not also pay OpenAI (about 0.70 USD an hour
of audio) for a second transcript when Skribby's text worked.

Evidence, meeting 38 on 2026-11-16: the recording ran 89:23, but speech ended at 56:42, which is 63% of the
recording, under the 70% line. Our bot had stayed 33 minutes in a call where only another bot was left. So
the fallback ran, `meetings.transcript_source` read `own`, and we paid for a second transcript although
Skribby's text and its speaker names were good. The rule treated "the speech ended early" as "the transcript
lost its end"; the first is normal (a bot waiting out its last-person timer, people leaving), and the
second is rare.

## How, and what was decided

| Piece | Decision |
|---|---|
| Removed | `MIN_COVERAGE`, the `last_end < MIN_COVERAGE * seconds` check and its reason `"stops well before the end of the recording"`, and the comments and docs that described a 70% rule |
| Kept, unchanged | no transcript at all; fewer words than `max(5, ceil(20 x seconds / 60))`; a loop (distinct words under 5% of all words); no segment times |
| Why "no segment times" stays | Without it a transcript with no times at all would be trusted blind; it fires only when Skribby gave nothing usable. So `Candidate.last_end`, `BotMeeting.last_segment_end` and `skribby._last_segment_end` stay: they now only answer "were there any times", and the docstrings say so |
| Not touched | `TRANSCRIPT_SHADOW_PERCENT` and the sampled shadow check; the pipeline caller (`app/pipeline.py`, `transcript_source.fallback_reason(found, recording.seconds, MIN_WORDS)`, same signature); the rebuild script reads the same function, so it follows |
| Alternative rejected | Lowering the line (for example to 50%) or measuring coverage against the end of speech instead of the recording: both still guess, and Adebayo's call was to remove the rule |

**Accepted risk.** A Skribby transcript that really lost its tail (the provider cut the text off while the
call went on) is no longer caught by the fallback; the meeting's notes then cover only the part Skribby
returned. Nothing else catches it today. The sampled shadow check can reveal it in numbers only if
`TRANSCRIPT_SHADOW_PERCENT` is above 0 (it is 0 on the server since the flip).

## What's involved

| File | Change |
|---|---|
| `app/transcript_source.py` | constant, check, reason and comments removed; module and function docstrings say the reach is not checked |
| `tests/test_transcript_source.py` | the parametrised "stops_early" fallback case and the 70%-edge test replaced by tests that speech ending at 60% to 63% is accepted, and that the other reasons still fire; a pipeline test shows source `skribby`, no OpenAI transcription, no `transcribe` ledger row |
| `tests/test_rebuild_meeting_from_evidence.py` | the refusal for an early-ending text is now a test that it is not refused for that |
| `docs/sop/2026-11-09-transcript-source-switch.md`, `docs/sop/2026-11-16-rebuild-meeting-from-evidence.md`, `docs/superpowers/specs/2026-11-15-billing-and-plans-design.md`, `docs/checklist/master-checklist.md` | no longer describe a 70% rule |

Checked and needing no change: `.env.example`, `deploy/README.md` (they name only the missing and under-20-words
cases), `scripts/skribby_segments_report.py` (does not read coverage), `app/pipeline.py` (calls
`fallback_reason`; the log line prints whichever reason fires).

## Verification

- `ruff check .` and `ruff format --check .` (the exact commands in `.github/workflows/ci.yml`; the project
  has no type-check step) and the three touched test files only. CI runs the rest.
- Tests: speech ending at 63%, 60% (exactly), 10% and 100% of a recording gives no fallback reason; the
  pipeline test with speech ending at 60% of 1830 s stores `transcript_source = skribby` with zero OpenAI
  transcriptions; no transcript, too few words, a loop and no segment times still fall back.
- Not proven: nothing ran against a real Skribby meeting. The next meeting whose speech ends early should
  read `skribby` in `meetings.transcript_source`.

## Operate / roll back

Deploy as any code change. Roll back: revert the PR (the rule returns, constant 0.70).

## Follow-ups

- If a real Skribby tail loss is ever seen, a cheaper guard than coverage may be needed (for example a
  words-a-minute check on the last stretch); not built, not decided.
