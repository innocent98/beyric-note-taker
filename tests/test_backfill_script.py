from datetime import UTC, datetime, timedelta

from sqlalchemy import select

from app.models import Decision, Meeting, Template, User
from scripts import backfill_extraction
from tests.test_workspace import NOTES, TRANSCRIPT


def seed(session):
    user = User(email="a@example.test")
    session.add(user)
    session.flush()
    template = Template(owner_user_id=user.id, name="General", instructions="x")
    session.add(template)
    session.flush()
    now = datetime.now(UTC)
    for days in (2, 60):
        session.add(
            Meeting(
                owner_user_id=user.id,
                url="u",
                platform="zoom",
                native_id=str(days),
                template_id=template.id,
                status="ready",
                step="done",
                notes=NOTES,
                speaker_transcript=TRANSCRIPT,
                created_at=now - timedelta(days=days),
            )
        )
    session.commit()


def statuses(session):
    return {m.native_id: m.extraction_status for m in session.scalars(select(Meeting))}


def test_the_script_queues_recent_meetings_and_reads_decisions_for_every_one(
    session, engine, monkeypatch, capsys
):
    seed(session)
    monkeypatch.setattr(backfill_extraction, "SessionLocal", lambda: type(session)(engine))
    assert backfill_extraction.run([]) == 0
    session.expire_all()
    assert statuses(session) == {"2": "pending", "60": "skipped"}
    assert len(session.scalars(select(Decision)).all()) == 2  # the old one's decisions
    out = capsys.readouterr().out
    assert "1 queued" in out and "1 decisions only" in out

    assert backfill_extraction.run([]) == 0  # again: nothing to do
    assert "0 queued" in capsys.readouterr().out


def test_the_task_window_can_be_changed(session, engine, monkeypatch):
    seed(session)
    monkeypatch.setattr(backfill_extraction, "SessionLocal", lambda: type(session)(engine))
    assert backfill_extraction.run(["--task-days", "90"]) == 0
    session.expire_all()
    assert statuses(session) == {"2": "pending", "60": "pending"}
