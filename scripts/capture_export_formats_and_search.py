"""Capture the Markdown/text export, the Tasks CSV and the Meetings search over real HTTP.

    PYTHONPATH=. python scripts/capture_export_formats_and_search.py \
        postgresql+psycopg://USER:PASS@HOST:PORT/postgres

Same method as `capture_export_api.py`: a scratch database with the real migrations, the real app
under uvicorn on a free local port, only the bot provider and OpenAI faked (not used here), httpx
for every call. Each exchange goes to `docs/fe-captures/export-formats-and-search/NN-name.json`.
A text file the server sends is recorded with its full text (`body_text`: the notes are
placeholder words, so it is safe to commit), its headers and whether it starts with a byte order
mark; JSON bodies are recorded as sent.

One thing cannot be provoked from outside: "a PDF is being built". The capture holds the export
slot inside this process (the app and the script share one process), and the record says so. The
answers the client gets are the real ones.
"""

import json
import os
import shutil
import socket
import sys
import threading
import time
from datetime import UTC, date, datetime
from pathlib import Path
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "docs" / "fe-captures" / "export-formats-and-search"
DB_NAME = "notetaker_capture_efs"
OWNER_EMAIL = "owner@example.test"  # the admin account migration 0006 creates (a placeholder)
PASSWORD = "-".join(["capture", "password", "placeholder"])
PILOT_EMAIL = "pilot@example.test"
PILOT_PASSWORD = "-".join(["pilot", "password", "placeholder"])
MEET = "https://meet.google.com/abc-defg-hij"


def database_urls(admin_url: str) -> tuple[str, str]:
    parsed = urlparse(admin_url)
    return admin_url, parsed._replace(path=f"/{DB_NAME}").geturl()


admin_url, scratch_url = database_urls(sys.argv[1] if len(sys.argv) > 1 else "")
os.environ["DATABASE_URL"] = scratch_url
os.environ["OWNER_EMAIL"] = OWNER_EMAIL
os.environ["OWNER_PASSWORD"] = PASSWORD

import httpx  # noqa: E402
import uvicorn  # noqa: E402
from alembic import command  # noqa: E402
from alembic.config import Config  # noqa: E402
from sqlalchemy import create_engine, select, text  # noqa: E402
from sqlalchemy.orm import Session  # noqa: E402

from app import export, teams, translation  # noqa: E402
from app.accounts import create_user_defaults  # noqa: E402
from app.config import Settings  # noqa: E402
from app.main import create_app  # noqa: E402
from app.models import (  # noqa: E402
    Meeting,
    MeetingTranslation,
    Project,
    Sprint,
    Task,
    Template,
    User,
)
from app.passwords import hash_password  # noqa: E402

NOTES = "\n\n".join(
    [
        "## Summary\nThe team reviewed the **pricing** for the pilot and agreed the launch stays "
        "on 8 December. Ada Example will confirm the *final copy* with marketing.",
        "## Key points\n- Checkout is the main blocker\n  - Payment provider sandbox is slow\n"
        "- Marketing needs the final copy by Friday",
        "## Decisions\n- Ship the portal on 8 December",
        "## Action items\n- Ada Example: finish payment checkout (before Friday)",
        "## Open questions\n- Who owns the invoice template?",
    ]
)
SPEAKER_TRANSCRIPT = (
    "[Ada Example]: Hello everyone, thanks for joining. The pricing is still on for "
    "8 December.\n"
    "[Ben Sample]: Good. Checkout is the blocker.\n"
    "I will send the copy to marketing today.\n"
    "[Ada Example]: I will finish payment checkout before Friday."
)
FRENCH_NOTES = "## Résumé\nL'équipe a **confirmé** la date de lancement."
FRENCH_TRANSCRIPT = "[Ada Example]: Bonjour à tous."


class Recorder:
    count = 0

    def __init__(self, base_url: str) -> None:
        self.http = httpx.Client(base_url=base_url, follow_redirects=False, timeout=60)
        self.csrf: str | None = None
        self.masks: dict[str, str] = {}

    def mask(self, token: str) -> str:
        return self.masks.setdefault(token, f"<csrf-{chr(ord('A') + len(self.masks))}>")

    def call(self, name, method, path, *, body=None, headers=None, note=""):
        sent = dict(headers or {})
        if self.csrf and method != "GET":
            sent["X-CSRF-Token"] = self.csrf
        had_cookie = "session" in self.http.cookies
        response = self.http.request(method, path, json=body, headers=sent)
        kind = response.headers.get("content-type", "")
        record_body: dict = {}
        if "json" in kind:
            data = response.json()
            if isinstance(data, dict) and "csrf_token" in data:
                self.csrf = data["csrf_token"]
                data = {**data, "csrf_token": self.mask(data["csrf_token"])}
            record_body = {"body": data}
        else:
            raw = response.content
            record_body = {
                "starts_with_utf8_bom": raw.startswith(b"\xef\xbb\xbf"),
                "body_text": raw.decode("utf-8-sig"),
            }
        Recorder.count += 1
        shown = {k: self.mask(v) if k == "X-CSRF-Token" else v for k, v in sent.items()}
        if had_cookie:
            shown["Cookie"] = "session=<cookie>"
        kept = {
            key: response.headers[key]
            for key in (
                "content-type",
                "content-disposition",
                "cache-control",
                "x-content-type-options",
                "retry-after",
            )
            if key in response.headers
        }
        record = {
            "note": note,
            "request": {"method": method, "path": path, "headers": shown, "body": body},
            "response": {"status": response.status_code, "headers": kept, **record_body},
        }
        file = OUT / f"{Recorder.count:02d}-{name}.json"
        file.write_text(json.dumps(record, indent=2, ensure_ascii=False) + "\n")
        print(f"{file.name:52} {method:4} {path:60} -> {response.status_code}")
        return response


def start_server(app) -> tuple[uvicorn.Server, str]:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning"))
    threading.Thread(target=server.run, daemon=True).start()
    while not server.started:
        time.sleep(0.05)
    return server, f"http://127.0.0.1:{port}"


def prepare_database() -> None:
    admin = create_engine(admin_url, isolation_level="AUTOCOMMIT")
    with admin.connect() as connection:
        connection.execute(text(f"drop database if exists {DB_NAME} with (force)"))
        connection.execute(text(f"create database {DB_NAME}"))
    admin.dispose()
    command.upgrade(Config(str(ROOT / "alembic.ini")), "head")


def seed(engine) -> dict[str, int]:
    with Session(engine) as session:
        owner = session.scalars(select(User).where(User.email == OWNER_EMAIL)).one()
        owner.name = "Ada Example"
        general = session.scalars(
            select(Template).where(Template.owner_user_id == owner.id, Template.name == "General")
        ).one()
        pilot = User(
            email=PILOT_EMAIL,
            name="Pilot Example",
            password_hash=hash_password(PILOT_PASSWORD),
            email_verified_at=datetime.now(UTC),
        )
        session.add(pilot)
        session.commit()
        create_user_defaults(session, pilot)
        pilot_general = session.scalars(
            select(Template).where(Template.owner_user_id == pilot.id, Template.name == "General")
        ).one()
        team = teams.ensure_team(session, owner)
        pilot_team = teams.ensure_team(session, pilot)

        def meeting(owner_id, template_id, **fields):
            base = {
                "owner_user_id": owner_id,
                "url": MEET,
                "platform": "google_meet",
                "native_id": "abc-defg-hij",
                "template_id": template_id,
            }
            row = Meeting(**{**base, **fields})
            session.add(row)
            session.flush()
            return row

        ready = {"status": "ready", "step": "done"}

        def when(day: int) -> datetime:
            return datetime(2026, 11, day, 14, 0, tzinfo=UTC)

        main = meeting(
            owner.id,
            general.id,
            **ready,
            title="Weekly pricing sync",
            notes=NOTES,
            transcript="plain",
            speaker_transcript=SPEAKER_TRANSCRIPT,
            recorded_seconds=4350,
            created_at=when(3),
        )
        stale = meeting(
            owner.id,
            general.id,
            **ready,
            title="Stale translation",
            notes=NOTES,
            speaker_transcript=SPEAKER_TRANSCRIPT,
            created_at=when(4),
        )
        processing = meeting(
            owner.id,
            general.id,
            status="processing",
            step="notes",
            title="Pricing call in progress",
            transcript="we talked about hedgehogs",
            created_at=when(5),
        )
        titled_only = meeting(
            owner.id,
            general.id,
            **ready,
            title="Q4 roadmap",
            notes="## Summary\nNone noted",
            created_at=when(6),
        )
        transcript_only = meeting(
            owner.id,
            general.id,
            **ready,
            title="Engineering catch-up",
            notes="## Summary\nNone noted",
            speaker_transcript="[Ben Sample]: let us refactor the ingestion pipeline",
            created_at=when(9),
        )
        meeting(
            owner.id,
            general.id,
            **ready,
            title="Pricing review 2",
            notes=NOTES,
            speaker_transcript=SPEAKER_TRANSCRIPT,
            created_at=when(10),
        )
        theirs = meeting(
            pilot.id,
            pilot_general.id,
            **ready,
            title="Pilot's private pricing meeting",
            notes=NOTES,
            speaker_transcript=SPEAKER_TRANSCRIPT,
            created_at=when(11),
        )
        for row, notes_hash in (
            (main, translation.source_hash(main.notes, translation.transcript_of(main))),
            (stale, "stale-hash"),
        ):
            session.add(
                MeetingTranslation(
                    meeting_id=row.id,
                    language="fr",
                    status="ready",
                    notes_md=FRENCH_NOTES,
                    transcript=FRENCH_TRANSCRIPT,
                    source_hash=notes_hash,
                    model="capture",
                )
            )
        project = Project(team_id=team.id, name="Client Portal", color="#0E6B5E")
        session.add(project)
        session.flush()
        sprint = Sprint(
            team_id=team.id,
            number=4,
            start_date=date(2026, 11, 2),
            end_date=date(2026, 11, 15),
        )
        session.add(sprint)
        session.flush()
        tasks = [
            ("Finish payment checkout", "doing", main.id, owner.id, date(2026, 11, 20), True),
            ("Order the launch cake", "todo", None, None, None, False),
            ('=HYPERLINK("http://example.test","Click me")', "todo", main.id, None, None, False),
            ("@SUM(1+1)", "done", None, owner.id, date(2026, 11, 1), False),
            ("Send the copy, with a comma", "todo", main.id, owner.id, None, False),
        ]
        for title, status, meeting_id, assignee, due, filed in tasks:
            session.add(
                Task(
                    team_id=team.id,
                    meeting_id=meeting_id,
                    title=title,
                    status=status,
                    assignee_user_id=assignee,
                    due_date=due,
                    project_id=project.id if filed else None,
                    sprint_id=sprint.id if filed else None,
                    source="ai" if meeting_id else "manual",
                    ai_fingerprint=f"fp-{title}" if meeting_id else None,
                    assigned_on=date(2026, 11, 3),
                )
            )
        session.add(Task(team_id=pilot_team.id, title="Pilot's own task", source="manual"))
        session.commit()
        return {
            "main": main.id,
            "stale": stale.id,
            "processing": processing.id,
            "titled_only": titled_only.id,
            "transcript_only": transcript_only.id,
            "theirs": theirs.id,
            "team": team.id,
            "pilot_team": pilot_team.id,
        }


def main() -> None:
    if not sys.argv[1:]:
        sys.exit(__doc__)
    prepare_database()
    shutil.rmtree(OUT, ignore_errors=True)
    OUT.mkdir(parents=True)
    settings = Settings(
        _env_file=None,
        app_env="test",
        session_secret="capture-secret-" + "x" * 30,
        cookie_secure=False,
        database_url=scratch_url,
    )
    app = create_app(settings)
    server, base = start_server(app)
    engine = create_engine(scratch_url)
    try:
        run(base, seed(engine))
    finally:
        server.should_exit = True
        engine.dispose()


def run(base: str, ids: dict[str, int]) -> None:
    owner = Recorder(base)
    owner.call("session-anonymous", "GET", "/api/session")
    owner.call(
        "login-ok",
        "POST",
        "/api/session",
        body={"email": OWNER_EMAIL, "password": PASSWORD},
        note="The login every call below needs. GETs send no CSRF token.",
    )
    path = f"/api/meetings/{ids['main']}/export"
    # ---- Markdown and text ----
    owner.call("export-md", "GET", f"{path}?format=md", note="Notes only, as Markdown.")
    owner.call("export-md-transcript", "GET", f"{path}?format=md&transcript=true")
    owner.call("export-txt", "GET", f"{path}?format=txt", note="Notes only, as plain text.")
    owner.call("export-txt-transcript", "GET", f"{path}?format=txt&transcript=true")
    owner.call(
        "export-md-french",
        "GET",
        f"{path}?format=md&language=fr&transcript=true",
        note="The finished French translation of this meeting.",
    )
    owner.call("export-txt-french", "GET", f"{path}?format=txt&language=fr")
    owner.call(
        "export-md-spanish-missing",
        "GET",
        f"{path}?format=md&language=es",
        note="No Spanish translation: 409 `translation_missing`.",
    )
    owner.call(
        "export-txt-french-stale",
        "GET",
        f"/api/meetings/{ids['stale']}/export?format=txt&language=fr",
        note="A French translation of notes that have changed since: 409 `translation_missing`.",
    )
    owner.call(
        "export-md-not-ready",
        "GET",
        f"/api/meetings/{ids['processing']}/export?format=md",
        note="Notes not written yet: 409 `not_ready`.",
    )
    owner.call(
        "export-md-of-another-users-meeting",
        "GET",
        f"/api/meetings/{ids['theirs']}/export?format=md",
        note="Someone else's meeting is a 404, never a 403.",
    )
    owner.call("export-md-unknown-meeting", "GET", "/api/meetings/999999/export?format=txt")
    owner.call("export-unknown-format", "GET", f"{path}?format=rtf")
    # While a PDF or Word file is "being built" (the export slot held inside this process), md and
    # txt are still answered; pdf is the 503.
    export._one_at_a_time.acquire()
    try:
        owner.call(
            "export-md-while-pdf-busy",
            "GET",
            f"{path}?format=md",
            note="The export slot was held inside this process (a PDF being built): md answers "
            "200 all the same, because it never uses the slot.",
        )
        owner.call(
            "export-pdf-while-busy",
            "GET",
            f"{path}?format=pdf",
            note="Same moment: PDF and Word still answer 503 `export_busy`.",
        )
    finally:
        export._one_at_a_time.release()
    # ---- Tasks CSV ----
    owner.call(
        "tasks-csv",
        "GET",
        "/api/tasks/export?format=csv",
        headers={"X-Team-Id": str(ids["team"])},
        note="The active team's tasks, newest first. The titles that begin with = and @ have a "
        "single quote in front; the one with a comma and the one with quotes are quoted CSV.",
    )
    owner.call("tasks-csv-default-team", "GET", "/api/tasks/export", note="No team header.")
    owner.call(
        "tasks-csv-not-my-team",
        "GET",
        "/api/tasks/export",
        headers={"X-Team-Id": str(ids["pilot_team"])},
        note="A team the caller is not in: 404.",
    )
    owner.call("tasks-csv-unknown-format", "GET", "/api/tasks/export?format=xlsx")
    Recorder(base).call("tasks-csv-not-logged-in", "GET", "/api/tasks/export")
    # ---- Meetings search ----
    team = {"X-Team-Id": str(ids["team"])}
    owner.call("meetings-all", "GET", "/api/meetings?limit=50", headers=team, note="No search.")
    owner.call(
        "meetings-q-notes",
        "GET",
        "/api/meetings?q=pricing",
        headers=team,
        note="'pricing' is in the notes and transcript of meetings 1, 2 and 6, and in the title "
        "of 3 (still processing, so matched on its title only) and 6. The pilot's meeting 7, "
        "which has the same words, is not there.",
    )
    owner.call(
        "meetings-q-title",
        "GET",
        "/api/meetings?q=ROADMAP",
        headers=team,
        note="A title match ignores case.",
    )
    owner.call(
        "meetings-q-transcript",
        "GET",
        "/api/meetings?q=ingestion%20pipeline",
        headers=team,
        note="Both words are in the transcript only.",
    )
    owner.call(
        "meetings-q-not-ready-body",
        "GET",
        "/api/meetings?q=hedgehogs",
        headers=team,
        note="A meeting that is not ready is matched on its title only: its transcript is not "
        "searched, so nothing is found.",
    )
    owner.call("meetings-q-no-match", "GET", "/api/meetings?q=zebra", headers=team)
    first = owner.call(
        "meetings-q-page-1", "GET", "/api/meetings?q=pricing&limit=2", headers=team
    ).json()
    owner.call(
        "meetings-q-page-2",
        "GET",
        f"/api/meetings?q=pricing&limit=2&before_id={first['next_before_id']}",
        headers=team,
        note="`next_before_id` of page 1, with the same `q`.",
    )
    owner.call(
        "meetings-q-with-dates",
        "GET",
        "/api/meetings?q=pricing&from=2026-11-09&to=2026-11-10",
        headers=team,
        note="The date filter and the search together.",
    )
    owner.call(
        "meetings-q-literal-percent",
        "GET",
        "/api/meetings?q=%25%25",
        headers=team,
        note="A typed % is a % and not 'anything'.",
    )
    for label, value in (
        ("one-char", "a"),
        ("empty", ""),
        ("blank", "%20%20"),
        ("long", "x" * 101),
    ):
        owner.call(
            f"meetings-q-invalid-{label}",
            "GET",
            f"/api/meetings?q={value}",
            headers=team,
            note="422 `invalid_query`.",
        )
    Recorder(base).call("meetings-q-not-logged-in", "GET", "/api/meetings?q=pricing")


if __name__ == "__main__":
    main()
