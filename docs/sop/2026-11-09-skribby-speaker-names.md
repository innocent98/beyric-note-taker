# SOP: Skribby speaker names from the diarization tag and the candidate list

**What shipped.** `app/skribby.py` now names a Skribby transcript segment from Skribby's own speaker tag and its `potential_speaker_names`, not only from the segment's own `speaker_name`. PR: see the branch `fix/skribby-speaker-names` (no migration, no setting, no endpoint). Tests: `tests/test_skribby_provider.py` ("who spoke" section).

## Why

Adebayo tested Zoom, Teams and Meet through the app on 2026-11-09. Meet named him; Zoom named only one of two speakers; Teams named nobody. A saved Skribby answer from a real Zoom meeting showed the shape (names here are placeholders):

| Segment | `speaker` | `speaker_name` | `potential_speaker_names` |
|---|---|---|---|
| Speaker 1, every segment | `"1"` | present | absent |
| Speaker 2, every segment | `"2"` | **absent** | `[{"name": "<name given to tag 1>", "confidence": 1}, {"name": "<another participant>", "confidence": 0.713}]` |

`_segments` read only `speaker_name`, so tag 2 became `""` and the notes said "Unknown speaker". The same family of problem is the likely cause for Teams (one speaker, no name): **not verified**, the Teams answer was not kept.

## How

`_tag_names` (new) works out one name per tag, then `_segments` uses it only for a segment with no `speaker_name` of its own:

1. **Tag name from its own segments.** A segment's own cleaned `speaker_name` always wins. A segment without one takes the name other segments of the same tag carry, **only if the tag carries exactly one distinct name** (names compared after cleaning, ignoring case). A tag that carries two different names (Skribby contradicting itself) names nothing for its bare segments.
2. **Tag with no name at all: its candidates.** Take the cleaned names in `potential_speaker_names` (all segments of the tag, merged), drop every name another tag already has, and use the name **only if exactly one is left**. Two or more left, or none: unnamed.
3. **Never two tags, one name.** If two unnamed tags would both end up with the same single candidate, neither gets it.
4. **Never a guess on partial data.** A segment whose candidate list is longer than `MAX_CANDIDATES` (20; real ones hold a few) marks the tag unreadable, so it stays unnamed instead of choosing from a cut-off list. Non-list values, entries that are not objects, non-string or empty names and names that clean to nothing are ignored. A tag that is missing, a boolean, a float, a list or empty has no inference at all. An integer tag and its text form are one tag.
5. Every name goes through `_clean_name`, so a hostile candidate name is scrubbed like any other.

**Decisions.** No confidence floor: in the real answer the right candidate scored 0.713, and the rule already needs the candidate to be the only one left after removing other tags' names, which is the strong signal; a floor would be a number picked without evidence. Candidate lists of one tag are merged (union), so two segments that disagree add up to ambiguity (unnamed), the safe side. **Rejected:** taking the highest-confidence candidate (it picks a name with no other evidence), and naming the first tag by order of appearance (order-dependent).

**Unchanged on purpose.** The handed-on shape is still `{"speaker", "text"}`; `notes.label_transcript` is untouched. The transcript source, billing and Vexa are untouched.

## What's involved

| Path | Change |
|---|---|
| `app/skribby.py` | `_tag`, `_same`, `_candidate_names`, `_tag_names` (new), `_segments` uses them, `MAX_CANDIDATES = 20` |
| `tests/test_skribby_provider.py` | `segment()` helper takes `tag` and `candidates` (it no longer writes `speaker: 0` on every segment, which would have put every old test's speakers on one tag); 17 new test functions (some parametrized) |
| `docs/checklist/master-checklist.md` | line under B2 |

## Verification

- `ruff check .` and `ruff format --check .` (the project's pinned ruff 0.17.3) clean.
- `tests/test_skribby_provider.py`: 208 pass; 7 errors are the existing tests that need a local Postgres (not started here; CI runs them). Also run: `tests/test_transcript_compare.py`, `tests/test_skribby_live_check.py`, `tests/test_bakeoff_tool.py` (100 pass).
- Cases covered: the real shape with fake names; a low-confidence candidate still counts; a segment with no name takes its tag's name; two names on one tag; three candidates; no candidate left; both tags unnamed; two tags wanting the same name; case and bracket differences; duplicate and empty candidates; disagreeing candidate lists; non-list and malformed `potential_speaker_names`; a 5000-entry list; hostile candidate name; missing or junk tags.
- **Not verified live.** Checked against the shape of one saved real answer only; nothing was re-run against Skribby. Teams is unconfirmed (see Why).

## Operate / roll back

No setting, no migration, no server step. Revert the PR to go back to `speaker_name` only. Takes effect on the next meeting that finishes (the segments are read when the bot is `finished`).

**FE integration guide: no change** (no endpoint or response shape changed).

## Follow-ups

- Look at the next real Teams answer: does it carry a tag and candidates, and is one of the candidates the bot itself? A candidate that is the bot's own name would be taken as the speaker's name if it were the only one left; nothing in the saved Zoom answer shows this, and it is cheap to rule out with one more real answer.
- Notes still show "Unknown speaker" when Skribby gives neither a name nor a single usable candidate; that is deliberate.
