"""Drive `POST /api/meetings` with the optional `title` over real HTTP and save every exchange for
the frontend guide.

    python scripts/capture_meeting_title.py postgresql+psycopg://USER:PASS@localhost:PORT/postgres

Same set-up as `scripts/capture_meeting_delete.py`: real migrations, the real app under uvicorn,
real HTTP, only Vexa and OpenAI replaced by fakes. Between sends the earlier meetings are set to
`ready` by SQL (the worker is not running), so the account is never at its bot limit. Files go to
`docs/fe-captures/meeting-title/NN-name.json`.

What is NOT real: Vexa and OpenAI (fakes), the worker (it never runs).
"""

import shutil
import sys

import capture_owner_api as base

base.OUT = base.ROOT / "docs" / "fe-captures" / "meeting-title"
OUT = base.OUT

from sqlalchemy import create_engine, text  # noqa: E402

from app.config import Settings  # noqa: E402
from app.main import create_app  # noqa: E402
from app.services import get_openai_client, get_vexa  # noqa: E402

Recorder = base.Recorder


def main() -> None:
    if not sys.argv[1:]:
        sys.exit(__doc__)
    base.prepare_database()
    shutil.rmtree(OUT, ignore_errors=True)
    OUT.mkdir(parents=True)
    settings = Settings(
        _env_file=None,
        app_env="test",
        session_secret="capture-secret-" + "x" * 30,
        cookie_secure=False,
        database_url=base.scratch_url,
    )
    vexa, openai_client = base.FakeVexa(), base.FakeOpenAI()
    app = create_app(settings)
    app.dependency_overrides[get_vexa] = lambda: vexa
    app.dependency_overrides[get_openai_client] = lambda: openai_client
    server, server_base = base.start_server(app)
    engine = create_engine(base.scratch_url)
    try:
        run(server_base, engine)
    finally:
        server.should_exit = True
        engine.dispose()


def finish_all(engine) -> None:
    with engine.begin() as connection:
        connection.execute(text("update meetings set status = 'ready', step = 'done'"))


def run(server_base, engine) -> None:
    owner = Recorder(server_base)
    owner.call("session-anonymous", "GET", "/api/session", note="Read the CSRF token.")
    owner.call("login", "POST", "/api/session", body=base.LOGIN)
    template_id = owner.call("templates", "GET", "/api/templates").json()["templates"][0]["id"]

    def create(name, code, note, **extra):
        response = owner.call(
            name,
            "POST",
            "/api/meetings",
            body={"url": f"https://meet.google.com/{code}", "template_id": template_id, **extra},
            note=note,
        )
        finish_all(engine)
        return response

    create(
        "create-with-title",
        "aaa-bbbb-ccc",
        "A pasted link with a title: `title` is the words typed, and the list shows them.",
        title="Weekly planning",
    )
    create(
        "create-without-title",
        "ddd-eeee-fff",
        "No `title` key: today's behaviour, named by the platform and the code.",
    )
    create(
        "create-title-null",
        "ggg-hhhh-iii",
        "`title: null` is the same as leaving it out.",
        title=None,
    )
    create(
        "create-title-blank",
        "jjj-kkkk-lll",
        "Only spaces: no title is stored, the fallback name is shown (201, not an error).",
        title="   ",
    )
    create(
        "create-title-cleaned",
        "mmm-nnnn-ooo",
        "A newline, a tab and a control character become single spaces, then the ends are trimmed.",
        title="  Q4\n\tplanning \u0000 with   spaces  ",
    )
    create(
        "create-title-markup",
        "ppp-qqqq-rrr",
        "Markup is kept as plain text, exactly as for a calendar event's title: the pages draw "
        "it as text and never as HTML.",
        title='<b>Plan</b> & "review"',
    )
    create(
        "create-title-long",
        "sss-tttt-uuu",
        "300 characters: cut to 200, the last one an ellipsis (201, not an error).",
        title="x" * 300,
    )
    create(
        "create-title-huge",
        "vvv-wwww-xxx",
        "More than 1000 characters: 422 validation_error on `title`, nothing sent or saved.",
        title="y" * 1001,
    )
    create(
        "create-title-not-text",
        "yyy-zzzz-aaa",
        "A number is not text: 422 validation_error on `title`.",
        title=5,
    )
    owner.call(
        "create-bad-link-with-title",
        "POST",
        "/api/meetings",
        body={"url": "https://example.com/x", "template_id": template_id, "title": "Weekly"},
        note="A refused link stays refused, with the usual 400.",
    )
    owner.call(
        "meetings-list",
        "GET",
        "/api/meetings",
        note="The list: a pasted-link meeting is named by its title, or by platform and code.",
    )


if __name__ == "__main__":
    main()
