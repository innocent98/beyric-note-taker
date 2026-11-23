# SOP: line-level speaker correction (a line follows Meet's timeline when exactly one person was lit)

**What shipped.** A Skribby segment now moves to the person **Meet's own speaking timeline** shows speaking over it,
when **exactly one person was lit** and that person is not the one its diarization tag says. It runs in the pipeline
for Skribby transcripts (`TRANSCRIPT_SOURCE=skribby`), after the tag names are derived and before the speaker
transcript and notes are built, and in the rebuild script. **ON by default** (Adebayo, 2026-11-19: no dark shipping,
no trial scaffolding); one emergency kill switch, `SPEAKER_LINE_CORRECTION` (default `true`). Branch
`feature/line-level-speaker-correction`; the PR number is on the checklist. **No migration, no new column, no endpoint,
no screen.** Design note: `docs/superpowers/specs/2026-11-18-line-level-speaker-correction-design.md` (its section 4 is
superseded by the rule below).

## Why

Naming a tag cannot fix a tag that holds other people's voices. Measured with
`scripts/speaker_reattribution_report.py` (docs/sop/2026-11-18-speaker-reattribution-report.md), by Meet's own signal:
meeting 40, 6% of the seconds sat under the wrong person (all of it the host's speech tagged to two other people in
the first minutes); meeting 39, 62% (six of nine tags unnamed); meeting 38, clean, 0.2%. Adebayo checked by hand
that the moved lines in 40 and four sampled lines in 39 were really said by the person Meet names.

## The rule (Adebayo's decision 2, 2026-11-19)

Not "a share of the line": a segment contains pauses, so Meet's light covers only part of a long one (in meeting 39
only 4 of 15 sampled moves reached 80% lit, yet all four he checked were right). A segment moves to person P when
**all** hold (`app/speaker_correction.py`, `assess` and `correct`):

| Condition | Value |
|---|---|
| Meet's events exist, are readable, are placed on the transcript's clock and no shift of Meet's clock makes the segments agree clearly better with the people they are named for | `speaker_correction.clock_check`, a **hard gate**: for each shift (whole seconds up to 30 either way, half seconds up to 5) it sums the seconds each named segment's person (matched by the same exact name) was lit over it. The meeting is flagged (`clock_shifted`, **nothing moves in it**) only when the best shift is **at least `CLOCK_MIN_SHIFT` = 2 s AND** raises that agreement by more than 10% (`CLOCK_GAIN`). **Why 2 s:** the indicator itself lags by about half a second to a second, so a consistent shift under 2 s is normal lag, and flagging it would stop the correction on every meeting; 2 s is the first size that is clearly not lag, and it puts the first seconds of every turn under the previous speaker. It is a judgement from those lag sizes, **not measured on a real meeting**: the first live log lines (`clock best shift`) will say if it is wrong. The naming rule's union-of-everyone check cannot see an offset of a couple of seconds in a conversation without gaps; this one can. Tests: offsets of 2, 3, -2, -3 and 10 s are flagged; lags of 0, 0.3, 0.5, 1, -0.5, -1 and 1.5 s are not; the 38/39/40-like fixtures are not. A smaller steady offset (under 2 s) is NOT caught and is read as lag. With no named segment it falls back to the union against all segments. The best shift and its gain are in the evidence summary and the log line |
| P was lit at least | `MIN_SECONDS = 1.0` s over the segment |
| No runner-up reaches | `AMBIGUOUS_RATIO = 0.25` of P's seconds (a runner-up under `MIN_SECONDS` never counts) |
| P differs from the person the segment carries now | the segment's own `speaker_name`, else its tag's name, else nobody ("Unknown speaker", which a clear speaker moves to somebody). **Names are compared by whole words** (case, accents and punctuation ignored; `name_words`): `same_person` says yes when every word of one name is among the other's. It is used ONLY to decide that a segment already carries the lit person (a false yes leaves a line where it is, the safe direction), **never to choose where a line goes**. "Ann" is not "Joanne", "Tomi" is not "Tomiwa", "Ade" is not "Adebayo" |
| P is a name already in the meeting | `resolve_label`: the destination takes an **EXACT** match only: the same words as a name a tag or segment already uses (case, accents, punctuation ignored), and it takes that spelling so the moved line joins that person's turns. **Anything else is `unmatched` and the line stays**, including a name that merely *contains* the lit one: if Meet lights "Obi" and the tags are "Ada Obi" and "Tunde Bello", the line stays under Tunde, it is not handed to Ada Obi (a second review finding: a first version took a subset match in any word position). Same for "Ada" against "Ada Obi" and "Ade" against "Zoë Obi-Ade". Exact matching loses little: tag names come from the same Meet names, so the same person's spelling normally matches. Cost: a person with no tag name at all, or whose tag is spelt differently ("Bo" against "Bo Example"), is not given their lines |

Never moves: two or more lit (**ambiguous**), nobody lit (**no signal**), the note taker on top (**bot**), a lit person with no matching name here (**unmatched**), not timed. A participant who left and rejoined
is one person (same grouping as naming). The label a moved line gets is the spelling a tag already uses for that person
(so it joins that person's turns instead of making a second spelling), else the name Meet shows.

`MIN_SECONDS` and `AMBIGUOUS_RATIO` are the report's defaults and are **imported by the report from the same module**,
so the measurement and production cannot drift. Dropped from the design note's section 4 by this decision (not built):
the 2 s / 0.34 ratio start values, the 35%-of-the-line share, "protect people with no light", "at least 4 words" and
the A-B-A-B smoothing. They were guesses meant to be tuned by the report; the decided rule replaces them.

## How

| Piece | What it does |
|---|---|
| `app/speaker_correction.py` | Pure functions, no database, no settings, no logging. `place` puts Meet's intervals on the transcript's clock (the bot left out); `assess` classifies each segment (`agrees`, `move`, `ambiguous`, `no_signal`, plus the `not_timed` count); `correct` applies the clock gate and returns a `Correction` (`moves` = item position to label, `refused`, counts, `summary()` numbers only) |
| `app/skribby.py::_labelled` / `_segments` | `_resolve_tags` once, then `_correct_lines` (kill switch + `TRANSCRIPT_SOURCE=skribby` + events present, all wrapped so any exception keeps the tags' names and logs the exception class only), then the segments are built; a moved segment gets its label there. `map_bot` passes the same `Correction` to `_evidence` |
| Evidence (`skribby_segment_evidence.items`, 7 days) | A moved item keeps `speaker` (the original tag) and gets `corrected_to` (the label); the evidence dict carries `correction` = `{moved_segments, moved_seconds, segments, refused}`. **Only when the correction ran** (events existed): a meeting with no events has neither key, so its evidence is byte-identical to before. Numbers and labels only, never text |
| `app/pipeline.py::_log_correction` | One INFO line per meeting: `meeting N: speaker correction moved X of Y segments (S s)` plus `; refused: clock_shifted` when the clock check stopped it. Nothing is logged for a meeting it did not run for. This is the durable record (the evidence lives 7 days); `docker compose logs worker \| grep "speaker correction"` |
| `app/skribby.py::_events_for_naming` | The second Skribby request (speaker events) is now made when **either** `NAME_BY_MEET_TIMELINE` is on **or** the correction is on with `TRANSCRIPT_SOURCE=skribby`, so turning naming off cannot silently turn the correction off |
| `scripts/speaker_reattribution_report.py` | Imports the constants, `classify`, `assess` and `place` from the module. **Tag names are now read again with the current naming rules by default** (as production and the rebuild do); `--saved-names` reads the names saved with the evidence, which reproduces the first runs. Prints how many segments already carry a `corrected_to` marker |
| `scripts/rebuild_meeting_from_evidence.py` | Applies the same correction (same `_labelled` call). The dry run prints the seven outcomes (agrees, move, ambiguous, no signal, bot, unmatched, not timed) with the same counts as the report (segments and seconds) and, per tag, how many move out. `--apply` prints the moved count. See "Existing meetings" |

**Kept, rejected.** Kept: one kill switch, an exact no-op without events, failure isolation. Rejected, on Adebayo's
rule: percent rollouts, a shadow mode, a default-off flag. Rejected for now: a per-meeting column for the moved counts
(the evidence markers and the log line are enough and need no migration); a note on the page saying a line was moved.

**Where it does not run.** Not for `TRANSCRIPT_SOURCE=own` (our own transcription with model-based labelling keeps its
tags' names). If Skribby's text is not used for a meeting and the pipeline falls back to our own transcription, the
corrected segments are still the hints the labelling step reads; harmless and unmeasured. Teams, Zoom and any meeting
whose speaker events could not be fetched have no events: an exact no-op (proved by a test).

## Existing meetings change only on an explicit rebuild

`docker compose exec -T worker python scripts/rebuild_meeting_from_evidence.py <id>` (dry run), `--apply`,
`--restore FILE --apply`. What changed for it:

* **It is safe to run again, including on a meeting an earlier version already rebuilt (meeting 39).** The old guard
  ("`skribby-rebuilt` means nothing to do") is replaced by `same_turns`: the stored speaker transcript and the text a
  rebuild would build must have the same turns (same people, same order) **and the same words, except respellings**.
  Why not names alone: a line moved to a neighbouring turn leaves the list of names unchanged. Why not word counts: the
  stored text has been through the owner's vocabulary pass (a model call, which cannot be repeated for a comparison)
  and the built text has not; the pass may change counts ("bay rick" to "Beyric"), so for an owner with terms a count
  comparison said "different" on every run and every `--apply` made a new notes call, new rows and a new backup (found
  in review). The pass can only **replace** words (`app/vocabulary_pass.py` refuses a pure insertion or deletion), while
  a line that moves is a pure deletion in one turn and a pure insertion in another, so `difflib` opcodes tell them
  apart. So meeting 39 rebuilds once more (its first rebuild had no correction), then is left alone; 38 and 40 rebuild
  once; a re-run of any of them changes nothing, makes no model call and saves no second backup (tests, including a
  vocabulary pass that changes the word count). Known limit: a rebuilt text whose only difference is a word the pass
  changed AND a one-word line that moved in the same turn cannot be told from a respelling: not seen, not tested.
* **Restore** is unchanged: the backup is taken before every apply; `--restore` puts the previous text and notes back
  (and `transcript_source`, so a restored 39 is `skribby-rebuilt` again and can be rebuilt again).
* **A rebuilt meeting whose evidence has expired** still says "already rebuilt ... nothing to do" (exit 0), not a refusal.
* **Citations and row numbers.** Nothing in the database stores a row number: Ask works out its row citations from the
  displayed transcript at question time (`app/ask.py::transcript_rows`) and its answers are never stored; a task's or
  decision's `quote` is a text quote, re-read from the **new** notes by the worker after the rebuild (quiet: no badge, no
  mail). So after a rebuild nothing points at a stale row. The only thing that can still show old row numbers is a
  browser tab with an Ask answer from before the rebuild (client side, gone on reload). Turn boundaries do change
  (moved lines can split or merge turns), so row numbers of the displayed transcript differ from before: that is the
  point of the rebuild.
* **Kill switch and rebuild.** With `SPEAKER_LINE_CORRECTION=false` the rebuild builds without the correction and the
  dry run says "not applied: switched off".
* Meeting 40's evidence is deleted around **2026-11-25**; 39 around 11-24; 38 around 11-23.

## What's involved

`app/speaker_correction.py` (new), `app/skribby.py` (`_correct_lines`, `_labelled`, `_segments`, `_evidence`,
`_safe_evidence`, `map_bot`, `_events_for_naming`), `app/pipeline.py` (`_log_correction`, one call), `app/config.py`
(`speaker_line_correction`), `.env.example`, `deploy/README.md`, `scripts/speaker_reattribution_report.py`,
`scripts/rebuild_meeting_from_evidence.py`. Tests: `tests/test_speaker_correction.py` (new),
`tests/speaker_correction_fixtures.py` (new, shared builder), additions in `tests/test_rebuild_meeting_from_evidence.py`
and `tests/test_skribby_evidence.py`. No `docs/fe-integration-guide-*.md` change: the transcript is the same string of
`[Name]: words` turns, no field, endpoint or status code changed, only which name a moved line carries.

## Verification

Bounded, on the Mac, against a scratch Postgres database (removed afterwards): `ruff check`, `ruff format --check` (the
pinned 0.17.3), and the named files: `test_speaker_correction`, `test_speaker_reattribution_report`,
`test_rebuild_meeting_from_evidence`, `test_skribby_evidence`, `test_meet_timeline_naming`, `test_skribby_provider`,
`test_transcript_source`, `test_dockerfile_scripts` (584 tests), plus `test_provider_contract`, `test_bot_name`,
`test_skribby_live_check`, `test_skribby_e2e` (293). CI runs the whole suite on the pushed head (its result is on the PR).

What the tests prove. **Read this first: the 40, 39 and 38 tests are not real evidence.** Their fixtures are synthetic
meetings (invented names and words, `tests/speaker_correction_fixtures.py`) **built so that** their counts equal the
numbers from the three reports (each segment is lit fully by one person, 40 s apart, no lag, no crosstalk at the
edges); the tests then check that the rule reproduces what was laid out. That proves the counting, the label
resolution, the gate and the no-op paths are right; it does **not** prove the rule gives those numbers on the real
meetings. Only the server dry run on real evidence does that.

* **The counts built in:** meeting 40's 170 agree / 32 move / 19 ambiguous / 181 no signal (402 segments), with exactly
  the 32 built moves made to the person built; meeting 39's 128 moves out of three unnamed tags (its other counts are
  invented); meeting 38's 2 moves of 347 segments with every word still there in the same order.
* **Edge cases:** one person lit moves; a tie and a 50% runner-up stay (ambiguous); a 3% runner-up (lag) still moves; a
  runner-up under 1 s never counts; under 1 s lit is no signal; the note taker alone on top stays (`bot`), beside one
  person makes it ambiguous, and the report agrees; blank and "Unknown speaker" participants are skipped; untimed or
  untagged segments stay; **names: Ann/Joanne, Tomi/Tomiwa, Ade/Adebayo, Ada/Adaeze never match; the destination is an exact name
  only (accents and case ignored): Obi lit with tags "Ada Obi" and "Tunde Bello" stays under Tunde, "Ada" does not go to
  "Ada Obi", "Ade" does not go to "Zoë Obi-Ade", an unknown name moves nothing; "Ada" under a tag and "Ada Obi" lit
  agrees (the line stays)**; a moved line takes the spelling a tag already uses; an unnamed tag is credited; a segment's own name is what
  is compared; a rejoin is one person; a 60 s segment lit for 9 s still moves; a steady clock offset of 2, 3, -2, -3 and
  10 s moves nothing and is flagged with its best shift, a lag of up to 1.5 s does not; a 2,000-segment meeting is read in
  under 8 s.
* **Off equals today:** with `SPEAKER_LINE_CORRECTION=false` the segments **and the stored text** are identical to a
  verbatim copy of the old code's output (kept in the test fixtures), on all three meetings; the same with
  `TRANSCRIPT_SOURCE=own`.
* **No events is an exact no-op:** segments and evidence equal the output without the feature (no `correction` key, no
  marker), for no record and an empty one.
* **Failure isolation:** a correction that raises keeps the uncorrected segments, the meeting is still `completed`, the
  log holds only the exception class (no word, no name); garbage events never stop a meeting.
* **Report parity:** the report script's own `report()` and the rebuild dry run print the same counts and seconds for
  all seven outcomes on all three meetings; the dry run prints no name and no word.
* **Rebuild:** apply on 40 moves 32 and a second run does nothing; a meeting already `skribby-rebuilt` without
  correction (39) rebuilds once (128 moved) then does nothing, restores, and rebuilds again; 38 changes two lines; off
  equals the old text byte for byte; no events and a shifted clock are said in a line and move nothing; a vocabulary pass that changes the word count does not make a
  second run rebuild (the test that caught the review's second finding), while a meeting whose lines would move is still
  rebuilt after the pass.

**Not verified:** nothing has run against real evidence on the server (none is on the Mac) or live in a meeting. The
"128 moves" of meeting 39 was measured with the names saved at processing time, so the report now (current names) will
print **fewer** for 39: tags the timeline can now name are not "Unknown speaker -> X" any more. **`--saved-names` is NOT
verified to reproduce 128**: the comparison now uses a segment's own `speaker_name` first, names are matched by whole
words and a lit person with no name in the meeting is `unmatched`, so the first runs' numbers can differ even with the
saved names. Treat it as "the names saved with the evidence", not as a reproduction.

**Speed (measured on the Mac):** the review feared tens of seconds per poll. `correct()` takes 0.26 s for 800 segments
and 1.4 s for 2,000 segments with eight people (a 24-hour synthetic timeline: more intervals than any real meeting);
the end times are worked out once instead of at every overlap, and the clock fit runs once, not twice (the naming
rule's own fit is a separate step). A guard test fails if it takes over 8 s. The Meet clock placement is still the same not-proven-live assumption as the naming
rule (the clock gate is the guard).

## Operate / roll back

* **Deploy:** merge; the deploy restarts the containers and the correction is on (default). No `.env` change.
* **After deploy, read meetings 38, 39, 40 (oldest first), one combined command** (the report's counts and the
  rebuild's dry-run counts must match, line for line; both read names with the current rules):

```bash
ssh haven-respite 'bash -s' <<'EOF'
cd /opt/beyric-note-taker
for id in 38 39 40; do
  echo "=============== meeting $id: report ==============="
  docker compose exec -T web python scripts/speaker_reattribution_report.py $id
  echo "=============== meeting $id: rebuild dry run ==============="
  docker compose exec -T worker python scripts/rebuild_meeting_from_evidence.py $id
done
EOF
```

* **Rebuild 40 (before 2026-11-25), and undo it:**

```bash
ssh haven-respite 'cd /opt/beyric-note-taker && docker compose exec -T worker python scripts/rebuild_meeting_from_evidence.py 40 --apply'
# it prints the backup path; to put the old text and notes back:
ssh haven-respite 'cd /opt/beyric-note-taker && docker compose exec -T worker python scripts/rebuild_meeting_from_evidence.py --restore <the path it printed> --apply'
```

  The backup holds what people said (private, audio volume): delete it once the result is checked.
* **After each new live meeting:** `docker compose logs worker | grep "speaker correction"` (numbers only), or the report
  on the meeting within 7 days ("Already applied when this meeting was processed").
* **Kill switch (emergency only):** `SPEAKER_LINE_CORRECTION=false` in `.env`, then
  `docker compose up -d --force-recreate web worker scheduler` (a restart does not re-read `.env`). New meetings keep
  their tags' names; meetings already corrected stay as they are until restored with the rebuild's `--restore`. A value
  that is not `true` or `false` stops web, worker and scheduler at startup.
* **Revert the PR** to remove the feature entirely (no migration to undo).

## Known limits (no code; from the Opus review)

* **"Exactly one person lit" means exactly one *visible* person lit.** `meet_timeline.speaking_intervals` drops an
  interval that never closes (a lost `stopped-speaking`) and one longer than 600 s, and a person with no events at all
  (a dial-in, a room mic, a nameless participant) is invisible. Those periods count as "nobody else lit". Example: the
  host's `stopped-speaking` is lost, so the host's span is dropped, and another person's 1 s laugh then moves the host's
  lines to them. A guard (treat a dropped interval's period as unknown and block moves inside it) is not built.
* **One person under two Meet names is two speakers.** "Ada Obi" and "Ada's iPhone" are two keys. If only the phone is
  lit over Ada's line, the phone name is "Ada's iPhone" and a tag carries exactly that name, the line moves to it; if no
  tag does it is `unmatched` and stays (the destination must be an exact name). Two different people who use the same display name are one
  person (same as the naming rule).
* **Short lines and indicator lag** can be moved to the previous speaker (design note section 6): accepted, with the
  kill switch.
* **The rebuild cannot see the name Skribby reports for the bot in its body** (the evidence does not keep it), so a bot
  under a display name other than the stored one or the account's name stays in the timeline there; it has no events
  in practice, so it is never lit, and the bot rule above is tested with the names the rebuild does know.
* **A steady clock offset under 2 s is not caught** (it is read as the indicator's normal lag); it puts the first
  seconds of some turns under the previous speaker. An offset of 2 s or more stops the correction for the whole meeting
  (`refused: clock_shifted` and the best shift in the log). The 2 s line is a judgement, not a measurement; read the first
  live `clock best shift` values.
* Expect the first real read to move fewer lines than the report's earlier numbers: the stricter name matching leaves
  a line alone when the lit person's name matches no tag name here.

## Follow-ups

* Adebayo reads the first real meeting after deploy (the log line and the report) and judges a few moved lines by hand,
  as he did for 39 and 40; the rule has not been judged on a live meeting with this exact code.
* Ask Adebayo whether an edge trim (ignore the first and last 0.5 to 1 s of a segment's overlap) is acceptable if short
  lines are seen moving wrongly; it is not built.
* If a shared Meet account or a room mic shows a remote person's line pulled into the room's name, the design note's
  "protect people with no light" rule is the first thing to add back (it was dropped by decision).
* Outcome counts per meeting are not queryable after the evidence expires (7 days); the log line is the record. Add a
  column only if that proves too thin.
* The report's `--saved-names` flag exists to reproduce the earlier numbers; delete it when nobody needs them.

## Update 2026-11-23: more tags arrive named

`_resolve_tags` now also names tags by a chained elimination and by exclusion against Meet's participant list
(`docs/sop/2026-11-23-speaker-name-chain.md`). The correction is unchanged: it receives the same `resolved` mapping, so a
tag named by either route is a name "already in the meeting" for `resolve_label` like any other, and every invariant above
(exactly one person lit, a line only moves to an exact existing name, exact no-op without events) holds as written.
