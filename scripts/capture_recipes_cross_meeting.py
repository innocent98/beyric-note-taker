"""Drive the meeting picker and a cross-meeting draft over real HTTP and save every exchange for the
frontend guide (`docs/fe-integration-guide-recipes-cross-meeting.md`).

    PYTHONPATH=. python scripts/capture_recipes_cross_meeting.py postgresql+psycopg://USER:PASS@HOST:PORT/postgres

The argument is any Postgres server the script may create a scratch database on. It runs the real
migrations there, starts the real app under uvicorn and the real worker loop, and signs in as
placeholder users: Ada (owns the team and its meetings), Daniel (a member of Ada's team: the person
who picks) and Eve (another team). Each exchange is written to
`docs/fe-captures/recipes-cross-meeting/NN-name.json`.

What is real: the app, auth, CSRF, validation, team scoping (`X-Team-Id`), visibility (Ada's private
meetings are unreadable to Daniel), the series, project and sprint rules, the cap of 12, the queue,
the worker and every error body.

What is NOT real, and is labelled where it matters:
* **The model.** `FakeModel` stands in for the OpenAI client (no key, no network). It answers with
  the strict-JSON shape `app/recipes/generate.py` asks for, citing the meeting, decision and task
  ids it finds in the prompt it is given; a real model's wording will differ, the shape of every
  response body will not.
* **The starter recipes.** `app/recipes/library.py` belongs to slice S3 (PR #267, not merged when
  this was captured), so the script registers a stand-in `status_report` (scope `many`) and
  `follow_up_email` (scope `one`).
Nobody's real meeting or address is involved; every name is a placeholder. A word in a private
meeting's title is searched for in every saved response at the end: it must never appear.
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
OUT = ROOT / "docs" / "fe-captures" / "recipes-cross-meeting"
DB_NAME = "notetaker_capture_cross"
ADMIN_EMAIL = "admin@example.test"
ADMIN_PASSWORD = "-".join(["capture", "admin", "placeholder"])
PEOPLE = {  # email -> (name, password); all placeholders
    "ada@example.test": ("Ada Lovelace", "-".join(["ada", "chosen", "password", "1"])),
    "daniel@example.test": ("Daniel Okoro", "-".join(["daniel", "chosen", "password", "2"])),
    "eve@example.test": ("Eve Outsider", "-".join(["eve", "chosen", "password", "3"])),
}
PRIVATE_WORD = "PRIVATE-WORDS"
TRANSCRIPT_WORD = "TRANSCRIPT-SENTINEL"

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
from sqlalchemy import create_engine, select, text  # noqa: E402
from sqlalchemy.orm import Session  # noqa: E402

from app import accounts, teams  # noqa: E402
from app.config import Settings  # noqa: E402
from app.main import create_app  # noqa: E402
from app.models import (  # noqa: E402
    AppState,
    Decision,
    Meeting,
    OpenQuestion,
    Project,
    Series,
    Sprint,
    Task,
    TeamMember,
    Template,
    User,
)
from app.passwords import hash_password  # noqa: E402
from app.recipes import generate  # noqa: E402
from app.recipes import service as recipe_service  # noqa: E402

STATUS = SimpleNamespace(key="status_report", name="Status report", instruction="Summarise progress, decisions, blockers and next steps over the meetings.", output_type="document", scope="many", uses_brief=False)  # fmt: skip
ONE = SimpleNamespace(key="follow_up_email", name="Follow-up email", instruction="Write a short, friendly follow-up email to the client.", output_type="email", scope="one", uses_brief=True)  # fmt: skip

BLANK = {"level": None, "items": None, "columns": None, "rows": None, "title": None, "cards": None}


class FakeModel:
    """The OpenAI client, replaced. It cites what it finds in the prompt: a meeting, a decision
    `[D3]`, a task `[T9]` per `<meeting>` block."""

    def __init__(self) -> None:
        self.calls: list[dict] = []
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self._create))

    def _create(self, **kwargs):
        self.calls.append(kwargs)
        prompt = kwargs["messages"][1]["content"]
        blocks = [{**BLANK, "type": "heading", "level": 1, "text": "Progress", "sources": None}]
        for part in prompt.split("<meeting>")[1:]:
            meeting_id = int(re.search(r"Meeting id: (\d+)", part).group(1))
            title = re.search(r"Title: (.+)", part).group(1)
            sources = [
                {
                    "kind": "meeting",
                    "meeting_id": meeting_id,
                    "line": None,
                    "quote": None,
                    "ref": None,
                }
            ]
            for kind, mark in (("decision", "D"), ("task", "T")):
                found = re.search(rf"\[{mark}(\d+)\]", part)
                if found:
                    sources.append(
                        {
                            "kind": kind,
                            "meeting_id": meeting_id,
                            "line": None,
                            "quote": None,
                            "ref": int(found.group(1)),
                        }
                    )
            blocks.append({**BLANK, "type": "paragraph", "text": f"{title}: on track.", "sources": sources})  # fmt: skip
        content = json.dumps({"title": "Status report", "blocks": blocks})
        return SimpleNamespace(
            choices=[
                SimpleNamespace(message=SimpleNamespace(content=content), finish_reason="stop")
            ],
            usage=SimpleNamespace(prompt_tokens=1800, completion_tokens=220),
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

    def call(self, name, method, path, *, body=None, csrf="session", note="", team=None):
        headers = {}
        token = (None if method == "GET" else self.csrf) if csrf == "session" else csrf
        if token:
            headers["X-CSRF-Token"] = token
        if team is not None:
            headers["X-Team-Id"] = str(team)
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
        print(f"{Recorder.count:02d}-{name:46} {method:5} {path:30} -> {response.status_code}")
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
        ada, daniel, eve = (ids[e] for e in PEOPLE)
        team = teams.ensure_team(session, session.get(User, ada))
        session.add(TeamMember(team_id=team.id, user_id=daniel, role="member"))
        session.commit()
        ids["team"] = team.id
        template = session.scalars(select(Template.id).where(Template.owner_user_id == ada, Template.name == "General")).one()  # fmt: skip

        def meeting(
            title, day, *, shared=True, series=None, owner=ada, tpl=template, status="ready"
        ):
            n = Meeting.__table__  # noqa: F841  (kept simple: one call per meeting)
            row = Meeting(
                owner_user_id=owner, url="https://meet.google.com/abc-defg-hij", platform="google_meet",
                native_id="abc-defg-hij", template_id=tpl, title=title, status=status, step="done",
                notes=f"## Summary\n{title}: progress was steady.\n",
                transcript=f"[Ada Lovelace]: {TRANSCRIPT_WORD} {title}.",
                speaker_transcript=f"[Ada Lovelace]: {TRANSCRIPT_WORD} {title}.",
                created_at=datetime(2026, 11, 2, 10, 0, tzinfo=UTC) + timedelta(days=day),
                shared_team_id=team.id if shared else None, series_id=series, source="calendar",
            )  # fmt: skip
            session.add(row)
            session.commit()
            return row

        weekly = Series(team_id=team.id, owner_user_id=ada, title="Monday planning")
        daily = Series(team_id=team.id, owner_user_id=ada, title="Daily stand-up")
        session.add_all([weekly, daily])
        session.commit()
        ids["weekly"], ids["daily"] = weekly.id, daily.id
        shared = [meeting("Kick-off", 0, series=weekly.id), meeting("Planning", 7, series=weekly.id), meeting("Review", 14, series=weekly.id)]  # fmt: skip
        hidden = [meeting(f"Salary talk {PRIVATE_WORD}", 3, shared=False, series=weekly.id), meeting(f"Layoffs {PRIVATE_WORD}", 10, shared=False, series=weekly.id)]  # fmt: skip
        for n in range(14):
            meeting(f"Stand-up {n + 1:02d}", 20 + n, series=daily.id)
        ids["shared"], ids["hidden"] = [m.id for m in shared], [m.id for m in hidden]
        ids["outside"] = meeting("Outside the range", 60).id
        eve_template = session.scalars(select(Template.id).where(Template.owner_user_id == eve, Template.name == "General")).one()  # fmt: skip
        ids["eves"] = meeting("Eve's meeting", 1, shared=False, owner=eve, tpl=eve_template).id
        for n, m in enumerate(shared):
            session.add(Decision(meeting_id=m.id, text=f"Decision {n + 1}: keep the plan.", position=0, fingerprint=f"d{n}"))  # fmt: skip
            session.add(Task(team_id=team.id, meeting_id=m.id, title=f"Task {n + 1}: send the update", spoken_owner="Ada", source="ai", ai_fingerprint=f"t{n}"))  # fmt: skip
            session.add(OpenQuestion(meeting_id=m.id, text=f"Question {n + 1}: who signs off?", position=0, fingerprint=f"q{n}"))  # fmt: skip
        project = Project(team_id=team.id, name="Onboarding", color="#0E6B5E")
        sprint = Sprint(
            team_id=team.id, number=1, start_date=date(2026, 11, 16), end_date=date(2026, 11, 22)
        )
        session.add_all([project, sprint])
        session.commit()
        ids["project"], ids["sprint"] = project.id, sprint.id
        for m in (shared[0], shared[1], hidden[0]):
            session.add(Task(team_id=team.id, meeting_id=m.id, title="Filed task", source="manual", project_id=project.id))  # fmt: skip
        session.add(Task(team_id=team.id, meeting_id=shared[0].id, title="Planned task", source="manual", sprint_id=sprint.id))  # fmt: skip
        session.commit()
    return ids


def wait_for(rec: Recorder, doc_id: int, status: str) -> None:
    for _ in range(100):
        got = rec.http.get(f"/api/documents/{doc_id}").json()
        if got["status"] == status:
            return
        time.sleep(0.1)
    raise SystemExit(f"draft {doc_id} never became {status}")


def run(base: str, ids: dict, model: FakeModel) -> None:
    daniel, eve = Recorder(base, "daniel"), Recorder(base, "eve")
    for rec, email in ((daniel, "daniel@example.test"), (eve, "eve@example.test")):
        rec.call("session", "GET", "/api/session")
        rec.call(
            "login", "POST", "/api/session", body={"email": email, "password": PEOPLE[email][1]}
        )
    anon = Recorder(base, "visitor")
    anon.call("session", "GET", "/api/session")
    resolve = "/api/recipes/picker/resolve"
    anon.call(
        "resolve-logged-out",
        "POST",
        resolve,
        body={"series_id": ids["weekly"]},
        note="No login: 401.",
    )

    daniel.call("resolve-series", "POST", resolve, body={"series_id": ids["weekly"]}, note="A recurring meeting. The series has 5 ready meetings; Daniel can read 3 (the other 2 are Ada's private ones). The answer lists the 3, newest first, with title and `day`, and says `left_out_count: 2` with a sentence to show. The two left-out meetings are not named and their ids are not in the answer. `max_meetings` is 12 and `capped` is false.")  # fmt: skip
    daniel.call("resolve-series-capped", "POST", resolve, body={"series_id": ids["daily"]}, note="A series with 14 meetings: the 12 newest are returned (`Stand-up 14` first) and `capped` is true: tell the person the oldest were cut.")  # fmt: skip
    daniel.call("resolve-project", "POST", resolve, body={"project_id": ids["project"]}, team=ids["team"], note="A project: the meetings its tasks come from. Two readable meetings; the third (Ada's private one) is left out and counted. `X-Team-Id` names the active team, as on `GET /api/meetings`.")  # fmt: skip
    daniel.call("resolve-sprint", "POST", resolve, body={"sprint_id": ids["sprint"]}, team=ids["team"], note="A sprint: the meetings its tasks are planned into, and the team's meetings held on the sprint's days (16 to 22 Nov).")  # fmt: skip
    daniel.call("resolve-dates", "POST", resolve, body={"from_day": "2026-11-02", "to_day": "2026-11-09", "tz": "Africa/Lagos"}, team=ids["team"], note="A date range, both days included, in the person's stored time zone, else the `tz` sent (the browser's). `day` in the answer is the day in that zone. Only meetings of the active team's list that Daniel may read; a date range never counts hidden meetings (`left_out_count` is 0).")  # fmt: skip
    daniel.call("resolve-from-only", "POST", resolve, body={"from_day": "2026-12-20"}, note="Either end of a range is enough: every meeting of that day or later (here only the one far-off meeting).")  # fmt: skip
    daniel.call("resolve-nothing", "POST", resolve, body={"from_day": "2030-01-01"}, note="Nothing matches: `meetings` is empty and nothing else is wrong. Say so plainly; do not offer to make a draft.")  # fmt: skip
    daniel.call("resolve-hand-picked", "POST", resolve, body={"meeting_ids": [ids["shared"][0], ids["hidden"][0], ids["shared"][2], 987654]}, note="Hand-picked ids. The private meeting and the id that does not exist look the same: both are left out and counted (`left_out_count: 2`), neither is named.")  # fmt: skip
    daniel.call(
        "resolve-none", "POST", resolve, body={}, note="No way named: 422 `validation_error`."
    )
    daniel.call("resolve-two-ways", "POST", resolve, body={"series_id": ids["weekly"], "project_id": ids["project"]}, note="Two ways named: 422 `validation_error`; name exactly one.")  # fmt: skip
    daniel.call("resolve-bad-range", "POST", resolve, body={"from_day": "2026-11-09", "to_day": "2026-11-02"}, note="`from_day` after `to_day`: 422 `invalid_date_range`.")  # fmt: skip
    daniel.call("resolve-bad-zone", "POST", resolve, body={"from_day": "2026-11-01", "tz": "Mars/Phobos"}, note="A time zone the server does not know: 422 `invalid_time_zone`.")  # fmt: skip
    daniel.call("resolve-no-csrf", "POST", resolve, body={"series_id": ids["weekly"]}, csrf=None, note="No CSRF token: 403 `csrf_rejected` (it is a POST).")  # fmt: skip
    daniel.call("resolve-wrong-team", "POST", resolve, body={"from_day": "2026-11-01"}, team=999999, note="`X-Team-Id` naming a team the caller is not in: 404 `not_found`.")  # fmt: skip
    eve.call("resolve-other-teams-series", "POST", resolve, body={"series_id": ids["weekly"]}, note="A recurring meeting of another team: 404 `not_found`, as if it did not exist.")  # fmt: skip
    eve.call("resolve-other-teams-project", "POST", resolve, body={"project_id": ids["project"]}, note="A project of another team: 404.")  # fmt: skip
    eve.call("resolve-other-teams-sprint", "POST", resolve, body={"sprint_id": ids["sprint"]}, note="A sprint of another team: 404.")  # fmt: skip

    # a draft made from the picker
    preview = daniel.call("resolve-for-draft", "POST", resolve, body={"series_id": ids["weekly"]}, note="The same choice as the first call, repeated before making the draft.").json()  # fmt: skip
    chosen = [m["id"] for m in preview["meetings"]]
    made = daniel.call("create-from-picker", "POST", "/api/documents", body={"recipe_key": "status_report", "meeting_ids": chosen, "picker": {"series_id": ids["weekly"]}}, note="Make a cross-meeting draft: send the preview's ids as `meeting_ids` and the same choice as `picker`. The server counts the meetings the person cannot read again (it does not trust a number from the client), so the draft says `left_out_count: 2` and `left_out_message`, from the first moment. `picker` never adds a meeting: only `meeting_ids` the caller can read are used. MODEL FAKED.")  # fmt: skip
    doc = made.json()["id"]
    wait_for(daniel, doc, "ready")
    daniel.call("get-ready", "GET", f"/api/documents/{doc}", note="Ready. Each paragraph cites its meeting (`kind: \"meeting\"`), and a decision and a task of it (`kind: \"decision\"`, `\"task\"`, with `ref` the decision or task id); every source carries its `meeting_id`, which is where a link goes. There are no `line` sources: a cross-meeting draft is never given the transcripts. `left_out_count` and `left_out_message` stay on the draft.")  # fmt: skip
    prompt = model.calls[-1]["messages"][1]["content"]
    note_file("prompt-check", "What the model was sent for that draft (checked by the script, not the whole prompt): per meeting the notes, decisions, tasks and open questions; no transcript and no line numbers; nothing of the two meetings Daniel cannot read.", {"meetings_in_prompt": prompt.count("<meeting>"), "transcript_text_in_prompt": TRANSCRIPT_WORD in prompt, "line_markers_in_prompt": bool(re.search(r"\[L\d+\]", prompt)), "has_decisions": "Decisions:" in prompt, "has_tasks": "Tasks:" in prompt, "has_open_questions": "Open questions:" in prompt, "private_words_in_prompt": PRIVATE_WORD in prompt})  # fmt: skip
    daniel.call("create-picker-for-one-meeting-recipe", "POST", "/api/documents", body={"recipe_key": "follow_up_email", "meeting_ids": [ids["shared"][0]], "picker": {"series_id": ids["weekly"]}}, note="A `picker` with a recipe that reads one meeting: 422 `meetings_invalid`.")  # fmt: skip
    eve.call("create-picker-other-teams-series", "POST", "/api/documents", body={"recipe_key": "status_report", "meeting_ids": [ids["eves"]], "picker": {"series_id": ids["weekly"]}}, note="A `picker` naming another team's recurring meeting: 404 `not_found`.")  # fmt: skip
    daniel.call("create-without-picker", "POST", "/api/documents", body={"recipe_key": "status_report", "meeting_ids": [ids["shared"][1], ids["hidden"][1]]}, note="Without `picker`, a hidden meeting named in `meeting_ids` is still left out and counted: `left_out_count: 1`, and the draft is made from the other.")  # fmt: skip


def main() -> None:
    if not sys.argv[1:]:
        sys.exit(__doc__)
    prepare_database()
    shutil.rmtree(OUT, ignore_errors=True)
    OUT.mkdir(parents=True)
    settings = Settings(_env_file=None, app_env="test", session_secret="capture-secret-" + "x" * 30, cookie_secure=False, database_url=scratch_url)  # fmt: skip
    app = create_app(settings)
    model = FakeModel()
    generate._client = lambda: model  # the worker's OpenAI client, replaced
    recipe_service.starter_for = {
        STATUS.key: STATUS,
        ONE.key: ONE,
    }.get  # slice S3's library is not on this branch
    stop = threading.Event()
    worker = threading.Thread(target=recipe_service.run_loop, args=(stop,), kwargs={"idle_seconds": 0.2}, daemon=True)  # fmt: skip
    from app import db  # the worker's sessions

    db.SessionLocal.configure(bind=create_engine(scratch_url))
    worker.start()
    server, base = start_server(app)
    engine = create_engine(scratch_url)
    try:
        run(base, seed(engine), model)
        leaked = [p.name for p in OUT.glob("*.json") if PRIVATE_WORD in p.read_text()]
        if leaked:
            raise SystemExit(f"a private meeting's words reached a response: {leaked}")
        print("checked: the private meetings' words are in no saved response")
    finally:
        stop.set()
        server.should_exit = True
        engine.dispose()


if __name__ == "__main__":
    main()
    sys.stdout.flush()
    os._exit(0)  # the server and worker threads need no orderly stop for a capture
