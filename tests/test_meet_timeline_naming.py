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


def layout(table, listed=LISTED, given=GIVEN):
    """Skribby's transcript items and each person's speaking spans (seconds) for `table`: one block
    of time per (tag, person) cell where that person speaks and the tag's segment plays, plus one
    silent block per tag for the rest of its total. Blocks never overlap each other."""
    items, spans, at, word = [], {name: [] for name in PEOPLE}, 0.0, 0
    for tag, (cells, total) in table.items():
        blocks = [(name, seconds) for name, seconds in zip(PEOPLE, cells, strict=True) if seconds]
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
    # Before: tags 0, 2, 5, 6, 7, 8 are 72% of the words and the meeting reads complete (the bug).
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
    # Meet names or the clock against, so the timeline names nothing.
    table = {"0": ([200.0, 0, 0, 0, 0, 0], 220.0), "1": ([0, 0, 0, 150.0, 0, 0], 160.0)}
    items, spans = layout(table, {"0": [ADA, BO], "1": [CY, DI]}, {})
    after = resolve(items, events_record(spans))
    assert names(after) == {"0": "", "1": ""}
    assert after["0"]["timeline"] == "uncorroborated"


def test_a_tag_carrying_two_names_of_its_own_is_never_named_from_the_timeline():
    items, spans = layout(TABLE)
    for item in items:
        if item["speaker"] == "6":  # Skribby put two different names on this tag's segments
            item["speaker_name"] = BO if item["start"] % 2 < 1 else EVE
    after = resolve(items, events_record(spans))
    assert after["6"]["note"] == "several_names" and after["6"]["name"] == ""
    assert after["6"]["timeline"] == "not_eligible"


# ---- the rules about one tag ----


def one_tag_case(ada, bo, *, listed=(ADA, BO)):
    """Tag 9 is split off with `ada` and `bo` seconds of overlap; tags 1, 3, 4 give the meeting
    its earlier names so the timeline is corroborated."""
    table = {k: TABLE[k] for k in ("1", "3", "4")}
    table["9"] = ([ada, bo, 0, 0, 0, 0], ada + bo + 50)
    items, spans = layout(table, {"9": list(listed), "1": [CY], "3": [BO]})
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
    items, spans = layout(table, {"9": [ADA, BO], "1": [CY], "3": [BO]})
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
