"""Drive "recurring meetings follow the meetings" over real HTTP and save every exchange for the
frontend guide and the runbook.

    python scripts/capture_series_follow.py postgresql+psycopg://USER:PASS@HOST:PORT/postgres

Same method as `capture_team_scoped_lists.py`: a scratch database with the real migrations, the real
app under uvicorn on a free local port, httpx for every call, every exchange saved to
`docs/fe-captures/series-follow/NN-name.json`, so each claim in
`docs/fe-integration-guide-series-teams.md` can be re-derived by running this again.

The story, in the order the server steps run it:

1. Flag `SERIES_FOLLOW_MEETINGS` OFF (as production is today), recurring meetings the way the
   calendar link leaves them: ONE row per person holding every meeting of that person's event,
   shared, private and other-team alike (the legacy rows).
2. The real backfill script (`scripts/backfill_series_follow.py`): dry run, `--apply`, dry run
   again. Its output is saved as it printed.
3. The flag ON. Recurring cards and pages for each person and each team; the personal row; the
   errors; a share and an unshare, with the rows following the meeting.
4. A "straggler" (a meeting put back in a row of another scope by hand): the readers hide it, the
   flag-on backfill moves it, and the next dry run says 0.

What is real: the app, auth, CSRF, validation, team roles and visibility, the database, the
migrations, serialisation, every error, the share and unshare routes with their follow hooks, the
calendar link (`series.link_from_calendar`) and the backfill script. What is NOT real: no model is
called (the "Keeps coming up" topics are never computed here: `topics` is `[]` on every page), no
mail is sent or built (the series mail is covered by `capture_series_api.py`), the calendar events
and joins are rows as the sync and the scheduler leave them (Google is not involved), the meetings
are rows as the pipeline leaves them (`status ready`), and the flag is flipped inside the running
process (`get_settings.cache_clear()`), which is what recreating the containers with the new `.env`
does for the real services. Every name and address is a placeholder.
"""

# ruff: noqa: E501  (the notes are prose for the guide, one long line each)

import contextlib
import io
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
OUT = ROOT / "docs" / "fe-captures" / "series-follow"
DB_NAME = "notetaker_capture_series_follow"
ADA = "owner@example.test"  # the account migration 0006 creates (a placeholder)
PAT, QUINN = "pat@example.test", "quinn@example.test"
PASSWORD = "-".join(["capture", "password", "placeholder"])
MEET = "https://meet.google.com/abc-defg-hij"
NOTES = "## Summary\nWe agreed the plan.\n\n## Action items\n- Pat: send the deck\n"


def talk(user) -> str:
    """The recorder and one guest nobody has an account for: no teammate is named in it."""
    return f"[{user.name}]: Let us start.\n[Guest Visitor]: Thanks for having me.\n"


def database_urls(admin_url: str) -> tuple[str, str]:
    parsed = urlparse(admin_url)
    return admin_url, parsed._replace(path=f"/{DB_NAME}").geturl()


admin_url, scratch_url = database_urls(sys.argv[1] if len(sys.argv) > 1 else "")
os.environ["DATABASE_URL"] = scratch_url
os.environ["OWNER_EMAIL"] = ADA
os.environ["OWNER_PASSWORD"] = PASSWORD
os.environ["MULTI_TEAM_ENABLED"] = "true"
os.environ.setdefault("APP_ENV", "development")
os.environ.setdefault("COOKIE_SECURE", "false")  # plain http on localhost
os.environ.setdefault("SESSION_SECRET", "-".join(["capture", "placeholder", "secret"] * 4))
os.environ.pop("SERIES_FOLLOW_MEETINGS", None)  # off, like production today
sys.path.insert(0, str(ROOT))

import httpx  # noqa: E402
import uvicorn  # noqa: E402
from alembic import command  # noqa: E402
from alembic.config import Config  # noqa: E402
from sqlalchemy import create_engine, select, text  # noqa: E402
from sqlalchemy.orm import Session  # noqa: E402

from app import series, teams  # noqa: E402
from app.accounts import create_user_defaults  # noqa: E402
from app.config import Settings, get_settings  # noqa: E402
from app.extraction import fingerprint  # noqa: E402
from app.main import create_app  # noqa: E402
from app.models import (  # noqa: E402
    CalendarConnection,
    CalendarEvent,
    Meeting,
    OpenQuestion,
    ScheduledJoin,
    Series,
    Task,
    Team,
    TeamMember,
    Template,
    User,
)
from app.passwords import hash_password  # noqa: E402
from scripts.backfill_series_follow import run as run_backfill  # noqa: E402


class Recorder:
    count = 0

    def __init__(self, base_url: str, who: str) -> None:
        self.http = httpx.Client(base_url=base_url, follow_redirects=False, timeout=120)
        self.csrf: str | None = None
        self.who = who
        self.masks: dict[str, str] = {}

    def mask(self, token: str) -> str:
        return self.masks.setdefault(token, f"<csrf-{chr(ord('A') + len(self.masks))}>")

    def call(self, name, method, path, *, body=None, note="", team=None):
        headers = {"X-CSRF-Token": self.csrf} if self.csrf and method != "GET" else {}
        if team is not None:
            headers["X-Team-Id"] = str(team)
        had_cookie = "session" in self.http.cookies
        response = self.http.request(method, path, json=body, headers=headers)
        data = response.json() if "json" in response.headers.get("content-type", "") else None
        if isinstance(data, dict) and "csrf_token" in data:
            self.csrf = data["csrf_token"]
        Recorder.count += 1
        sent = {k: self.mask(v) if k == "X-CSRF-Token" else v for k, v in headers.items()}
        if had_cookie:
            sent["Cookie"] = "session=<cookie>"
        shown = json.loads(json.dumps(data))
        if isinstance(shown, dict) and "csrf_token" in shown:
            shown["csrf_token"] = self.mask(shown["csrf_token"])
        record = {
            "note": note,
            "as": self.who,
            "request": {"method": method, "path": path, "headers": sent, "body": body},
            "response": {"status": response.status_code, "body": shown},
        }
        file = OUT / f"{Recorder.count:02d}-{name}.json"
        file.write_text(json.dumps(record, indent=2, ensure_ascii=False) + "\n")
        print(f"{file.name:58} {self.who:6} {method:6} {path:44} -> {response.status_code}")
        return response


def note_file(name: str, note: str, content) -> None:
    Recorder.count += 1
    record = {"note": note, "content": content}
    (OUT / f"{Recorder.count:02d}-{name}.json").write_text(
        json.dumps(record, indent=2, ensure_ascii=False, default=str) + "\n"
    )
    print(f"{Recorder.count:02d}-{name}")


def start_server(app) -> tuple[uvicorn.Server, str]:
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


def make_person(session, email: str, name: str) -> User:
    user = User(
        email=email,
        name=name,
        password_hash=hash_password(PASSWORD),
        email_verified_at=datetime.now(UTC),
    )
    session.add(user)
    session.commit()
    create_user_defaults(session, user)
    teams.ensure_personal_team(session, user)
    session.commit()
    return user


def make_team(session, boss: User, name: str, *members: User) -> Team:
    team = Team(owner_user_id=boss.id, name=name, is_personal=False)
    session.add(team)
    session.flush()
    session.add(TeamMember(team_id=team.id, user_id=boss.id, role="owner"))
    for person in members:
        session.add(TeamMember(team_id=team.id, user_id=person.id, role="member"))
    session.commit()
    return team


def seed(engine) -> dict:
    """Two recurring events, linked by the real calendar link with the flag OFF (so each person's
    meetings of an event land in ONE row, the legacy shape):

    * Ada's "Standup" (one team, X): Monday-style occurrences. A1 shared to X, A2 private.
    * Pat's "Design sync" (Pat is in X and in Y, default team his personal one): P1 shared to X,
      P2 shared to Y, P3 private.
    """
    now = datetime.now(UTC)
    with Session(engine) as session:
        ada = session.scalars(select(User).where(User.email == ADA)).one()
        ada.name = "Ada Example"
        session.commit()
        teams.ensure_personal_team(session, ada)
        session.commit()
        pat = make_person(session, PAT, "Pat Example")
        quinn = make_person(session, QUINN, "Quinn Example")
        x = make_team(session, ada, "Platform team", pat)
        y = make_team(session, quinn, "Client Y", pat)
        general = {
            u.id: session.scalars(
                select(Template).where(Template.owner_user_id == u.id, Template.name == "General")
            ).one()
            for u in (ada, pat, quinn)
        }
        # Weekly events, week 0 four weeks back .. week 6 two weeks ahead, at the hour that was two
        # hours ago (so this week's occurrence is always already past and the next is a week away).
        day0 = (now - timedelta(hours=2) - timedelta(weeks=4)).replace(
            minute=0, second=0, microsecond=0
        )
        events: dict[tuple[int, str, int], CalendarEvent] = {}

        def calendar_of(user: User) -> CalendarConnection:
            conn = CalendarConnection(
                user_id=user.id,
                granted_scopes="https://www.googleapis.com/auth/calendar.events.readonly",
            )
            session.add(conn)
            session.commit()
            return conn

        ada_cal, pat_cal = calendar_of(ada), calendar_of(pat)

        def event(user, conn, rid, title, weeks_from_day0) -> CalendarEvent:
            if (user.id, rid, weeks_from_day0) in events:
                return events[(user.id, rid, weeks_from_day0)]
            starts = day0 + timedelta(weeks=weeks_from_day0)
            row = CalendarEvent(
                user_id=user.id,
                connection_id=conn.id,
                source_event_id=f"{rid}-{weeks_from_day0}",
                recurring_event_id=rid,
                title=title,
                starts_at=starts,
                ends_at=starts + timedelta(minutes=30),
                platform="google_meet",
                meeting_url=MEET,
                native_id="abc-defg-hij",
                status="confirmed",
            )
            session.add(row)
            session.commit()
            events[(user.id, rid, weeks_from_day0)] = row
            return row

        def meeting(user, conn, rid, title, weeks, label, **fields) -> Meeting:
            ev = event(user, conn, rid, title, weeks)
            row = Meeting(
                owner_user_id=user.id,
                url=MEET,
                platform="google_meet",
                native_id="abc-defg-hij",
                template_id=general[user.id].id,
                status="ready",
                step="done",
                source="calendar",
                notes=NOTES,
                transcript=talk(user),
                speaker_transcript=talk(user),
                recorded_seconds=1500,
                title=title,
                created_at=ev.starts_at,
                **fields,
            )
            session.add(row)
            session.commit()
            session.add(
                ScheduledJoin(
                    user_id=user.id,
                    calendar_event_id=ev.id,
                    platform="google_meet",
                    native_id="abc-defg-hij",
                    meeting_url=MEET,
                    starts_at=ev.starts_at,
                    ends_at=ev.ends_at,
                    run_at=ev.starts_at,
                    deadline_at=ev.ends_at,
                    state="completed",
                    meeting_id=row.id,
                )
            )
            session.commit()
            series.link_from_calendar(session, row)  # flag OFF: the recorder's default team
            session.commit()
            personal = teams.personal_team(session, user.id).id
            home = fields.get("shared_team_id") or personal
            question = f"Who owns {label}?"
            session.add(
                OpenQuestion(
                    meeting_id=row.id, text=question, position=0, fingerprint=fingerprint(question)
                )
            )
            session.add(
                Task(
                    team_id=home,
                    meeting_id=row.id,
                    title=f"Follow up on {label}",
                    source="ai",
                    ai_fingerprint=f"cap-{label}",
                    status="todo",
                    assigned_on=ev.starts_at.date(),
                )
            )
            session.commit()
            return row

        # Ada's Standup: A1 two weeks ago shared to X, A2 last week private.
        a1 = meeting(ada, ada_cal, "rid-standup", "Standup", 2, "A1 shared", shared_team_id=x.id)
        a2 = meeting(ada, ada_cal, "rid-standup", "Standup", 3, "A2 private")
        for weeks in range(7):  # the rest of the weekly calendar, the future ones are "next"
            event(ada, ada_cal, "rid-standup", "Standup", weeks)
        # Pat's Design sync: P3 private (three weeks ago), P1 shared to X, P2 shared to Y.
        p3 = meeting(pat, pat_cal, "rid-sync", "Design sync", 1, "P3 private")
        p1 = meeting(
            pat, pat_cal, "rid-sync", "Design sync", 2, "P1 shared to X", shared_team_id=x.id
        )
        p2 = meeting(
            pat, pat_cal, "rid-sync", "Design sync", 3, "P2 shared to Y", shared_team_id=y.id
        )
        for weeks in range(7):
            event(pat, pat_cal, "rid-sync", "Design sync", weeks)
        rows = {"a1": a1, "a2": a2, "p1": p1, "p2": p2, "p3": p3}
        return {
            "ids": {k: v.id for k, v in rows.items()},
            "x": x.id,
            "y": y.id,
            "ada_home": teams.personal_team(session, ada.id).id,
            "pat_home": teams.personal_team(session, pat.id).id,
            "quinn_home": teams.personal_team(session, quinn.id).id,
        }


def login(base: str, who: str, email: str) -> Recorder:
    handle = Recorder(base, who)
    handle.call(f"session-{who.lower()}", "GET", "/api/session")
    handle.call(
        f"login-{who.lower()}", "POST", "/api/session", body={"email": email, "password": PASSWORD}
    )
    return handle


def backfill(label: str, *args: str) -> str:
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        code = run_backfill(list(args))
    assert code == 0
    lines = out.getvalue().splitlines()
    note_file(
        f"backfill-{label}",
        "The real `scripts/backfill_series_follow.py` "
        + (f"with {' '.join(args)}" if args else "(a dry run)")
        + ", exactly as it printed.",
        {"args": list(args), "output": lines},
    )
    return out.getvalue()


def flag_on() -> None:
    os.environ["SERIES_FOLLOW_MEETINGS"] = "true"
    get_settings.cache_clear()
    assert series.follow_enabled()


def run(base: str, seeded: dict, engine) -> None:
    ids, x, y = seeded["ids"], seeded["x"], seeded["y"]
    home = {"ada": seeded["ada_home"], "pat": seeded["pat_home"], "quinn": seeded["quinn_home"]}
    ada, pat, quinn = login(base, "Ada", ADA), login(base, "Pat", PAT), login(base, "Quinn", QUINN)

    def row_of(rid: str, team: int) -> int:
        with Session(engine) as session:
            return session.scalars(
                select(Series.id).where(Series.calendar_recurring_id == rid, Series.team_id == team)
            ).one()

    # ---- 1. flag OFF, legacy rows: the state production is in today ----
    assert not series.follow_enabled()
    with Session(engine) as session:
        legacy = [
            (r.id, r.team_id, r.calendar_recurring_id)
            for r in session.scalars(select(Series).order_by(Series.id))
        ]
    note_file(
        "legacy-rows",
        "The recurring rows as the calendar link leaves them with the flag OFF: ONE row per person "
        "and event, in the recorder's DEFAULT team (Ada: team X; Pat, who is in two teams: his "
        "personal team), each holding every meeting of that event (shared, private, other team).",
        {"flag": "off", "rows": [dict(id=i, team_id=t, recurring=r) for i, t, r in legacy]},
    )
    ada_x_row = row_of("rid-standup", x)
    ada.call(
        "off-cards-ada",
        "GET",
        "/api/series",
        team=x,
        note="FLAG OFF. Ada's team X list: one Standup card whose counts include her PRIVATE meeting (A2's task and question). `team` is already on the card (additive, always sent).",
    )
    ada.call(
        "off-page-ada",
        "GET",
        f"/api/series/{ada_x_row}",
        note="FLAG OFF. The Standup page lists both A1 (shared) and A2 (private): the row mixes scopes.",
    )

    # ---- 2. the backfill, flag still OFF ----
    backfill("1-dry-run")
    backfill("2-apply", "--apply")
    backfill("3-dry-run-again")

    # ---- 3. flag ON ----
    flag_on()
    ada_x = row_of("rid-standup", x)
    with Session(engine) as session:
        rows_now = [
            (r.id, r.team_id, r.calendar_recurring_id)
            for r in session.scalars(select(Series).order_by(Series.id))
        ]
    note_file(
        "rows-after-backfill",
        "The rows after the backfill: one row per team scope. Ada's Standup has a row in X and one in her personal team; Pat's Design sync has one in X, one in Y and one in his personal team. Same recurring id, separate rows.",
        {"flag": "on", "rows": [dict(id=i, team_id=t, recurring=r) for i, t, r in rows_now]},
    )
    ada_p = row_of("rid-standup", home["ada"])
    pat_x = row_of("rid-sync", x)
    pat_y = row_of("rid-sync", y)
    pat_p = row_of("rid-sync", home["pat"])

    def names(response) -> list[str]:
        return [c["title"] for c in response.json()["series"]]

    # cards
    ada.call(
        "cards-ada-x",
        "GET",
        "/api/series",
        team=x,
        note="FLAG ON. Ada, team X: Standup with A1 only (one unfinished task, one open question); the private A2 is not counted. `team` names X, `is_personal` false; the next meeting is shown.",
    )
    ada.call(
        "cards-ada-personal",
        "GET",
        "/api/series",
        team=home["ada"],
        note="Ada's personal space: the Standup row of her PRIVATE meeting (A2). `team.is_personal` is true and the next meeting is null (hidden on personal rows).",
    )
    ada.call(
        "cards-ada-no-header",
        "GET",
        "/api/series",
        note="No X-Team-Id: Ada's default team, X (she has one ordinary team), so the same list as the X list.",
    )
    pat.call(
        "cards-pat-x", "GET", "/api/series", team=x, note="Pat in team X: Design sync with P1 only."
    )
    pat.call(
        "cards-pat-y", "GET", "/api/series", team=y, note="Pat in team Y: Design sync with P2 only."
    )
    pat.call(
        "cards-pat-personal",
        "GET",
        "/api/series",
        team=home["pat"],
        note="Pat's personal space: Design sync with P3 only (private). Not Ada's Standup.",
    )
    pat.call(
        "cards-pat-no-header",
        "GET",
        "/api/series",
        note="No header: Pat's default team is his personal space (two ordinary teams), so the personal list.",
    )
    empty = quinn.call(
        "cards-quinn-personal-empty",
        "GET",
        "/api/series",
        team=home["quinn"],
        note="EMPTY STATE: Quinn's personal space has no recurring meeting. 200 with an empty list, not an error.",
    )
    assert empty.json()["series"] == []
    quinn.call(
        "cards-quinn-y",
        "GET",
        "/api/series",
        team=y,
        note="Quinn, team Y: Pat's Design sync (P2 is shared to Y, so Quinn may read it). Not Pat's X or personal rows.",
    )
    # a list for a team the caller is not in
    pat.call(
        "cards-not-my-team",
        "GET",
        "/api/series",
        team=y + 100,
        note="A team Pat is not in, or that does not exist: 404 `No such team.`",
    )

    # pages
    ada.call(
        "page-ada-x",
        "GET",
        f"/api/series/{ada_x}",
        note="Ada, X's Standup page: past meetings, tasks, questions and attendees of A1 only. `team` is X.",
    )
    personal_page = ada.call(
        "page-ada-personal",
        "GET",
        f"/api/series/{ada_p}",
        note="Ada's PERSONAL Standup page: A2 only. `team.is_personal` true; `next_starts_at`, `next_text` and `sprint_note` are null; `topics` is [] (no Keeps coming up on a personal row); `agenda_recipients` is 0 (nobody to send to).",
    )
    assert personal_page.json()["next_text"] is None
    pat.call("page-pat-x", "GET", f"/api/series/{pat_x}", note="Pat, X's Design sync: P1 only.")
    pat.call("page-pat-y", "GET", f"/api/series/{pat_y}", note="Pat, Y's Design sync: P2 only.")
    pat.call(
        "page-pat-personal",
        "GET",
        f"/api/series/{pat_p}",
        note="Pat's personal Design sync: P3 only.",
    )
    quinn.call(
        "page-quinn-y",
        "GET",
        f"/api/series/{pat_y}",
        note="Quinn reads Y's Design sync (P2, shared to Y).",
    )
    quinn.call(
        "page-quinn-x-404",
        "GET",
        f"/api/series/{pat_x}",
        note="Quinn is not in X: 404 for X's row, the same answer as for a row that does not exist.",
    )
    quinn.call(
        "page-quinn-personal-404",
        "GET",
        f"/api/series/{pat_p}",
        note="Pat's personal row to Quinn: 404.",
    )
    pat.call(
        "page-pat-ada-standup-404",
        "GET",
        f"/api/series/{ada_p}",
        note="Ada's personal row to Pat: 404 (Pat is in X, but a personal row belongs to its owner only).",
    )
    ada.call(
        "page-not-a-number", "GET", "/api/series/999999", note="A row that does not exist: 404."
    )

    # the personal row: agenda and send
    ada.call(
        "personal-agenda-add",
        "POST",
        f"/api/series/{ada_p}/agenda",
        body={"text": "Prepare my own notes"},
        note="A line typed on the personal page.",
    )
    ada.call(
        "personal-send-agenda",
        "POST",
        f"/api/series/{ada_p}/agenda/send",
        note="Send agenda on a personal row: nobody else is on it. 409 `no_attendees`. The frontend hides the button here (`team.is_personal`).",
    )
    ada.call(
        "x-page-after-personal-line",
        "GET",
        f"/api/series/{ada_x}",
        note="X's page after the personal line was typed: `agenda` does not contain it (a line stays on the page it was typed on).",
    )

    # ---- follow: a share and an unshare move the meeting between rows ----
    ada.call(
        "share-a2",
        "POST",
        f"/api/meetings/{ids['a2']}/share",
        body={"team_id": x},
        note="Ada shares A2 (her private Standup) to X. The meeting leaves her personal row and joins X's row.",
    )
    ada.call(
        "cards-ada-x-after-share",
        "GET",
        "/api/series",
        team=x,
        note="X's card now counts A1 and A2 (two unfinished tasks, two questions).",
    )
    ada.call(
        "page-ada-x-after-share",
        "GET",
        f"/api/series/{ada_x}",
        note="X's page lists both meetings.",
    )
    ada.call(
        "cards-ada-personal-after-share",
        "GET",
        "/api/series",
        team=home["ada"],
        note="Ada's personal list after sharing her only private Standup: the row has no meeting left. By the design note's rule an EMPTY card stays for its creator (the ghost empty card, a decision for Adebayo): shown here as the server really answers.",
    )
    ada.call(
        "page-ada-personal-after-share",
        "GET",
        f"/api/series/{ada_p}",
        note="EMPTY STATE: the personal page of that emptied row: no past meetings, no tasks, no questions, no attendees, `next_*` null. Everything is a plain empty list, nothing is missing or null apart from the three `next_*` fields. The one agenda line typed earlier is still there (lines stay on their row).",
    )
    ada.call(
        "unshare-a2",
        "DELETE",
        f"/api/meetings/{ids['a2']}/share",
        note="Ada unshares A2: it follows back to her personal row.",
    )
    ada.call(
        "cards-ada-x-after-unshare", "GET", "/api/series", team=x, note="X is back to A1 only."
    )
    ada.call(
        "cards-ada-personal-after-unshare",
        "GET",
        "/api/series",
        team=home["ada"],
        note="The personal card has its meeting again.",
    )

    # ---- 4. a straggler: the readers hide it, the flag-on backfill moves it, the next dry run says 0 ----
    with Session(engine) as session:
        session.execute(
            text("update meetings set series_id = :row where id = :id"),
            {"row": ada_x, "id": ids["a2"]},
        )
        session.commit()
    ada.call(
        "straggler-page-x",
        "GET",
        f"/api/series/{ada_x}",
        note="A STRAGGLER: A2 (private) put back into X's row by hand, as an old writer could. X's page still lists A1 only: every reader checks the scope, so a mixed row never shows another scope's meeting.",
    )
    backfill("4-flag-on-apply-moves-the-straggler", "--apply")
    backfill("5-flag-on-dry-run-says-zero")
    ada.call(
        "straggler-fixed-personal",
        "GET",
        f"/api/series/{ada_p}",
        note="After the backfill A2 is back in the personal row.",
    )

    # ---- control ----
    ada.call(
        "control-x-list-again",
        "GET",
        "/api/series",
        team=x,
        note="Control: X's list again, unchanged by the straggler round trip.",
    )


def main() -> None:
    if OUT.exists():
        shutil.rmtree(OUT)
    OUT.mkdir(parents=True)
    prepare_database()
    settings = Settings()
    assert settings.multi_team_enabled and not settings.series_follow_meetings
    engine = create_engine(scratch_url)
    seeded = seed(engine)
    app = create_app()
    server, base = start_server(app)
    try:
        run(base, seeded, engine)
    finally:
        server.should_exit = True
        engine.dispose()
    print(f"\n{Recorder.count} files in {OUT}")


if __name__ == "__main__":
    main()
