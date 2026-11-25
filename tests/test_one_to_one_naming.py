"""Naming every speaker tag of a meeting where Skribby named none (`how: one_to_one`).

The shape that raised it is meeting 46 (Google Meet, 2026-11-25, two people): tag 0 listed one name
on every one of its 85 segments, tag 1 another name on every one of its 84, Skribby set no
`speaker_name`, so the sole-candidate rule (needs one tag Skribby named) and the Meet-timeline rule
(needs one named tag to prove the clock) had no anchor and the transcript stayed unnamed. The rule:
every tag has exactly one candidate on every segment, no two tags share it, tags and names are as
many, and, when Meet's events are readable, the call holds nobody else who spoke, every name is in
the call, and Meet's timeline does not clearly put a tag under someone else. Docs:
docs/sop/2026-11-25-speaker-naming-one-to-one.md. Names and words are made up.
"""

import pytest

from app import skribby
from tests.test_meet_timeline_naming import ADA, BO, BOT, CY, events_record, layout

CASE_46 = {"0": ([200.0, 3.0], 220.0), "1": ([2.0, 150.0], 170.0)}  # (Ada, Bo) seconds, total


def lines(tag, count, *names, start=0.0):
    """`count` segments of one tag, each listing exactly `names` as `potential_speaker_names`, no
    `speaker_name` (Skribby named nobody)."""
    return [
        {
            "speaker": tag,
            "speaker_name": None,
            "potential_speaker_names": [{"name": n} for n in names],
            "start": start + n,
            "end": start + n + 0.9,
            "transcript": f"placeholder words {tag} {n}",
        }
        for n in range(count)
    ]


def resolve(items, events=None, *, elimination=True, timeline=True):
    return skribby._resolve_tags(items, (BOT,), elimination, events, timeline)


def two_people(*, tag_a=(ADA,), tag_b=(BO,), count=5):
    return [*lines("0", count, *tag_a), *lines("1", count, *tag_b, start=100.0)]


def named(tags):
    return {tag: info["name"] for tag, info in tags.items()}


def timeline_case(table=CASE_46, listed=None, people=(ADA, BO), extra=None, **kw):
    """Skribby's items and Meet's events laid out from `table` (`layout` of the timeline tests)."""
    listed = listed or {"0": [ADA], "1": [BO]}
    # a person in `people` beyond the table's columns is in the call and never lit
    table = {
        tag: ([*cells, *[0.0] * (len(people) - len(cells))], total)
        for tag, (cells, total) in table.items()
    }
    items, spans = layout(table, listed=listed, given={}, people=list(people), extra=extra)
    return items, events_record(spans, **kw)


# ---- the meeting-46 shape ----


def test_two_tags_with_one_name_each_on_every_segment_are_both_named():
    tags = resolve(two_people())
    assert tags["0"] == {"name": ADA, "how": "one_to_one", "note": ""}
    assert tags["1"] == {"name": BO, "how": "one_to_one", "note": ""}


def test_meeting_46s_numbers_with_meet_agreeing_name_both_and_say_nothing_else():
    items, events = timeline_case()
    tags = resolve(items, events)
    assert tags["0"] == {"name": ADA, "how": "one_to_one", "note": ""}
    assert tags["1"] == {"name": BO, "how": "one_to_one", "note": ""}


def test_without_any_events_the_rule_still_names_a_meeting_with_no_timeline():
    # Zoom and Teams carry no speaker events: the structure alone decides there
    assert named(resolve(two_people(), None)) == {"0": ADA, "1": BO}


def test_the_transcript_lines_carry_the_names(monkeypatch):
    body = {"transcript": two_people()}
    out = skribby._segments(body, (BOT,), True, None, False)
    assert [seg["speaker"] for seg in out] == [ADA] * 5 + [BO] * 5


def test_a_third_person_who_is_silent_does_not_stop_it():
    items, events = timeline_case(people=(ADA, BO, CY))  # Cy is in the call, never lit
    assert named(resolve(items, events)) == {"0": ADA, "1": BO}


def test_three_tags_for_three_people_are_named_too():
    items = [*lines("0", 4, ADA), *lines("1", 4, BO, start=50.0), *lines("2", 4, CY, start=100.0)]
    assert named(resolve(items)) == {"0": ADA, "1": BO, "2": CY}


# ---- the switch ----


def test_with_elimination_off_nothing_is_named_but_would_be_is_recorded():
    tags = resolve(two_people(), elimination=False)
    assert tags["0"] == {"name": "", "how": "unknown", "note": "elimination_off", "would_be": ADA}
    assert tags["1"] == {"name": "", "how": "unknown", "note": "elimination_off", "would_be": BO}


# ---- shapes that must stay unnamed ----


def test_three_voices_merged_into_two_tags_stay_unnamed_meeting_32_shape():
    # Cy spoke 60 s inside Ada's tag; Skribby lists one name per tag; Meet lit Cy but no tag is his
    table = {"0": ([150.0, 0.0, 60.0], 260.0), "1": ([2.0, 150.0, 0.0], 170.0)}
    items, events = timeline_case(table, people=(ADA, BO, CY))
    tags = resolve(items, events)
    assert named(tags) == {"0": "", "1": ""}
    assert tags["0"]["one_to_one"] == tags["1"]["one_to_one"] == "extra_speaker"
    assert tags["0"]["how"] == "unknown"


def test_a_merged_tag_that_lists_both_people_stays_unnamed():
    # the realistic form of the same meeting: the merged tag lists two names
    tags = resolve(two_people(tag_a=(ADA, CY)))
    assert named(tags) == {"0": "", "1": ""}
    assert "one_to_one" not in tags["0"]  # not even a candidate for the rule


def test_a_tag_whose_only_candidate_is_the_bot_names_nothing():
    tags = resolve(two_people(tag_a=(BOT,)))
    assert named(tags) == {"0": "", "1": ""}


def test_a_tag_with_no_candidate_names_nothing_for_either_tag():
    items = [*lines("0", 5), *lines("1", 5, BO, start=100.0)]
    tags = resolve(items)
    assert named(tags) == {"0": "", "1": ""}
    assert tags["0"]["note"] == "no_candidates"


def test_two_tags_with_the_same_single_candidate_both_stay_unknown():
    assert named(resolve(two_people(tag_a=(ADA,), tag_b=(ADA,)))) == {"0": "", "1": ""}


def test_a_tag_with_two_candidates_stays_unknown_and_so_does_the_other():
    tags = resolve(two_people(tag_a=(ADA, CY)))
    assert named(tags) == {"0": "", "1": ""}
    assert tags["1"]["how"] == "unknown"


def test_a_tag_with_fewer_than_three_segments_blocks_the_meeting():
    items = [*lines("0", 5, ADA), *lines("1", 2, BO, start=100.0)]
    assert named(resolve(items)) == {"0": "", "1": ""}
    items = [*lines("0", 3, ADA), *lines("1", 3, BO, start=100.0)]
    assert named(resolve(items)) == {"0": ADA, "1": BO}  # three is enough


def test_a_name_missing_from_one_segment_of_a_tag_blocks_the_meeting():
    items = two_people()
    items[2]["potential_speaker_names"] = []
    assert named(resolve(items)) == {"0": "", "1": ""}


def test_a_single_tag_is_never_named_this_way():
    # one tag cannot tell one person from a merged crowd
    assert named(resolve(lines("0", 8, ADA))) == {"0": ""}


def test_a_meeting_where_skribby_named_a_tag_goes_by_the_older_rules():
    items = [
        *[{**seg, "speaker_name": ADA} for seg in lines("0", 4, ADA)],
        *lines("1", 4, BO, start=50.0),
    ]
    tags = resolve(items)
    assert tags["0"]["how"] == "given"
    assert tags["1"]["how"] == "sole_candidate"  # the existing rule, not this one


# ---- the checks against Meet ----


def test_a_candidate_who_is_not_in_the_call_names_nothing():
    # the call has Ada and Cy; the tags list Ada and Bo, who is not there
    table = {"0": ([200.0, 3.0], 220.0), "1": ([2.0, 150.0], 170.0)}
    items, events = timeline_case(table, people=(ADA, CY))
    tags = resolve(items, events)
    assert named(tags) == {"0": "", "1": ""}
    assert tags["0"]["one_to_one"] == "not_in_call"


def test_meet_clearly_putting_a_tag_under_someone_else_names_nothing_contradicted():
    # the names are swapped: Meet lights Bo over tag 0 and Ada over tag 1
    table = {"0": ([3.0, 200.0], 220.0), "1": ([150.0, 2.0], 170.0)}
    items, events = timeline_case(table)
    tags = resolve(items, events)
    assert named(tags) == {"0": "", "1": ""}
    assert tags["0"]["one_to_one"] == tags["1"]["one_to_one"] == "contradicted"


def test_an_unclear_timeline_does_not_veto():
    # equal seconds for both people over each tag: no clear leader, no contradiction
    table = {"0": ([100.0, 100.0], 220.0), "1": ([80.0, 80.0], 170.0)}
    items, events = timeline_case(table)
    assert named(resolve(items, events)) == {"0": ADA, "1": BO}


def test_a_clock_that_does_not_fit_leaves_the_timeline_out_but_not_the_attendance_check():
    # Meet's clock is 20 s late: the timeline is refused (clock_shifted), so no veto from it; the
    # call still holds exactly the two people, so the structure decides
    items, events = timeline_case(shift=20.0)
    assert named(resolve(items, events)) == {"0": ADA, "1": BO}


def test_someone_who_spoke_with_no_tag_is_a_veto_even_when_the_clock_is_refused():
    table = {"0": ([150.0, 0.0, 60.0], 260.0), "1": ([2.0, 150.0, 0.0], 170.0)}
    items, events = timeline_case(table, people=(ADA, BO, CY), shift=20.0)
    assert named(resolve(items, events)) == {"0": "", "1": ""}


def test_the_bot_in_the_participant_list_is_not_a_third_person():
    items, events = timeline_case(bot_listed=True)
    assert BOT in [p["name"] for p in events["participants"]]
    assert named(resolve(items, events)) == {"0": ADA, "1": BO}


def test_with_the_timeline_rule_off_no_events_are_read_and_the_structure_decides():
    # with the timeline rule off no events are read: the structure alone, as without events
    items, events = timeline_case()
    assert named(resolve(items, events, timeline=False)) == {"0": ADA, "1": BO}


@pytest.mark.parametrize("spelling", [ADA.upper(), f"  {ADA}  "])
def test_a_candidate_spelled_another_way_is_still_the_participant(spelling):
    items, events = timeline_case(listed={"0": [spelling], "1": [BO]})
    assert named(resolve(items, events))["0"].strip() == spelling.strip()


def test_a_refused_clock_is_not_read_for_a_contradiction():
    # Meet's clock is 20 s late, so its overlaps say nothing about which tag is whose: no veto
    # from the timeline (the attendance checks still ran and passed)
    table = {"0": ([3.0, 200.0], 220.0), "1": ([150.0, 2.0], 170.0)}  # swapped, as above
    items, events = timeline_case(table, shift=20.0)
    assert skribby._timeline_reading(items, events).refused == "clock_shifted"
    assert named(resolve(items, events)) == {"0": ADA, "1": BO}
