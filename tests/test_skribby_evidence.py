"""What Skribby sent for a meeting is kept 7 days so a bad speaker labelling can be diagnosed.

Saved in the speakers step (just before the provider's copy is deleted), never in the way of the
notes, deleted by the retention job after 7 days, with the meeting and with the account, and read
only by `scripts/skribby_segments_report.py`.
"""

import importlib.util
import logging
from copy import deepcopy
from datetime import UTC, datetime, timedelta
from pathlib import Path

from sqlalchemy import func, select

from app import deletion, pipeline, services
from app.models import Meeting, SkribbySegmentEvidence
from app.vexa import VexaMeeting
from tests.test_pipeline import (  # noqa: F401  (fixtures used by name)
    DONE,
    NOW,
    FakeVexa,
    env,
    meeting,
    run,
)
from tests.test_web import owner  # noqa: F401

REC_AT = datetime(2026, 11, 10, 9, 0, tzinfo=UTC)
REC_MS = int(REC_AT.timestamp() * 1000)
SECRET = "the budget is two million"
EVIDENCE = {
    "items": [
        {
            "speaker": "1",
            "speaker_name": "Ada Example",
            "potential_speaker_names": [],
            "start": 1.5,
            "end": 4.0,
            "text": SECRET,
        },
        {
            "speaker": "2",
            "speaker_name": None,
            "potential_speaker_names": ["Ada Example", "Bo Example"],
            "start": 5.0,
            "end": 9.25,
            "text": "agreed, ship it friday",
        },
    ],
    "tags": {
        "1": {"name": "Ada Example", "how": "given", "note": ""},
        "2": {
            "name": "",
            "how": "unknown",
            "note": "elimination_off",
            "would_be": "Bo Example",
        },
    },
    "by_elimination": False,
}


def finished(evidence=EVIDENCE):
    return VexaMeeting(
        status="completed",
        segments=DONE.segments,
        recording_id=7,
        media_id=9,
        recording_status="completed",
        evidence=deepcopy(evidence),
    )


def rows(session):
    session.expire_all()
    return list(session.scalars(select(SkribbySegmentEvidence)))


def test_the_speakers_step_keeps_what_skribby_sent_and_the_notes_are_written(session, meeting):
    run(session, meeting, FakeVexa(finished()))
    assert (meeting.status, meeting.step) == ("ready", "done")
    [row] = rows(session)
    assert row.meeting_id == meeting.id
    assert row.items == EVIDENCE["items"]
    assert row.tags == EVIDENCE["tags"]
    assert row.by_elimination is False
    assert row.created_at is not None


def test_a_provider_with_no_evidence_leaves_no_row(session, meeting):
    run(session, meeting, FakeVexa(DONE))  # Vexa: evidence is None
    assert (meeting.status, meeting.step) == ("ready", "done")
    assert rows(session) == []


def test_keeping_it_twice_for_one_meeting_leaves_one_row(session, meeting):
    pipeline._keep_evidence(session, meeting.id, EVIDENCE)
    session.commit()
    again = deepcopy(EVIDENCE)
    again["by_elimination"] = True
    pipeline._keep_evidence(session, meeting.id, again)
    session.commit()
    [row] = rows(session)
    assert row.by_elimination is True


def test_failing_to_keep_it_never_stops_the_meeting_and_never_logs_the_words(
    session, meeting, caplog
):
    broken = deepcopy(EVIDENCE)
    broken["items"][0]["text"] = {SECRET}  # cannot be written as JSON
    with caplog.at_level(logging.WARNING):
        run(session, meeting, FakeVexa(finished(broken)))
    assert (meeting.status, meeting.step) == ("ready", "done")
    assert meeting.speaker_transcript and meeting.notes
    assert rows(session) == []
    assert "could not keep the speaker evidence" in caplog.text
    assert "StatementError" in caplog.text or "TypeError" in caplog.text
    assert SECRET not in caplog.text


def test_the_retention_job_deletes_evidence_after_seven_days_only(session, meeting):
    other = Meeting(
        owner_user_id=meeting.owner_user_id,
        url="https://meet.google.com/xyz-xyzx-xyz",
        platform="google_meet",
        native_id="xyz-xyzx-xyz",
        template_id=meeting.template_id,
        status="bot_sent",  # a meeting still going: its evidence is on its own clock
    )
    session.add(other)
    session.commit()
    old = SkribbySegmentEvidence(meeting_id=meeting.id, **_parts())
    old.created_at = NOW - timedelta(days=7, minutes=1)
    young = SkribbySegmentEvidence(meeting_id=other.id, **_parts())
    young.created_at = NOW - timedelta(days=6, hours=23)
    session.add_all([old, young])
    session.commit()
    pipeline.delete_expired_audio(session, NOW)
    assert [r.meeting_id for r in rows(session)] == [other.id]
    assert pipeline.EVIDENCE_RETENTION_DAYS == 7


def _parts():
    return {
        "items": EVIDENCE["items"],
        "tags": EVIDENCE["tags"],
        "by_elimination": False,
    }


def test_deleting_a_meeting_deletes_its_evidence_and_only_its_own(session, meeting):
    keep = Meeting(
        owner_user_id=meeting.owner_user_id,
        url="https://meet.google.com/xyz-xyzx-xyz",
        platform="google_meet",
        native_id="xyz-xyzx-xyz",
        template_id=meeting.template_id,
        status="ready",
    )
    session.add(keep)
    meeting.status, meeting.audio_deleted_at = "ready", NOW
    session.commit()
    session.add_all(
        [
            SkribbySegmentEvidence(meeting_id=meeting.id, **_parts()),
            SkribbySegmentEvidence(meeting_id=keep.id, **_parts()),
        ]
    )
    session.commit()
    services.delete_meeting(session, FakeVexa(DONE), session.get(Meeting, meeting.id))
    assert [r.meeting_id for r in rows(session)] == [keep.id]


def test_deleting_an_account_deletes_the_evidence_of_their_meetings(session, meeting, owner):
    session.add(SkribbySegmentEvidence(meeting_id=meeting.id, **_parts()))
    session.commit()
    deletion.purge_user(session, owner, vexa=None, force=True)
    assert rows(session) == []
    assert session.scalar(select(func.count()).select_from(Meeting)) == 0


# ---- the report script ----


def _script():
    path = Path(__file__).parent.parent / "scripts" / "skribby_segments_report.py"
    spec = importlib.util.spec_from_file_location("skribby_segments_report", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_the_report_groups_by_tag_with_the_derived_name_and_why(session, meeting):
    session.add(SkribbySegmentEvidence(meeting_id=meeting.id, **_parts()))
    session.commit()
    text = _script().report(session, meeting.id)
    assert f"Meeting {meeting.id}: 2 items" in text
    assert "name by elimination was OFF" in text
    assert "== tag 1: Ada Example [given] - 1 segments ==" in text
    assert (
        "== tag 2: Unknown speaker [unknown, elimination_off "
        "(elimination would have said: Bo Example)] - 1 segments ==" in text
    )
    assert "named: Ada Example | possible: -" in text
    assert "named: - | possible: Ada Example, Bo Example" in text
    assert SECRET in text and "agreed, ship it friday" in text
    assert text.index("tag 1") < text.index("tag 2")


def test_the_report_says_plainly_when_nothing_is_kept_and_changes_nothing(session, meeting):
    before = rows(session)
    text = _script().report(session, meeting.id)
    assert "no Skribby evidence is kept" in text
    assert rows(session) == before == []


# ---- who was speaking when (migration 0041, `events`) ----


def person(name, *spans):
    """A participant with speaking spans `(from, to)` in seconds from the recording start, as
    Skribby's `events` (13-digit millisecond timestamps)."""
    events = []
    for start, end in spans:
        events += [
            {"type": "started-speaking", "timestamp": REC_MS + int(start * 1000)},
            {"type": "stopped-speaking", "timestamp": REC_MS + int(end * 1000)},
        ]
    return {"name": name, "presence_intervals": [{"joined_at": REC_MS}], "events": events}


def record(*people, started=REC_AT):
    return {
        "participants": list(people),
        "status_times": [["recording", started.isoformat()]],
        "recording_started_at": started.isoformat(),
        "truncated": None,
        "bytes": 1,
    }


def timed(tag, name, start, end):
    return {
        "speaker": tag,
        "speaker_name": name,
        "potential_speaker_names": [],
        "start": start,
        "end": end,
        "text": f"words of tag {tag}",
    }


class EventsVexa(FakeVexa):
    """A hosted provider that can also tell who was speaking."""

    def __init__(self, *polls, events=None):
        super().__init__(*polls)
        self.events = events
        self.asked = []

    def get_speaker_events(self, vexa_meeting_id):
        self.asked.append(vexa_meeting_id)
        if isinstance(self.events, Exception):
            raise self.events
        return self.events


def test_the_speakers_step_keeps_the_events_next_to_the_transcript_evidence(session, meeting):
    meeting.vexa_meeting_id = "3c09b2aa-6708-42c1-8145-aa55ee223613"
    session.commit()
    sent = record(person("Ada Example", (10, 30)))
    vexa = EventsVexa(finished(), events=sent)
    run(session, meeting, vexa)
    assert (meeting.status, meeting.step) == ("ready", "done")
    [row] = rows(session)
    assert row.events == sent and row.items == EVIDENCE["items"]
    assert vexa.asked == ["3c09b2aa-6708-42c1-8145-aa55ee223613"]


def test_no_events_is_a_null_column_and_the_transcript_evidence_is_still_kept(session, meeting):
    meeting.vexa_meeting_id = "3c09b2aa-6708-42c1-8145-aa55ee223613"
    session.commit()
    run(session, meeting, EventsVexa(finished(), events=None))
    [row] = rows(session)
    assert row.events is None and row.items == EVIDENCE["items"]


def test_a_failing_events_call_never_stops_the_meeting_or_the_evidence(session, meeting, caplog):
    meeting.vexa_meeting_id = "3c09b2aa-6708-42c1-8145-aa55ee223613"
    session.commit()
    boom = RuntimeError("Ada Example said the budget is two million")
    with caplog.at_level(logging.WARNING):
        run(session, meeting, EventsVexa(finished(), events=boom))
    assert (meeting.status, meeting.step) == ("ready", "done")
    [row] = rows(session)
    assert row.events is None and row.items == EVIDENCE["items"]
    assert "no speaker events kept (RuntimeError)" in caplog.text
    assert "Ada Example" not in caplog.text and SECRET not in caplog.text


def test_events_that_cannot_be_written_cost_only_the_events(session, meeting, caplog):
    meeting.vexa_meeting_id = "3c09b2aa-6708-42c1-8145-aa55ee223613"
    session.commit()
    bad = record(person("Ada Example", (1, 2)))
    bad["participants"].append({"name": {"Ada Example"}})  # cannot be written as JSON
    with caplog.at_level(logging.WARNING):
        run(session, meeting, EventsVexa(finished(), events=bad))
    assert (meeting.status, meeting.step) == ("ready", "done")
    [row] = rows(session)
    assert row.events is None and row.items == EVIDENCE["items"]
    assert "with its events" in caplog.text and "Ada Example" not in caplog.text


def test_a_provider_without_the_call_and_a_meeting_without_evidence_ask_nothing(session, meeting):
    vexa = EventsVexa(DONE, events=record(person("Ada Example", (1, 2))))  # evidence is None
    meeting.vexa_meeting_id = "3c09b2aa-6708-42c1-8145-aa55ee223613"
    session.commit()
    run(session, meeting, vexa)
    assert vexa.asked == [] and rows(session) == []


def test_the_events_go_with_the_row_after_seven_days_and_with_the_meeting(session, meeting):
    old = SkribbySegmentEvidence(
        meeting_id=meeting.id, events=record(person("A", (1, 2))), **_parts()
    )
    old.created_at = NOW - timedelta(days=8)
    session.add(old)
    session.commit()
    pipeline.delete_expired_audio(session, NOW)
    assert rows(session) == []  # the events were in the row: nothing is left of them


# ---- the report: Meet's own speaker timeline ----

TWO_TAGS = [timed("1", "Ada Example", 10, 30), timed("2", "Ada Example", 40, 60)]
TAGS = {
    "1": {"name": "Ada Example", "how": "given", "note": ""},
    "2": {"name": "Ada Example", "how": "elimination", "note": ""},
}


def section(items, tags, events, offset=0.0):
    return "\n".join(_script().timeline_section(items, tags, events, offset))


def test_the_timeline_flags_the_tag_whose_name_is_not_who_spoke_most():
    events = record(person("Ada Example", (10, 30)), person("Bo Example", (40, 60)))
    text = section(TWO_TAGS, TAGS, events)
    assert "Ada Example: 20.0 s" in text and "Bo Example: 20.0 s" in text
    assert "1       20.0         0.0       20.0  Ada Example -> ok" in " ".join(
        text.split("\n")
    ) or ("Ada Example -> ok" in text)
    assert "Ada Example -> MISMATCH: Meet says Bo Example spoke most" in text
    assert text.count("MISMATCH") == 1
    assert "no shift of up to 30 s" in text


def test_an_unnamed_tag_and_a_tag_nobody_was_speaking_for_are_said_plainly():
    events = record(person("Ada Example", (10, 30)))
    items = [timed("1", None, 10, 30), timed("2", "Ada Example", 200, 210)]
    tags = {"1": {"name": "", "how": "unknown"}, "2": TAGS["2"]}
    text = section(items, tags, events)
    assert "Unknown speaker -> UNNAMED; Meet says Ada Example" in text
    assert "no participant was speaking during this tag's segments" in text


def test_a_meet_clock_that_is_out_of_step_is_noticed_and_an_offset_fixes_it():
    late = record(person("Ada Example", (15, 35)), person("Bo Example", (45, 65)))  # 5 s late
    text = section(TWO_TAGS, TAGS, late)
    assert "moving the Meet times -5 s would raise the total overlap" in text
    assert "--offset -5" in text
    fixed = section(TWO_TAGS, TAGS, late, offset=-5)
    assert "no shift of up to 30 s" in fixed and "Ada Example -> ok" in fixed


def test_a_participant_who_never_stopped_ends_at_the_last_time_seen():
    spans = _script().speaking_intervals(
        {
            "participants": [
                {
                    "name": "Ada",
                    "events": [
                        {"type": "started-speaking", "timestamp": REC_MS + 5000},
                        {"type": "muted", "timestamp": REC_MS + 9000},
                    ],
                }
            ]
        }
    )
    assert spans == {"Ada": [(REC_MS / 1000 + 5, REC_MS / 1000 + 9)]}


def test_absent_or_unreadable_events_are_one_plain_line():
    script = _script()
    assert "No speaker events were kept" in section(TWO_TAGS, TAGS, None)
    assert "not in a shape this report can read" in section(TWO_TAGS, TAGS, {"participants": "x"})
    assert "not in a shape this report can read" in section(
        TWO_TAGS, TAGS, {"participants": [{"name": 5}, "junk"]}
    )
    no_anchor = record(person("Ada Example", (10, 30)))
    no_anchor["recording_started_at"] = None
    assert "cannot be placed on the transcript" in section(TWO_TAGS, TAGS, no_anchor)
    small = {
        "participants": [
            {
                "name": "Ada Example",
                "events": [
                    {"type": "started-speaking", "timestamp": 10},
                    {"type": "stopped-speaking", "timestamp": 30},
                ],
            }
        ]
    }
    assert "Ada Example -> ok" in section(TWO_TAGS[:1], TAGS, small)  # relative seconds
    assert script.timeline_section([], {}, record(person("A", (1, 2))))  # nothing to compare


def test_the_whole_report_carries_the_section(session, meeting):
    session.add(
        SkribbySegmentEvidence(
            meeting_id=meeting.id,
            items=TWO_TAGS,
            tags=TAGS,
            by_elimination=True,
            events=record(person("Ada Example", (10, 30)), person("Bo Example", (40, 60))),
        )
    )
    session.commit()
    text = _script().report(session, meeting.id)
    assert "== Meet's own speaker timeline" in text and "MISMATCH" in text
    older = _script().report(session, meeting.id, offset=0.0)
    assert older == text
