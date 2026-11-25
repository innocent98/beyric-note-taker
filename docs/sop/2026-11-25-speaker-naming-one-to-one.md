# SOP: name every tag when Skribby named none but each tag lists exactly one, different, person (`how: one_to_one`)

**What shipped.** A new naming route, `how: one_to_one`, in `app/skribby.py::_one_to_one` (called at the end of
`_earlier_rules`). In a meeting where **Skribby named no tag itself**, if **every** tag lists exactly one name on **every**
segment, the names are all different, and Meet's participant list (when we have it) agrees, every tag takes its name.
Branch `feature/speaker-naming-one-to-one`; the PR number is on the checklist. **No migration, no new setting** (the
existing `SKRIBBY_NAME_BY_ELIMINATION` is the one switch), no endpoint or FE guide change (`how` lives in the saved
evidence JSON only). It goes live on deploy for new meetings; an existing meeting changes only when it is rebuilt (below).
**Nothing here is proven live: the rule is proven on made-up fixtures of meeting 46's shape. It is proven on the real
meeting only when Adebayo runs the rebuild below.**

## Why

Meeting 46 (Google Meet, 2026-11-25, two people). Read from `skribby_segment_evidence` (no words): tag 0 listed
`["Adebayo Tosin"]` on all 85 of its segments, tag 1 listed `["Oluwaseyi Daniel"]` on all 84, and Skribby set
`speaker_name` on neither. Stored: both tags `how: unknown, note: unresolved, timeline: uncorroborated`,
`speakers_incomplete` true, so the transcript had no speaker names. Meeting 45 (same shape, but Skribby gave both names)
worked.

Two rules each refused it, for a reason that was right when it was added:

| Rule | What it needs | Why it was added |
|---|---|---|
| Sole candidate (`docs/sop/2026-11-16-skribby-name-sole-candidate.md`, `any(given.values())`) | at least one OTHER tag Skribby named itself | meeting 32 (3 voices merged into 2 tags) and meeting 29 (elimination named someone who was not in the call): one name on every segment is not proof when nothing in the meeting shows Skribby's naming works |
| Meet timeline (`_meeting_gate`, `docs/sop/2026-11-17-name-by-meet-timeline.md`) | at least one already-named tag whose person Meet's timeline puts on top | proves Meet's names and clock line up in this meeting |

With **zero** named tags both rules have no anchor. A two-tags / two-people / one-name-each meeting, the easiest
meeting there is, stayed blank.

## How, and what was decided

### The rule

`_one_to_one` names the meeting only when **all** of these hold (any failure names nothing and the older behaviour is
unchanged):

| Condition | Why |
|---|---|
| No tag carries a `speaker_name`, and there are **at least two** tags | When Skribby named a tag the older rules own the meeting (`sole_candidate` and the chain). One tag alone cannot tell one person from a crowd the model merged into it |
| **Every** tag has exactly one candidate on **every** segment, at least `MIN_SOLE_CANDIDATE_SEGMENTS` (3) segments, and it is not the bot | The sole-candidate evidence, required of all tags, not one. One tag that fails (two names, none, the bot, two lines, a list too long to read) blocks the whole meeting: a meeting that has an unreadable tag is not the clean shape this rule is for |
| No two tags share a name, so tags and names are as many | Two tags with one candidate is a shared claim (`unresolved` as before) |
| **Meet's participant list, when the kept events hold it** (read whether or not `NAME_BY_MEET_TIMELINE` is on; that switch only controls the older timeline-naming rules): every name is in the call (`not_in_call`) | meeting 29's failure (a name that was not in the call), and the follow-up written in the sole-candidate SOP. It needs the names only, so it also runs when the size cap dropped every speaking event (`events_dropped`) |
| ...and **nobody else was lit for `meet_timeline.MIN_EXTRA_SECONDS` (10 s) or more** (`extra_speaker`) | the meeting-32 signature: a person who spoke and has no tag of their own means voices were merged into fewer tags. Meeting 32's third voice spoke about 30 s inside another's tag, so the timeline rule's 30 s floor would have missed it; 10 s is a veto floor (it refuses, it does not name), a sentence or two, not a cough. A person in the call who is silent (an observer) and the note taker are **no veto**: it uses seconds lit, not the head count |
| ...and **no tag has a different person clearly on top** of Meet's timeline (`contradicted`, `meet_timeline.verdict`, the bot in the ranking). When the clock check refuses the timeline (`clock_shifted`) it is read at zero shift **and** at the shift that fits best, and either clearly pointing elsewhere refuses | the two signals disagree, so one is wrong and we do not know which: swapped names look exactly like this. An unclear timeline (a tie, too little) is no veto: only a clear lean the other way is |
| The elimination switch is on | With `SKRIBBY_NAME_BY_ELIMINATION=false` every tag records `elimination_off` and `would_be` holds the name, exactly like elimination and the sole candidate |

Without events (Zoom, Teams, a failed fetch) **the shape alone decides**, which is design A of the brief. When events
were kept but a check could not run, the tags are named and say which checks were skipped, in an `unchecked` key (comma-
joined: `extra_speaker` when nobody has a speaking interval, `contradicted` when the clock cannot be placed or there is no
timeline, all three when the participant list is unreadable); the report and the rebuild dry run print it as `Meet checks
not run: ...`. **In the live path the events are fetched when `NAME_BY_MEET_TIMELINE` or `SPEAKER_LINE_CORRECTION` (with
`TRANSCRIPT_SOURCE=skribby`) is on** (`SkribbyProvider._events_for_naming`, unchanged, both default on): with both off live,
no events reach naming and the shape alone decides; the rebuild script and the report always read the saved events. A tag the rule refused on the
Meet checks gets `one_to_one: <reason>` next to its `unresolved` note in the saved evidence; the report and the rebuild
dry run print it (`one-to-one naming refused: contradicted`). A meeting whose shape does not fit says nothing extra (no
new key), so saved evidence of every other meeting is byte-identical to before.

`how` is a new value, `one_to_one`, beside `given`, `elimination`, `sole_candidate`, `meet_timeline`, `meet_exclusion`,
`unknown`. Consumers checked: nothing branches on `how` (`_segments`/`_tag_names` read `name`; the evidence is JSON with
no enum; `speaker_correction` reads names only; the report and the rebuild script print it).

### Designs evaluated

* **A, one-to-one on the structure alone.** Right shape, but it is exactly the shape the older rules were made to
  distrust (meeting 32: two tags, one name each is what a merged-voices meeting could also look like), with no check
  that the names are people in the call. Kept as the core, not used alone where Meet's data exists.
* **B, a sole candidate corroborated by Meet's timeline counts as the anchor.** Rejected as the main route: it would
  not name a meeting whose timeline is weak (a shifted clock, no events, Zoom/Teams, a quiet person) even though the
  structure is perfect, and it changes the anchor semantics of two existing rules (so meeting 37/38/39's tests and
  SOPs would need re-reading). Its useful part, Meet's lean, is used here as a veto.
* **C, A plus a veto on a clear timeline leader who is someone else.** Adopted, with two more vetoes from the same
  participant list (`not_in_call`, `extra_speaker`), because swapped names are only one of the ways this shape is wrong;
  an unmatched person who spoke is the other, and it is the meeting-32 shape that the brief says must stay unnamed.
* **Also rejected:** naming when only some tags are clean (a tag we cannot read makes the meeting a different shape: the
  `unresolved` tag could be the one merged crowd); a single tag with one candidate (no way to tell a merged crowd);
  a new switch (`SKRIBBY_NAME_BY_ELIMINATION` already covers "a name Skribby did not give").

### The risk that stays, stated plainly

If the real speaker is not on Skribby's list and the list is two other people, the rule would name both tags after
people it believes are in the call. Without events nothing catches that. With events, `not_in_call`, `extra_speaker`
and `contradicted` catch most of it, but the participant check compares names **exactly after cleaning**: if Meet shows
a different spelling than Skribby listed (`Ada` versus `Ada Obi`) the rule refuses (`not_in_call`), the safe direction,
at the cost of a blank on that meeting; the dry run says which. Meet's indicator lags and lights for coughs; the 10 s
for `extra_speaker` is a veto floor chosen from meeting 32's ~30 s third voice, not tuned further.

## What's involved

| File | Change |
|---|---|
| `app/skribby.py` | `_one_to_one` (new); `_resolve_tags` reads the kept events for it whether or not the timeline switch is on (`kept`); `_earlier_rules` calls it after the chain, records picks as `chosen`/`how: one_to_one` (so `elimination_off` + `would_be` and the timeline rule's agreement check work as for every other route) and writes `one_to_one: <reason>` on a vetoed tag |
| `app/meet_timeline.py` | `Reading.lit` (seconds each participant was lit in all, no clock needed), `Reading.lit_known`, `Reading.shifted_cells` (cells at the best shift, when the clock is refused as `clock_shifted`), `MIN_EXTRA_SECONDS`; `Reading.shown` is now also filled when the clock is refused (`clock_unplaced`, `no_times`) and when no participant has a usable speaking interval (`events_dropped`), so the participant checks run there. Nothing that read `shown` before runs on a refused reading |
| `scripts/skribby_segments_report.py`, `scripts/rebuild_meeting_from_evidence.py` | print `one-to-one naming refused: <reason>`; the report's header text names `one_to_one` |
| `app/models.py` | docstring lists the `how` values |
| `tests/test_one_to_one_naming.py` (new, 40), `tests/test_skribby_provider.py` | the old two-unnamed-tags test split: shared candidate stays unnamed, different candidates are now `one_to_one` |
| `docs/sop/2026-11-16-skribby-name-sole-candidate.md`, `2026-11-17-name-by-meet-timeline.md` | update lines: the zero-anchor meeting is now named by this rule |
| `docs/checklist/master-checklist.md` | addendum |

## Verification

* **Tests** (`tests/test_one_to_one_naming.py`, 40, made-up names, placeholder words; the timeline fixtures are the ones of
  `tests/test_meet_timeline_naming.py`): meeting 46's shape (5 segments each, and laid out with Meet's seconds) names
  both tags `one_to_one`; with Meet agreeing; with no events; three tags for three people; a silent third person in the
  call; a lit note taker; the transcript lines carry the names; **meeting 32's shape** (a third person lit 60 s, and 29 s,
  inside one tag) stays unnamed with `extra_speaker`, and its realistic form (the merged tag lists two names) stays
  unnamed; the 10 s floor (9 s and 9.9 s of a third voice name, 10 s and 30 s refuse); a tag whose candidate is the bot,
  a tag with no candidate, a list over `MAX_CANDIDATES`, two tags with the same single candidate, a tag with two
  candidates, fewer than 3 segments (and exactly 3 names), a name missing from one segment, one tag only: all unnamed;
  elimination off: `elimination_off` + `would_be`; a candidate not in the call (`not_in_call`), also with the timeline
  rule off and with every speaking event dropped; swapped names (`contradicted`), also with a refused clock (read at the
  best shift too, one test builds a reading where only the shifted cells point elsewhere) and with the timeline rule
  off; events dropped and a clock that cannot be placed name by shape and record `unchecked`; an unclear timeline; a
  name spelled in capitals or with spaces; a meeting where Skribby named a tag goes to `sole_candidate` as before.
* **Each guard fails without it** (undone one at a time by a script, then restored): `not_in_call` (1 test fails),
  `extra_speaker` (2), the 10 s floor (2), `contradicted` (1), contradiction at the best shift (1), contradiction under
  a refused clock (3), vetoes reading events with the timeline switch off (2), the `unchecked` record (2), `lit_known`
  (1), names kept when events are dropped (2), the two-tags minimum (1), distinct names (1), bot/3-segment guard (3),
  the "Skribby named no tag" gate (1).
* **Bounded runs on this Mac:** `tests/test_one_to_one_naming.py`, `test_meet_timeline_naming.py`,
  `test_skribby_provider.py`, `test_speaker_correction.py` all pass; the
  Postgres-backed tests of `tests/test_skribby_evidence.py` and `tests/test_rebuild_meeting_from_evidence.py` error here
  only because this Mac runs no Postgres (connection refused on 5432): they are left to CI. `ruff check .` and
  `ruff format --check .` with the pinned ruff 0.17.3 (CI runs no type checker).
* **Not verified:** against the **real** evidence of meeting 46 (its participant names versus Skribby's candidate
  spellings, the bot's display name in the list, the clock check on that recording): only the dry run below can say; any
  Zoom or Teams meeting (no events there, the shape alone decides, never seen live).

## Operate / roll back

* **Off:** `SKRIBBY_NAME_BY_ELIMINATION=false` in `.env` and `docker compose up -d --force-recreate web worker scheduler`
  (a restart does not re-read `.env`). That turns off every derived name, not this one only; tags record `would_be`.
* **Revert:** revert the PR; no migration. Tags already named keep their names in stored meetings until rebuilt.
* **Rebuild meeting 46 (after this PR is merged and deployed; Adebayo runs them; the saved evidence is deleted about
  2026-12-02, so do it before then).** Plain words: the text of meeting 46 has no speaker names. The saved copy of Skribby's
  answer is read again with this rule, the transcript and notes of that meeting are rewritten from it, the old ones are
  saved first in a private file and can be put back. Full guarded blocks, the roll back and the backup deletion are in
  `docs/sop/2026-11-16-rebuild-meeting-from-evidence.md` (use them with `ID=46`). The two commands:

  Dry run (changes nothing, prints no names and no words):

  ```bash
  ssh haven-respite 'cd /opt/beyric-note-taker && docker compose exec -T worker python scripts/rebuild_meeting_from_evidence.py 46 </dev/null'
  ```

  **A good result:** no `REFUSED`; both tag lines say `named [one_to_one]` (a `Meet checks not run: ...` suffix means
  it was named with that check skipped: read it to the lead); `speakers named: before 0, after 2`; `lines credited to no
  one` goes to 0; the last line says `DRY RUN: nothing was changed`. If a tag says `NOT named [unknown, unresolved,
  one-to-one naming refused: not_in_call|extra_speaker|contradicted, timeline: ...]` (the real shape of the line: `how`,
  then the note, then the refusal, then the timeline reason), send the line as it is to the lead: that is the rule
  refusing, with its reason (a spelling difference between Meet's list and Skribby's is the likely `not_in_call`).

  Apply (only after a good dry run; keep the backup path it prints):

  ```bash
  ssh haven-respite 'cd /opt/beyric-note-taker && docker compose exec -T worker python scripts/rebuild_meeting_from_evidence.py 46 --apply </dev/null'
  ```

  Then Step 3 of the rebuild SOP a few minutes later, open meeting 46 and read it: each person's lines should be theirs.
  If it reads wrong, Step 4 of that SOP puts the old text and notes back; tell the lead what was wrong (a wrong name is
  the thing to catch) and set `SKRIBBY_NAME_BY_ELIMINATION=false`.
* **Read a result any time:** `docker compose exec -T web python scripts/skribby_segments_report.py ID` (prints what people
  said: do not paste it anywhere public); tag headers show `[one_to_one]`.

## Follow-ups

* Read the evidence of the next two-person Meet meeting where Skribby gave no names: did `one_to_one` name both correctly?
* If `not_in_call` ever blocks a real meeting because of a spelling difference between Meet's list and Skribby's, compare
  the two spellings in the report and decide on a safe normalisation (not guessed here).
* Zoom and Teams have no events, so the shape alone decides there. Check the first real one before trusting it.
* A one-to-one rule for meetings with a partly clean shape (some tags clean, one tag with two names) was rejected for
  now; revisit with evidence.
