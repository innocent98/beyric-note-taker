"""Drive the draft API of meeting recipes over real HTTP and save every exchange for the frontend
guide (`docs/fe-integration-guide-recipes.md`).

    PYTHONPATH=. python scripts/capture_recipes.py postgresql+psycopg://USER:PASS@HOST:PORT/postgres

The argument is any Postgres server the script may create a scratch database on. It runs the real
migrations there, starts the real app under uvicorn, starts the real worker loop
(`app.recipes.service.run_loop`) in a thread and signs in as placeholder users. Each exchange is
written to `docs/fe-captures/recipes/NN-name.json`.

What is real: the app, auth, CSRF, validation, ownership (404 for another person's draft), the
database, migrations, the queue and worker claim, the daily cap and the ledger, the checks on the
model's reply, the number audit and every error body.

What is NOT real, and is labelled where it matters:
* **The model.** `FakeModel` stands in for the OpenAI client (no key, no network). It returns the
  strict-JSON shape `app/recipes/generate.py` asks for, citing real line numbers it is shown; a real
  model's wording will differ, the shape of every response body will not.
* **The starter recipes.** `app/recipes/library.py` belongs to slice S3 and is not on this branch, so
  the script registers one starter, `follow_up_email`, under the key the plan names. A recipe row
  (`recipe_id`) is real.
* **The daily cap** is 6 here (`RECIPE_DAILY_LIMIT=6`); the default is 20.
* **Not run live:** the `plan_inactive` refusal (needs `FEATURE_GATES_MODE=enforce` and a person with
  no plan; shape from `tests/test_feature_gates.py` and `tests/test_recipe_jobs.py`).
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
OUT = ROOT / "docs" / "fe-captures" / "recipes"
DB_NAME = "notetaker_capture_recipes"
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
os.environ["RECIPE_DAILY_LIMIT"] = "6"
os.environ.setdefault("APP_ENV", "development")

import httpx  # noqa: E402
import uvicorn  # noqa: E402
from alembic import command  # noqa: E402
from alembic.config import Config  # noqa: E402
from sqlalchemy import create_engine, select, text, update  # noqa: E402
from sqlalchemy.orm import Session  # noqa: E402

from app import accounts  # noqa: E402
from app.config import Settings  # noqa: E402
from app.main import create_app  # noqa: E402
from app.models import AiRun, AppState, Decision, Meeting, Recipe, Template, User  # noqa: E402
from app.passwords import hash_password  # noqa: E402
from app.recipes import generate  # noqa: E402
from app.recipes import service as recipe_service  # noqa: E402

STARTER = SimpleNamespace(
    key="follow_up_email",
    name="Follow-up email",
    instruction="Write a short, friendly follow-up email to the client.",
    output_type="email",
    scope="one",
    uses_brief=True,
)

NOTES = (
    "## Summary\nOnboarding for the Daniel Okoro's team was agreed for 50,000 naira.\n\n"
    "## Decisions\n- Start on 2026-12-01.\n\n## Action items\n- Ada: send the plan by Friday.\n"
)
TRANSCRIPT = (
    "[Ada Lovelace]: Thanks for joining, Daniel.\n"
    "[Daniel Okoro]: We would like the onboarding for 50,000 naira, starting 2026-12-01.\n"
    "[Ada Lovelace]: Agreed. I will send the plan by Friday."
)


class FakeModel:
    """The OpenAI client, replaced. `mode` says how it answers the next call."""

    def __init__(self) -> None:
        self.mode = "good"
        self.calls: list[dict] = []
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self._create))

    def _create(self, **kwargs):
        self.calls.append(kwargs)
        meeting_id = int(kwargs["messages"][1]["content"].split("Meeting id: ")[1].split("\n")[0])
        if self.mode == "slow":
            time.sleep(2.0)
        if self.mode == "garbage":
            content = "this is not JSON"
        else:
            blank = {
                "type": "paragraph",
                "level": None,
                "items": None,
                "columns": None,
                "rows": None,
                "title": None,
                "cards": None,
            }
            content = json.dumps(
                {
                    "title": "Thanks for today, next steps on the onboarding",
                    "blocks": [
                        {**blank, "text": "Subject: Thanks for today, next steps on the onboarding", "sources": []},
                        {**blank, "text": "Hi Daniel, thank you for your time today.", "sources": [{"kind": "line", "meeting_id": meeting_id, "line": 1, "quote": None, "ref": None}]},
                        {**blank, "text": "As agreed, the onboarding is 50,000 naira and starts on 2026-12-01. The setup fee is 120,000 naira. Our first call is [need: call date].", "sources": [{"kind": "line", "meeting_id": meeting_id, "line": 2, "quote": None, "ref": None}, {"kind": "line", "meeting_id": meeting_id, "line": 99, "quote": None, "ref": None}]},
                        {**blank, "type": "bullets", "text": None, "sources": None, "items": [{"text": "Ada sends the plan by Friday.", "sources": [{"kind": "line", "meeting_id": meeting_id, "line": 3, "quote": None, "ref": None}]}]},
                    ],
                }
            )  # fmt: skip
        return SimpleNamespace(
            choices=[
                SimpleNamespace(message=SimpleNamespace(content=content), finish_reason="stop")
            ],
            usage=SimpleNamespace(prompt_tokens=2100, completion_tokens=260),
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

    def call(self, name, method, path, *, body=None, csrf="session", note=""):
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
        Recorder.count += 1
        sent = {k: (self.mask(v) if k == "X-CSRF-Token" else v) for k, v in headers.items()}
        if had_cookie:
            sent["Cookie"] = "session=<cookie>"
        kept = {k: response.headers[k] for k in ("content-type", "allow") if k in response.headers}
        shown = json.loads(json.dumps(data))
        if isinstance(shown, dict) and "csrf_token" in shown:
            shown["csrf_token"] = self.mask(shown["csrf_token"])
        record = {
            "note": note,
            "client": self.who,
            "request": {"method": method, "path": path, "headers": sent, "body": hide_tokens(body)},
            "response": {"status": response.status_code, "headers": kept, "body": shown},
        }
        (OUT / f"{Recorder.count:02d}-{name}.json").write_text(
            json.dumps(record, indent=2, ensure_ascii=False) + "\n"
        )
        print(f"{Recorder.count:02d}-{name:44} {method:6} {path:24} -> {response.status_code}")
        return response


def note_file(name: str, note: str, content) -> None:
    Recorder.count += 1
    (OUT / f"{Recorder.count:02d}-{name}.json").write_text(
        json.dumps({"note": note, "content": content}, indent=2, ensure_ascii=False, default=str)
        + "\n"
    )
    print(f"{Recorder.count:02d}-{name}")


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


def add_meeting(session, owner_id, template_id, title, **fields):
    values = {
        "status": "ready",
        "step": "done",
        "notes": NOTES,
        "transcript": TRANSCRIPT,
        "speaker_transcript": TRANSCRIPT,
        **fields,
    }
    meeting = Meeting(owner_user_id=owner_id, url="https://meet.google.com/abc-defg-hij", platform="google_meet", native_id="abc-defg-hij", template_id=template_id, title=title, created_at=datetime(2026, 11, 20, 10, 0, tzinfo=UTC), recorded_seconds=1800, source="calendar", **values)  # fmt: skip
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
        ada, eve = ids["ada@example.test"], ids["eve@example.test"]
        template_id = session.scalars(select(Template.id).where(Template.owner_user_id == ada, Template.name == "General")).one()  # fmt: skip
        eve_template = session.scalars(select(Template.id).where(Template.owner_user_id == eve, Template.name == "General")).one()  # fmt: skip
        ids["meeting"] = add_meeting(session, ada, template_id, "Onboarding call").id
        ids["second"] = add_meeting(session, ada, template_id, "Kick-off").id
        ids["third"] = add_meeting(session, ada, template_id, "Review").id
        ids["doomed"] = add_meeting(session, ada, template_id, "Doomed meeting").id
        ids["processing"] = add_meeting(session, ada, template_id, "Still processing", status="processing", notes=None, transcript=None, speaker_transcript=None).id  # fmt: skip
        ids["eves"] = add_meeting(session, eve, eve_template, "Eve's private meeting").id
        session.add(Decision(meeting_id=ids["meeting"], text="Start on 2026-12-01.", position=0, fingerprint="d1"))  # fmt: skip
        recipe = Recipe(owner_user_id=ada, name="Short thank-you", instruction="Thank the client in two lines.", output_type="email")  # fmt: skip
        session.add(recipe)
        session.commit()
        ids["recipe"] = recipe.id
    return ids


def wait_for(rec: Recorder, doc_id: int, status: str) -> None:
    for _ in range(100):
        got = rec.http.get(f"/api/documents/{doc_id}").json()
        if got["status"] == status:
            return
        time.sleep(0.1)
    raise SystemExit(f"draft {doc_id} never became {status}")


def ledger(engine) -> list[dict]:
    with Session(engine) as session:
        return [
            {
                "kind": r.kind,
                "model": r.model,
                "prompt_version": r.prompt_version,
                "input_tokens": r.input_tokens,
                "output_tokens": r.output_tokens,
                "status": r.status,
                "error": r.error,
                "has_user": r.user_id is not None,
                "meeting_id": r.meeting_id,
            }  # fmt: skip
            for r in session.scalars(select(AiRun).where(AiRun.kind == "recipe").order_by(AiRun.id))
        ]


def run(base: str, ids: dict, engine, model: FakeModel) -> None:
    ada, eve = (Recorder(base, n.split()[0].lower()) for n, _ in PEOPLE.values())
    for rec, email in ((ada, "ada@example.test"), (eve, "eve@example.test")):
        rec.call("session", "GET", "/api/session")
        rec.call(
            "login", "POST", "/api/session", body={"email": email, "password": PEOPLE[email][1]}
        )
    anon = Recorder(base, "visitor")
    anon.call("session", "GET", "/api/session")
    anon.call("create-logged-out", "POST", "/api/documents", body={"recipe_key": "follow_up_email", "meeting_ids": [ids["meeting"]]}, note="No login: 401.")  # fmt: skip

    m = ids["meeting"]
    # Slow model first, so the writing state and PATCH-while-writing are real.
    model.mode = "slow"
    made = ada.call("create", "POST", "/api/documents", body={"recipe_key": "follow_up_email", "meeting_ids": [m], "options": {"tone": "friendly", "recipient": "Daniel"}}, note="Make a draft from a starter. 202 with `status: \"writing\"`, `blocks: []`; nothing has been written yet. MODEL FAKED (slow, so the next calls see `writing`).")  # fmt: skip
    doc = made.json()["id"]
    ada.call("create-again-same-request", "POST", "/api/documents", body={"recipe_key": "follow_up_email", "meeting_ids": [m], "options": {"tone": "friendly", "recipient": "Daniel"}}, note="The same request while that draft is still being written (a double press or a second tab): the SAME draft comes back (same `id`), no second draft and no second count against the day's 20.")  # fmt: skip
    ada.call("get-writing", "GET", f"/api/documents/{doc}", note="Poll every 2 seconds while `status` is `writing`: `blocks` is empty, `checks` empty.")  # fmt: skip
    ada.call("patch-while-writing", "PATCH", f"/api/documents/{doc}", body={"blocks": [{"type": "paragraph", "text": "x", "sources": []}]}, note="Editing a draft that is still being written: 409 `writing`.")  # fmt: skip
    wait_for(ada, doc, "ready")
    ready = ada.call("get-ready", "GET", f"/api/documents/{doc}", note="Ready. Look at `checks`: the model wrote a setup fee of 120,000 naira that is nowhere in the meeting, so it is a `figure` flag; `[need: call date]` is a blank. 50,000 naira and 2026-12-01 are in the meeting so they are not flagged. The model also cited line 99, which does not exist: that source was dropped. Each line source's `quote` is written by the server from the real line, not by the model. Note `title` is the model's, replaced the recipe name.")  # fmt: skip
    blocks = ready.json()["blocks"]

    ada.call("list", "GET", "/api/documents", note="Own drafts, newest first; `meeting_ids` as made.")  # fmt: skip
    ada.call("list-for-meeting", "GET", f"/api/documents?meeting_id={m}", note="Only drafts made from that meeting.")  # fmt: skip
    ada.call("list-other-meeting", "GET", f"/api/documents?meeting_id={ids['second']}", note="A meeting with no drafts: an empty list.")  # fmt: skip

    edited = [
        {"type": "heading", "level": 1, "text": "Next steps"},
        {"type": "paragraph", "text": blocks[2]["text"].replace("120,000", "50,000"), "sources": blocks[2]["sources"] + [{"kind": "line", "meeting_id": m, "line": 3, "quote": None, "ref": None}]},
        {"type": "table", "columns": ["Step", "Owner"], "rows": [[{"text": "Send the plan", "sources": []}, {"text": "Ada", "sources": []}]]},
    ]  # fmt: skip
    ada.call("patch", "PATCH", f"/api/documents/{doc}", body={"blocks": edited, "title": "Next steps"}, note="Save an edit: send EVERY block as it now stands. `edited` becomes true; the figure that was wrong is now right so its flag is gone, the blank is still reported, and the new `blocks[1].sources` has only the one source the draft already had (a line the draft did not cite, line 3, is dropped: an edit may keep sources, never add them). HTML is stripped. `block_index` in `checks` counts the NEW blocks.")  # fmt: skip
    ada.call("patch-bad-table", "PATCH", f"/api/documents/{doc}", body={"blocks": [{"type": "table", "columns": ["A", "B"], "rows": [[{"text": "x", "sources": []}]]}]}, note="A table row that does not match its columns: 422 `blocks_invalid`. Nothing is saved.")  # fmt: skip
    ada.call("patch-empty", "PATCH", f"/api/documents/{doc}", body={"blocks": []}, note="No blocks at all: 422 `blocks_invalid`.")  # fmt: skip
    ada.call("patch-bad-body", "PATCH", f"/api/documents/{doc}", body={}, note="A body that is not the shape: the ordinary 422 `validation_error` with `fields`.")  # fmt: skip
    ada.call("patch-no-csrf", "PATCH", f"/api/documents/{doc}", body={"blocks": []}, csrf=None, note="No CSRF token: 403 `csrf_rejected`.")  # fmt: skip
    ada.call("get-after-edit", "GET", f"/api/documents/{doc}", note="What was saved is what is read, and `checks` is worked out again from it.")  # fmt: skip

    # stale and sources_available
    with Session(engine) as session:
        session.execute(
            update(Meeting).where(Meeting.id == m).values(notes=NOTES + "- A new point.\n")
        )
        session.commit()
    ada.call("get-stale", "GET", f"/api/documents/{doc}", note="The meeting's notes changed after the draft was made: `stale` is true. Label the draft \"older than the meeting\"; regenerating (a new `POST`) makes a new draft.")  # fmt: skip

    # a recipe row, two meetings (one of them a meeting that is then deleted)
    model.mode = "good"
    row_made = ada.call("create-with-recipe-row", "POST", "/api/documents", body={"recipe_id": ids["recipe"], "meeting_ids": [ids["doomed"]], "use_brief": False}, note="Make a draft from the person's own recipe row (`recipe_id`) instead of a starter. `recipe_key` is null in the answer.")  # fmt: skip
    doomed = row_made.json()["id"]
    wait_for(ada, doomed, "ready")
    with Session(engine) as session:
        session.execute(text("delete from meetings where id = :m"), {"m": ids["doomed"]})
        session.commit()
    ada.call("get-meeting-deleted", "GET", f"/api/documents/{doomed}", note="The only meeting it was made from was deleted: the draft stays, `meeting_ids` is empty and `sources_available` is false (show the source links as \"open the meeting\" is not possible). The server removes single-meeting drafts when their meeting is deleted in the integration slice S7; until then this is the state.")  # fmt: skip

    # failures and refusals
    model.mode = "garbage"
    bad = ada.call("create-model-fails", "POST", "/api/documents", body={"recipe_key": "follow_up_email", "meeting_ids": [ids["second"]]}, note="A draft the model will fail (it answers with something that is not JSON). MODEL FAKED.")  # fmt: skip
    wait_for(ada, bad.json()["id"], "failed")
    ada.call("get-failed", "GET", f"/api/documents/{bad.json()['id']}", note="`status: \"failed\"` with a `failure_code` and a plain `failure_message` to show. A failed attempt still counts against the day's 20.")  # fmt: skip
    ada.call("patch-failed", "PATCH", f"/api/documents/{bad.json()['id']}", body={"blocks": [{"type": "paragraph", "text": "x", "sources": []}]}, note="A failed draft cannot be edited: 409 `not_ready`.")  # fmt: skip
    model.mode = "good"
    ada.call("create-no-recipe", "POST", "/api/documents", body={"meeting_ids": [m]}, note="Neither `recipe_key` nor `recipe_id`: 422 `recipe_invalid`.")  # fmt: skip
    ada.call("create-both-recipes", "POST", "/api/documents", body={"recipe_key": "follow_up_email", "recipe_id": ids["recipe"], "meeting_ids": [m]}, note="Both: 422 `recipe_invalid`.")  # fmt: skip
    ada.call("create-no-meetings", "POST", "/api/documents", body={"recipe_key": "follow_up_email", "meeting_ids": []}, note="No meetings: the ordinary 422 `validation_error` with `fields`.")  # fmt: skip
    ada.call("create-unknown-recipe", "POST", "/api/documents", body={"recipe_key": "no_such_recipe", "meeting_ids": [m]}, note="No such recipe: 404 `not_found`.")  # fmt: skip
    ada.call("create-other-persons-meeting", "POST", "/api/documents", body={"recipe_key": "follow_up_email", "meeting_ids": [ids["eves"]]}, note="A meeting the caller cannot read: 404 `not_found`, the same as a meeting that does not exist.")  # fmt: skip
    ada.call("create-not-ready", "POST", "/api/documents", body={"recipe_key": "follow_up_email", "meeting_ids": [ids["processing"]]}, note="A meeting whose notes are not written yet: 409 `not_ready`.")  # fmt: skip
    ada.call("create-no-csrf", "POST", "/api/documents", body={"recipe_key": "follow_up_email", "meeting_ids": [m]}, csrf=None, note="No CSRF token: 403 `csrf_rejected`.")  # fmt: skip
    # the header is sent by hand: Recorder.call sends only the CSRF header
    ada.http.headers["X-Team-Id"] = "999999"
    ada.call("create-wrong-team-header", "POST", "/api/documents", body={"recipe_key": "follow_up_email", "meeting_ids": [m]}, note="`X-Team-Id` naming a team the caller is not in: 404 `not_found`.")  # fmt: skip
    del ada.http.headers["X-Team-Id"]

    # ownership
    eve.call("get-other-persons", "GET", f"/api/documents/{doc}", note="Another person's draft: 404 `not_found` (never 403).")  # fmt: skip
    eve.call("patch-other-persons", "PATCH", f"/api/documents/{doc}", body={"blocks": [{"type": "paragraph", "text": "x", "sources": []}]}, note="Same for editing: 404.")  # fmt: skip
    eve.call("delete-other-persons", "DELETE", f"/api/documents/{doc}", note="Same for discarding: 404.")  # fmt: skip
    eve.call("list-other-persons", "GET", "/api/documents", note="Eve's list has none of Ada's drafts.")  # fmt: skip

    # the daily cap (6 here): fill it, then one more
    for n in range(10):
        got = ada.http.post("/api/documents", json={"recipe_key": "follow_up_email", "meeting_ids": [ids["third"]], "options": {"n": n}}, headers={"X-CSRF-Token": ada.csrf})  # fmt: skip
        if got.status_code == 429:
            break
    ada.call("create-over-the-cap", "POST", "/api/documents", body={"recipe_key": "follow_up_email", "meeting_ids": [ids["third"]], "options": {"n": 99}}, note="The day's limit (6 in this capture, 20 by default) reached: 429 `recipe_limit`. Every attempt that reached the model counts, failed ones and regenerations included. A refused request counts for nothing. Show the message; it clears itself a day after the oldest attempt.")  # fmt: skip

    ada.call("delete", "DELETE", f"/api/documents/{doc}", note="Discard: 204, no body. It does not give back a slot of the day's 20.")  # fmt: skip
    ada.call("get-after-delete", "GET", f"/api/documents/{doc}", note="Gone: 404.")  # fmt: skip
    ada.call("delete-again", "DELETE", f"/api/documents/{doc}", note="Discarding again: 404.")  # fmt: skip
    ada.call("get-nonsense-id", "GET", "/api/documents/99999999999", note="An id too large to exist: 404, not an error.")  # fmt: skip
    note_file("ledger-after", "The ledger rows these drafts left (`ai_runs`, kind `recipe`): one per request that was not a double press, whatever its outcome. No draft text is stored there. A failed attempt shows its error class name only.", ledger(engine))  # fmt: skip


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
    model = FakeModel()
    generate._client = lambda: model  # the worker's OpenAI client, replaced
    recipe_service.starter_for = {
        STARTER.key: STARTER
    }.get  # slice S3's library is not on this branch
    stop = threading.Event()
    worker = threading.Thread(target=recipe_service.run_loop, args=(stop,), kwargs={"idle_seconds": 0.2}, daemon=True)  # fmt: skip
    from app import db  # the worker's sessions

    db.SessionLocal.configure(bind=create_engine(scratch_url))
    worker.start()
    server, base = start_server(app)
    engine = create_engine(scratch_url)
    try:
        run(base, seed(engine), engine, model)
    finally:
        stop.set()
        server.should_exit = True
        engine.dispose()


if __name__ == "__main__":
    main()
