"""Drive `DELETE /api/meetings/{id}` over real HTTP and save every exchange for the frontend guide.

    python scripts/capture_meeting_delete.py postgresql+psycopg://USER:PASS@localhost:PORT/postgres

Same set-up as `scripts/capture_template_delete.py`: real migrations, the real app under uvicorn,
real HTTP, only Vexa and OpenAI replaced by fakes (the fake Vexa accepts `delete_recording`). Rows
are inserted by SQL in the states the worker leaves them in, and the stored rows are saved between
calls. Files go to `docs/fe-captures/meeting-delete/NN-name.json`.

What is NOT real: Vexa and OpenAI (fakes), the worker (it never runs). The `502
recording_delete_failed` answer comes from a fake Vexa told to fail.
"""

import shutil
import sys
from datetime import UTC, datetime

import capture_owner_api as base

base.OUT = base.ROOT / "docs" / "fe-captures" / "meeting-delete"
OUT = base.OUT

from sqlalchemy import create_engine, func, select  # noqa: E402
from sqlalchemy.orm import Session  # noqa: E402

from app.accounts import create_user_defaults  # noqa: E402
from app.config import Settings  # noqa: E402
from app.main import create_app  # noqa: E402
from app.models import (  # noqa: E402
    AiRun,
    Decision,
    MailOutbox,
    Meeting,
    MeetingTranslation,
    Notification,
    OpenQuestion,
    ReviewItem,
    UsageEntry,
    User,
)
from app.passwords import hash_password  # noqa: E402
from app.services import get_openai_client, get_vexa  # noqa: E402
from app.vexa import VexaError  # noqa: E402

Recorder = base.Recorder


class DeletingVexa(base.FakeVexa):
    fail = False
    deleted: list

    def delete_recording(self, recording_id):
        if self.fail:
            raise VexaError("delete_recording failed: upstream detail")
        self.deleted = getattr(self, "deleted", []) + [recording_id]


def note_file(name: str, note: str, content: dict) -> None:
    import json

    Recorder.count += 1
    record = {"note": note, "content": content}
    (OUT / f"{Recorder.count:02d}-{name}.json").write_text(
        json.dumps(record, indent=2, ensure_ascii=False) + "\n"
    )
    print(f"{Recorder.count:02d}-{name}")


def counts(engine, meeting_id: int) -> dict:
    with Session(engine) as session:

        def n(model, col=None):
            return session.scalar(
                select(func.count())
                .select_from(model)
                .where((col or model.meeting_id) == meeting_id)
            )

        return {
            "meeting": n(Meeting, Meeting.id),
            "translations": n(MeetingTranslation),
            "decisions": n(Decision),
            "open_questions": n(OpenQuestion),
            "review_items": n(ReviewItem),
            "notifications": n(Notification),
            "ai_runs_still_linked": n(AiRun),
            "usage_entries_still_linked": n(UsageEntry),
            "mail_waiting_for_it": session.scalar(
                select(func.count()).where(
                    MailOutbox.payload["meeting_id"].astext == str(meeting_id)
                )
            ),
            "owner_usage_seconds": session.scalar(
                select(func.coalesce(func.sum(UsageEntry.seconds), 0))
            ),
            "owner_ai_runs": session.scalar(select(func.count()).select_from(AiRun)),
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
    vexa, openai_client = DeletingVexa(), base.FakeOpenAI()
    app = create_app(settings)
    app.dependency_overrides[get_vexa] = lambda: vexa
    app.dependency_overrides[get_openai_client] = lambda: openai_client
    server, server_base = base.start_server(app)
    engine = create_engine(base.scratch_url)
    try:
        run(server_base, engine, vexa)
    finally:
        server.should_exit = True
        engine.dispose()


def make_meeting(session, owner_id, template_id, native_id, **fields) -> Meeting:
    values = dict(
        owner_user_id=owner_id,
        url=base.MEET,
        platform="google_meet",
        native_id=native_id,
        template_id=template_id,
        status="ready",
        step="done",
        transcript=base.PLAIN_TRANSCRIPT,
        speaker_transcript=base.SPEAKER_TRANSCRIPT,
        notes=base.NOTES,
    )
    meeting = Meeting(**{**values, **fields})
    session.add(meeting)
    session.commit()
    return meeting


def run(server_base, engine, vexa) -> None:
    owner_id = base.owner_id_of(engine)
    owner = Recorder(server_base)
    owner.call("session-anonymous", "GET", "/api/session", note="Read the CSRF token.")
    owner.call("login", "POST", "/api/session", body=base.LOGIN)
    template_id = owner.call("templates", "GET", "/api/templates").json()["templates"][0]["id"]

    with Session(engine) as session:
        done = make_meeting(session, owner_id, template_id, "abc-defg-hij", vexa_recording_id=77)
        live = make_meeting(
            session,
            owner_id,
            template_id,
            "live-meet-one",
            status="bot_sent",
            step="waiting",
            transcript=None,
            speaker_transcript=None,
            notes=None,
        )
        writing = make_meeting(
            session,
            owner_id,
            template_id,
            "writing-one",
            status="processing",
            step="notes",
            transcript=None,
            speaker_transcript=None,
            notes=None,
        )
        done_id, live_id, writing_id = done.id, live.id, writing.id
        session.add_all(
            [
                MeetingTranslation(
                    meeting_id=done_id,
                    language="fr",
                    status="ready",
                    notes_md="Notes",
                    transcript="Texte",
                    source_hash="x",
                    model="m",
                ),
                Decision(meeting_id=done_id, text="Ship on Friday.", position=0, fingerprint="d"),
                OpenQuestion(meeting_id=done_id, text="Who owns QA?", position=0, fingerprint="q"),
                ReviewItem(
                    meeting_id=done_id,
                    owner_user_id=owner_id,
                    text="Check the name.",
                    position=0,
                    fingerprint="r",
                ),
                Notification(
                    user_id=owner_id,
                    kind="notes_ready",
                    title="Your notes are ready",
                    meeting_id=done_id,
                    dedupe_key="notes-ready-done",
                ),
                AiRun(meeting_id=done_id, user_id=owner_id, kind="translate", model="m"),
                UsageEntry(
                    user_id=owner_id,
                    email_hash="placeholder-hash",
                    meeting_id=done_id,
                    seconds=1800,
                    counted_at=datetime.now(UTC),
                ),
                MailOutbox(
                    to_email=base.OWNER_EMAIL,
                    template="notes_ready",
                    payload={"meeting_id": done_id},
                ),
            ]
        )
        session.commit()
    note_file(
        "rows-before", "Everything that hangs off the finished meeting.", counts(engine, done_id)
    )

    owner.call(
        "delete-without-csrf",
        "DELETE",
        f"/api/meetings/{done_id}",
        csrf=None,
        note="Like every write: no token, no delete (403 csrf_rejected).",
    )
    owner.call(
        "delete-while-bot-in-call",
        "DELETE",
        f"/api/meetings/{live_id}",
        note="The bot is in the call (`bot_sent`): 409 meeting_active. Cancel it first.",
    )
    owner.call(
        "delete-while-writing",
        "DELETE",
        f"/api/meetings/{writing_id}",
        note="The notes are being written (`processing`): the same 409.",
    )
    vexa.fail = True
    owner.call(
        "delete-vexa-down",
        "DELETE",
        f"/api/meetings/{done_id}",
        note="Vexa would not remove the recording: 502, and nothing was deleted.",
    )
    note_file("rows-after-failed-delete", "Nothing changed.", counts(engine, done_id))
    vexa.fail = False
    owner.call("delete", "DELETE", f"/api/meetings/{done_id}", note="Deleted: 204, no body.")
    note_file(
        "rows-after",
        "The meeting and its derived rows are gone; usage seconds and the "
        "AI-run row stay, with no link to the meeting. Vexa was asked to delete recording "
        f"{getattr(vexa, 'deleted', None)}.",
        counts(engine, done_id),
    )
    owner.call("get-after", "GET", f"/api/meetings/{done_id}", note="Gone: 404 not_found.")
    owner.call(
        "delete-again",
        "DELETE",
        f"/api/meetings/{done_id}",
        note="A second delete (double click, another tab): 404, never 500.",
    )
    owner.call(
        "meetings-after", "GET", "/api/meetings", note="The list: the two active ones remain."
    )
    owner.call(
        "cancel-live",
        "POST",
        f"/api/meetings/{live_id}/cancel",
        note="After cancelling the bot's meeting ...",
    )
    owner.call(
        "delete-cancelled",
        "DELETE",
        f"/api/meetings/{live_id}",
        note="... it is `failed`, and can be deleted: 204.",
    )

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
        f"/api/meetings/{writing_id}",
        note="Another person's meeting: 404 not_found, the same as one that does not exist.",
    )


if __name__ == "__main__":
    main()
