"""Drive the Decisions record over real HTTP and save every exchange for the frontend guide.

    python scripts/capture_decisions_api.py postgresql+psycopg://USER:PASS@HOST:PORT/postgres

The argument is any Postgres server the script may create a scratch database on. It runs the real
migrations there, starts the real app under uvicorn and signs in as placeholder users. Each exchange
is written to `docs/fe-captures/decisions/NN-name.json`, so every claim in
`docs/fe-integration-guide-decisions.md` can be re-derived by running this again.

What is real: the app, auth, CSRF, validation, roles and visibility, the database, migrations,
serialisation, every error, **the worker's extraction step** (`workspace.process_next`, the very
function the worker calls, with the real checks on the model's answer, the real decision rows and
links, the real reconcile on a re-run).

What is NOT real, and is labelled where it matters:
* **The model call.** This session had no OpenAI key. The replies are **stand-ins written for this
  capture** (`MODEL_REPLY`, `MODEL_REPLY_AGAIN` below), shaped as `extract-v3` asks (a `decided_by`,
  `quote`, `proposed` and `related_actions` per decision bullet) and served by a stand-in client; so
  *which* decisions the real model rates proposed, and which speakers and quotes it returns, are not
  captured. Everything done with the reply is real. The reply holds a speaker that is not in the
  transcript, a quote nobody said and an action index that does not exist on purpose, to show the
  checks drop them.
* **Team membership.** The teammate is placed on the owner's team by the script, in the database,
  and the meeting is shared with the team by writing `shared_team_id` (the share route is covered by
  `fe-integration-guide-share.md`).
* The recording, transcription and speaker steps and the bot: the meeting rows are inserted as the
  pipeline leaves them (`status ready`, notes and speaker transcript in place).
Nobody's real meeting or address is involved; every name and address is a placeholder.

Secrets in the files are masked: CSRF tokens become `<csrf-A>`, `<csrf-B>` in order of first
appearance and cookies `<cookie>`. The passwords are placeholders built at run time.
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
from types import SimpleNamespace
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "docs" / "fe-captures" / "decisions"
DB_NAME = "notetaker_capture_decisions"
ADMIN_EMAIL = "admin@example.test"  # created by migration 0006 from these (placeholders)
ADMIN_PASSWORD = "-".join(["capture", "admin", "placeholder"])
PEOPLE = {  # email -> (name, role on Dan's team, password); all placeholders
    "dan@example.test": ("Dan Okoro", "owner", "-".join(["dan", "chosen", "password", "1"])),
    "chidi@example.test": ("Chidi Eze", "member", "-".join(["chidi", "chosen", "password", "2"])),
    "amina@example.test": ("Amina Bello", "admin", "-".join(["amina", "chosen", "password", "3"])),
    "eve@example.test": ("Eve Outsider", None, "-".join(["eve", "chosen", "password", "4"])),
}

TRANSCRIPT = (
    "[Dan]: Okay, pricing. I think we should keep the free plan for two more months.\n"
    "[Chidi]: Agreed, and I'll update the pricing page before Friday.\n"
    "[Dan]: Next, the launch. Let's move it to the 30th of November.\n"
    "[Amina]: I suggest we also look at a lifetime deal, maybe later.\n"
    "[Dan]: Fine, nobody has to decide that today."
)
NOTES = """\
## Summary
Pricing and launch date.

## Decisions
- Keep the free plan for two more months.
- Move the launch to 30 November.
- Look at a lifetime deal at some point.

## Action items
- Chidi: update the pricing page (by Friday)
- Dan: tell the customers about the new launch date

## Open questions
None noted

## Please check
None noted
"""
PRIVATE_NOTES = """\
## Summary
Private call.

## Decisions
- Let the contractor go at the end of the month.

## Action items
None noted

## Open questions
None noted

## Please check
None noted
"""
ACTIONS = [
    {
        "index": 0,
        "title": "Update the pricing page",
        "spoken_owner": "Chidi",
        "due_phrase": "before Friday",
        "due_date": "2026-11-13",
        "quote": "I'll update the pricing page before Friday.",
        "confidence": "high",
        "reason": None,
    },
    {
        "index": 1,
        "title": "Tell the customers about the new launch date",
        "spoken_owner": "Dan",
        "due_phrase": None,
        "due_date": None,
        "quote": None,
        "confidence": "high",
        "reason": None,
    },
]
MODEL_REPLY = {  # STAND-IN written for this capture, not a recorded model answer
    "action_items": ACTIONS,
    "review": [],
    "milestones": [],
    "decisions": [
        {
            "index": 0,
            "decided_by": "Dan",
            "quote": "I think we should keep the free plan for two more months.",
            "proposed": False,
            "related_actions": [0],
        },
        {
            "index": 1,
            "decided_by": "Zebediah Quill",
            "quote": "We all agreed to launch in December.",
            "proposed": False,
            "related_actions": [1, 7],
        },
        {
            "index": 2,
            "decided_by": "Amina",
            "quote": "I suggest we also look at a lifetime deal, maybe later.",
            "proposed": True,
            "related_actions": [],
        },
    ],
}
MODEL_REPLY_AGAIN = {  # the same notes read again: the model now says the opposite of everything
    **MODEL_REPLY,
    "decisions": [
        {**MODEL_REPLY["decisions"][0], "proposed": True},
        {**MODEL_REPLY["decisions"][1], "proposed": True},
        {**MODEL_REPLY["decisions"][2], "proposed": False},
    ],
}
MODEL_REPLY_PRIVATE = {
    "action_items": [], "review": [], "milestones": [],
    "decisions": [{"index": 0, "decided_by": None, "quote": None, "proposed": False,
                   "related_actions": []}],
}  # fmt: skip

admin_url = sys.argv[1] if len(sys.argv) > 1 else ""
scratch_url = urlparse(admin_url)._replace(path=f"/{DB_NAME}").geturl()
os.environ["DATABASE_URL"] = scratch_url  # read by app.db when it is imported below
os.environ["OWNER_EMAIL"] = ADMIN_EMAIL
os.environ["OWNER_PASSWORD"] = ADMIN_PASSWORD
os.environ.setdefault("APP_ENV", "development")

import httpx  # noqa: E402
import openai  # noqa: E402,F401
import uvicorn  # noqa: E402
from alembic import command  # noqa: E402
from alembic.config import Config  # noqa: E402
from sqlalchemy import create_engine, select, text  # noqa: E402
from sqlalchemy.orm import Session  # noqa: E402

from app import accounts, teams, workspace  # noqa: E402
from app.config import Settings  # noqa: E402
from app.main import create_app  # noqa: E402
from app.models import Decision, DecisionTask, Meeting, Task, TeamMember, Template, User  # noqa: E402
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
        shown_body = (
            {**body, "password": "<password placeholder>"}
            if isinstance(body, dict) and "password" in body
            else body
        )
        shown = json.loads(json.dumps(data))
        if isinstance(shown, dict) and "csrf_token" in shown:
            shown["csrf_token"] = self.mask(shown["csrf_token"])
        record = {
            "note": note,
            "client": self.who,
            "request": {"method": method, "path": path, "headers": sent, "body": shown_body},
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


class StandInModel:
    """Serves MODEL_REPLY (see the module docstring)."""

    def __init__(self, reply):
        self.reply = reply
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self._create))

    def _create(self, **kwargs):
        return SimpleNamespace(
            choices=[
                SimpleNamespace(
                    message=SimpleNamespace(content=json.dumps(self.reply)), finish_reason="stop"
                )
            ],
            usage=SimpleNamespace(prompt_tokens=1184, completion_tokens=702),
        )


def make_people(session):
    for email, (name, _role, password) in PEOPLE.items():
        user = User(
            email=email,
            name=name,
            password_hash=hash_password(password),
            email_verified_at=datetime.now(UTC),
        )
        session.add(user)
        session.flush()
        accounts.add_user_defaults(session, user)
    session.commit()
    dan_team = teams.team_of(
        session, session.scalars(select(User.id).where(User.email == "dan@example.test")).one()
    )
    for email in ("chidi@example.test", "amina@example.test"):  # invites are W3
        uid = session.scalars(select(User.id).where(User.email == email)).one()
        session.execute(text("delete from team_members where user_id = :u"), {"u": uid})
        session.execute(text("delete from teams where owner_user_id = :u"), {"u": uid})
        session.add(TeamMember(team_id=dan_team.id, user_id=uid, role=PEOPLE[email][1]))
    session.commit()
    return session.scalars(
        select(Template.id)
        .join(User, User.id == Template.owner_user_id)
        .where(User.email == "dan@example.test", Template.name == "General")
    ).one()


def add_meeting(session, template_id, owner_email, **fields):
    owner = session.scalars(select(User).where(User.email == owner_email)).one()
    values = dict(
        owner_user_id=owner.id, url="https://example.test/meeting", platform="google_meet",
        native_id="nat-aaaa-bbb", template_id=template_id, status="ready", step="done",
        transcript=TRANSCRIPT, speaker_transcript=TRANSCRIPT, notes=NOTES,
        created_at=datetime.now(UTC) - timedelta(minutes=30),
    )  # fmt: skip
    meeting = Meeting(**{**values, **fields})
    session.add(meeting)
    session.flush()
    workspace.mark_pending(meeting, quiet=fields.get("extraction_quiet", False))
    session.commit()
    return meeting


def run(base: str, engine) -> None:
    clients = {
        email: Recorder(base, name.split()[0].lower()) for email, (name, *_r) in PEOPLE.items()
    }
    dan, chidi, amina, eve = (clients[e] for e in PEOPLE)
    with Session(engine) as session:
        template_id = make_people(session)
        shared_id = add_meeting(session, template_id, "dan@example.test").id
        add_meeting(
            session, template_id, "dan@example.test", notes=PRIVATE_NOTES,
            native_id="nat-pppp-rrr", created_at=datetime.now(UTC) - timedelta(minutes=10),
        )  # fmt: skip
        dan_team = teams.team_of(
            session, session.scalars(select(User.id).where(User.email == "dan@example.test")).one()
        )
        session.get(Meeting, shared_id).shared_team_id = dan_team.id
        session.commit()
    for email, rec in clients.items():
        rec.call("session", "GET", "/api/session")
        rec.call(
            "login", "POST", "/api/session", body={"email": email, "password": PEOPLE[email][2]}
        )

    # --- the worker's step (two meetings: the shared one, then Dan's private one) ---
    with Session(engine) as session:
        workspace.process_next(session, client=StandInModel(MODEL_REPLY))
        workspace.process_next(session, client=StandInModel(MODEL_REPLY_PRIVATE))
        note_file(
            "decision-rows-after-extraction",
            "The decisions table as the extraction left it. Decision 2: the model's `decided_by` "
            "(a speaker who is not in the transcript) and `quote` (words nobody said) were "
            "dropped by the checks, and the related action 7 (no such action) was dropped.",
            [dict(id=d.id, meeting_id=d.meeting_id, text=d.text, status=d.status,
                  decided_by=d.decided_by, quote=d.quote, revisit_on=d.revisit_on,
                  replaced_by_id=d.replaced_by_id, hidden=d.hidden_at is not None)
             for d in session.scalars(select(Decision).order_by(Decision.id))],
        )  # fmt: skip
        note_file(
            "decision-task-links",
            "`decision_tasks` after the extraction (decision id, task id).",
            [dict(decision_id=link.decision_id, task_id=link.task_id)
             for link in session.scalars(select(DecisionTask).order_by(DecisionTask.decision_id))],
        )  # fmt: skip

    # --- reading ---
    dan_list = dan.call(
        "dan-list", "GET", "/api/decisions",
        note="Dan owns both meetings: four decisions in two groups, `can_edit` true. "
        "`counts` are over the whole log; `due_this_week` is 0 (no revisit dates yet).",
    ).json()  # fmt: skip
    by_text = {d["text"]: d for g in dan_list["groups"] for d in g["decisions"]}
    free, launch, lifetime = (
        by_text["Keep the free plan for two more months."],
        by_text["Move the launch to 30 November."],
        by_text["Look at a lifetime deal at some point."],
    )
    contractor = by_text["Let the contractor go at the end of the month."]
    note_file("ids", "Decision ids used below.", dict(
        free=free["id"], launch=launch["id"], lifetime=lifetime["id"], contractor=contractor["id"]))  # fmt: skip
    chidi.call(
        "chidi-list", "GET", "/api/decisions",
        note="Chidi is on Dan's team: he reads only the SHARED meeting's decisions (three), "
        "`can_edit` false, and the private meeting's decision is in no count. His related tasks "
        "are the ones he may see.",
    )  # fmt: skip
    eve.call("eve-list", "GET", "/api/decisions",
             note="Someone outside the team sees nothing: empty groups, zero counts.")  # fmt: skip
    dan.call("tab-proposed", "GET", "/api/decisions?status=proposed",
             note="A tab: only the proposed ones. `counts` do not change.")  # fmt: skip
    dan.call("tab-replaced-empty", "GET", "/api/decisions?status=replaced",
             note="A tab with nothing in it: `groups` empty, `counts` still whole.")  # fmt: skip
    dan.call("search", "GET", "/api/decisions?q=launch&status=approved",
             note="Search and a tab together.")  # fmt: skip
    dan.call("status-invalid", "GET", "/api/decisions?status=done",
             note="Not a status: 422 `validation_error`.")  # fmt: skip

    # --- changing a decision ---
    dan.call("patch-status", "PATCH", f"/api/decisions/{free['id']}", body={"status": "rejected"},
             note="The owner changes the status. 200 with the whole decision (`can_edit` true).")  # fmt: skip
    dan.call("patch-revisit", "PATCH", f"/api/decisions/{launch['id']}",
             body={"revisit_on": datetime.now(UTC).date().isoformat()},
             note="A revisit date of today: the banner counts it (`due_this_week`, below).")  # fmt: skip
    dan.call("after-revisit", "GET", "/api/decisions",
             note="`due_this_week` is 1 (an approved decision with a date this week). "
             "`counts` now show one rejected.")  # fmt: skip
    dan.call("patch-revisit-clear", "PATCH", f"/api/decisions/{launch['id']}",
             body={"revisit_on": None}, note="null clears the date.")  # fmt: skip
    dan.call("patch-replaced", "PATCH", f"/api/decisions/{lifetime['id']}",
             body={"status": "replaced", "replaced_by_id": launch["id"]},
             note="Replaced with the decision that replaced it, in one request.")  # fmt: skip
    dan.call("after-replaced", "GET", "/api/decisions",
             note="`replaced_by` on the old decision, `replaces` on the new one.")  # fmt: skip
    dan.call("patch-back-to-proposed", "PATCH", f"/api/decisions/{lifetime['id']}",
             body={"status": "proposed"},
             note="Moving off Replaced clears `replaced_by` (and so the other side's `replaces`).")  # fmt: skip
    dan.call("patch-replaced-none", "PATCH", f"/api/decisions/{lifetime['id']}",
             body={"status": "replaced"}, note="Replaced with none chosen is allowed.")  # fmt: skip
    dan.call("patch-replaced-later", "PATCH", f"/api/decisions/{lifetime['id']}",
             body={"replaced_by_id": launch["id"]}, note="...and one chosen afterwards.")  # fmt: skip
    dan.call("patch-replaced-cleared", "PATCH", f"/api/decisions/{lifetime['id']}",
             body={"replaced_by_id": None}, note="null clears it; it stays Replaced.")  # fmt: skip

    # --- refusals ---
    chidi.call("chidi-patch-403", "PATCH", f"/api/decisions/{free['id']}", body={"status": "approved"},
               note="Chidi can read this decision but did not record the meeting: 403 `not_owner`.")  # fmt: skip
    chidi.call("chidi-patch-private-404", "PATCH", f"/api/decisions/{contractor['id']}",
               body={"status": "approved"},
               note="A decision of Dan's PRIVATE meeting: Chidi cannot read it, so 404 (not 403).")  # fmt: skip
    eve.call("eve-patch-404", "PATCH", f"/api/decisions/{free['id']}", body={"status": "approved"})
    dan.call("patch-missing-404", "PATCH", "/api/decisions/999999", body={"status": "approved"})
    dan.call("patch-no-csrf", "PATCH", f"/api/decisions/{free['id']}", body={"status": "approved"},
             csrf=None, note="No CSRF token: refused, nothing changes.")  # fmt: skip
    dan.call("patch-replaced-by-needs-replaced", "PATCH", f"/api/decisions/{free['id']}",
             body={"replaced_by_id": launch["id"]},
             note="`free` is Rejected: naming a replacement needs status Replaced. 422.")  # fmt: skip
    dan.call("patch-replaced-by-self", "PATCH", f"/api/decisions/{launch['id']}",
             body={"status": "replaced", "replaced_by_id": launch["id"]}, note="Itself: 422.")  # fmt: skip
    dan.call("patch-replaced-again", "PATCH", f"/api/decisions/{lifetime['id']}",
             body={"replaced_by_id": launch["id"]},
             note="Set again, so the next call has something to loop back to.")  # fmt: skip
    dan.call("patch-replaced-by-cycle", "PATCH", f"/api/decisions/{launch['id']}",
             body={"status": "replaced", "replaced_by_id": lifetime["id"]},
             note="`lifetime` is already replaced by `launch`: a cycle, 422.")  # fmt: skip
    dan.call("patch-replaced-by-missing", "PATCH", f"/api/decisions/{free['id']}",
             body={"status": "replaced", "replaced_by_id": 999999},
             note="No such decision in the caller's log: 422 `replaced_by_not_found`.")  # fmt: skip
    dan.call("patch-bad-status", "PATCH", f"/api/decisions/{free['id']}", body={"status": "done"})
    dan.call("patch-null-status", "PATCH", f"/api/decisions/{free['id']}", body={"status": None})
    dan.call(
        "patch-bad-date",
        "PATCH",
        f"/api/decisions/{free['id']}",
        body={"revisit_on": "next friday"},
    )
    dan.call("patch-unknown-field", "PATCH", f"/api/decisions/{free['id']}", body={"text": "x"})

    # --- the model is asked again: what a person set stays ---
    with Session(engine) as session:
        meeting = session.get(Meeting, shared_id)
        workspace.mark_pending(meeting)
        session.commit()
        workspace.process_next(
            session, datetime.now(UTC) + timedelta(hours=2), client=StandInModel(MODEL_REPLY_AGAIN)
        )
    dan.call("after-rerun", "GET", "/api/decisions",
             note="The notes were read again and the stand-in model now says the opposite for "
             "every decision. All three decisions of this meeting had been changed by a person "
             "(`free` Rejected, `lifetime` Replaced, `launch` given and cleared a date), so none "
             "of them moves: status, date and replaced_by stay as the owner left them.")  # fmt: skip

    # --- related tasks and who may see them ---
    with Session(engine) as session:
        tasks_ = session.scalars(select(Task).order_by(Task.position)).all()
        note_file(
            "tasks",
            "The two tasks of the shared meeting.",
            [dict(id=t.id, title=t.title, suggested=t.suggested) for t in tasks_],
        )
        tasks_[0].suggested = True
        session.commit()
    dan.call("related-with-suggested", "GET", "/api/decisions",
             note="The pricing task was made a SUGGESTED task (by a direct database write, as if "
             "Ask before adding unclear tasks had held it): it is gone from `related_tasks` "
             "everywhere. Suggested tasks are never shown here.")  # fmt: skip
    dan.call("hide", "POST", f"/api/decisions/{launch['id']}/hide",
             note="Remove from log (unchanged by this feature): 204.")  # fmt: skip
    dan.call("after-hide", "GET", "/api/decisions",
             note="A hidden decision is in no group and no count.")  # fmt: skip
    dan.call("patch-hidden-404", "PATCH", f"/api/decisions/{launch['id']}", body={"status": "approved"},
             note="...and cannot be changed: 404.")  # fmt: skip


def main() -> None:
    if not sys.argv[1:]:
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
