"""Capture what the front end gets for the per-user note taker name, over real HTTP.

    python scripts/capture_bot_name.py postgresql+psycopg://USER:PASS@localhost:PORT/postgres

Same setup as `scripts/capture_bot_invite_tip.py` (it imports `capture_calendar_connect` for the
HTTP recorder, the scratch database and the app server): real migrations, the real app under
uvicorn, real HTTP. The server runs as production would, `BOT_PROVIDER=skribby`, with the REAL
Skribby client pointed at a local stand-in for Skribby's API (`StandInSkribby`), which saves the
body of every `POST /bot` it receives: that file shows exactly what `bot_name` the app sends.

What is NOT real: Skribby itself. No real bot was sent and no real meeting joined, so nothing here
says what a participant sees in Meet, Zoom or Teams (see the SOP, "how to prove it live"). The
"waiting to be let in" rows are set up directly in the state the worker leaves them in.

Files go to `docs/fe-captures/bot-name/NN-name.json`. Every value is a placeholder.
"""

import os
import sys

# Read by `get_settings()` (the meeting answer and the provider factory) when they run, so they are
# set before anything imports the app. All placeholders.
os.environ["BOT_PROVIDER"] = "skribby"
os.environ["SKRIBBY_API_KEY"] = "capture-placeholder-key"
os.environ["SKRIBBY_BOT_NAME"] = "Beyric Meeting Support"

import json  # noqa: E402
import shutil  # noqa: E402
import threading  # noqa: E402
import unicodedata  # noqa: E402
from datetime import UTC, datetime  # noqa: E402
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer  # noqa: E402

import capture_calendar_connect as base  # noqa: E402  (reads sys.argv: the admin database URL)

OUT = base.ROOT / "docs" / "fe-captures" / "bot-name"
base.OUT = OUT

from sqlalchemy import create_engine, select, update  # noqa: E402
from sqlalchemy.orm import Session  # noqa: E402

from app import skribby  # noqa: E402
from app.config import Settings  # noqa: E402
from app.main import create_app  # noqa: E402
from app.models import Meeting, Template, User  # noqa: E402

Recorder = base.Recorder
RECEIVED: list[dict] = []
BOT_ID = "3c09b2aa-6708-42c1-8145-aa55ee223613"  # a placeholder


class StandInSkribby(BaseHTTPRequestHandler):
    """Takes a bot (`POST /bot`) and keeps what it was sent. Answers like Skribby's create call."""

    def do_POST(self):  # noqa: N802
        length = int(self.headers.get("content-length", 0))
        body = json.loads(self.rfile.read(length) or b"{}")
        RECEIVED.append(body)
        number = len(RECEIVED)
        answer = json.dumps({"id": BOT_ID.replace("3c09", f"{number:04d}"), "status": "booting"})
        self.send_response(201)
        self.send_header("content-type", "application/json")
        self.end_headers()
        self.wfile.write(answer.encode())

    def do_GET(self):  # noqa: N802
        self.send_response(404)
        self.end_headers()

    def log_message(self, *args):
        pass


def start_stand_in() -> str:
    server = ThreadingHTTPServer(("127.0.0.1", 0), StandInSkribby)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return f"http://127.0.0.1:{server.server_address[1]}"


def set_name(engine, email, name):
    with Session(engine) as session:
        session.execute(update(User).where(User.email == email).values(bot_name=name))
        session.commit()


def free_the_slot(engine, meeting_id) -> None:
    """The local note taker holds one bot at a time: the earlier meeting is marked over (as the
    worker would once its call ended), so the next send is not answered 409 `bot_busy`."""
    with Session(engine) as session:
        session.execute(update(Meeting).where(Meeting.id == meeting_id).values(status="failed"))
        session.commit()


def run(app, server_base, engine) -> None:
    user = Recorder(server_base, "signed-in user")
    base.login(user, "login", base.ADMIN_EMAIL, base.ADMIN_PASSWORD)
    visitor = Recorder(server_base, "not signed in")
    visitor.call("anonymous-read", "GET", "/api/settings/bot-name", note="No session: 401.")

    user.call(
        "read-default",
        "GET",
        "/api/settings/bot-name",
        note="Nothing set: `bot_name` is `null` (present, not absent) and `bot_name_default` is "
        "`SKRIBBY_BOT_NAME` from the server. The form's placeholder and help text read it "
        "from here.",
    )
    user.call(
        "save",
        "PUT",
        "/api/settings/bot-name",
        body={"bot_name": "  Alex   from  Product "},
        note="Trimmed and inner whitespace collapsed: the answer is what was stored.",
    )
    user.call("read-after-save", "GET", "/api/settings/bot-name")
    user.call(
        "save-dot-name",
        "PUT",
        "/api/settings/bot-name",
        body={"bot_name": "Dr.Smith"},
        note="A dot is fine unless it ends in a listed domain: `Dr.Smith` is accepted.",
    )
    user.call(
        "save-recorder-like-name",
        "PUT",
        "/api/settings/bot-name",
        body={"bot_name": "Alex's AI Notetaker"},
        note="The soft warning: the save works and `warning` says Meet and Zoom may refuse it.",
    )
    user.call(
        "read-with-warning", "GET", "/api/settings/bot-name", note="The warning is also on read."
    )
    user.call(
        "save-emoji",
        "PUT",
        "/api/settings/bot-name",
        body={"bot_name": "Notes 📝 for Ada"},
        note="An emoji is a plain character and is accepted.",
    )
    for name, value, note in (
        ("refused-too-long", "A" * 51, "51 characters: Teams' limit is 50."),
        ("refused-email", "alex@acme.example", "Looks like an email address."),
        ("refused-link", "https://acme.example/alex", "Looks like a web address."),
        (
            "refused-bare-domain",
            "Alex from acme.com",
            "`label.tld` with a TLD from the short list in `app/titles.py` is a web address.",
        ),
        ("refused-bidi", "Alex \u202eProduct", "A bidirectional override character."),
    ):
        user.call(
            f"put-{name}", "PUT", "/api/settings/bot-name", body={"bot_name": value}, note=note
        )
    user.call(
        "put-wrong-type",
        "PUT",
        "/api/settings/bot-name",
        body={"bot_name": 12},
        note="Not a string: the framework's own validation answer, not `bot_name_invalid`.",
    )
    user.call(
        "read-after-refusals", "GET", "/api/settings/bot-name", note="Refusals changed nothing."
    )
    user.call(
        "put-without-csrf",
        "PUT",
        "/api/settings/bot-name",
        body={"bot_name": "X"},
        csrf=None,
        note="A write without the CSRF token.",
    )

    # A meeting sent with the custom name, over the real route.
    user.call(
        "save-for-meeting", "PUT", "/api/settings/bot-name", body={"bot_name": "Alex from Product"}
    )
    with Session(engine) as session:
        template = session.scalars(
            select(Template)
            .join(User, User.id == Template.owner_user_id)
            .where(User.email == base.ADMIN_EMAIL)
        ).first()
        template_id = template.id
    created = user.call(
        "create-meeting-custom-name",
        "POST",
        "/api/meetings",
        body={"url": "https://meet.google.com/abc-defg-hij", "template_id": template_id},
        note="A manual meeting. The stand-in Skribby received the name: see the next file.",
    )
    free_the_slot(engine, created.json()["id"])
    zoom = user.call(
        "create-meeting-zoom",
        "POST",
        "/api/meetings",
        body={"url": "https://us02web.zoom.us/j/81234567890", "template_id": template_id},
        note="A Zoom meeting by the same person.",
    )
    free_the_slot(engine, zoom.json()["id"])
    user.call(
        "clear", "PUT", "/api/settings/bot-name", body={"bot_name": None}, note="`null` clears."
    )
    user.call(
        "clear-with-empty-string",
        "PUT",
        "/api/settings/bot-name",
        body={"bot_name": ""},
        note='`""` clears too.',
    )
    started = user.call(
        "create-meeting-default-name",
        "POST",
        "/api/meetings",
        body={"url": "https://meet.google.com/ddd-eeee-fff", "template_id": template_id},
        note="Cleared: the meeting is sent with the server default.",
    )
    base.note_file(
        "skribby-stand-in-received",
        "What the stand-in Skribby received on `POST /bot`, in order: the three meetings above. "
        "Only `bot_name` (and the platform) are relevant here; the rest is the app's usual body.",
        {
            "received": [
                {
                    "service": body.get("service"),
                    "bot_name": body.get("bot_name"),
                    "has_authentication": "authentication" in body,
                }
                for body in RECEIVED
            ]
        },
    )

    ids = {
        "custom": created.json()["id"],
        "zoom": zoom.json()["id"],
        "default": started.json()["id"],
    }
    with Session(engine) as session:
        rows = session.scalars(select(Meeting).where(Meeting.id.in_(ids.values()))).all()
        base.note_file(
            "stored-meeting-names",
            "`meetings.bot_name` of the three meetings (a database read; this column is not in any "
            "API answer).",
            {
                label: next(m.bot_name for m in rows if m.id == meeting_id)
                for label, meeting_id in ids.items()
            },
        )
        # An older meeting: made before the column existed, so null.
        old = Meeting(
            owner_user_id=rows[0].owner_user_id,
            url="https://meet.google.com/ggg-hhhh-iii",
            platform="google_meet",
            native_id="ggg-hhhh-iii",
            template_id=template_id,
            bot_provider="skribby",
        )
        session.add(old)
        session.commit()
        ids["old"] = old.id
        session.execute(
            update(Meeting)
            .where(Meeting.id.in_(ids.values()))
            .values(
                status="bot_sent",
                completion_reason="awaiting_admission",
                bot_state_seen_at=datetime.now(UTC),
            )
        )
        session.commit()
    for label, note in (
        ("custom", "Waiting to be let in: the line names the name this meeting's bot joined with."),
        ("zoom", "The same for the Zoom meeting."),
        ("default", "The meeting sent after clearing: the server default."),
        ("old", "A meeting made before the feature (null name): the server default, as before."),
    ):
        user.call(f"meeting-{label}-admit-line", "GET", f"/api/meetings/{ids[label]}", note=note)


def main() -> None:
    if not sys.argv[1:]:
        sys.exit(__doc__)
    base.prepare_database()
    shutil.rmtree(OUT, ignore_errors=True)
    OUT.mkdir(parents=True)
    stand_in = start_stand_in()
    skribby.BASES["eu"] = stand_in  # the real client, pointed at the local stand-in
    settings = Settings(
        _env_file=None,
        app_env="test",
        session_secret="capture-secret-" + "x" * 30,
        cookie_secure=False,
        database_url=base.SCRATCH_URL,
        public_base_url="https://notes.example.test",
        token_enc_key=base.KEY,
    )
    app = create_app(settings)  # `get_bot_provider` is NOT overridden: the real Skribby provider
    server, server_base = base.start_server(app)
    engine = create_engine(base.SCRATCH_URL)
    try:
        run(app, server_base, engine)
    finally:
        server.should_exit = True
        engine.dispose()
    show_hidden_characters_as_escapes()


def show_hidden_characters_as_escapes() -> None:
    """The refused name with a bidirectional override is saved as `\\u202e` in the JSON, not as the
    invisible character itself, so a reader (or a review tool) can see what was sent."""
    for path in OUT.glob("*.json"):
        text = path.read_text()
        fixed = "".join(
            f"\\u{ord(char):04x}" if unicodedata.category(char) == "Cf" else char for char in text
        )
        if fixed != text:
            path.write_text(fixed)


if __name__ == "__main__":
    main()
