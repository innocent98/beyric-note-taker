# SOP: speaker naming runs down a chain, and the last tag is named by exclusion against Meet's participant list

**What shipped.** Two changes to how a Skribby diarization tag gets a name, in one PR (branch
`fix/speaker-name-chained-elimination`; the PR number is on the checklist). **No migration, no endpoint change, no FE
guide change, no setting.** It applies to new meetings on deploy. A meeting already saved is **not** rebuilt (Adebayo's
rule: no rebuilds of past meetings); meeting 43's saved result is unchanged by design.

1. **Chained elimination** (`how: elimination`, as before). `_earlier_rules` in `app/skribby.py` now runs to a fixed
   point. A name that one round gave to exactly one tag (by elimination or as a sole candidate) counts as **held** by that
   tag in the next round, so a tag whose candidates were ruled out only by *that* name is named in its turn.
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

**Chain.** Rounds are synchronous: every tag in a round sees the same holders, so the order of the tags changes nothing.
A round collects each open tag's proposal (candidates minus the bot's names minus names held by *another* tag; exactly one
left; `elimination` if something was ruled out, else the unchanged `sole_candidate` test). Proposals are then grouped by name:

* one tag: committed, and the name becomes held by it for the next round;
* several tags in the round, or a name already contested in an earlier round: **all of them get `shared_claim` and the
  name is never held**, so a contested name rules nothing out further down (a late third claimant joins the claim, it does
  not win it);
* a tag with no candidate list, or with two names of its own, is untouched.

Each round settles at least one tag, so the loop ends after at most as many rounds as there are open tags. The saved
`tags` record keeps its shape: a chained result is plain `elimination` (no new key). With elimination off the chain still
runs on the would-be names, so `would_be` shows what the switch changed on every link.

**Exclusion.** Runs only after the timeline rule's own gates (events readable, clock check passed, at least one named tag
agrees with the timeline, no named tag contradicted). Then:

| Condition | Why |
|---|---|
| exactly **one** tag still unnamed, with a note the other rules left for lack of an answer | with two open tags which is which is a guess; a tag with two names of its own holds two people |
| exactly **one** participant left over: not the bot, not the name of any named tag, lit by Meet for at least `MIN_SECONDS` (30 s) in the whole meeting; a rejoin is one person (grouped as `_name_by_timeline` groups) | a person who barely spoke cannot be a tag long enough to name |
| the person is on the tag's candidate list when it has one (a list too long to read: refused) and is the name the earlier rules had picked for the tag when they picked one they could not give | the model's own evidence is never overridden |
| the tag covers at least `MIN_EXCLUSION_TAG_SECONDS` = 90 s | the failing tag had 193 s; 90 s is three times `MIN_SECONDS`, about half of it; noise tags are never named by being left over |
| the person was lit over the tag for at least `MIN_SECONDS` (30 s) and `MIN_SHARE` (20%) of its seconds | same floors as the timeline rule; the share guard stops a tag whose real speaker has no Meet entry (a dial-in) from going to the one talker-over who is left over |
| **nobody else, the bot included, was lit over the tag as long or longer** | no margin is needed (being left over is the evidence), but Meet must not point at someone else: a tag lit mostly over a person who already has a tag is as likely an over-split of that person |

A lone open tag that fails these gets an `exclusion` key with the first reason (`not_eligible`, `participants`,
`not_on_list`, `disagrees`, `tag_too_short`, `too_little`, `small_share`, `beaten`). With several open tags no key is set.

**Decided and rejected.** `MIN_RATIO` is untouched (a lower global line would name meeting 39's weak tags). Rejected:
matching several open tags to several left-over people by their overlaps (a guess at scale); relaxing "one open tag" to
"one open tag per left-over person". The last row of the table is stricter than the brief's "not clearly beaten by a
different named person" on purpose: a wrong name is worse than "Unknown speaker".

## What's involved

| Path | Change |
|---|---|
| `app/skribby.py` `_earlier_rules` | the fixed-point rounds; `shared_tags` / `shared_keys` replace the one-shot `claims` |
| `app/skribby.py` `_name_by_exclusion`, `_name_by_timeline` | the new route, called last |
| `app/skribby.py` `apply_timeline` | reads the earlier rules again for tags **saved unnamed**, so a report on an old row shows what today's rules name (read-only) |
| `app/meet_timeline.py` | `Reading.speaking` (seconds each person was lit in the meeting), `MIN_EXCLUSION_TAG_SECONDS`, `unaccounted`, `exclusion_verdict` |
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
| `test_the_failure_names_the_tag_the_chain_reaches_not_unknown` | tag 2 is Charlie, `elimination`; also with no events and the timeline off |
| `test_with_elimination_off_...` | nothing is named, `would_be` is set on tags 1 and 2 |
| `test_the_labels_of_the_failure_carry_the_chained_name_...` | the labelled lines and the line correction see the new name; none is blank |
| chain-of-three (both orders), long chain, sole-candidate link | the fixed point, order independence, termination |
| shared-claim tests (three) | a contested name is neither given nor held; a late claimant joins the claim |
| bot / no-list / two-names tests | the existing safeguards survive the chain |
| `test_the_failure_without_candidate_lists_names_the_last_tag_by_exclusion` | tag 2 is Charlie, `meet_exclusion`; not without events |
| two open tags; two left-over people (and a quiet third who does not count); bot; list leaves person out; another person on top; too little; too short; small share; shifted clock; no agreeing named tag | each ambiguous or unsafe case stays Unknown, with the reason |
| `test_the_report_prints_the_route_...` | the report names the route and does not touch the saved record |

Meeting 39's tests and the 70+ older tests in the file pass unchanged except three one-tag cases whose fixture now keeps the
new routes out (they test the timeline rule alone: tag 9 listed Bo, whom tag 3 holds, which the chain now rightly resolves).
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
