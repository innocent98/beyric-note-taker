"""Old meetings that a provider no longer in the code ran (`bot_provider = 'vexa'`, history).

Nothing may be asked of anyone for them: not a lookup, a stop, a delete or a download. Account
deletion and the retention pass treat the provider's copy as already gone (it was verified deleted
before the provider was removed) and never wait on it or fail on it; a meeting of theirs still in
flight is failed at once; one past the download needs nothing from the provider and finishes.
See docs/sop/2026-11-20-remove-vexa.md.
"""

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select

from app import deletion, pipeline, services
from app.config import Settings
from app.models import Meeting, Template, User
from tests.test_web import make_user

NOW = datetime(2026, 11, 20, 12, 0, tzinfo=UTC)


class Tripwire:
    """A provider that fails the test if anything at all is asked of it."""

    def __getattr__(self, name):
        def call(*args, **kwargs):
            raise AssertionError(f"a retired provider's meeting asked the provider: {name}")

        return call


@pytest.fixture(autouse=True)
def audio_dir(tmp_path, monkeypatch):
    settings = Settings(audio_dir=str(tmp_path))
    monkeypatch.setattr(pipeline, "get_settings", lambda: settings)
    monkeypatch.setattr(deletion, "get_settings", lambda: settings, raising=False)
    return tmp_path


@pytest.fixture
def template(session):
    owner = make_user(session, "old@example.test")
    tpl = Template(owner_user_id=owner.id, name="General", instructions="x")
    session.add(tpl)
    session.commit()
    return tpl


def old_meeting(session, template, *, status="ready", step="done", **fields):
    values = {
        "owner_user_id": template.owner_user_id,
        "url": "https://meet.google.com/abc-defg-hij",
        "platform": "google_meet",
        "native_id": "abc-defg-hij",
        "template_id": template.id,
        "bot_provider": "vexa",
        "provider_bot_id": "55",
        "provider_recording_id": 42,
        "status": status,
        "step": step,
        "created_at": NOW - timedelta(days=30),
        **fields,
    }
    meeting = Meeting(**values)
    session.add(meeting)
    session.commit()
    return meeting


def test_retention_clears_an_old_meeting_without_asking_anyone(session, template):
    meeting = old_meeting(session, template)
    cleared = pipeline.delete_expired_audio(session, NOW, provider=Tripwire())
    assert cleared == 1
    session.refresh(meeting)
    assert meeting.audio_deleted_at is not None  # done: nothing was left to wait for


def test_retention_does_not_mix_up_a_retired_and_a_current_meeting(session, template):
    old = old_meeting(session, template)
    deleted = []

    class Current:
        name = "skribby"

        def delete_recording(self, recording_id):
            deleted.append(recording_id)

        def delete_bot_record(self, bot_id):
            deleted.append(f"record:{bot_id}")

    new = old_meeting(
        session,
        template,
        bot_provider="skribby",
        provider_bot_id="9a8b7c6d-5e4f-4a3b-8c2d-1e0f9a8b7c6d",
        provider_recording_id=2**60 + 5,
        native_id="zzz-yyyy-xxx",
    )
    assert pipeline.delete_expired_audio(session, NOW, provider=Current()) == 2
    assert deleted == [2**60 + 5]  # only the current provider was asked, and only for its own
    session.refresh(old)
    session.refresh(new)
    assert old.audio_deleted_at is not None and new.audio_deleted_at is not None


def test_deleting_an_account_with_an_old_meeting_never_waits_or_fails(session, template):
    old_meeting(session, template)
    stuck = old_meeting(session, template, status="bot_sent", step="waiting", native_id="stu-ckst")
    user = session.get(User, template.owner_user_id)
    deletion.purge_user(session, user, Tripwire(), NOW)  # no stop, no lookup, no delete
    assert session.scalars(select(Meeting)).all() == []
    assert session.get(Meeting, stuck.id) is None


def test_deleting_one_old_meeting_by_hand_works_without_the_provider(session, template):
    meeting = old_meeting(session, template)
    services.delete_meeting(session, Tripwire(), meeting)
    assert session.get(Meeting, meeting.id) is None


@pytest.mark.parametrize("step", ["waiting", "audio"])
def test_an_old_meeting_still_in_flight_is_failed_at_once_and_nothing_is_asked(
    session, template, step
):
    meeting = old_meeting(session, template, status="bot_sent", step=step)
    pipeline.advance_meeting(session, meeting.id, provider=Tripwire(), now=NOW)
    session.refresh(meeting)
    assert (meeting.status, meeting.failure_reason) == ("failed", pipeline.REASON_PROVIDER_GONE)


def test_the_failed_meeting_frees_its_bot_slot(session, template):
    meeting = old_meeting(session, template, status="bot_sent", step="waiting")
    pipeline.advance_meeting(session, meeting.id, provider=Tripwire(), now=NOW)
    session.refresh(meeting)
    assert meeting.status == "failed"  # placement counts only `bot_sent` and `processing`


def test_an_old_meeting_past_the_download_finishes_without_the_provider(session, template):
    from tests.test_pipeline import NOTES, TRANSCRIPT, FakeOpenAI

    meeting = old_meeting(
        session,
        template,
        status="processing",
        step="speakers",
        transcript=TRANSCRIPT,
        transcript_source="own",
    )
    pipeline.advance_meeting(
        session, meeting.id, provider=Tripwire(), openai_client=FakeOpenAI(notes=NOTES), now=NOW
    )
    session.refresh(meeting)
    assert meeting.status == "ready"  # the speakers step read "not found" as "no names"
    assert meeting.speakers_incomplete is True
