# Line-level speaker correction: should a line follow Meet's timeline when the tag disagrees?

**Status:** design note, 2026-11-18. **BUILT 2026-11-19** (SOP `docs/sop/2026-11-19-line-level-speaker-correction.md`,
branch `feature/line-level-speaker-correction`), with Adebayo's decisions overriding this note where they differ:
**ON by default** with one emergency kill switch `SPEAKER_LINE_CORRECTION` (not default-off, no percent rollout or shadow
mode; sections 2, 7 and the end of 9 are superseded); the rule is **"exactly one person lit, nobody else"** with the
report's own defaults `--min-seconds 1` and `--ambiguous-ratio 0.25` as the production constants (the table in section 4
is superseded: no share of the line, no 4-word minimum, no smoothing, no "protect people with no light"); it runs in the
pipeline for `TRANSCRIPT_SOURCE=skribby` and in the rebuild script (section 5: not in `_segments` alone but through
`skribby._labelled`, the evidence keeps `corrected_to`). Sections 1, 3, 6, 8 and 10 still describe the problem, the
numbers, the risks and the alternative. Written first: the read-only measurement
`scripts/speaker_reattribution_report.py` (SOP `docs/sop/2026-11-18-speaker-reattribution-report.md`).

## 1. The question

Today a Skribby segment is credited to whoever its **diarization tag** is named after. Naming got
good (Skribby's own names, elimination, sole candidate, then Meet's timeline,
`docs/sop/2026-11-17-name-by-meet-timeline.md`): in meeting 40 every tag was named correctly. But a tag
can still hold other people's voices, and naming the tag cannot fix that, because the tag itself mixes
voices. Should production re-attribute a **segment** to the person Meet's own speaking timeline shows
speaking over it, when that person differs from the tag's person?

## 2. Recommendation (short)

**Not yet, and not before the measurement has been read on real meetings.** Run the report on meetings
38, 39 and 40 (section 9). Build the correction only if those runs pass the bar in section 9, and then
build it **per segment, behind a switch that defaults off**, with the rule in section 4. If the report
shows that most "moves" are Meet's indicator lagging or Meet missing a speaker, drop the idea and
spend the effort on the model setting (section 10) instead.

Why not simply switch it on: the signal is the platform's speaking light, not proof of who said a line;
it lights for only about half to two thirds of a person's speech and lags; and a wrong move is worse than a wrong tag
because it is invisible: the line looks confidently attributed. The first job is to find out how big the
problem really is, and that is what the script does.

## 3. What the numbers say, and one caveat

Handed over for meeting 40 (2026-11-18, three speakers, `elevenlabs/scribe-v2`), per
`skribby_segments_report.py`: the share of each tag's time that overlaps its named person speaking per
Meet was about 59% (Paul), 64% (Daniel) and 88% (Adebayo); Adebayo spoke 1063.6 s per Meet but only
692.2 s of it sits under his tag. The handoff recorded about 4% for meeting 39 in the same measure.

**Caveat that changes how to read them:** Meet's light covers only about half to two thirds of speech: the right
winners in meeting 39 held 39% to 61% of their tags' seconds (`app/meet_timeline.py`, `MIN_SHARE`), and
the meeting 34 bake-off read-out gave about 63% (a figure from that session's notes, not written in the
SOP). So 59% and 64% are *not* "41% and 36% wrong": a perfectly attributed tag would score about 60% there too. What does show a real mix-up is Adebayo's
370 s (1063.6 - 692.2) that sits under *other people's* tags. The right measure is therefore **"another
person was clearly lit over this line"**, not "the named person was not lit". That is what the script
counts, and it is why it separates `would move` from `ambiguous` and `no signal`.

## 4. The rule it would apply (proposal, to be tuned by the script)

A segment is re-credited to participant X only when **all** hold. Starting values are guesses from one
measured meeting each for the naming rule and none yet for this one; the script's output decides them.

| Condition | Proposed start | Why |
|---|---|---|
| Meet's clock is trusted | the existing clock check passes (`meet_timeline.read` is not `clock_shifted`, `clock_unplaced`, `no_events`); never uses `--offset` | A shifted clock moves every line wrongly; the naming rule already refuses in that case |
| Clear winner | X lit for at least 2 s over the segment, and at least 3 times the runner-up (script knobs: `--min-seconds 2 --ambiguous-ratio 0.34`) | A cough, a lag at a turn boundary or one stray second must not move a line |
| Share of the line | X lit over at least about 35% of the segment's own length (not in the script yet; the per-move line prints "lit a of b s" so it can be judged) | Meet lights for roughly half to two thirds of speech, so a majority of the line is too strict; a tiny sliver is too loose |
| **Never when two speak** | a runner-up with 1/3 of the top's seconds makes the segment ambiguous: stays | Crosstalk: the light cannot say who said the words |
| **Never when Meet has no signal** | nobody lit for the minimum seconds: stays | A dial-in, a muted tile or a gap: absence of a light is not evidence |
| Protect people with no light | if the tag's own person has no speaking events at all in the meeting, nothing moves out of their tag | A phone participant never lights; every line of theirs would look like someone else's |
| Not the bot, not a rejoin duplicate | the bot is never a destination; a person listed twice is one person (same grouping as naming) | Same as the naming rule |
| Enough words | the segment has at least about 4 words | A "yes" or "mm-hm" is exactly where the light lags; leave those |
| Smoothing | a move that would sit between two lines of one other person is kept; a move that flips A-B-A-B on short lines is not made | Keeps turns readable and stops flicker |

Unnamed tags (`Unknown speaker`) are the easiest case: a clear Meet speaker is a gain, from nobody to
somebody (the script counts these as moves, labelled `Unknown speaker -> X`).

## 5. Where it would plug in

At the one place where each segment already gets its name: `app/skribby.py::_segments` (called from
`map_bot` with the saved `speaker_events`), after `_resolve_tags` has named the tags. It would set the
segment's `speaker_name` from the rule above and otherwise leave the tag's name. The same function is
what `scripts/rebuild_meeting_from_evidence.py` calls, so a rebuild applies it too. The evidence row
(`skribby_segment_evidence`, 7 days) would keep the original tag and a `corrected_to` marker per segment,
**never text**, so the effect can be audited and undone.

## 6. Risks

| Risk | What goes wrong | Mitigation / status |
|---|---|---|
| **Several people on one Meet account, or a room mic** | Meet shows one participant lit for the whole room. Diarization may have separated the people correctly; the timeline cannot tell them apart | Tags in that room are named the same account, so same-name segments "agree" and do not move. But a *remote* person's line can be pulled into the room name when the remote person's light is missing. The "protect people with no light" and ambiguity rules limit it; the proof run must include a meeting with a shared account if one exists. Cannot be fully solved from this data |
| **Indicator lag** | The previous speaker stays lit for a fraction of a second into the next turn, and the new speaker's light starts late | **Not mitigated by the built rule (corrected 2026-11-19; this row used to say short lines are protected, which is false once the 2 s / 4-word / share conditions were dropped by decision).** A short line ("yes, I'll take it", 2 s) can be moved to the previous speaker when that speaker's light lags into it and the line's own speaker's light covers under 1 s: the previous speaker is then the one clearly lit. Accepted by Adebayo's rule ("exactly one person lit") because such lines rarely hold action items or decisions, the clock check stops a steady offset of 1 s or more from correcting anything, and `SPEAKER_LINE_CORRECTION=false` is the emergency off. An edge trim (ignore the first and last 0.5 to 1 s of a segment's overlap) is the cheap guard if live reading shows it; it is not built and is not a share rule |
| **Meet misses a speaker** | Only about half to two thirds of speech lights; a quiet person may never light | Absence is never a reason to move (rule above), only a clear *other* person |
| **Clock placement not proven live** | Event times are read as Unix ms placed from the bot's `recording` status; "not proven live" in the report. A fixed offset of a few seconds would make every move wrong | The clock check is a hard gate (the same one naming uses). The proof run reads its line first. Meeting 39's clock check said no shift of up to 30 s fits better, which is some evidence the placement is right |
| **Double-correcting the model** | When the tag is right and the light lags, the correction makes it wrong | The proof step counts moves a human judges wrong; the bar is in section 9 |
| **Unfalsifiable output** | A moved line looks confidently attributed | Audit marker in the evidence; the switch (section 7); a note on the page is not planned (no wording invented) |

## 7. Off switch

`SPEAKER_LINE_CORRECTION` (bool, **default false**), read in `app/config.py` like `name_by_meet_timeline`,
documented in `.env.example` and `deploy/README.md`. Per the 2026-11-17 decision (no dark shipping,
"dark" only as a flag's default), the build ships with the exact switch-on sequence and a proof command per
step, and is **exercised locally on saved evidence first**: the rebuild script's dry run prints how many
segments the rule would move (counts only, no words) with the switch set, on meetings 38, 39 and 40,
before anything is switched on for live meetings. Turning it off restores today's behaviour for new
meetings; a rebuilt meeting is restored with the rebuild script's `--restore`.

## 8. What it does to notes and Ask

* **Transcript:** `candidate()` merges consecutive segments of one speaker into one `[Name]:` turn, so
  moved lines change turn boundaries; a long turn can split. Smoothing (section 4) keeps this readable.
* **Notes:** generated from the labelled transcript at processing time. Decisions, tasks and "who said
  what" follow the labels, so moved lines change owners and "decided by" for the better when the move is
  right and worse when wrong. An existing meeting's notes change only on an explicit rebuild.
* **Ask:** retrieval passages carry the speaker labels and row numbers of the displayed transcript
  (`app/ask.py`, `transcript_rows` must split exactly as `web/lib/transcript.ts`). Corrections are
  applied **before the transcript is stored** so rows and the stored text agree. A stored meeting is never
  silently rewritten: citations that already point at row numbers would shift; only the rebuild script
  changes one, deliberately.
* **`speakers_incomplete`:** unaffected (it counts unnamed words); correct moves out of "Unknown
  speaker" would lower the unnamed share.

## 9. How to prove it before any switch-on (meetings 38, 39, 40)

Evidence is kept 7 days: 38 until about 2026-11-23, 39 until about 2026-11-24, 40 until about 2026-11-25.
Read the oldest first.

```bash
ssh haven-respite 'bash -s' <<'EOF'
cd /opt/beyric-note-taker
for id in 38 39 40; do
  echo "=============== meeting $id ==============="
  docker compose exec -T web python scripts/speaker_reattribution_report.py $id
done
EOF
```

Then once more with the stricter knobs (`--min-seconds 2 --ambiguous-ratio 0.34`). Read, per meeting:

1. **The clock line.** If it says `CLOCK WARNING`, that meeting proves nothing; do not count it.
2. **Move size.** Moved seconds per tag and the "per person" table: how much of each person's Meet time
   sits under another tag. If the moves are under about 5% of a meeting's speech, the problem is not
   worth the risk.
3. **The 15 printed moves**, read by a person who was in the meeting (Adebayo). Pass bar to proceed to a
   build: at least 13 of 15 judged right in each of meetings 39 and 40, **and** meeting 38 (four
   speakers, every tag right and named, speech clean) is a control: it should show few moves, and the
   ones it shows should be judged by the same standard. If a clean meeting shows many moves, the rule is
   noise.
4. **Ambiguous and no-signal seconds.** If most of the disagreement lands in `ambiguous` or `no
   signal`, the rule would do little even if right: then the better path is section 10.

Only if all four pass: build with the switch off, run the rebuild dry run on the same three meetings
(counts must match this report), then one live meeting read with the same script.

## 10. The alternative: a different model or diarization setting

The voice split is the model's job, and the bake-off already measured models on meeting 34 against Meet's
timeline: `elevenlabs/scribe-v2` separated four of six speakers where `soniox/stt-async-v6` separated one
(`docs/sop/2026-11-11-skribby-model-bakeoff.md`; one window, small speakers 5 to 16 s; the server has run
`elevenlabs/scribe-v2` since 2026-11-13, and meeting 40 is on it). So meeting 40's mixing is **what the best
measured model still does**, on a three-person call. Options in that SOP's frame: re-run the bake-off on
meeting 40's own window (`--score-against-meet`, about USD 0.21 per window for the four models, while its
audio is kept 7 days), look at whether Skribby offers a diarization setting per model, or our own
diarizing pass (`docs/sop/2026-11-10-diarize-experiment.md`, `scripts/diarize_experiment.py`). Any of
these fixes the **cause**, so it benefits notes, Ask and the transcript at once and carries no "Meet's light
was wrong" risk; the cost is money per meeting hour and another measurement round. A line-level correction
is cheap in money but sits on a weaker signal.

**Choosing:** the two are not exclusive. If the report shows large clear moves that humans judge right,
the correction is a cheap fix on top of the model; if it shows mostly ambiguity or lag, only the model
or diarization route is left.

## 11. Open decisions for Adebayo

1. Run the three-meeting proof (section 9) now, before the evidence expires: yes or no.
2. If it passes, a build behind `SPEAKER_LINE_CORRECTION` (default off): yes or no.
3. In parallel, spend about USD 0.21 on a bake-off window for meeting 40: yes or no.
