"""Drive the export endpoint over real HTTP and save every exchange for the frontend guide.

    python scripts/capture_export_api.py postgresql+psycopg://USER:PASS@HOST:PORT/postgres

Same method as `capture_owner_api.py`: a scratch database with the real migrations, the real app
under uvicorn on a free local port, only Vexa and OpenAI faked, httpx for every call. Each
exchange goes to `docs/fe-captures/export/NN-name.json`; a file the server sends is saved under
`files/` (they hold only the placeholder text below, so they are safe to commit and open in
Word and a PDF viewer), and the JSON records its headers, size and first bytes.

Two failures cannot be provoked from outside, so they are provoked inside this process and the
capture says so in its `note`: the time limit (set to a fraction of a second, so the real child
process is really killed) and a renderer whose PDF library cannot load (so the real child really
fails). The answer the client gets is the real one in both cases.

It also times the endpoint for a one-hour, three-hour and 40,000-word transcript and writes
`timings.json`, with the renderer's own peak memory read from the app's log line. Run it inside
the app's image to get Linux numbers (docs/spike/export-pdf-memory.md).
"""

import hashlib
import json
import logging
import os
import re
import shutil
import socket
import sys
import tempfile
import threading
import time
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "docs" / "fe-captures" / "export"
DB_NAME = "notetaker_capture_export"
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

from app import export  # noqa: E402
from app.accounts import create_user_defaults  # noqa: E402
from app.config import Settings  # noqa: E402
from app.main import create_app  # noqa: E402
from app.models import Meeting, Template, User  # noqa: E402
from app.passwords import hash_password  # noqa: E402

NOTES = "\n\n".join(
    [
        "## Summary\nThe team reviewed the **portal** timeline and agreed the launch stays on "
        "8 December. Ada Example will confirm the *final copy* with marketing.",
        "## Key points\n- Checkout is the main blocker\n  - Payment provider sandbox is slow\n"
        "- Marketing needs the final copy by Friday\n- Support rota starts Monday",
        "## Decisions\n- Ship the portal on 8 December\n- Hire one more engineer",
        "## Action items\n- Ada Example: finish payment checkout (before Friday)\n"
        "- Ben Sample: send the copy to marketing",
        "## Open questions\n- Who owns the invoice template?",
        '## Please check\n- Heard "Metrika"; may be Metrica',
    ]
)
SPEAKER_TRANSCRIPT = (
    "[Ada Example]: Hello everyone, thanks for joining. The portal launch is still on for "
    "8 December.\n"
    "[Ben Sample]: Good. Checkout is the blocker. I will send the copy to marketing today.\n"
    "[Ada Example]: I will finish payment checkout before Friday. Who owns the invoice template?"
)
PLAIN_TRANSCRIPT = (
    "Hello everyone, thanks for joining. The portal launch is still on for 8 December. "
    "Good. Checkout is the blocker. I will send the copy to marketing today."
)
WORDS = (
    "the project budget timeline client portal payment checkout design review launch release "
    "customer feedback priority risk blocker estimate sprint backlog update deadline Friday "
    "Monday decision approve schedule meeting notes action owner marketing engineering "
    "revenue forecast quarter hiring contract invoice vendor security privacy"
).split()


def long_transcript(words: int) -> str:
    """Speaker-labelled turns of 40 to 120 words, like the pipeline's output."""
    import random

    rng = random.Random(7)
    names = ["Ada Example", "Ben Sample", "Kemi Adeyemi"]
    turns, used = [], 0
    while used < words:
        n = min(rng.randint(40, 120), words - used)
        turns.append(f"[{rng.choice(names)}]: " + " ".join(rng.choice(WORDS) for _ in range(n)))
        used += n
    return "\n".join(turns)


class RenderLog(logging.Handler):
    """Collects the app's `export:` log lines, which carry the renderer's peak memory."""

    def __init__(self) -> None:
        super().__init__()
        self.lines: list[str] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.lines.append(record.getMessage())


class Recorder:
    count = 0

    def __init__(self, base_url: str) -> None:
        self.http = httpx.Client(base_url=base_url, follow_redirects=False, timeout=120)
        self.csrf: str | None = None
        self.masks: dict[str, str] = {}

    def mask(self, token: str) -> str:
        return self.masks.setdefault(token, f"<csrf-{chr(ord('A') + len(self.masks))}>")

    def call(self, name, method, path, *, body=None, note=""):
        headers = {"X-CSRF-Token": self.csrf} if self.csrf and method != "GET" else {}
        had_cookie = "session" in self.http.cookies
        response = self.http.request(method, path, json=body, headers=headers)
        kind = response.headers.get("content-type", "")
        file_info = None
        if "json" in kind:
            data = response.json()
            if isinstance(data, dict) and "csrf_token" in data:
                self.csrf = data["csrf_token"]
        else:
            data = None
            file_info = self._save_file(name, response)
        self._save(name, method, path, headers, body, had_cookie, response, data, file_info, note)
        return response

    def _save_file(self, name: str, response: httpx.Response) -> dict:
        content = response.content
        extension = "pdf" if content[:4] == b"%PDF" else "docx"
        target = OUT / "files" / f"{Recorder.count + 1:02d}-{name}.{extension}"
        target.parent.mkdir(exist_ok=True)
        target.write_bytes(content)
        return {
            "saved_as": f"files/{target.name}",
            "bytes": len(content),
            "first_bytes": content[:8].decode("latin-1"),
            "sha256": hashlib.sha256(content).hexdigest(),
        }

    def _save(self, name, method, path, headers, body, had_cookie, response, data, file_info, note):
        Recorder.count += 1
        sent = {k: self.mask(v) if k == "X-CSRF-Token" else v for k, v in headers.items()}
        if had_cookie:
            sent["Cookie"] = "session=<cookie>"
        kept = {}
        for key in (
            "content-type",
            "content-disposition",
            "content-length",
            "cache-control",
            "x-content-type-options",
            "retry-after",
            "allow",
        ):
            if key in response.headers:
                kept[key] = response.headers[key]
        shown = json.loads(json.dumps(data))
        if isinstance(shown, dict) and "csrf_token" in shown:
            shown["csrf_token"] = self.mask(shown["csrf_token"])
        response_record = {"status": response.status_code, "headers": kept}
        if file_info:
            response_record["file"] = file_info
        else:
            response_record["body"] = shown
        record = {
            "note": note,
            "request": {"method": method, "path": path, "headers": sent, "body": body},
            "response": response_record,
        }
        file = OUT / f"{Recorder.count:02d}-{name}.json"
        file.write_text(json.dumps(record, indent=2, ensure_ascii=False) + "\n")
        print(f"{file.name:48} {method:4} {path:55} -> {response.status_code}")


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
            return row.id

        ready = {"status": "ready", "step": "done", "notes": NOTES}
        ids = {
            "ready": meeting(
                owner.id,
                general.id,
                **ready,
                transcript=PLAIN_TRANSCRIPT,
                speaker_transcript=SPEAKER_TRANSCRIPT,
                recorded_seconds=4350,
            ),
            "ready_plain": meeting(
                owner.id, general.id, **ready, transcript=PLAIN_TRANSCRIPT, recorded_seconds=1800
            ),
            "processing": meeting(owner.id, general.id, status="processing", step="notes"),
            "failed": meeting(
                owner.id, general.id, status="failed", step="audio", failure_reason="Placeholder."
            ),
            "theirs": meeting(
                pilot.id, pilot_general.id, **ready, speaker_transcript=SPEAKER_TRANSCRIPT
            ),
        }
        for key, words in (("hour", 9000), ("three_hours", 27000), ("forty_thousand", 40000)):
            text_ = long_transcript(words)
            ids[key] = meeting(
                owner.id,
                general.id,
                **ready,
                transcript=text_,
                speaker_transcript=text_,
                recorded_seconds=words * 60 // 150,  # 150 words a minute
            )
        session.commit()
    return ids


def peak_mib(lines_before: int, log: RenderLog) -> int | None:
    for line in log.lines[lines_before:]:
        found = re.search(r"renderer peak (\d+) MiB", line)
        if found:
            return int(found.group(1))
    return None


def main() -> None:
    if not sys.argv[1:]:
        sys.exit(__doc__)
    prepare_database()
    shutil.rmtree(OUT, ignore_errors=True)
    OUT.mkdir(parents=True)
    log = RenderLog()
    logging.getLogger("app.export").addHandler(log)
    logging.getLogger("app.export").setLevel(logging.INFO)
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
        run(base, seed(engine), log)
    finally:
        server.should_exit = True
        engine.dispose()


def run(base: str, ids: dict[str, int], log: RenderLog) -> None:
    anonymous = Recorder(base)
    anonymous.call("session-anonymous", "GET", "/api/session")
    anonymous.call(
        "export-not-logged-in",
        "GET",
        f"/api/meetings/{ids['ready']}/export?format=pdf",
        note="No login: 401 JSON, as for every route under /api.",
    )

    owner = Recorder(base)
    owner.call("session-owner-anonymous", "GET", "/api/session")
    owner.call(
        "login-ok",
        "POST",
        "/api/session",
        body={"email": OWNER_EMAIL, "password": PASSWORD},
        note="The login every export needs. A GET export sends no CSRF token.",
    )
    path = f"/api/meetings/{ids['ready']}/export"
    owner.call(
        "export-pdf-default",
        "GET",
        path,
        note="No query at all: a PDF of the notes, no transcript.",
    )
    owner.call("export-pdf-transcript", "GET", f"{path}?format=pdf&transcript=true")
    owner.call("export-docx", "GET", f"{path}?format=docx")
    owner.call("export-docx-transcript", "GET", f"{path}?format=docx&transcript=true")
    owner.call(
        "export-docx-transcript-unlabelled",
        "GET",
        f"/api/meetings/{ids['ready_plain']}/export?format=docx&transcript=true",
        note="A meeting with no speaker-labelled transcript: the plain one is exported instead.",
    )
    owner.call(
        "export-not-ready-processing",
        "GET",
        f"/api/meetings/{ids['processing']}/export?format=pdf",
        note="The notes are not written yet: 409 `not_ready`.",
    )
    owner.call(
        "export-not-ready-failed",
        "GET",
        f"/api/meetings/{ids['failed']}/export?format=pdf",
        note="A failed meeting has no notes either: the same 409.",
    )
    owner.call(
        "export-of-another-users-meeting",
        "GET",
        f"/api/meetings/{ids['theirs']}/export?format=pdf",
        note="Someone else's meeting is a 404, never a 403.",
    )
    owner.call("export-unknown-meeting", "GET", "/api/meetings/999999/export?format=pdf")
    owner.call("export-unknown-format", "GET", f"{path}?format=odt")
    owner.call("export-bad-transcript-flag", "GET", f"{path}?format=pdf&transcript=maybe")

    # A second export while one runs: the first is the 40,000-word PDF, which takes seconds.
    big = f"/api/meetings/{ids['forty_thousand']}/export?format=pdf&transcript=true"
    background = httpx.Client(base_url=base, timeout=120, cookies=owner.http.cookies)
    first = threading.Thread(target=lambda: background.get(big))
    first.start()
    time.sleep(0.6)
    owner.call(
        "export-busy",
        "GET",
        f"{path}?format=docx",
        note="Sent while another file was being built (the 40,000-word PDF, started 0.6 s "
        "earlier): 503 `export_busy` with `Retry-After`.",
    )
    first.join()

    real_limit = export.TIME_LIMIT_SECONDS
    export.TIME_LIMIT_SECONDS = 0.2
    owner.call(
        "export-timeout",
        "GET",
        big,
        note="PROVOKED IN-PROCESS: the time limit was set to 0.2 s for this one call (it is 45 s "
        "in the app), so the real child process was really killed. The answer is the real one.",
    )
    export.TIME_LIMIT_SECONDS = real_limit
    real_env = export._child_env
    with tempfile.TemporaryDirectory() as broken:
        # A renderer whose PDF library cannot load, which is what a missing Pango looks like.
        (Path(broken) / "weasyprint").mkdir()
        (Path(broken) / "weasyprint" / "__init__.py").write_text(
            'raise OSError("cannot load library libpango-1.0-0")\n'
        )
        export._child_env = lambda: {**real_env(), "PYTHONPATH": broken}
        owner.call(
            "export-failed",
            "GET",
            f"{path}?format=pdf",
            note="PROVOKED IN-PROCESS: the renderer's PDF library was made unloadable (as a "
            "missing Pango would), so the real child process really exited with an error. "
            "The answer is the real one; the message says nothing of the cause.",
        )
        export._child_env = real_env

    timings = []
    for key, words in (("hour", 9000), ("three_hours", 27000), ("forty_thousand", 40000)):
        for fmt in ("pdf", "docx"):
            for transcript in (False, True):
                flag = str(transcript).lower()
                url = f"/api/meetings/{ids[key]}/export?format={fmt}&transcript={flag}"
                before = len(log.lines)
                started = time.monotonic()
                response = owner.http.get(url)
                seconds = time.monotonic() - started
                assert response.status_code == 200, (url, response.status_code)
                timings.append(
                    {
                        "transcript_words": words,
                        "format": fmt,
                        "with_transcript": transcript,
                        "seconds": round(seconds, 1),
                        "bytes": len(response.content),
                        "renderer_peak_mib": peak_mib(before, log),
                    }
                )
                print(timings[-1])
    (OUT / "timings.json").write_text(json.dumps(timings, indent=2) + "\n")


if __name__ == "__main__":
    main()
