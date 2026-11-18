"""Line-level speaker correction (`app/speaker_correction.py`, wired into `app/skribby.py`).

Three kinds of test:

* the rule's edge cases on a small hand-made timeline (one person lit, a tie, a runner-up that is
  only lag, nobody lit, the bot, an untimed segment, the same person under a longer spelling, an
  unnamed tag, a participant who rejoined, a shifted clock, no events);
* meetings built from the NUMBERS of the three reports (`tests/speaker_correction_fixtures.py`):
  meeting 40's 170 / 32 / 19 / 181 by outcome, meeting 39's 128 moves, meeting 38's two;
* what the switch and the failure paths guarantee: off is byte-identical to the old output, no
  events is an exact no-op, a failing correction keeps the uncorrected transcript, and neither the
  logs nor the evidence summary hold a word anybody said.

Every name and word is invented.
"""

import importlib.util
import logging
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import patch

import pytest

from app import pipeline, skribby, speaker_correction, transcript_source
from app.config import Settings
from app.skribby import map_bot
from tests.speaker_correction_fixtures import (
    ADA,
    BO,
    BOT,
    CY,
    meeting_38,
    meeting_39,
    meeting_40,
    old_segments,
)

REC_AT = datetime(2026, 11, 18, 9, 0, tzinfo=UTC)
REC_MS = int(REC_AT.timestamp() * 1000)
OWN = (BOT,)
NOW = datetime(2026, 11, 18, 12, 0, tzinfo=UTC)


def person(name, *spans):
    events = []
    for start, end in spans:
        events += [
            {"type": "started-speaking", "timestamp": REC_MS + int(start * 1000)},
            {"type": "stopped-speaking", "timestamp": REC_MS + int(end * 1000)},
        ]
    return {"name": name, "events": events}


def record(*people):
    return {
        "recording_started_at": REC_AT.isoformat(),
        "participants": [person(*p) if isinstance(p, tuple) else p for p in people],
    }


def seg(tag, start, end, name=None):
    return {"speaker": tag, "speaker_name": name, "start": start, "end": end, "transcript": "x y"}


TAGS = {
    "1": {"name": ADA, "how": "given", "note": ""},
    "2": {"name": BO, "how": "given", "note": ""},
    "3": {"name": "", "how": "unknown", "note": "unresolved"},
}
# Ada speaks 0-60 and 200-230; Bo speaks 60-130 and 230-260 (as the report's own tests).
EVENTS = record((ADA, (0, 60), (200, 230)), (BO, (60, 130), (230, 260)))


def moves(items, tags=TAGS, events=EVENTS, own=OWN):
    return speaker_correction.correct(items, tags, events, own).moves


# ---- the rule, one condition at a time ----


def test_exactly_one_person_lit_over_a_segment_of_another_moves_it():
    assert moves([seg("1", 70, 120)]) == {0: BO}


def test_a_segment_the_tag_already_has_right_stays():
    assert moves([seg("1", 5, 30), seg("2", 70, 120)]) == {}


def test_two_people_lit_is_ambiguous_and_stays():
    # 50-80: Ada 10 s, Bo 20 s: the runner-up has half the top's seconds
    got = speaker_correction.correct([seg("1", 50, 80)], TAGS, EVENTS, OWN)
    assert got.moves == {} and [a.outcome for a in got.assessments] == ["ambiguous"]


def test_two_people_lit_for_the_same_seconds_is_a_tie_and_stays():
    events = record((ADA, (0, 20)), (BO, (0, 20)))
    got = speaker_correction.correct([seg("2", 0, 20)], TAGS, events, OWN)
    assert got.moves == {} and [a.outcome for a in got.assessments] == ["ambiguous"]


def test_a_runner_up_that_is_only_lag_does_not_make_it_ambiguous():
    # 58-130: Ada 2 s, Bo 70 s: 3% of the top, the edge of a turn, not two speakers
    assert moves([seg("1", 58, 130)]) == {0: BO}


def test_a_runner_up_under_the_minimum_seconds_never_counts():
    # Bo 0.6 s at the start, Ada 20 s after: 0.6 s is under the 1 s minimum, even at 3% of the top
    events = record((BO, (0, 0.6)), (ADA, (0.6, 20.6)))
    assert moves([seg("2", 0, 20.6)], events=events) == {0: ADA}


def test_a_person_lit_for_under_the_minimum_seconds_is_no_signal():
    events = record((BO, (0, 0.8)))
    got = speaker_correction.correct([seg("1", 0, 20)], TAGS, events, OWN)
    assert got.moves == {} and [a.outcome for a in got.assessments] == ["no_signal"]


def test_nobody_lit_stays():
    # (beside one segment Meet agrees with, so the clock check has something to fit)
    events = record((ADA, (0, 60)), (BO, (60, 130)))
    got = speaker_correction.correct([seg("1", 5, 30), seg("1", 140, 190)], TAGS, events, OWN)
    assert got.moves == {} and [a.outcome for a in got.assessments] == ["agrees", "no_signal"]


def test_the_note_taker_is_never_a_destination_and_never_makes_a_segment_ambiguous():
    # the bot is "lit" over the whole of Ada's segment: it is left out before anything is ranked
    events = record((ADA, (0, 60)), (BOT, (0, 60)), (BO, (0, 0.5)))
    assert moves([seg("1", 5, 30)], events=events) == {}  # Ada alone: agrees
    # and where only the bot is lit there is no signal, so nothing moves to it
    events = record((BOT, (0, 60)), (BO, (100, 120)))
    got = speaker_correction.correct([seg("1", 5, 30)], TAGS, events, OWN)
    assert got.moves == {} and got.assessments[0].outcome == "no_signal"


def test_a_participant_who_is_blank_or_unknown_speaker_is_not_a_person():
    events = record(("Unknown speaker", (0, 60)), (BO, (100, 110)))
    got = speaker_correction.correct([seg("1", 5, 30)], TAGS, events, OWN)
    assert got.moves == {} and got.assessments[0].outcome == "no_signal"


def test_a_segment_with_no_tag_or_no_times_stays_and_is_counted_as_not_timed():
    items = [seg(None, 70, 120), seg("1", None, 120), seg("1", 120, 120), seg("1", 70, 60)]
    got = speaker_correction.correct(items + [seg("2", 70, 120)], TAGS, EVENTS, OWN)
    assert got.moves == {} and got.not_timed["count"] == 4
    assert [a.outcome for a in got.assessments] == ["agrees"]


def test_a_longer_spelling_of_the_same_name_is_the_same_person():
    tags = {"1": {"name": "Ada", "how": "given", "note": ""}}
    assert moves([seg("1", 5, 30)], tags=tags) == {}


def test_the_moved_line_takes_the_spelling_a_tag_already_uses_for_that_person():
    # Meet shows "Bo Example"; tag 2 is named "Bo": the line joins that person's turns as "Bo"
    tags = TAGS | {"2": {"name": "Bo", "how": "given", "note": ""}}
    assert moves([seg("1", 70, 120)], tags=tags) == {0: "Bo"}


def test_an_unnamed_tag_with_one_person_lit_is_credited_to_them():
    assert moves([seg("3", 5, 30)]) == {0: ADA}


def test_the_name_a_segment_carries_itself_is_what_is_compared():
    # the segment says Skribby itself named it Bo; Meet shows Ada alone: moves; Meet shows Bo: stays
    own = seg("3", 5, 30, name=BO)
    assert moves([own]) == {0: ADA}
    assert moves([seg("3", 70, 120, name=BO)]) == {}


def test_a_participant_who_rejoined_is_one_person():
    # Bo is listed twice (two visits): 20 s on one visit and 20 s on the other are 40 s for one
    # person, not two people of 20 s each splitting the segment into an ambiguous one
    events = record((BO, (0, 20)), (BO, (20, 40)))
    assert moves([seg("1", 0, 40)], events=events) == {0: BO}


def test_a_long_segment_lit_for_only_part_of_it_still_moves():
    # a 60 s segment with pauses: Bo lit 9 s of it (15%), nobody else: exactly one person lit
    events = record((BO, (0, 3), (20, 26)))
    assert moves([seg("1", 0, 60)], events=events) == {0: BO}


# ---- when nothing may move at all ----


def test_no_events_is_an_exact_no_op():
    for kept in (None, {}):
        got = speaker_correction.correct([seg("1", 70, 120)], TAGS, kept, OWN)
        assert got.moves == {} and got.ran is False
    for kept in ({"participants": []}, {"participants": "no"}):  # kept but unreadable
        got = speaker_correction.correct([seg("1", 70, 120)], TAGS, kept, OWN)
        assert got.moves == {} and got.refused == "unreadable"
    assert speaker_correction.correct([seg("1", 70, 120)], TAGS, None, OWN).refused == "no_events"


def test_unix_times_with_no_recording_status_cannot_be_placed_and_move_nothing():
    unplaced = record((ADA, (0, 60)), (BO, (60, 130)))
    del unplaced["recording_started_at"]
    got = speaker_correction.correct([seg("1", 70, 120)], TAGS, unplaced, OWN)
    assert got.moves == {} and got.refused == "clock_unplaced" and got.ran is True


def test_a_clock_that_fits_better_a_few_seconds_away_moves_nothing():
    # Meet's times are 20 s early relative to the segments: shifting them fits clearly better, so
    # the clock is not trusted and every line stays (the naming rule's own gate)
    items = [seg("1", 100 + 100 * n, 140 + 100 * n) for n in range(5)]
    early = record((BO, *[(80 + 100 * n, 120 + 100 * n) for n in range(5)]))
    got = speaker_correction.correct(items, TAGS, early, OWN)
    assert got.moves == {} and got.refused == "clock_shifted"
    # the same events on the right clock do move them
    right = record((BO, *[(100 + 100 * n, 140 + 100 * n) for n in range(5)]))
    assert set(speaker_correction.correct(items, TAGS, right, OWN).moves) == set(range(5))


def test_events_with_no_timed_segment_move_nothing():
    got = speaker_correction.correct([seg("1", None, None)], TAGS, EVENTS, OWN)
    assert got.moves == {} and got.refused == "no_times"


# ---- the numbers of the three reports ----


def outcomes(case):
    got = speaker_correction.correct(case.items, tags_of(case), case.events, OWN)
    return got, {key: got.outcome(key)[0] for key in ("agrees", "move", "ambiguous", "no_signal")}


def tags_of(case, **flags):
    return skribby._resolve_tags(case.items, OWN, True, case.events, True)


def test_meeting_40s_numbers_170_agree_32_move_19_ambiguous_181_no_signal():
    case = meeting_40()
    got, counts = outcomes(case)
    assert counts == {"agrees": 170, "move": 32, "ambiguous": 19, "no_signal": 181}
    assert got.refused is None and got.not_timed["count"] == 0
    assert got.moved_segments == 32
    assert {i: label for i, label in got.moves.items()} == case.moved_to


def test_meeting_39s_128_moves_come_out_of_tags_that_had_no_name():
    case = meeting_39()
    tags = tags_of(case)
    # the timeline cannot name the three tags (even spread): they stay `Unknown speaker`
    assert [bool(tags[t]["name"]) for t in "012345"] == [True, True, True, False, False, False]
    got, counts = outcomes(case)
    assert counts["move"] == 128 and got.moved_segments == 128
    assert got.moves == case.moved_to
    unnamed = {a.index for a in got.assessments if a.tag in "345" and a.outcome == "move"}
    assert unnamed  # from nobody to somebody, as the design note says
    assert all(a.current == "" for a in got.assessments if a.index in unnamed)


def test_meeting_38_the_clean_control_changes_almost_nothing():
    case = meeting_38()
    got, counts = outcomes(case)
    assert counts["move"] == 2 and got.moved_segments == 2
    total = sum(counts.values())
    assert got.moved_segments / total < 0.01  # two of 347 segments
    # and the transcript the pipeline builds differs from today's in exactly those two lines
    old = old_segments(case.body, OWN, True, case.events, True)
    new = skribby._segments(case.body, OWN, True, case.events, True, True)
    assert [i for i, (a, b) in enumerate(zip(old, new, strict=True)) if a != b] == list(got.moves)
    assert len(old) == len(new)


def test_the_report_and_production_read_the_same_counts_on_every_meeting():
    mod = report_script()
    for case in (meeting_38(), meeting_39(), meeting_40()):
        tags = tags_of(case)
        placed = speaker_correction.place(case.events, OWN)
        result = mod.analyse(case.evidence_items(), tags, placed.spans)
        got = speaker_correction.correct(case.items, tags, case.events, OWN)
        for key in ("agrees", "move", "ambiguous", "no_signal"):
            ours = sum(1 for s in result["segments"] if s["outcome"] == key)
            assert ours == got.outcome(key)[0]
            seconds = sum(s["seconds"] for s in result["segments"] if s["outcome"] == key)
            assert seconds == pytest.approx(got.outcome(key)[1])
        assert result["not_timed"]["count"] == got.not_timed["count"]


def report_script():
    path = Path(__file__).parent.parent / "scripts" / "speaker_reattribution_report.py"
    spec = importlib.util.spec_from_file_location("speaker_reattribution_report", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class OneRow:
    """A session that holds one evidence row and nothing else (the report reads two things)."""

    def __init__(self, case, saved_tags):
        self.row = type(
            "Row",
            (),
            {
                "items": case.evidence_items(),
                "tags": saved_tags,
                "events": case.events,
                "created_at": datetime(2026, 11, 18, 10, 0),
            },
        )

    def scalar(self, _statement):
        return self.row

    def get(self, _model, _id):
        return type("Meeting", (), {"bot_name": BOT})


def moves_printed(text):
    import re

    return int(re.search(r"WOULD MOVE to another person\s+(\d+)", text).group(1))


def test_the_report_reads_current_names_by_default_and_saved_names_on_request():
    """Meeting 39's lesson: saved with no names (the tags were unnamed when it was processed), so
    every clear line of a named tag looked like a move; with the names read again now only the
    lines that truly belong to someone else are."""
    case = meeting_39()
    for item in case.items:  # tag 0: Skribby never named it, but lists one person on every segment
        if item["speaker"] == "0":
            item["speaker_name"] = None
            item["potential_speaker_names"] = [{"name": ADA}]
    saved = {t: {"name": "", "how": "unknown", "note": "unresolved"} for t in "012345"}
    mod = report_script()
    current = mod.report(OneRow(case, saved), 39)
    as_saved = mod.report(OneRow(case, saved), 39, current_names=False)
    assert "Tag names: read again now" in current and "as saved" in as_saved
    assert moves_printed(current) == 128
    assert moves_printed(as_saved) > 128


def test_the_production_constants_are_the_reports_defaults():
    mod = report_script()
    assert mod.MIN_SECONDS is speaker_correction.MIN_SECONDS == 1.0
    assert mod.AMBIGUOUS_RATIO is speaker_correction.AMBIGUOUS_RATIO == 0.25


# ---- the switch, the settings gate and "off is today" ----


def settings(source="skribby", **fields):
    extra = {"skribby_transcription_model": "elevenlabs/scribe-v2"} if source == "skribby" else {}
    return Settings(transcript_source=source, **extra, **fields)


@pytest.fixture
def skribby_mode(monkeypatch):
    """The settings `skribby._segments` reads when it is not told: Skribby's transcript, the
    correction on (its default). Returns a function that changes either one."""

    def setup(*, correction=True, source="skribby"):
        fixed = settings(source, speaker_line_correction=correction)
        monkeypatch.setattr(skribby, "get_settings", lambda: fixed)
        return fixed

    setup()
    return setup


@pytest.mark.parametrize("make", [meeting_38, meeting_39, meeting_40])
def test_switched_off_the_output_is_byte_identical_to_the_old_code(make, skribby_mode):
    case = make()
    old = old_segments(case.body, OWN, True, case.events, True)
    assert skribby._segments(case.body, OWN, True, case.events, True, False) == old
    skribby_mode(correction=False)
    assert skribby._segments(case.body, OWN, True, case.events, True) == old
    # and the whole stored text, which is what a meeting keeps
    assert (
        transcript_source.candidate(skribby._segments(case.body, OWN, True, case.events, True)).text
        == transcript_source.candidate(old).text
    )


@pytest.mark.parametrize("make", [meeting_38, meeting_39, meeting_40])
def test_in_our_own_transcription_mode_nothing_moves(make, skribby_mode):
    case = make()
    skribby_mode(source="own")
    assert skribby._segments(case.body, OWN, True, case.events, True) == old_segments(
        case.body, OWN, True, case.events, True
    )


@pytest.mark.parametrize("make", [meeting_38, meeting_39, meeting_40])
def test_on_it_moves_exactly_the_built_lines_and_nothing_else(make, skribby_mode):
    case = make()
    old = old_segments(case.body, OWN, True, case.events, True)
    new = skribby._segments(case.body, OWN, True, case.events, True)
    assert len(new) == len(old)
    changed = {i: seg["speaker"] for i, seg in enumerate(new) if seg != old[i]}
    assert changed == case.moved_to
    assert [s["text"] for s in new] == [s["text"] for s in old]  # words never touched


@pytest.mark.parametrize("make", [meeting_38, meeting_39, meeting_40])
@pytest.mark.parametrize("kept", [None, {}])
def test_without_meet_events_the_output_and_the_evidence_are_exactly_what_they_were(
    make, kept, skribby_mode
):
    case = make()
    old = old_segments(case.body, OWN, True, None, True)
    assert skribby._segments(case.body, OWN, True, kept, True) == old
    skribby_mode(correction=False)
    off = skribby._evidence(case.body, OWN, True, None, True)
    skribby_mode()
    on = skribby._labelled(case.body, OWN, True, kept, True)
    assert on[1] is None
    assert skribby._evidence(case.body, OWN, True, kept, True, on[1]) == off
    assert "correction" not in off and not any("corrected_to" in i for i in off["items"])


def finished(case, **extra):
    body = {"id": "x", "service": "gmeet", "status": "finished", "transcript": case.items}
    return body | extra


def test_map_bot_hands_the_pipeline_the_corrected_segments_and_the_evidence(skribby_mode):
    case = meeting_40()
    info = map_bot(finished(case), NOW, "x", OWN, case.events)
    labels = [s["speaker"] for s in info.segments]
    old = [s["speaker"] for s in old_segments(case.body, OWN, True, case.events, True)]
    assert {i for i, (a, b) in enumerate(zip(labels, old, strict=True)) if a != b} == set(
        case.moved_to
    )
    evidence = info.evidence
    marked = {
        i: row["corrected_to"] for i, row in enumerate(evidence["items"]) if "corrected_to" in row
    }
    assert marked == case.moved_to
    # the original tag is kept next to the marker, so a move can be audited and undone
    assert all(evidence["items"][i]["speaker"] == case.items[i]["speaker"] for i in marked)
    summary = evidence["correction"]
    assert summary["moved_segments"] == 32 and summary["segments"] == 402
    assert summary["refused"] is None
    assert summary["moved_seconds"] == pytest.approx(
        sum(case.items[i]["end"] - case.items[i]["start"] for i in case.moved_to), abs=0.1
    )


def test_the_evidence_and_its_summary_hold_no_word_and_no_name_beyond_the_labels(skribby_mode):
    case = meeting_40()
    summary = map_bot(finished(case), NOW, "x", OWN, case.events).evidence["correction"]
    assert set(summary) == {"moved_segments", "moved_seconds", "segments", "refused"}
    assert all(isinstance(v, int | float | type(None)) for v in summary.values())


def test_a_shifted_clock_is_recorded_as_a_refusal_and_moves_nothing(skribby_mode):
    case = meeting_40()
    late = {
        **case.events,
        "participants": [
            {
                **p,
                "events": [{**e, "timestamp": e["timestamp"] + 20_000} for e in p["events"]],
            }
            for p in case.events["participants"]
        ],
    }
    info = map_bot(finished(case), NOW, "x", OWN, late)
    old = old_segments(case.body, OWN, True, late, True)
    assert info.segments == old
    assert info.evidence["correction"]["refused"] == "clock_shifted"
    assert info.evidence["correction"]["moved_segments"] == 0
    assert not any("corrected_to" in row for row in info.evidence["items"])


# ---- a failure never fails the meeting ----


def test_a_failing_correction_keeps_the_uncorrected_transcript_and_logs_no_text(
    skribby_mode, caplog
):
    case = meeting_40()
    old = old_segments(case.body, OWN, True, case.events, True)
    with patch.object(speaker_correction, "correct", side_effect=ValueError("w1 w2 secret words")):
        with caplog.at_level(logging.INFO):
            info = map_bot(finished(case), NOW, "x", OWN, case.events)
    assert info.status == "completed" and info.segments == old
    assert "correction" not in info.evidence  # nothing was claimed
    logged = " ".join(r.getMessage() for r in caplog.records)
    assert "ValueError" in logged and "secret" not in logged and "w1" not in logged
    for name in (ADA, BO, CY):
        assert name not in logged


def test_garbage_in_the_events_never_stops_the_meeting(skribby_mode):
    case = meeting_40()
    weird = {
        "recording_started_at": REC_AT.isoformat(),
        "participants": [{"name": ADA, "events": [{"type": "started-speaking", "timestamp": "x"}]}],
    }
    info = map_bot(finished(case), NOW, "x", OWN, weird)
    assert info.status == "completed"
    assert info.segments == old_segments(case.body, OWN, True, weird, True)


def test_the_pipeline_log_line_is_numbers_only(skribby_mode, caplog):
    case = meeting_40()
    info = map_bot(finished(case), NOW, "x", OWN, case.events)
    with caplog.at_level(logging.INFO, logger=pipeline.logger.name):
        pipeline._log_correction(7, info.evidence)
    line = " ".join(r.getMessage() for r in caplog.records)
    assert "meeting 7: speaker correction moved 32 of 402 segments" in line
    assert not any(word in line for word in ("w0", ADA, BO, CY))


def test_nothing_is_logged_for_a_meeting_the_correction_did_not_run_for(skribby_mode, caplog):
    case = meeting_40()
    info = map_bot(finished(case), NOW, "x", OWN, None)
    with caplog.at_level(logging.INFO, logger=pipeline.logger.name):
        pipeline._log_correction(7, info.evidence)
        pipeline._log_correction(7, None)
    assert caplog.records == []


# ---- the events are fetched when only the correction needs them ----


@pytest.mark.parametrize(
    ("naming", "correction", "source", "asked"),
    [
        (False, False, "skribby", False),  # neither feature wants the events
        (False, True, "skribby", True),  # naming off must not silently switch the correction off
        (False, True, "own", False),  # the correction acts only on Skribby's transcript
        (True, False, "own", True),  # naming alone, as before
    ],
)
def test_the_events_are_asked_for_when_either_feature_needs_them(
    monkeypatch, naming, correction, source, asked
):
    from tests.test_meet_timeline_naming import provider_with

    case = meeting_40()
    done = {"id": "x", "service": "gmeet", "status": "finished", "transcript": case.items}
    fixed = settings(source, speaker_line_correction=correction, name_by_meet_timeline=naming)
    monkeypatch.setattr(skribby, "get_settings", lambda: fixed)
    provider, calls = provider_with([done])
    provider._look("3c09b2aa-6708-42c1-8145-aa55ee223613")
    assert (len(calls) == 2) is asked
