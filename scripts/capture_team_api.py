"""Drive the Team API over real HTTP and save every exchange for the frontend guide.

    python scripts/capture_team_api.py postgresql+psycopg://USER:PASS@HOST:PORT/postgres

The argument is any Postgres server the script may create a scratch database on. It runs the real
migrations there, starts the real app under uvicorn and signs in as placeholder users. Each exchange
is written to `docs/fe-captures/team-w3/NN-name.json`, so every claim in
`docs/fe-integration-guide-team.md` can be re-derived by running this again.

What is real: the app, auth, CSRF, validation, roles and visibility, the database, migrations,
serialisation, every error, the invitation links (read from the mail outbox, as the mail provider
would be handed them), sign-up and email confirmation with a team link (`/api/signup`,
`/api/verify-email`), and the rendering of the Team invite mail by the real builder
(`mail.deliver_pending`).

What is NOT real, and is labelled where it matters:
* **The mail provider.** The message is built and handed to a capturing mailer instead of Resend.
* **Time.** An expired link and the one-minute resend wait are made by changing the row's
  timestamps in the database, never by waiting.
* **Google sign-in** is not exercised here (it needs Google): the same joining code is covered by
  `tests/test_team.py`, not by a capture.
* The recording, transcription and speaker steps and the bot: the meeting row is inserted as the
  pipeline leaves it (`status ready`).
Nobody's real meeting or address is involved; every name and address is a placeholder.

Secrets in the files are masked: CSRF tokens become `<csrf-A>`, `<csrf-B>` in order of first
appearance, cookies `<cookie>`, and the token inside an invitation link `<invite-token-N>`.
"""

import json
import os
import re
import shutil
import socket
import sys
import threading
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "docs" / "fe-captures" / "team-w3"
DB_NAME = "notetaker_capture_team"
ADMIN_EMAIL = "admin@example.test"
ADMIN_PASSWORD = "-".join(["capture", "admin", "placeholder"])
PEOPLE = {  # email -> (name, password); all placeholders
    "dan@example.test": ("Dan Okoro", "-".join(["dan", "chosen", "password", "1"])),
    "chidi@example.test": ("Chidi Eze", "-".join(["chidi", "chosen", "password", "2"])),
    "eve@example.test": ("Eve Outsider", "-".join(["eve", "chosen", "password", "4"])),
}
ADA = ("ada@example.test", "Ada Lovelace", "-".join(["ada", "chosen", "password", "5"]))

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

from app import accounts, mail  # noqa: E402
from app.config import Settings  # noqa: E402
from app.main import create_app  # noqa: E402
from app.models import AppState, MailOutbox, Meeting, TeamInvite, Template, User  # noqa: E402
from app.passwords import hash_password  # noqa: E402
from app.services import get_vexa  # noqa: E402

TOKEN_IN_LINK = re.compile(r"token=([\w-]+)")
token_names: dict[str, str] = {}


def hide_tokens(value):
    """Invitation tokens are secrets: show a stable placeholder instead."""
    if isinstance(value, str):

        def swap(m):
            name = token_names.setdefault(m[1], f"<invite-token-{len(token_names) + 1}>")
            return f"token={name}"

        return TOKEN_IN_LINK.sub(swap, value)
    if isinstance(value, list):
        return [hide_tokens(v) for v in value]
    if isinstance(value, dict):
        return {
            k: (
                f"<{k.replace('_', '-')}>"
                if k in ("token", "invite_token") and isinstance(v, str)
                else hide_tokens(v)
            )
            for k, v in value.items()
        }
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
        print(f"{Recorder.count:02d}-{name:46} {method:6} {path:40} -> {response.status_code}")
        return response


def note_file(name: str, note: str, content) -> None:
    Recorder.count += 1
    record = {"note": note, "content": hide_tokens(content)}
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


class CapturingMailer:
    def __init__(self):
        self.sent = []

    def send(self, message, key):
        self.sent.append(message)


def make_people(session) -> int:
    for email, (name, password) in PEOPLE.items():
        user = User(
            email=email,
            name=name,
            password_hash=hash_password(password),
            email_verified_at=datetime.now(UTC),
        )
        session.add(user)
        session.flush()
        accounts.add_user_defaults(session, user)
    state = session.get(AppState, 1) or AppState(id=1)
    state.signups_open = False  # the pilot is CLOSED: a team link must still work
    state.password_signup_enabled = True
    state.max_accounts = 10
    session.add(state)
    session.commit()
    return session.scalars(
        select(Template.id)
        .join(User, User.id == Template.owner_user_id)
        .where(User.email == "dan@example.test", Template.name == "General")
    ).one()


def link_for(engine, to: str, template: str) -> str:
    """The raw token in the newest queued mail of this kind to this address (what the person's
    mail would carry)."""
    with Session(engine) as session:
        row = session.scalars(
            select(MailOutbox)
            .where(MailOutbox.to_email == to, MailOutbox.template == template)
            .order_by(MailOutbox.id.desc())
        ).first()
        return TOKEN_IN_LINK.search(row.payload["link"])[1]


def run(base: str, engine) -> None:
    clients = {
        email: Recorder(base, name.split()[0].lower()) for email, (name, _) in PEOPLE.items()
    }
    dan, chidi, eve = (clients[e] for e in PEOPLE)
    with Session(engine) as session:
        template_id = make_people(session)
        meeting = Meeting(
            owner_user_id=session.scalars(select(User.id).where(User.email == "dan@example.test")).one(),
            url="https://example.test/meeting", platform="google_meet", native_id="nat-aaaa-bbb",
            template_id=template_id, status="ready", step="done", title="Planning call",
            transcript="[Dan]: Hello.", speaker_transcript="[Dan]: Hello.",
            notes="## Summary\nA short call.\n\n## Decisions\nNone noted\n\n## Action items\nNone noted\n\n## Open questions\nNone noted\n\n## Please check\nNone noted\n",
            created_at=datetime(2026, 11, 3, 10, 0, tzinfo=UTC),
        )  # fmt: skip
        session.add(meeting)
        session.commit()
        meeting_id = meeting.id
    for email, rec in clients.items():
        rec.call("session", "GET", "/api/session")
        rec.call(
            "login", "POST", "/api/session", body={"email": email, "password": PEOPLE[email][1]}
        )
    anon = Recorder(base, "visitor")
    anon.call("team-logged-out", "GET", "/api/team", note="No login: 401.")

    # --- a team of one ---
    dan.call("team-of-one", "GET", "/api/team", note="Every account has a team from sign-up.")
    dan.call("members-of-one", "GET", "/api/team/members")

    # --- inviting ---
    dan.call(
        "invite", "POST", "/api/team/invites",
        body={"invitees": [{"email": "Ada@Example.test", "name": "Ada L"}, {"email": "chidi@example.test"}, {"email": "not an email"}], "role": "member"},
        note="One new person (needs a pilot place), one existing account, one bad address. Same "
        "`status` and shape for the first two: the answer never says who has an account.",
    )  # fmt: skip
    dan.call(
        "invite-again", "POST", "/api/team/invites",
        body={"invitees": [{"email": "ada@example.test"}], "role": "member"},
        note="Within a minute of the last mail: `already_invited`, no second mail.",
    )  # fmt: skip
    dan.call(
        "members-with-invites",
        "GET",
        "/api/team/members",
        note="`invites` lists the open ones; no token anywhere.",
    )
    chidi_link, ada_link = (
        link_for(engine, "chidi@example.test", "team_invite"),
        link_for(engine, "ada@example.test", "team_invite"),
    )
    with Session(engine) as session:
        rows = session.scalars(
            select(MailOutbox).where(MailOutbox.template == "team_invite").order_by(MailOutbox.id)
        ).all()
        note_file("mail-outbox", "Queued Team invite mails (payload holds the link: scrubbed once sent).",
                  [dict(to=r.to_email, template=r.template, payload=r.payload, status=r.status) for r in rows])  # fmt: skip
        mailer = CapturingMailer()
        later = datetime.now(UTC) + timedelta(hours=1)
        for row in rows:
            row.created_at = later - timedelta(minutes=5)
        session.commit()
        mail.deliver_pending(
            session,
            mailer,
            Settings(_env_file=None, public_base_url="https://notes.example.test"),
            later,
        )
        note_file("mail-rendered", "What the mail provider would be handed (CAPTURING MAILER, not Resend).",
                  [dict(to=m.to, subject=m.subject, text=m.text, html=m.html) for m in mailer.sent if m.to == "ada@example.test"])  # fmt: skip

    # --- what a link says, before anyone signs in ---
    anon.call(
        "preview-new-person", "GET", f"/api/team/invites/preview?token={ada_link}",
        note="PUBLIC: the token is the credential. `has_account` false: sign up with the link.",
    )  # fmt: skip
    anon.call(
        "preview-existing-account", "GET", f"/api/team/invites/preview?token={chidi_link}",
        note="`has_account` true: sign in, then accept.",
    )  # fmt: skip
    anon.call("preview-bad-token", "GET", "/api/team/invites/preview?token=nope", note="400 `invite_invalid`: the one answer for every dead link.")  # fmt: skip

    # --- a new person signs up with the link, in a CLOSED pilot ---
    ada = Recorder(base, "ada")
    ada.call("ada-session", "GET", "/api/session")
    ada.call(
        "signup-without-link", "POST", "/api/signup",
        body={"name": ADA[1], "email": ADA[0], "password": ADA[2], "accept_terms": True, "accept_recording": True},
        note="No link: the pilot is closed, 403 `pilot_closed`.",
    )  # fmt: skip
    ada.call(
        "signup-with-someone-elses-link", "POST", "/api/signup",
        body={"name": "Eve", "email": "eve2@example.test", "password": ADA[2], "accept_terms": True, "accept_recording": True, "invite_token": ada_link},
        note="A link works for its own address only: 400 `invite_invalid`.",
    )  # fmt: skip
    ada.call(
        "signup-with-link", "POST", "/api/signup",
        body={"name": ADA[1], "email": ADA[0], "password": ADA[2], "accept_terms": True, "accept_recording": True, "invite_token": ada_link},
        note="202, as for every sign-up. The pilot is closed and this still works.",
    )  # fmt: skip
    verify = link_for(engine, ADA[0], "verify_email")
    ada.call(
        "verify-email", "POST", "/api/verify-email", body={"token": verify, "password": ADA[2]},
        note="Confirming the address is what joins the team.",
    )  # fmt: skip
    ada.call("ada-login", "POST", "/api/session", body={"email": ADA[0], "password": ADA[2]})
    ada.call("ada-team", "GET", "/api/team", note="`my_role` member, `can_manage` false.")
    dan.call("members-after-ada", "GET", "/api/team/members", note="Ada is on the team, with 'Ada L' as an alias; her invite is gone from `invites`.")  # fmt: skip

    # --- an existing account accepts ---
    chidi.call("accept-wrong-token", "POST", "/api/team/invites/accept", body={"token": "x" * 43}, note="400 `invite_invalid`: one answer for every bad link.")  # fmt: skip
    chidi.call("accept", "POST", "/api/team/invites/accept", body={"token": chidi_link}, note="Chidi's team of one dissolves into Dan's.")  # fmt: skip
    chidi.call("accept-again", "POST", "/api/team/invites/accept", body={"token": chidi_link}, note="Single use: 400 `invite_invalid`.")  # fmt: skip
    dan.call("team-three", "GET", "/api/team")

    # --- a member may read, not manage ---
    ada.call("member-invite", "POST", "/api/team/invites", body={"invitees": [{"email": "x@example.test"}], "role": "member"}, note="403 `team_forbidden`.")  # fmt: skip
    ada.call("member-patch-team", "PATCH", "/api/team", body={"name": "Mine"})
    ada.call("member-remove", "DELETE", f"/api/team/members/{_uid(engine, 'chidi@example.test')}")
    ada.call("member-members", "GET", "/api/team/members", note="Each row says what the caller may do (`can_remove`, `can_change_role`, `can_edit_aliases`).")  # fmt: skip
    eve.call("outsider-patch-member", "PATCH", f"/api/team/members/{_uid(engine, 'chidi@example.test')}", body={"aliases": ["x"]}, note="Someone on another team: 404, never 403.")  # fmt: skip
    eve.call(
        "outsider-remove-member", "DELETE", f"/api/team/members/{_uid(engine, 'ada@example.test')}"
    )
    eve.call("outsider-team", "GET", "/api/team/members", note="Eve sees only her own team of one.")

    # --- roles and aliases ---
    dan.call("role-to-admin", "PATCH", f"/api/team/members/{_uid(engine, 'chidi@example.test')}", body={"role": "admin"}, note="Owner only. The answer is the member list.")  # fmt: skip
    chidi.call("admin-invites-admin", "POST", "/api/team/invites", body={"invitees": [{"email": "y@example.test"}], "role": "admin"}, note="Only the owner may invite an admin: 403.")  # fmt: skip
    chidi.call("admin-changes-role", "PATCH", f"/api/team/members/{_uid(engine, 'ada@example.test')}", body={"role": "admin"}, note="Admins cannot change roles: 403.")  # fmt: skip
    dan.call("owner-role", "PATCH", f"/api/team/members/{_uid(engine, 'dan@example.test')}", body={"role": "member"}, note="The owner's role is fixed: 403.")  # fmt: skip
    dan.call("role-owner-invalid", "PATCH", f"/api/team/members/{_uid(engine, 'ada@example.test')}", body={"role": "owner"}, note="`owner` is not a role you can give: 422.")  # fmt: skip
    chidi.call("admin-edits-aliases", "PATCH", f"/api/team/members/{_uid(engine, 'ada@example.test')}", body={"aliases": ["Ada L", "Countess", " countess ", ""]}, note="Aliases replace the list; cleaned and de-duplicated.")  # fmt: skip
    ada.call(
        "member-edits-own-aliases",
        "PATCH",
        f"/api/team/members/{_uid(engine, 'ada@example.test')}",
        body={"aliases": ["Ada"]},
    )
    ada.call("member-edits-other-aliases", "PATCH", f"/api/team/members/{_uid(engine, 'dan@example.test')}", body={"aliases": ["Boss"]}, note="403.")  # fmt: skip
    ada.call("aliases-too-many", "PATCH", f"/api/team/members/{_uid(engine, 'ada@example.test')}", body={"aliases": ["a"] * 21}, note="At most 20: 422.")  # fmt: skip
    dan.call("team-settings", "PATCH", "/api/team", body={"name": "Product team", "share_new_meetings": True}, note="`share_new_meetings` applies to the owner's and admins' meetings only, at first notes.")  # fmt: skip
    dan.call("team-settings-empty-name", "PATCH", "/api/team", body={"name": "   "})

    # --- resend, expiry and revoke ---
    dan.call(
        "invite-limit-new",
        "POST",
        "/api/team/invites",
        body={"invitees": [{"email": "later@example.test"}], "role": "member"},
    )
    with Session(engine) as session:
        invite_id = session.scalars(
            select(TeamInvite.id).where(TeamInvite.email == "later@example.test")
        ).one()
    dan.call(
        "resend-too-soon",
        "POST",
        f"/api/team/invites/{invite_id}/resend",
        note="Within a minute of the last mail: 429 `too_soon`.",
    )
    with Session(engine) as session:
        row = session.get(TeamInvite, invite_id)
        row.last_sent_at, row.expires_at = (
            datetime.now(UTC) - timedelta(days=8),
            datetime.now(UTC) - timedelta(days=1),
        )  # TIME FAKED
        session.commit()
    dan.call(
        "members-expired-invite",
        "GET",
        "/api/team/members",
        note="TIME FAKED in the database: the invite reads `expired`.",
    )
    old = link_for(engine, "later@example.test", "team_invite")
    dan.call(
        "resend",
        "POST",
        f"/api/team/invites/{invite_id}/resend",
        note="A new link, 7 more days; the old link stops working.",
    )
    new = link_for(engine, "later@example.test", "team_invite")
    note_file(
        "resend-links-differ", "The link in the mail changes on resend.", dict(changed=old != new)
    )
    dan.call("revoke", "DELETE", f"/api/team/invites/{invite_id}")
    dan.call(
        "revoke-again",
        "DELETE",
        f"/api/team/invites/{invite_id}",
        note="404: no open invitation by that id.",
    )
    dan.call("invite-not-mine", "DELETE", f"/api/team/invites/{invite_id}")
    eve.call("outsider-resend", "POST", f"/api/team/invites/{invite_id}/resend")

    # --- a full pilot ---
    with Session(engine) as session:
        state = session.get(AppState, 1)
        state.max_accounts = 4  # dan, chidi, eve, ada
        session.commit()
    dan.call("invite-pilot-full", "POST", "/api/team/invites", body={"invitees": [{"email": "newcomer@example.test"}], "role": "member"}, note="No free place: 409 `pilot_full`, nothing sent, nobody invited.")  # fmt: skip
    dan.call("invite-existing-when-full", "POST", "/api/team/invites", body={"invitees": [{"email": "eve@example.test"}], "role": "member"}, note="An account needs no place: it still works.")  # fmt: skip

    # --- sharing a meeting ---
    ada.call(
        "shared-before", "GET", f"/api/meetings/{meeting_id}", note="Private: 404 for a teammate."
    )
    dan.call(
        "share",
        "POST",
        f"/api/meetings/{meeting_id}/share",
        note="The owner shares; the answer is the meeting.",
    )
    ada.call("shared-read", "GET", f"/api/meetings/{meeting_id}", note="A teammate reads it: `can_share` false, `shared_by` names the owner, every `can_*` false.")  # fmt: skip
    ada.call(
        "teammate-share", "POST", f"/api/meetings/{meeting_id}/share", note="Only the owner: 404."
    )
    ada.call("teammate-unshare", "DELETE", f"/api/meetings/{meeting_id}/share")
    eve.call("outsider-share", "POST", f"/api/meetings/{meeting_id}/share")
    dan.call("unshare", "DELETE", f"/api/meetings/{meeting_id}/share")
    ada.call("shared-after-unshare", "GET", f"/api/meetings/{meeting_id}")

    # --- tasks chips: the people list ---
    dan.call(
        "tasks-people",
        "GET",
        "/api/tasks",
        note="`people` is the active members of the team: what the chips are built from.",
    )

    # --- leaving and removing ---
    dan.call("owner-leave", "DELETE", f"/api/team/members/{_uid(engine, 'dan@example.test')}", note="The owner cannot leave while others remain: 409.")  # fmt: skip
    ada.call("leave", "DELETE", f"/api/team/members/{_uid(engine, 'ada@example.test')}", note="A member leaves: 204. They get a fresh team of one.")  # fmt: skip
    ada.call("after-leave-team", "GET", "/api/team")
    chidi.call("admin-removes-owner", "DELETE", f"/api/team/members/{_uid(engine, 'dan@example.test')}", note="403.")  # fmt: skip
    dan.call("remove-admin", "DELETE", f"/api/team/members/{_uid(engine, 'chidi@example.test')}", note="The owner removes an admin: 204.")  # fmt: skip
    dan.call("final-team", "GET", "/api/team/members")


def _uid(engine, email: str) -> int:
    with Session(engine) as session:
        return session.scalars(select(User.id).where(User.email == email)).one()


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
