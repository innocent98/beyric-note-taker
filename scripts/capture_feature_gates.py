"""Drive the feature gates over real HTTP and save every exchange for the billing guide (stage S3).

    python scripts/capture_feature_gates.py postgresql+psycopg://USER:PASS@localhost:PORT/postgres

Same setup as `scripts/capture_hours_usage.py` (it imports `capture_calendar_connect` for the HTTP
recorder, the scratch database and the app server): real migrations, the real app under uvicorn,
real HTTP, in three phases: `FEATURE_GATES_MODE=enforce`, then `shadow`, then `off`. The mode is
read from the process settings (`app.feature_gates.get_settings`), as in production where web has
one setting; the script swaps it between phases.

Files go to `docs/fe-captures/feature-gates/NN-name.json`. Passwords in login calls are
placeholders.

What is real: the app, the routes, the gate, the entitlement rule, the grants, the 403 refusal, the
shadow log line (the real logger, read through a handler this script attaches), the database
rows. What is NOT, said in each file's note where it matters:
- The people, their meetings and their notes are placeholders. A person's grants were inserted by
  SQL in the shape the code makes: sign-up gives nobody a trial yet (stage S10) and no purchase
  exists yet (stage S8), so no route makes a `trial` grant.
- The notes model is a fake client (the rewrite let through must not call OpenAI): the
  rewritten notes in that capture are the fake's text. Ask is shown on a question that finds
  nothing, which passes the gate and answers without a model call. **No OpenAI call is made.**
- `past_due` and `lapsed` do not exist until stage S8: not captured, covered by the table test.
"""

import logging
import shutil
import sys
from datetime import UTC, datetime, timedelta

import capture_calendar_connect as base  # reads sys.argv at import (the admin database URL)

OUT = base.ROOT / "docs" / "fe-captures" / "feature-gates"
base.OUT = OUT

from sqlalchemy import create_engine  # noqa: E402
from sqlalchemy.orm import Session  # noqa: E402

from app import feature_gates  # noqa: E402
from app.config import Settings  # noqa: E402
from app.main import create_app  # noqa: E402
from app.models import HourGrant, Meeting, Template, User  # noqa: E402
from app.passwords import hash_password  # noqa: E402
from app.services import get_bot_provider, get_openai_client  # noqa: E402
from app.usage import email_hash  # noqa: E402
from tests.test_web import NOTES, FakeOpenAI  # noqa: E402

Recorder = base.Recorder
HOUR = 3600
PASSWORD = "-".join(["capture", "person", "password"])  # a placeholder, scratch database only
TRANSCRIPT = "[Adebayo]: We ship on Friday.\n[Tunde]: I will send the contract."


def person(engine, email: str, *, grant: str | None = None) -> tuple[int, int, int]:
    """A verified account with two templates and one ready meeting; `grant` (a kind) gives it
    hours. Returns `(user id, meeting id, second template id)`."""
    now = datetime.now(UTC)
    with Session(engine) as session:
        user = User(email=email, password_hash=hash_password(PASSWORD), email_verified_at=now)
        session.add(user)
        session.flush()
        general = Template(owner_user_id=user.id, name="General", instructions="Write notes.")
        short = Template(owner_user_id=user.id, name="Short", instructions="Be brief.")
        session.add_all([general, short])
        session.flush()
        meeting = Meeting(
            owner_user_id=user.id,
            url="https://meet.google.com/abc-defg-hij",
            platform="google_meet",
            native_id="abc-defg-hij",
            template_id=general.id,
            status="ready",
            step="done",
            notes=NOTES,
            transcript=TRANSCRIPT,
            speaker_transcript=TRANSCRIPT,
        )
        session.add(meeting)
        if grant:
            session.add(
                HourGrant(
                    user_id=user.id,
                    email_hash=email_hash(email),
                    kind=grant,
                    seconds=3 * HOUR,
                    starts_at=now - timedelta(days=1),
                    expires_at=now + timedelta(days=13),
                )
            )
        session.commit()
        return user.id, meeting.id, short.id


class LogTap(logging.Handler):
    """The lines the gate logs, kept so a capture can show them."""

    def __init__(self) -> None:
        super().__init__(logging.INFO)
        self.lines: list[str] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.lines.append(record.getMessage())


def signed_in(server_base: str, who: str, email: str) -> Recorder:
    client = Recorder(server_base, who)
    base.login(client, f"login-{who.split()[0]}", email, PASSWORD)
    return client


def refused_set(c: Recorder, meeting_id: int, short_id: int, tag: str) -> None:
    """The three gated calls and the share in French, for a person the gate refuses."""
    c.call(
        f"{tag}-ask",
        "POST",
        "/api/ask",
        body={"question": "What did we decide about pricing?"},
        note="Ask Beyric, caller has no live plan or trial: 403 `plan_inactive`. No model call, "
        "nothing written.",
    )
    c.call(
        f"{tag}-ask-my-tasks",
        "POST",
        "/api/ask",
        body={"question": "What are my open tasks?"},
        note="The free 'my tasks' answer is refused too: Ask is one switch.",
    )
    c.call(
        f"{tag}-translate",
        "POST",
        f"/api/meetings/{meeting_id}/translations",
        body={"language": "fr"},
        note="A NEW translation: 403 `plan_inactive`. Nothing is queued.",
    )
    c.call(
        f"{tag}-rewrite",
        "POST",
        f"/api/meetings/{meeting_id}/regenerate",
        body={"template_id": short_id},
        note="Rewrite the notes with another template: 403 `plan_inactive`. The old notes are "
        "kept and the notes model is not called.",
    )
    c.call(
        f"{tag}-share-french",
        "POST",
        f"/api/meetings/{meeting_id}/shares",
        body={"recipients": [{"email": "client@example.test"}], "language": "fr"},
        note="Sending the notes in French needs a NEW translation, so it is gated too: 403 "
        "`plan_inactive`, no share and no translation are made.",
    )


def run(engine, server_base: str, set_mode, tap: LogTap) -> None:
    ada = "ada@example.test"
    nora = "nora@example.test"
    ada_id, ada_meeting, ada_short = person(engine, ada, grant="trial")
    nora_id, nora_meeting, nora_short = person(engine, nora)

    # ---- enforce: a person with no plan (nora) and a person on a trial (ada) ----
    set_mode("enforce")
    n = signed_in(server_base, "nora no plan", nora)
    refused_set(n, nora_meeting, nora_short, "enforce-noplan")
    n.call(
        "enforce-noplan-read-list",
        "GET",
        "/api/meetings",
        note="Reading is never gated: the same person lists their meetings.",
    )
    n.call(
        "enforce-noplan-read-detail",
        "GET",
        f"/api/meetings/{nora_meeting}",
        note="...and opens one, notes and transcript included.",
    )
    n.call(
        "enforce-noplan-ask-scope",
        "GET",
        "/api/ask/scope",
        note="The Ask scope line and chips are not gated (no model call).",
    )
    n.call(
        "enforce-noplan-review",
        "GET",
        "/api/review",
        note="Review (and its 'add the corrected spelling' step, which calls no model) stays open.",
    )

    a = signed_in(server_base, "ada trial", ada)
    a.call(
        "enforce-trial-ask",
        "POST",
        "/api/ask",
        body={"question": "zzz qqq xyzzy"},
        note="A person on a trial passes the gate. This question finds nothing, so it answers "
        "`none` with no model call (the capture makes no OpenAI call).",
    )
    a.call(
        "enforce-trial-ask-my-tasks",
        "POST",
        "/api/ask",
        body={"question": "What are my open tasks?"},
        note="The 'my tasks' answer for the same person: 200, no model.",
    )
    a.call(
        "enforce-trial-translate",
        "POST",
        f"/api/meetings/{ada_meeting}/translations",
        body={"language": "fr"},
        note="A new translation for a person on a trial: 202, queued (the worker is not running "
        "in this capture).",
    )
    a.call(
        "enforce-trial-translate-again",
        "POST",
        f"/api/meetings/{ada_meeting}/translations",
        body={"language": "fr"},
        note="Asked again while it is being made: 202, unchanged. A running or cached "
        "translation is never refused, even after a plan ends (tests).",
    )
    a.call(
        "enforce-trial-rewrite",
        "POST",
        f"/api/meetings/{ada_meeting}/regenerate",
        body={"template_id": ada_short},
        note="Rewrite for a person on a trial: 200. The notes model here is a fake client, so "
        "the notes are the fake's text; the shape is the existing meeting detail.",
    )

    # ---- shadow: nora is let through and the gate logs one line each time ----
    set_mode("shadow")
    tap.lines.clear()
    n.call(
        "shadow-noplan-ask",
        "POST",
        "/api/ask",
        body={"question": "zzz qqq xyzzy"},
        note="Shadow: the person with no plan is NOT refused (200, `none`: nothing found, no "
        "model call).",
    )
    n.call(
        "shadow-noplan-translate",
        "POST",
        f"/api/meetings/{nora_meeting}/translations",
        body={"language": "fr"},
        note="Shadow: a new translation is queued (202).",
    )
    base.note_file(
        "shadow-log-lines",
        "The lines the gate logged during the two calls above (logger `app.feature_gates`, level "
        "INFO), read from the real logger through a handler this script attaches (that web's own "
        "logging prints it is `tests/test_logging_setup.py`). "
        f"User id {nora_id} is this scratch database's id. "
        "One line per refused-if-enforced call; ids and state only, never a name or an email.",
        {"lines": list(tap.lines)},
    )

    # ---- off: nothing is read and nothing is logged ----
    set_mode("off")
    tap.lines.clear()
    n.call(
        "off-noplan-ask",
        "POST",
        "/api/ask",
        body={"question": "zzz qqq xyzzy"},
        note="Off (the default): as before the gates existed.",
    )
    base.note_file(
        "off-log-lines",
        "The lines the gate logged during the call above: none.",
        {"lines": list(tap.lines)},
    )


def main() -> None:
    if not sys.argv[1:]:
        sys.exit(__doc__)
    base.prepare_database()
    shutil.rmtree(OUT, ignore_errors=True)
    OUT.mkdir(parents=True)

    def settings_for(mode: str) -> Settings:
        return Settings(
            _env_file=None,
            app_env="test",
            session_secret="capture-secret-" + "x" * 30,
            cookie_secure=False,
            database_url=base.SCRATCH_URL,
            public_base_url="https://notes.example.test",
            token_enc_key=base.KEY,
            feature_gates_mode=mode,
        )

    current = {"settings": settings_for("enforce")}
    feature_gates.get_settings = lambda: current["settings"]

    def set_mode(mode: str) -> None:
        current["settings"] = settings_for(mode)

    app = create_app(current["settings"])
    app.dependency_overrides[get_bot_provider] = lambda: base.FakeBot()
    app.dependency_overrides[get_openai_client] = lambda: FakeOpenAI()
    server, server_base = base.start_server(app)
    tap = LogTap()
    logging.getLogger(feature_gates.__name__).addHandler(tap)
    logging.getLogger(feature_gates.__name__).setLevel(logging.INFO)
    engine = create_engine(base.SCRATCH_URL)
    try:
        run(engine, server_base, set_mode, tap)
        for path in OUT.glob("*.json"):  # no password, even a placeholder, stays in a file
            text = path.read_text()
            for secret in (PASSWORD, base.ADMIN_PASSWORD):
                text = text.replace(secret, "<password placeholder>")
            path.write_text(text)
    finally:
        server.should_exit = True
        engine.dispose()


if __name__ == "__main__":
    main()
