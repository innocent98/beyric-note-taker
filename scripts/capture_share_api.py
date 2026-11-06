"""Drive the Share-notes routes over real HTTP and save every exchange for the frontend guide.

    python scripts/capture_share_api.py postgresql+psycopg://USER:PASS@HOST:PORT/postgres

Same method as `capture_translate_api.py`: a scratch database with the real migrations, the real
app under uvicorn on a free local port, httpx for every call. What is faked, on purpose:

- the translating model answers from `tests/fixtures/translate_sample.json` (the designer's own
  French and Spanish); it says nothing about real translation quality;
- **no mail leaves this machine.** The script runs `mail.deliver_pending` itself, as the worker
  does, with a mailer that writes each message it is given to `mail-*.json` (subject, sender
  fields, text, html, attachment names and sizes) instead of calling Resend. So the mails are the
  real rendered ones; whether Resend accepts them (attachment size, daily quota, types) is NOT
  verified here.

The worker is not started: the script runs `translation.process_next` and `deliver_pending`
between calls. Each exchange goes to `docs/fe-captures/share/NN-name.json`.
"""

import json
import os
import re
import shutil
import socket
import sys
import threading
import time
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "docs" / "fe-captures" / "share"
SAMPLE = json.loads((ROOT / "tests" / "fixtures" / "translate_sample.json").read_text())
DB_NAME = "notetaker_capture_share"
OWNER_EMAIL = "owner@example.test"  # the admin account migration 0006 creates (a placeholder)
PASSWORD = "-".join(["capture", "password", "placeholder"])
PILOT_EMAIL = "pilot@example.test"
OUTSIDE = "client@example.test"
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
from sqlalchemy import create_engine, func, select, text  # noqa: E402
from sqlalchemy.orm import Session  # noqa: E402

from app import mail, translation  # noqa: E402
from app.accounts import create_user_defaults  # noqa: E402
from app.config import Settings  # noqa: E402
from app.main import create_app  # noqa: E402
from app.models import Meeting, MeetingShareRecipient, TeamMember, Template, User  # noqa: E402
from app.passwords import hash_password  # noqa: E402
from app.teams import ensure_team  # noqa: E402

NOTES = SAMPLE["original"]["notes"]
TRANSCRIPT = SAMPLE["original"]["transcript"]
TRANSLATIONS = {
    code: dict(
        zip(
            SAMPLE["units"]["original"]["transcript"],
            SAMPLE["units"][code]["transcript"],
            strict=True,
        )
    )
    | {SAMPLE["units"]["original"]["summary"]: SAMPLE["units"][code]["summary"]}
    | {SAMPLE["units"]["original"]["check"]: SAMPLE["units"][code]["check"]}
    | dict(zip(SAMPLE["units"]["original"]["key"], SAMPLE["units"][code]["key"], strict=True))
    | dict(
        zip(
            SAMPLE["units"]["original"]["decisions"],
            SAMPLE["units"][code]["decisions"],
            strict=True,
        )
    )
    | dict(
        zip(
            SAMPLE["units"]["original"]["questions"],
            SAMPLE["units"][code]["questions"],
            strict=True,
        )
    )
    for code in ("fr", "es")
}
CHANGED = "Automation and the app's effectiveness remain to be explored."
NEW_SENTENCE = "Automation, cost and the app's effectiveness remain to be explored."
LANGUAGE_OF = {"French": "fr", "Spanish": "es"}


class FakeTranslator:
    """Answers a translation call from the designer's own sentences (see the module note)."""

    def __init__(self) -> None:
        self.drift = False
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self._create))

    def _create(self, **kwargs):
        user = kwargs["messages"][1]["content"]
        language = LANGUAGE_OF[re.search(r"Translate into (\w+)", user).group(1)]
        texts = json.loads(re.search(r"<texts>\n(.*?)\n</texts>", user, re.DOTALL).group(1))
        out = [TRANSLATIONS[language].get(t, f"[{language}] {t}") for t in texts]
        if self.drift:
            out = out[:-1]
        content = json.dumps({"texts": out}, ensure_ascii=False)
        usage = SimpleNamespace(
            prompt_tokens=len(user) // 4 + 400, completion_tokens=len(content) // 4
        )
        return SimpleNamespace(
            choices=[
                SimpleNamespace(message=SimpleNamespace(content=content), finish_reason="stop")
            ],
            usage=usage,
        )


class FakeNotesModel:
    """The notes model for "Rewrite notes": the sample notes with one sentence changed."""

    def __init__(self) -> None:
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self._create))

    def _create(self, **kwargs):
        content = NOTES.replace(CHANGED, NEW_SENTENCE)
        return SimpleNamespace(
            choices=[
                SimpleNamespace(message=SimpleNamespace(content=content), finish_reason="stop")
            ],
            usage=SimpleNamespace(prompt_tokens=10, completion_tokens=10),
        )


class Recorder:
    count = 0

    def __init__(self, base_url: str) -> None:
        self.http = httpx.Client(base_url=base_url, follow_redirects=False, timeout=120)
        self.csrf: str | None = None
        self.masks: dict[str, str] = {}

    def mask(self, token: str) -> str:
        return self.masks.setdefault(token, f"<csrf-{chr(ord('A') + len(self.masks))}>")

    def call(self, name, method, path, *, body=None, note="", raw=None):
        headers = {"X-CSRF-Token": self.csrf} if self.csrf and method != "GET" else {}
        had_cookie = "session" in self.http.cookies
        if raw is not None:
            headers["Content-Type"] = "application/json"
        response = self.http.request(method, path, json=body, content=raw, headers=headers)
        kind = response.headers.get("content-type", "")
        file_info = None
        data = None
        if "json" in kind:
            data = response.json()
            if isinstance(data, dict) and "csrf_token" in data:
                self.csrf = data["csrf_token"]
        elif response.content:
            file_info = self._save_file(name, response)
        self._save(name, method, path, headers, body, had_cookie, response, data, file_info, note)
        return response

    def _save_file(self, name: str, response: httpx.Response) -> dict:
        target = OUT / "files" / f"{Recorder.count + 1:02d}-{name}.bin"
        target.parent.mkdir(exist_ok=True)
        target.write_bytes(response.content)
        return {"saved_as": f"files/{target.name}", "bytes": len(response.content)}

    def _save(self, name, method, path, headers, body, had_cookie, response, data, file_info, note):
        Recorder.count += 1
        if CapturingMailer.first_token and body is not None:
            body = json.loads(
                json.dumps(body).replace(CapturingMailer.first_token, "<opt-out-token>")
            )
        sent = {k: self.mask(v) if k == "X-CSRF-Token" else v for k, v in headers.items()}
        if had_cookie:
            sent["Cookie"] = "session=<cookie>"
        shown = json.loads(json.dumps(data))
        if isinstance(shown, dict) and "csrf_token" in shown:
            shown["csrf_token"] = self.mask(shown["csrf_token"])
        kept = {
            k: response.headers[k]
            for k in ("content-type", "content-disposition", "cache-control", "retry-after")
            if k in response.headers
        }
        response_record = {"status": response.status_code, "headers": kept}
        if file_info:
            response_record["file"] = file_info
        elif response.content:
            response_record["body"] = shown
        record = {
            "note": note,
            "request": {"method": method, "path": path, "headers": sent, "body": body},
            "response": response_record,
        }
        file = OUT / f"{Recorder.count:02d}-{name}.json"
        file.write_text(json.dumps(record, indent=2, ensure_ascii=False) + "\n")
        print(f"{file.name:52} {method:6} {path:50} -> {response.status_code}")


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


def seed(engine) -> dict:
    with Session(engine) as session:
        owner = session.scalars(select(User).where(User.email == OWNER_EMAIL)).one()
        owner.name = "Adebayo Example"
        general = session.scalars(
            select(Template).where(Template.owner_user_id == owner.id, Template.name == "General")
        ).one()
        pilot = User(
            email=PILOT_EMAIL,
            name="Daniel Example",
            password_hash=hash_password("-".join(["pilot", "password", "placeholder"])),
            email_verified_at=datetime.now(UTC),
        )
        session.add(pilot)
        session.commit()
        create_user_defaults(session, pilot)
        pilot_general = session.scalars(
            select(Template).where(Template.owner_user_id == pilot.id, Template.name == "General")
        ).one()
        team = ensure_team(session, owner)
        session.execute(
            TeamMember.__table__.delete().where(TeamMember.user_id == pilot.id)
        )  # their own team of one goes; they join the owner's
        session.add(TeamMember(team_id=team.id, user_id=pilot.id, role="member"))

        def meeting(owner_id, template_id, **fields) -> int:
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

        ready = {"status": "ready", "step": "done"}
        ids = {
            "ready": meeting(
                owner.id,
                general.id,
                **ready,
                notes=NOTES,
                transcript=TRANSCRIPT,
                speaker_transcript=TRANSCRIPT,
                recorded_seconds=1260,
                shared_team_id=team.id,
            ),
            "private": meeting(
                owner.id,
                general.id,
                **ready,
                notes=NOTES,
                transcript=TRANSCRIPT,
                speaker_transcript=TRANSCRIPT,
                recorded_seconds=1260,
            ),
            "processing": meeting(owner.id, general.id, status="processing", step="notes"),
            "theirs": meeting(
                pilot.id, pilot_general.id, **ready, notes=NOTES, speaker_transcript=TRANSCRIPT
            ),
        }
        session.commit()
    return ids


def mask(text: str) -> str:
    """The opt-out token is a credential until it is used: the captures show a placeholder."""
    return re.sub(r"(unsubscribe\?token=)[^\s\"<]+", r"\1<opt-out-token>", text)


class CapturingMailer:
    """Writes each message to a file instead of sending it."""

    count = 0
    first_token: str | None = None

    def send(self, message, key: str) -> None:
        CapturingMailer.count += 1
        if CapturingMailer.first_token is None:
            CapturingMailer.first_token = re.search(
                r"unsubscribe\?token=([^\s\"<]+)", message.html
            )[1]
        record = {
            "to": message.to,
            "reply_to": message.reply_to,
            "subject": message.subject,
            "headers": dict(message.headers),
            "attachments": [
                {"filename": a.filename, "content_type": a.content_type, "bytes": len(a.content)}
                for a in message.attachments
            ],
            "text": mask(message.text),
            "html": mask(message.html),
        }
        name = f"mail-{CapturingMailer.count:02d}-{message.to.split('@')[0]}.json"
        (OUT / name).write_text(json.dumps(record, indent=2, ensure_ascii=False) + "\n")
        print(f"{name:52} mail to {message.to}: {message.subject}")


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
        public_base_url="https://notes.example.test",
    )
    app = create_app(settings)
    server, base = start_server(app)
    engine = create_engine(scratch_url)
    try:
        run(base, seed(engine), engine, settings)
    finally:
        server.should_exit = True
        engine.dispose()


def work(engine, model) -> None:
    with Session(engine) as session:
        translation.process_next(session, datetime.now(UTC), client=model)


def deliver(engine, settings) -> int:
    with Session(engine) as session:
        return mail.deliver_pending(session, CapturingMailer(), settings, datetime.now(UTC))


def run(base: str, ids: dict, engine, settings) -> None:
    model = FakeTranslator()
    mid, private = ids["ready"], ids["private"]
    anonymous = Recorder(base)
    anonymous.call("session-anonymous", "GET", "/api/session")
    anonymous.call(
        "share-not-logged-in",
        "POST",
        f"/api/meetings/{mid}/shares",
        body={"recipients": [{"email": OUTSIDE}], "language": "en"},
        note="No login: 401 JSON, as for every route under /api.",
    )
    owner = Recorder(base)
    owner.call("session-anonymous", "GET", "/api/session")
    owner.call("login", "POST", "/api/session", body={"email": OWNER_EMAIL, "password": PASSWORD})
    owner.call("shares-empty", "GET", f"/api/meetings/{mid}/shares")

    # English, a Word file with the transcript inside, a teammate and an outside address.
    owner.call(
        "share-en-docx",
        "POST",
        f"/api/meetings/{mid}/shares",
        body={
            "recipients": [{"email": OUTSIDE, "name": "Client Example"}, {"email": PILOT_EMAIL}],
            "language": "en",
            "attach": "docx",
            "include_transcript": True,
            "message": "Sharing the notes from today's call. Let me know if anything looks off.",
        },
    )
    owner.call(
        "shares-pending",
        "GET",
        f"/api/meetings/{mid}/shares",
        note="Before the worker has sent anything: every recipient is `pending`.",
    )
    deliver(engine, settings)
    owner.call(
        "shares-sent",
        "GET",
        f"/api/meetings/{mid}/shares",
        note="After the worker's send pass. The teammate's name comes from their account.",
    )

    # A language that is not translated yet: waits for the translation, then the mail.
    owner.call(
        "share-fr-waiting",
        "POST",
        f"/api/meetings/{mid}/shares",
        body={"recipients": [{"email": OUTSIDE}], "language": "fr", "attach": "none"},
        note="202 with `waiting_translation`: the translation is queued and the mail follows.",
    )
    owner.call("shares-fr-waiting", "GET", f"/api/meetings/{mid}/shares")
    work(engine, model)
    owner.call(
        "shares-fr-queued",
        "GET",
        f"/api/meetings/{mid}/shares",
        note="The translation finished: the share is `queued` without the sender doing anything.",
    )
    deliver(engine, settings)
    owner.call("shares-fr-sent", "GET", f"/api/meetings/{mid}/shares")

    # Opt-out, from the link in the first mail.
    token = CapturingMailer.first_token
    visitor = Recorder(base)
    visitor.call("session-visitor", "GET", "/api/session")
    visitor.call(
        "optout-ok",
        "POST",
        "/api/mail/opt-out",
        body={"token": token},
        note="Public. The token is the one in the footer link of the first mail "
        "(`/unsubscribe?token=...`). It is used up by this call, so the captured mail files show "
        "`<opt-out-token>` instead of it.",
    )
    visitor.call("optout-used-twice", "POST", "/api/mail/opt-out", body={"token": token})
    visitor.call("optout-garbage", "POST", "/api/mail/opt-out", body={"token": "not-a-token"})
    visitor.call("optout-missing-token", "POST", "/api/mail/opt-out", body={})
    owner.call(
        "share-after-optout",
        "POST",
        f"/api/meetings/{private}/shares",
        body={
            "recipients": [{"email": OUTSIDE}, {"email": "other@example.test"}],
            "language": "en",
        },
    )
    deliver(engine, settings)
    owner.call(
        "shares-after-optout",
        "GET",
        f"/api/meetings/{private}/shares",
        note="The address that opted out shows `not_sent`; the reason is never named.",
    )

    # Refusals.
    for name, path, body in (
        ("share-language-unsupported", mid, {"recipients": [{"email": OUTSIDE}], "language": "de"}),
        (
            "share-too-many",
            mid,
            {"recipients": [{"email": f"p{i}@example.test"} for i in range(11)], "language": "en"},
        ),
        ("share-bad-address", mid, {"recipients": [{"email": "nope"}], "language": "en"}),
        ("share-no-recipients", mid, {"recipients": [], "language": "en"}),
        (
            "share-message-too-long",
            mid,
            {"recipients": [{"email": OUTSIDE}], "language": "en", "message": "x" * 501},
        ),
        (
            "share-attach-unknown",
            mid,
            {"recipients": [{"email": OUTSIDE}], "language": "en", "attach": "zip"},
        ),
        (
            "share-not-ready",
            ids["processing"],
            {"recipients": [{"email": OUTSIDE}], "language": "en"},
        ),
        (
            "share-someone-elses-meeting",
            ids["theirs"],
            {"recipients": [{"email": OUTSIDE}], "language": "en"},
        ),
        ("share-no-such-meeting", 999999, {"recipients": [{"email": OUTSIDE}], "language": "en"}),
    ):
        owner.call(name, "POST", f"/api/meetings/{path}/shares", body=body)
    owner.call("shares-someone-elses-meeting", "GET", f"/api/meetings/{ids['theirs']}/shares")

    # A teammate may read the shared meeting but may not share or list it: 404, not 403.
    mate = Recorder(base)
    mate.call("session-anonymous", "GET", "/api/session")
    mate.call(
        "login-teammate",
        "POST",
        "/api/session",
        body={
            "email": PILOT_EMAIL,
            "password": "-".join(["pilot", "password", "placeholder"]),
        },
    )
    mate.call("teammate-reads-meeting", "GET", f"/api/meetings/{mid}")
    mate.call(
        "teammate-share-is-404",
        "POST",
        f"/api/meetings/{mid}/shares",
        body={"recipients": [{"email": OUTSIDE}], "language": "en"},
    )
    mate.call("teammate-list-is-404", "GET", f"/api/meetings/{mid}/shares")

    # The daily limit: 20 recipients in 24 hours (10 + 10 + 1).
    with Session(engine) as session:
        used = session.scalar(select(func.count()).select_from(MeetingShareRecipient))
    room = 20 - used
    owner.call(
        "share-fills-the-day",
        "POST",
        f"/api/meetings/{private}/shares",
        body={
            "recipients": [{"email": f"fill{i}@example.test"} for i in range(min(room, 10))],
            "language": "en",
        },
        note=f"{used} recipients were already used today by the calls above.",
    )
    if room > 10:
        owner.call(
            "share-fills-the-day-2",
            "POST",
            f"/api/meetings/{private}/shares",
            body={
                "recipients": [{"email": f"fill-b{i}@example.test"} for i in range(room - 10)],
                "language": "en",
            },
        )
    owner.call(
        "share-limit",
        "POST",
        f"/api/meetings/{private}/shares",
        body={"recipients": [{"email": "one-more@example.test"}], "language": "en"},
        note="429 `share_limit`: 20 recipients a day per account.",
    )


if __name__ == "__main__":
    main()
