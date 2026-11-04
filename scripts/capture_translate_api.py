"""Drive the translation routes over real HTTP and save every exchange for the frontend guide.

    python scripts/capture_translate_api.py postgresql+psycopg://USER:PASS@HOST:PORT/postgres

Same method as `capture_export_api.py`: a scratch database with the real migrations, the real app
under uvicorn on a free local port, httpx for every call. Only the models are faked, and they are
faked on purpose, so read the captures with that in mind:

- the translating model answers from `tests/fixtures/translate_sample.json`, the designer's own
  French and Spanish for the designer's own sample meeting, sentence by sentence. It is not a
  model, so nothing here says anything about real translation quality (the TR1 eval does:
  `tests/eval/translation_eval.py`);
- the notes model (for "Rewrite notes") returns the sample notes with one sentence changed.

The worker is not started: the script runs `translation.process_next` itself between calls, which
is exactly what the worker's thread does, so each state the screen polls can be captured in
order. Each exchange goes to `docs/fe-captures/translate/NN-name.json`; a file the server sends
is saved under `files/`. Two things cannot be provoked from outside and are provoked inside this
process (the capture's `note` says so): a translation that fails its checks (the fake model is
told to return one line too few), and the stale state (the real "Rewrite notes" route, with the
fake notes model).
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
OUT = ROOT / "docs" / "fe-captures" / "translate"
SAMPLE = json.loads((ROOT / "tests" / "fixtures" / "translate_sample.json").read_text())
DB_NAME = "notetaker_capture_translate"
OWNER_EMAIL = "owner@example.test"  # the admin account migration 0006 creates (a placeholder)
PASSWORD = "-".join(["capture", "password", "placeholder"])
PILOT_EMAIL = "pilot@example.test"
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

from app import translation  # noqa: E402
from app.accounts import create_user_defaults  # noqa: E402
from app.config import Settings  # noqa: E402
from app.main import create_app  # noqa: E402
from app.models import Meeting, Template, User  # noqa: E402
from app.passwords import hash_password  # noqa: E402
from app.services import get_openai_client  # noqa: E402

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
        target = OUT / "files" / f"{Recorder.count + 1:02d}-{name}.docx"
        target.parent.mkdir(exist_ok=True)
        target.write_bytes(response.content)
        return {"saved_as": f"files/{target.name}", "bytes": len(response.content)}

    def _save(self, name, method, path, headers, body, had_cookie, response, data, file_info, note):
        Recorder.count += 1
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


def seed(engine) -> dict[str, list[int] | int]:
    with Session(engine) as session:
        owner = session.scalars(select(User).where(User.email == OWNER_EMAIL)).one()
        general = session.scalars(
            select(Template).where(Template.owner_user_id == owner.id, Template.name == "General")
        ).one()
        pilot = User(
            email=PILOT_EMAIL,
            name="Pilot Example",
            password_hash=hash_password("-".join(["pilot", "password", "placeholder"])),
            email_verified_at=datetime.now(UTC),
        )
        session.add(pilot)
        session.commit()
        create_user_defaults(session, pilot)
        pilot_general = session.scalars(
            select(Template).where(Template.owner_user_id == pilot.id, Template.name == "General")
        ).one()

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
        ids: dict[str, list[int] | int] = {
            "ready": meeting(
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
            "plain": meeting(
                owner.id,
                general.id,
                **ready,
                notes="## Summary\nA short call.\n",
                transcript="A short call.",
            ),
        }
        ids["limit"] = [
            meeting(
                owner.id,
                general.id,
                **ready,
                notes=f"## Summary\nCall {n}.\n",
                speaker_transcript=f"[Ada Example]: Call {n}.",
            )
            for n in range(21)
        ]
        session.commit()
    return ids


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
    app.dependency_overrides[get_openai_client] = lambda: FakeNotesModel()
    server, base = start_server(app)
    engine = create_engine(scratch_url)
    try:
        run(base, seed(engine), engine)
    finally:
        server.should_exit = True
        engine.dispose()


def work(engine, model: FakeTranslator) -> None:
    """One step of the worker's translation thread."""
    with Session(engine) as session:
        translation.process_next(session, datetime.now(UTC), client=model)


def run(base: str, ids: dict, engine) -> None:
    model = FakeTranslator()
    anonymous = Recorder(base)
    anonymous.call("session-anonymous", "GET", "/api/session")
    mid = ids["ready"]
    anonymous.call(
        "translate-not-logged-in",
        "POST",
        f"/api/meetings/{mid}/translations",
        body={"language": "fr"},
        note="No login: 401 JSON, as for every route under /api.",
    )
    owner = Recorder(base)
    owner.call("session-anonymous", "GET", "/api/session")
    owner.call("login", "POST", "/api/session", body={"email": OWNER_EMAIL, "password": PASSWORD})
    owner.call("meeting-before", "GET", f"/api/meetings/{mid}", note="Nothing translated yet.")
    owner.call("language-default", "GET", "/api/settings/language")

    # French: queued, polled while it is made, ready, then served from the cache.
    owner.call(
        "translate-fr-queued", "POST", f"/api/meetings/{mid}/translations", body={"language": "fr"}
    )
    owner.call("translate-fr-translating", "GET", f"/api/meetings/{mid}/translations/fr")
    owner.call(
        "translate-fr-again-while-working",
        "POST",
        f"/api/meetings/{mid}/translations",
        body={"language": "fr"},
        note="Asking again while it is being made changes nothing: still 202 translating.",
    )
    work(engine, model)
    owner.call(
        "translate-fr-ready",
        "GET",
        f"/api/meetings/{mid}/translations/fr",
        note="`notes` has the dictionary's headings; `transcript` has the original's lines.",
    )
    owner.call(
        "translate-fr-cached", "POST", f"/api/meetings/{mid}/translations", body={"language": "fr"}
    )
    owner.call("meeting-with-french", "GET", f"/api/meetings/{mid}")
    owner.call(
        "translate-es-queued", "POST", f"/api/meetings/{mid}/translations", body={"language": "es"}
    )
    work(engine, model)
    owner.call("translate-es-ready", "GET", f"/api/meetings/{mid}/translations/es")

    # Download in a language.
    owner.call(
        "export-fr-docx",
        "GET",
        f"/api/meetings/{mid}/export?format=docx&transcript=true&language=fr",
        note="Built from the cached French translation.",
    )
    owner.call("export-en-docx", "GET", f"/api/meetings/{mid}/export?format=docx&language=en")
    owner.call(
        "export-language-unknown",
        "GET",
        f"/api/meetings/{mid}/export?format=docx&language=yo",
    )

    # The default language.
    owner.call("language-set-fr", "PUT", "/api/settings/language", body={"default_language": "fr"})
    owner.call("language-get-fr", "GET", "/api/settings/language")
    owner.call(
        "language-set-unsupported", "PUT", "/api/settings/language", body={"default_language": "yo"}
    )
    owner.call("language-set-missing", "PUT", "/api/settings/language", body={})
    owner.call("language-set-en", "PUT", "/api/settings/language", body={"default_language": "en"})

    # Refusals.
    for name, body in (
        ("translate-language-unsupported", {"language": "yo"}),
        ("translate-language-english", {"language": "en"}),
        ("translate-language-missing", {}),
    ):
        owner.call(name, "POST", f"/api/meetings/{mid}/translations", body=body)
    owner.call(
        "translate-not-ready",
        "POST",
        f"/api/meetings/{ids['processing']}/translations",
        body={"language": "fr"},
    )
    owner.call("translate-get-never-asked", "GET", f"/api/meetings/{ids['plain']}/translations/fr")
    owner.call("translate-get-unsupported-language", "GET", f"/api/meetings/{mid}/translations/yo")
    owner.call(
        "export-fr-not-translated",
        "GET",
        f"/api/meetings/{ids['plain']}/export?format=docx&language=fr",
    )
    theirs = ids["theirs"]
    owner.call(
        "translate-foreign-meeting",
        "POST",
        f"/api/meetings/{theirs}/translations",
        body={"language": "fr"},
    )
    owner.call("translate-foreign-get", "GET", f"/api/meetings/{theirs}/translations/fr")
    owner.call("translate-foreign-delete", "DELETE", f"/api/meetings/{theirs}/translations/fr")
    owner.call("translate-missing-meeting", "GET", "/api/meetings/999999/translations/fr")

    # Stale: the notes are rewritten (the real route, the notes model faked), so the finished
    # French is no longer of the current notes.
    template_id = owner.call("templates", "GET", "/api/templates").json()["templates"][0]["id"]
    owner.call(
        "rewrite-notes",
        "POST",
        f"/api/meetings/{mid}/regenerate",
        body={"template_id": template_id},
        note="One sentence of the notes changes (the notes model is a fake).",
    )
    owner.call("translate-fr-stale", "GET", f"/api/meetings/{mid}/translations/fr")
    owner.call(
        "export-fr-stale",
        "GET",
        f"/api/meetings/{mid}/export?format=docx&language=fr",
        note="A stale translation is not downloaded.",
    )
    owner.call(
        "translate-fr-again", "POST", f"/api/meetings/{mid}/translations", body={"language": "fr"}
    )
    work(engine, model)
    owner.call("translate-fr-fresh", "GET", f"/api/meetings/{mid}/translations/fr")

    # Failed: the fake model returns one line too few, twice, so the code's checks refuse it.
    owner.call("translate-es-delete", "DELETE", f"/api/meetings/{mid}/translations/es")
    model.drift = True
    owner.call(
        "translate-es-queued-drift",
        "POST",
        f"/api/meetings/{mid}/translations",
        body={"language": "es"},
    )
    work(engine, model)
    owner.call(
        "translate-es-failed",
        "GET",
        f"/api/meetings/{mid}/translations/es",
        note="Provoked inside the capture: the fake model dropped a line, so the check failed.",
    )
    model.drift = False
    owner.call(
        "translate-es-retry", "POST", f"/api/meetings/{mid}/translations", body={"language": "es"}
    )
    work(engine, model)
    owner.call("translate-es-ready-again", "GET", f"/api/meetings/{mid}/translations/es")
    owner.call("translate-delete", "DELETE", f"/api/meetings/{mid}/translations/es")
    owner.call("translate-after-delete", "GET", f"/api/meetings/{mid}/translations/es")
    owner.call("translate-delete-again", "DELETE", f"/api/meetings/{mid}/translations/es")

    # The daily limit: what the translations above used is counted (each counts once it is queued
    # or made, and deleting gives nothing back). The rest of the 20 are used up without recording
    # them, and the next request is the 429 that is.
    with Session(engine) as session:
        owner_id = session.scalars(select(User.id).where(User.email == OWNER_EMAIL)).one()
        used = translation.daily_count(session, owner_id, datetime.now(UTC))
    room = translation.DAILY_LIMIT - used
    headers = {"X-CSRF-Token": owner.csrf}
    for limit_id in ids["limit"][:room]:
        owner.http.post(
            f"/api/meetings/{limit_id}/translations", json={"language": "fr"}, headers=headers
        )
    owner.call(
        "translate-limit",
        "POST",
        f"/api/meetings/{ids['limit'][room]}/translations",
        body={"language": "fr"},
        note=f"{used} translations were counted by the earlier calls and {room} more were queued "
        "just before this one: the 21st of the day.",
    )


if __name__ == "__main__":
    main()
