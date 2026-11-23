# SOP: name a speaker tag after a sole consistent candidate, and the one-transcript flip runbook

**What shipped.** `app/skribby.py::_resolve_tags` now also names a diarization tag that has no `speaker_name`
when every segment of the tag lists the same single name in `potential_speaker_names` and nothing else (a
**sole candidate**). The tag is recorded as `how: sole_candidate`. PR: branch `fix/skribby-name-sole-candidate`
(#208). **No migration.** **It goes live on deploy, for the `own` path too:** the switch `SKRIBBY_NAME_BY_ELIMINATION` defaults to on and every Skribby meeting's tags are read through it, so the day this is deployed (no `.env` change, no server step, before any flip) a tag in the meeting-37 shape is named in the notes of the `own` path as well. The off switch is `SKRIBBY_NAME_BY_ELIMINATION=false` plus a recreate. Plus the plain-words server steps to flip to one transcript
(`TRANSCRIPT_SOURCE=skribby`) once this is deployed, with the stale restart wording in older SOPs fixed.

**Update 2026-11-17** (`docs/sop/2026-11-17-name-by-meet-timeline.md`). Two things in this SOP changed. (1) A third rule
now runs after the sole candidate: a tag still unnamed may take the participant Meet's own speaking indicator shows speaking
most over it (`how: meet_timeline`, switch `NAME_BY_MEET_TIMELINE`, default on). The sole-candidate and elimination rules
themselves are unchanged and still run first; a tag they named is never touched. (2) `speakers_incomplete` on the `skribby`
path was "someone is named" (`Candidate.complete`) and is now false only when someone is named **and** the "Unknown speaker"
lines hold under 5% of the words, so Step 2's "good result" below (`f` in `speakers_incomplete`) now also means "almost
every line has a name". Part 2's remark that one unnamed tag of three "does not by itself make a meeting speakers
incomplete" still holds for a small tag and no longer holds for one carrying 5% or more of the words.

## Why

Meeting 37 (Google Meet, three people, `elevenlabs/scribe-v2`): Skribby split the voices cleanly. Two tags
carried Skribby's own `speaker_name`. The third tag (72 segments) carried none, but every one of its 72
segments listed exactly one possible name, the same person. The app showed `Unknown speaker [unknown,
unresolved]`. Meet's own speaker timeline in the same evidence points the same way: the tag overlaps that person's speaking for 551.7 s against 11.4 s and 8.4 s for the other two, and the other two tags overlap their own people the most too (a clear ranking, not a proof: the report says the Meet clock is not proven live). So the voice
splitting was fine and the **naming** failed.

Cause: elimination (`how: elimination`) only names a tag when at least one candidate was ruled out because
another tag holds it and exactly one is left (`ruled_out and len(left) == 1`). A sole candidate has nothing
to rule out, so it was refused. That refusal was added on purpose after meeting 29, where elimination named a
tag after someone who was not in the call (`docs/sop/2026-11-10-skribby-raw-segments.md`).

## How, and what was decided

A tag with no name of its own is named after its sole candidate only when **all** of these hold:

| Condition | Why |
|---|---|
| The elimination switch is on (`SKRIBBY_NAME_BY_ELIMINATION`, default true) | One switch for every naming that is not Skribby's own name, so one setting turns the whole family off. With it off the answer is recorded as `would_be`, exactly like elimination |
| The tag carries no `speaker_name` on any segment | A name Skribby gave always wins |
| **At least one other tag in the meeting carries a name of its own** (`given`) | A named tag shows Skribby's naming works in this meeting. A meeting where Skribby named nobody, or where one tag is all there is (the shape of meeting 32, where three voices became two tags), has nothing to corroborate a name, so one name on every segment is not enough there. Added after the review of the first version, which did not require it |
| The union of candidates over the tag's segments is exactly one name (compared the way names always are: cleaned, NFKC, no invisible characters, case-folded) | Two names is a choice between people, not a name. A list longer than `MAX_CANDIDATES` marks the tag unreadable, as before |
| **Every** segment of the tag lists it, and there are at least `MIN_SOLE_CANDIDATE_SEGMENTS` (3) | A name that shows up on some segments and not others is weaker evidence than one on all of them; I chose "all" over "most" because it keeps the rule one line and the meeting-29 posture conservative (meeting 37 had 72 of 72). Two lines are too little to put a person's name on |
| It is not one of the bot's own names | Same guard as elimination |
| No other tag holds it (a name another tag already has is "ruled out") and no other tag claims it as its sole candidate (`shared_claim` stays unknown) | Two tags never share a name |

`how` is a new value, `sole_candidate`, next to `given`, `elimination` and `unknown`, so the saved evidence
and `scripts/skribby_segments_report.py` show **why** the tag got its name (the report prints `how`
as it is: `[sole_candidate]`). Consumers of `how` were checked: `_segments` and `_tag_names` only read `name`;
the evidence is stored as JSON (`skribby_segment_evidence.tags`, no enum), `app/pipeline.py::_keep_evidence`
passes it through; the report script prints it. Nothing branches on the value `elimination`.

Rejected: a minimum share of segments (e.g. 80%) instead of "all" (more knobs, no data to set them); naming
from a sole candidate when the elimination switch is off (the switch exists so a bad name can be turned off
in one line); a new column or a new setting (the evidence JSON and the existing switch already carry it).

**What does not change.** The older test `test_a_lone_candidate_nothing_rules_anything_out_for_is_not_a_name` is still true: a lone candidate in a meeting where no other tag has a name stays unnamed, now with any number of segments (new test `test_a_sole_candidate_needs_one_tag_Skribby_named_itself_in_the_meeting`).

**The risk that stays, stated plainly.** If the real speaker is not on Skribby's list, a sole candidate on
every segment would name the tag after someone who may not be in the call. Nothing in the data catches that.
The rule is narrower than elimination (one name, on every segment, at least three, nobody else has it), and the
saved evidence shows `how: sole_candidate`, so a wrong name can be found and the switch turned off.

## What's involved

| File | Change |
|---|---|
| `app/skribby.py` | `MIN_SOLE_CANDIDATE_SEGMENTS`; `_resolve_tags` counts segments and listings per tag and records `sole_candidate`; docstrings |
| `tests/test_skribby_provider.py` | 12 tests (section "a sole consistent candidate"), one comment of an older test updated |
| `app/config.py`, `app/models.py`, `.env.example`, `scripts/skribby_segments_report.py`, `tests/test_skribby_evidence.py` | comments and docstrings say the switch covers the sole candidate and what `how` can be; the report says "naming by elimination or sole candidate would have said" for a `would_be` |
| `docs/sop/2026-11-09-skribby-speaker-names.md`, `2026-11-10-skribby-raw-segments.md` | update lines: elimination text changed |
| `docs/sop/2026-11-09-transcript-source-switch.md`, `2026-11-13-skribby-elevenlabs-single-transcript.md`, `2026-11-10-skribby-raw-segments.md`, `.env.example`, `deploy/README.md`, `docs/checklist/master-checklist.md`, `docs/HANDOFF-master.md` | stale "restart" changed to "recreate" (a restart does not re-read `.env`) |

## Verification

* **Tests, in order:** the four positive tests (meeting-37 shape, spelled two ways, and the two that need
  the rule) were written first and run before the change: 4 failed (tag stayed `unresolved`). After the review, the test for a meeting where no tag is named was written first and failed too (the tag was named), then the rule was tightened. After the change
  252 tests of `tests/test_skribby_provider.py` pass (7 more need Postgres, which this Mac does not run, and are
  left to CI; the same for the Postgres-backed tests of `tests/test_skribby_evidence.py`).
* **What each guard test proves:**

| Test | Proves |
|---|---|
| `test_a_sole_candidate_on_every_segment_names_the_tag_meeting_37_shape` | two named tags + one unnamed tag with one candidate on every segment: named, `how: sole_candidate`, the two given tags unchanged |
| `..._does_nothing_with_elimination_off_but_records_would_be` | switch off: tag stays unknown, `elimination_off`, `would_be` holds the name |
| `test_a_sole_candidate_needs_one_tag_Skribby_named_itself_in_the_meeting` | a single tag of 5 segments, or two unnamed tags with a sole candidate each: unnamed |
| `test_a_sole_candidate_that_is_the_bot_is_not_a_name` | the bot's own name is never taken |
| `test_a_sole_candidate_another_tag_already_holds_is_not_taken_twice` | a name another tag has is ruled out |
| `test_two_tags_with_the_same_sole_candidate_both_stay_unknown` | shared claim: both `shared_claim` |
| `test_candidates_that_differ_between_segments_...` | two different single names on one tag: unnamed |
| `test_a_list_with_a_second_name_on_any_segment_...` | one segment with two names breaks it |
| `test_a_list_too_long_to_trust_...` | a list over `MAX_CANDIDATES` marks the tag unreadable |
| `test_a_candidate_missing_from_some_segments_...` | one segment with no list: unnamed |
| `test_a_sole_candidate_on_too_few_segments_is_not_a_name` | 2 segments: unnamed |
| `test_a_sole_candidate_spelled_two_ways_is_still_one_name` | `Cy Example` / `CY  EXAMPLE` is one name |

  The guard tests (all but the one above) passed before the change too (the refusal already covered them): they pin the rule so
  loosening it later breaks a test. The existing test `test_a_lone_candidate_nothing_rules_anything_out_for_is_not_a_name`
  still passes (one segment).
* `ruff check`, `ruff format --check` with the pinned ruff 0.17.3, run as CI runs them.
* **Not verified:** against a real Skribby answer (only meeting 37's shape, rebuilt with made-up names, which
  is the whole point of the rule); the report script's printout for `sole_candidate` (it prints `how` as it
  is; its tests need Postgres and run on CI); the runbook against the real server (below).

## Part 2: why lines can land on the wrong person in the notes (diagnosis, no code change)

Written up in the shared notes folder (`naming-diagnosis.md`). Short version: with `TRANSCRIPT_SOURCE=own` the
notes use our text, and `label_transcript` puts Skribby's tags on it by share of text with no times. An
unnamed tag is **kept** as `Unknown speaker` (not merged, not dropped) and handed to the model, which may keep
it or give it to the neighbour; the alignment can also drift by itself. `label_transcript` returns `complete=False` when nobody is named, **and also** when a section's model call fails or the words drift over 2%; one unnamed tag of three does not by itself make a meeting "speakers incomplete". With `skribby` there is no such step:
each line keeps the tag Skribby gave it, an unnamed tag stays `[Unknown speaker]`, and this fix names the
meeting-37 shape. **Not provable** without the notes' own speaker lines of meeting 37.

## Server steps: flip to one transcript (after this PR is merged and deployed)

**What this is for, in plain words.** Today the notes are made from OUR transcription of the recording, with
Skribby's speaker tags laid on top by a model that guesses who said what. After the flip the notes are made from
**Skribby's own transcript**, where every line already has its speaker, so lines cannot move to another person.
Our own transcription is then only run for a **sample** of meetings (`TRANSCRIPT_SHADOW_PERCENT`, 25 below; use
10 if there are many meetings) so that after a week a report can say how close the two texts are. If Skribby
sends nothing or far too little text, the meeting falls back to ours by itself. Two `.env` lines change; they only
take effect when the three services are **recreated** (`docker compose up -d --force-recreate`, **not**
`restart`: a restart does not re-read `.env`, so the file would say `skribby` while the services still run
`own`). Nothing is deleted; stored meetings keep their text.

**One more thing the recreate does:** the queue of sampled comparison jobs lives in memory (`docs/sop/2026-11-09-transcript-source-switch.md`), so a recreate drops jobs not yet run. The no-meeting-in-progress guard makes that harmless.

**Order and why.** Run steps 0 and 1 in one sitting. **No meeting may be recording or processing** (and no join
due in ten minutes) when the services are recreated: it restarts the worker and the scheduler. Step 1 checks that
itself and stops without changing anything if one is. Step 1 also **refuses to change anything** unless
`BOT_PROVIDER=skribby` and `SKRIBBY_TRANSCRIPTION_MODEL=elevenlabs/scribe-v2` are in `.env` (with any other model
the app **refuses to start** in `skribby` mode and the site would be down) and unless `SKRIBBY_NAME_BY_ELIMINATION` is `false` (with it `false` the point of this fix is off and the meeting-37 shape would come out `[Unknown speaker]`). Every block is one command to paste;
each `docker` call has `</dev/null` so it cannot swallow the rest of the script.

### Step 0. Look first (changes nothing)

**A good result:** line 1 says `OK: the running web has the sole-candidate rule`; the `.env` lines show
`BOT_PROVIDER=skribby` and `SKRIBBY_TRANSCRIPTION_MODEL=elevenlabs/scribe-v2` and `TRANSCRIPT_SOURCE` missing or
`own`, and `SKRIBBY_NAME_BY_ELIMINATION` missing or `true` (**if it says `false`, stop and tell the lead: the sole-candidate naming is off**); web, worker and scheduler each `running`/`Up`; the last line is `0 0` (meetings recording or processing,
joins live or due within ten minutes). If it is not `0 0`, wait and run it again. If line 1 says `NOT DEPLOYED
YET`, the merge has not reached the server: stop.

```bash
ssh haven-respite 'bash -s' <<'EOF'
cd /opt/beyric-note-taker
echo "--- 1. does the running web have the fix? (must say OK):"
docker compose exec -T web python -c "import app.skribby as s; print('OK: the running web has the sole-candidate rule' if hasattr(s, 'MIN_SOLE_CANDIDATE_SEGMENTS') else 'NOT DEPLOYED YET: stop here')" </dev/null
echo "--- 2. the lines that matter in .env:"
grep -E '^(BOT_PROVIDER|SKRIBBY_TRANSCRIPTION_MODEL|SKRIBBY_NAME_BY_ELIMINATION|TRANSCRIPT_SOURCE|TRANSCRIPT_SHADOW_PERCENT)=' .env || echo "(none of them is set in .env)"
echo "--- 3. the services (web, worker, scheduler must each say running, or Up):"
docker compose ps web worker scheduler </dev/null
echo "--- 4. is anything being recorded or processed right now? (two numbers, both must be 0):"
docker compose exec -T db sh -c 'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -tA -F " " -v ON_ERROR_STOP=1' <<'SQL'
select
  (select count(*) from meetings where status in ('bot_sent', 'processing')) as meetings_in_progress,
  (select count(*) from scheduled_joins
     where state in ('dispatching', 'no_capacity', 'joined')
        or (state = 'scheduled' and run_at <= now() + interval '10 minutes' and deadline_at >= now())) as joins_live_or_due_within_10_min;
SQL
EOF
```

### Step 1. Switch on and recreate

It stops by itself, changing nothing, if a meeting is being recorded or processed, or if the provider or model
lines are not as above, or if `SKRIBBY_NAME_BY_ELIMINATION` is `false`. Otherwise it sets `TRANSCRIPT_SOURCE=skribby` and `TRANSCRIPT_SHADOW_PERCENT=25` in `.env`,
checks the file took them, recreates `web`, `worker` and `scheduler` (the site is unreachable for a few
seconds), waits, and asks **each running service** what it sees. **A good result:** each of web, worker and
scheduler prints `skribby` then `25`; the list shows all three running (web may say `health: starting` for up
to a minute; it must not say `unhealthy` or `restarting` after that). If a service is restarting, run the roll
back and tell the lead what `docker compose logs --tail 20 web` says (the message names the setting).

```bash
ssh haven-respite 'bash -s' <<'EOF'
cd /opt/beyric-note-taker
SHADOW=25   # 10 to 25: the share of meetings where our own transcription also runs, to compare
echo "--- is anything being recorded or processed right now? (must be 0 and 0, or this stops):"
counts=$(docker compose exec -T db sh -c 'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -tA -F " " -v ON_ERROR_STOP=1' <<'SQL'
select
  (select count(*) from meetings where status in ('bot_sent', 'processing')),
  (select count(*) from scheduled_joins
     where state in ('dispatching', 'no_capacity', 'joined')
        or (state = 'scheduled' and run_at <= now() + interval '10 minutes' and deadline_at >= now()));
SQL
)
echo "meetings in progress, joins live or due within 10 minutes: $counts"
if [ "$counts" != "0 0" ]; then
  echo "STOPPED: a meeting is being recorded or processed, or one starts within 10 minutes. Nothing was changed. Run this again when the numbers are 0 0."
  exit 1
fi
if ! grep -qx 'BOT_PROVIDER=skribby' .env || ! grep -qx 'SKRIBBY_TRANSCRIPTION_MODEL=elevenlabs/scribe-v2' .env; then
  echo "STOPPED: .env must say BOT_PROVIDER=skribby and SKRIBBY_TRANSCRIPTION_MODEL=elevenlabs/scribe-v2 or the app will refuse to start. Nothing was changed. Tell the lead what step 0 printed."
  exit 1
fi
if grep -qix 'SKRIBBY_NAME_BY_ELIMINATION=false' .env; then
  echo "STOPPED: SKRIBBY_NAME_BY_ELIMINATION is false, so a speaker Skribby did not name would come out as Unknown speaker. Nothing was changed. Tell the lead."
  exit 1
fi
set_env() {
  if grep -q "^$1=" .env; then sed -i "s/^$1=.*/$1=$2/" .env; else printf '\n%s=%s\n' "$1" "$2" >> .env; fi
  grep -qx "$1=$2" .env
}
echo "--- setting the two lines in .env:"
set_env TRANSCRIPT_SOURCE skribby && set_env TRANSCRIPT_SHADOW_PERCENT "$SHADOW" || { echo "STOPPED: could not set the lines in .env, so nothing was recreated. Tell the lead what the lines below say."; grep -E '^TRANSCRIPT_' .env; exit 1; }
grep -E '^TRANSCRIPT_' .env
echo "--- recreating web, worker and scheduler (the site is unreachable for a few seconds):"
docker compose up -d --force-recreate web worker scheduler </dev/null
sleep 20
echo "--- the two values as each running service sees them (skribby, then $SHADOW, three times):"
for s in web worker scheduler; do
  echo "$s:"
  docker compose exec -T $s printenv TRANSCRIPT_SOURCE TRANSCRIPT_SHADOW_PERCENT </dev/null
done
echo "--- the services:"
docker compose ps web worker scheduler </dev/null
EOF
```

### Step 2. After the next real meeting (changes nothing)

Proves the new path ran and the names came through. **A good result:** the newest row says `skribby` in
`transcript_source` (`own` means the fallback ran: Skribby's text was missing or too short; look at it) and `f` in
`speakers_incomplete`. Then run the second block with the `id` of the first row that finished after step 1
(replace `ID` with the number); its output holds what people said, so give it to the lead and do not post it
anywhere public. Each tag header should read `[given]` or `[sole_candidate]`, none `[unknown ...]`.

```bash
ssh haven-respite 'bash -s' <<'EOF'
cd /opt/beyric-note-taker
docker compose exec -T db sh -c 'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -v ON_ERROR_STOP=1' <<'SQL'
select id, status, transcript_source, speakers_incomplete, created_at from meetings order by id desc limit 5;
SQL
EOF
```

```bash
ssh haven-respite 'bash -s' <<'EOF'
cd /opt/beyric-note-taker
docker compose exec -T web python scripts/skribby_segments_report.py ID </dev/null
EOF
```

### Step 3. After about a week: read the comparison (changes nothing)

Reads numbers only, never transcript text. **A good result:** a short text report: how many meetings
took their transcript from where, how many were compared, how many notes were made from Skribby's text versus
fell back to ours, the median/lowest/highest "words in common" (1.00 is identical; a low number means the two
disagree, which does not alone say which is worse: open the meeting), the share of segments with no speaker
name, and the five meetings that disagree most. "Nothing to compare yet" means no sampled meeting has run:
leave it another week. Paste it to the lead, who decides: keep `skribby`, or go back.

```bash
ssh haven-respite 'bash -s' <<'EOF'
cd /opt/beyric-note-taker
docker compose exec -T web python scripts/transcript_shadow_report.py </dev/null
EOF
```

### Roll back

Sets `TRANSCRIPT_SOURCE=own` and `TRANSCRIPT_SHADOW_PERCENT=0` and recreates; it stops by itself if a meeting is
being recorded or processed. Meetings already stored keep their text and `transcript_source`; new ones use ours.
**A good result:** `own` then `0` three times. The code does not need reverting.

```bash
ssh haven-respite 'bash -s' <<'EOF'
cd /opt/beyric-note-taker
echo "--- is anything being recorded or processed right now? (must be 0 and 0, or this stops):"
counts=$(docker compose exec -T db sh -c 'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -tA -F " " -v ON_ERROR_STOP=1' <<'SQL'
select
  (select count(*) from meetings where status in ('bot_sent', 'processing')),
  (select count(*) from scheduled_joins
     where state in ('dispatching', 'no_capacity', 'joined')
        or (state = 'scheduled' and run_at <= now() + interval '10 minutes' and deadline_at >= now()));
SQL
)
echo "meetings in progress, joins live or due within 10 minutes: $counts"
if [ "$counts" != "0 0" ]; then
  echo "STOPPED: a meeting is being recorded or processed, or one starts within 10 minutes. Nothing was changed. Run this again when the numbers are 0 0."
  exit 1
fi
set_env() {
  if grep -q "^$1=" .env; then sed -i "s/^$1=.*/$1=$2/" .env; else printf '\n%s=%s\n' "$1" "$2" >> .env; fi
  grep -qx "$1=$2" .env
}
set_env TRANSCRIPT_SOURCE own && set_env TRANSCRIPT_SHADOW_PERCENT 0 || { echo "STOPPED: could not set the lines in .env, so nothing was recreated. Tell the lead what the lines below say."; grep -E '^TRANSCRIPT_' .env; exit 1; }
grep -E '^TRANSCRIPT_' .env
echo "--- recreating web, worker and scheduler (the site is unreachable for a few seconds):"
docker compose up -d --force-recreate web worker scheduler </dev/null
sleep 20
echo "--- the two values as each running service sees them (own, then 0, three times):"
for s in web worker scheduler; do
  echo "$s:"
  docker compose exec -T $s printenv TRANSCRIPT_SOURCE TRANSCRIPT_SHADOW_PERCENT </dev/null
done
echo "--- the services:"
docker compose ps web worker scheduler </dev/null
EOF
```

**Checked here:** every block passes `bash -n`; steps 0, 1 and the roll back were run against a stub `docker` (quiet; busy, which stops with `.env` unchanged; a wrong model line, which stops with `.env` unchanged; a `.env` with no final newline, where both lines still land on their own lines; the values read back three times). **Not verified:** any block against the real server: `docker compose up --force-recreate` and `printenv` on the
real containers, `sed -i` on the real `.env` (GNU sed is assumed), the real timing of the recreate, and the
column names in the Step 2 query on the real database (`transcript_source` and `speakers_incomplete` are on the
`meetings` model in `app/models.py`; the query was not run).

## Operate / roll back (the code)

Revert the PR: the rule goes, tags named after a sole candidate go back to `Unknown speaker`. Or leave the code
and set `SKRIBBY_NAME_BY_ELIMINATION=false` (recreate web, worker and scheduler): all naming that is not
Skribby's own name stops; the evidence still records `would_be`.

## Follow-ups

* Read the evidence of the next real meeting with an unnamed tag: did `sole_candidate` name the right person?
* The `own` path's share-of-text alignment is the likely source of misplaced lines (Part 2). It goes away if
  the one-transcript flip holds; if it does not, the alignment needs times (Skribby segment `start`/`end`
  against the audio) rather than a share of text. Needs the notes' own speaker lines of meeting 37 to confirm.
* **Keep Skribby's confidence for `sole_candidate` tags** (so a 0.05 guess can be told from a 0.95 one). Not done here: `_candidate_names` keeps names only, so it means carrying the confidence through the candidate maps and changing the exact-dict evidence tests. Needs no migration (the evidence is JSONB); a small change of its own.
* A tag that lists a name another UNNAMED tag also lists (tag X sole `Cy`, tag Y `Cy` and `Di`) is not treated as a conflict. Not a bug against the stated rule; wait for evidence.
* `MIN_SOLE_CANDIDATE_SEGMENTS = 3` has no data behind it (three back-channel segments would be named); consider a minimum of words or seconds once a second real case exists.
* If a sole candidate is ever wrong, the next step is to require it to be on the call's participant list, which
  Skribby's `participants` carry (the saved events already hold them, so the report script could print "is on Meet's participant list: yes/no" for `sole_candidate` tags with no rule change); not done here (no live evidence it is needed).

## Update 2026-11-23: elimination is a chain

A name that elimination or the sole-candidate rule gave to exactly one tag now counts as held by that tag when the other
tags are resolved, in rounds until nothing changes; a name from round 2 on is given only on Meet's positive evidence (usable events, agreeing with the names given, the person leading the tag 1.5x) (`docs/sop/2026-11-23-speaker-name-chain.md`). A name two tags would
claim in any round goes to neither and is never held. The sole-candidate conditions in this SOP are unchanged.
