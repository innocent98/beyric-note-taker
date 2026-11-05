"""The 5 recorded hours: what counts, when it is checked, and what the user is told."""

from datetime import UTC, datetime, timedelta

import pytest
import sqlalchemy as sa
from sqlalchemy import select

from app import audio, pipeline, usage
from app.config import get_settings
from app.models import AiRun, AppState, Meeting, Notification, UsageEntry
from tests.test_accounts import visitor
from tests.test_api import anon, api, error_of  # noqa: F401  (fixtures used by name)
from tests.test_pipeline import DONE, NOW, RECORDED_SECONDS, FakeVexa, env, meeting, run  # noqa: F401  # noqa: F401  (fixtures used by name)
from tests.test_web import (  # noqa: F401
    MEET,
    OWNER_EMAIL,
    add_meeting,
    anon_client,
    app,
    client,
    fake_openai,
    fake_vexa,
    make_user,
    owner,
    post,
    template,
)

HOUR = 3600
DAY_AGO = datetime.now(UTC) - timedelta(days=1)


def entry(session, user, seconds, meeting_id=None):
    row = UsageEntry(
        user_id=user.id,
        email_hash=usage.email_hash(user.email),
        seconds=seconds,
        meeting_id=meeting_id,
    )
    session.add(row)
    session.commit()
    return row


# ---- the cap ----


def test_the_email_hash_ignores_case_and_spaces():
    assert usage.email_hash(" Ada@Example.TEST ") == usage.email_hash("ada@example.test")
    assert len(usage.email_hash("a@b.test")) == 64


def test_the_default_cap_is_five_hours(session, owner):
    session.add(AppState(id=1))
    session.commit()
    assert usage.effective_cap(session, owner) == 5 * HOUR


def test_with_no_state_row_the_cap_still_applies(session, owner):
    assert usage.effective_cap(session, owner) == 5 * HOUR


def test_a_users_own_cap_beats_the_default(session, owner):
    session.add(AppState(id=1))
    owner.hours_cap_seconds = 2 * HOUR
    session.commit()
    assert usage.effective_cap(session, owner) == 2 * HOUR


def test_a_null_default_means_no_cap_anywhere(session, owner):
    session.add(AppState(id=1, default_hours_cap_seconds=sa.null()))
    session.commit()
    assert usage.effective_cap(session, owner) is None


def test_an_admin_is_never_capped(session):
    admin = make_user(session, "admin@example.test", admin=True, hours_cap_seconds=60)
    assert usage.effective_cap(session, admin) is None


# ---- what is used ----


def test_used_hours_are_the_ledger(session, owner):
    entry(session, owner, 1800)
    entry(session, owner, 600)
    assert usage.hours_used(session, owner, datetime.now(UTC)) == 2400


def test_used_hours_survive_deleting_the_account_and_signing_up_again(session, owner):
    entry(session, owner, 3 * HOUR)
    old_id = owner.id
    session.delete(owner)  # the ledger row keeps only user_id (set null) and the email hash
    session.commit()
    again = make_user(session)  # the same address signs up again
    assert again.id != old_id
    assert usage.hours_used(session, again, datetime.now(UTC)) == 3 * HOUR


def test_other_users_hours_do_not_count(session, owner):
    other = make_user(session, "other@example.test")
    entry(session, other, 4 * HOUR)
    assert usage.hours_used(session, owner, datetime.now(UTC)) == 0


def test_a_running_meeting_counts_the_time_since_its_bot_was_sent(session, owner, template):
    now = datetime.now(UTC)
    add_meeting(session, template, started_polling_at=now - timedelta(minutes=40))
    add_meeting(
        session, template, status="processing", started_polling_at=now - timedelta(minutes=5)
    )
    assert usage.hours_used(session, owner, now) == pytest.approx(45 * 60, abs=2)


def test_a_running_meeting_is_capped_at_the_polling_limit(session, owner, template):
    now = datetime.now(UTC)
    add_meeting(session, template, started_polling_at=now - timedelta(hours=20))
    assert usage.hours_used(session, owner, now, max_poll_seconds=6 * HOUR) == 6 * HOUR


def test_a_meeting_already_in_the_ledger_is_not_counted_twice(session, owner, template):
    now = datetime.now(UTC)
    running = add_meeting(
        session, template, status="processing", started_polling_at=now - timedelta(minutes=30)
    )
    entry(session, owner, 1500, running.id)
    assert usage.hours_used(session, owner, now) == 1500


def test_finished_meetings_without_an_entry_cost_nothing(session, owner, template):
    now = datetime.now(UTC)
    add_meeting(session, template, status="failed", started_polling_at=now - timedelta(hours=2))
    add_meeting(session, template, status="ready", started_polling_at=now - timedelta(hours=2))
    assert usage.hours_used(session, owner, now) == 0


def test_someone_elses_running_meeting_does_not_count(session, owner, template):
    other = make_user(session, "other@example.test")
    theirs = add_meeting(
        session,
        template,
        owner_user_id=other.id,
        started_polling_at=datetime.now(UTC) - timedelta(hours=2),
    )
    assert theirs.owner_user_id == other.id
    assert usage.hours_used(session, owner, datetime.now(UTC)) == 0


# ---- the gate ----


def test_a_meeting_is_refused_once_the_hours_are_used(api, session, owner, template, fake_vexa):
    entry(session, owner, 5 * HOUR)
    error = error_of(
        api.post("/api/meetings", {"url": MEET, "template_id": template.id}),
        403,
        "hours_cap_reached",
    )
    assert "5 recorded hours" in error["message"] and "hello@beyrictech.com" in error["message"]
    assert fake_vexa.sent == []
    assert session.scalars(select(Meeting)).all() == []


def test_a_meeting_is_allowed_just_under_the_cap(api, session, owner, template, fake_vexa):
    entry(session, owner, 5 * HOUR - 1)
    assert api.post("/api/meetings", {"url": MEET, "template_id": template.id}).status_code == 201
    assert fake_vexa.sent == [MEET]


def test_the_message_names_the_users_own_cap(api, session, owner, template):
    owner.hours_cap_seconds = 90 * 60
    session.commit()
    entry(session, owner, 90 * 60)
    error = error_of(
        api.post("/api/meetings", {"url": MEET, "template_id": template.id}),
        403,
        "hours_cap_reached",
    )
    assert "1.5 recorded hours" in error["message"]


def test_a_cap_that_was_removed_lets_everyone_in(api, session, owner, template):
    session.add(AppState(id=1, default_hours_cap_seconds=sa.null()))
    session.commit()
    entry(session, owner, 500 * HOUR)
    assert api.post("/api/meetings", {"url": MEET, "template_id": template.id}).status_code == 201


def test_an_admin_is_not_refused(app, anon_client, session, fake_vexa):
    admin = make_user(session, "admin@example.test", admin=True)
    entry(session, admin, 500 * HOUR)
    from tests.test_web import Template

    template = Template(owner_user_id=admin.id, name="General", instructions="x")
    session.add(template)
    session.commit()
    client = visitor(app)
    assert client.log_in(email="admin@example.test").status_code == 200
    assert (
        client.post("/api/meetings", {"url": MEET, "template_id": template.id}).status_code == 201
    )


def test_the_page_form_is_refused_too(client, session, owner, template, fake_vexa):
    entry(session, owner, 5 * HOUR)
    response = post(client, "/meetings", {"url": MEET, "template_id": template.id})
    assert response.status_code == 403
    assert "5 recorded hours" in response.text
    assert fake_vexa.sent == []


def test_the_cap_is_checked_before_the_bot_busy_rule(api, session, owner, template):
    entry(session, owner, 5 * HOUR)
    add_meeting(session, template, status="processing", step="notes")
    error_of(
        api.post("/api/meetings", {"url": MEET, "template_id": template.id}),
        403,
        "hours_cap_reached",
    )


# ---- what the user sees ----


def test_the_session_reports_used_and_cap(api, session, owner):
    entry(session, owner, 8100)
    assert api.get("/api/session").json()["usage"] == {"used_seconds": 8100, "cap_seconds": 18000}


def test_the_session_reports_no_cap_for_an_admin(app, session):
    make_user(session, "admin@example.test", admin=True)
    client = visitor(app)
    body = client.log_in(email="admin@example.test").json()
    assert body["usage"] == {"used_seconds": 0, "cap_seconds": None}
    assert body["user"]["is_admin"] is True


# ---- counting ----


def test_recording_usage_writes_the_entry_and_the_meetings_seconds(session, meeting, owner):
    usage.record_usage(session, meeting, 1800)
    session.refresh(meeting)
    assert meeting.recorded_seconds == 1800
    [row] = session.scalars(select(UsageEntry)).all()
    assert (row.user_id, row.meeting_id, row.seconds) == (owner.id, meeting.id, 1800)
    assert row.email_hash == usage.email_hash(OWNER_EMAIL)


def test_recording_usage_twice_counts_once(session, meeting):
    usage.record_usage(session, meeting, 1800)
    usage.record_usage(session, meeting, 1800)
    assert len(session.scalars(select(UsageEntry)).all()) == 1


def test_deleting_a_meeting_does_not_give_the_hours_back(session, meeting, owner):
    usage.record_usage(session, meeting, 1800)
    session.delete(meeting)
    session.commit()
    assert usage.hours_used(session, owner, datetime.now(UTC)) == 1800


def test_at_eighty_percent_the_user_gets_one_notice(session, meeting, owner):
    usage.record_usage(session, meeting, 3 * HOUR)  # 60%: nothing yet
    assert session.scalars(select(Notification)).all() == []
    second = Meeting(
        owner_user_id=owner.id,
        url="u",
        platform="zoom",
        native_id="2",
        template_id=meeting.template_id,
    )
    third = Meeting(
        owner_user_id=owner.id,
        url="u",
        platform="zoom",
        native_id="3",
        template_id=meeting.template_id,
    )
    session.add_all([second, third])
    session.commit()
    usage.record_usage(session, second, 1 * HOUR + 600)  # 4h10: past 80%
    usage.record_usage(session, third, 600)  # another entry: no second notice
    [notice] = session.scalars(select(Notification)).all()
    assert (notice.user_id, notice.kind, notice.severity) == (owner.id, "usage.hours_low", "info")
    assert "4.2 of your 5 recorded hours" in notice.body
    assert notice.read_at is None


def test_an_admin_gets_no_hours_notice(session, meeting, owner):
    owner.is_admin = True
    session.commit()
    usage.record_usage(session, meeting, 50 * HOUR)
    assert session.scalars(select(Notification)).all() == []


# ---- the pipeline counts the recording ----


def test_the_audio_step_counts_the_recording(session, meeting, owner):
    run(session, meeting, FakeVexa(DONE))
    assert meeting.status == "ready"
    assert meeting.recorded_seconds == RECORDED_SECONDS
    [row] = session.scalars(select(UsageEntry)).all()
    assert (row.user_id, row.meeting_id, row.seconds) == (owner.id, meeting.id, RECORDED_SECONDS)


def test_a_retry_does_not_count_the_recording_again(session, meeting, monkeypatch):
    calls = []
    real = pipeline.generate_notes

    def fail_once(*args, **kwargs):
        calls.append(1)
        if len(calls) == 1:
            raise RuntimeError("boom")
        return real(*args, **kwargs)

    monkeypatch.setattr(pipeline, "generate_notes", fail_once)
    run(session, meeting, FakeVexa(DONE))
    assert meeting.status == "failed"  # the recording was counted, the notes were not written
    meeting.status = "processing"
    session.commit()
    run(session, meeting, FakeVexa(DONE))
    assert len(session.scalars(select(UsageEntry)).all()) == 1


def test_a_recording_with_no_speech_still_counts(session, meeting, monkeypatch):
    monkeypatch.setattr(
        audio, "prepare_recording", lambda s, o: audio.Recording(pipeline_chunks(o), 120)
    )
    monkeypatch.setattr(pipeline, "transcribe_chunks", lambda *a, **k: "uh")
    run(session, meeting, FakeVexa(DONE))
    assert meeting.status == "failed"
    assert [row.seconds for row in session.scalars(select(UsageEntry))] == [120]


def test_a_failed_transcription_still_counts_the_recording(session, meeting, monkeypatch):
    """The OpenAI cost is spent whether or not the transcript comes back: the ledger row is
    committed before transcription, so the failure's rollback cannot take it away."""

    def refuse(*args, **kwargs):
        raise pipeline.TranscriptionFailed("openai down")

    monkeypatch.setattr(pipeline, "transcribe_chunks", refuse)
    run(session, meeting, FakeVexa(DONE))
    assert meeting.status == "failed"
    assert [row.seconds for row in session.scalars(select(UsageEntry))] == [RECORDED_SECONDS]
    assert meeting.recorded_seconds == RECORDED_SECONDS


def pipeline_chunks(out_dir):
    from tests.test_pipeline import _good_chunks

    return _good_chunks(out_dir)


def test_a_meeting_that_never_recorded_costs_nothing(session, meeting):
    run(
        session,
        meeting,
        FakeVexa(__import__("app.vexa", fromlist=["x"]).VexaMeeting(status="failed")),
    )
    assert meeting.status == "failed"
    assert session.scalars(select(UsageEntry)).all() == []
    assert meeting.recorded_seconds is None


def test_a_transcription_is_one_ledger_row_with_the_audio_seconds(session, meeting, monkeypatch):
    monkeypatch.setattr(
        audio, "prepare_recording", lambda s, o: audio.Recording(pipeline_chunks(o), 125)
    )
    monkeypatch.setattr(pipeline, "transcribe_chunks", lambda *a, **k: "uh")  # no speech: billed
    run(session, meeting, FakeVexa(DONE))
    rows = list(session.scalars(select(AiRun).where(AiRun.kind == "transcribe")))
    assert [(r.meeting_id, r.audio_seconds, r.status, r.input_tokens) for r in rows] == [
        (meeting.id, 125, "ok", None)
    ]
    assert rows[0].model == get_settings().transcription_model
