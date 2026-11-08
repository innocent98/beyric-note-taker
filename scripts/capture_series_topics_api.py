"""Drive Keeps coming up and Suggested agenda over real HTTP and save every exchange for the guide.

    python scripts/capture_series_topics_api.py postgresql+psycopg://USER:PASS@HOST:PORT/postgres

The argument is any Postgres server the script may create a scratch database on. It runs the real
migrations there, starts the real app under uvicorn and signs in as placeholder users. Each exchange
is written to `docs/fe-captures/series-topics/NN-name.json`, so every claim in
`docs/fe-integration-guide-series-topics.md` can be re-derived by running this again.

What is real: the app, auth, CSRF, validation, team visibility, the database, migrations, the
topics job's own code (the input it builds, the checks on the model's reply, the replacement of the
stored set, the ledger) and every response body.

What is NOT real, and is labelled where it matters:
* **The model.** `FakeModel` stands in for the OpenAI client (no key, no network). It returns the
  topics a good model would (and one an approved decision should remove). The shape of the raw reply
  is the one the code asks for (`app/topics.py` SCHEMA); a real model's wording will differ, the
  response bodies' shape will not.
* **The worker.** The job is run by this script calling `topics.process_next` against the same
  database, as the worker's loop does (`app/worker.py`), not by a separate worker process.
* **Meetings** are inserted as the pipeline and the extraction leave them (notes, decision rows).
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
from datetime import UTC, date, datetime
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "docs" / "fe-captures" / "series-topics"
DB_NAME = "notetaker_capture_topics"
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
os.environ.setdefault("APP_ENV", "development")

import httpx  # noqa: E402
import uvicorn  # noqa: E402
from alembic import command  # noqa: E402
from alembic.config import Config  # noqa: E402
from sqlalchemy import create_engine, select, text  # noqa: E402
from sqlalchemy.orm import Session  # noqa: E402

from app import accounts, teams, topics  # noqa: E402
from app.config import Settings  # noqa: E402
from app.main import create_app  # noqa: E402
from app.models import (  # noqa: E402
    AiRun,
    AppState,
    Decision,
    Meeting,
    OpenQuestion,
    Series,
    Task,
    TeamMember,
    Template,
    User,
)
from app.passwords import hash_password  # noqa: E402


def hide_tokens(value):
    """Passwords never reach a capture: the sign-in body shows a placeholder."""
    if isinstance(value, dict) and "password" in value:
        return {**value, "password": "<password placeholder>"}
    return value


class FakeModel:
    """The OpenAI client, replaced. `mode` says how it answers the next call."""

    def __init__(self) -> None:
        self.mode = "topics"
        self.calls: list[dict] = []
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self._create))

    def _create(self, **kwargs):
        self.calls.append(kwargs)
        numbers = [
            int(n) for n in re.findall(r'<meeting number="(\d+)"', kwargs["messages"][1]["content"])
        ]
        if self.mode == "garbage":
            content = "this is not JSON"
        else:
            content = json.dumps(
                {
                    "topics": [
                        {
                            "label": "Pricing model",
                            "meetings": numbers[:3],
                            "reason": "no_decision",
                            "agenda": "Decide the pricing model",
                        },
                        {
                            "label": "Compensation review",
                            "meetings": [numbers[1], numbers[-1]],
                            "reason": "no_owner",
                            "agenda": "Give the compensation review an owner",
                        },
                        {
                            "label": "Hiring plan",
                            "meetings": numbers[:2],
                            "reason": "not_final",
                            "agenda": "Finalise the hiring plan",
                        },
                    ]
                }
            )
        return SimpleNamespace(
            choices=[
                SimpleNamespace(message=SimpleNamespace(content=content), finish_reason="stop")
            ],
            usage=SimpleNamespace(prompt_tokens=1400, completion_tokens=160),
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


def notes(*points, decisions=()):
    keys = "\n".join(f"- {p}" for p in points) or "None noted"
    decided = "\n".join(f"- {d}" for d in decisions) or "None noted"
    return (
        f"## Summary\nWeekly planning.\n\n## Key points\n{keys}\n\n## Decisions\n{decided}\n\n"
        "## Action items\nNone noted\n\n## Open questions\nNone noted\n\n## Please check\nNone noted"
    )


def add_meeting(session, owner_id, template_id, when, title, body, series_id, **fields):
    meeting = Meeting(
        owner_user_id=owner_id, url="https://meet.google.com/abc-defg-hij", platform="google_meet",
        native_id="abc-defg-hij", template_id=template_id, title=title, notes=body,
        transcript="[Ada Lovelace]: Hello.", speaker_transcript="[Ada Lovelace]: Hello.",
        created_at=when, recorded_seconds=1800, source="calendar", status="ready", step="done",
        extraction_status="done", series_id=series_id, **fields,
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
        found = Series(team_id=team.id, owner_user_id=ada, title="Monday planning")
        session.add(found)
        session.commit()
        sid = found.id
        # Ada's three shared Monday meetings, then Ada's own private one, then Dan's private one.
        m1 = add_meeting(session, ada, template_id, datetime(2026, 10, 19, 10, 0, tzinfo=UTC), "Monday planning", notes("Pricing model came up: per seat or per workspace.", "Hiring plan for next quarter."), sid, shared_team_id=team.id)  # fmt: skip
        m2 = add_meeting(session, ada, template_id, datetime(2026, 10, 26, 10, 0, tzinfo=UTC), "Monday planning", notes("Pricing model again, no agreement.", "Compensation review needs someone to run it.", "Hiring plan: two roles."), sid, shared_team_id=team.id)  # fmt: skip
        m3 = add_meeting(session, ada, template_id, datetime(2026, 11, 2, 10, 0, tzinfo=UTC), "Monday planning", notes("Pricing model, third time.", "Launch checklist."), sid, shared_team_id=team.id, )  # fmt: skip
        m4 = add_meeting(session, ada, template_id, datetime(2026, 11, 4, 10, 0, tzinfo=UTC), "Ada private notes", notes("Compensation review: the CTO's pay band."), sid)  # fmt: skip
        m5 = add_meeting(session, dan, template_id, datetime(2026, 11, 3, 10, 0, tzinfo=UTC), "Dan private notes", notes("MULBERRY reorganisation: who is affected."), sid)  # fmt: skip
        # An approved decision about the hiring plan: the model's "Hiring plan" topic is removed by code.
        session.add(Decision(meeting_id=m3.id, text="The hiring plan for next quarter is approved", position=0, fingerprint="d1", status="approved"))  # fmt: skip
        session.add(OpenQuestion(meeting_id=m3.id, text="Which pricing model?", position=0, fingerprint="q1"))  # fmt: skip
        session.add(Task(team_id=team.id, meeting_id=m3.id, title="Send the pricing sheet", assignee_user_id=ada, source="ai", ai_fingerprint="t1", due_date=date(2026, 10, 30), assigned_on=date(2026, 10, 26)))  # fmt: skip
        session.add(Task(team_id=team.id, meeting_id=m3.id, title="Maybe book the venue", source="ai", ai_fingerprint="t2", suggested=True, confidence="low", assigned_on=date(2026, 11, 2)))  # fmt: skip
        session.commit()
        ids.update(series=sid, m1=m1.id, m2=m2.id, m3=m3.id, m4=m4.id, m5=m5.id)
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
            for r in session.scalars(select(AiRun).where(AiRun.kind == "topics").order_by(AiRun.id))
        ]


def stored(engine) -> list[dict]:
    with Session(engine) as session:
        from app.models import SeriesTopic

        return [
            {
                "label": t.label,
                "agenda": t.agenda,
                "reason": t.reason,
                "meeting_ids": t.meeting_ids,
                "source_meeting_ids": t.source_meeting_ids,
            }
            for t in session.scalars(select(SeriesTopic).order_by(SeriesTopic.id))
        ]


def run_job(engine, model, when) -> None:
    with Session(engine) as session:
        topics.process_next(session, when, client=model)


def run(base: str, ids: dict, engine, model: FakeModel) -> None:
    clients = {e: Recorder(base, n.split()[0].lower()) for e, (n, _) in PEOPLE.items()}
    ada, dan, eve = (clients[e] for e in PEOPLE)
    for email, rec in clients.items():
        rec.call("session", "GET", "/api/session")
        rec.call(
            "login", "POST", "/api/session", body={"email": email, "password": PEOPLE[email][1]}
        )
    anon = Recorder(base, "visitor")
    page = f"/api/series/{ids['series']}"

    anon.call("series-logged-out", "GET", page, note="No login: 401.")
    ada.call("series-before-the-job", "GET", page, note="Before the worker has looked at the series: `topics` is [] (the section is absent, no spinner). `suggested_agenda` already has the lines that need no model: the overdue task, the carried question and the suggested task to confirm. The suggested task is in no other list or count on the page.")  # fmt: skip
    note_file(
        "stored-before-the-job", "The `series_topics` rows before the job ran.", stored(engine)
    )
    run_job(engine, model, datetime.now(UTC))
    note_file("prompt-sent-to-model", "What the model was shown (the worker's job, run here with the stand-in model): the system prompt and the user message. Five meetings exist in the series; the model got four. Dan's private meeting (MULBERRY) was never read: the job reads only the meetings the Series page shows to the series' owner (Ada), which are her own and those shared with the team. Ada's own private meeting is read.", {"model": model.calls[-1]["model"], "max_completion_tokens": model.calls[-1]["max_completion_tokens"], "timeout": model.calls[-1]["timeout"], "system": model.calls[-1]["messages"][0]["content"], "user": model.calls[-1]["messages"][1]["content"]})  # fmt: skip
    note_file("stored-after-the-job", "The `series_topics` rows after the job. The stand-in model proposed three topics; \"Hiring plan\" is gone: more than half of its words are in an approved decision of the series (checked in code). `meeting_ids` are the meetings a topic cites; `source_meeting_ids` are all four meetings the model was shown (Dan's private one is not among them). A viewer who cannot read all of the sources sees a topic only if its words are in the notes they can read.", stored(engine))  # fmt: skip
    note_file("ledger-after-the-job", "The ledger row (`ai_runs`, kind `topics`). No meeting text is stored; `meeting_id` and the account are null (one call per series).", ledger(engine))  # fmt: skip
    ada.call("series-ada", "GET", page, note="Ada (the series' owner) reads two topics: \"Pricing model\" across three meetings, and \"Compensation review\" across a shared meeting and her own private one. `line` is the sub-line, written by the server; `dates_text` is in her zone, oldest first. `suggested_agenda` lists the topics' agenda sentences first, then the overdue task, the carried question and the suggested task to confirm.")  # fmt: skip
    dan.call("series-dan", "GET", page, note="Dan (a teammate) may read the three shared meetings but not Ada's private one. \"Pricing model\" is shown with its three readable meetings. \"Compensation review\" came from one shared and one private meeting: Dan can read only one, so the whole topic is hidden from him (its label would reveal what Ada's private meeting said). Its agenda line is hidden with it. Nothing from Dan's own private meeting was ever read by the job.")  # fmt: skip
    eve.call("series-eve", "GET", page, note="Another team: 404, the same as for any series that is not yours.")  # fmt: skip
    ada.call("agenda-add", "POST", f"{page}/agenda", body={"text": "Decide the pricing model"}, note="\"Add to agenda\" posts the suggested line's `text` to the existing agenda route.")  # fmt: skip
    after_add = ada.call("series-after-add", "GET", page, note="The agenda now holds the line, so the FE shows \"Added\" for the suggested row whose `text` equals an agenda item's `text` (exact string). `suggested_agenda` is unchanged: it does not hide added lines.")  # fmt: skip
    item = after_add.json()["agenda"][0]["id"]
    ada.call("agenda-remove", "DELETE", f"{page}/agenda/{item}", note="A second click on \"Added\" removes the item by its id.")  # fmt: skip
    ada.call("series-after-remove", "GET", page, note="Back to \"Add to agenda\".")  # fmt: skip
    # A new meeting changes the input; the model then fails once: the stored set stays as it was.
    with Session(engine) as session:
        template_id = session.scalars(select(Template.id).where(Template.owner_user_id == ids["ada@example.test"])).first()  # fmt: skip
        add_meeting(session, ids["ada@example.test"], template_id, datetime(2026, 11, 9, 10, 0, tzinfo=UTC), "Monday planning", notes("Pricing model, fourth time."), ids["series"], shared_team_id=teams.team_of(session, ids["ada@example.test"]).id)  # fmt: skip
        from app import series as series_rules

        series_rules.mark_topics_stale(session, ids["series"])
        session.commit()
    model.mode = "garbage"
    run_job(engine, model, datetime.now(UTC))
    ada.call("series-after-a-failed-run", "GET", page, note="The model answered with something that is not the requested JSON (STAND-IN misbehaving on purpose). The job records a failed ledger row and leaves the stored topics exactly as they were; the page is unchanged (the new meeting is in `past_meetings`).")  # fmt: skip
    note_file("ledger-after-the-failed-run", "Two rows now: the first ok, the second failed with the error class name only.", ledger(engine))  # fmt: skip


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
    server, base = start_server(app)
    engine = create_engine(scratch_url)
    try:
        run(base, seed(engine), engine, model)
    finally:
        server.should_exit = True
        engine.dispose()


if __name__ == "__main__":
    main()
