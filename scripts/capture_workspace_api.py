"""Drive the Decisions and Review API over real HTTP and save every exchange for the frontend guide.

    OPENAI_API_KEY=... python scripts/capture_workspace_api.py postgresql+psycopg://USER:PASS@HOST:PORT/postgres

The argument is any Postgres server the script may create a scratch database on. It runs the real
migrations there, starts the real app under uvicorn, and signs in as two placeholder users. Each
exchange is written to `docs/fe-captures/ai-workspace-w1/NN-name.json`, so every claim in
`docs/fe-integration-guide-ai-workspace.md` can be re-derived by running this again.

What is real: the app, auth, CSRF, validation, the database, migrations, serialisation, every
error, **the worker's extraction step** (`workspace.process_next`, the very function the worker
calls) with **the real model call** to `gpt-6-luna`, the failure states (a model name that does not
exist makes OpenAI refuse for real), the backfill, and "rewrite the notes" (which calls the real
notes model). What is not: the recording, transcription and speaker steps, and the bot. The meeting
rows are inserted as the pipeline leaves them (`status ready`, notes and speaker transcript in
place, `extraction_status pending`). The transcripts are the synthetic ones of the AI1 eval
(`tests/eval/extraction/`) and the notes are what luna wrote for them in that run
(`docs/spike/ai-extraction-eval/`). Nobody's real meeting is involved.

Secrets in the files are masked: CSRF tokens become `<csrf-A>`, `<csrf-B>` in order of first
appearance and cookies `<cookie>`. The OpenAI key is never written anywhere.
"""

import json
import os
import shutil
import socket
import sys
import threading
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "docs" / "fe-captures" / "ai-workspace-w1"
EVAL = ROOT / "tests" / "eval" / "extraction"
NOTES = ROOT / "docs" / "spike" / "ai-extraction-eval"
DB_NAME = "notetaker_capture_workspace"
ADMIN_EMAIL = "admin@example.test"  # created by migration 0006 from these (placeholders)
ADMIN_PASSWORD = "-".join(["capture", "admin", "placeholder"])
ADA = "ada@example.test"  # placeholders, not real people
ADA_PASSWORD = "-".join(["ada", "chosen", "password", "1"])
BEN = "ben@example.test"
BEN_PASSWORD = "-".join(["ben", "chosen", "password", "2"])

admin_url = sys.argv[1] if len(sys.argv) > 1 else ""
scratch_url = urlparse(admin_url)._replace(path=f"/{DB_NAME}").geturl()
os.environ["DATABASE_URL"] = scratch_url  # read by app.db when it is imported below
os.environ["OWNER_EMAIL"] = ADMIN_EMAIL
os.environ["OWNER_PASSWORD"] = ADMIN_PASSWORD
os.environ.setdefault("APP_ENV", "development")

import httpx  # noqa: E402
import uvicorn  # noqa: E402
from alembic import command  # noqa: E402
from alembic.config import Config  # noqa: E402
from sqlalchemy import create_engine, select, text  # noqa: E402
from sqlalchemy.orm import Session  # noqa: E402

from app import workspace  # noqa: E402
from app.config import Settings, get_settings  # noqa: E402
from app.main import create_app  # noqa: E402
from app.models import AiRun, Meeting, Template, User  # noqa: E402
from app.passwords import hash_password  # noqa: E402
from app.services import get_vexa  # noqa: E402


class Recorder:
    """A logical client: a cookie jar, its CSRF token, and a numbered file for each call."""

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
        """`csrf`: "session" sends the current token, None sends none, any other string is sent."""
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
        kept = {
            k: response.headers[k]
            for k in ("content-type", "cache-control", "allow")
            if k in response.headers
        }
        shown = json.loads(json.dumps(data))
        if isinstance(shown, dict) and "csrf_token" in shown:
            shown["csrf_token"] = self.mask(shown["csrf_token"])
        record = {
            "note": note,
            "client": self.who,
            "request": {"method": method, "path": path, "headers": sent, "body": body},
            "response": {"status": response.status_code, "headers": kept, "body": shown},
        }
        (OUT / f"{Recorder.count:02d}-{name}.json").write_text(
            json.dumps(record, indent=2, ensure_ascii=False) + "\n"
        )
        print(f"{Recorder.count:02d}-{name:46} {method:6} {path:40} -> {response.status_code}")
        return response


def note_file(name: str, note: str, content) -> None:
    """Save something that is not an HTTP exchange (a database row, a script step)."""
    Recorder.count += 1
    record = {"note": note, "content": content}
    (OUT / f"{Recorder.count:02d}-{name}.json").write_text(
        json.dumps(record, indent=2, ensure_ascii=False, default=str) + "\n"
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


def make_user(session: Session, email: str, password: str, name: str) -> Template:
    user = User(
        email=email,
        name=name,
        password_hash=hash_password(password),
        email_verified_at=datetime.now(UTC),
    )
    session.add(user)
    session.flush()
    template = Template(owner_user_id=user.id, name="General", instructions="Write notes.")
    session.add(template)
    session.commit()
    return template


def add_meeting(session, template, case, notes_file, *, platform, native_id, days_ago, **fields):
    transcript = (EVAL / f"{case}.txt").read_text().strip()
    meeting = Meeting(
        owner_user_id=template.owner_user_id,
        url="https://example.test/meeting",
        platform=platform,
        native_id=native_id,
        template_id=template.id,
        status="ready",
        step="done",
        transcript=transcript,
        speaker_transcript=transcript,
        notes=(NOTES / f"{notes_file}.notes.md").read_text(),
        created_at=datetime.now(UTC) - timedelta(days=days_ago),
        **fields,
    )
    session.add(meeting)
    session.flush()
    return meeting


def run(base: str, engine) -> None:
    anon = Recorder(base, "visitor")
    ada, ben = Recorder(base, "ada"), Recorder(base, "ben")
    with Session(engine) as session:
        ada_template = make_user(session, ADA, ADA_PASSWORD, "Ada Example")
        ben_template = make_user(session, BEN, BEN_PASSWORD, "Ben Example")
        a = add_meeting(
            session, ada_template, "names_and_terms", "names_and_terms",
            platform="google_meet", native_id="nat-aaaa-bbb", days_ago=3,
        )  # fmt: skip
        b = add_meeting(
            session, ada_template, "mention_is_not_owner", "mention_is_not_owner",
            platform="zoom", native_id="81234567890", days_ago=2,
        )  # fmt: skip
        c = add_meeting(
            session, ada_template, "vague_timing", "vague_timing",
            platform="google_meet", native_id="vag-cccc-ddd", days_ago=1,
        )  # fmt: skip
        theirs = add_meeting(
            session, ben_template, "names_and_terms", "names_and_terms",
            platform="google_meet", native_id="ben-eeee-fff", days_ago=1,
        )  # fmt: skip
        for meeting in (a, b, c, theirs):
            workspace.mark_pending(meeting)  # as the pipeline does in the commit that sets `ready`
        session.commit()
        ids = dict(a=a.id, b=b.id, c=c.id, theirs=theirs.id)

    anon.call("session-anonymous", "GET", "/api/session", note="Logged out: `badges` is null.")
    anon.call("decisions-logged-out", "GET", "/api/decisions", note="No login: 401.")
    ada.call("session-anonymous-ada", "GET", "/api/session")
    ada.call(
        "login", "POST", "/api/session", body={"email": ADA, "password": ADA_PASSWORD},
        note="After login `badges.review_open` is 0: nothing has been extracted yet.",
    )  # fmt: skip
    ben.call("session-anonymous-ben", "GET", "/api/session")
    ben.call("login-ben", "POST", "/api/session", body={"email": BEN, "password": BEN_PASSWORD})
    ada.call(
        "meeting-before", "GET", f"/api/meetings/{ids['a']}",
        note="Notes are ready, extraction not run yet: `extraction_status` is `pending`.",
    )  # fmt: skip
    ada.call(
        "decisions-before", "GET", "/api/decisions",
        note="Nothing read from the notes yet: an empty list, not an error.",
    )  # fmt: skip
    ada.call("review-before", "GET", "/api/review", note="Same for Review.")

    # --- the worker's step, the real function and the real model ---
    for _ in range(10):
        with Session(engine) as session:
            if workspace.process_next(session) is None:
                break
    with Session(engine) as session:
        runs = session.scalars(select(AiRun).order_by(AiRun.id)).all()
        note_file(
            "worker-ai-runs",
            "`ai_runs` after the worker ran `workspace.process_next` until nothing was due. One "
            "real call per meeting with a Please check item; a meeting with none makes no call.",
            [
                dict(
                    meeting_id=r.meeting_id, kind=r.kind, model=r.model, status=r.status,
                    prompt_version=r.prompt_version, input_tokens=r.input_tokens,
                    output_tokens=r.output_tokens, error=r.error,
                )
                for r in runs
            ],
        )  # fmt: skip
    ada.call("meeting-after", "GET", f"/api/meetings/{ids['a']}", note="Now `done`.")
    ada.call("meeting-after-no-call", "GET", f"/api/meetings/{ids['c']}", note="Nothing to check.")

    # --- Decisions ---
    ada.call("decisions", "GET", "/api/decisions", note="Newest meeting first, grouped by meeting.")
    ada.call("decisions-search-text", "GET", "/api/decisions?q=metrika")
    ada.call(
        "decisions-search-title", "GET", "/api/decisions?q=81234567890",
        note="A title (meeting code) match shows all of that meeting's decisions.",
    )  # fmt: skip
    ada.call("decisions-search-none", "GET", "/api/decisions?q=zebra")
    page = ada.call(
        "decisions-page-1", "GET", "/api/decisions?limit=1",
        note="`limit` counts meetings. `next_before_id` is the cursor.",
    ).json()  # fmt: skip
    ada.call(
        "decisions-page-2", "GET", f"/api/decisions?limit=1&before_id={page['next_before_id']}"
    )
    ada.call("decisions-invalid-limit", "GET", "/api/decisions?limit=0")
    ada.call("decisions-invalid-limit-high", "GET", "/api/decisions?limit=51")
    first = page["groups"][0]["decisions"][0]["id"]
    ada.call("decision-hide", "POST", f"/api/decisions/{first}/hide", note="204, no body.")
    ada.call("decision-hide-again", "POST", f"/api/decisions/{first}/hide", note="Same: 204.")
    ada.call("decisions-after-hide", "GET", "/api/decisions?limit=1")
    with Session(engine) as session:
        other = session.scalars(
            text_select("decisions", ids["theirs"])
        ).first()  # a decision of Ben's meeting
    ada.call(
        "decision-hide-foreign", "POST", f"/api/decisions/{other}/hide",
        note="Someone else's decision: 404, never 403.",
    )  # fmt: skip
    ada.call("decision-hide-unknown", "POST", "/api/decisions/999999/hide")
    ada.call("decision-hide-no-csrf", "POST", f"/api/decisions/{first}/hide", csrf=None)
    ada.call("decision-hide-bad-id", "POST", "/api/decisions/abc/hide", note="Not a number: 404.")

    # --- Review ---
    ada.call(
        "session-badges", "GET", "/api/session", note="`badges.review_open` counts open items."
    )
    review = ada.call("review-open", "GET", "/api/review").json()
    ada.call("review-invalid-state", "GET", "/api/review?state=all")
    item = next((i for i in review["items"] if i["heard_as"]), review["items"][0])
    ada.call("review-check", "POST", f"/api/review/{item['id']}/check")
    ada.call("review-check-again", "POST", f"/api/review/{item['id']}/check",
             note="Checking again keeps the first `checked_at`.")  # fmt: skip
    ada.call("review-checked", "GET", "/api/review?state=checked")
    ada.call("review-uncheck", "POST", f"/api/review/{item['id']}/uncheck")
    ada.call(
        "review-vocabulary-invalid", "POST", f"/api/review/{item['id']}/vocabulary",
        body={"term": "  "}, note="400 and nothing changes: the item stays open.",
    )  # fmt: skip
    ada.call(
        "review-vocabulary", "POST", f"/api/review/{item['id']}/vocabulary",
        body={"term": item["heard_as"] or "Metrika"},
        note="201: the term is in the vocabulary and the item is checked.",
    )  # fmt: skip
    ada.call("vocabulary-after", "GET", "/api/vocabulary")
    ada.call(
        "review-vocabulary-duplicate", "POST", f"/api/review/{item['id']}/vocabulary",
        body={"term": item["heard_as"] or "Metrika"},
        note="409 `duplicate_term`; the item stays checked.",
    )  # fmt: skip
    ada.call(
        "review-checked-after-duplicate", "GET", "/api/review?state=checked",
        note="After the 409 the item is still in the checked list.",
    )  # fmt: skip
    with Session(engine) as session:
        foreign = session.scalars(text_select("review_items", ids["theirs"])).first()
    for action, body in (("check", None), ("uncheck", None), ("vocabulary", {"term": "x"})):
        ada.call(f"review-{action}-foreign", "POST", f"/api/review/{foreign}/{action}", body=body)
    ada.call("review-check-no-csrf", "POST", f"/api/review/{item['id']}/check", csrf=None)
    ada.call("session-badges-after", "GET", "/api/session")

    # --- states: rewrite the notes, a failed call, an old meeting ---
    with Session(engine) as session:
        before_rewrite = session.execute(
            text("select id, text from decisions where meeting_id = :m order by position limit 1"),
            {"m": ids["a"]},
        ).one()
    ada.call(
        "decision-hide-before-rewrite", "POST", f"/api/decisions/{before_rewrite.id}/hide",
        note="Hide a decision of the meeting whose notes are about to be rewritten.",
    )  # fmt: skip
    ada.call(
        "regenerate", "POST", f"/api/meetings/{ids['a']}/regenerate",
        body={"template_id": ada_template_id(engine)},
        note="Rewriting the notes (a real call to the notes model) queues the extraction again.",
    )  # fmt: skip
    with Session(engine) as session:
        workspace.process_next(session)
    ada.call("decisions-after-rewrite", "GET", "/api/decisions")
    with Session(engine) as session:
        rows = session.execute(
            text(
                "select id, text, hidden_at is not null as hidden from decisions"
                " where meeting_id = :m order by position"
            ),
            {"m": ids["a"]},
        ).all()
    note_file(
        "decisions-of-the-rewritten-meeting",
        f"Decision {before_rewrite.id} (hidden before the rewrite) and the decisions of the same "
        "meeting after it. A row is matched by its words: the rewritten notes either still say it "
        "(the row is kept, still hidden) or word it differently (a new row, visible).",
        dict(
            hidden_before=dict(id=before_rewrite.id, text=before_rewrite.text),
            after=[dict(r._mapping) for r in rows],
        ),
    )

    os.environ["EXTRACT_MODEL"] = "no-such-model-w1"
    get_settings.cache_clear()
    with Session(engine) as session:
        failing = add_meeting(
            session, session.get(Template, ada_template_id(engine)), "names_and_terms",
            "names_and_terms", platform="google_meet", native_id="fai-gggg-hhh", days_ago=0,
        )  # fmt: skip
        workspace.mark_pending(failing)
        session.commit()
        failing_id = failing.id
    clock = datetime.now(UTC)
    for attempt in (1, 2, 3):
        with Session(engine) as session:
            workspace.process_next(session, clock + timedelta(hours=attempt))
        ada.call(
            f"meeting-extraction-attempt-{attempt}", "GET", f"/api/meetings/{failing_id}",
            note="OpenAI refused the model name for real; the lists are still there."
            if attempt == 1 else "",
        )  # fmt: skip
    with Session(engine) as session:
        note_file(
            "failed-ai-runs", "The failed calls in `ai_runs`: the class name, no text.",
            [
                dict(meeting_id=r.meeting_id, status=r.status, error=r.error, model=r.model)
                for r in session.scalars(select(AiRun).where(AiRun.status == "failed"))
            ],
        )  # fmt: skip
    del os.environ["EXTRACT_MODEL"]
    get_settings.cache_clear()

    with Session(engine) as session:
        old = add_meeting(
            session, session.get(Template, ada_template_id(engine)), "vague_timing",
            "planted", platform="zoom", native_id="99999999999", days_ago=60,
        )  # fmt: skip
        session.commit()
        old_id = old.id
        counts = workspace.backfill(session, datetime.now(UTC))
    note_file(
        "backfill", "`workspace.backfill` (what scripts/backfill_extraction.py runs).", counts
    )
    ada.call(
        "meeting-skipped", "GET", f"/api/meetings/{old_id}",
        note="An old meeting got its decisions only: `skipped`.",
    )  # fmt: skip
    ada.call("decisions-with-old", "GET", "/api/decisions")


def text_select(table: str, meeting_id: int):
    from sqlalchemy import text as sql

    return sql(f"select id from {table} where meeting_id = {int(meeting_id)} order by id")


def ada_template_id(engine) -> int:
    with Session(engine) as session:
        return session.scalars(
            select(Template.id)
            .join(User, User.id == Template.owner_user_id)
            .where(User.email == ADA)
        ).one()


def main() -> None:
    if not sys.argv[1:] or not os.environ.get("OPENAI_API_KEY"):
        sys.exit(__doc__)
    prepare_database()
    shutil.rmtree(OUT, ignore_errors=True)
    OUT.mkdir(parents=True)
    settings = Settings(
        _env_file=None,
        app_env="test",  # lets cookies work over plain http; production adds the Secure flag
        session_secret="capture-secret-" + "x" * 30,
        cookie_secure=False,
        database_url=scratch_url,
        public_base_url="https://notes.example.test",
    )
    app = create_app(settings)
    app.dependency_overrides[get_vexa] = lambda: None
    server, base = start_server(app)
    engine = create_engine(scratch_url)
    try:
        run(base, engine)
    finally:
        server.should_exit = True
        engine.dispose()


if __name__ == "__main__":
    main()
