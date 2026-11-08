"""Drive Ask Beyric over real HTTP and save every exchange for the frontend guide.

    python scripts/capture_ask_api.py postgresql+psycopg://USER:PASS@HOST:PORT/postgres

The argument is any Postgres server the script may create a scratch database on. It runs the real
migrations there, starts the real app under uvicorn and signs in as placeholder users. Each exchange
is written to `docs/fe-captures/ask-beyric/NN-name.json`, so every claim in
`docs/fe-integration-guide-ask-beyric.md` can be re-derived by running this again.

What is real: the app, auth, CSRF, validation, team visibility, the database, migrations, retrieval
(Postgres full text, passage cutting and scoring), the checks on the model's reply, the cap, the
ledger and every error body.

What is NOT real, and is labelled where it matters:
* **The model.** `FakeModel` stands in for the OpenAI client (no key, no network). It cites the
  first passage it is shown, or answers badly on purpose where a capture says so. The shape of the
  raw reply it returns is the one the code asks for (`app/ask.py` SCHEMA); a real model's wording
  will differ, the response bodies' shape will not.
* **The daily cap** is 5 here (`ASK_DAILY_LIMIT=5`) so that it can be reached; the default is 30.
* **Time.** Dates in the answers are those of the seeded meetings; "last week" and "today" are
  worked out from the real clock when the script runs.
* The recordings, transcripts and notes: meetings are inserted as the pipeline leaves them.
Nobody's real meeting or address is involved; every name and address is a placeholder.

Secrets are masked: CSRF tokens become `<csrf-A>`, `<csrf-B>` in order of first appearance, cookies
`<cookie>`.
"""

# ruff: noqa: E501  (the notes are prose for the guide, one long line each)

import json
import os
import re
import shutil
import socket
import sys
import threading
import time
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "docs" / "fe-captures" / "ask-beyric"
DB_NAME = "notetaker_capture_ask"
ADMIN_EMAIL = "admin@example.test"
ADMIN_PASSWORD = "-".join(["capture", "admin", "placeholder"])
PEOPLE = {  # email -> (name, password); all placeholders
    "ada@example.test": ("Ada Lovelace", "-".join(["ada", "chosen", "password", "1"])),
    "dan@example.test": ("Dan Okoro", "-".join(["dan", "chosen", "password", "2"])),
    "eve@example.test": ("Eve Outsider", "-".join(["eve", "chosen", "password", "3"])),
}

admin_url = sys.argv[1] if len(sys.argv) > 1 else ""
scratch_url = urlparse(admin_url)._replace(path=f"/{DB_NAME}").geturl()
os.environ["DATABASE_URL"] = scratch_url
os.environ["OWNER_EMAIL"] = ADMIN_EMAIL
os.environ["OWNER_PASSWORD"] = ADMIN_PASSWORD
os.environ["ASK_DAILY_LIMIT"] = "5"
os.environ.setdefault("APP_ENV", "development")

import httpx  # noqa: E402
import uvicorn  # noqa: E402
from alembic import command  # noqa: E402
from alembic.config import Config  # noqa: E402
from sqlalchemy import create_engine, select, text  # noqa: E402
from sqlalchemy.orm import Session  # noqa: E402

from app import accounts, teams  # noqa: E402
from app.config import Settings  # noqa: E402
from app.main import create_app  # noqa: E402
from app.models import AiRun, AppState, Decision, Meeting, Task, TeamMember, Template, User  # noqa: E402
from app.passwords import hash_password  # noqa: E402
from app.services import get_openai_client  # noqa: E402


def hide_tokens(value):
    """Passwords never reach a capture: the sign-in body shows a placeholder."""
    if isinstance(value, dict) and "password" in value:
        return {**value, "password": "<password placeholder>"}
    return value


class FakeModel:
    """The OpenAI client, replaced. `mode` says how it answers the next call."""

    def __init__(self) -> None:
        self.mode = "first"
        self.calls: list[dict] = []
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self._create))

    def _create(self, **kwargs):
        self.calls.append(kwargs)
        ids = re.findall(r'<passage id="(P\d+)">', kwargs["messages"][1]["content"])
        if self.mode == "garbage":
            content = "this is not JSON"
        elif self.mode == "found_false":
            content = json.dumps({"lead": "Nothing.", "points": [], "found": False})
        elif self.mode == "invented":
            content = json.dumps(
                {
                    "lead": "Sure.",
                    "found": True,
                    "points": [{"text": "Invented.", "passages": ["P99"]}],
                }
            )
        else:
            content = json.dumps(
                {
                    "lead": "Here is what your meetings say.",
                    "found": True,
                    "points": [
                        {"text": "The answer drawn from the first passage.", "passages": ids[:1]}
                    ],
                }
            )
        return SimpleNamespace(
            choices=[
                SimpleNamespace(message=SimpleNamespace(content=content), finish_reason="stop")
            ],
            usage=SimpleNamespace(prompt_tokens=1800, completion_tokens=120),
        )


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
            "request": {
                "method": method,
                "path": hide_tokens(path),
                "headers": sent,
                "body": hide_tokens(body),
            },
            "response": {"status": response.status_code, "headers": kept, "body": shown},
        }
        (OUT / f"{Recorder.count:02d}-{name}.json").write_text(
            json.dumps(record, indent=2, ensure_ascii=False) + "\n"
        )
        print(f"{Recorder.count:02d}-{name:46} {method:6} {path:20} -> {response.status_code}")
        return response


def note_file(name: str, note: str, content) -> None:
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


PRICING_NOTES = (
    "## Summary\nThe team agreed the pricing for the launch.\n\n"
    "## Key points\n- Pricing tiers were discussed at length.\n- The launch is planned for 14 March.\n\n"
    "## Decisions\n- Pricing is set at 40 pounds a seat.\n\n"
    "## Action items\n- Dan to send the pricing sheet by Friday.\n\n"
    "## Open questions\nNone noted\n"
)
PRICING_TRANSCRIPT = (
    "[Ada Lovelace]: Let us talk about pricing.\n"
    "[Daniel Okoro]: I think 40 pounds a seat is right.\n"
    "[Ada Lovelace]: Agreed, pricing is 40 pounds a seat.\n"
    "[Daniel Okoro]: And I will send the pricing sheet by Friday."
)
DESIGN_NOTES = "## Summary\nThe Zanzibar rebrand moves ahead.\n\n## Key points\n- Zanzibar logo options were shown.\n"
DESIGN_TRANSCRIPT = "[Dan Okoro]: The Zanzibar logo options are ready.\n[Ada Lovelace]: Great."
PRIVATE_NOTES = "## Summary\nDan's private notes on the Mulberry reorganisation.\n"


def add_meeting(session, owner_id, template_id, when, title, notes, transcript, **fields):
    values = {"status": "ready", "step": "done", **fields}
    meeting = Meeting(
        owner_user_id=owner_id, url="https://meet.google.com/abc-defg-hij", platform="google_meet",
        native_id="abc-defg-hij", template_id=template_id,
        title=title, transcript=transcript, speaker_transcript=transcript, notes=notes,
        created_at=when, recorded_seconds=1800, source="calendar", **values,
    )  # fmt: skip
    session.add(meeting)
    session.commit()
    return meeting


def seed(engine) -> dict:
    now = datetime.now(UTC)
    ids: dict = {}
    with Session(engine) as session:
        for email, (name, password) in PEOPLE.items():
            user = User(
                email=email, name=name, password_hash=hash_password(password),
                email_verified_at=now,
            )  # fmt: skip
            session.add(user)
            session.flush()
            accounts.add_user_defaults(session, user)
            ids[email] = user.id
        state = session.get(AppState, 1) or AppState(id=1)
        state.max_accounts = 10
        session.add(state)
        session.commit()
        ada, dan = ids["ada@example.test"], ids["dan@example.test"]
        template_id = session.scalars(
            select(Template.id).where(Template.owner_user_id == ada, Template.name == "General")
        ).one()
        team = teams.ensure_team(session, session.get(User, ada))
        dan_team = teams.team_of(session, dan)
        member = session.scalars(select(TeamMember).where(TeamMember.user_id == dan)).one()
        member.team_id, member.role = team.id, "member"
        session.flush()
        if dan_team is not None:
            session.delete(dan_team)
        session.commit()
        pricing = add_meeting(session, ada, template_id, datetime(2026, 11, 2, 10, 0, tzinfo=UTC), "Pricing review", PRICING_NOTES, PRICING_TRANSCRIPT)  # fmt: skip
        design = add_meeting(session, dan, template_id, datetime(2026, 11, 3, 10, 0, tzinfo=UTC), "Design sync", DESIGN_NOTES, DESIGN_TRANSCRIPT, shared_team_id=team.id)  # fmt: skip
        private = add_meeting(session, dan, template_id, datetime(2026, 11, 4, 10, 0, tzinfo=UTC), "Dan private 1:1", PRIVATE_NOTES, "[Dan Okoro]: Mulberry.")  # fmt: skip
        add_meeting(session, ada, template_id, datetime(2026, 11, 5, 10, 0, tzinfo=UTC), "Still processing", PRICING_NOTES, PRICING_TRANSCRIPT, status="processing")  # fmt: skip
        session.add(Decision(meeting_id=pricing.id, text="Pricing is set at 40 pounds a seat.", position=0, fingerprint="d1"))  # fmt: skip
        session.add(Decision(meeting_id=pricing.id, text="Hidden decision", position=1, fingerprint="d2", hidden_at=now))  # fmt: skip
        # `assigned_on` is a Wednesday of the week before the run, so "last week" finds these two
        # whenever the script runs (in production it is the day of the meeting).
        last_week = (now - timedelta(days=now.weekday() + 5)).date()
        # Ada's own tasks: one from her meeting (with a quote found in the transcript), one from Dan's
        # private meeting (hers to see, not a meeting she may open), one done, one from a past week.
        session.add(Task(team_id=team.id, meeting_id=pricing.id, title="Send the pricing sheet", assignee_user_id=ada, quote="I will send the pricing sheet by Friday", source="ai", ai_fingerprint="t1", due_date=date(2026, 11, 13), assigned_on=last_week))  # fmt: skip
        session.add(Task(team_id=team.id, meeting_id=private.id, title="Prepare the reorganisation plan", assignee_user_id=ada, quote="Mulberry", source="ai", ai_fingerprint="t2", assigned_on=date(2026, 11, 4)))  # fmt: skip
        session.add(Task(team_id=team.id, meeting_id=pricing.id, title="Book the launch venue", assignee_user_id=ada, source="ai", ai_fingerprint="t3", status="done", assigned_on=last_week))  # fmt: skip
        session.commit()
        ids["pricing"], ids["design"], ids["private"] = pricing.id, design.id, private.id
    return ids


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
            }
            for r in session.scalars(select(AiRun).where(AiRun.kind == "ask").order_by(AiRun.id))
        ]


def run(base: str, ids: dict, engine, model: FakeModel) -> None:
    clients = {e: Recorder(base, n.split()[0].lower()) for e, (n, _) in PEOPLE.items()}
    ada, dan, eve = (clients[e] for e in PEOPLE)
    for email, rec in clients.items():
        rec.call("session", "GET", "/api/session")
        rec.call(
            "login", "POST", "/api/session", body={"email": email, "password": PEOPLE[email][1]}
        )
    anon = Recorder(base, "visitor")
    anon.call("session", "GET", "/api/session")

    anon.call("scope-logged-out", "GET", "/api/ask/scope", note="No login: 401.")
    anon.call(
        "ask-logged-out", "POST", "/api/ask", body={"question": "pricing"}, note="No login: 401."
    )
    ada.call("scope-ada", "GET", "/api/ask/scope", note="Ada: Pricing review (hers) and Design sync (Dan's, shared) are ready and readable = 2 meetings. The not-ready meeting and Dan's private 1:1 are not counted. Decisions: 1 (the hidden one is not counted). Tasks: 2 (her two on Pricing review; the one on Dan's private meeting is on a meeting she cannot read, so it is not counted). `suggestions` has the name chip: the most frequent speaker other than Ada.")  # fmt: skip
    dan.call(
        "scope-dan",
        "GET",
        "/api/ask/scope",
        note="Dan reads his own two ready meetings (Design sync, shared; his private 1:1) = 2. Ada's Pricing review is private to Ada, so it is not counted, nor its decision. His tasks count is 1: the task on his private 1:1 (assigned to Ada) is on a meeting he can read.",
    )
    eve.call("scope-eve", "GET", "/api/ask/scope", note="Eve is on another team: nothing. No name chip (her meetings name no one), so four suggestions.")  # fmt: skip

    ada.call("ask-answer", "POST", "/api/ask", body={"question": "What did we decide about pricing?"}, note="Search path. MODEL FAKED: it cites the first passage. Everything else is real: the chip is built by code from the passage's meeting (title, day, owner, shared, target, line).")  # fmt: skip
    note_file("prompt-sent-to-model", "What the model was shown for the last question: the system prompt and the user message (the question as a JSON string, the passages inside delimiters). Captured from the call the fake received.", {"model": model.calls[-1]["model"], "max_completion_tokens": model.calls[-1]["max_completion_tokens"], "timeout": model.calls[-1]["timeout"], "system": model.calls[-1]["messages"][0]["content"], "user": model.calls[-1]["messages"][1]["content"]})  # fmt: skip
    ada.call("ask-transcript-line", "POST", "/api/ask", body={"question": "What did Daniel say?"}, note="A transcript passage: `target` \"transcript\" and `line` is the 1-based row of the transcript string the meeting page draws.")  # fmt: skip
    ada.call("ask-shared-teammate-meeting", "POST", "/api/ask", body={"question": "Tell me about the Zanzibar logo"}, note="A teammate's meeting shared with the team: `owner_name` is the owner's display name and `shared` is true.")  # fmt: skip
    ada.call("ask-private-teammate-word", "POST", "/api/ask", body={"question": "What about the Mulberry reorganisation?"}, note="The word only appears in Dan's PRIVATE meeting, which Ada may not read: nothing is retrieved, so `none`, no model call, no charge.")  # fmt: skip
    dan.call("ask-private-owner", "POST", "/api/ask", body={"question": "What about the Mulberry reorganisation?"}, note="Dan owns that meeting: he gets an answer (MODEL FAKED).")  # fmt: skip
    ada.call("ask-nothing-retrieved", "POST", "/api/ask", body={"question": "xylophone quarterly"}, note="Nothing matches: `none`, free.")  # fmt: skip
    model.mode = "found_false"
    ada.call("ask-found-false", "POST", "/api/ask", body={"question": "What was the pricing?"}, note="MODEL FAKED to say it found nothing (`found: false`): `none`. This call was made, so it counts toward the cap.")  # fmt: skip
    model.mode = "garbage"
    ada.call("ask-model-garbage", "POST", "/api/ask", body={"question": "What was the pricing?"}, note="MODEL FAKED to return text that is not JSON: 502 `ask_failed`. The call still counts toward the cap.")  # fmt: skip
    model.mode = "first"
    ada.call("ask-cap-reached", "POST", "/api/ask", body={"question": "What did we decide about pricing?"}, note="The cap is 5 in this capture (ASK_DAILY_LIMIT=5; default 30): 429 `ask_limit`. No model call.")  # fmt: skip
    ada.call("ask-tasks", "POST", "/api/ask", body={"question": "What are my tasks?"}, note="Tasks path: no model, free even over the cap. The task from Dan's private meeting is listed with `cites: []` (Ada may see the task, not open the meeting).")  # fmt: skip
    ada.call("ask-tasks-last-week", "POST", "/api/ask", body={"question": "What did I commit to last week?"}, note="A date phrase filters by the day the task was assigned (the meeting's day), all statuses (the done one is listed here). The seed sets `assigned_on` to a day of the week before the run, so this answer holds whenever the script runs.")  # fmt: skip
    eve.call("ask-tasks-none", "POST", "/api/ask", body={"question": "my tasks"}, note="No tasks: still `tasks`, with a lead that says so, and no points.")  # fmt: skip
    ada.call("ask-empty", "POST", "/api/ask", body={"question": "   "}, note="Blank: 422 `question_invalid`.")  # fmt: skip
    ada.call("ask-too-long", "POST", "/api/ask", body={"question": "x" * 301}, note="301 characters: 422 `question_invalid`.")  # fmt: skip
    ada.call("ask-not-a-string", "POST", "/api/ask", body={"question": 5}, note="A body that is not the shape: the ordinary 422 `validation_error` with `fields`.")  # fmt: skip
    ada.call("ask-missing-question", "POST", "/api/ask", body={}, note="Same.")  # fmt: skip
    ada.call("ask-no-csrf", "POST", "/api/ask", body={"question": "pricing"}, csrf=None, note="No CSRF token: 403 `csrf_rejected`.")  # fmt: skip
    # Fill the per-minute limit (20 questions a minute, free ones included) with unrecorded task
    # questions until it refuses, then record one more refusal.
    for _ in range(30):
        filler = ada.http.post(
            "/api/ask", json={"question": "my tasks"}, headers={"X-CSRF-Token": ada.csrf}
        )
        if filler.status_code == 429:
            break
    ada.call("ask-rate-limited", "POST", "/api/ask", body={"question": "my tasks"}, note="More than 20 questions in a minute (the free ones count too): 429 `ask_rate`, a different code from the daily cap. Wait a minute and ask again.")  # fmt: skip
    note_file("ledger-after", "The ledger rows these questions left (`ai_runs`, kind `ask`). No question or answer text is stored; free questions leave no row.", ledger(engine))  # fmt: skip


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
    app.dependency_overrides[get_openai_client] = lambda: model
    server, base = start_server(app)
    engine = create_engine(scratch_url)
    try:
        run(base, seed(engine), engine, model)
    finally:
        server.should_exit = True
        engine.dispose()


if __name__ == "__main__":
    main()
