"""`scripts/speaker_reattribution_report.py`: what would move if a line followed Meet's timeline.

All fixtures are numbers on a made-up timeline with placeholder words (meeting 40's real names and
words are not in this repository). No database: the report's parts are passed in, and the
database call is checked with a stand-in session. The script is read-only by construction; the last
tests check that it prints no text beyond the first 15 moves and does not touch the session.
"""

import importlib.util
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

REC_AT = datetime(2026, 11, 18, 9, 0, tzinfo=UTC)
REC_MS = int(REC_AT.timestamp() * 1000)
ADA, BO, BOT = "Ada Example", "Bo Example", "Beyric Note Taker (recording)"


def _script():
    path = Path(__file__).parent.parent / "scripts" / "speaker_reattribution_report.py"
    spec = importlib.util.spec_from_file_location("speaker_reattribution_report", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def person(name, *spans):
    events = []
    for start, end in spans:
        events += [
            {"type": "started-speaking", "timestamp": REC_MS + int(start * 1000)},
            {"type": "stopped-speaking", "timestamp": REC_MS + int(end * 1000)},
        ]
    return {"name": name, "presence_intervals": [{"joined_at": REC_MS}], "events": events}


def record(*people):
    return {
        "recording_started_at": REC_AT.isoformat(),
        "participants": [person(*p) if isinstance(p, tuple) else p for p in people],
    }


def seg(tag, start, end, text="placeholder words"):
    return {"speaker": tag, "start": start, "end": end, "text": text}


TAGS = {
    "1": {"name": ADA, "how": "given", "note": ""},
    "2": {"name": BO, "how": "given", "note": ""},
    "3": {"name": "", "how": "unknown", "note": "unresolved"},
}

# Ada speaks 0-60 and 200-230; Bo speaks 60-130 and 230-260. A little Ada over Bo at 100-102.
EVENTS = record((ADA, (0, 60), (100, 102), (200, 230)), (BO, (60, 130), (230, 260)))


def run(items, tags=TAGS, events=EVENTS, **knobs):
    return _script().build_report(40, items, tags, events, (BOT,), **knobs)


def analysis(items, tags=TAGS, events=EVENTS, **knobs):
    mod = _script()
    spans = mod.meet_timeline.speaking_intervals(events, mod._key_and_shown)
    spans = {
        n: mod.meet_timeline.merge([(a - REC_AT.timestamp(), b - REC_AT.timestamp()) for a, b in v])
        for n, v in spans.items()
    }
    return mod.analyse(items, tags, spans, **knobs)


def outcomes(items, **kw):
    return [s["outcome"] for s in analysis(items, **kw)["segments"]]


# ---- the per-segment decision ----


def test_a_segment_where_meet_agrees_with_the_tag_stays():
    assert outcomes([seg("1", 5, 30), seg("2", 70, 120)]) == ["agrees", "agrees"]


def test_a_segment_lit_for_someone_else_would_move_to_them():
    items = [seg("1", 70, 120)]  # tag says Ada, Meet shows Bo
    got = analysis(items)["segments"][0]
    assert got["outcome"] == "move" and got["person"] == BO and got["lit"] == 50.0


def test_two_people_lit_over_a_segment_is_ambiguous_and_does_not_move():
    # 50-80: Ada 10 s (50-60), Bo 20 s (60-80): the runner-up has half the top's seconds
    assert outcomes([seg("1", 50, 80)]) == ["ambiguous"]


def test_a_small_overlap_by_a_second_person_does_not_make_it_ambiguous():
    # 55-72: Ada 5 s, Bo 12 s: runner-up is 42% of the top, so ambiguous (over 25%)
    assert outcomes([seg("1", 55, 72)]) == ["ambiguous"]
    # 58-130: Ada 2 s, Bo 70 s: 3% of the top, a lag at the edge, not two speakers
    got = analysis([seg("1", 58, 130)])["segments"][0]
    assert got["outcome"] == "move" and got["person"] == BO


def test_a_segment_nobody_was_lit_over_has_no_signal_and_does_not_move():
    assert outcomes([seg("1", 140, 190)]) == ["no_signal"]


def test_under_the_minimum_seconds_there_is_no_signal():
    # Bo lit for 0.5 s of 130-130.5 only
    assert outcomes([seg("1", 129.5, 140)]) == ["no_signal"]
    assert outcomes([seg("1", 129.5, 140)], min_seconds=0.4) == ["move"]


def test_an_unnamed_tag_with_a_clear_speaker_is_a_move_from_unknown_speaker():
    got = analysis([seg("3", 5, 30)])["segments"][0]
    assert got["outcome"] == "move" and got["person"] == ADA and got["tag_name"] == ""


def test_a_longer_spelling_of_the_same_name_is_not_a_move():
    tags = {"1": {"name": "Ada", "how": "given", "note": ""}}
    assert outcomes([seg("1", 5, 30)], tags=tags) == ["agrees"]


def test_segments_with_no_tag_or_no_times_are_counted_and_never_read():
    items = [
        {"speaker": None, "start": 5, "end": 30, "text": "x"},
        {"speaker": "1", "start": None, "end": 30, "text": "x"},
        {"speaker": "1", "start": 30, "end": 30, "text": "x"},
        seg("1", 5, 30),
    ]
    got = analysis(items)
    assert [s["outcome"] for s in got["segments"]] == ["agrees"]
    assert got["not_timed"]["count"] == 3


def test_the_bot_is_never_a_candidate():
    ev = record((BOT, (0, 100)), (ADA, (0, 10)))
    text = _script().build_report(40, [seg("2", 0, 50)], TAGS, ev, (BOT,))
    assert BOT not in text.split("== Per person")[1]
    assert "Ada Example" in text


def test_a_rejoin_listed_twice_is_one_person():
    ev = record((ADA, (0, 20)), (ADA, (30, 50)), (BO, (60, 70)))
    got = analysis([seg("1", 0, 50)], events=ev)
    assert [s["outcome"] for s in got["segments"]] == ["agrees"]
    assert got["people"][ADA]["total"] == 40.0


# ---- per person ----


def test_per_person_seconds_split_own_other_unnamed_and_outside():
    items = [seg("1", 0, 100), seg("2", 100, 130), seg("3", 200, 230)]
    people = analysis(items)["people"]
    ada = people[ADA]
    # Ada lit 0-60 and 100-102 and 200-230: own tag 1 holds 0-60 (60 s); tag 2 holds 100-102 (2 s);
    # tag 3 (unnamed) holds 200-230 (30 s); nothing is outside the segments
    assert (ada["total"], ada["own"], ada["other"], ada["unnamed"], ada["outside"]) == (
        92.0,
        60.0,
        2.0,
        30.0,
        0.0,
    )
    assert ada["elsewhere"] == {BO: 2.0, "Unknown speaker": 30.0}
    bo = people[BO]
    # Bo lit 60-130 and 230-260: 60-100 sits under Ada's tag 1 (40 s), 100-130 under their own tag
    # 2 (30 s), 230-260 is in no segment (30 s)
    assert (bo["own"], bo["other"], bo["outside"]) == (30.0, 40.0, 30.0)
    assert bo["elsewhere"] == {ADA: 40.0}


def _own_other(tag_name, lit_name):
    """One tag named `tag_name` over 0-40 s, `lit_name` lit over all of it: the lit person's row."""
    tags = {"1": {"name": tag_name, "how": "given", "note": ""}}
    events = record((lit_name, (0, 40)))
    return analysis([seg("1", 0, 40)], tags=tags, events=events)["people"][lit_name]


def test_a_part_of_a_word_is_not_the_person_in_the_per_person_split():
    # the tag is named "Joanne"; "Ann" was lit: "Ann" is not "Joanne", so it is not Ann's own tag
    row = _own_other("Joanne", "Ann")
    assert (row["own"], row["other"]) == (0.0, 40.0)
    assert row["elsewhere"] == {"Joanne": 40.0}


def test_a_surname_alone_is_not_the_full_name_in_the_per_person_split():
    # the live correction never moves a line between "Obi" and "Ada Obi" (they may be two people),
    # so neither tag is the other's own tag here, in either direction
    for tag_name, lit in (("Obi", "Ada Obi"), ("Ada Obi", "Obi")):
        row = _own_other(tag_name, lit)
        assert (row["own"], row["other"]) == (0.0, 40.0), (tag_name, lit)


def test_the_same_words_are_the_person_whatever_the_case_order_or_punctuation():
    for tag_name, lit in (("ada obi", "Ada Obi"), ("Obi, Ada", "Ada Obi")):
        row = _own_other(tag_name, lit)
        assert (row["own"], row["other"]) == (40.0, 0.0), (tag_name, lit)


# ---- the printed report ----


def test_the_report_has_the_counts_the_tag_and_the_person_tables():
    items = [seg("1", 2, 58), seg("1", 70, 120), seg("1", 50, 80), seg("1", 140, 190)]
    text = run(items)
    assert "Meeting 40: 4 items from Skribby" in text
    assert "Clock check:" in text or "CLOCK WARNING" in text  # this toy timeline trips the gate
    table = text.split("== Segments by outcome ==")[1].split("== Per tag")[0]
    assert "agrees with the tag's name" in table and "WOULD MOVE" in table
    moved = [line for line in table.splitlines() if "WOULD MOVE" in line][0]
    assert moved.split()[-2:] == ["1", "50.0"]
    tag_row = [line for line in text.splitlines() if line.startswith("    1    Ada")][0]
    assert "Bo Example 50.0 s" in tag_row
    person_row = [line for line in text.splitlines() if line.startswith("    Bo Example")][0]
    assert "Ada Example 60.0 s" in person_row  # Bo lit 60-120 sits under tag 1 (50-120 merged)


def test_only_the_first_fifteen_moves_print_their_text_and_nothing_else_does():
    items = [seg("1", 230 + i * 1.5, 231.2 + i * 1.5, f"move number {i:02d}") for i in range(20)]
    items.append(seg("1", 5, 30, "text that agrees and must not print"))
    items.append(seg("1", 140, 190, "text with no signal and must not print"))
    text = run(items)
    for i in range(15):
        assert f"move number {i:02d}" in text
    for i in range(15, 20):
        assert f"move number {i:02d}" not in text
    assert "(5 more moves not shown)" in text
    assert "must not print" not in text


def test_a_long_move_text_is_cut():
    text = run([seg("1", 70, 120, "word " * 100)])
    assert "…" in text
    assert max(len(line) for line in text.splitlines()) < 260


def test_no_moves_says_none():
    assert "None: no segment would move." in run([seg("1", 5, 30)])


def test_a_shifted_clock_is_called_out_with_the_offset_to_use():
    # every segment sits 8 s after the stretch Meet lit for it: the fit wants Meet moved 8 s later
    ev = record((ADA, (0, 10), (40, 50)), (BO, (20, 30), (60, 70)))
    items = [seg("1", 8, 18), seg("2", 28, 38), seg("1", 48, 58), seg("2", 68, 78)]
    text = run(items, events=ev)
    assert "CLOCK WARNING" in text and "--offset" in text


def test_no_events_unreadable_events_and_unplaced_clock_each_say_so_in_a_line():
    items = [seg("1", 5, 30)]
    assert "No speaker events were kept" in run(items, events=None)
    assert "not in a shape this can read" in run(items, events={"participants": "x"})
    unplaced = {"participants": record((ADA, (0, 5)))["participants"]}
    assert "cannot be placed" in run(items, events=unplaced)


# ---- the database wrapper changes nothing ----


class Rows:
    """A stand-in session: returns the saved evidence, records any write."""

    def __init__(self, row):
        self.row = row
        self.calls = []

    def scalar(self, _stmt):
        self.calls.append("scalar")
        return self.row

    def get(self, _model, _id):
        self.calls.append("get")
        return SimpleNamespace(bot_name=None)

    def __getattr__(self, name):
        raise AssertionError(f"the report must not call session.{name}")


def test_report_says_plainly_when_no_evidence_is_kept():
    session = Rows(None)
    assert "no Skribby evidence is kept" in _script().report(session, 40)
    assert session.calls == ["scalar"]


def test_report_reads_the_saved_row_and_calls_nothing_but_the_two_reads():
    row = SimpleNamespace(
        items=[seg("1", 70, 120, "one line")],
        tags=TAGS,
        events=EVENTS,
        created_at=datetime(2026, 11, 18, 10, 0),
    )
    session = Rows(row)
    text = _script().report(session, 40, current_names=False)
    assert "saved 2026-11-18 10:00 UTC" in text and "one line" in text
    assert session.calls == ["scalar", "get"]
