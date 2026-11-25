# SOP: speaker naming, relaxed (a small extra voice no longer blanks a meeting, and the gates that blank real meetings were reviewed)

**What shipped.** (1) The `extra_speaker` veto of the one-to-one rule is now a share, not a switch: when the people lit
for 10 s or more who have no tag add up to **under 10% of all lit time**, the tags are named anyway (`how: one_to_one`,
`note: extra_speaker_small`), the transcript is marked `speakers_incomplete`, and the line correction may move that
person's lines to them. (2) A review of every naming gate in `app/skribby.py` and `app/meet_timeline.py` (table below),
with four more relaxations where a wrong line is cheap and a blank is expensive. One switch stays the off button:
`SKRIBBY_NAME_BY_ELIMINATION`. Branch `feature/speaker-naming-relaxed`; the PR number is on the checklist. **No migration,
no new setting, no endpoint or FE-guide change** (`speakers_incomplete` stays a boolean; the new `note` lives in the saved
evidence JSON only). It goes live on deploy for new meetings; an existing meeting changes only when rebuilt (Operate).

**Nothing here is proven live.** It is proven on made-up fixtures of meeting 46's and meeting 32's shapes. Meeting 46 is
proven only when Adebayo runs the two commands under Operate after deploy (its saved evidence is deleted about
2026-12-02).

## Why

Meeting 46 (Google Meet, 2026-11-25), read from the saved evidence: three people were in the call. Skribby made two tags,
tag 0 listing `["Adebayo Tosin"]` on all 85 segments and tag 1 `["Oluwaseyi Daniel"]` on all 84, naming neither. Meet's own
speaking indicator lit 682.3 s, 240.7 s and 34.1 s (the third person, Paul Iyanu, **3.6% of 957.1 s**). The clock check
passed. `_one_to_one` refused both tags with `extra_speaker` because anyone lit for 10 s or more with no tag vetoes, so the
transcript had no names at all. Adebayo's decision: "Unknown speaker" on most meetings with a guest will lose customers; the
rules are too strict; accept a small share of wrong lines, as already accepted for meeting 39 (about 4% of the lines inside
a tag belong to someone else, SOP `2026-11-17-name-by-meet-timeline.md`).

## Part 1: the extra voice

| Question | Answer |
|---|---|
| What changed | `_one_to_one` (`app/skribby.py`) sums the lit seconds of **everyone** with no tag together (not the note taker; a person Meet shows under a fuller spelling of a tag's name is that tag's person). Under `MIN_EXTRA_SECONDS` (10 s) altogether it is a cough: no veto, no note. From 10 s, the sum is compared to all lit time (note taker excluded): under `meet_timeline.MAX_EXTRA_SHARE` (**10%**) the meeting is named and carries the note; at 10% or more it refuses `extra_speaker`, **however many people the sum is spread over** (four people lit 9.9 s each are 10.04% of a 395 s call: refused). Only the people lit 10 s or more individually can be a correction destination |
| What the named tags say | `how: one_to_one`, `note: extra_speaker_small` (was `""`), plus the existing `unchecked` key when a Meet check could not run. `skribby.holds_other_speakers(tags)` reads it |
| `speakers_incomplete` | stays **true**: `transcript_source.Candidate` has a new field `other_speakers`, and `complete` is false when it is set, whatever is named. Set by `pipeline._step_transcribing` (from `evidence["tags"]`) and by the rebuild script (from the tags it resolves). The page notice is the existing "Speaker names unavailable for part of this transcript"; its wording does not say "another person may be included". That is an FE copy decision, not made here |
| Can the line correction name a person who has no tag? | **Not before this change.** `speaker_correction.resolve_label` only moves a line to a name already used in the meeting (a tag's or a segment's own), on purpose: "Obi" must never become "Ada Obi". Paul had no tag, so every line Meet lit only for him came back `unmatched` and stayed under the wrong name. **Now:** `speaker_correction.extra_names` adds the people Meet lit for 10 s or more whose name is no tag's, but **only in a meeting whose tags say `extra_speaker_small`**, so they carry Meet's own spelling and the guard is unchanged everywhere else. A line still moves only under the old rule: exactly one person lit over it for at least 1 s, no runner-up at 25% of theirs, clock check passed, not the note taker, not a person it already carries. Lines where nobody or two people were lit stay under the tag's name, which is why the flag stays true |
| Does the correction run for these tags | It runs for any named tags (`app/skribby.py::_labelled` passes the resolved tags); nothing gated it on `how`. The new test shows Cy's two lines moved to Cy and the rest unchanged; with `SPEAKER_LINE_CORRECTION` off they stay |

### Why 10%, and the numbers behind it

* Meeting 46: 3.6% (34.1 of 957.1 s). Meeting 39: about 4% of lines inside a tag belong to someone else. A third voice of
  meeting 32 was about 30 s of a 46-minute call (that SOP's figure; **its share of that meeting's lit time is not in the
  repository** and I did not guess it). All three are well under 10%.
* What it must still refuse: a third person who is a real participant. Three people sharing a call evenly are 33% each; a
  guest who says a fifth of what the other two do is about 17%; the test of meeting 32's shape (60 s of 362 s lit) is 17%.
  10% sits between "a guest who said a few sentences" and "someone with a share of the meeting".
* **Combined, not per person**: two small extras of 6% each are 12%: refused; many people each under 10 s count together for the
  veto and for the note. Several small voices are as likely to be a merged crowd as one large one.
* Rejected: **5%** (would have blanked a guest who talked about a minute in a 20-minute call; meeting 46 passes it but with
  little room); **a fixed number of seconds** (a minute is nothing in an hour and a lot in ten minutes: a share scales);
  **20%** (a quarter of the speech under wrong names is not "a few lines").
* **The accepted cost, stated plainly:** a meeting-32-shaped meeting whose merged voice is about 30 s and whose tags are
  otherwise clean would now be named, with that voice's lines under someone else's name unless the correction moves them.
  That is what Adebayo accepted. The realistic form of meeting 32 (the merged tag lists **two** names) is still blank: a
  big tag with two names blocks the one-to-one rule.

## Part 2: the review of every gate

How to read the table. **Blank** = the tag shows as "Unknown speaker". **Wrong** = a whole tag, or a share of its lines,
under someone else's name. "Assumed shape" means I am reasoning from the shapes in earlier SOPs (meetings 29, 32, 37, 39, 45,
46) and have not seen the scenario in saved evidence; each such row says so.

### Gates that leave a normal meeting blank, and what I did

| # | Gate (where) | Real-world meeting it blanks | Cost if relaxed | Decision |
|---|---|---|---|---|
| 1 | `extra_speaker`: anyone with no tag lit 10 s or more vetoes (`_one_to_one`) | **Guest joins** and speaks a few minutes; **late joiner** whose voice merged into another tag; **dial-in** who is lit | Wrong: the guest's lines under another name, a share of the lit time | **Relaxed** under 10% of lit time (Part 1); at 10% or more still refuses |
| 2 | one big tag with two names blocks the whole meeting (`_one_to_one`, "every tag") | **Two devices of one person** under two display names; an **assistant bot** (a third-party notetaker) listed beside the person; a **silent attendee** listed beside the speaker; a **guest** merged into a tag | Wrong: a tag named after one of two people, which is a coin flip when nothing else says which | **Kept blank for this rule.** The cases are rescued by gate 9 below when Meet leads the tag with one of its candidates and the one-to-one rule did not itself refuse the meeting |
| 3 | a stray tiny tag (a cough, a "mm-hm") with two names or none blocks every other tag (`_one_to_one`) | any meeting where the model made one stray tag | Wrong: none (the stray stays unnamed); Blank: the whole meeting, before | **Relaxed:** a tag under `STRAY_TAG_SHARE` (**5% of all segments**) neither blocks nor takes a name unless it is clean with a name nobody else has; still needs at least two big tags. A third person lit 10 s or more still counts in the extra check |
| 4 | the name must be listed on **every** segment of the tag (`_one_to_one`, and `sole_candidate`) | Anyone whose segments are not all lit: Meet's indicator lights for only about half of a person's speech (SOP 2026-11-17), so Skribby's candidate list is empty on some segments in a long meeting (assumed; meeting 46's 85 of 85 is a short two-person call) | Wrong: a second voice hiding in the 20% with no list, while the 80% agree on one name; cheap, and a second name on any segment still blocks | **Relaxed** to `MIN_LISTED_SHARE` (**80%** of the tag's segments, and at least 3 segments) for both rules. A second name on any segment still blocks |
| 5 | `not_in_call`: the candidate must equal a Meet participant exactly after cleaning (`_one_to_one`) | **A name spelled differently in Meet and Skribby** ("Ada" and "Ada Obi", a shortened first name) | Wrong: only if two participants share the words; the rule needs exactly **one** participant whose words fit (`speaker_correction.same_person`), two or none still refuses; the note taker is never that participant ("Beyric" does not fit the bot) | **Relaxed** for the one-to-one rule only (`_in_the_call`); the matched person is also not counted as an "extra" voice. The timeline rule's `disagrees` and the `contradicted` comparisons stay exact (see "kept", below) |
| 6 | the sole candidate needs a tag **Skribby named itself** as the anchor (`_earlier_rules`) | meetings where Skribby named nobody | Wrong: one tag alone cannot tell one person from a merged crowd (meetings 32, 29) | **Kept.** The zero-anchor meeting is covered by the one-to-one rule and by gate 9 |
| 7 | one-to-one needs at least two tags; one tag alone stays blank | a **silent attendee** (one person talks, one tag) | Wrong: a merged crowd named as one person | **Kept** for the shape rule. Gate 9 names the tag when its candidate list holds the person Meet leads it with |
| 8 | one-to-one needs distinct names; two tags with the same one candidate name nothing | one person over-split into two tags (assumed shape) | Wrong: a second person in the call who is not listed | **Kept** (the sole-candidate SOP calls this a shared claim). Gate 9 names both tags when Meet leads each with that person |
| 9 | the timeline rule's **corroboration**: at least one tag the earlier rules named must have the same person on top of Meet's timeline, else nothing is named (`_name_by_timeline`, `uncorroborated`) | every meeting where Skribby named **no** tag and the tags list several names: a tag listing `[Ada, Guest]`, `[Ada, a notetaker bot]`, `[Ada, a silent attendee]`, a person's two devices | Wrong: a tag named after Meet's leader that Skribby also lists. Meet's own signal must still clear the thresholds in gate 10 and the clock check, and the leader must be on the tag's own candidate list (an exact name) | **Relaxed:** with no corroborating tag, a tag is named when its own candidate list holds the person Meet's timeline leads it with (`how: meet_timeline`). The tag's candidate list is the corroboration (Skribby and Meet agree on that tag, on the spelling and on the clock). **It never works round a veto:** a tag the one-to-one rule looked at and refused (`one_to_one: extra_speaker`, `not_in_call` or `contradicted` on it) is not named in this mode, so a large extra share stays blank with `NAME_BY_MEET_TIMELINE` on. The exclusion route does not run in this mode. `contradicted` still stops everything. Earlier SOPs rejected "name by timeline when nobody is named" because nothing proved the names; the candidate list is what proves it per tag |
| 10 | timeline `MIN_SECONDS` 30, `MIN_SHARE` 20%, `MIN_RATIO` 3.0 (`meet_timeline.verdict`) | a quiet person (under 30 s lit over a tag); a tag where two people talk over each other | Wrong: meeting 39's weak tags (11.5 s at 2.45x, 10.9 s at 1.45x) would be named on a coin flip. Those tags hold about 35 s of speech each, so the blank costs little | **Kept.** Relaxing gains almost nothing (blanks on weak tags are a few lines) and the only real data (meeting 39) sits just under the line |
| 11 | clock check: a shift of up to 30 s that fits 10% better refuses the whole timeline (`clock_shifted`) | a late-starting recording, a Meet clock offset | Wrong: names assigned from a timeline that is out of step | **Kept.** A wrong clock makes every overlap wrong |
| 12 | `contradicted`: a named tag with a different person clearly on top blocks everything (`_meeting_gate`, `_one_to_one`) | swapped names; also a **spelling difference** between Skribby's name and Meet's, which reads as a contradiction | Wrong: swapped names are exactly this | **Kept** (the brief). The spelling case stays blank on the timeline routes; it is relaxed only for the one-to-one rule (gate 5) |
| 13 | `disagrees`: the timeline winner must equal one of the tag's candidates, compared exactly (`_name_by_timeline`) | a **spelling difference** | Wrong: needs loose matching in a rule that picks a name from Meet | **Kept exact** (the same loose-matching step would be needed in four places; one meeting's evidence will say if it matters, see Follow-ups) |
| 14 | elimination chain round 2+, exclusion route (`meet_exclusion`): need usable events, agreeing named tag, 90 s tag, 1.5x | Zoom/Teams (no events); a meeting with one open tag | Wrong: a name resting on an inferred name | **Kept.** Reviewed in `2026-11-23-speaker-name-chain.md`; no new evidence |
| 15 | candidate list over `MAX_CANDIDATES` (20) reads as no answer | **A large meeting** or webinar (more than 20 people listed) | Wrong: choosing from a cut-off list is a guess | **Kept.** Such meetings fall back on the timeline rule alone |
| 16 | `speakers_incomplete` at 5% unnamed words | any meeting with a stray unnamed tag | Wrong/noise: a notice nobody should see | **Unchanged**, except it is also true when a tag says `extra_speaker_small` |

### Cases that must stay blank (each has a test)

* A name with no support at all: a candidate Skribby lists that is not in the call (`not_in_call`, **nothing is named, with the
  timeline rule on or off**), a candidate that fits two Meet participants, one that fits none or only the note taker, a leader Meet
  shows that the tag does not list (gate 9).
* A big tag with two names (the merged-crowd shape): blocks the one-to-one rule; one big tag and a stray stays blank.
* A clear contradiction from Meet (swapped names), at zero shift and at the best shift; also stops gate 9.
* An extra share of 10% or more, with `NAME_BY_MEET_TIMELINE` on **and** off (50 s of 405 s, 12%; 90 s, 20%; 60 s of 362 s, 17%;
  four people lit 9.9 s each, 10.04%) and the cough boundary (a single 9.0 or 9.9 s, or four of 2 s, gives no note).
* A name listed on only 60% of a tag's segments; a second name on any segment.
* `SKRIBBY_NAME_BY_ELIMINATION=false`, with the timeline on or off: every tag stays unnamed (`elimination_off`, `would_be`, and
  `via: one_to_one` which keeps the timeline rule from naming it), and the transcript is not marked.

## How, and what was decided (summary)

* `app/skribby.py`: `_one_to_one` returns `(picks, why, unchecked, small_extra)`; `_listed_enough`, `_in_the_call`,
  `holds_other_speakers`, `STRAY_TAG_SHARE`, `MIN_LISTED_SHARE`, `EXTRA_SPEAKER_SMALL`; `_name_by_timeline` has a
  `candidate_only` mode (gate 9).
* `app/meet_timeline.py`: `MAX_EXTRA_SHARE` with the numbers above.
* `app/speaker_correction.py`: `extra_names` and an optional `extra` argument of `assess`.
* `app/transcript_source.py`: `Candidate.other_speakers`; `candidate(..., other_speakers)`.
* `app/pipeline.py`, `scripts/rebuild_meeting_from_evidence.py`: pass the flag; the dry run prints `one_to_one,
  extra_speaker_small` next to a tag and `speakers incomplete: before .., after yes`.
* Alternatives rejected: a new switch (one off button is enough); marking the label text "(possibly Paul)" (inventing UI);
  naming a tag with two names by its larger candidate (cannot tell a merged crowd); a list of third-party notetaker bot names
  (an invention: Skribby's `own_names` is all we know, and gate 9 handles the bot through Meet).

## What's involved

| File | Change |
|---|---|
| `app/skribby.py`, `app/meet_timeline.py`, `app/speaker_correction.py`, `app/transcript_source.py`, `app/pipeline.py` | as above |
| `scripts/rebuild_meeting_from_evidence.py` | resolves the tags before `candidate` so the flag survives a rebuild |
| `tests/test_speaker_naming_relaxed.py` (new, 35), `tests/test_one_to_one_naming.py`, `tests/test_meet_timeline_naming.py`, `tests/test_rebuild_meeting_from_evidence.py` (+1) | tests; the older ones that asserted the strict behavior (meeting 32 shape, "name missing from one segment", "not in the call", the 10 s/30 s veto, "no name without a named tag") were rewritten to the new rule and say why |
| `docs/sop/2026-11-25-speaker-naming-one-to-one.md`, `2026-11-17-name-by-meet-timeline.md`, `2026-11-16-skribby-name-sole-candidate.md` | update lines pointing here |
| `docs/checklist/master-checklist.md` | addendum |

## Verification

* **Tests on made-up fixtures** (names, words and seconds invented; meeting 46's shares reproduced at half the seconds so no lit
  stretch exceeds `MAX_SPAN_SECONDS`): meeting 46's shape names both tags with the note and `holds_other_speakers`; the
  extra-share boundary at 9 s, 9.9 s, 10 s, 39 s (named) and 40 s, 90 s (refused); meeting 32's shape at 60 s refuses `extra_speaker`
  with the timeline rule on and off (both tags blank), and at 29 s is named with the note; 12% and 20% shares refuse with the
  timeline on; many small extras (four at 9.9 s refuse, two at 6 s are named with the note); the correction moves the third person's two lines to them and **does nothing without the
  note, without 10 s lit, with the correction switched off**; stray tags (two names, no candidate, a repeat, a clean one,
  the 4%/5% line, one big tag plus a stray, a stray's third person still vetoes); the 80% line (4 of 5 names, 3 of 5 does not;
  sole candidate too); loose spelling (the one participant it fits, two that fit, none); the zero-anchor timeline (names a tag
  listing Meet's leader, not one that does not, `contradicted` still stops); a rebuild of meeting-46-shaped evidence (flag
  stays yes, `[Cy Example]` lines appear, no names in the dry run).
* **Each new gate fails without it** (undone one at a time by a script, then restored): extra share limit, stray tags, the 80%
  line, loose spelling (both the no-match and the pick-the-first-of-many direction), the zero-anchor timeline mode and its
  candidate check, its skip when the one-to-one rule refused, the elimination-off hold on the timeline, the combined extra
  share, the note taker dropped from the participants, the extra destinations, their 10 s floor, the incomplete flag, and the note itself. All caught.
* **Bounded runs on this Mac** (scratch Postgres 17, removed afterwards): `tests/test_speaker_naming_relaxed.py`,
  `test_one_to_one_naming.py`, `test_meet_timeline_naming.py`, `test_speaker_correction.py`, `test_transcript_source.py`,
  `test_skribby_provider.py`, `test_skribby_evidence.py`, `test_rebuild_meeting_from_evidence.py`; `ruff check .` and
  `ruff format --check .` with the pinned ruff 0.17.3. CI runs the rest.
* **Not proven live:** (a) **meeting 46 itself**: only the rebuild below can say whether its real spellings, bot name and
  clock pass; (b) the participant names in meeting 46 against Skribby's candidate spellings; (c) the 10% line is three
  meetings' worth of numbers, one of them not recomputed; (d) every "assumed shape" in the table; (e) gate 9 against any real
  meeting without a named tag; (f) the correction against a real clock offset on 46 (it refuses on `clock_shifted` and the
  lines then stay put, the tags still named).

## Operate / roll back

* **Off:** `SKRIBBY_NAME_BY_ELIMINATION=false` in `.env`, then `docker compose up -d --force-recreate web worker scheduler`
  (a restart does not re-read `.env`). Every derived name stops; tags record `would_be`. The line correction has its own
  switch, `SPEAKER_LINE_CORRECTION`. `NAME_BY_MEET_TIMELINE=false` also turns gate 9 off.
* **Revert:** revert the PR; no migration. Meetings already named keep their names until rebuilt.
* **Rebuild meeting 46 (after this PR is merged and deployed; Adebayo runs them; before about 2026-12-02).** In plain words:
  the text of meeting 46 has no speaker names. The saved copy of Skribby's answer is read again with the new rule, the
  transcript and notes of that one meeting are rewritten from it, and the old ones are saved first in a private file and can be
  put back (full guarded blocks and the roll back are in `docs/sop/2026-11-16-rebuild-meeting-from-evidence.md`, use `ID=46`).

  Dry run (changes nothing, prints no names and no words):

  ```bash
  ssh haven-respite 'cd /opt/beyric-note-taker && docker compose exec -T worker python scripts/rebuild_meeting_from_evidence.py 46 </dev/null'
  ```

  **A good result:** no `REFUSED`; both tag lines say `named [one_to_one, extra_speaker_small]`; `speakers named: before 0,
  after 2`; `speakers incomplete: before yes, after yes`; `lines credited to no one` goes to 0; the last line says `DRY RUN:
  nothing was changed`. A tag line `NOT named [... one-to-one naming refused: not_in_call|extra_speaker|contradicted ...]`
  is the rule refusing with its reason: send the line as it is to the lead (the extra share of 3.6% should not refuse; a
  spelling difference would say `not_in_call`).

  Apply (only after a good dry run; keep the backup path it prints):

  ```bash
  ssh haven-respite 'cd /opt/beyric-note-taker && docker compose exec -T worker python scripts/rebuild_meeting_from_evidence.py 46 --apply </dev/null'
  ```

  Then Step 3 of the rebuild SOP a few minutes later, open meeting 46 and read it: Adebayo's and Oluwaseyi's lines should be
  theirs and Paul's few lines should show under Paul. If it reads wrong, Step 4 of that SOP puts the old text and notes back;
  tell the lead what was wrong.

## Follow-ups

* Read the evidence of the next real meeting with a guest: did the note appear, did the correction move the guest's lines to
  the right person, how much of the lit time was the guest's? Tune 10% only from several meetings.
* A new sentence for the `speakers_incomplete` notice when the cause is a possible extra speaker (FE copy; the flag is a
  boolean and carries no reason today).
* Loose spelling is only in the one-to-one rule. If a Meet/Skribby spelling difference blocks a real meeting on the timeline
  routes (`disagrees`, `contradicted`), compare the two spellings in the report and extend `_in_the_call`'s unique-match test.
* A big tag with two names is still blank. Naming it from Meet's lead among its two candidates works only when the lead is clear
  (gate 9); a case Meet cannot settle needs a different signal.
* The correction can name only people Meet lit for 10 s or more in a meeting the naming flagged. A guest in a meeting that was
  not flagged (the naming refused, or the tags came from Skribby's own names) still cannot be a destination.
