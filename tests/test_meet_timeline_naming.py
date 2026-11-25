"""Naming a Skribby speaker tag from Meet's own speaking indicator (`meet_timeline`).

Every fixture is built from NUMBERS: seconds of overlap between each tag's segments and each
participant speaking, laid out on a timeline with made-up names and placeholder words. The table
below has the shape of the report of meeting 39 (2026-11-17, six speakers, nine tags; the real
names and the real words are not in this repository): `Ada` is the person who talked most and
whom the model split over three tags, `Bo` and `Cy` spoke little, `Di` was split over two tags,
`Flo` has one clean tag. Docs: docs/sop/2026-11-17-name-by-meet-timeline.md.
"""

from datetime import UTC, datetime
from unittest.mock import patch

import pytest
from pydantic import ValidationError

from app import meet_timeline, skribby, transcript_source
from app.config import Settings, get_settings
from app.skribby import SkribbyClient, SkribbyProvider, map_bot

BOT = "Beyric Note Taker (recording)"
ADA, BO, CY, DI, EVE, FLO = (f"{n} Example" for n in ("Ada", "Bo", "Cy", "Di", "Eve", "Flo"))
PEOPLE = [ADA, BO, CY, DI, EVE, FLO]

# tag -> (seconds each of PEOPLE was speaking over the tag's segments, the tag's total seconds)
TABLE = {
    "0": ([209.0, 0.0, 2.7, 39.5, 4.2, 7.0], 441.8),
    "1": ([4.8, 0.0, 34.0, 1.8, 0.0, 0.4], 81.3),
    "2": ([75.9, 0.0, 15.0, 0.5, 14.0, 4.1], 195.3),
    "3": ([0.6, 58.6, 0.0, 0.0, 2.4, 1.7], 131.2),
    "4": ([7.6, 1.4, 0.0, 10.1, 0.0, 333.1], 548.0),
    "5": ([449.0, 8.4, 1.7, 14.4, 0.0, 13.8], 1029.8),
    "6": ([15.5, 0.0, 0.3, 134.5, 0.0, 37.9], 274.5),
    "7": ([0.0, 0.3, 0.0, 11.5, 0.0, 4.7], 35.3),
    "8": ([10.9, 0.0, 0.0, 7.5, 0.0, 6.6], 33.9),
}
# What Skribby itself gave, as in the report: tag 4 named on its own segments; tags 1 and 3 a sole
# candidate on every segment; tags 0, 2 and 5 all claim `Ada` (shared_claim); tags 6, 7 and 8 list
# two people each (unresolved).
GIVEN = {"4": FLO}
LISTED = {
    "0": [ADA],
    "1": [CY],
    "2": [ADA],
    "3": [BO],
    "5": [ADA],
    "6": [DI, EVE],
    "7": [DI, EVE],
    "8": [ADA, DI],
}
GAP = 60.0  # silence between blocks: wider than the clock fit's 30 s, so shifting never helps
START = datetime(2026, 11, 17, 10, 0, tzinfo=UTC)


def layout(table, listed=LISTED, given=GIVEN, people=PEOPLE, extra=None):
    """Skribby's transcript items and each person's speaking spans (seconds) for `table`: one block
    of time per (tag, person) cell where that person speaks and the tag's segment plays, plus one
    silent block per tag for the rest of its total. Blocks never overlap each other. `extra` maps
    a name to seconds that person speaks under no segment at all (a block with no item)."""
    items, spans, at, word = [], {name: [] for name in people}, 0.0, 0
    for tag, (cells, total) in table.items():
        blocks = [(name, seconds) for name, seconds in zip(people, cells, strict=True) if seconds]
        blocks.append((None, total - sum(cells)))
        for who, seconds in blocks:
            if seconds <= 0:
                continue
            items.append(
                {
                    "speaker": tag,
                    "speaker_name": given.get(tag),
                    "potential_speaker_names": [{"name": n} for n in listed.get(tag, [])],
                    "start": at,
                    "end": at + seconds,
                    # placeholder words, all different (a transcript of one repeated word is a loop)
                    "transcript": " ".join(f"w{word + n}" for n in range(max(1, round(seconds)))),
                }
            )
            word += max(1, round(seconds))
            if who:
                spans[who].append((at, at + seconds))
            at += seconds + GAP
    for name, seconds in (extra or {}).items():
        spans[name].append((at, at + seconds))
        at += seconds + GAP
    return items, spans


def events_record(spans, *, shift=0.0, visits=None, bot_listed=True):
    """The kept speaker events for `spans`, as Unix milliseconds placed by `recording_started_at`.
    `visits` maps a name to the number of entries (rejoins) its spans are dealt over."""
    zero = START.timestamp()

    def stamp(seconds):
        return int((zero + seconds + shift) * 1000)

    people = []
    for name, found in spans.items():
        count = (visits or {}).get(name, 1)
        for visit in range(count):
            events = []
            for a, b in found[visit::count]:
                events += [
                    {"type": "started-speaking", "timestamp": stamp(a)},
                    {"type": "stopped-speaking", "timestamp": stamp(b)},
                ]
            people.append({"name": name, "events": events})
    if bot_listed:
        people.append({"name": BOT, "events": []})
    return {"participants": people, "recording_started_at": START.isoformat()}


def resolve(items, events, *, timeline=True, elimination=True):
    return skribby._resolve_tags(items, (BOT,), elimination, events, timeline)


def names(tags):
    return {tag: info["name"] for tag, info in tags.items()}


@pytest.fixture(scope="module")
def case_39():
    items, spans = layout(TABLE)
    return items, events_record(spans)


# ---- the table of meeting 39 ----


def test_the_numbers_of_meeting_39_name_the_split_tags_and_leave_the_weak_ones(case_39):
    items, events = case_39
    before = resolve(items, None)
    assert names(before) == {
        "0": "",
        "1": CY,
        "2": "",
        "3": BO,
        "4": FLO,
        "5": "",
        "6": "",
        "7": "",
        "8": "",
    }
    assert before["0"]["note"] == "shared_claim" and before["6"]["note"] == "unresolved"
    after = resolve(items, events)
    assert names(after) == {
        "0": ADA,
        "1": CY,
        "2": ADA,
        "3": BO,
        "4": FLO,
        "5": ADA,
        "6": DI,
        "7": "",
        "8": "",
    }
    for tag in ("0", "2", "5", "6"):
        assert after[tag] == {"name": names(after)[tag], "how": "meet_timeline", "note": ""}
    # The tags the earlier rules named are untouched, with the reason they got their name.
    assert after["1"] == before["1"] and after["3"] == before["3"] and after["4"] == before["4"]
    assert [after[t]["how"] for t in ("1", "3", "4")] == [
        "sole_candidate",
        "sole_candidate",
        "given",
    ]
    # The two weak ones say why, and keep the reason the earlier rules gave.
    assert after["7"]["timeline"] == "too_little" and after["7"]["note"] == "unresolved"
    assert after["8"]["timeline"] == "too_little" and after["8"]["how"] == "unknown"


def test_the_overlap_numbers_are_the_table(case_39):
    items, events = case_39
    spans = meet_timeline.speaking_intervals(events)
    placed = {
        n: meet_timeline.merge([(a - START.timestamp(), b - START.timestamp()) for a, b in v])
        for n, v in spans.items()
    }
    heard = meet_timeline.heard_by_tag(items, lambda item: item["speaker"])
    for tag, (cells, total) in TABLE.items():
        mine = meet_timeline.merge(heard[tag])
        assert sum(b - a for a, b in mine) == pytest.approx(total, abs=0.01)
        for name, seconds in zip(PEOPLE, cells, strict=True):
            got = sum(meet_timeline.overlap(placed[name], a, b) for a, b in mine)
            assert got == pytest.approx(seconds, abs=0.01)


def test_the_segments_are_labelled_with_the_new_names_and_the_meeting_reads_complete(case_39):
    items, events = case_39
    body = {"transcript": items}
    old = skribby._segments(body, (BOT,), True, None, True)
    new = skribby._segments(body, (BOT,), True, events, True)
    off = skribby._segments(body, (BOT,), True, events, False)
    assert off == old
    assert {s["speaker"] for s in new} == {ADA, BO, CY, DI, FLO, ""}
    found_old = transcript_source.candidate(old)
    found_new = transcript_source.candidate(new)
    # Before: tags 0, 2, 5, 6, 7, 8 are 72% of the words. (The old rule, "someone is named",
    # would have called this complete: true by inspection, it was the whole rule.)
    assert found_old.unnamed_words / found_old.words > 0.7
    assert found_old.complete is False
    # After: tags 7 and 8 (69.2 of 2,771 s, 2.5%) are all that is left.
    assert found_new.unnamed_words / found_new.words == pytest.approx(69.2 / 2771.0, abs=0.01)
    assert found_new.complete is True
    assert found_new.speakers == 5


def test_the_map_bot_path_names_with_the_events_and_records_how(case_39):
    items, events = case_39
    body = {"id": "x", "service": "gmeet", "status": "finished", "transcript": items}
    now = datetime(2026, 11, 17, 12, 0, tzinfo=UTC)
    with patch.object(get_settings(), "name_by_meet_timeline", True):
        info = map_bot(body, now, own_names=(BOT,), speaker_events=events)
        bare = map_bot(body, now, own_names=(BOT,))
    assert {s["speaker"] for s in info.segments} >= {ADA, DI}
    assert info.evidence["tags"]["5"] == {"name": ADA, "how": "meet_timeline", "note": ""}
    assert {s["speaker"] for s in bare.segments} == {CY, BO, FLO, ""}  # no events: as before
    assert "timeline" not in bare.evidence["tags"]["5"]  # nothing about it in the evidence either
    with patch.object(get_settings(), "name_by_meet_timeline", False):
        off = map_bot(body, now, own_names=(BOT,), speaker_events=events)
    assert {s["speaker"] for s in off.segments} == {CY, BO, FLO, ""}


# ---- never touches what the earlier rules gave, and meetings 37 and 38 stay as they were ----


def test_a_meeting_37_shape_is_the_same_with_or_without_the_timeline():
    # Three people, all named by the earlier rules (two given, one sole candidate).
    table = {
        "0": ([300.0, 0.0, 5.0, 0.0, 0.0, 0.0], 400.0),
        "1": ([2.0, 0.0, 120.0, 0.0, 0.0, 0.0], 200.0),
        "2": ([3.0, 0.0, 4.0, 0.0, 0.0, 90.0], 150.0),
    }
    items, spans = layout(table, {"2": [FLO]}, {"0": ADA, "1": CY})
    plain = resolve(items, None)
    assert names(plain) == {"0": ADA, "1": CY, "2": FLO}
    assert resolve(items, events_record(spans)) == plain


def test_a_meeting_38_shape_with_elimination_is_the_same_with_the_timeline():
    # Four people: two given, one by elimination (candidates Ada, Cy; Ada is taken), one given.
    table = {
        "0": ([200.0, 0.0, 0.0, 0.0, 0.0, 0.0], 220.0),
        "1": ([0.0, 150.0, 0.0, 0.0, 0.0, 0.0], 160.0),
        "2": ([0.0, 0.0, 80.0, 0.0, 0.0, 0.0], 90.0),
        "3": ([0.0, 0.0, 0.0, 0.0, 0.0, 120.0], 130.0),
    }
    items, spans = layout(table, {"2": [ADA, CY]}, {"0": ADA, "1": BO, "3": FLO})
    plain = resolve(items, None)
    assert names(plain) == {"0": ADA, "1": BO, "2": CY, "3": FLO}
    assert plain["2"]["how"] == "elimination"
    assert resolve(items, events_record(spans)) == plain


def test_a_name_skribby_gave_is_never_overridden_even_when_the_timeline_says_otherwise():
    # Tag 3 carries Skribby's own name for Bo, the timeline says Ada spoke over it (a little: not
    # enough to call it a contradiction): the name stays.
    table = dict(TABLE)
    table["3"] = ([20.0, 25.0, 0.0, 0.0, 0.0, 0.0], 131.2)
    items, spans = layout(table, given={"4": FLO, "3": BO})
    after = resolve(items, events_record(spans))
    assert after["3"] == {"name": BO, "how": "given", "note": ""}


def test_a_clear_contradiction_with_a_name_the_earlier_rules_gave_names_nothing():
    # Tag 3 is Bo by the earlier rules but Meet says Ada spoke over it for 120 s and Bo for 5 s:
    # either the clock or the participant names are not what we think, so no tag is named.
    table = dict(TABLE)
    table["3"] = ([120.0, 5.0, 0.0, 0.0, 0.0, 0.0], 131.2)
    items, spans = layout(table)
    after = resolve(items, events_record(spans))
    assert after["3"]["name"] == BO  # the earlier name is not touched
    assert after["0"]["name"] == "" and after["0"]["timeline"] == "contradicted"
    assert all(after[t].get("how") != "meet_timeline" for t in after)


def test_no_name_is_given_when_the_timeline_agrees_with_no_named_tag():
    # Skribby named nobody, the sole-candidate rule had no tag to start from: nothing to check the
    # Meet names or the clock against. A tag whose own candidates do not hold Meet's leader stays
    # unnamed (`uncorroborated`).
    table = {"0": ([200.0, 0, 0, 0, 0, 0], 220.0), "1": ([0, 0, 0, 150.0, 0, 0], 160.0)}
    items, spans = layout(table, {"0": [BO, CY], "1": [BO, CY]}, {})
    after = resolve(items, events_record(spans))
    assert names(after) == {"0": "", "1": ""}
    assert after["0"]["timeline"] == "uncorroborated"
    assert after["1"]["timeline"] == "uncorroborated"


def test_without_a_named_tag_a_tag_is_named_when_it_lists_the_person_meet_shows_leading_it():
    # relaxed 2026-11-25: the tag's own candidate list holds Meet's leader, so Skribby and Meet
    # agree on that tag (no named tag is needed to prove the spelling and the clock)
    table = {"0": ([200.0, 0, 0, 0, 0, 0], 220.0), "1": ([0, 0, 0, 150.0, 0, 0], 160.0)}
    items, spans = layout(table, {"0": [ADA, BO], "1": [CY, DI]}, {})
    after = resolve(items, events_record(spans))
    assert names(after) == {"0": ADA, "1": DI}
    assert after["0"]["how"] == after["1"]["how"] == "meet_timeline"


def test_a_clear_contradiction_still_stops_a_meeting_without_a_named_tag():
    # tag 0 is named by Skribby as Bo but Meet leads it with Ada: `contradicted`, nothing named
    table = {"0": ([200.0, 3.0, 0, 0, 0, 0], 220.0), "1": ([0, 0, 0, 150.0, 0, 0], 160.0)}
    items, spans = layout(table, {"0": [BO], "1": [CY, DI]}, {"0": BO})
    after = resolve(items, events_record(spans))
    assert after["1"]["name"] == "" and after["1"]["timeline"] == "contradicted"


def test_without_a_named_tag_a_leader_the_tag_does_not_list_is_not_a_name():
    # Meet leads tag 0 with Ada, the tag lists Bo and Cy only: no support from Skribby at all.
    # Tag 1 lists Di, whom Meet leads it with, but Ada's 200 s are a voice no named tag holds (57%
    # of the lit time): the extra-share policy of the one-to-one rule keeps both blank
    table = {"0": ([200.0, 0, 0, 0, 0, 0], 220.0), "1": ([0, 0, 0, 150.0, 0, 0], 160.0)}
    items, spans = layout(table, {"0": [BO, CY], "1": [DI, EVE]}, {})
    after = resolve(items, events_record(spans))
    assert names(after) == {"0": "", "1": ""}
    assert after["1"]["timeline"] == "extra_speaker"


def test_a_tag_carrying_two_names_of_its_own_is_never_named_from_the_timeline():
    items, spans = layout(TABLE)
    for item in items:
        if item["speaker"] == "6":  # Skribby put two different names on this tag's segments
            item["speaker_name"] = BO if item["start"] % 2 < 1 else EVE
    after = resolve(items, events_record(spans))
    assert after["6"]["note"] == "several_names" and after["6"]["name"] == ""
    assert after["6"]["timeline"] == "not_eligible"


# ---- the rules about one tag ----


def one_tag_case(ada, bo, *, listed=(ADA, DI)):
    """Tag 9 is split off with `ada` and `bo` seconds of overlap; tags 1, 3, 4 give the meeting
    its earlier names so the timeline is corroborated. These cases are about the timeline rule
    alone, so the two newer routes are kept out of them (2026-11-23): tag 9 does not list Bo, whom
    tag 3 holds (the chain would name it Ada), and Eve speaks 100 s on no tag, so two people are
    left over and exclusion has nothing to say."""
    table = {k: TABLE[k] for k in ("1", "3", "4")}
    table["9"] = ([ada, bo, 0, 0, 0, 0], ada + bo + 50)
    items, spans = layout(table, {"9": list(listed), "1": [CY], "3": [BO]}, extra={EVE: 100.0})
    return resolve(items, events_record(spans))["9"]


def test_ties_and_small_margins_are_not_names():
    assert one_tag_case(100.0, 100.0)["timeline"] == "not_clear"  # a tie
    assert one_tag_case(100.0, 40.0)["timeline"] == "not_clear"  # 2.5 times: under the line
    assert one_tag_case(100.0, 34.0)["timeline"] == "not_clear"  # 2.94 times: still under
    named = one_tag_case(100.0, 30.0)  # 3.33 times: over it
    assert named["name"] == ADA and named["how"] == "meet_timeline"
    assert one_tag_case(100.0, 0.0)["name"] == ADA  # nobody else at all


def test_too_few_seconds_is_not_a_name_however_clear_the_margin():
    assert one_tag_case(29.0, 0.0)["timeline"] == "too_little"
    assert one_tag_case(30.0, 0.0)["name"] == ADA


def test_a_tag_nobody_was_speaking_over_stays_unknown():
    items, spans = layout({k: TABLE[k] for k in ("1", "3", "4")} | {"9": ([0] * 6, 50.0)})
    after = resolve(items, events_record(spans))
    assert after["9"]["timeline"] == "no_overlap"


def test_the_bot_is_never_a_name_and_never_skipped_for_the_person_below():
    table = {k: TABLE[k] for k in ("1", "3", "4")}
    table["9"] = ([20.0, 0, 0, 0, 0, 0], 90.0)
    items, spans = layout(table, {"9": [ADA, DI], "1": [CY], "3": [BO]}, extra={EVE: 100.0})
    spans[BOT] = [(items[-1]["start"], items[-1]["start"] + 60.0)]  # the bot "speaks" most
    after = resolve(items, events_record(spans))
    assert after["9"]["name"] == "" and after["9"]["timeline"] == "bot"
    # ...and with the bot not in the call's names, its name is just a name.
    again = skribby._resolve_tags(items, (), True, events_record(spans), True)
    assert again["9"]["timeline"] != "bot"


def test_a_winner_missing_from_the_tags_own_candidate_list_is_a_disagreement():
    got = one_tag_case(100.0, 0.0, listed=(BO, CY))
    assert got["name"] == "" and got["timeline"] == "disagrees"
    assert one_tag_case(100.0, 0.0, listed=())["name"] == ADA  # no list: nothing to disagree with


def test_a_shared_claim_for_someone_else_is_a_disagreement_not_an_override():
    # Tags 0, 2, 5 all claim Ada by the earlier rules; if the timeline says Di spoke over tag 0,
    # the two signals disagree about it: it stays unknown (the others, which agree, are named).
    table = dict(TABLE)
    table["0"] = ([0.0, 0.0, 0.0, 200.0, 0.0, 0.0], 441.8)
    items, spans = layout(table)
    after = resolve(items, events_record(spans))
    assert after["0"]["name"] == "" and after["0"]["timeline"] == "disagrees"
    assert after["2"]["name"] == ADA and after["5"]["name"] == ADA


def test_a_shared_claim_is_checked_against_the_name_the_earlier_rules_picked():
    # Tags 0 and 2 both claim Ada by the earlier rules (tag 0 listed Ada and Di, Di is held by tag
    # 10, so elimination left Ada; tag 2 lists Ada alone on three or more segments). Meet says Di
    # spoke over tag 0. Di IS on tag 0's list, so the list alone would let Di through: the
    # claimed name is what refuses it.
    table = {
        "0": ([0, 0, 0, 200.0, 0, 0], 220.0),
        "2": ([100.0, 1.0, 1.0, 0, 0, 0], 130.0),
        "4": ([0, 0, 0, 0, 0, 150.0], 160.0),
        "10": ([0, 0, 0, 120.0, 0, 0], 130.0),
    }
    listed = {"0": [ADA, DI], "2": [ADA]}
    items, spans = layout(table, listed, {"4": FLO, "10": DI})
    before = resolve(items, None)
    assert before["0"]["note"] == "shared_claim" and before["2"]["note"] == "shared_claim"
    after = resolve(items, events_record(spans))
    assert after["0"]["name"] == "" and after["0"]["timeline"] == "disagrees"
    assert after["2"]["name"] == ADA  # the claim and the timeline agree


def test_with_elimination_off_the_timeline_names_only_what_elimination_would_have_said():
    items, spans = layout(TABLE)
    events = events_record(spans)
    off = resolve(items, events, elimination=False)
    assert off["0"]["name"] == ADA  # elimination off left it unknown; the timeline agrees
    # A tag elimination would have named differently (tag 6 would be Di by elimination here):
    table = dict(TABLE)
    table["6"] = ([0, 0, 0, 10.0, 0, 140.0], 274.5)  # the timeline says Flo, who is on the list
    listed = LISTED | {"6": [FLO, DI]}  # Flo is held by tag 4, so elimination says Di
    items, spans = layout(table, listed)
    off = resolve(items, events_record(spans), elimination=False)
    assert off["6"]["name"] == "" and off["6"]["timeline"] == "disagrees"
    assert off["6"]["would_be"] == DI


# ---- a share of the tag, not only seconds ----


def test_crosstalk_alone_cannot_name_a_big_tag():
    # A 1,000 s tag whose real speaker has no usable Meet entry; one person talked over it for
    # 35 s and nobody else for more than 10: seconds and ratio pass, the share (3.5%) does not.
    table = {k: TABLE[k] for k in ("1", "3", "4")}
    table["9"] = ([35.0, 10.0, 0, 0, 0, 0], 1000.0)
    items, spans = layout(table, {"1": [CY], "3": [BO]})
    got = resolve(items, events_record(spans))["9"]
    assert got["name"] == "" and got["timeline"] == "small_share"


def test_the_share_floor_is_a_fifth_of_the_tags_seconds():
    def tag(ada, total):
        table = {k: TABLE[k] for k in ("1", "3", "4")} | {"9": ([ada, 0, 0, 0, 0, 0], total)}
        items, spans = layout(table, {"1": [CY], "3": [BO]})
        return resolve(items, events_record(spans))["9"]

    assert tag(39.0, 200.0)["timeline"] == "small_share"  # 19.5%
    assert tag(41.0, 200.0)["name"] == ADA  # 20.5%


# ---- a speaking interval that never closes ----


def start_ms():
    return int(START.timestamp() * 1000)


def test_a_started_speaking_with_no_stop_names_no_later_tag():
    # Gus's connection dropped at the start: one `started-speaking`, no stop, no `left`. Run to
    # the end of the record he would "speak" over every later tag, tag 9 included.
    table = {"9": ([0] * 6, 100.0), **{k: TABLE[k] for k in ("1", "3", "4")}}
    items, spans = layout(table, {"1": [CY], "3": [BO]})
    events = events_record(spans)
    events["participants"].append(
        {"name": "Gus Example", "events": [{"type": "started-speaking", "timestamp": start_ms()}]}
    )
    got = resolve(items, events)
    assert got["9"]["name"] == "" and got["9"]["timeline"] == "no_overlap"
    assert names(got)["1"] == CY  # nor does he disturb the tags that were named


def test_an_interval_that_never_closes_is_dropped_and_so_is_an_absurdly_long_one():
    def spans_of(*stamps):
        record = {
            "participants": [
                {"name": "Gus", "events": [{"type": k, "timestamp": t} for k, t in stamps]}
            ]
        }
        return meet_timeline.speaking_intervals(record)

    assert spans_of(("started-speaking", 10), ("muted", 5000)) == {"Gus": []}
    assert spans_of(("started-speaking", 10), ("stopped-speaking", 70)) == {"Gus": [(10, 70)]}
    limit = meet_timeline.MAX_SPAN_SECONDS
    assert spans_of(("started-speaking", 10), ("stopped-speaking", 10 + limit)) != {"Gus": []}
    assert spans_of(("started-speaking", 10), ("stopped-speaking", 11 + limit)) == {"Gus": []}


# ---- names are compared exactly ----


def test_a_shorter_spelling_of_a_name_is_not_the_same_person():
    # Meet shows "Bo" where Skribby named "Bo Example": we cannot tell it is the same person, so
    # the timeline does not corroborate and in fact contradicts: nothing is named.
    items, spans = layout(TABLE)
    spans["Bo"] = spans.pop(BO)
    after = resolve(items, events_record(spans))
    assert after["0"]["name"] == "" and after["0"]["timeline"] == "contradicted"
    # and a winner "Ada" is not a tag's only candidate "Ada Example":
    table = {k: TABLE[k] for k in ("1", "3", "4")} | {"9": ([100.0, 0, 0, 0, 0, 0], 150.0)}
    items, spans = layout(table, {"1": [CY], "3": [BO], "9": [ADA]})
    spans["Ada"] = spans.pop(ADA)
    got = resolve(items, events_record(spans))["9"]
    assert got["name"] == "" and got["timeline"] == "disagrees"


# ---- participants listed more than once, the clock, the events ----


def test_a_participant_who_rejoined_counts_as_one_person_not_three():
    items, spans = layout(TABLE)
    events = events_record(spans, visits={ADA: 3})
    assert sum(1 for p in events["participants"] if p["name"] == ADA) == 3
    assert names(resolve(items, events)) == names(resolve(items, events_record(spans)))
    # Three entries, each with only a third of the speech: as three people none would win, as one
    # person Ada wins every tag she should. The number the rule sees is the union, not a sum:
    read = meet_timeline.read(
        [{"tag": i["speaker"], "start": i["start"], "end": i["end"]} for i in items],
        events,
        lambda n: (skribby._same(n), n),
        tag_of=lambda i: i["tag"],
    )
    assert read.cells["5"][skribby._same(ADA)] == pytest.approx(449.0, abs=0.01)


def test_one_person_under_two_spellings_is_one_person():
    items, spans = layout(TABLE)
    events = events_record(spans)
    ada = next(p for p in events["participants"] if p["name"] == ADA)
    half = len(ada["events"]) // 4 * 2  # whole start/stop pairs
    ada["events"], rest = ada["events"][:half], ada["events"][half:]
    ada["name"] = "ADA  example"  # another spelling, as a rejoin may show it
    events["participants"].append({"name": ADA, "events": rest})
    got = names(resolve(items, events))
    assert got["5"] == "ADA example" and got["0"] == "ADA example"  # one person, first spelling


def test_a_clock_that_fits_better_a_few_seconds_away_names_nothing():
    items, spans = layout(TABLE)
    late = events_record(spans, shift=8.0)  # the Meet clock runs 8 s late
    after = resolve(items, late)
    assert after["0"]["name"] == "" and after["0"]["timeline"] == "clock_shifted"
    assert after["6"]["timeline"] == "clock_shifted"
    assert names(after)["1"] == CY  # what the earlier rules gave is not touched
    # A clock one second off is within what the check tolerates (no shift is clearly better).
    near = resolve(items, events_record(spans, shift=1.0))
    assert near["5"]["name"] == ADA


@pytest.mark.parametrize("record", [None, {}])
def test_no_events_at_all_change_nothing_not_even_the_evidence(case_39, record):
    items, _ = case_39
    assert resolve(items, record) == resolve(items, None)


@pytest.mark.parametrize("record", [{"participants": "x"}, {"participants": [{"name": 5}]}])
def test_unreadable_events_name_nothing_and_say_so(case_39, record):
    items, _ = case_39
    after = resolve(items, record)
    assert names(after) == names(resolve(items, None))
    assert after["0"]["timeline"] == "no_events"


def test_unix_times_with_no_recording_time_cannot_be_placed():
    items, spans = layout(TABLE)
    events = events_record(spans)
    events["recording_started_at"] = None
    after = resolve(items, events)
    assert after["0"]["timeline"] == "clock_unplaced" and after["0"]["name"] == ""


def test_segments_without_times_name_nothing():
    items, spans = layout(TABLE)
    for item in items:
        item["start"] = item["end"] = None
    assert resolve(items, events_record(spans))["0"]["timeline"] == "no_times"


def test_times_already_in_seconds_from_the_start_are_read_as_such():
    items, spans = layout(TABLE)
    small = {
        "participants": [
            {
                "name": name,
                "events": [
                    e
                    for a, b in found
                    for e in (
                        {"type": "started-speaking", "timestamp": a},
                        {"type": "stopped-speaking", "timestamp": b},
                    )
                ],
            }
            for name, found in spans.items()
        ]
    }
    assert names(resolve(items, small))["5"] == ADA


# ---- the switch, the default, the report ----


def test_the_switch_is_on_by_default_is_read_from_the_environment_and_a_bad_value_stops(
    monkeypatch,
):
    assert Settings().name_by_meet_timeline is True
    monkeypatch.setenv("NAME_BY_MEET_TIMELINE", "false")
    assert Settings().name_by_meet_timeline is False
    monkeypatch.setenv("NAME_BY_MEET_TIMELINE", "maybe")
    with pytest.raises(ValidationError, match="name_by_meet_timeline"):
        Settings()


def test_with_the_switch_off_the_tags_are_exactly_what_they_were(case_39):
    items, events = case_39
    assert resolve(items, events, timeline=False) == resolve(items, None)


def test_the_report_says_what_the_rule_would_do_without_printing_it_as_done():
    import importlib.util
    from pathlib import Path

    path = Path(__file__).parent.parent / "scripts" / "skribby_segments_report.py"
    spec = importlib.util.spec_from_file_location("skribby_segments_report_x", path)
    script = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(script)
    items, spans = layout(TABLE)
    saved = resolve(items, None)  # the tags as saved before the rule existed
    flat = [
        {
            "speaker": i["speaker"],
            "speaker_name": i["speaker_name"],
            "potential_speaker_names": [e["name"] for e in i["potential_speaker_names"]],
            "start": i["start"],
            "end": i["end"],
            "text": i["transcript"],
        }
        for i in items
    ]
    text = "\n".join(script.naming_section(flat, saved, events_record(spans)))
    assert f"tag 0: would be named {ADA} [meet_timeline]" in text
    assert f"tag 6: would be named {DI} [meet_timeline]" in text
    assert "tag 7: stays unknown (too_little)" in text and "tag 8: stays unknown" in text
    assert "tag 1:" not in text  # already named
    assert "No speaker events" in "\n".join(script.naming_section(flat, saved, None))
    # With the bot's names given (as the live path and the rebuild give them) the report agrees
    # with them: a tag whose top person is the bot is not named.
    as_bot = "\n".join(script.naming_section(flat, saved, events_record(spans), (ADA,), True))
    assert "tag 0: stays unknown (bot)" in as_bot


# ---- the extra request the live path makes ----


def provider_with(answers):
    """A provider whose client answers `get_bot` with each of `answers` in turn."""
    client = SkribbyClient(api_key="skr-live-0123456789abcdef", base_url="https://skribby.test/v1")
    calls = []

    def get_bot(bot_id, *, timeout=None, params=None):
        calls.append(params)
        answer = answers[min(len(calls) - 1, len(answers) - 1)]
        if isinstance(answer, Exception):
            raise answer
        return answer

    client.get_bot = get_bot
    return SkribbyProvider(client, None, clock=lambda: START, sleep=lambda s: None), calls


def test_the_live_lookup_asks_for_the_events_once_the_bot_is_finished(case_39):
    items, events = case_39
    bot_id = "3c09b2aa-6708-42c1-8145-aa55ee223613"
    finished = {"id": bot_id, "service": "gmeet", "status": "finished", "transcript": items}
    provider, calls = provider_with(
        [
            finished,
            finished
            | {
                "participants": events["participants"],
                "events": [
                    {
                        "data": {"new_status": "recording"},
                        "created_at": events["recording_started_at"],
                    }
                ],
            },
        ]
    )
    info = provider._look(bot_id, own_names=(BOT,))
    assert calls == [None, {"with-speaker-events": "true"}]
    assert names_of(info)["5"] == ADA


def names_of(info):
    return {tag: t["name"] for tag, t in info.evidence["tags"].items()}


def test_a_bot_still_live_makes_no_second_request(case_39):
    items, _ = case_39
    provider, calls = provider_with([{"id": "x", "service": "gmeet", "status": "recording"}])
    provider._look("3c09b2aa-6708-42c1-8145-aa55ee223613")
    assert calls == [None]


def test_with_the_switch_off_no_second_request_is_made(case_39):
    items, _ = case_39
    finished = {"id": "x", "service": "gmeet", "status": "finished", "transcript": items}
    provider, calls = provider_with([finished])
    with patch.object(get_settings(), "name_by_meet_timeline", False):
        provider._look("3c09b2aa-6708-42c1-8145-aa55ee223613")
    assert calls == [None]


def test_a_failed_events_request_leaves_the_tags_named_as_before(case_39):
    items, _ = case_39
    finished = {"id": "x", "service": "gmeet", "status": "finished", "transcript": items}
    provider, calls = provider_with(
        [finished, skribby.SkribbyError("get_meeting failed: HTTP 500")]
    )
    info = provider._look("3c09b2aa-6708-42c1-8145-aa55ee223613", own_names=(BOT,))
    assert len(calls) == 2
    assert names_of(info)["5"] == "" and names_of(info)["1"] == CY


def test_a_finished_bot_looked_at_again_does_not_ask_for_the_events_again(case_39):
    items, events = case_39
    bot_id = "3c09b2aa-6708-42c1-8145-aa55ee223613"
    finished = {"id": bot_id, "service": "gmeet", "status": "finished", "transcript": items}
    with_events = finished | {"participants": events["participants"]}
    provider, calls = provider_with([finished, with_events, finished, finished])
    provider._look(bot_id, own_names=(BOT,))
    provider._look(bot_id, own_names=(BOT,))
    provider._look(bot_id, own_names=(BOT,))
    # three plain looks, one request for the events
    assert calls == [None, {"with-speaker-events": "true"}, None, None]


def test_a_failed_events_request_is_not_kept_so_the_next_look_asks_again(case_39):
    items, _ = case_39
    finished = {"id": "x", "service": "gmeet", "status": "finished", "transcript": items}
    provider, calls = provider_with([finished, skribby.SkribbyError("HTTP 500"), finished])
    provider._look("3c09b2aa-6708-42c1-8145-aa55ee223613")
    provider._look("3c09b2aa-6708-42c1-8145-aa55ee223613")
    assert calls.count({"with-speaker-events": "true"}) == 2


# ---- the chained elimination and the exclusion by Meet's participant list (2026-11-23) ----
# The first meeting recorded under the new rules (three real speakers, here Alpha, Bravo and
# Charlie; the real names are not in this repository) left one tag "Unknown speaker" although both
# rules had what they needed. The numbers below are that meeting's: Meet's speaking seconds per
# person (255, 185 over two entries, 126), the overlap of each tag with each person, tag 2's total
# (193.3 s). The totals of tags 0 and 1 and the candidate lists' order are not in the notes taken;
# they are made up (250 s, 330 s) and no rule here reads them.
# Docs: docs/sop/2026-11-23-speaker-name-chain.md.

AL, BR, CH, DE = (f"{n} Sample" for n in ("Alpha", "Bravo", "Charlie", "Delta"))
FAIL_TABLE = {
    "0": ([124.3, 9.3, 7.2], 250.0),
    "1": ([86.8, 111.6, 19.6], 330.0),
    "2": ([6.8, 43.6, 84.6], 193.3),
}
# Speaking that sits under no segment, so each person's whole time is 255 / 185 / 126 s.
FAIL_EXTRA = {AL: 255.0 - 217.9, BR: 185.0 - 164.5, CH: 126.0 - 111.4}
FAIL_GIVEN = {"0": AL}
FAIL_LISTED = {"1": [BR, AL], "2": [CH, BR]}


def failure(*, table=FAIL_TABLE, listed=FAIL_LISTED, given=FAIL_GIVEN, more=None, **record):
    """The failure's items and Meet's events: Bravo is listed twice (a rejoin). `more` adds people
    who only speak (name -> seconds, under no segment)."""
    more = more or {}
    people = [AL, BR, CH, *more]
    padded = {t: ([*cells, *[0.0] * len(more)], total) for t, (cells, total) in table.items()}
    items, spans = layout(padded, listed, given, people, {**FAIL_EXTRA, **more})
    return items, events_record(spans, visits={BR: 2}, **record)


def settle(items, events=None, *, own=(BOT,), elimination=True):
    return skribby._resolve_tags(items, own, elimination, events, True)


def test_the_failure_names_the_tag_the_chain_reaches_not_unknown():
    items, events = failure()
    tags = settle(items, events)
    assert names(tags) == {"0": AL, "1": BR, "2": CH}
    assert [tags[t]["how"] for t in ("0", "1", "2")] == ["given", "elimination", "elimination"]
    assert tags["2"] == {"name": CH, "how": "elimination", "note": ""}


def test_without_meet_a_chain_name_from_round_two_is_not_given():
    # Teams, Zoom, a failed fetch, the switch off: tag 1 (round 1) is named as before the chain,
    # tag 2 (round 2, rests on an inferred name) stays Unknown exactly as it did.
    items, events = failure()
    for tags in (
        settle(items, None),
        skribby._resolve_tags(items, (BOT,), True, events, False),
        skribby._resolve_tags(items, (BOT,), True, None, True),
    ):
        assert names(tags) == {"0": AL, "1": BR, "2": ""}
        assert tags["2"] == {"name": "", "how": "unknown", "note": "unresolved"}


def test_a_clock_that_may_be_off_also_withholds_a_round_two_name():
    items, events = failure(shift=20.0)
    tags = settle(items, events)
    assert tags["2"]["name"] == "" and tags["2"]["timeline"] == "clock_shifted"
    assert tags["1"]["name"] == BR  # round 1, as before the chain


def test_with_elimination_off_the_chain_names_nothing_and_says_what_it_would_have():
    items, events = failure()
    tags = settle(items, events, elimination=False)
    assert names(tags) == {"0": AL, "1": "", "2": ""}
    assert tags["1"]["note"] == "elimination_off" and tags["1"]["would_be"] == BR
    assert tags["2"]["note"] == "elimination_off" and tags["2"]["would_be"] == CH


def test_the_labels_of_the_failure_carry_the_chained_name_and_none_is_blank():
    items, events = failure()
    body = {"transcript": [{**i, "transcript": i["transcript"]} for i in items]}
    segments, correction = skribby._labelled(body, (BOT,), True, events, True, True)
    assert correction is not None and correction.ran
    assert all(s["speaker"] for s in segments)  # nobody is left unnamed
    assert {s["speaker"] for s in segments} == {AL, BR, CH}
    # Tag 2's lines that nobody else was lit over carry the chained name.
    quiet = [
        s["speaker"]
        for i, s in zip(items, segments, strict=True)
        if i["speaker"] == "2" and i["start"] > 0 and s["speaker"] == CH
    ]
    assert quiet


def chain(spec, *, own=(BOT,), segments=3):
    """Items without times: `spec` maps a tag to (name it carries, candidates listed)."""
    items = []
    for tag, (given, listed) in spec.items():
        for n in range(segments):
            items.append(
                {
                    "speaker": tag,
                    "speaker_name": given,
                    "potential_speaker_names": [{"name": x} for x in listed],
                    "transcript": f"w{tag}{n}",
                }
            )
    return skribby._resolve_tags(items, own, True)


def timed_chain(spec, truth, *, own=(BOT,), events=True, shift=0.0):
    """A meeting with times and Meet's events. `spec` maps a tag to (name it carries, candidates
    it lists); `truth` maps a tag to the person really speaking on it (lit 100 s over its 150 s,
    each segment cut in three so a sole candidate has enough segments)."""
    people = sorted({p for p in truth.values() if p})  # None: nobody is lit over that tag
    table = {t: ([100.0 if p == truth[t] else 0.0 for p in people], 150.0) for t in spec}
    items, spans = layout(
        table, {t: v[1] for t, v in spec.items()}, {t: v[0] for t, v in spec.items()}, people
    )
    cut = []
    for item in items:
        for n in range(3):
            third = (item["end"] - item["start"]) / 3
            cut.append(
                {**item, "start": item["start"] + n * third, "end": item["start"] + (n + 1) * third}
            )
    record = events_record(spans, shift=shift)
    return skribby._resolve_tags(cut, own, True, record if events else None, True)


def test_a_chain_of_three_tags_is_named_in_either_order_when_meet_agrees():
    truth = {"0": "A", "1": "B", "2": "C", "3": "D"}
    forward = timed_chain(
        {"0": ("A", []), "1": (None, ["B", "A"]), "2": (None, ["C", "B"]), "3": (None, ["D", "C"])},
        truth,
    )
    assert names(forward) == {"0": "A", "1": "B", "2": "C", "3": "D"}
    assert [forward[t]["how"] for t in "123"] == ["elimination"] * 3
    backward = timed_chain(
        {"3": (None, ["B", "A"]), "2": (None, ["C", "B"]), "1": (None, ["D", "C"]), "0": ("A", [])},
        {"0": "A", "3": "B", "2": "C", "1": "D"},
    )
    assert names(backward) == {"0": "A", "3": "B", "2": "C", "1": "D"}


def test_a_long_chain_ends_and_names_every_link():
    spec = {"0": ("N0", [])}
    spec.update({str(i): (None, [f"N{i}", f"N{i - 1}"]) for i in range(1, 40)})
    tags = timed_chain(spec, {str(i): f"N{i}" for i in range(40)})
    assert names(tags) == {str(i): f"N{i}" for i in range(40)}


def test_a_sole_candidate_name_is_held_for_the_next_round():
    # Tag 1 lists only B on every segment (sole candidate); tag 2 lists C and B: B is taken.
    tags = timed_chain(
        {"0": ("A", []), "1": (None, ["B"]), "2": (None, ["C", "B"])},
        {"0": "A", "1": "B", "2": "C"},
    )
    assert tags["1"]["how"] == "sole_candidate" and tags["1"]["name"] == "B"
    assert tags["2"] == {"name": "C", "how": "elimination", "note": ""}


def test_an_over_split_person_is_named_by_meet_not_by_the_chain():
    # Bo is split over tag 3 (lists only Bo: sole candidate) and tag 9 (lists Ada and Bo). The
    # chain would hold Bo on tag 3 and name tag 9 Ada; Meet clearly has Bo on tag 9, so the chain's
    # name is refused and the timeline rule names it Bo.
    got = one_tag_case(0.0, 100.0, listed=(ADA, BO))
    assert got == {"name": BO, "how": "meet_timeline", "note": ""}


def test_a_round_two_name_needs_meets_positive_lead_of_one_and_a_half_times():
    # Tag 9 lists Ada and Bo; Bo is held by tag 3, so the chain proposes Ada: Meet must lead.
    clear = one_tag_case(100.0, 0.0, listed=(ADA, BO))
    assert clear == {"name": ADA, "how": "elimination", "note": ""}
    assert one_tag_case(80.0, 50.0, listed=(ADA, BO))["name"] == ADA  # 1.6x
    close = one_tag_case(70.0, 50.0, listed=(ADA, BO))  # 1.4x: no lead, nothing is named
    assert close["name"] == "" and close["how"] == "unknown"
    assert one_tag_case(100.0, 80.0, listed=(ADA, BO))["name"] == ""  # 1.25x


def test_a_lean_towards_the_held_name_below_the_naming_bar_still_refuses_the_chain_name():
    # Bo 50 s against Ada 20 s (2.5x: under the 3.0x that would name Bo from Meet alone, but Ada
    # has no lead and under 30 s): tag 9 must not be Ada.
    got = one_tag_case(20.0, 50.0, listed=(ADA, BO))
    assert got["name"] == "" and got["how"] == "unknown"


def test_a_wrong_link_does_not_make_the_next_one_wrong_too():
    # Tag 1 is really C but lists B and A, so round 1 (as before the chain) says B. Tag 2 is really
    # B and lists C and B: the chain would hold B on tag 1 and name tag 2 C. Meet has B on tag 2:
    # the second error is not made (it stays Unknown, as before the chain).
    tags = timed_chain(
        {"0": ("A", []), "1": (None, ["B", "A"]), "2": (None, ["C", "B"])},
        {"0": "A", "1": "C", "2": "B"},
    )
    assert tags["1"]["name"] == "B"  # round 1: unchanged, no new risk
    assert tags["2"]["name"] == ""


def test_a_round_two_clash_is_not_recorded_so_the_timeline_can_still_name_the_tags():
    # B is claimed by tags 1 and 2 in round 1 (recorded, as before the chain). Tag 4 takes C; tag 3
    # (B or C) is then left with B in round 2: that claim rests on inferred names and is not
    # recorded; tag 3 stays open (it is not named B or anything else here).
    tags = timed_chain(
        {
            "0": ("A", []),
            "1": (None, ["B", "A"]),
            "2": (None, ["B", "A"]),
            "4": (None, ["C", "A"]),
            "3": (None, ["B", "C"]),
        },
        {"0": "A", "1": None, "2": None, "4": "C", "3": None},  # Meet says nothing about 1, 2, 3
    )
    assert names(tags) == {"0": "A", "1": "", "2": "", "3": "", "4": "C"}
    assert tags["1"]["note"] == tags["2"]["note"] == "shared_claim"  # round 1 clash: as before
    assert tags["3"]["note"] == "unresolved" and tags["4"]["how"] == "elimination"


def test_an_over_split_person_on_two_tags_is_named_by_meet_not_blocked_by_a_round_two_clash():
    # Bo is split over tags 9 and 10, both list Ada and Bo. Round 2 would make both claim Ada (Bo
    # is held by tag 3); that clash must not stop the timeline, which clearly has Bo on both.
    table = {k: TABLE[k] for k in ("1", "3", "4")}
    table["9"] = ([0, 100.0, 0, 0, 0, 0], 150.0)
    table["10"] = ([0, 100.0, 0, 0, 0, 0], 150.0)
    listed = {"9": [ADA, BO], "10": [ADA, BO], "1": [CY], "3": [BO]}
    items, spans = layout(table, listed, extra={EVE: 100.0})
    tags = resolve(items, events_record(spans))
    assert tags["9"] == {"name": BO, "how": "meet_timeline", "note": ""}
    assert tags["10"] == {"name": BO, "how": "meet_timeline", "note": ""}


def test_two_tags_that_would_both_take_a_name_get_neither_in_a_chain_too():
    tags = chain({"0": ("A", []), "1": (None, ["B", "A"]), "2": (None, ["B", "A"])})
    assert names(tags) == {"0": "A", "1": "", "2": ""}
    assert tags["1"]["note"] == tags["2"]["note"] == "shared_claim"


def test_a_shared_claim_is_not_held_so_it_names_nothing_further_down():
    # Tags 1 and 2 both want B: neither gets it, and B is not "taken", so tag 3 (C or B) stays open.
    tags = chain(
        {"0": ("A", []), "1": (None, ["B", "A"]), "2": (None, ["B", "A"]), "3": (None, ["C", "B"])}
    )
    assert tags["3"] == {"name": "", "how": "unknown", "note": "unresolved"}


def test_the_bot_is_not_a_chain_name_and_a_tag_with_no_list_is_untouched():
    tags = chain(
        {"0": ("A", []), "1": (None, ["B", "A"]), "2": (None, [BOT, "B"]), "3": (None, [])}
    )
    assert tags["2"]["name"] == "" and tags["2"]["note"] == "unresolved"  # only the bot is left
    assert tags["3"] == {"name": "", "how": "unknown", "note": "no_candidates"}
    assert tags["1"]["name"] == "B"


def test_a_tag_carrying_two_names_of_its_own_still_holds_them_as_before_the_chain():
    # Tag 0 carries A and B (several_names): both stay held by it (unchanged) and it is not a link.
    items = [
        {"speaker": "0", "speaker_name": "A", "transcript": "x"},
        {"speaker": "0", "speaker_name": "B", "transcript": "y"},
        {
            "speaker": "1",
            "speaker_name": None,
            "transcript": "z",
            "potential_speaker_names": [{"name": "C"}, {"name": "A"}],
        },
    ]
    tags = skribby._resolve_tags(items, (BOT,), True)
    # A is held by tag 0 (it carries A among its names), so tag 1 is C: the old pass did this.
    assert tags["0"]["note"] == "several_names" and tags["1"]["name"] == "C"


# -- the second route: exclusion against Meet's participant list --


def exclusion_case(**changes):
    """The failure with tag 1 named by Skribby itself (so only tag 2 is open) and no candidates on
    tag 2: Bravo and Charlie are the people not yet on a tag... except that Bravo has a tag."""
    base = {"given": {"0": AL, "1": BR}, "listed": {}}
    return failure(**{**base, **changes})


def test_the_failure_without_candidate_lists_names_the_last_tag_by_exclusion():
    items, events = exclusion_case()
    tags = settle(items, events)
    assert names(tags) == {"0": AL, "1": BR, "2": CH}
    assert tags["2"] == {"name": CH, "how": "meet_exclusion", "note": ""}
    # Without the events there is nothing to exclude against.
    assert names(settle(items, None))["2"] == ""


def test_exclusion_works_when_the_tag_lists_the_person_among_others():
    items, events = exclusion_case(listed={"2": [CH, DE]})
    assert settle(items, events)["2"]["how"] == "meet_exclusion"


def test_the_literal_failure_without_lists_leaves_two_open_tags_and_names_neither_by_exclusion():
    # Tags 1 and 2 both open: Bravo and Charlie are both unaccounted. Which is which is a guess.
    items, events = failure(listed={}, given=FAIL_GIVEN)
    tags = settle(items, events)
    assert names(tags) == {"0": AL, "1": "", "2": ""}
    assert "exclusion" not in tags["1"] and "exclusion" not in tags["2"]


def test_two_unaccounted_participants_leave_the_tag_unknown():
    items, events = exclusion_case(more={DE: 100.0})
    tags = settle(items, events)
    assert tags["2"]["name"] == "" and tags["2"]["exclusion"] == "participants"


def test_a_participant_who_barely_spoke_still_counts_as_someone_left_over():
    # Delta was lit 10 s: a person Meet hardly sees is as possible a speaker as Charlie.
    items, events = exclusion_case(more={DE: 10.0})
    tags = settle(items, events)
    assert tags["2"]["name"] == "" and tags["2"]["exclusion"] == "participants"


def test_a_dial_in_with_no_events_counts_as_someone_left_over():
    items, events = exclusion_case()
    events["participants"].append({"name": "Dial In Sample", "events": []})
    tags = settle(items, events)
    assert tags["2"]["name"] == "" and tags["2"]["exclusion"] == "participants"


def test_a_participant_off_the_tags_own_candidate_list_is_ignored_for_the_count():
    # Skribby says tag 2 is Charlie or Delta (Delta is not in the call): the dial-in and the
    # 10 s participant are not on that list, so they do not make the answer ambiguous.
    items, events = exclusion_case(listed={"2": [CH, DE]}, more={"Echo Sample": 10.0})
    events["participants"].append({"name": "Dial In Sample", "events": []})
    assert settle(items, events)["2"] == {"name": CH, "how": "meet_exclusion", "note": ""}
    # ...but two people on the list are ambiguous.
    items, events = exclusion_case(listed={"2": [CH, "Echo Sample"]}, more={"Echo Sample": 10.0})
    assert settle(items, events)["2"]["exclusion"] == "participants"


def test_the_bot_is_never_the_name_exclusion_gives():
    items, events = exclusion_case()
    tags = settle(items, events, own=(BOT, CH))  # Charlie is the note taker's own name
    assert tags["2"]["name"] == ""


def test_a_candidate_list_that_leaves_the_person_out_blocks_exclusion():
    # Tag 2 lists Delta and Echo only (two names, unresolved), and neither is in the call: Charlie,
    # who is not on the list, is not a name Skribby allows.
    items, events = exclusion_case(listed={"2": [DE, "Echo Sample"]})
    tags = settle(items, events)
    assert tags["2"]["name"] == "" and tags["2"]["exclusion"] == "participants"


def test_a_clear_other_person_on_the_tag_blocks_exclusion():
    # Alpha was speaking over tag 2 five times as long as Charlie, and tag 2 lists Charlie or Delta
    # so the timeline rule cannot give it to Alpha: nor may exclusion give it to Charlie.
    table = {**FAIL_TABLE, "2": ([150.0, 0.0, 40.0], 193.3)}
    items, events = exclusion_case(table=table, listed={"2": [CH, DE]})
    tags = settle(items, events)
    assert tags["2"]["name"] == "" and tags["2"]["exclusion"] == "beaten"


def test_a_participant_barely_lit_over_the_tag_is_not_enough():
    # Charlie is on top of tag 2 but with 20 s: under the 30 s any naming needs.
    table = {**FAIL_TABLE, "2": ([0.0, 0.0, 20.0], 193.3)}
    items, events = exclusion_case(table=table)
    tags = settle(items, events)
    assert tags["2"]["name"] == "" and tags["2"]["exclusion"] == "too_little"


def test_a_participant_who_is_not_on_top_of_the_tag_is_not_named_even_without_a_clear_winner():
    # Bravo has a tag; Charlie does not. Meet shows Bravo over tag 2 more than Charlie (60 s to
    # 40 s: no clear margin, so the timeline rule names nothing), and Charlie is not on top:
    # that is an over-split of Bravo as likely as it is Charlie.
    table = {**FAIL_TABLE, "2": ([6.8, 60.0, 40.0], 193.3)}
    items, events = exclusion_case(table=table)
    tags = settle(items, events)
    assert tags["2"]["name"] == "" and tags["2"]["exclusion"] == "beaten"


def test_a_talker_over_is_not_the_name_of_a_big_tag_just_for_being_left_over():
    # 40 s of Charlie over a 400 s tag (10%): the tag's real speaker may have no Meet entry.
    table = {**FAIL_TABLE, "2": ([6.8, 30.0, 40.0], 400.0)}
    items, events = exclusion_case(table=table)
    tags = settle(items, events)
    assert tags["2"]["name"] == "" and tags["2"]["exclusion"] == "small_share"


def test_a_tag_too_short_to_be_a_person_is_not_named_by_exclusion():
    table = {**FAIL_TABLE, "2": ([3.0, 25.0, 35.0], 60.0)}  # 1.4x: the timeline names nothing
    items, events = exclusion_case(table=table)
    tags = settle(items, events)
    assert tags["2"]["name"] == "" and tags["2"]["exclusion"] == "tag_too_short"


def test_a_clock_that_may_be_off_blocks_exclusion():
    items, events = exclusion_case(shift=20.0)
    tags = settle(items, events)
    assert tags["2"]["name"] == "" and tags["2"]["timeline"] == "clock_shifted"


def test_exclusion_needs_a_named_tag_the_timeline_agrees_with():
    # Tag 0's own overlaps give the timeline no say about Alpha: nobody is corroborated.
    table = {**FAIL_TABLE, "0": ([5.0, 4.0, 3.0], 250.0)}
    items, events = exclusion_case(table=table)
    tags = settle(items, events)
    assert tags["2"]["name"] == "" and tags["2"]["timeline"] == "uncorroborated"


def test_exclusion_gives_a_tag_to_nobody_when_meet_shows_a_person_who_already_has_a_tag():
    # Charlie has tag 1 by Skribby. Tag 2 is mostly Charlie by Meet (84.6 s to Bravo's 43.6 s: not
    # a clear margin). The only unaccounted person is Bravo, who is second on the tag: not named.
    items, events = exclusion_case(given={"0": AL, "1": CH})
    tags = settle(items, events)
    assert tags["2"]["name"] == "" and tags["2"]["exclusion"] == "beaten"


def test_exclusion_names_only_the_one_tag_that_has_no_name_of_the_several_that_may_exist():
    # A tag carrying two names of its own is not open to either route.
    items, events = exclusion_case()
    for item in items:
        if item["speaker"] == "2":
            item["speaker_name"] = AL if item["start"] % 2 else BR
    tags = settle(items, events)
    assert tags["2"]["note"] == "several_names" and tags["2"]["name"] == ""


def test_the_left_over_person_needs_one_and_a_half_times_a_named_persons_seconds():
    # Bravo has tag 1. Charlie is lit 56 s to Bravo's 40 s over tag 2 (1.4x): as likely an
    # over-split of Bravo as Charlie's: Unknown. At 64 s (1.6x) he is named. Meeting 43's own
    # tag was 84.6 s to 43.6 s (1.94x).
    close = {**FAIL_TABLE, "2": ([6.8, 40.0, 56.0], 193.3)}
    items, events = exclusion_case(table=close)
    tags = settle(items, events)
    assert tags["2"]["name"] == "" and tags["2"]["exclusion"] == "too_close"
    clear = {**FAIL_TABLE, "2": ([6.8, 40.0, 64.0], 193.3)}
    items, events = exclusion_case(table=clear)
    assert settle(items, events)["2"] == {"name": CH, "how": "meet_exclusion", "note": ""}


def test_the_exclusion_verdict_numbers():
    verdict = meet_timeline.exclusion_verdict
    assert verdict({"c": 84.6, "b": 43.6}, "c", 193.3) == "named"
    assert verdict({"c": 60.0, "b": 40.0}, "c", 193.3) == "named"  # exactly 1.5x
    assert verdict({"c": 59.9, "b": 40.0}, "c", 193.3) == "too_close"
    assert verdict({"c": 40.0, "b": 40.0}, "c", 193.3) == "beaten"
    assert verdict({"c": 100.0}, "c", 89.9) == "tag_too_short"
    assert verdict({"c": 29.9}, "c", 193.3) == "too_little"
    assert verdict({"c": 38.0}, "c", 200.0) == "small_share"


def test_known_limit_two_people_with_one_display_name_are_one_person():
    # Documents a limit, not a wish (docs/sop/2026-11-23-speaker-name-chain.md, "Known limits"):
    # two real people both called "Sam" are one key, because the grouping that makes a rejoin one
    # person cannot tell them apart without presence intervals. Tag 0 is the first Sam, tag 1 the
    # second Sam, who lists Sam and Kim: the name is "taken", so elimination (round 1, exactly
    # as before the chain) gives tag 1 to Kim.
    tags = chain({"0": ("Sam", []), "1": (None, ["Sam", "Kim"])})
    assert tags["1"] == {
        "name": "Kim",
        "how": "elimination",
        "note": "",
    }  # wrong for the second Sam


def test_the_chain_leads_by_one_and_a_half_times_on_the_failures_own_tag():
    for c, named in ((60.0, "tag 2 is Charlie"), (59.9, "")):
        table = {**FAIL_TABLE, "2": ([6.8, 40.0, c], 193.3)}
        items, events = failure(table=table)
        tags = settle(items, events)
        assert (tags["2"]["name"] == CH) == bool(named), c
        if named:
            assert tags["2"]["how"] == "elimination"
        else:
            assert tags["2"]["how"] == "unknown"  # Bravo 40 s: too close, Unknown, as before


def test_a_record_with_participants_but_no_speaking_events_is_no_evidence_at_all():
    items, events = failure()
    for person in events["participants"]:
        person["events"] = []  # what the size cap keeps when it drops every event
    tags = settle(items, events)
    assert names(tags) == {"0": AL, "1": BR, "2": ""}  # round 1 only, exactly as with no events
    assert tags["2"]["timeline"] == "no_events"
    names_only = {"participants": [{"name": n} for n in (AL, BR, CH, BOT)]}
    assert names(settle(items, names_only)) == {"0": AL, "1": BR, "2": ""}


def test_events_with_no_overlap_over_the_tag_give_no_round_two_name():
    table = {**FAIL_TABLE, "2": ([0.0, 0.0, 0.0], 193.3)}
    items, events = failure(table=table)
    assert names(settle(items, events)) == {"0": AL, "1": BR, "2": ""}


def test_a_timeline_that_contradicts_a_given_name_gives_no_round_two_name():
    # Meet puts Bravo on top of tag 0, whose own name is Alpha: the signal or clock is wrong.
    table = {**FAIL_TABLE, "0": ([9.3, 124.3, 7.2], 250.0)}
    items, events = failure(table=table)
    tags = settle(items, events)
    assert tags["2"]["name"] == "" and tags["1"]["name"] == BR  # round 1 as before


def test_a_timeline_that_confirms_no_named_tag_gives_no_round_two_name():
    table = {**FAIL_TABLE, "0": ([5.0, 4.0, 3.0], 250.0)}
    items, events = failure(table=table)
    assert settle(items, events)["2"]["name"] == ""


def test_a_bot_whose_meet_name_is_not_known_fails_closed():
    # The note taker shows up in Meet's list under a name nobody told us (a Google account's own
    # name, with no SKRIBBY_ACCOUNT_NAME set): it counts as someone left over, so exclusion names
    # nothing. Fewer names, never a wrong one. With the name known (`own`) it is excluded.
    items, events = exclusion_case()
    assert settle(items, events, own=("Some Other Name",))["2"]["exclusion"] == "participants"
    assert settle(items, events, own=(BOT,))["2"]["how"] == "meet_exclusion"
    assert settle(items, events, own=("Stored Custom Name", BOT))["2"]["how"] == "meet_exclusion"


def test_the_report_prints_the_route_of_a_tag_the_new_rules_name():
    import importlib.util
    from pathlib import Path

    path = Path(__file__).parent.parent / "scripts" / "skribby_segments_report.py"
    spec = importlib.util.spec_from_file_location("skribby_segments_report_y", path)
    script = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(script)

    def flat(items):
        return [
            {
                "speaker": i["speaker"],
                "speaker_name": i["speaker_name"],
                "potential_speaker_names": [e["name"] for e in i["potential_speaker_names"]],
                "start": i["start"],
                "end": i["end"],
                "text": i["transcript"],
            }
            for i in items
        ]

    # The failure as it was saved by the old code: tag 2 unknown. The report says what the rules
    # would name now, by which route, and changes nothing.
    items, events = failure()
    saved = {
        "0": {"name": AL, "how": "given", "note": ""},
        "1": {"name": BR, "how": "elimination", "note": ""},
        "2": {"name": "", "how": "unknown", "note": "unresolved", "timeline": "not_clear"},
    }
    text = "\n".join(script.naming_section(flat(items), saved, events, (BOT,), True))
    assert f"tag 2: would be named {CH} [elimination]" in text
    # And the exclusion route.
    items, events = exclusion_case()
    saved = {
        "0": {"name": AL, "how": "given", "note": ""},
        "1": {"name": BR, "how": "given", "note": ""},
        "2": {"name": "", "how": "unknown", "note": "no_candidates", "timeline": "not_clear"},
    }
    text = "\n".join(script.naming_section(flat(items), saved, events, (BOT,), True))
    assert f"tag 2: would be named {CH} [meet_exclusion]" in text
    assert saved["2"]["name"] == ""  # read-only: the saved record is untouched
