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

from app import meet_timeline, skribby
from tests.test_meet_timeline_naming import ADA, BO, BOT, CY, START, events_record, layout

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
    # Meet's clock is 20 s late: the timeline is refused (clock_shifted), but it is read again at
    # the shift that fits and agrees with the names, so nothing is vetoed and nothing is unchecked
    items, events = timeline_case(shift=20.0)
    assert skribby._timeline_reading(items, events).refused == "clock_shifted"
    tags = resolve(items, events)
    assert named(tags) == {"0": ADA, "1": BO}
    assert "unchecked" not in tags["0"]


def test_someone_who_spoke_with_no_tag_is_a_veto_even_when_the_clock_is_refused():
    table = {"0": ([150.0, 0.0, 60.0], 260.0), "1": ([2.0, 150.0, 0.0], 170.0)}
    items, events = timeline_case(table, people=(ADA, BO, CY), shift=20.0)
    assert resolve(items, events)["0"]["one_to_one"] == "extra_speaker"


def test_the_bot_in_the_participant_list_is_not_a_third_person():
    items, events = timeline_case(bot_listed=True)
    assert BOT in [p["name"] for p in events["participants"]]
    assert named(resolve(items, events)) == {"0": ADA, "1": BO}


@pytest.mark.parametrize("spelling", [ADA.upper(), f"  {ADA}  "])
def test_a_candidate_spelled_another_way_is_still_the_participant(spelling):
    items, events = timeline_case(listed={"0": [spelling], "1": [BO]})
    assert named(resolve(items, events))["0"].strip() == spelling.strip()


# ---- review fixes (2026-11-25): what the checks read, and what they cannot read ----

SMALL = {"0": ([200.0, 3.0, 0.0], 220.0), "1": ([2.0, 150.0, 0.0], 170.0)}  # (Ada, Bo, Cy)


def test_swapped_names_are_refused_even_when_the_clock_does_not_fit():
    # Meet's clock is 20 s late (clock_shifted): the timeline is read at zero and at the best
    # shift, and either one clearly putting a tag under the other person is a veto
    table = {
        "0": ([3.0, 200.0], 220.0),
        "1": ([150.0, 2.0], 170.0),
    }  # Bo over tag 0, Ada over tag 1
    items, events = timeline_case(table, shift=20.0)
    assert skribby._timeline_reading(items, events).refused == "clock_shifted"
    tags = resolve(items, events)
    assert named(tags) == {"0": "", "1": ""}
    assert tags["0"]["one_to_one"] == tags["1"]["one_to_one"] == "contradicted"


def test_swapped_names_are_refused_with_the_timeline_rule_off():
    # NAME_BY_MEET_TIMELINE only controls the older timeline-naming rule: the kept events are
    # still read by the vetoes
    table = {"0": ([3.0, 200.0], 220.0), "1": ([150.0, 2.0], 170.0)}
    items, events = timeline_case(table)
    tags = resolve(items, events, timeline=False)
    assert named(tags) == {"0": "", "1": ""}
    assert tags["0"]["one_to_one"] == "contradicted"
    assert named(resolve(*timeline_case(), timeline=False)) == {"0": ADA, "1": BO}


def test_a_candidate_not_in_the_call_is_refused_with_the_timeline_rule_off():
    items, events = timeline_case(people=(ADA, CY))
    tags = resolve(items, events, timeline=False)
    assert tags["0"]["one_to_one"] == "not_in_call"


def test_the_timeline_rule_off_still_leaves_older_timeline_naming_off():
    # the switch keeps its meaning: a tag the earlier rules cannot name is not named from Meet
    items = [*lines("0", 5, ADA, BO), *lines("1", 5, ADA, BO, start=100.0)]
    _, events = timeline_case()
    assert named(resolve(items, events, timeline=False)) == {"0": "", "1": ""}


def dropped(events):
    """The record as `_cap_events` leaves it when it is over the size cap (`events_dropped`): the
    names stay, every list of events is empty."""
    return {
        **events,
        "truncated": "events_dropped",
        "participants": [{"name": p["name"], "events": []} for p in events["participants"]],
    }


def test_events_dropped_still_checks_that_the_names_are_in_the_call():
    items, events = timeline_case(people=(ADA, CY))  # Bo is not in the call
    tags = resolve(items, dropped(events))
    assert named(tags) == {"0": "", "1": ""}
    assert tags["0"]["one_to_one"] == "not_in_call"


def test_events_dropped_names_by_shape_and_records_what_it_could_not_check():
    items, events = timeline_case()
    tags = resolve(items, dropped(events))
    assert named(tags) == {"0": ADA, "1": BO}
    assert tags["0"]["unchecked"] == tags["1"]["unchecked"] == "contradicted,extra_speaker"
    assert tags["0"]["how"] == "one_to_one"


def test_events_with_a_clock_that_cannot_be_placed_record_the_timeline_check_as_unchecked():
    items, events = timeline_case()
    events["recording_started_at"] = None  # Unix times and no `recording` time: clock_unplaced
    assert skribby._timeline_reading(items, events).refused == "clock_unplaced"
    tags = resolve(items, events)
    assert named(tags) == {"0": ADA, "1": BO}
    assert tags["0"]["unchecked"] == "contradicted"  # attendance was checked, the timeline was not


def test_a_lit_note_taker_is_not_a_third_person():
    items, events = timeline_case()
    zero = int(START.timestamp() * 1000)
    events["participants"][-1]["events"] = [  # the note taker lit for 90 s over nothing
        {"type": "started-speaking", "timestamp": zero + 5_000},
        {"type": "stopped-speaking", "timestamp": zero + 95_000},
    ]
    assert events["participants"][-1]["name"] == BOT
    assert named(resolve(items, events)) == {"0": ADA, "1": BO}


def test_a_candidate_list_over_the_length_cap_blocks_the_meeting():
    too_many = [f"Person {n} Example" for n in range(skribby.MAX_CANDIDATES + 1)]
    items = [*lines("0", 5, *too_many), *lines("1", 5, BO, start=100.0)]
    assert named(resolve(items)) == {"0": "", "1": ""}


@pytest.mark.parametrize(
    ("seconds", "named_both"), [(9.0, True), (9.9, True), (10.0, False), (30.0, False)]
)
def test_a_third_voice_of_ten_seconds_or_more_is_a_veto_under_ten_is_a_cough(seconds, named_both):
    # Cy is in the call and speaks `seconds` under no tag at all; the floor is
    # `meet_timeline.MIN_EXTRA_SECONDS`, well under the timeline rule's 30 s
    items, events = timeline_case(SMALL, people=(ADA, BO, CY), extra={CY: seconds})
    tags = resolve(items, events)
    assert (named(tags) == {"0": ADA, "1": BO}) is named_both
    if not named_both:
        assert tags["0"]["one_to_one"] == "extra_speaker"


def test_a_merged_third_voice_of_about_thirty_seconds_in_a_tag_is_a_veto():
    table = {"0": ([150.0, 0.0, 29.0], 260.0), "1": ([2.0, 150.0, 0.0], 170.0)}
    items, events = timeline_case(table, people=(ADA, BO, CY))
    assert resolve(items, events)["0"]["one_to_one"] == "extra_speaker"


def test_the_shifted_reading_alone_can_veto():
    # at zero shift Meet's lights are even over both tags (no clear leader, no veto); at the
    # shift that fits best they clearly put tag 0 under Bo and tag 1 under Ada: swapped names
    ada, bo = skribby._same(ADA), skribby._same(BO)
    reading = meet_timeline.Reading(
        cells={"0": {ada: 100.0, bo: 100.0}, "1": {ada: 80.0, bo: 80.0}},
        shifted_cells={"0": {ada: 3.0, bo: 200.0}, "1": {ada: 150.0, bo: 2.0}},
        totals={"0": 220.0, "1": 170.0},
        shown={ada: ADA, bo: BO},
        lit={ada: 233.0, bo: 202.0},
        refused="clock_shifted",
    )
    given = {"0": {}, "1": {}}
    listed = {"0": {ada: ADA}, "1": {bo: BO}}
    segments = {"0": 5, "1": 5}
    listing = {"0": {ada: 5}, "1": {bo: 5}}
    assert skribby._one_to_one(given, listed, segments, listing, set(), reading) == (
        None,
        "contradicted",
        "",
    )


@pytest.mark.parametrize("odd", [None, 5, ["x"], {"a": 1}])
def test_two_events_at_the_same_instant_with_an_odd_type_do_not_break_the_read(odd):
    # `speaking_intervals` used to sort `(time, type)` pairs: equal times then compared the types,
    # and a missing or non-string type raised TypeError, which would have stopped the whole
    # transcript fetch, not just the naming
    items, events = timeline_case()
    stamp = events["participants"][0]["events"][0]["timestamp"]
    events["participants"][0]["events"] += [
        {"type": odd, "timestamp": stamp},
        {"timestamp": stamp},
        {"type": "started-speaking", "timestamp": stamp},
    ]
    assert meet_timeline.speaking_intervals(events)[ADA]  # the sort itself does not raise
    assert meet_timeline.read(items, events, lambda n: (n, n)).refused is None
    tags = resolve(items, events)  # does not raise
    assert named(tags) == {"0": ADA, "1": BO}


def test_a_timeline_that_cannot_be_read_at_all_is_no_events_not_a_failure(monkeypatch):
    def boom(*args, **kwargs):
        raise TypeError("unreadable")

    monkeypatch.setattr(meet_timeline, "read", boom)
    items, events = timeline_case()
    tags = resolve(items, events)  # naming goes on without Meet; the transcript is still saved
    assert named(tags) == {"0": ADA, "1": BO}  # by shape alone, as without events
