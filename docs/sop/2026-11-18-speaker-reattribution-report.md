# SOP: speaker re-attribution report (what would move if a line followed Meet's timeline)

**What shipped.** A read-only measurement, `scripts/speaker_reattribution_report.py <meeting_id>`, and a design
note, `docs/superpowers/specs/2026-11-18-line-level-speaker-correction-design.md`, on whether production should
re-credit a segment to the person Meet's own speaking timeline shows over it when that differs from the
diarization tag. Branch `docs/speaker-lines-report` (PR number is on the checklist). **When this SOP shipped,
nothing about how production attributes speakers changed.** No migration, no env variable, no endpoint, no screen (so no FE guide).

**Update 2026-11-19: the correction it measured is built and ON by default** (`docs/sop/2026-11-19-line-level-speaker-correction.md`,
switch `SPEAKER_LINE_CORRECTION`). The report now shares the rule with production: `MIN_SECONDS` (1 s) and
`AMBIGUOUS_RATIO` (0.25) are imported from `app/speaker_correction.py` (so the defaults below are the production
constants), and `classify`, `assess` and `place` live there. Two behaviour changes: **tag names are read again with the
current naming rules by default** (as production and the rebuild do; `--saved-names` reads the names saved with the
evidence, which reproduces the first runs: meeting 39's 128 moves came from tags that were unnamed when it was
processed, so the default will print fewer), and the segment's own `speaker_name` is what a segment is compared with
(else its tag's name). With a `CLOCK WARNING` the report still prints its numbers, but production would move nothing in
that meeting. A segment already moved when the meeting was processed carries `corrected_to` in the evidence; the report
prints how many.

## Why

Meeting 40 (2026-11-18, three speakers, `elevenlabs/scribe-v2`): every tag was named correctly, yet a tag holds
other people's voices (Adebayo spoke 1063.6 s per Meet, only 692.2 s sits under his tag). Naming cannot fix a tag
that mixes voices. Before anyone builds a per-line correction, the size of the problem and the quality of the
signal have to be measured on real meetings. Note on reading the old overlap figures (59%, 64%, 88%): Meet's light
covers only about half to two thirds of speech, so "the named person was not lit" is not "the line is wrong".
This report counts the other thing: **another person clearly lit over the line**.

## How to run it

Read-only; it prints text only for the first 15 moves. Within 7 days of the meeting (the evidence,
`skribby_segment_evidence`, is deleted after 7 days: 38 about 2026-11-23, 39 about 11-24, 40 about 11-25).

```bash
ssh haven-respite 'bash -s' <<'EOF'
cd /opt/beyric-note-taker
docker compose exec -T web python scripts/speaker_reattribution_report.py 40
EOF
```

Options: `--min-seconds S` (default 1: fewest lit seconds that count), `--ambiguous-ratio R` (default 0.25: a runner-up
with this share of the top's seconds makes a segment ambiguous), `--offset S` (Meet's times shifted, same meaning as
in `skribby_segments_report.py`). Stricter run: `--min-seconds 2 --ambiguous-ratio 0.34`.

## How it reads

For each Skribby segment (tag and times needed) it asks who Meet shows speaking over most of it, using the same
timeline reading, clock placement and participant grouping as the naming rule (`app/meet_timeline.py`,
`app/skribby.py`); the bot is never a candidate; a rejoin is one person.

| Outcome | Meaning | Moves? |
|---|---|---|
| agrees | Meet's top speaker is the person the tag names (equal or one name inside the other) | no |
| **would move** | a different person (or nobody, for an unnamed tag) was clearly lit over it | yes |
| ambiguous | two or more people lit: the runner-up has the ratio and the minimum seconds | no |
| no signal | nobody lit for the minimum seconds | no |
| not timed | no tag or no usable times | no |

Sections: the clock line (a `CLOCK WARNING` means the numbers may be off; re-run with the printed `--offset`);
segments by outcome (count and seconds); **per tag** (what moves out, the share, and to whom); **per person** (their Meet
speaking seconds and how much sits under their own tag, another person's tag, an unnamed tag, or in no segment);
the first 15 moves with their text (cut at 200 characters). Per-person and per-tag seconds are merged intervals,
so overlapping segments of different tags can count a second twice in the per-person table.

## What's involved

`scripts/speaker_reattribution_report.py` (pure `classify`, `analyse`, `render`, `build_report`; `report` is the only
database read: one `skribby_segment_evidence` select and one `Meeting` get); `tests/test_speaker_reattribution_report.py`;
`tests/test_dockerfile_scripts.py` already checks that a documented container script is in the image (the command above
matches its pattern). Reused: `app/meet_timeline.py`, `app/skribby.py` (`_clean_name`, `_same`, `_tag`), `app/bot_names.py`.

## Verification

Bounded, on the Mac: `ruff check`, `ruff format --check` (the project's pinned 0.17.3), and the two named test files
(25 tests). The tests use a made-up timeline and placeholder words: each outcome (agrees, move, two lit, no signal, under the
minimum seconds, unnamed tag, spelling difference, untimed items), the bot excluded, a rejoin as one person, the per-person
split (own, other, unnamed, outside), the printed tables, **only the first 15 moves print text and nothing else does**, a
long text cut, a shifted clock called out, no events / unreadable events / unplaced clock each said in a line, and the
database wrapper making exactly two reads and no write. **Not run against real evidence**: no meeting 40 data is on the Mac.
CI runs the whole suite.

## Operate / roll back

Nothing to deploy beyond the merge (the script is in the image: `Dockerfile` copies `scripts/`). Revert the PR to remove it.
No cost: no network, no Skribby call.

## Follow-ups

* Adebayo runs it on 38, 39 and 40 (oldest first) and reads it by section 9 of the design note; then decide: build
  `SPEAKER_LINE_CORRECTION` (default off), or the model/diarization route.
* The design note's "share of the line" rule is not a script option yet; the per-move line prints "lit a of b s" so a person
  can judge it. Add `--min-share` if the first real read says it matters.
* Done 2026-11-19 (`fix/report-people-exact-name`): the per-person table ("own tag", "other", "unnamed") now uses
  `speaker_correction.same_name`, the exact test the correction moves a line by (the same words: "Obi" is not "Ada Obi",
  "Ann" is not "Joanne"), instead of the looser `same_person`, so the report cannot say a tag "belongs" to a person the
  correction would never give a line to. `same_name` is now the one place that rule lives (`resolve_label` and the clock
  gate call it too; no behaviour change there). The segment outcomes (`agrees`, `would move`, ...) are unchanged: they come
  from `assess`, which keeps `same_person` only to LEAVE a line alone, as the live path does. Consequence to read: a person
  "Ada" lit under a tag named "Ada Obi" is a segment that `agrees` but shows under "other" in the per-person table. Tests:
  surname-alone in both directions, "Ann" vs "Joanne", and same words in another case or order.
* The Meet clock placement is still not proven live (same caveat as the segments report).
