"""What Skribby sent for a meeting is kept 7 days so a bad speaker labelling can be diagnosed.

Saved in the speakers step (just before the provider's copy is deleted), never in the way of the
notes, deleted by the retention job after 7 days, with the meeting and with the account, and read
only by `scripts/skribby_segments_report.py`.
"""

import importlib.util
import logging
from copy import deepcopy
from datetime import timedelta
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
