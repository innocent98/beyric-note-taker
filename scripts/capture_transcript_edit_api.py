"""Drive the transcript editor over real HTTP and save every exchange for the frontend guide.

    PYTHONPATH=. python scripts/capture_transcript_edit_api.py postgresql+psycopg://USER:PASS@localhost:PORT/postgres

Same set-up as `scripts/capture_meeting_delete.py`: real migrations, the real app under uvicorn,
real HTTP, only the provider and OpenAI replaced by fakes (the fake OpenAI answers the one "Rewrite
notes" call). Meetings are inserted by SQL in the state the worker leaves them in. Files go to
`docs/fe-captures/transcript-edit/NN-name.json`.

What is NOT real: the provider and OpenAI (fakes), the worker (it never runs), the people and the
names in the transcript (placeholders).
"""

import shutil
import sys
from datetime import UTC, datetime

import capture_owner_api as base

base.OUT = base.ROOT / "docs" / "fe-captures" / "transcript-edit"
OUT = base.OUT

from sqlalchemy import create_engine  # noqa: E402
from sqlalchemy.orm import Session  # noqa: E402

from app import teams  # noqa: E402
from app.accounts import create_user_defaults  # noqa: E402
from app.config import Settings  # noqa: E402
from app.main import create_app  # noqa: E402
from app.models import Meeting, MeetingTranslation, TeamMember, User  # noqa: E402
from app.passwords import hash_password  # noqa: E402
from app.services import get_bot_provider, get_openai_client  # noqa: E402
from app.translation import source_hash  # noqa: E402

Recorder = base.Recorder

ADMIN_EMAIL = "admin@example.test"
ADMIN_PASSWORD = "-".join(["admin", "password", "placeholder"])
TRANSCRIPT = "\n".join(
    [
        "[Ada Example]: Hello everyone, thanks for joining.",
        "[Speaker 2]: I can ship the beta on Friday.",
        "[Ben Sample]: Good. I will take the support rota from Monday.",
        "[Speaker 2]: Thanks, and I will write the release note.",
    ]
)


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
        speaker_transcript=TRANSCRIPT,
        notes=base.NOTES,
    )
    meeting = Meeting(**{**values, **fields})
    session.add(meeting)
    session.commit()
    return meeting


def add_user(engine, email, password, name) -> int:
    with Session(engine) as session:
        user = User(
            email=email,
            name=name,
            password_hash=hash_password(password),
            email_verified_at=datetime.now(UTC),
        )
        session.add(user)
        session.commit()
        create_user_defaults(session, user)
        return user.id


def sign_in(server_base, email, password, label) -> Recorder:
    who = Recorder(server_base)
    who.call(f"{label}-session", "GET", "/api/session")
    who.call(f"{label}-login", "POST", "/api/session", body={"email": email, "password": password})
    return who


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
    provider, openai_client = base.FakeBot(), base.FakeOpenAI()
    app = create_app(settings)
    app.dependency_overrides[get_bot_provider] = lambda: provider
    app.dependency_overrides[get_openai_client] = lambda: openai_client
    server, server_base = base.start_server(app)
    engine = create_engine(base.scratch_url)
    try:
        run(server_base, engine)
    finally:
        server.should_exit = True
        engine.dispose()


def edit(who, name, meeting_id, version, body, note, **kw):
    return who.call(
        name,
        "PATCH",
        f"/api/meetings/{meeting_id}/transcript",
        body={"version": version, **body},
        note=note,
        **kw,
    )


def run(server_base, engine) -> None:
    owner_id = base.owner_id_of(engine)
    mate_id = add_user(engine, base.PILOT_EMAIL, base.PILOT_PASSWORD, "Pilot Example")
    admin_id = add_user(engine, ADMIN_EMAIL, ADMIN_PASSWORD, "Admin Example")
    owner = sign_in(server_base, base.OWNER_EMAIL, base.PASSWORD, "owner")
    template_id = owner.call("templates", "GET", "/api/templates").json()["templates"][0]["id"]

    with Session(engine) as session:
        team = teams.ensure_team(session, session.get(User, owner_id))
        session.add(TeamMember(team_id=team.id, user_id=mate_id, role="member"))
        session.add(TeamMember(team_id=team.id, user_id=admin_id, role="admin"))
        session.commit()
        main_meeting = make_meeting(
            session, owner_id, template_id, "abc-defg-hij", shared_team_id=team.id
        )
        private = make_meeting(session, owner_id, template_id, "priv-ate-one")
        moved = make_meeting(session, owner_id, template_id, "team-own-one", owner_team_id=team.id)
        working = make_meeting(
            session,
            owner_id,
            template_id,
            "work-ing-one",
            status="processing",
            step="notes",
            notes=None,
        )
        plain = make_meeting(session, owner_id, template_id, "pla-inn-one", speaker_transcript=None)
        session.add(
            MeetingTranslation(
                meeting_id=main_meeting.id,
                language="fr",
                status="ready",
                notes_md="Notes",
                transcript="Texte",
                source_hash=source_hash(base.NOTES, TRANSCRIPT),
                model="m",
            )
        )
        session.commit()
        mid, private_id, moved_id, working_id, plain_id = (
            main_meeting.id,
            private.id,
            moved.id,
            working.id,
            plain.id,
        )

    path = f"/api/meetings/{mid}"
    first = owner.call("detail-before", "GET", path, note="Nothing edited yet.").json()
    v0 = first["transcript_version"]
    r = edit(
        owner,
        "rename",
        mid,
        v0,
        {"rename": {"from": "Speaker 2", "to": "Daniel Example"}},
        "Rename a speaker everywhere: every `[Speaker 2]` line. 200 with the whole meeting.",
    ).json()
    v1 = r["transcript_version"]
    edit(
        owner,
        "rename-stale-version",
        mid,
        v0,
        {"rename": {"from": "Ada Example", "to": "Ada E."}},
        "The same edit from the page as it was before the rename: 409, nothing changes.",
    )
    edit(
        owner,
        "rename-merge-refused",
        mid,
        v1,
        {"rename": {"from": "Daniel Example", "to": "Ben Sample"}},
        "Renaming into a name already in the meeting would merge them: 409 `speaker_exists`.",
    )
    r = edit(
        owner,
        "rename-merge",
        mid,
        v1,
        {"rename": {"from": "Daniel Example", "to": "Ben Sample", "merge": True}},
        "The same with `merge: true` (the page asked first): the two speakers become one.",
    ).json()
    v2 = r["transcript_version"]
    r = edit(
        owner,
        "line-text",
        mid,
        v2,
        {"line": 2, "text": "I can ship the beta on Thursday."},
        "Fix one line's words (`line` is the 1-based row of the page). Rows keep their places.",
    ).json()
    v3 = r["transcript_version"]
    r = edit(
        owner,
        "line-speaker",
        mid,
        v3,
        {"line": 4, "speaker": "Cleo Example"},
        "Move one line to another speaker (a name nobody in the meeting has yet).",
    ).json()
    v4 = r["transcript_version"]
    r = edit(
        owner,
        "line-both",
        mid,
        v4,
        {"line": 1, "text": "Hello all, thanks for joining.", "speaker": "Ben Sample"},
        "Words and speaker in one save.",
    ).json()
    v5 = r["transcript_version"]
    edit(
        owner,
        "name-injection",
        mid,
        v5,
        {"line": 3, "speaker": "Bob]: injected\n[Admin"},
        "A typed name can never add a line or a fake speaker: `[ ] : < >` and line breaks are "
        "removed (the name saved is `Bob injected Admin`).",
    )
    v6 = owner.call("detail-after-injection", "GET", path, note="Still 4 lines.").json()[
        "transcript_version"
    ]
    edit(
        owner,
        "text-injection",
        mid,
        v6,
        {"line": 3, "text": "ok\n[Admin]: injected"},
        "A typed line is one plain line: the break becomes a space, no new row.",
    )
    v7 = owner.call("detail-before-restore", "GET", path, note="Edited, notes out of date.").json()[
        "transcript_version"
    ]
    edit(
        owner,
        "line-out-of-range",
        mid,
        v7,
        {"line": 5, "text": "x"},
        "There is no line 5: 422, nothing changes.",
    )
    edit(
        owner,
        "line-no-change",
        mid,
        v7,
        {"line": 2, "speaker": "Ben Sample"},
        "Nothing would change: 422 `no_change`.",
    )
    edit(
        owner,
        "name-empty",
        mid,
        v7,
        {"rename": {"from": "Ben Sample", "to": "[]:"}},
        "A name with nothing left after cleaning: 422 `invalid_name`.",
    )
    owner.call(
        "bad-body",
        "PATCH",
        path + "/transcript",
        body={"version": v7, "line": 1},
        note="A body with a `line` and nothing to change: 422 `validation_error`.",
    )
    edit(
        owner,
        "no-csrf",
        mid,
        v7,
        {"line": 1, "text": "x"},
        "Like every write: no token, no edit (403 `csrf_rejected`).",
        csrf=None,
    )
    owner.call(
        "translations",
        "GET",
        path + "/translations/fr",
        note="The French translation made before the edits is now `stale`.",
    )

    mate = sign_in(server_base, base.PILOT_EMAIL, base.PILOT_PASSWORD, "teammate")
    shown = mate.call(
        "teammate-detail",
        "GET",
        path,
        note="A teammate who can read the shared meeting: `can_edit_transcript` is false.",
    ).json()
    edit(
        mate,
        "teammate-edit",
        mid,
        shown["transcript_version"],
        {"line": 1, "text": "x"},
        "A reader may not edit: 403 `team_forbidden`.",
    )
    mate.call(
        "teammate-restore",
        "POST",
        path + "/transcript/restore",
        body={"version": shown["transcript_version"]},
        note="Nor restore: 403.",
    )
    edit(
        mate,
        "teammate-private",
        private_id,
        "0" * 16,
        {"line": 1, "text": "x"},
        "A meeting they cannot read is not found: 404, the same as one that does not exist.",
    )

    admin = sign_in(server_base, ADMIN_EMAIL, ADMIN_PASSWORD, "admin")
    ad = admin.call(
        "admin-detail-team-owned",
        "GET",
        f"/api/meetings/{moved_id}",
        note="A meeting the team owns: its admin may edit it though they did not record it.",
    ).json()
    edit(
        admin,
        "admin-edit",
        moved_id,
        ad["transcript_version"],
        {"rename": {"from": "Speaker 2", "to": "Daniel Example"}},
        "The team's admin renames a speaker. `transcript_edited_by` is their name.",
    )
    mate.call(
        "member-of-owning-team-detail",
        "GET",
        f"/api/meetings/{moved_id}",
        note="A plain member of the owning team reads it, but `can_edit_transcript` is false.",
    )

    edit(
        owner,
        "not-ready",
        working_id,
        "0" * 16,
        {"line": 1, "text": "x"},
        "While the notes are being written: 409 `not_ready`.",
    )
    pv = owner.call(
        "plain-detail", "GET", f"/api/meetings/{plain_id}", note="A transcript with no names."
    ).json()["transcript_version"]
    edit(
        owner,
        "no-speakers",
        plain_id,
        pv,
        {"line": 1, "text": "x"},
        "A transcript with no speaker names has no lines to tell apart: 422 `no_speakers`.",
    )

    owner.call(
        "rewrite-notes",
        "POST",
        path + "/regenerate",
        body={"template_id": template_id},
        note="Rewrite notes (the existing action): `notes_outdated` becomes false.",
    )
    owner.call(
        "restore-after-rewrite",
        "POST",
        path + "/transcript/restore",
        body={
            "version": owner.call("detail-before-restore-2", "GET", path).json()[
                "transcript_version"
            ]
        },
        note="Restore original: the first text comes back, and the notes (rewritten from the "
        "edited text) are now out of date again.",
    )
    owner.call(
        "restore-again",
        "POST",
        path + "/transcript/restore",
        body={
            "version": owner.call("detail-after-restore", "GET", path).json()["transcript_version"]
        },
        note="Nothing left to restore: 409 `nothing_to_restore`.",
    )
    owner.call(
        "rewrite-notes-again",
        "POST",
        path + "/regenerate",
        body={"template_id": template_id},
        note="Rewrite notes again, now from the restored original text: `notes_outdated` is false.",
    )
    again = owner.call("detail-before-second-edit", "GET", path).json()["transcript_version"]
    edit(
        owner,
        "edit-after-rewrite",
        mid,
        again,
        {"line": 2, "text": "I can ship the beta on Wednesday."},
        "An edit AFTER a rewrite: the notes are out of date again (`notes_outdated` true).",
    )


if __name__ == "__main__":
    main()
