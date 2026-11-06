"""W0.5: `DELETE /api/meetings/{id}` and what goes with the meeting.

The cascade test is written over the schema, not over a list of tables: any table that points at
`meetings.id` must either cascade or be named in `KEPT` with a reason. A table added later fails
here until it does one or the other (W2's `tasks` cascades).
"""

import pytest
from sqlalchemy import select

from app import teams
from app.models import (
    AiRun,
    Base,
    Decision,
    MailOutbox,
    Meeting,
    MeetingTranslation,
    Notification,
    OpenQuestion,
    ReviewItem,
    ScheduledJoin,
    Task,
    UsageEntry,
    User,
)
from app.vexa import VexaError
from tests.test_api import (  # noqa: F401  (fixtures used by name)
    anon,
    api,
    error_of,
    ready,
)
from tests.test_team_visibility import TEAMMATE_EMAIL, share, sign_in, teammate  # noqa: F401
from tests.test_web import (  # noqa: F401
    OTHER_EMAIL,
    anon_client,
    app,
    fake_openai,
    fake_vexa,
    owner,
    stranger,
    template,
)

# Tables that keep their row, with `meeting_id` set to null, and why.
KEPT = {
    "usage_entries": "the recorded hours stay spent",
    "ai_runs": "cost per hour and the daily translation limit; holds no meeting text",
    "scheduled_joins": "the calendar event stays joined, so no bot is sent to it twice",
    "series_agenda_items": "the line stays consumed (consumed_at), so it does not come back",
    "milestones": "a confirmed milestone stays; delete_meeting removes the meeting's proposals",
}


def test_every_table_that_points_at_a_meeting_cascades_or_is_kept_on_purpose():
    found = {}
    for table in Base.metadata.sorted_tables:
        for fk in table.foreign_keys:
            if fk.column.table.name == "meetings" and fk.parent.table.name != "meetings":
                found[table.name] = (fk.ondelete, fk.parent.name)
    assert found, "the schema walk found nothing"
    for name, (rule, column) in found.items():
        if name in KEPT:
            assert rule == "SET NULL", (name, column, rule)
        else:
            assert rule == "CASCADE", f"{name}.{column} must cascade or be listed in KEPT"
    assert set(KEPT) <= set(found), set(KEPT) - set(found)


def fill(session, meeting):
    """One row of everything that hangs off a meeting."""
    uid = meeting.owner_user_id
    session.add_all(
        [
            MeetingTranslation(
                meeting_id=meeting.id,
                language="fr",
                status="ready",
                notes_md="Notes",
                transcript="Texte",
                source_hash="x",
                model="m",
            ),
            Decision(meeting_id=meeting.id, text="d", position=0, fingerprint="d"),
            Task(
                team_id=teams.ensure_team(session, session.get(User, uid)).id,
                meeting_id=meeting.id,
                title="t",
                source="ai",
                ai_fingerprint="t",
            ),
            OpenQuestion(meeting_id=meeting.id, text="q", position=0, fingerprint="q"),
            ReviewItem(
                meeting_id=meeting.id, owner_user_id=uid, text="r", position=0, fingerprint="r"
            ),
            Notification(
                user_id=uid, kind="k", title="t", meeting_id=meeting.id, dedupe_key=f"k{meeting.id}"
            ),
            AiRun(meeting_id=meeting.id, user_id=uid, kind="translate", model="m"),
            UsageEntry(user_id=uid, email_hash="h", meeting_id=meeting.id, seconds=600),
            MailOutbox(
                to_email="o@example.test",
                template="notes_ready",
                payload={"meeting_id": meeting.id},
            ),
            MailOutbox(
                to_email="t@example.test",
                template="tasks_assigned",
                payload={"meeting_id": meeting.id, "user_id": uid},
            ),
        ]
    )
    session.add(
        ScheduledJoin(
            user_id=uid,
            platform="google_meet",
            native_id=meeting.native_id,
            meeting_url="https://meet.google.com/" + meeting.native_id,
            starts_at=meeting.created_at,
            ends_at=meeting.created_at,
            run_at=meeting.created_at,
            deadline_at=meeting.created_at,
            state="joined",
            meeting_id=meeting.id,
        )
    )
    session.commit()


def count(session, model, **where):
    return len(session.scalars(select(model).filter_by(**where)).all())


def test_the_owner_deletes_a_meeting_and_everything_derived_from_it(api, session, template):
    gone, kept = ready(session, template), ready(session, template, native_id="zzz-zzzz-zzz")
    fill(session, gone)
    fill(session, kept)
    gone_id, owner_id = gone.id, gone.owner_user_id
    response = api.delete(f"/api/meetings/{gone_id}")
    assert response.status_code == 204 and response.content == b""
    session.expire_all()
    assert session.get(Meeting, gone_id) is None
    for model in (MeetingTranslation, Decision, Task, OpenQuestion, ReviewItem, Notification):
        assert count(session, model, meeting_id=gone_id) == 0, model
        assert count(session, model, meeting_id=kept.id) == 1, model
    # mail waiting for this meeting is gone, mail for another meeting is not
    mails = session.scalars(select(MailOutbox)).all()
    assert [m.payload["meeting_id"] for m in mails] == [kept.id, kept.id]
    # kept on purpose, unlinked
    assert count(session, UsageEntry, user_id=owner_id, seconds=600) == 2
    assert count(session, UsageEntry, meeting_id=gone_id) == 0
    assert count(session, AiRun, meeting_id=gone_id) == 0
    assert count(session, AiRun, user_id=owner_id) == 2
    joins = session.scalars(select(ScheduledJoin).order_by(ScheduledJoin.id)).all()
    assert [j.meeting_id for j in joins] == [None, kept.id] and joins[0].state == "joined"
    assert api.get(f"/api/meetings/{gone_id}").status_code == 404
    assert [m["id"] for m in api.get("/api/meetings").json()["meetings"]] == [kept.id]


def test_a_meeting_that_is_recording_or_being_written_is_not_deleted(api, session, template):
    for status, step in (("bot_sent", "waiting"), ("processing", "notes")):
        meeting = ready(session, template, status=status, step=step)
        error = error_of(api.delete(f"/api/meetings/{meeting.id}"), 409, "meeting_active")
        assert "still" in error["message"]
        session.expire_all()
        assert session.get(Meeting, meeting.id) is not None


@pytest.mark.parametrize("status", ["ready", "failed"])
def test_a_finished_meeting_can_go_whatever_its_outcome(api, session, template, status):
    meeting = ready(session, template, status=status)
    assert api.delete(f"/api/meetings/{meeting.id}").status_code == 204


def test_a_teammate_and_an_outsider_get_404_and_nothing_changes(
    app, session, template, owner, teammate, stranger
):
    meeting = ready(session, template)
    share(session, meeting, owner)
    for email in (TEAMMATE_EMAIL, OTHER_EMAIL):
        error_of(sign_in(app, email).delete(f"/api/meetings/{meeting.id}"), 404, "not_found")
    session.expire_all()
    assert session.get(Meeting, meeting.id) is not None


def test_an_unknown_id_is_404_and_a_visitor_is_refused(api, anon_client, session, template):
    error_of(api.delete("/api/meetings/999999"), 404, "not_found")
    assert anon_client.delete("/api/meetings/1").status_code in (401, 403)


def test_the_recording_at_vexa_is_removed_first_and_a_failure_deletes_nothing(
    api, fake_vexa, session, template
):
    calls = []
    fake_vexa.delete_recording = calls.append
    meeting = ready(session, template, vexa_recording_id=77)
    assert api.delete(f"/api/meetings/{meeting.id}").status_code == 204
    assert calls == [77]

    def refuse(_id):
        raise VexaError("delete_recording failed: secret detail")

    fake_vexa.delete_recording = refuse
    other = ready(session, template, vexa_recording_id=78)
    error = error_of(api.delete(f"/api/meetings/{other.id}"), 502, "recording_delete_failed")
    assert "secret" not in error["message"]
    session.expire_all()
    assert session.get(Meeting, other.id) is not None


def test_audio_already_deleted_is_not_asked_of_vexa_again(api, fake_vexa, session, template):
    from datetime import UTC, datetime

    fake_vexa.delete_recording = lambda _id: pytest.fail("called")
    meeting = ready(session, template, vexa_recording_id=79, audio_deleted_at=datetime.now(UTC))
    assert api.delete(f"/api/meetings/{meeting.id}").status_code == 204


def test_the_audio_folder_goes_with_the_meeting(api, session, template, tmp_path, monkeypatch):
    from app import services

    folder = tmp_path / "7"
    folder.mkdir()
    (folder / "audio.wav").write_bytes(b"x")
    monkeypatch.setattr(services, "audio_path", lambda _id: folder / "audio.wav")
    meeting = ready(session, template)
    assert api.delete(f"/api/meetings/{meeting.id}").status_code == 204
    assert not folder.exists()


def test_a_cancelled_meeting_whose_recording_id_was_never_stored_is_asked_of_vexa(
    api, fake_vexa, session, template
):
    """The bot was stopped and the meeting marked failed before Vexa's recording id was seen."""
    from types import SimpleNamespace

    deleted = []
    fake_vexa.delete_recording = deleted.append
    fake_vexa.get_meeting_by_id = lambda _id: SimpleNamespace(status="completed", recording_id=91)
    meeting = ready(session, template, status="failed", vexa_meeting_id="55")
    assert api.delete(f"/api/meetings/{meeting.id}").status_code == 204
    assert deleted == [91]


def test_a_meeting_vexa_has_not_settled_is_not_deleted(api, fake_vexa, session, template):
    from types import SimpleNamespace

    fake_vexa.get_meeting_by_id = lambda _id: SimpleNamespace(status="active", recording_id=None)
    meeting = ready(session, template, status="failed", vexa_meeting_id="56")
    error_of(api.delete(f"/api/meetings/{meeting.id}"), 502, "recording_delete_failed")
    session.expire_all()
    assert session.get(Meeting, meeting.id) is not None


def test_mail_already_sent_stays_for_the_budget_and_only_waiting_mail_goes(api, session, template):
    meeting = ready(session, template)
    session.add(
        MailOutbox(
            to_email="o@example.test",
            template="notes_ready",
            payload={"meeting_id": meeting.id},
            status="sent",
        )
    )
    session.commit()
    assert api.delete(f"/api/meetings/{meeting.id}").status_code == 204
    assert len(session.scalars(select(MailOutbox)).all()) == 1
