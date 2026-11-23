# SOP: speaker naming runs down a chain, and the last tag is named by exclusion against Meet's participant list

**What shipped.** Two changes to how a Skribby diarization tag gets a name, in one PR (branch
`fix/speaker-name-chained-elimination`; the PR number is on the checklist). **No migration, no endpoint change, no FE
guide change, no setting.** It applies to new meetings on deploy. A meeting already saved is **not** rebuilt (Adebayo's
rule: no rebuilds of past meetings); meeting 43's saved result is unchanged by design.

1. **Chained elimination** (`how: elimination`, as before). `_earlier_rules` in `app/skribby.py` now runs to a fixed
   point. A name that one round gave to exactly one tag (by elimination or as a sole candidate) counts as **held** by that
   tag in the next round, so a tag whose candidates were ruled out only by *that* name is named in its turn. **A name from
   round 2 on stands only on Meet's positive evidence** (usable events, checked clock, the timeline agrees with the names
   already given, and the person leads the tag by 1.5x and at least 30 s); without that it is not given (see "Review fixes").
2. **Exclusion against Meet's participant list** (`how: meet_exclusion`, new). `_name_by_exclusion` in `app/skribby.py`,
   called at the end of `_name_by_timeline`: when exactly one tag is still unnamed and exactly one person in Meet's own
   participant list is left over, the tag is that person's, under the guards below.

## Why

The first meeting recorded with the 2026-11-17 and 2026-11-19 rules (meeting 43, three real speakers; the names below are
placeholders A, B, C, the real ones are on the server only) ended with one speaker as "Unknown speaker": 22% of the
words, `speakers_incomplete` true, although both rules had what they needed.

| | Skribby | Meet |
|---|---|---|
| tag 0 (34 segments) | `speaker_name` A on every segment | A: 255 s speaking |
| tag 1 (74 segments) | no name; candidates [B, A] | B: 185 s (listed twice: a rejoin) |
| tag 2 (59 segments) | no name; candidates [C, B] | C: 126 s |

Overlap of each tag with each person (seconds): tag 0: A 124.3, B 9.3, C 7.2; tag 1: A 86.8, B 111.6, C 19.6; tag 2: A 6.8,
B 43.6, **C 84.6** (tag total 193.3 s). Result: tag 0 `given` A; tag 1 `elimination` B (A ruled out, tag 0 has it: correct);
tag 2 `unknown / unresolved`, `timeline: not_clear`. The right answer was C.

Root causes (read in the code before changing it):

* **Elimination was one pass.** `holders` held only names Skribby *gave* on a tag's own segments. B, which elimination
  had just assigned to tag 1, was not "taken" when tag 2 was resolved, so nothing was ruled out for tag 2 and the rule skipped it.
* **The timeline refused tag 2 on a margin.** C had 84.6 s against B's 43.6 s: 1.94 times, under `MIN_RATIO` 3.0. That
  line is right: meeting 39's weak tags (2.45x and 1.45x) must stay unnamed, and it must not be lowered globally.

## How

**Chain.** Round 1 is exactly what the rules did before this PR. A round-2+ round runs only if Meet is usable and
agrees with the names given so far, and a proposal is committed only if Meet positively supports it:

| Check (round 2 on) | Where | Why |
|---|---|---|
| a reading exists and is not refused: events readable, **at least one participant with a speaking interval** (`meet_timeline.read` refuses `no_events` otherwise: the size cap's `events_dropped` keeps names and empties every list), Unix times placed, clock check passed | `_earlier_rules` | participants with no events are no evidence; behave exactly as with no events (round 1 only) |
| the meeting-level gate the timeline rule uses (`_meeting_gate`): at least one named tag's top speaker is that person, none has a different person clearly on top | `_earlier_rules`, shared with `_name_by_timeline` | if Meet contradicts a name already given, the whole timeline is distrusted, chain names included |
| the proposed person **leads the tag** (`meet_timeline.leads`): lit at least `MIN_SECONDS` (30 s) and at least `EXCLUSION_RATIO` (1.5x) as long as anybody else, the bot included | per proposal | positive support, not just "not clearly contradicted": Bo 50 s against Ada 20 s (2.5x, under the 3.0x naming bar) must not give tag 9 to Ada |

**Meeting 43 still passes:** its tag 2 is Charlie 84.6 s against Bravo 43.6 s (1.94x, over 1.5x and 30 s), tag 0 confirms
Alpha (124.3 s against 9.3 s), no named tag is contradicted. A proposal that fails is neither given nor held, so an error
cannot run down the chain, and the tag stays open for the timeline rule, which may name it (that is how an over-split person
is repaired). With no events (Teams, Zoom, a failed fetch), the timeline switch off, no usable events, a clock that may be
off, or a contradiction, a round-2+ name is not given: the tag stays Unknown exactly as before. Rounds are synchronous: every tag in a round sees the same holders, so the order of the tags changes nothing.
A round collects each open tag's proposal (candidates minus the bot's names minus names held by *another* tag; exactly one
left; `elimination` if something was ruled out, else the unchanged `sole_candidate` test). Proposals are then grouped by name:

* one tag: committed, and the name becomes held by it for the next round;
* several tags in round 1: **all of them get `shared_claim` and the name is never held**, so a contested name rules
  nothing out further down. A clash in round 2 or later is **not recorded** (those tags stay `unresolved`, as the old code
  left them), because it is made of inferred names and a recorded `shared_claim` would stop the timeline rule from naming
  them: Bo over-split onto tags 9 and 10, both listing Ada and Bo, are both named Bo by the timeline, as before this PR;
* a tag with no candidate list, or with two names of its own, is untouched.

Each round settles at least one tag, so the loop ends after at most as many rounds as there are open tags. The saved
`tags` record keeps its shape: a chained result is plain `elimination` (no new key). With elimination off the chain still
runs on the would-be names, so `would_be` shows what the switch changed on every link.

**Exclusion.** Runs only after the timeline rule's own gates (events readable, clock check passed, at least one named tag
agrees with the timeline, no named tag contradicted). Then:

| Condition | Why |
|---|---|
| exactly **one** tag still unnamed, with a note the other rules left for lack of an answer | with two open tags which is which is a guess; a tag with two names of its own holds two people |
| exactly **one** participant left over: anyone in Meet's participant list who is not the bot and not the name of a named tag, **however little they were lit or whether they have events at all**; a rejoin is one person (grouped as `_name_by_timeline` groups). **Exception:** when the open tag has a candidate list, left-over participants not on it are ignored (Skribby says the speaker is one of those) | a dial-in or a weakly lit person is exactly the speaker Meet cannot see; counting only the visible people would make the one visible person look like the only one left |
| the person is on the tag's candidate list when it has one (a list too long to read: refused) and is the name the earlier rules had picked for the tag when they picked one they could not give | the model's own evidence is never overridden |
| the tag covers at least `MIN_EXCLUSION_TAG_SECONDS` = 90 s | the failing tag had 193 s; 90 s is three times `MIN_SECONDS`, about half of it; noise tags are never named by being left over |
| the person was lit over the tag for at least `MIN_SECONDS` (30 s) and `MIN_SHARE` (20%) of its seconds | same floors as the timeline rule; the share guard stops a tag whose real speaker has no Meet entry (a dial-in) from going to the one talker-over who is left over |
| the person was lit over the tag **at least `EXCLUSION_RATIO` = 1.5 times as long as anybody else** (the bot included); anyone lit as long or longer is `beaten`, between 1x and 1.5x is `too_close` | a tag lit about as much over someone who already has a tag is as likely an over-split of that person (41 s against 40 s must not name). **Why 1.5, not `MIN_RATIO`'s 3.0:** 3.0 is the line for naming a tag from Meet *alone* and would have refused meeting 43's own tag (84.6 s against 43.6 s = **1.94x**, which passes 1.5); here being the only one left over is also evidence, so Meet need only clearly point the same way. 1.5 is chosen from that one case and the near-tie signature, not measured further. `MIN_RATIO` is not changed |

A lone open tag that fails these gets an `exclusion` key with the first reason (`not_eligible`, `participants`,
`not_on_list`, `disagrees`, `tag_too_short`, `too_little`, `small_share`, `beaten`, `too_close`). With several open tags no key is set.

**Decided and rejected.** `MIN_RATIO` is untouched (a lower global line would name meeting 39's weak tags). Rejected:
matching several open tags to several left-over people by their overlaps (a guess at scale); relaxing "one open tag" to
"one open tag per left-over person". The ratio row is stricter than a plain "not clearly
beaten" on purpose: a wrong name is worse than "Unknown speaker".

## Review fixes (Opus review of the first head, `c87cfd70`)

| Finding | What was wrong | Fix |
|---|---|---|
| HIGH: over-split person (first review) | Bo split over tag 3 (lists only Bo: `sole_candidate`) and tag 9 (lists Ada, Bo): the chain held Bo on tag 3 and named tag 9 **Ada**, although Meet clearly has Bo on it; the old code left tag 9 open and the timeline rule named it Bo. Also one wrong link made the next link wrong (tag 1 really C listing B, A; tag 2 really B listing C, B) | the round-2+ checks inside the loop (above). Test `test_an_over_split_person_is_named_by_meet_not_by_the_chain` restores the (Ada, Bo) case; `test_a_wrong_link_does_not_make_the_next_one_wrong_too` |
| MEDIUM: no margin | C 41 s beat A 40 s though A already has a tag | `EXCLUSION_RATIO` 1.5 (table above); tests at 1.4x (Unknown) and 1.6x (named) |
| LOW-MEDIUM: quiet participants dropped | an invisible real speaker (dial-in, weak indicator) looked like "nobody", so the one visible person was "the only one left" | everyone present counts; the candidate-list exception keeps the route useful; tests for a 10 s participant, a no-events dial-in, and the exception |
| LOW: same display name | two people with one name are one key | **not fixed, a known limit** (below) |
| HIGH A (delta review): no usable events | a record with participants and empty `events` was not refused, all overlaps were 0 and the vouch passed, so every round-2 name was given with no Meet evidence | `read` refuses `no_events` when nobody has a speaking interval; round 2+ also needs a positive lead on the tag. Tests: events emptied, participants only (with and without an `events` key), zero overlap on the tag |
| MEDIUM B (delta review): vouch only at the full bar | Bo 50 s against Ada 20 s still gave tag 9 Ada | the positive-lead rule and the meeting gate (above); tests at 1.4x (Unknown), 1.6x (named), 1.25x, Bo 50 / Ada 20, contradicted and uncorroborated gates |
| LOW C (delta review): the bot's Meet name | the bot now counts as left over unless its name is in `own_names` | no code change needed: `own_names` already holds the server default (`SKRIBBY_BOT_NAME`), the Google account's own name (`SKRIBBY_ACCOUNT_NAME`), the meeting's stored `bot_name` and the `bot_name` Skribby reports (`SkribbyProvider._look`, `app/pipeline.py`). A bot whose Meet name is none of those (for example no `SKRIBBY_ACCOUNT_NAME` set) **fails closed**: it counts as someone left over, exclusion says `participants`, fewer names and never a wrong one. Test `test_a_bot_whose_meet_name_is_not_known_fails_closed`. Check the real Meet name of the bot once |

## Known limits

* **Two people with the same display name are one person.** `group` merges them, as it must for a rejoin (one person listed
  once per visit). Telling a rejoin from a second person needs presence intervals, and the saved events only carry
  speaking, so two Sams whose speaking windows do not overlap cannot be told apart and overlap-based detection would
  mislabel a real rejoin whose entries interleave. Failing example, pinned by `test_known_limit_two_people_with_one_display_name_are_one_person`: tag 0 is the first Sam; tag 1 is the second Sam and lists Sam and Kim; Sam counts as taken, so round 1 (unchanged from before this PR) names tag 1 Kim. A round-2+ name is vouched by the timeline, which has the same merged key, so it does not help here. A real fix needs Skribby's presence data and a look at real duplicate-name meetings.
* Other note takers and room devices that appear in Meet's list also count as someone left over unless the tag has a candidate list (fails closed).
* A round-2+ name needs the timeline switch (`NAME_BY_MEET_TIMELINE`) on and events present; in Teams and Zoom meetings the chain stops after round 1, as the rules did before.

## What's involved

| Path | Change |
|---|---|
| `app/skribby.py` `_earlier_rules`, `_meeting_gate`, `_timeline_reading` | the fixed-point rounds with the Meet check from round 2; `shared_tags` / `shared_keys` replace the one-shot `claims`; the timeline is read once and handed to the chain and to `_name_by_timeline` |
| `app/skribby.py` `_name_by_exclusion`, `_name_by_timeline` | the new route, called last |
| `app/skribby.py` `apply_timeline` | reads the earlier rules again for tags **saved unnamed**, so a report on an old row shows what today's rules name (read-only) |
| `app/meet_timeline.py` | `MIN_EXCLUSION_TAG_SECONDS`, `EXCLUSION_RATIO`, `unaccounted`, `exclusion_verdict` |
| `scripts/skribby_segments_report.py` | the last section prints the route (`[elimination]`, `[meet_timeline]`, `[meet_exclusion]`) and the `exclusion` reason |
| `app/speaker_correction.py` | **not changed**: it receives `resolved` from `_resolve_tags`, so the chained and excluded names reach it like any other |
| `tests/test_meet_timeline_naming.py` | the new tests (below) and a fixture adjustment on three old one-tag cases |

Invariants of the older SOPs, kept: a name Skribby gave is never overridden; a name two tags would both take goes to neither;
a tag with no list is untouched; the bot is never a name; elimination off names nothing; no events, a refused clock or no
agreeing named tag name nothing from Meet; meeting 39's weak tags stay unnamed (its fixtures pass unchanged); the line
correction still moves a line only to a name already in the meeting, and an exact no-op without events.

## Verification

Fixtures use fake names (Alpha, Bravo, Charlie, Delta) and the failure's numbers (the totals of tags 0 and 1 are made up and
no rule reads them). In `tests/test_meet_timeline_naming.py`:

| Test | Proves |
|---|---|
| `test_the_failure_names_the_tag_the_chain_reaches_not_unknown` | tag 2 is Charlie, `elimination` (Meet vouches: 84.6 s against 43.6 s names no one else) |
| `test_without_meet_a_chain_name_from_round_two_is_not_given`, `test_a_clock_that_may_be_off_...`, `test_a_record_with_participants_but_no_speaking_events_...`, `test_events_with_no_overlap_...`, contradicted and uncorroborated gates | no events, timeline off, a shifted clock, no usable events, no overlap, or a timeline that does not confirm the given names: tag 1 (round 1) is named as before, tag 2 (round 2) stays Unknown exactly as before |
| `test_with_elimination_off_...` | nothing is named, `would_be` is set on tags 1 and 2 |
| `test_the_labels_of_the_failure_carry_the_chained_name_...` | the labelled lines and the line correction see the new name; none is blank |
| chain-of-three (both orders), 40-link chain, sole-candidate link (with events) | the fixed point, order independence, termination |
| over-split Bo; a round-2 name needs a 1.5x lead (1.4x Unknown, 1.6x named); Bo 50 / Ada 20; wrong link does not compound | the HIGH and MEDIUM B findings |
| shared-claim tests (three) | a contested name is neither given nor held; a late claimant joins the claim |
| bot / no-list / two-names tests | the existing safeguards survive the chain |
| `test_the_failure_without_candidate_lists_names_the_last_tag_by_exclusion` | tag 2 is Charlie, `meet_exclusion`; not without events |
| two open tags; two left-over people; a 10 s participant and a no-events dial-in (both count); the candidate-list exception; bot; list leaves person out; another person on top; 1.4x and 1.6x; too little; too short; small share; shifted clock; no agreeing named tag | each ambiguous or unsafe case stays Unknown, with the reason |
| `test_known_limit_two_people_...` | documents the same-name limit |
| `test_the_report_prints_the_route_...` | the report names the route and does not touch the saved record |

Meeting 39's tests and the older tests in the file pass unchanged except three one-tag cases whose fixture keeps the
new routes out (they test the timeline rule alone: tag 9 lists Di instead of Bo, and Eve speaks 100 s on no tag); the (Ada, Bo) case is now tested on its own, above.
Run locally: `ruff check .`, `ruff format --check .` (pinned 0.17.3), and the tests of the touched files; CI runs the rest.
**Not verified:** a real meeting. The literal "no candidate lists on tags 1 and 2" variant leaves two open tags and stays
Unknown by design (tag 1's 1.29x margin names nothing); the exclusion test names tag 1 by Skribby instead.

## Operate / roll back

After deploy, on the server, a read-only proof on meeting 43's stored evidence (not a rebuild; nothing is written):

```bash
docker compose exec -T web python scripts/skribby_segments_report.py 43
```

The last section should say `tag 2: would be named <C> [elimination]`. Roll back = revert the PR; the saved `tags` shape is
unchanged and the new `exclusion` key is optional, so saved rows read either way.

## Follow-ups

* The next live meeting is the proof; nothing here was run on real Meet data (the fixture is the failure's shape).
* `scripts/rebuild_meeting_from_evidence.py` prints a tag's `timeline` reason but not `exclusion`.
* Two open tags with two left-over people stay Unknown; a safe rule for that needs real examples.
