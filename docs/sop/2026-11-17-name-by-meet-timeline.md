# SOP: name Skribby speaker tags from Meet's own speaker timeline, and a true `speakers_incomplete`

**What shipped.** (1) A new naming source, `how: meet_timeline`: a diarization tag that is still unnamed after
Skribby's own name, elimination and the sole-candidate rule is named after the participant that **Meet's own
speaking indicator** shows speaking most over that tag's segments, only on a clear margin, only after a clock
check, and only in a meeting where that indicator already agrees with names Skribby gave. Several tags may take
one name (that is the point: the model splits one person over several tags). Switch `NAME_BY_MEET_TIMELINE`
(default **on**). (2) `speakers_incomplete` for a Skribby-text meeting is now true when the lines under
"Unknown speaker" hold **5% or more of the words**, not only when nobody is named. (3) The rebuild script and the
segments report read the same timeline, so meeting 39 can be rebuilt with it. Branch
`feature/name-by-meet-timeline`; the PR number is on the checklist. **No migration, no new column.** It goes live
on deploy for new meetings (default on, no `.env` change); an existing meeting changes only when Adebayo runs the
rebuild (below).

## Why

Meeting 39 (2026-11-17, Google Meet, six real speakers, `TRANSCRIPT_SOURCE=skribby`, name by elimination on):
Skribby over-split the voices into **nine tags** and named three (tag 1 and tag 3 by `sole_candidate`, tag 4 by
Skribby's own `speaker_name`). Six tags stayed `Unknown speaker`: several tags each claimed the same candidate
(`shared_claim`) or their candidates were ambiguous (`unresolved`). Meet also listed two people more than once
(one three times, one twice: they left and rejoined), so the participant list has repeated names. The report's
own "Meet's own speaker timeline" section (seconds of overlap between each tag's segments and each participant
speaking; its clock check said no shift of up to 30 s fits better) already pointed at the right person for every
tag that had enough speech. The **voice splitting** was Skribby's fault; the **naming** was ours to improve,
because the platform's own signal was sitting in the saved evidence unused.

The numbers (seconds of overlap, the participant with most, the runner-up, and what the rule does):

| Tag | Total s | Most (s) | Runner-up (s) | Ratio | Result |
|---|---|---|---|---|---|
| 0 | 441.8 | person A 209.0 | 39.5 | 5.3 | named A |
| 1 | 81.3 | person C 34.0 | 4.8 | 7.1 | already named by an earlier rule (agrees) |
| 2 | 195.3 | person A 75.9 | 15.0 | 5.1 | named A |
| 3 | 131.2 | person B 58.6 | 2.4 | 24 | already named (agrees) |
| 4 | 548.0 | person F 333.1 | 10.1 | 33 | already named (agrees) |
| 5 | 1029.8 | person A 449.0 | 14.4 | 31 | named A |
| 6 | 274.5 | person D 134.5 | 37.9 | 3.55 | named D |
| 7 | 35.3 | person D 11.5 | 4.7 | 2.45 | **stays unknown** (11.5 s, under 30 s and under 3x) |
| 8 | 33.9 | person A 10.9 | 7.5 | 1.45 | **stays unknown** (10.9 s, 1.45x) |

(The table is the same one `tests/test_meet_timeline_naming.py::TABLE` lays out, with made-up names; the real
names are not in this repository.)

## Why `speakers_incomplete` read `f` (it is a bug)

`models.Meeting.speakers_incomplete` is documented as "True when some part of the transcript could not be given
speaker names" and the page says "Speaker names unavailable for part of this transcript" when it is set. On the
`skribby` path it was set from `Candidate.complete`, which was `named`: **"at least one speaker has a name"**.
That is the contract of `notes.label_transcript` (which only ever has the one question for the `own` path:
could anyone be named), copied onto a path where unnamed tags are common and each line already carries its tag.
So any meeting with one named speaker was "complete", however much of the speech sat under "Unknown speaker":
meeting 39 had six of nine tags unnamed, about 73% of the words by Meet's own seconds (2,010 of 2,771 s), and
read `f`. **It is a bug against the flag's own meaning**, not a different definition.

**The rule now** (`app/transcript_source.py`, `Candidate.complete`, `MAX_UNNAMED_SHARE = 0.05`): the labelling is
complete when at least one speaker is named **and** the lines credited to "Unknown speaker" hold **under 5% of
the words**. At 5% or more, or with nobody named, `speakers_incomplete` is true. Why 5% of words: "named-person
tags cover too little" and "Unknown tags with substantial speech" are the same measure, the share of speech with
no name; a hole of a few percent (a back-channel tag, two "mm-hm" tags) is not worth a notice on the page,
whereas 5% of an hour is three minutes of speech; words, not segments, because a one-word segment and a ten-minute
tag must not weigh the same. No data sets the 5% (one meeting); it is a constant, one line to change.
With the new naming meeting 39 would leave about 2.5% (tags 7 and 8 by seconds, a proxy for words) unnamed: not
incomplete.

The flag is only recomputed when a meeting is processed or rebuilt: stored meetings keep their value until then.
The `own` path (`label_transcript`) is untouched; its `complete` is a different, older question.

## How, and what was decided

### The rule, in the order it runs

`app/skribby.py::_resolve_tags` runs, unchanged and in order: Skribby's `speaker_name` (`given`), elimination,
the sole candidate. Then, only if `NAME_BY_MEET_TIMELINE` is on **and the meeting's speaker events are available**,
`_name_by_timeline` looks at the tags still unnamed. The arithmetic (placing Meet's Unix-millisecond times on the
transcript's clock from the bot's `recording` status, overlap of each tag's segments with each participant's
speaking, the 30 s clock fit) lives in the new `app/meet_timeline.py`, shared with the report script, so the
report and the app cannot read the timeline two ways.

| Guard | What it does | Why |
|---|---|---|
| Events present and readable | otherwise nothing is named; tags carry `timeline: no_events` (and nothing is added when no events were given at all) | no signal, no name |
| Clock check (`clock_shifted`) | if moving Meet's times by up to 30 s either way raises the total overlap by more than 10%, **nothing is named** | the report's own test; a clock that fits better elsewhere means the overlaps do not mean what they say. Unix times with no `recording` status time (`clock_unplaced`) or no timed segment (`no_times`) also name nothing |
| Corroboration (`uncorroborated`) | at least one tag the earlier rules named must have that same person clearly on top of the timeline (at least 30 s and 3x) | proves the Meet participant names are the names Skribby uses and that the clock lines up in *this* meeting. Same idea as the sole-candidate rule's "one tag Skribby named itself". **A meeting where nothing was named gets no timeline names** |
| No contradiction (`contradicted`) | if a tag the earlier rules named has a *different* person clearly on top, nothing is named | the two signals disagree: one of them (or the clock) is wrong, and we do not know which. The earlier name is never touched |
| Only unnamed tags in a "could not tell" state (`not_eligible` otherwise) | `unresolved`, `shared_claim`, `no_candidates`, `elimination_off` | a tag with two names of its own (`several_names`) already holds two people: a third name would be one of them |
| Overlap of the winner at least **30 s** (`too_little`) | | see thresholds |
| Winner at least **20%** of the tag's own seconds (`small_share`) | | added after review: a tag whose real speaker has no usable Meet entry (a dial-in, events cut by the size cap) must not be named after whoever talked over it for 35 s of a 1,000 s tag. Meeting 39's right winners hold 39% to 61% of their tags, so 20% blocks none of them |
| Winner at least **3.0x** the runner-up (`not_clear`) | ties are therefore never named | see thresholds |
| The bot (`bot`) | the bot takes part in the ranking; if it ranks first the tag is not named. It is never skipped to the person below | otherwise a quiet tag would be named after whoever came second |
| On the tag's own candidate list (`disagrees`) | when Skribby listed candidates for the tag (and the list is readable), the winner must be one of them. Names are compared **exactly** after cleaning (case, spaces, brackets, invisible characters): "Ada" is never "Ada Obi". The price: if Meet shows a shorter or longer spelling than Skribby used, the rule refuses (a mismatch with a name an earlier rule gave even reads as a contradiction and stops the whole meeting), which is the safe direction | two independent signals must not contradict. **This is the one guard that can block a real name if Skribby's list for a tag lacks the right person; the dry run says `disagrees` when it does** |
| For `shared_claim` and `elimination_off` tags, the winner must be the name the earlier rules had picked for the tag (`chosen` in `_earlier_rules`; a shared claim carries no `would_be` in the saved evidence, so the pick is passed in, not read from the tag) | otherwise `disagrees` | two signals must not contradict. It can refuse a real over-split (a tag claimed for A that Meet shows is B, whose other tag is named): that is deliberate, the dry run says `disagrees` |
| Repeated names are one person | participants with the same name after the app's cleaning (brackets, whitespace, case, invisible characters) are read together: their speaking intervals are merged (a **union**, so a rejoin never counts twice) and the first spelling is shown. **Two real people with the same display name are merged into one too**: their speech adds up and the tag takes that shared name (the label is not wrong text, since both show under that name, but the tag is one name for two people) |
| A speaking interval that never closes is dropped (`app/meet_timeline.py`) | a `started-speaking` with no `stopped-speaking` or `left` is **dropped, not run to the end of the record**; so is one longer than 600 s (a missing stop) | added after review. Run to the end, a connection that dropped at minute 20 would have that person "speaking" over every later tag, could win weak tags, and would flatten the clock check. The bot leaving while someone talks leaves one such interval in an ordinary meeting, so refusing the whole meeting would be fragile; dropping costs that one interval. Whether Skribby always sends a `left` on a dropped connection is not proven live. The 600 s is a guess (Meet's indicator flickers with every pause); the meeting-39 numbers in the table above were not recomputed with it, so check the dry run | meeting 39: Meet listed two people three and two times |
| Several tags may share a name | | the model over-split one person (tags 0, 2, 5 are one person); `candidate()` merges consecutive lines of one name |
| Never overrides | a tag with any name from `given`, `elimination` or `sole_candidate` is left as it is, whatever the timeline says | |

**Thresholds, and why.** `MIN_SECONDS = 30` and `MIN_RATIO = 3.0` (`app/meet_timeline.py`). In meeting 39 the tags
that were right and clear had 75.9 s to 449 s of overlap and 3.55x to 31x over the runner-up; the two weak tags
had 11.5 s and 10.9 s and 2.45x and 1.45x. Any minimum between 11.5 and 75.9 s fits those numbers; I chose 30 s as
"a few real turns, not a cough". The ratio is the line with the least room: 2.45 (not named) against 3.55 (named).
The share floor (`MIN_SHARE = 0.2`) was added after review and sits below every right winner of meeting 39 (47%, 39%, 44%, 49% for the four named tags). **These come from one meeting** and tag 6 passes at 3.55x with little to spare; they were set conservatively
(a wrong name is worse than "Unknown speaker": it moves who said what in the notes, and nothing flags it), not
fitted to be loose. Tag 7 really is Meet's top person for 11.5 s against 4.7 s, and it may well be right; at 35 s
of speech it is not worth the risk and it stays unknown. If more meetings show the weak side is also reliable, lower
the constants; if a wrong name ever appears, raise them or turn the switch off.

### The two switches are independent

`NAME_BY_MEET_TIMELINE` does **not** follow `SKRIBBY_NAME_BY_ELIMINATION`. With elimination off, a tag elimination would
have named is left `elimination_off`, and the timeline **may still name it, but only with the name elimination would
have given** (otherwise `disagrees`). An operator who turned elimination off because it once named someone not in the
call therefore still gets those names back when Meet's speaking indicator agrees. I kept it independent because the
timeline is a different, independent signal and the agreement makes it stronger than either alone; to stop all derived
names, turn **both** switches off. (Alternative, rejected: make the timeline obey the elimination switch, which would tie
a new rule to the reason an old one was switched off.)

### Default on, and why

`NAME_BY_MEET_TIMELINE` defaults to **true**, as the elimination switch does. The case for on: the meeting that
raised this had 6 of 9 tags unnamed and the people in it are the pilot users; the guards above are many and each
refuses rather than guesses; every name it gives is recorded as `how: meet_timeline` in the saved evidence, so a
wrong one can be found; one line in `.env` plus a recreate turns it off. The case against: it is proven on one
meeting only. I judged "a table full of Unknown speakers" worse than "a rare wrong name behind five guards", and the
alternative (off, then Adebayo edits `.env` and recreates before a dry run can even show the effect on 39) costs
server steps for nothing, since the dry run changes nothing. **Say so if you disagree; it is a one-line default.**

### Where the events come from in the live path

The plain Skribby bot read carries no speaker events; they come from a second request
(`?with-speaker-events=true`, the same one `SkribbyProvider.get_speaker_events` already makes for the evidence).
`SkribbyProvider._events_for_naming` makes it **only** for a bot whose status is `finished` with a transcript and
only when the switch is on, so a live bot polled every few seconds still costs one request per poll. A finished bot is looked at more than
once (the status poll, then the transcript step), so a good answer is **kept in the process** (the last 32 bots): **one
extra request per meeting and process**, not per look; a failure is not kept, so the next look asks again. The events of a
finished meeting do not change; another process (worker versus web) or a restart asks once more. If it fails (or
answers in a shape that is not the documented one) it is None, logged by exception class only, and the tags are
named without the timeline, exactly as before: **it can never fail a meeting**. `map_bot` takes the record as
`speaker_events` and gives it to `_segments` and `_safe_evidence`, so the stored transcript and the saved
evidence name the tags the same way. The pipeline's own events fetch for the evidence is unchanged (another request,
harmless).

### What the label says

The existing code does **not** mark derived names in the label: a turn is `[Name]: text` whether the name was
Skribby's, eliminated or a sole candidate; only the saved evidence says why (`how`). The timeline name is the same:
the label shows the name plainly, and `how: meet_timeline` is in `skribby_segment_evidence.tags` and in the report
header. A tag the rule looked at and left unnamed gets a `timeline` key in the evidence (`too_little`, `small_share`, `not_clear`,
`bot`, `disagrees`, `clock_shifted`, `uncorroborated`, `contradicted`, `not_eligible`, `no_overlap`, `no_events`,
`clock_unplaced`, `no_times`) so the report says **why** a tag stayed unknown. I did not add a visible
"(from Meet's timeline)" marker: nothing else is marked, and a new UI convention is not mine to invent.

### Rejected alternatives

* **Name the tag after its top timeline person whatever the margin** (rejected: tags 7 and 8 would be named on 11 s).
* **Name by timeline when nobody is named** (rejected: no way to tell the Meet names or the clock are right; `uncorroborated`).
* **Fit a clock offset and use it** (rejected: a shifted clock is a reason to distrust the signal; the report still
  offers `--offset` for a human to look).
* **Trust the timeline over Skribby's own names** (rejected: it is not proof, see below; an own name always wins).
* **A high share-of-tag threshold** (rejected: Meet's indicator covers only 40-50% of a tag's time even for the
  right person in meeting 39, so a high floor would refuse right names). A **low** floor (20%) is kept: it only
  blocks a winner that holds a sliver of the tag (first version of this SOP wrongly rejected both).
* **Extend a never-closed interval to the end, or refuse the meeting for one** (rejected: the first names the
  wrong person, the second throws away the whole meeting for the ordinary end-of-call open interval).
* **Loose (word-subset) name matching** (rejected after review: "Ada" vs "Ada Obi" are two people).
* **A new column or a new evidence field for the switch** (rejected: the tag's `how` says it; no migration).

## What's involved

| File | Change |
|---|---|
| `app/meet_timeline.py` (new) | `speaking_intervals` (now groups one person's several entries), `anchor`, `heard_by_tag`, `clock_fit`, `read`, `verdict`, `same_person`; `MIN_SECONDS`, `MIN_RATIO` |
| `app/skribby.py` | `_resolve_tags(..., events, by_timeline)` + `_name_by_timeline`, `apply_timeline` (for the report), `_tag_names`/`_segments`/`_evidence` take the events, `map_bot(speaker_events=...)`, `SkribbyProvider._events_for_naming` |
| `app/config.py`, `.env.example`, `deploy/README.md` | `NAME_BY_MEET_TIMELINE` (bool, default true; a value that is not a boolean stops startup like the other switches) |
| `app/transcript_source.py` | `Candidate.unnamed_words`, `complete` = named and under `MAX_UNNAMED_SHARE` unnamed |
| `scripts/rebuild_meeting_from_evidence.py` | passes the saved events (`row.events`) and the switch to the same functions; the dry run prints `timeline: <reason>` after a tag that stayed unknown |
| `scripts/skribby_segments_report.py` | imports the timeline arithmetic from `app/meet_timeline.py`; a last section "What naming from Meet's timeline would do to the unnamed tags" |
| `tests/test_meet_timeline_naming.py` (new, 42), `tests/test_transcript_source.py`, `tests/test_rebuild_meeting_from_evidence.py` (+3), `tests/test_skribby_evidence.py` (the never-closed interval), `tests/test_bot_name.py`, `tests/test_provider_contract.py`, `tests/test_skribby_live_check.py` | tests; the last three only tolerate the second request of a finished bot |

## Verification

* **From the numbers of meeting 39 only** (`tests/test_meet_timeline_naming.py`, made-up names, placeholder words,
  blocks of time laid out so each overlap equals the table, with 60 s of silence between them so a shifted clock
  never fits better): tags 0, 2, 5 named person A (`meet_timeline`), tag 6 person D, tags 7 and 8 stay unknown
  (`too_little`), tags 1, 3, 4 unchanged (`sole_candidate`, `sole_candidate`, `given`). A separate test recomputes the
  overlaps from the laid-out times and checks they equal the table. The labelled transcript of the fixture: unnamed
  words 72% before, 2.5% after; the old rule (`complete` = someone is named) would have said true for both
  (the bug; true by inspection, no test asserts the old rule), the new one says false for the 72% case and true for the 2.5% case.
* **Meetings 37 and 38 shapes** (three people all named by earlier rules; four people with one by elimination) give
  identical tags with and without events (`==` on the whole dict). They cannot change **by construction**: every tag is
  named, so there is no unnamed tag for the rule to look at (the timeline only ever visits unnamed tags). The tests pin
  that.
* **Guards, each with a test that fails without it** (undone by hand, then restored): minimum seconds (3 tests fail),
  ratio (1), clock check (1), contradiction stop (1), corroboration (1), bot first-rank (1), the tag's own candidate list (3),
  the earlier choice for a shared claim / elimination-off tag (2), the share floor (2), the never-closed interval (2), the 600 s cap (1), the eligibility list (1). Also tested: ties and
  2.5x/2.94x/3.33x margins, 29 s and 30 s, nobody speaking, the bot, rejoin entries (three entries each with a third of
  Ada's speech name her; the union equals 449.0 s), one person under two spellings, a clock 8 s late (nothing named,
  tags say `clock_shifted`) and 1 s late (named), no/unreadable events, Unix times with no `recording` time, no segment
  times, times already in seconds, elimination off, the switch off (tags identical to before), the switch's default and
  its parsing (`maybe` raises `ValidationError`), the report section, and the live lookup (second request only for a
  finished bot with the switch on; a failed second request leaves the tags named as before).
* **speakers_incomplete:** boundary tests at 49/999 words (complete), 50/1000 (incomplete), nobody unnamed
  (complete), most of the speech unnamed with two named (incomplete), nobody named (incomplete, as before).
* **Rebuild:** a meeting-39-shaped evidence row with saved events: the dry run prints `meet_timeline`, `timeline:
  too_little`, no names, `speakers incomplete: before yes, after no`; `--apply` writes `[Ada Example]` and `[Di Example]`
  lines and leaves `[Unknown speaker]` for the two weak tags; with the switch off or no events the transcript is as before.
* **Bounded runs on this Mac** (scratch Postgres 17, removed afterwards): the files above plus `tests/test_skribby_provider.py`,
  `tests/test_skribby_evidence.py`, `tests/test_skribby_e2e.py`, `tests/test_transcript_source.py`,
  `tests/test_provider_contract.py`, `tests/test_skribby_live_check.py`, `tests/test_bot_name.py`. `ruff check .` and
  `ruff format --check .` with the pinned ruff 0.17.3. CI runs the rest.
* **Not verified.** (a) Against the **real meeting 39 evidence**: the fixture has the numbers, not the real candidate
  lists, so whether the `disagrees` guard lets tags 0, 2, 5, 6 through on the real data is only known from the dry run.
  (b) The **second request to the real Skribby** (`with-speaker-events`): the same call is already used for the
  evidence and its answer was saved for 39, but this code path was never run against Skribby. (c) The timeline is
  **Meet's speaking indicator**, "not proven live" except for meeting 39, where the clock check passed: it lags, lights
  for a cough, and lights two people at once; the clock fit is a whole-seconds search over a recording of a single meeting.
  (d) Zoom and Teams: the evidence of those has not been read for timelines at all; the rule runs for any
  Skribby meeting that has events, and the guards are the only protection. (e) The two thresholds are one meeting's data.
  (f) **Naming does not fix lines inside a tag.** A few percent of the lines inside a tag still belong to someone
  else because the model's own splitting is imperfect (Meet's overlap for tag 5, named for the person with 449 s,
  also shows 14.4 s and 13.8 s of two other people speaking; some of that is people talking over each other, some may be
  lines the model put in the wrong tag, and nothing here can tell which); the timeline names a *tag*, it does not re-split it.

## Operate / roll back

* **Off:** `NAME_BY_MEET_TIMELINE=false` in `.env`, then `docker compose up -d --force-recreate web worker scheduler` (a restart does not re-read `.env`). Tags go back to
  the three earlier rules, and no extra request is made. A meeting already named keeps its names.
* **Revert:** revert the PR; nothing else changed (no migration). `speakers_incomplete` goes back to "someone is named".
* **Read a result:** `docker compose exec -T web python scripts/skribby_segments_report.py ID` (changes nothing; prints what
  people said, do not paste it anywhere public). Tag headers show `[meet_timeline]`; the last section says what the
  rule does to each unnamed tag and why not.

### Server steps for meeting 39 (after this PR is merged and deployed; Adebayo runs them, one block each)

**What this is for, in plain words.** The text of meeting 39 has six speakers shown as "Unknown speaker". The saved copy
of Skribby's answer (kept until about **2026-11-24**) holds Meet's own record of who was speaking. This reads it
again with the new rule, rewrites the transcript and the notes of that one meeting from it, saves the old ones first in
a private file, and can put them back. **The notes are rewritten by the script itself** (one model call), because a
relabel changes who said what and the old notes credit the old, unnamed speakers; there is no separate step. The
lists (tasks, decisions, questions) are read again from the new notes by the worker a minute later, quietly (no mail). If a
person has ticked, hidden or resolved something whose wording the new notes lose, the script **stops and says so**: do
not add `--force` without telling the lead. The guarded blocks, the roll back and the backup deletion are in
`docs/sop/2026-11-16-rebuild-meeting-from-evidence.md`; use them with `ID=39`.

1. **Look first (changes nothing).** Step 0 of that SOP: line 1 must say `OK: the script is deployed` and meeting 39 must
   be in the table with a `kept until` in the future. To also see what the rule would do **without changing anything**:

   ```bash
   ssh haven-respite 'bash -s' <<'EOF'
   cd /opt/beyric-note-taker
   docker compose exec -T web python scripts/skribby_segments_report.py 39 </dev/null | sed -n '/== What naming from Meet/,$p'
   EOF
   ```

   **A good result:** tags 0, 2, 5 and 6 say `would be named ... [meet_timeline]` and tags 7 and 8 say
   `stays unknown (too_little)`. That output has names but no speech. If a tag says `stays unknown (disagrees)`, `uncorroborated`,
   `contradicted` or `clock_shifted`, tell the lead the line as it is: it is the rule refusing, with its reason.
2. **Dry run (changes nothing, prints no names and no words).** Same as Step 1 of the rebuild SOP with `ID=39`:
   `docker compose exec -T worker python scripts/rebuild_meeting_from_evidence.py 39 </dev/null`. **A good result:** no
   `REFUSED`; the tag lines show `named [meet_timeline]` for the tags above and `NOT named [... timeline: too_little]` for the two
   weak ones; `speakers named: before 3, after 5`; `lines credited to no one` goes down; the last line says `DRY RUN: nothing was changed`.
3. **Apply.** Step 2 of the rebuild SOP with `ID=39` (`--apply`). Keep the backup path it prints. Then Step 3 (a few minutes later) and open
   the meeting and read it: each speaker's lines should be that person's (some lines inside a tag can still be another's, see Verification (f)).
4. **If it reads wrong:** Step 4 of the rebuild SOP puts the old text and notes back. Then tell the lead what was wrong (a wrong name is the thing
   to catch) and set `NAME_BY_MEET_TIMELINE=false`.
5. When happy, Step 5 deletes the backup (it holds what people said).

## Follow-ups

* Read the evidence of the next real multi-speaker meeting: did `meet_timeline` name the right person on every tag it named?
  (`scripts/skribby_segments_report.py`, header `[meet_timeline]`.) Then reconsider 30 s / 3.0x with more than one meeting.
* A keep-in-the-notes link between a tag and the lines the model mis-split is out of scope: the real fix would need the speaking
  intervals to re-split a tag (a line-level use of the same timeline), a larger design.
* Zoom and Teams timelines have not been read; check one before trusting them.
* Meetings already stored keep their old `speakers_incomplete`; only a rebuilt or newly processed meeting gets the new rule.
* `speakers_incomplete` threshold 5% is one meeting's reasoning; revisit with the first notices in the field.

## Update 2026-11-23: what changed around this rule

Elimination is now a chain and a second route, `how: meet_exclusion`, names the last open tag when exactly one Meet
participant is left over (`docs/sop/2026-11-23-speaker-name-chain.md`). What this SOP promises still holds: `MIN_RATIO`
stays 3.0 (meeting 39's weak tags stay unnamed; its fixtures pass unchanged), the same gates (readable events, checked
clock, at least one agreeing named tag, no contradiction) come first, and the exclusion route runs only behind them. The
timeline rule's "the tags the other rules left unnamed" now includes fewer tags, because the chain names more of them first; a chain name that this timeline does not positively support is not given, so the tag stays open and this rule can name it (an over-split person).

## Update 2026-11-25: the zero-anchor meeting

The corroboration gate ("a meeting where nothing was named gets no timeline names") is unchanged. A meeting where nothing
was named because each tag lists one different person is named by `how: one_to_one` instead, which uses this module's
`verdict` only as a veto and adds `Reading.lit`/`Reading.shown` for the participant checks
(`docs/sop/2026-11-25-speaker-naming-one-to-one.md`).

## Update 2026-11-25 (later): corroboration by the tag's own candidates

The corroboration gate ("at least one named tag the timeline agrees with") still stands as written, with one exception
(`docs/sop/2026-11-25-speaker-naming-relaxed.md`, gate 9): when it fails as `uncorroborated`, a tag is still named if its own
candidate list holds the person Meet's timeline leads it with (same 30 s, 20% and 3.0x, same clock check), and the exclusion
route does not run, and it is skipped for a tag the one-to-one rule refused (`extra_speaker`, `not_in_call`, `contradicted`). `contradicted` still stops everything. The line "A meeting where nothing was named gets no timeline names"
is therefore true only for a tag whose candidates do not hold Meet's leader.
