"""The relaxed naming gates (docs/sop/2026-11-25-speaker-naming-relaxed.md): what now names a
meeting that used to stay blank, and what must still stay blank. Names and words are made up.

Part 1, the meeting-46 shape: two tags, one name each, a third person lit for a small share of
all lit time. The tags are named (`how: one_to_one`, `note: extra_speaker_small`), the transcript
is marked incomplete, and the line correction may move that person's lines to them although no tag
carries their name.

Part 2, the review: a stray tiny tag no longer blocks the meeting; a name listed on most (not all)
of a tag's segments stands for it; a candidate spelled a little differently from Meet's name is the
one participant it fits; a tag is named from Meet's timeline without a named tag when its own
candidates hold Meet's leader (the last one is in `tests/test_meet_timeline_naming.py`).
"""

import pytest

from app import skribby, speaker_correction, transcript_source
from tests.test_meet_timeline_naming import ADA, BO, BOT, CY, DI, EVE, FLO, START, events_record
from tests.test_one_to_one_naming import SMALL, lines, named, resolve, timeline_case


def meeting_46_like():
    """Ada's tag holds two of Cy's lines (20 s of 240 s lit: 8.3%), Bo's tag is clean; Skribby
    lists one name per tag on every segment. Every segment sits on its own stretch of time, 40 s
    apart, so no shift of Meet's clock fits better."""
    items, spans, at = [], {ADA: [], BO: [], CY: []}, 0.0
    plan = [("0", ADA, ADA)] * 12 + [("0", CY, ADA)] * 2 + [("1", BO, BO)] * 10
    for index, (tag, lit, listed) in enumerate(plan):
        items.append(
            {
                "speaker": tag,
                "speaker_name": None,
                "potential_speaker_names": [{"name": listed}],
                "start": at,
                "end": at + 10.0,
                "transcript": f"placeholder words {index}",
            }
        )
        spans[lit].append((at, at + 10.0))
        at += 50.0
    return items, events_record(spans)


# ---- Part 1: the extra person's lines may move to them ----


def test_a_line_of_a_person_with_no_tag_moves_to_them_when_the_naming_said_they_spoke():
    items, events = meeting_46_like()
    out = skribby._segments({"transcript": items}, (BOT,), True, events, True, True)
    assert [s["speaker"] for s in out[:12]] == [ADA] * 12
    assert [s["speaker"] for s in out[12:14]] == [CY, CY]  # Cy's two lines, inside Ada's tag
    assert [s["speaker"] for s in out[14:]] == [BO] * 10


def test_with_the_correction_off_the_lines_stay_under_the_tags_name():
    items, events = meeting_46_like()
    out = skribby._segments({"transcript": items}, (BOT,), True, events, True, False)
    assert [s["speaker"] for s in out[12:14]] == [ADA, ADA]


def test_without_the_note_a_line_is_never_moved_to_a_person_with_no_tag():
    items, events = meeting_46_like()
    tags = skribby._resolve_tags(items, (BOT,), True, events, True)
    assert {t["note"] for t in tags.values()} == {"extra_speaker_small"}
    plain = {tag: {**info, "note": ""} for tag, info in tags.items()}
    moved = speaker_correction.correct(items, plain, events, (BOT,))
    assert moved.moves == {}  # the destination guard of `resolve_label` is unchanged elsewhere
    assert speaker_correction.correct(items, tags, events, (BOT,)).moves == {12: CY, 13: CY}


def test_a_person_lit_under_ten_seconds_is_not_a_destination_even_with_the_note():
    # Cy is lit for 5 s over one line of Ada's tag: a cough, not a speaker
    items, spans, at = [], {ADA: [], BO: [], CY: []}, 0.0
    plan = [("0", ADA, 10.0)] * 12 + [("0", CY, 5.0)] + [("1", BO, 10.0)] * 10
    for index, (tag, who, seconds) in enumerate(plan):
        listed = ADA if tag == "0" else BO
        items.append(
            {
                "speaker": tag,
                "speaker_name": None,
                "potential_speaker_names": [{"name": listed}],
                "start": at,
                "end": at + seconds,
                "transcript": f"placeholder words {index}",
            }
        )
        spans[who].append((at, at + seconds))
        at += seconds + 40.0
    events = events_record(spans)
    tags = skribby._resolve_tags(items, (BOT,), True, events, True)
    assert {t["note"] for t in tags.values()} == {""}  # under 10 s: no note, nobody extra
    assert speaker_correction.correct(items, tags, events, (BOT,)).moves == {}


def test_with_the_note_only_the_people_lit_ten_seconds_or_more_are_destinations():
    # Cy is lit 20 s (the note); Di, in the call, is lit 3 s over one more line of Ada's tag
    items, events = meeting_46_like()
    at = items[-1]["end"] + 50.0
    items.append(
        {
            "speaker": "0",
            "speaker_name": None,
            "potential_speaker_names": [{"name": ADA}],
            "start": at,
            "end": at + 3.0,
            "transcript": "placeholder words extra",
        }
    )
    zero = int(START.timestamp() * 1000)
    events["participants"].append(
        {
            "name": DI,
            "events": [
                {"type": "started-speaking", "timestamp": zero + int(at * 1000)},
                {"type": "stopped-speaking", "timestamp": zero + int((at + 3.0) * 1000)},
            ],
        }
    )
    tags = skribby._resolve_tags(items, (BOT,), True, events, True)
    moved = speaker_correction.correct(items, tags, events, (BOT,)).moves
    assert moved == {12: CY, 13: CY}  # Di's 3 s line stays under Ada


def test_the_note_does_not_move_a_line_to_the_tags_own_other_person():
    # the destination list holds the extra people only, never a second spelling of a tagged person
    items, events = meeting_46_like()
    tags = skribby._resolve_tags(items, (BOT,), True, events, True)
    placed = speaker_correction.place(events, (BOT,)).people
    assert speaker_correction.extra_names(tags, placed) == [CY]


def test_the_transcript_is_marked_incomplete_whatever_is_named():
    out = [{"speaker": ADA, "text": "some placeholder words here"}] * 4
    assert transcript_source.candidate(out).complete is True
    assert transcript_source.candidate(out, None, False).complete is True
    found = transcript_source.candidate(out, None, True)
    assert found.complete is False and found.other_speakers is True


def test_the_evidence_keeps_the_note_so_the_flag_can_be_read_again():
    items, events = meeting_46_like()
    record = skribby._evidence({"transcript": items}, (BOT,), True, events, True)
    assert skribby.holds_other_speakers(record["tags"])
    assert not skribby.holds_other_speakers(None)
    assert not skribby.holds_other_speakers({"0": {"name": ADA, "how": "given", "note": ""}})


@pytest.mark.parametrize("timeline", [False, True])
def test_elimination_off_records_would_be_and_no_name_with_the_timeline_on_or_off(timeline):
    # the off switch means no derived name: the timeline rule may not name a one-to-one tag
    items, events = meeting_46_like()
    tags = skribby._resolve_tags(items, (BOT,), False, events, timeline)
    assert {t["name"] for t in tags.values()} == {""}
    assert {t["note"] for t in tags.values()} == {"elimination_off"}
    assert {t["would_be"] for t in tags.values()} == {ADA, BO}
    assert not skribby.holds_other_speakers(tags)
    out = skribby._segments({"transcript": items}, (BOT,), False, events, timeline, True)
    assert {s["speaker"] for s in out} == {""}


@pytest.mark.parametrize("seconds", [50.0, 90.0])
def test_a_large_extra_share_stays_blank_with_the_timeline_on(seconds):
    # Cy lit 12% and 20% of all lit time: the owner's policy is that this stays blank, and the
    # timeline's candidate-only mode must not name the tags round the veto
    items, events = timeline_case(SMALL, people=(ADA, BO, CY), extra={CY: seconds})
    for timeline in (True, False):
        tags = resolve(items, events, timeline=timeline)
        assert named(tags) == {"0": "", "1": ""}
        assert tags["0"]["one_to_one"] == "extra_speaker"
        assert not skribby.holds_other_speakers(tags)


def test_many_small_extras_are_one_share_for_the_veto():
    # four people lit 9.9 s each (none reaches 10 s alone): together 39.6 of 394.6 s, 10.04%
    people = (ADA, BO, CY, DI, EVE, FLO)
    extra = {CY: 9.9, DI: 9.9, EVE: 9.9, FLO: 9.9}
    items, events = timeline_case(SMALL, people=people, extra=extra)
    for timeline in (True, False):
        tags = resolve(items, events, timeline=timeline)
        assert named(tags) == {"0": "", "1": ""}
        assert tags["0"]["one_to_one"] == "extra_speaker"


def test_a_few_small_extras_together_under_the_share_are_named_with_the_note():
    # two people lit 6 s each: 12 s together is over the floor and under 10% of the lit time
    items, events = timeline_case(SMALL, people=(ADA, BO, CY, DI), extra={CY: 6.0, DI: 6.0})
    tags = resolve(items, events, timeline=False)
    assert named(tags) == {"0": ADA, "1": BO}
    assert {t["note"] for t in tags.values()} == {"extra_speaker_small"}


def test_extras_that_do_not_add_up_to_ten_seconds_are_coughs():
    people = (ADA, BO, CY, DI, EVE, FLO)
    extra = {CY: 2.0, DI: 2.0, EVE: 2.0, FLO: 2.0}
    items, events = timeline_case(SMALL, people=people, extra=extra)
    tags = resolve(items, events, timeline=False)
    assert named(tags) == {"0": ADA, "1": BO}
    assert {t["note"] for t in tags.values()} == {""}


def test_a_candidate_that_only_fits_the_note_taker_is_not_in_the_call():
    # "Beyric" is a word of the note taker's own name and of nobody else's
    items, events = timeline_case(listed={"0": ["Beyric"], "1": [BO]})
    assert BOT in [p["name"] for p in events["participants"]]
    tags = resolve(items, events, timeline=False)
    assert tags["0"]["name"] == "" and tags["0"]["one_to_one"] == "not_in_call"


def test_a_tag_named_with_a_shorter_spelling_does_not_make_the_long_one_a_destination():
    # tag 0 lists "Ada", the call shows "Ada Example": one person, never a second destination
    items, events = meeting_46_like()
    for item in items:
        if item["speaker"] == "0":
            item["potential_speaker_names"] = [{"name": "Ada"}]
    tags = skribby._resolve_tags(items, (BOT,), True, events, True)
    assert tags["0"]["name"] == "Ada" and tags["0"]["note"] == "extra_speaker_small"
    placed = speaker_correction.place(events, (BOT,)).people
    assert speaker_correction.extra_names(tags, placed) == [CY]


def test_a_larger_extra_share_still_refuses_and_nothing_moves():
    # Cy lit 60 s of 362: 17%, the meeting-32 shape. Refused as before; Ada's tag is not named
    table = {"0": ([150.0, 0.0, 60.0], 260.0), "1": ([2.0, 150.0, 0.0], 170.0)}
    items, events = timeline_case(table, people=(ADA, BO, CY))
    tags = resolve(items, events)
    assert tags["0"]["name"] == "" and tags["0"]["one_to_one"] == "extra_speaker"


# ---- Part 2: a stray tiny tag ----


def with_stray(*names, count=2):
    """Ada's and Bo's tags (50 segments each) and a tag "2" of `count` segments listing `names`."""
    return [
        *lines("0", 50, ADA),
        *lines("1", 50, BO, start=100.0),
        *lines("2", count, *names, start=200.0),
    ]


def test_a_stray_tag_with_two_names_no_longer_blocks_the_meeting():
    tags = resolve(with_stray(ADA, CY))
    assert named(tags) == {"0": ADA, "1": BO, "2": ""}
    assert tags["2"]["how"] == "unknown"


def test_a_stray_tag_with_no_candidate_does_not_block_either():
    assert named(resolve(with_stray()))["0"] == ADA


def test_a_stray_tag_repeating_a_name_another_tag_has_stays_unnamed():
    assert named(resolve(with_stray(ADA))) == {"0": ADA, "1": BO, "2": ""}


def test_a_clean_stray_tag_with_a_name_of_its_own_is_named_too():
    items = with_stray(CY, count=3)
    assert named(resolve(items)) == {"0": ADA, "1": BO, "2": CY}


def test_a_stray_is_a_tag_under_five_percent_of_the_segments():
    # 5 of 100 segments is the line: a tag at 5% is a person and blocks, a tag at 4% is a stray
    items = [*lines("0", 48, ADA), *lines("1", 47, BO, start=100.0)]
    five = [*items, *lines("2", 5, ADA, CY, start=200.0)]
    assert named(resolve(five)) == {"0": "", "1": "", "2": ""}
    four = [*lines("0", 48, ADA), *lines("1", 48, BO, start=100.0), *lines("2", 4, ADA, CY)]
    assert named(resolve(four))["0"] == ADA


def test_a_big_tag_that_fails_still_blocks_every_tag():
    # tag 1 holds a third of the segments and lists two names: the merged-crowd shape
    items = [*lines("0", 20, ADA), *lines("1", 10, BO, CY, start=100.0)]
    assert named(resolve(items)) == {"0": "", "1": ""}


def test_one_big_tag_and_a_stray_is_still_one_tag_and_stays_unnamed():
    items = [*lines("0", 60, ADA), *lines("1", 2, BO, start=100.0)]
    assert named(resolve(items)) == {"0": "", "1": ""}


def test_a_stray_does_not_hide_a_third_person_from_the_extra_speaker_check():
    # the stray's person is lit 90 s with no tag of their own: refused, as without a stray
    table = {"0": ([200.0, 3.0, 0.0], 220.0), "1": ([2.0, 150.0, 0.0], 170.0)}
    items, events = timeline_case(table, people=(ADA, BO, CY), extra={CY: 90.0})
    items += lines("0", 60, ADA, start=1000.0)  # filler: unlit, so the stray is under 5%
    items += lines("1", 60, BO, start=2000.0)
    items += lines("2", 2, CY, start=500.0)
    tags = resolve(items, events, timeline=False)
    assert tags["0"]["name"] == "" and tags["0"]["one_to_one"] == "extra_speaker"


# ---- Part 2: a name on most of a tag's segments ----


def test_the_sole_candidate_may_be_missing_from_a_fifth_of_a_tags_segments():
    given = [{**seg, "speaker_name": ADA} for seg in lines("0", 4, ADA)]
    five = lines("1", 5, BO, start=50.0)
    five[0]["potential_speaker_names"] = []
    tags = resolve([*given, *five])
    assert tags["1"] == {"name": BO, "how": "sole_candidate", "note": ""}


def test_the_sole_candidate_missing_from_two_of_five_segments_is_not_named():
    given = [{**seg, "speaker_name": ADA} for seg in lines("0", 4, ADA)]
    five = lines("1", 5, BO, start=50.0)
    five[0]["potential_speaker_names"] = []
    five[1]["potential_speaker_names"] = []
    assert resolve([*given, *five])["1"]["name"] == ""


def test_the_sole_candidate_still_needs_a_tag_skribby_named():
    # nothing Skribby named, one tag with a clean name: this rule alone does not name it
    assert named(resolve(lines("0", 8, ADA))) == {"0": ""}


def test_a_second_name_on_any_segment_still_blocks():
    items = [
        *lines("0", 9, ADA),
        *lines("0", 1, ADA, CY, start=30.0),
        *lines("1", 10, BO, start=100.0),
    ]
    assert named(resolve(items)) == {"0": "", "1": ""}


# ---- Part 2: a name spelled a little differently from Meet's ----


def test_a_candidate_that_is_the_one_participant_it_fits_is_in_the_call():
    # Skribby lists "Ada", Meet shows "Ada Example": one participant fits, so she is in the call
    # and is not also counted as a third person lit for 200 s
    items, events = timeline_case(listed={"0": ["Ada"], "1": [BO]})
    tags = resolve(items, events)
    assert tags["0"] == {"name": "Ada", "how": "one_to_one", "note": ""}
    assert tags["1"]["name"] == BO


def test_a_candidate_that_fits_two_participants_is_not_in_the_call():
    # "Ada" fits "Ada Example" and "Ada Obi": which of the two is unknown, so no name from the rule
    table = {"0": ([200.0, 3.0, 0.0], 220.0), "1": ([2.0, 150.0, 0.0], 170.0)}
    items, events = timeline_case(
        table, listed={"0": ["Ada"], "1": [BO]}, people=(ADA, BO, "Ada Obi")
    )
    tags = resolve(items, events, timeline=False)
    assert tags["0"]["name"] == "" and tags["0"]["one_to_one"] == "not_in_call"


@pytest.mark.parametrize("listed", ["Cy Example", "Dee", "Ada Obi"])
def test_a_candidate_that_fits_nobody_in_the_call_is_not_in_the_call(listed):
    items, events = timeline_case(listed={"0": [listed], "1": [BO]})
    tags = resolve(items, events, timeline=False)
    assert tags["0"]["name"] == "" and tags["0"]["one_to_one"] == "not_in_call"
