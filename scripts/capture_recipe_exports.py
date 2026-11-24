"""Drive the draft export route over real HTTP and save every exchange for the frontend guide
(`docs/fe-integration-guide-recipes-documents.md`).

    PYTHONPATH=. python scripts/capture_recipe_exports.py postgresql+psycopg://USER:PASS@HOST:PORT/postgres

The argument is any Postgres server the script may create a scratch database on. It runs the real
migrations there, starts the real app under uvicorn and signs in as placeholder users. Each
exchange is written to `docs/fe-captures/recipes/exports/NN-name.json`.

What is real: the app, auth, ownership (404 for another person's draft), the database, the
migrations, the export route, `app/recipes/exporters.py`, and the Word and PDF render child
process (`app/export.py`). Text bodies are saved verbatim. A binary body (Excel, Word, PDF) cannot
be pasted into JSON, so the file records its size, its first bytes and what the script read back
by opening it with openpyxl or python-docx; the PDF records its size and header only.

What is NOT real, and is said here: the drafts are inserted straight into the database, because
the route that makes a draft (`POST /api/documents`) is PR 265's and this slice does not depend on
it. Their blocks are the exact shape `app/recipes/blocks.py` stores. Every name is a placeholder.

Secrets are masked: CSRF tokens become `<csrf-A>` in order of first appearance, cookies `<cookie>`.
"""

# ruff: noqa: E501  (the notes are prose for the guide, one long line each)

import io
import json
import os
import socket
import sys
import threading
import time
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "docs" / "fe-captures" / "recipes" / "exports"
DB_NAME = "notetaker_capture_recipe_exports"
ADMIN_EMAIL = "admin@example.test"
ADMIN_PASSWORD = "-".join(["capture", "admin", "placeholder"])
PEOPLE = {  # email -> (name, password); all placeholders
    "ada@example.test": ("Ada Lovelace", "-".join(["ada", "chosen", "password", "1"])),
    "eve@example.test": ("Eve Outsider", "-".join(["eve", "chosen", "password", "3"])),
}

admin_url = sys.argv[1] if len(sys.argv) > 1 else ""
scratch_url = urlparse(admin_url)._replace(path=f"/{DB_NAME}").geturl()
os.environ["DATABASE_URL"] = scratch_url
os.environ["OWNER_EMAIL"] = ADMIN_EMAIL
os.environ["OWNER_PASSWORD"] = ADMIN_PASSWORD
os.environ.setdefault("APP_ENV", "development")

import httpx  # noqa: E402
import uvicorn  # noqa: E402
from alembic import command  # noqa: E402
from alembic.config import Config  # noqa: E402
from docx import Document as WordDocument  # noqa: E402
from openpyxl import load_workbook  # noqa: E402
from sqlalchemy import create_engine, text  # noqa: E402
from sqlalchemy.orm import Session  # noqa: E402

from app import accounts, db  # noqa: E402
from app.config import Settings  # noqa: E402
from app.main import create_app  # noqa: E402
from app.models import AppState, Document, User  # noqa: E402
from app.passwords import hash_password  # noqa: E402
from app.recipes import blocks as b  # noqa: E402


def cell(text_: str) -> dict:
    return {"text": text_, "sources": []}


PROPOSAL = [
    {"type": "heading", "level": 1, "text": "Proposal for Acme Ltd"},
    {"type": "paragraph", "text": "Onboarding for Acme's support team, as we discussed.", "sources": []},
    {"type": "heading", "level": 2, "text": "Timeline"},
    {"type": "table", "columns": ["Phase", "When", "Owner"], "rows": [[cell("Setup"), cell("Week 1"), cell("Ada")], [cell("Training"), cell("Week 2"), cell("[need: trainer]")]]},
    {"type": "heading", "level": 2, "text": "Investment"},
    {"type": "table", "columns": ["Item", "Price", "Note"], "rows": [[cell("Onboarding"), cell("₦50,000"), cell("Paid, monthly")], [cell("Add-on, \"priority\" support"), cell("=1+1"), cell("-5% for 12 months")]]},
    {"type": "bullets", "items": [cell("Start date: [need: start date]")]},
]  # fmt: skip
NOTE_ONLY = [{"type": "paragraph", "text": "Thanks for today, Daniel.", "sources": []}]


class Recorder:
    count = 0
    masks: dict[str, str] = {}

    def __init__(self, base_url: str, who: str) -> None:
        self.http = httpx.Client(base_url=base_url, follow_redirects=False, timeout=120)
        self.csrf: str | None = None
        self.who = who

    @classmethod
    def mask(cls, token: str) -> str:
        return cls.masks.setdefault(token, f"<csrf-{chr(ord('A') + len(cls.masks))}>")

    def call(self, name, method, path, *, body=None, note=""):
        headers = {"X-CSRF-Token": self.csrf} if self.csrf and method != "GET" else {}
        had_cookie = "session" in self.http.cookies
        response = self.http.request(method, path, json=body, headers=headers)
        kind = response.headers.get("content-type", "")
        if "json" in kind:
            shown = response.json()
            if isinstance(shown, dict) and "csrf_token" in shown:
                self.csrf = shown["csrf_token"]
                shown = {**shown, "csrf_token": self.mask(shown["csrf_token"])}
        elif kind.startswith("text/"):
            shown = response.content.decode("utf-8")  # verbatim; a BOM stays visible below
        else:
            shown = describe_file(response.content, kind)
        Recorder.count += 1
        sent = {k: (self.mask(v) if k == "X-CSRF-Token" else v) for k, v in headers.items()}
        if had_cookie:
            sent["Cookie"] = "session=<cookie>"
        keep = ("content-type", "content-disposition", "cache-control", "retry-after")
        kept = {k: response.headers[k] for k in keep if k in response.headers}
        record = {
            "note": note,
            "client": self.who,
            "request": {"method": method, "path": path, "headers": sent, "body": body},
            "response": {
                "status": response.status_code,
                "headers": kept,
                "starts_with_bytes": response.content[:4].hex(),
                "body": shown,
            },
        }
        (OUT / f"{Recorder.count:02d}-{name}.json").write_text(
            json.dumps(record, indent=2, ensure_ascii=False) + "\n"
        )
        print(f"{Recorder.count:02d}-{name:34} {method} {path:42} -> {response.status_code}")
        return response


def describe_file(content: bytes, kind: str) -> dict:
    """What a binary export holds, read back with the library that opens it."""
    info: dict = {"bytes": len(content), "binary": True}
    if "spreadsheetml" in kind:
        book = load_workbook(io.BytesIO(content))
        info["sheets"] = {
            sheet.title: [[c.value for c in row] for row in sheet.iter_rows()]
            for sheet in book.worksheets
        }
        info["cell_types"] = sorted({c.data_type for s in book.worksheets for r in s.iter_rows() for c in r})  # fmt: skip
    elif "wordprocessingml" in kind:
        word = WordDocument(io.BytesIO(content))
        info["tables"] = [[[c.text for c in row.cells] for row in t.rows] for t in word.tables]
        info["paragraphs"] = [p.text for p in word.paragraphs if p.text][:8]
    elif kind == "application/pdf":
        info["pdf_header"] = content[:8].decode("latin-1")
    return info


def start_server(app):
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
    now = datetime.now(UTC)
    ids: dict = {}
    with Session(engine) as session:
        for email, (name, password) in PEOPLE.items():
            user = User(email=email, name=name, password_hash=hash_password(password), email_verified_at=now)  # fmt: skip
            session.add(user)
            session.flush()
            accounts.add_user_defaults(session, user)
            ids[email] = user.id
        state = session.get(AppState, 1) or AppState(id=1)
        state.max_accounts = 10
        session.add(state)
        session.commit()
        ada, eve = ids["ada@example.test"], ids["eve@example.test"]

        def draft(owner, title, blocks, status="ready"):
            row = Document(owner_user_id=owner, title=title, output_type="document", status=status, blocks=blocks, created_at=now)  # fmt: skip
            session.add(row)
            session.commit()
            return row.id

        ids["proposal"] = draft(ada, "Proposal for Acme Ltd", PROPOSAL)
        ids["words"] = draft(ada, "Thanks for today", NOTE_ONLY)
        ids["writing"] = draft(ada, "Still writing", [], status="writing")
        ids["eves"] = draft(eve, "Eve's draft", NOTE_ONLY)
        # An edited draft: the person rewrote the paragraph; every export must show the new text.
        edited = [{**PROPOSAL[1], "text": "Rewritten by Ada after the draft was made."}] + PROPOSAL[
            2:
        ]
        ids["edited"] = draft(ada, "Edited proposal", edited)
        session.get(Document, ids["edited"]).edited = True
        session.commit()
    assert b.load_blocks(PROPOSAL)  # the stored shape is the schema's
    return ids


def run(base: str, ids: dict) -> None:
    ada, eve = Recorder(base, "ada"), Recorder(base, "eve")
    for rec, email in ((ada, "ada@example.test"), (eve, "eve@example.test")):
        rec.call("session", "GET", "/api/session")
        rec.call("login", "POST", "/api/session", body={"email": email, "password": PEOPLE[email][1]})  # fmt: skip
    anon = Recorder(base, "visitor")
    p, x = ids["proposal"], "/export?format="
    anon.call("logged-out", "GET", f"/api/documents/{p}{x}md", note="No login: 401.")
    ada.call("markdown", "GET", f"/api/documents/{p}{x}md", note="Markdown. A table is a pipe table; the text is escaped for Markdown (`\\[need: ...\\]`, `\\*`...). The body is the file.")  # fmt: skip
    ada.call("text", "GET", f"/api/documents/{p}{x}txt", note="Plain text: bullets as `- `, a table as lines joined by ` | `, nothing escaped. What Copy and Open-in-mail-app use.")  # fmt: skip
    ada.call("csv-first-table", "GET", f"/api/documents/{p}{x}csv", note="CSV of the FIRST table. UTF-8 with a byte order mark (`starts_with_bytes` is `efbbbf..`); the file's text is shown in `body` with the mark as a leading U+FEFF. No formula cells here: the neutralising is shown in the second table.")  # fmt: skip
    ada.call("csv-second-table", "GET", f"/api/documents/{p}{x}csv&table=1", note="CSV of the table numbered 1 (the Investment table). `=1+1` is written `'=1+1` and `-5% for 12 months` is written `'-5% for 12 months`: a leading apostrophe on any cell that could run as a formula. The embedded quote and the comma are quoted as CSV needs.")  # fmt: skip
    ada.call("csv-no-such-table", "GET", f"/api/documents/{p}{x}csv&table=2", note="A table number past the last one: 409 `no_table`.")  # fmt: skip
    ada.call("xlsx", "GET", f"/api/documents/{p}{x}xlsx", note="Excel: ONE SHEET PER TABLE, named from the heading above it (`Timeline`, `Investment`), header row bold and frozen. `cell_types` is only `s`: every cell is text, so `=1+1` is a string `'=1+1`, never a formula. Body is the file read back with openpyxl.")  # fmt: skip
    ada.call("docx", "GET", f"/api/documents/{p}{x}docx", note="Word, built by the render child process. Body is read back with python-docx: two real tables, header row first.")  # fmt: skip
    ada.call("pdf", "GET", f"/api/documents/{p}{x}pdf", note="PDF, built by the render child process (WeasyPrint). Only the size and header are recorded.")  # fmt: skip
    ada.call("default-format", "GET", f"/api/documents/{p}/export", note="No `format`: PDF.")  # fmt: skip
    ada.call("edited-markdown", "GET", f"/api/documents/{ids['edited']}{x}md", note="A draft the person edited: the export has the edited paragraph, not the first generation.")  # fmt: skip
    ada.call("csv-without-a-table", "GET", f"/api/documents/{ids['words']}{x}csv", note="A draft with no table: CSV is 409 `no_table`.")  # fmt: skip
    ada.call("xlsx-without-a-table", "GET", f"/api/documents/{ids['words']}{x}xlsx", note="Same for Excel.")  # fmt: skip
    ada.call("markdown-without-a-table", "GET", f"/api/documents/{ids['words']}{x}md", note="Markdown, text, Word and PDF work for any ready draft.")  # fmt: skip
    ada.call("still-writing", "GET", f"/api/documents/{ids['writing']}{x}md", note="A draft still being written: 409 `not_ready`.")  # fmt: skip
    ada.call("bad-format", "GET", f"/api/documents/{p}{x}rtf", note="An unknown format: 422 `validation_error`, the field is `format`.")  # fmt: skip
    ada.call("bad-table-number", "GET", f"/api/documents/{p}{x}csv&table=-1", note="A negative table number: 422.")  # fmt: skip
    eve.call("someone-elses-draft", "GET", f"/api/documents/{p}{x}md", note="Another person's draft: 404 `not_found`, never 403.")  # fmt: skip
    ada.call("no-such-draft", "GET", f"/api/documents/999999{x}md", note="No such draft: 404, the same body.")  # fmt: skip


def main() -> None:
    if not admin_url:
        raise SystemExit(__doc__)
    OUT.mkdir(parents=True, exist_ok=True)
    for old in OUT.glob("*.json"):
        old.unlink()
    prepare_database()
    settings = Settings(
        _env_file=None,
        app_env="test",
        session_secret="capture-secret-" + "x" * 30,
        cookie_secure=False,
        database_url=scratch_url,
    )
    app = create_app(settings)
    db.SessionLocal.configure(bind=create_engine(scratch_url))
    engine = create_engine(scratch_url)
    ids = seed(engine)
    server, base = start_server(app)
    try:
        run(base, ids)
    finally:
        server.should_exit = True
        engine.dispose()


if __name__ == "__main__":
    main()
