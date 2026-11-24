"""Drive the board route of meeting recipes over real HTTP and save every exchange for the frontend
guide (`docs/fe-integration-guide-recipes-board.md`).

    PYTHONPATH=. python scripts/capture_recipe_board.py postgresql+psycopg://USER:PASS@HOST:PORT/postgres
    PYTHONPATH=. python scripts/capture_recipe_board.py ADMIN_URL --serve 8801   # for a browser pass

The argument is any Postgres server the script may create a scratch database on. It runs the real
migrations there, starts the real app under uvicorn, starts the real worker loop
(`app.recipes.service.run_loop`) in a thread and signs in as placeholder users. Each exchange is
written to `docs/fe-captures/recipes/board/NN-name.json`. With `--serve PORT` nothing is captured:
the app stays up on that port with two boards made, so a web dev server can be pointed at it
(`FASTAPI_INTERNAL_URL=http://127.0.0.1:PORT npm run dev`) and the screen looked at in a browser.

What is real: the app, auth, CSRF, validation, ownership (404 for another person), the database,
migrations, the queue and worker, the checks on the model's reply (a board is made of columns), the
number audit, every operation and every error body.

What is NOT real, and is labelled where it matters:
* **The model.** `FakeModel` stands in for the OpenAI client (no key, no network). It returns the
  strict-JSON shape `app/recipes/generate.py` asks for, citing real line numbers it is shown; a real
  model's wording and its grouping into themes will differ, the shape of every body will not.
* **PDF and Word of a board** go through slice S2's export route, which is not on this branch: the
  render model (`board.to_notes_document`) is shown being built and rendered, not fetched over HTTP.
* **Not run live:** a teammate the board was shared with (slice S4); shape from
  `tests/test_api_recipe_board.py` (404, as for anyone who is not the creator).
Nobody's real meeting or address is involved; every name and address is a placeholder.

Secrets are masked: CSRF tokens become `<csrf-A>`, `<csrf-B>` in order of first appearance, cookies
`<cookie>`.
"""

# ruff: noqa: E501  (the notes are prose for the guide, one long line each)

import json
import os
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
OUT = ROOT / "docs" / "fe-captures" / "recipes" / "board"
DB_NAME = "notetaker_capture_recipe_board"
ADMIN_EMAIL = "admin@example.test"
ADMIN_PASSWORD = "-".join(["capture", "admin", "placeholder"])
PEOPLE = {  # email -> (name, password); all placeholders
    "ada@example.test": ("Ada Lovelace", "-".join(["ada", "chosen", "password", "1"])),
    "eve@example.test": ("Eve Outsider", "-".join(["eve", "chosen", "password", "3"])),
}

args = [a for a in sys.argv[1:] if not a.startswith("--")]
admin_url = args[0] if args else ""
serve_port = int(sys.argv[sys.argv.index("--serve") + 1]) if "--serve" in sys.argv else None
if serve_port is not None:
    args = [a for a in args if a != str(serve_port)]
    admin_url = args[0] if args else ""
scratch_url = urlparse(admin_url)._replace(path=f"/{DB_NAME}").geturl()
os.environ["DATABASE_URL"] = scratch_url
os.environ["OWNER_EMAIL"] = ADMIN_EMAIL
os.environ["OWNER_PASSWORD"] = ADMIN_PASSWORD
os.environ.setdefault("APP_ENV", "development")

import httpx  # noqa: E402
import uvicorn  # noqa: E402
from alembic import command  # noqa: E402
from alembic.config import Config  # noqa: E402
from sqlalchemy import create_engine, select, text  # noqa: E402
from sqlalchemy.orm import Session  # noqa: E402

from app import accounts  # noqa: E402
from app.config import Settings  # noqa: E402
from app.main import create_app  # noqa: E402
from app.models import AppState, Meeting, Template, User  # noqa: E402
from app.passwords import hash_password  # noqa: E402
from app.recipes import blocks as blocks_mod  # noqa: E402
from app.recipes import board as board_mod  # noqa: E402
from app.recipes import generate, library  # noqa: E402
from app.recipes import service as recipe_service  # noqa: E402

BOARD = library.starter_by_key("board")  # the real starter (app/recipes/library.py)
EMAIL = SimpleNamespace(
    key="follow_up_email",
    name="Follow-up email",
    instruction="Write a short follow-up email.",
    output_type="email",
    scope="one",
    uses_brief=True,
)

NOTES = (
    "## Summary\nOnboarding for Daniel Okoro's team was agreed for 50,000 naira.\n\n"
    "## Decisions\n- Start on 2026-12-01.\n\n## Action items\n- Ada: send the plan by Friday.\n"
)
TRANSCRIPT = (
    "[Ada Lovelace]: Thanks for joining, Daniel.\n"
    "[Daniel Okoro]: We would like the onboarding for 50,000 naira, starting 2026-12-01.\n"
    "[Ada Lovelace]: Agreed. I will send the plan by Friday.\n"
    "[Daniel Okoro]: Who signs the contract on our side is still open."
)


def line(meeting_id: int, n: int) -> dict:
    return {"kind": "line", "meeting_id": meeting_id, "line": n, "quote": None, "ref": None}


def column(title: str, cards: list[dict]) -> dict:
    return {"type": "column", "title": title, "cards": cards, "text": None, "level": None, "sources": None, "items": None, "columns": None, "rows": None}  # fmt: skip


def card(text: str, kind: str, owner: str | None = None, sources: list[dict] | None = None) -> dict:
    return {"text": text, "kind": kind, "owner": owner, "sources": sources or []}


class FakeModel:
    """The OpenAI client, replaced. `mode` says what the next board is: `board` (a small realistic
    one), `full` (12 columns of 30 cards), `single` (one column), `slow` (a board, after 2 s)."""

    def __init__(self) -> None:
        self.mode = "board"
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self._create))

    def _create(self, **kwargs):
        user = kwargs["messages"][1]["content"]
        m = int(user.split("Meeting id: ")[1].split("\n")[0])
        if self.mode == "slow":
            time.sleep(2.0)
        if "Output type: email" in user:
            blank = {"type": "paragraph", "level": None, "items": None, "columns": None, "rows": None, "title": None, "cards": None}  # fmt: skip
            blocks = [{**blank, "text": "Hi Daniel, thanks for today.", "sources": [line(m, 1)]}]
        elif self.mode == "full":
            blocks = [column(f"Theme {c + 1}", [card(f"Card {c + 1}.{k + 1}", "note") for k in range(30)]) for c in range(12)]  # fmt: skip
        elif self.mode == "single":
            blocks = [column("Everything", [card("Send the plan", "action", "Ada", [line(m, 3)])])]
        else:
            blocks = [
                column("Pricing and start", [
                    card("Onboarding is 50,000 naira", "decision", None, [line(m, 2)]),
                    card("Start on 2026-12-01", "decision", None, [line(m, 2)]),
                    card("Setup fee of 120,000 naira", "note"),
                ]),
                column("Next steps", [
                    card("Send the plan by Friday", "action", "Ada", [line(m, 3)]),
                    card("Confirm the kick-off call on [need: call date]", "action", "Daniel"),
                ]),
                column("Open questions", [
                    card("Who signs the contract on Daniel's side?", "question", None, [line(m, 4)]),
                ]),
                column("Parked", []),
            ]  # fmt: skip
        content = json.dumps({"title": "Onboarding board", "blocks": blocks})
        return SimpleNamespace(
            choices=[
                SimpleNamespace(message=SimpleNamespace(content=content), finish_reason="stop")
            ],
            usage=SimpleNamespace(prompt_tokens=2100, completion_tokens=420),
        )


def hide_tokens(value):
    if isinstance(value, dict) and "password" in value:
        return {**value, "password": "<password placeholder>"}
    return value


class Recorder:
    count = 0
    masks: dict[str, str] = {}

    def __init__(self, base_url: str, who: str) -> None:
        self.http = httpx.Client(base_url=base_url, follow_redirects=False, timeout=300)
        self.csrf: str | None = None
        self.who = who

    @classmethod
    def mask(cls, token: str) -> str:
        return cls.masks.setdefault(token, f"<csrf-{chr(ord('A') + len(cls.masks))}>")

    def call(self, name, method, path, *, body=None, csrf="session", note="", record=True):
        headers = {}
        token = (None if method == "GET" else self.csrf) if csrf == "session" else csrf
        if token:
            headers["X-CSRF-Token"] = token
        had_cookie = "session" in self.http.cookies
        response = self.http.request(method, path, json=body, headers=headers)
        content_type = response.headers.get("content-type", "")
        data = response.json() if "json" in content_type else (response.text or None)
        if isinstance(data, dict) and "csrf_token" in data:
            self.csrf = data["csrf_token"]
        if not record:
            return response
        Recorder.count += 1
        sent = {k: (self.mask(v) if k == "X-CSRF-Token" else v) for k, v in headers.items()}
        if had_cookie:
            sent["Cookie"] = "session=<cookie>"
        kept = {k: response.headers[k] for k in ("content-type", "allow") if k in response.headers}
        shown = json.loads(json.dumps(data))
        if isinstance(shown, dict) and "csrf_token" in shown:
            shown["csrf_token"] = self.mask(shown["csrf_token"])
        record_ = {
            "note": note,
            "client": self.who,
            "request": {"method": method, "path": path, "headers": sent, "body": hide_tokens(body)},
            "response": {"status": response.status_code, "headers": kept, "body": shown},
        }
        (OUT / f"{Recorder.count:02d}-{name}.json").write_text(
            json.dumps(record_, indent=2, ensure_ascii=False) + "\n"
        )
        print(f"{Recorder.count:02d}-{name:40} {method:6} {path:30} -> {response.status_code}")
        return response


def note_file(name: str, note: str, content) -> None:
    Recorder.count += 1
    (OUT / f"{Recorder.count:02d}-{name}.json").write_text(
        json.dumps({"note": note, "content": content}, indent=2, ensure_ascii=False, default=str)
        + "\n"
    )
    print(f"{Recorder.count:02d}-{name}")


def start_server(app, port: int | None = None):
    if port is None:
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


def add_meeting(session, owner_id, template_id, title):
    meeting = Meeting(owner_user_id=owner_id, url="https://meet.google.com/abc-defg-hij", platform="google_meet", native_id="abc-defg-hij", template_id=template_id, title=title, created_at=datetime(2026, 11, 20, 10, 0, tzinfo=UTC), recorded_seconds=1800, source="calendar", status="ready", step="done", notes=NOTES, transcript=TRANSCRIPT, speaker_transcript=TRANSCRIPT)  # fmt: skip
    session.add(meeting)
    session.commit()
    return meeting


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
        ada = ids["ada@example.test"]
        template_id = session.scalars(select(Template.id).where(Template.owner_user_id == ada, Template.name == "General")).one()  # fmt: skip
        for key, title in (
            ("a", "Onboarding call"),
            ("b", "Kick-off"),
            ("c", "Review"),
            ("d", "Retro"),
        ):
            ids[key] = add_meeting(session, ada, template_id, title).id
    return ids


def wait_for(rec: Recorder, doc_id: int, status: str) -> dict:
    for _ in range(100):
        got = rec.http.get(f"/api/documents/{doc_id}").json()
        if got["status"] == status:
            return got
        time.sleep(0.1)
    raise SystemExit(f"draft {doc_id} never became {status}")


def make_board(rec: Recorder, model: FakeModel, meeting: int, mode: str, **options) -> dict:
    model.mode = mode
    made = rec.call("make", "POST", "/api/documents", body={"recipe_key": "board", "meeting_ids": [meeting], "options": options}, record=False)  # fmt: skip
    return wait_for(rec, made.json()["id"], "ready")


def sign_in(base: str) -> tuple[Recorder, Recorder]:
    ada, eve = (Recorder(base, n.split()[0].lower()) for n, _ in PEOPLE.values())
    for rec, email in ((ada, "ada@example.test"), (eve, "eve@example.test")):
        rec.call("session", "GET", "/api/session", record=False)
        rec.call("login", "POST", "/api/session", body={"email": email, "password": PEOPLE[email][1]}, record=False)  # fmt: skip
    return ada, eve


def run(base: str, ids: dict, model: FakeModel) -> None:
    ada, eve = sign_in(base)
    ada.call("session", "GET", "/api/session", note="The caller; `csrf_token` is what every write sends in `X-CSRF-Token`.")  # fmt: skip

    # Slow first, so PATCH while writing is real.
    model.mode = "slow"
    made = ada.call("make-board", "POST", "/api/documents", body={"recipe_key": "board", "meeting_ids": [ids["a"]]}, note="Make a board: the same `POST /api/documents` as any draft, with the `board` recipe. 202, `output_type: \"board\"`, `blocks: []` until the worker has written it. MODEL FAKED (slow, so the next call sees `writing`).")  # fmt: skip
    doc = made.json()["id"]
    ada.call("edit-while-writing", "PATCH", f"/api/documents/{doc}/board", body={"revision": made.json()["updated_at"], "op": "delete_card", "card": 0}, note="Editing a board that is still being written: 409 `writing`.")  # fmt: skip
    wait_for(ada, doc, "ready")
    got = ada.call("get-board", "GET", f"/api/documents/{doc}", note="The board when ready. `blocks` are ONLY `column` blocks, each with `cards` (`kind` is `decision`, `action`, `question` or `note`; `owner` may be null; `sources` as in any draft: the server wrote each line source's `quote`). `checks` is the audit of the cards: the 120,000 naira setup fee is in no source (a `figure` flag), `[need: call date]` is a blank, both with `block_index` = the COLUMN's place. `updated_at` is the `revision` the next edit sends. The empty column `Parked` has `cards: []`.")  # fmt: skip
    rev = got.json()["updated_at"]

    def edit(
        name: str,
        body: dict,
        note: str,
        who: Recorder = ada,
        document: int = doc,
        revision: str | None = None,
    ):
        nonlocal rev
        sent = {"revision": revision or rev, **body}
        response = who.call(name, "PATCH", f"/api/documents/{document}/board", body=sent, note=note)
        if response.status_code == 200 and document == doc and who is ada:
            rev = response.json()["updated_at"]
        return response

    old = rev
    edit("move-card", {"op": "move_card", "card": 2, "to_column": 3, "index": 0}, "Drag, or the \"Move to column…\" menu, both send this. `card` numbers cards THROUGH the columns from 0 (card 2 is the third card of the first column: the setup fee); `to_column` is a column's place; `index` is the place inside the target column once the card has left it (0 here: the empty `Parked`). The answer is the WHOLE draft with the new `updated_at` (the next `revision`); `edited` is now true. The card kept its kind, owner and sources.")  # fmt: skip
    edit("move-card-within-a-column", {"op": "move_card", "card": 0, "to_column": 0, "index": 1}, "Reordering inside one column: `index` counts places in that column WITHOUT the card (so 1 is below the other remaining card).")  # fmt: skip
    edit("move-card-from-menu-to-end", {"op": "move_card", "card": 3, "to_column": 2, "index": 1}, "What the menu sends: to the END of the chosen column (`index` = the cards the column holds after the card has left). Card 3 is the second card of `Next steps` (`Confirm the kick-off call`); the menu moved it to the end of `Open questions`, which then held one card, so `index` is 1.")  # fmt: skip
    edit("rename-column", {"op": "rename_column", "column": 1, "title": "<b>Do next</b>"}, "Rename a column. Text is made plain: the markup is stripped (so is any link) and the name is cut at 100 characters.")  # fmt: skip
    edit("add-column", {"op": "add_column", "title": "Risks"}, "Add a column at the end (add `index` to put it elsewhere). At most 12 columns.")  # fmt: skip
    edit("add-card", {"op": "add_card", "column": 4, "text": "Check the contract with Legal", "kind": "action", "owner": "Ada"}, "Add a card to the end of a column (add `index` for another place). `kind` defaults to `note`, `owner` to none. A new card has `sources: []`. At most 30 cards in a column.")  # fmt: skip
    edit("edit-card", {"op": "edit_card", "card": 0, "text": "Onboarding costs 50,000 naira", "owner": "Daniel"}, "Change a card's text, owner and/or kind: send ONLY what changes; `owner: \"\"` clears the owner. The card keeps its sources.")  # fmt: skip
    edit("recolour", {"op": "recolour", "card": 1, "kind": "question"}, "Recolour: colour is a function of the kind, so recolouring a card gives it another kind.")  # fmt: skip
    edit("delete-card", {"op": "delete_card", "card": 0}, "Delete a card.")  # fmt: skip
    edit("delete-column", {"op": "delete_column", "column": 4}, "Delete a column and the cards in it. Not in the plan's list of operations; added because a column made by mistake could not otherwise be removed. The last column cannot be deleted.")  # fmt: skip

    after = ada.call("get-after-edits", "GET", f"/api/documents/{doc}", note="What was saved is what is read; `checks` are worked out again from the cards as they now stand (the figure flag went with its card), and `edited` is true.")  # fmt: skip
    assert after.json()["edited"] is True

    # ---- the refusals ----
    edit("stale-screen", {"op": "delete_card", "card": 0}, "A screen that is out of date: the `revision` is the `updated_at` from before the edits above. 409 `stale_board`; nothing changes. The page reloads the draft and starts again.", revision=old)  # fmt: skip
    edit("stale-index", {"op": "delete_card", "card": 40}, "A card or column that is not there (the revision is current, the number is not): 409 `stale_board` too.")  # fmt: skip
    edit("stale-column", {"op": "move_card", "card": 0, "to_column": 9, "index": 0}, "A column that is not there: 409 `stale_board`.")  # fmt: skip
    edit("title-empty", {"op": "rename_column", "column": 0, "title": "<i>  </i>"}, "A name that is nothing once made plain: 422 `title_empty`.")  # fmt: skip
    edit("card-empty", {"op": "add_card", "column": 0, "text": "   "}, "A card with no text: 422 `card_empty`.")  # fmt: skip
    ada.call("revision-missing", "PATCH", f"/api/documents/{doc}/board", body={"op": "delete_card", "card": 0}, note="No `revision`: 422 `revision_required`.")  # fmt: skip
    ada.call("revision-bad", "PATCH", f"/api/documents/{doc}/board", body={"revision": "yesterday", "op": "delete_card", "card": 0}, note="A `revision` that is not a time: 422 `revision_invalid`.")  # fmt: skip
    ada.call("body-unknown-operation", "PATCH", f"/api/documents/{doc}/board", body={"revision": rev, "op": "teleport", "card": 0}, note="An operation that does not exist: the ordinary 422 `validation_error` (the bodies are strict: an unknown field, a negative or text number, text over 2000 characters or an unknown `kind` are all refused this way).")  # fmt: skip
    ada.call("body-extra-field", "PATCH", f"/api/documents/{doc}/board", body={"revision": rev, "op": "delete_card", "card": 0, "sneaky": True}, note="A field that is not named in the operation: 422 `validation_error`.")  # fmt: skip
    ada.call("no-csrf", "PATCH", f"/api/documents/{doc}/board", body={"revision": rev, "op": "delete_card", "card": 0}, csrf=None, note="No CSRF token: 403 `csrf_rejected`.")  # fmt: skip
    ada.call("logged-out", "PATCH", f"/api/documents/{doc}/board", body={"revision": rev, "op": "delete_card", "card": 0}, csrf=None, record=False)  # fmt: skip
    eve.call("someone-elses-board", "PATCH", f"/api/documents/{doc}/board", body={"revision": rev, "op": "delete_card", "card": 0}, note="Another person's board: 404 `not_found`, never 403. Only the creator edits, and that stays true once the board is shared with a team (slice S4; not run live here).")  # fmt: skip
    ada.call("no-such-board", "PATCH", "/api/documents/999999/board", body={"revision": rev, "op": "delete_card", "card": 0}, note="A draft that does not exist: 404 `not_found`.")  # fmt: skip

    model.mode = "board"
    email = ada.call("make-email", "POST", "/api/documents", body={"recipe_key": "follow_up_email", "meeting_ids": [ids["b"]]}, record=False).json()  # fmt: skip
    wait_for(ada, email["id"], "ready")
    ada.call("not-a-board", "PATCH", f"/api/documents/{email['id']}/board", body={"revision": wait_for(ada, email["id"], "ready")["updated_at"], "op": "delete_card", "card": 0}, note="An email or any draft that is not a board: 409 `not_a_board`.")  # fmt: skip

    full = make_board(ada, model, ids["c"], "full")
    other = {"revision": full["updated_at"]}
    ada.call("board-full", "PATCH", f"/api/documents/{full['id']}/board", body={**other, "op": "add_column", "title": "One too many"}, note="A board that already has 12 columns: 422 `board_full`.")  # fmt: skip
    ada.call("column-full", "PATCH", f"/api/documents/{full['id']}/board", body={**other, "op": "add_card", "column": 0, "text": "One too many"}, note="A column that already has 30 cards: 422 `column_full` (also when a card is moved INTO it from another column).")  # fmt: skip
    single = make_board(ada, model, ids["d"], "single")
    ada.call("last-column", "PATCH", f"/api/documents/{single['id']}/board", body={"revision": single["updated_at"], "op": "delete_column", "column": 0}, note="The only column cannot be deleted: 422 `last_column`.")  # fmt: skip

    # A malformed reply never shows: a board with no card at all fails as `structure`.
    class NoCards(FakeModel):
        def _create(self, **kwargs):
            user = kwargs["messages"][1]["content"]
            m = int(user.split("Meeting id: ")[1].split("\n")[0])
            content = json.dumps({"title": "Empty", "blocks": [column("Nothing", [])]})
            del m
            return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=content), finish_reason="stop")], usage=SimpleNamespace(prompt_tokens=10, completion_tokens=10))  # fmt: skip

    generate._client = lambda: NoCards()
    bad = ada.call("make-empty-board", "POST", "/api/documents", body={"recipe_key": "board", "meeting_ids": [ids["a"]], "options": {"try": "empty"}}, record=False).json()  # fmt: skip
    failed = wait_for(ada, bad["id"], "failed")
    ada.call("get-failed-board", "GET", f"/api/documents/{bad['id']}", note="A model reply with no card in any column is not a board: the draft fails as `structure` and is never half shown. Editing it is 409 `not_ready` (see the edit-while-writing capture for `writing`).")  # fmt: skip
    ada.call("edit-failed-board", "PATCH", f"/api/documents/{bad['id']}/board", body={"revision": failed["updated_at"], "op": "delete_card", "card": 0}, note="Editing a board that failed: 409 `not_ready`.")  # fmt: skip

    # PDF and Word: the render model, built and rendered (the HTTP route is slice S2's).
    from app import export

    columns = blocks_mod.load_blocks(after.json()["blocks"])
    doc_model = board_mod.to_notes_document(after.json()["title"], columns, when=datetime.now(UTC))
    pdf = export.build_file(doc_model, "pdf")
    docx = export.build_file(doc_model, "docx")
    note_file(
        "board-as-pdf-and-word",
        "NOT an HTTP exchange: slice S2's `GET /api/documents/{id}/export?format=pdf|docx` is not on this branch. This is `board.to_notes_document` (columns as sections, cards as bullets with kind and owner) rendered by the existing `export.build_file`: what that route will hand back for a board once it calls it.",
        {
            "sections": [
                {"heading": s.heading, "blocks": [b.kind for b in s.blocks]}
                for s in doc_model.sections
            ],
            "meta": doc_model.meta,
            "pdf_bytes_start": pdf[:5].decode("latin-1"),
            "pdf_size_bytes": len(pdf),
            "docx_bytes_start": docx[:2].decode("latin-1"),
            "docx_size_bytes": len(docx),
        },
    )


def main() -> None:
    if not admin_url:
        sys.exit(__doc__)
    prepare_database()
    if serve_port is None:
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
    model = FakeModel()
    generate._client = lambda: model  # the worker's OpenAI client, replaced
    recipe_service.starter_for = {
        BOARD.key: BOARD,
        EMAIL.key: EMAIL,
    }.get  # slice S3's library is not here
    stop = threading.Event()
    worker = threading.Thread(target=recipe_service.run_loop, args=(stop,), kwargs={"idle_seconds": 0.2}, daemon=True)  # fmt: skip
    from app import db  # the worker's sessions

    db.SessionLocal.configure(bind=create_engine(scratch_url))
    worker.start()
    server, base = start_server(app, serve_port)
    engine = create_engine(scratch_url)
    try:
        ids = seed(engine)
        if serve_port is None:
            run(base, ids, model)
        else:
            ada, _eve = sign_in(base)
            made = make_board(ada, model, ids["a"], "board")
            full = make_board(ada, model, ids["b"], "full")
            print(
                f"serving on {base}; sign in as ada@example.test / {PEOPLE['ada@example.test'][1]}"
            )
            print(f"board {made['id']} (small), board {full['id']} (12 x 30 cards)")
            while True:
                time.sleep(3600)
    finally:
        stop.set()
        server.should_exit = True
        engine.dispose()


if __name__ == "__main__":
    main()
