# SOP: Skribby speaker names from the diarization tag and the candidate list

> **Update 2026-11-10:** naming a tag by elimination (the "unnamed tag takes its only remaining candidate" rule below) is now behind a setting, `SKRIBBY_NAME_BY_ELIMINATION` (default **true**, so today's behaviour is unchanged). It may have named a third speaker after someone who had not joined (meeting 29). With `false` a tag is named only by the `speaker_name` its own segments carry. What Skribby sent is now kept 7 days, so the choice can be made on evidence. See `docs/sop/2026-11-10-skribby-raw-segments.md`.


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
2. **Tag with no name at all: naming by elimination.** Take the cleaned names in `potential_speaker_names` (all segments of the tag, merged, first spelling kept in the order listed), drop the **bot's own names** (`settings.skribby_bot_name` and, when set, `skribby_account_name`, handed in as `own_names` through `SkribbyClient.own_names` and `map_bot`), and drop every name another tag already has. The tag is named **only if at least one candidate was dropped because another tag has that name, and exactly one candidate is left.** A lone candidate that nothing was ruled out for (nothing else supports it) stays unnamed, and so does a list left with none or with two or more.
3. **Names are compared** NFKC-normalised, with invisible format characters (Unicode category Cf, e.g. zero-width space) dropped, then case-folded; so "Zoë" written precomposed or decomposed, or with a zero-width character inside, is one name.
4. **Never two tags, one name.** If two unnamed tags would both end up with the same name, neither gets it.
5. **Never a guess on partial data.** A segment whose candidate list is longer than `MAX_CANDIDATES` (20; real ones hold a few) marks the tag unreadable, so it stays unnamed. Non-list values, entries that are not objects, non-string or empty names and names that clean to nothing are ignored. A tag that is missing, a boolean, a float, a list or empty has no inference at all. An integer tag and its text form are one tag.
6. Every name goes through `_clean_name`, so a hostile candidate name is scrubbed like any other.

**The assumption elimination rests on, stated plainly.** It assumes the real speaker is in Skribby's candidate list. If it is not, the rule can name the wrong person and **nothing in the data catches it**: with three participants, tag 2's candidates `[Ada, Bo]` and the real speaker a dial-in Cy who is not listed, Ada is ruled out as tag 1's and Bo is given Cy's words. The rule is conservative (it never names from a lone candidate nothing supports, never from the bot's own name, never from an ambiguous list), but it is not proof. Skribby's own matching is the source of the list; this code only declines to guess beyond it.

**Decisions.** No confidence floor: in the real answer the right candidate scored 0.713, and the support for a name is the elimination (another tag already has the other candidate), not the score; a floor would be a number picked without evidence. The bot's own names are removed because the bot is in the participant list and could be the one candidate left. Candidate lists of one tag are merged (union), so two segments that disagree add up to ambiguity (unnamed), the safe side. **Rejected:** taking the highest-confidence candidate (a name with no other evidence), naming from a lone candidate with nothing ruled out (nothing supports it), and naming the first tag by order of appearance (order-dependent).

**Unchanged on purpose.** The handed-on shape is still `{"speaker", "text"}`; `notes.label_transcript` is untouched. The transcript source, billing and Vexa are untouched.

## What's involved

| Path | Change |
|---|---|
| `app/skribby.py` | `_tag`, `_same`, `_candidate_names`, `_tag_names` (new), `_segments` and `map_bot` take `own_names`, `SkribbyClient` takes `account_name` and has `own_names`, `MAX_CANDIDATES = 20` |
| `tests/test_skribby_provider.py` | `segment()` helper takes `tag` and `candidates` (it no longer writes `speaker: 0` on every segment, which would have put every old test's speakers on one tag); 23 new test functions (some parametrized) |
| `docs/checklist/master-checklist.md` | line under B2 |

## Verification

- `ruff check .` and `ruff format --check .` (the project's pinned ruff 0.17.3) clean.
- `tests/test_skribby_provider.py`: 208 pass; 7 errors are the existing tests that need a local Postgres (not started here; CI runs them). Also run: `tests/test_transcript_compare.py`, `tests/test_skribby_live_check.py`, `tests/test_bakeoff_tool.py` (100 pass).
- Cases covered: the real shape with fake names; a low-confidence candidate still counts (with something ruled out); a segment with no name takes its tag's name; two names on one tag; three candidates; no candidate left; both tags unnamed; two tags wanting the same name; case and bracket differences; duplicate and empty candidates; disagreeing candidate lists; non-list and malformed `potential_speaker_names`; a 5000-entry list; hostile candidate name; missing or junk tags; the bot's own name as the lone or the second candidate; a lone candidate with nothing ruled out; precomposed vs decomposed and zero-width names; first spelling kept.
- **Not verified live.** Checked against the shape of one saved real answer only; nothing was re-run against Skribby. Teams is unconfirmed (see Why).

## Operate / roll back

No setting, no migration, no server step. Revert the PR to go back to `speaker_name` only. Takes effect on the next meeting that finishes (the segments are read when the bot is `finished`).

**FE integration guide: no change** (no endpoint or response shape changed).

## Follow-ups

- Look at the next real Teams answer: does it carry a tag and candidates, and does the bot appear in the list under a name other than the two we know?
- Notes still show "Unknown speaker" when Skribby gives neither a name nor a single usable candidate; that is deliberate.
