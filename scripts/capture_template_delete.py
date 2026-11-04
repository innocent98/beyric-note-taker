"""Drive `DELETE /api/templates/{id}` over real HTTP and save every exchange for the frontend guide.

    python scripts/capture_template_delete.py postgresql+psycopg://USER:PASS@localhost:PORT/postgres

Same set-up as `scripts/capture_owner_api.py` (it is imported for the recorder, the fakes and the
database preparation): real migrations, the real app under uvicorn, real HTTP, only Vexa and OpenAI
replaced by fakes. The person is the admin account migration 0006 creates, with the four templates
migration 0007 gave them. Meetings are inserted by SQL in the states the worker leaves them in, and
the stored rows that matter (the calendar setting, a planned join) are saved as files between the
calls, so every claim in `docs/fe-integration-guide-owner-app.md` section 5b can be re-derived.

Files go to `docs/fe-captures/template-delete/NN-name.json`.

What is NOT real: Vexa and OpenAI (fakes), the worker (it never runs), and the Google calendar:
the calendar setting is written by SQL, because `PUT /api/calendar/settings` answers 404 while
`CALENDAR_ENABLED` is off, as in this run.
"""

import shutil
import sys
from datetime import UTC, datetime, timedelta

import capture_owner_api as base

base.OUT = base.ROOT / "docs" / "fe-captures" / "template-delete"
OUT = base.OUT

from sqlalchemy import create_engine, select  # noqa: E402
from sqlalchemy.orm import Session  # noqa: E402

from app.accounts import create_user_defaults  # noqa: E402
from app.config import Settings  # noqa: E402
from app.main import create_app  # noqa: E402
from app.models import Meeting, ScheduledJoin, Template, User, UserSettings  # noqa: E402
from app.passwords import hash_password  # noqa: E402
from app.services import get_openai_client, get_vexa  # noqa: E402

Recorder = base.Recorder


def note_file(name: str, note: str, content: dict) -> None:
    """Save something that is not an HTTP exchange (a stored row)."""
    import json

    Recorder.count += 1
    record = {"note": note, "content": content}
    (OUT / f"{Recorder.count:02d}-{name}.json").write_text(
        json.dumps(record, indent=2, ensure_ascii=False) + "\n"
    )
    print(f"{Recorder.count:02d}-{name}")


def rows(engine, owner_id: int) -> dict:
    """What the delete is meant to leave behind, read straight from the tables."""
    with Session(engine) as session:
        templates = session.execute(
            select(Template.id, Template.name, Template.deleted_at).where(
                Template.owner_user_id == owner_id
            )
        ).all()
        setting = session.scalars(
            select(UserSettings.calendar_template_id).where(UserSettings.user_id == owner_id)
        ).first()
        joins = session.execute(
            select(ScheduledJoin.state, ScheduledJoin.template_id)
            .where(ScheduledJoin.user_id == owner_id)
            .order_by(ScheduledJoin.id)
        ).all()
        meetings = session.execute(
            select(Meeting.id, Meeting.status, Meeting.template_id)
            .where(Meeting.owner_user_id == owner_id)
            .order_by(Meeting.id)
        ).all()
    return {
        "templates": [
            {"id": i, "name": n, "deleted": deleted is not None} for i, n, deleted in templates
        ],
        "calendar_template_id": setting,
        "scheduled_joins": [{"state": s, "template_id": t} for s, t in joins],
        "meetings": [{"id": i, "status": s, "template_id": t} for i, s, t in meetings],
    }


def main() -> None:
    if not sys.argv[1:]:
        sys.exit(__doc__)
    base.prepare_database()
    shutil.rmtree(OUT, ignore_errors=True)
    OUT.mkdir(parents=True)
    settings = Settings(
        _env_file=None,
        app_env="test",
        session_secret="capture-secret-" + "x" * 30,
        cookie_secure=False,
        database_url=base.scratch_url,
    )
    vexa, openai_client = base.FakeVexa(), base.FakeOpenAI()
    app = create_app(settings)
    app.dependency_overrides[get_vexa] = lambda: vexa
    app.dependency_overrides[get_openai_client] = lambda: openai_client
    server, server_base = base.start_server(app)
    engine = create_engine(base.scratch_url)
    try:
        run(server_base, engine)
    finally:
        server.should_exit = True
        engine.dispose()


def run(server_base, engine) -> None:
    owner_id = base.owner_id_of(engine)
    owner = Recorder(server_base)
    owner.call("session-anonymous", "GET", "/api/session", note="Read the CSRF token.")
    owner.call("login", "POST", "/api/session", body=base.LOGIN)
    listed = owner.call(
        "templates-before",
        "GET",
        "/api/templates",
        note="The four templates every account starts with, sorted by name.",
    ).json()["templates"]
    ids = {t["name"]: t["id"] for t in listed}

    # A meeting written with "Interview" (the one still being written is covered by the tests).
    with Session(engine) as session:
        ready = Meeting(
            owner_user_id=owner_id,
            url=base.MEET,
            platform="google_meet",
            native_id="abc-defg-hij",
            template_id=ids["Interview"],
            status="ready",
            step="done",
            transcript=base.PLAIN_TRANSCRIPT,
            speaker_transcript=base.SPEAKER_TRANSCRIPT,
            notes=base.NOTES,
        )
        session.add(ready)
        # "Interview" is the template for calendar meetings, and a join is planned with it
        start = datetime.now(UTC) + timedelta(hours=3)
        settings_row = session.get(UserSettings, owner_id) or UserSettings(user_id=owner_id)
        settings_row.calendar_template_id = ids["Interview"]
        session.add(settings_row)
        session.add(
            ScheduledJoin(
                user_id=owner_id,
                source="calendar",
                platform="google_meet",
                native_id="cal-meet-one",
                meeting_url=base.MEET,
                starts_at=start,
                ends_at=start + timedelta(hours=1),
                run_at=start - timedelta(minutes=1),
                deadline_at=start + timedelta(minutes=15),
                state="scheduled",
                template_id=ids["Interview"],
            )
        )
        session.commit()
        ready_id = ready.id
    note_file(
        "rows-before",
        "A meeting written with Interview, the calendar setting and a planned join pointing at it.",
        rows(engine, owner_id),
    )
    owner.call(
        "meeting-before",
        "GET",
        f"/api/meetings/{ready_id}",
        note="A ready meeting before the delete: `template_id` and `template_name` (new field).",
    )

    # ---- the delete ----
    owner.call(
        "delete-without-csrf",
        "DELETE",
        f"/api/templates/{ids['Interview']}",
        csrf=None,
        note="Like every write: no token, no delete (403 csrf_rejected).",
    )
    owner.call(
        "delete",
        "DELETE",
        f"/api/templates/{ids['Interview']}",
        note="Deleted: 204, no body.",
    )
    note_file(
        "rows-after",
        "The template row is marked deleted (kept); the calendar setting and the planned join "
        "moved to the oldest template left (General); the meeting still points at Interview.",
        rows(engine, owner_id),
    )
    owner.call(
        "templates-after",
        "GET",
        "/api/templates",
        note="Three left: the deleted one is not listed.",
    )
    owner.call(
        "meeting-after",
        "GET",
        f"/api/meetings/{ready_id}",
        note="The same meeting: notes kept, `template_id` unchanged, `template_name` is the "
        "name the notes were written with.",
    )
    owner.call(
        "meetings-after",
        "GET",
        "/api/meetings",
        note="The list items carry `template_name` too.",
    )
    owner.call(
        "delete-again",
        "DELETE",
        f"/api/templates/{ids['Interview']}",
        note="A second delete (a double click, another tab): 404 not_found, never 500.",
    )
    owner.call(
        "delete-unknown",
        "DELETE",
        "/api/templates/999999",
        note="An id that never existed: the same 404.",
    )
    owner.call(
        "edit-deleted",
        "PUT",
        f"/api/templates/{ids['Interview']}",
        body={"name": "Interview", "instructions": "x"},
        note="A deleted template cannot be edited: 404.",
    )
    owner.call(
        "rewrite-with-deleted",
        "POST",
        f"/api/meetings/{ready_id}/regenerate",
        body={"template_id": ids["Interview"]},
        note="Nor chosen for a rewrite (400 template_required, the message the meeting page "
        "already shows) ...",
    )
    owner.call(
        "new-meeting-with-deleted",
        "POST",
        "/api/meetings",
        body={"url": base.MEET, "template_id": ids["Interview"]},
        note="... or for a new meeting.",
    )
    owner.call(
        "rewrite-with-deleted-meeting-with-another",
        "POST",
        f"/api/meetings/{ready_id}/regenerate",
        body={"template_id": ids["General"]},
        note="The meeting can still be rewritten with a live template; it then names that one.",
    )
    owner.call(
        "name-reused",
        "POST",
        "/api/templates",
        body={"name": "Interview", "instructions": "A new one."},
        note="The deleted name is free again (201).",
    )

    # ---- somebody else's template ----
    with Session(engine) as session:
        pilot = User(
            email=base.PILOT_EMAIL,
            name="Pilot Example",
            password_hash=hash_password(base.PILOT_PASSWORD),
            email_verified_at=datetime.now(UTC),
        )
        session.add(pilot)
        session.commit()
        create_user_defaults(session, pilot)
    guest = Recorder(server_base)
    guest.call("guest-session", "GET", "/api/session")
    guest.call(
        "guest-login",
        "POST",
        "/api/session",
        body={"email": base.PILOT_EMAIL, "password": base.PILOT_PASSWORD},
    )
    guest.call(
        "delete-someone-elses",
        "DELETE",
        f"/api/templates/{ids['General']}",
        note="Another person's template: 404 not_found, the same as one that does not exist.",
    )

    # ---- the last one ----
    for name in ("Client call", "Team update"):
        owner.call(
            f"delete-{name.lower().replace(' ', '-')}", "DELETE", f"/api/templates/{ids[name]}"
        )
    with Session(engine) as session:
        interview_again = session.scalars(
            select(Template.id).where(
                Template.owner_user_id == owner_id,
                Template.name == "Interview",
                Template.deleted_at.is_(None),
            )
        ).one()
    owner.call(
        "delete-second-to-last",
        "DELETE",
        f"/api/templates/{interview_again}",
        note="Two left (General and the new Interview): deleting one is fine ...",
    )
    owner.call(
        "delete-last",
        "DELETE",
        f"/api/templates/{ids['General']}",
        note="... but the last one is refused: 409 `last_template`, with a message written for "
        "the person.",
    )
    owner.call("templates-last", "GET", "/api/templates", note="General is still there.")


if __name__ == "__main__":
    main()
