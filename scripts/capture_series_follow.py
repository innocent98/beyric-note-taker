"""Drive "recurring meetings follow the meetings" over real HTTP and save every exchange for the
frontend guide and the runbook.

    python scripts/capture_series_follow.py postgresql+psycopg://USER:PASS@HOST:PORT/postgres
    python scripts/capture_series_follow.py postgresql+psycopg://... --keep 51   # write only 52 and up

`--keep N` runs the whole story again (the later steps need its state) but leaves the first N captured
files exactly as committed: the way part 5 below (remove a recurring meeting, captures 52 and up) was
added without rewriting the 51 files the guide's sections 1 to 9 were checked against.

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
5. REMOVE A RECURRING MEETING (`DELETE /api/series/{id}`, lead-designed, 2026-11-15): who may and who
   may not, one row only, the meetings stay, the calendar sync and a later share do not bring the row
   back, an emptied "ghost" card, a hand-made row.

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
KEEP = int(sys.argv[sys.argv.index("--keep") + 1]) if "--keep" in sys.argv else 0
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
from scripts.remove_empty_team_cards import run as run_cleanup  # noqa: E402


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
        if Recorder.count > KEEP:
            file.write_text(json.dumps(record, indent=2, ensure_ascii=False) + "\n")
        print(f"{file.name:58} {self.who:6} {method:6} {path:44} -> {response.status_code}")
        return response


def note_file(name: str, note: str, content) -> None:
    Recorder.count += 1
    record = {"note": note, "content": content}
    if Recorder.count > KEEP:
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
        # Ada's 1:1: ALL of its meetings are private. Legacy: one row in team X holding it; after the
        # backfill its X row is empty (a card with no meeting) and the meeting is under her personal space.
        a3 = meeting(ada, ada_cal, "rid-1on1", "1:1", 3, "A3 private 1:1")
        for weeks in range(7):
            event(ada, ada_cal, "rid-1on1", "1:1", weeks)
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
        rows = {"a1": a1, "a2": a2, "a3": a3, "p1": p1, "p2": p2, "p3": p3}
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
    # ---- an all-private row left empty in the team ----
    empty_x = row_of("rid-1on1", x)
    ada.call(
        "page-ada-x-emptied-row",
        "GET",
        f"/api/series/{empty_x}",
        note="AN ALL-PRIVATE ROW LEFT EMPTY IN THE TEAM: Ada's 1:1 had only private meetings, so the backfill moved them to her personal row and X's 1:1 row has no meeting. It stays on X's list for her (its creator) as an empty card (capture 14 lists it with 0 and 0); opened, every list is empty.",
    )
    pat.call(
        "page-pat-x-emptied-row-404",
        "GET",
        f"/api/series/{empty_x}",
        note="Pat is in X but the empty card is Ada's alone (she made it): 404 for him, and it is not on his list (capture 17).",
    )

    # ---- 5. remove a recurring meeting (LEAD-DESIGNED, 2026-11-15) ----
    assert Recorder.count == 51, Recorder.count  # the story above is the guide's sections 1 to 9
    remove_story(
        base,
        seeded,
        engine,
        ada,
        pat,
        quinn,
        ids,
        x,
        y,
        home,
        ada_x,
        ada_p,
        empty_x,
        pat_x,
        pat_y,
        pat_p,
        row_of,
    )


def remove_story(
    base,
    seeded,
    engine,
    ada,
    pat,
    quinn,
    ids,
    x,
    y,
    home,
    ada_x,
    ada_p,
    empty_x,
    pat_x,
    pat_y,
    pat_p,
    row_of,
) -> None:
    def titles(response) -> list[str]:
        return [c["title"] for c in response.json()["series"]]

    ada.call(
        "remove-page-ada-x-before",
        "GET",
        f"/api/series/{ada_x}",
        note="BEFORE ANY REMOVAL. Ada owns team X: her page says `can_remove: true` (new field on every series page).",
    )
    pat_view = pat.call(
        "remove-page-pat-x-cannot",
        "GET",
        f"/api/series/{ada_x}",
        note="Pat is a plain member of X and did not make this row (Ada did): he can read it, and the page says `can_remove: false`. Do not draw the button.",
    )
    assert pat_view.json()["can_remove"] is False
    forbidden = pat.call(
        "remove-pat-x-403",
        "DELETE",
        f"/api/series/{ada_x}",
        note="A plain member who did not make the row: 403 `team_forbidden`, the project's usual team error. Nothing changed.",
    )
    assert forbidden.status_code == 403
    outsider = quinn.call(
        "remove-quinn-x-404",
        "DELETE",
        f"/api/series/{ada_x}",
        note="Quinn is not in X: 404, the same answer as for a row that does not exist (and as a read of it, capture 30). Nothing changed.",
    )
    assert outsider.status_code == 404
    not_owner = pat.call(
        "remove-pat-ada-personal-404",
        "DELETE",
        f"/api/series/{ada_p}",
        note="Ada's PERSONAL Standup, asked by Pat (who is in X): 404. A personal row belongs to its owner alone.",
    )
    assert not_owner.status_code == 404

    # The emptied "ghost" card (Ada's 1:1: all private, so the team's row has no meeting left).
    ghost = ada.call(
        "remove-ada-ghost-1on1",
        "DELETE",
        f"/api/series/{empty_x}",
        note="THE GHOST CARD: X's 1:1 row, empty since the backfill. Ada made it and owns X: 200. `meetings_kept` 0, nothing waiting. The calendar event is remembered as removed for X.",
    )
    assert ghost.status_code == 200 and ghost.json()["remembered"] is True
    ada.call(
        "remove-cards-ada-x-no-ghost",
        "GET",
        "/api/series",
        team=x,
        note="X's list after: the 1:1 card is gone; Standup and Pat's Design sync are still there.",
    )
    ada.call(
        "remove-cards-ada-personal-1on1-kept",
        "GET",
        "/api/series",
        team=home["ada"],
        note="Ada's PERSONAL list: her personal 1:1 row (the real private meeting) is untouched by removing X's empty 1:1. One removal acts on one row.",
    )

    # Pat removes his own personal Design sync; X's and Y's rows of the same event stay.
    mine = pat.call(
        "remove-pat-personal-design-sync",
        "DELETE",
        f"/api/series/{pat_p}",
        note="Pat removes his PERSONAL Design sync (he is the owner). 200. Only the personal scope is remembered.",
    )
    assert mine.status_code == 200
    pat.call(
        "remove-cards-pat-personal-after",
        "GET",
        "/api/series",
        team=home["pat"],
        note="Pat's personal list: empty.",
    )
    pat.call(
        "remove-cards-pat-x-still",
        "GET",
        "/api/series",
        team=x,
        note="Pat's X list: his X Design sync is still there (same recurring event, another row).",
    )
    pat.call(
        "remove-cards-pat-y-still",
        "GET",
        "/api/series",
        team=y,
        note="Pat's Y list: his Y Design sync is still there.",
    )
    with Session(engine) as session:
        private_meeting = session.get(Meeting, ids["p3"])
        assert private_meeting.series_id is None

    # Ada (owner of X) removes the Standup of X: the main case.
    body = ada.call(
        "remove-ada-x-standup",
        "DELETE",
        f"/api/series/{ada_x}",
        note="THE MAIN CASE. Ada, owner of X, removes X's Standup. 200 with counts only: `meetings_kept` 1 (A1, which she may read), `agenda_lines_removed` 0, `remembered` true.",
    )
    assert body.status_code == 200
    again = ada.call(
        "remove-ada-x-standup-again",
        "DELETE",
        f"/api/series/{ada_x}",
        note="The same call again: 404 (the row is gone). The frontend treats this 404 as 'already removed'.",
    )
    assert again.status_code == 404
    ada.call(
        "remove-page-ada-x-gone",
        "GET",
        f"/api/series/{ada_x}",
        note="Reading the removed row: 404.",
    )
    a1 = ada.call(
        "remove-meeting-a1-still-there",
        "GET",
        f"/api/meetings/{ids['a1']}",
        note="A1, the meeting that was in the removed row, is still an ordinary meeting: 200, notes and tasks as before, `series` is null (not grouped any more).",
    )
    assert a1.status_code == 200 and a1.json()["series"] is None
    after_x = ada.call(
        "remove-cards-ada-x-after",
        "GET",
        "/api/series",
        team=x,
        note="X's list: no Standup card now. Pat's Design sync (Pat's X row) is still listed.",
    )
    assert "Standup" not in titles(after_x)
    ada.call(
        "remove-cards-ada-personal-standup-kept",
        "GET",
        "/api/series",
        team=home["ada"],
        note="Ada's PERSONAL list: her personal Standup row (A2, private) is untouched by removing X's Standup.",
    )

    # The calendar sync does not bring it back (the real hourly sync, run in this process).
    with Session(engine) as session:
        made = series.sync_from_joins(session, datetime.now(UTC))
        rows_after = [
            (r.id, r.team_id, r.calendar_recurring_id)
            for r in session.scalars(select(Series).order_by(Series.id))
        ]
    assert made == 0
    note_file(
        "remove-hourly-sync-makes-nothing",
        "NOT AN HTTP CALL. The real `series.sync_from_joins` (the hourly sweep that makes a card for each recurring event the scheduler will join), run in this process after the removals. It makes 0 rows: every recurring event on Ada's and Pat's calendars already has its card, or its personal card was removed (Pat's Design sync). `rows` is every recurring row left.",
        {"rows_made": made, "rows": [dict(id=i, team_id=t, recurring=r) for i, t, r in rows_after]},
    )

    # The owner's explicit share clears the removed scope's mark: the card is made properly.
    ada.call(
        "remove-share-a2-to-x",
        "POST",
        f"/api/meetings/{ids['a2']}/share",
        body={"team_id": x},
        note="Ada (owner of X) shares A2, her private Standup, to X, where Standup was removed. An owner's or admin's explicit share makes the team's card again and clears the 'removed' mark for that scope.",
    )
    a2 = ada.call(
        "remove-meeting-a2-after-share",
        "GET",
        f"/api/meetings/{ids['a2']}",
        note="A2 is now in X's Standup again (a NEW row, a new id): the share by an owner made the card. It left her personal Standup row.",
    )
    assert a2.json()["series"] is not None
    after_share = ada.call(
        "remove-cards-ada-x-after-share",
        "GET",
        "/api/series",
        team=x,
        note="X's list: Standup is back, with A2 only.",
    )
    assert "Standup" in titles(after_share)

    # Her personal Standup now has no meeting left: remove the emptied card, with its waiting line.
    last = ada.call(
        "remove-ada-personal-standup-emptied",
        "DELETE",
        f"/api/series/{ada_p}",
        note="Ada removes her now-empty personal Standup card. 200: `meetings_kept` 0, `agenda_lines_removed` 1 (the line she typed earlier waited there and goes with the row).",
    )
    assert last.status_code == 200 and last.json()["agenda_lines_removed"] == 1

    # A hand-made recurring meeting: nothing to remember.
    made_by_hand = ada.call(
        "remove-hand-made-create",
        "POST",
        "/api/series",
        body={"title": "Founders sync"},
        team=x,
        note="Ada makes a recurring meeting by hand in X (no calendar event behind it).",
    )
    hand = made_by_hand.json()["id"]
    hand_removed = ada.call(
        "remove-hand-made-delete",
        "DELETE",
        f"/api/series/{hand}",
        note="Removing a hand-made one: 200, `remembered` false (there is no calendar event to forget).",
    )
    assert hand_removed.json()["remembered"] is False

    # "Only owners and admins" (Adebayo, 2026-11-15): the person who made a team row has no extra power.
    own = pat.call(
        "remove-page-pat-own-x-row",
        "GET",
        f"/api/series/{pat_x}",
        note="Pat made X's Design sync (his calendar synced first) but is a plain member of X: the page says `can_remove: false`. The person who made a team row gets no extra power.",
    )
    assert own.json()["can_remove"] is False
    own_403 = pat.call(
        "remove-pat-own-x-row-403",
        "DELETE",
        f"/api/series/{pat_x}",
        note="Pat tries to remove the calendar row he made: 403 `team_forbidden`. Nothing changed.",
    )
    assert own_403.status_code == 403
    # Since the simpler model a plain member cannot make a team card by hand (captures 89 and up). A row
    # like this one can only be a LEGACY row, made before that change: it is inserted directly.
    with Session(engine) as session:
        legacy_row = Series(
            team_id=x,
            owner_user_id=session.scalars(select(User.id).where(User.email == PAT)).one(),
            title="Pat's own recurring",
        )
        session.add(legacy_row)
        session.commit()
        legacy_id = legacy_row.id
    note_file(
        "remove-legacy-empty-hand-made-row",
        "NOT AN HTTP CALL. A completely empty hand-made recurring meeting in X, made by Pat (a plain member) BEFORE only owners and admins could make a team's card: inserted directly, as the rows made then are. Pat sees it (an empty card shows to its creator) and may remove it while it is empty.",
        {"id": legacy_id, "team_id": x, "made_by": "a plain member, before the change"},
    )
    hand_id = legacy_id
    empty_page = pat.call(
        "remove-page-pat-empty-hand-made",
        "GET",
        f"/api/series/{hand_id}",
        note='"Yes, empty rows only" (Adebayo, 2026-11-15): the person who made a row may remove it while it is COMPLETELY EMPTY (no meeting, no waiting agenda line, no project, no mail still to go). Pat made this one and nothing is in it: `can_remove` is true.',
    )
    assert empty_page.json()["can_remove"] is True
    line = pat.call(
        "remove-pat-hand-made-add-line",
        "POST",
        f"/api/series/{hand_id}/agenda",
        body={"text": "Plan the offsite"},
        note="Pat adds one agenda line to his hand-made row. The row is no longer empty.",
    )
    with_line = pat.call(
        "remove-page-pat-hand-made-with-line",
        "GET",
        f"/api/series/{hand_id}",
        note="With a waiting line in it the same page says `can_remove: false` for Pat. An owner or admin of X may still remove it.",
    )
    assert with_line.json()["can_remove"] is False
    refused = pat.call(
        "remove-pat-hand-made-with-line-403",
        "DELETE",
        f"/api/series/{hand_id}",
        note="Pat tries to remove it now: 403 `team_forbidden`. Nothing changed.",
    )
    assert refused.status_code == 403
    pat.call(
        "remove-pat-hand-made-remove-line",
        "DELETE",
        f"/api/series/{hand_id}/agenda/{line.json()['id']}",
        note="Pat removes his own line (its author may). The row is empty again.",
    )
    again_empty = pat.call(
        "remove-page-pat-hand-made-empty-again",
        "GET",
        f"/api/series/{hand_id}",
        note="`can_remove` is true again: the rule looks at what is in the row now.",
    )
    assert again_empty.json()["can_remove"] is True
    gone = pat.call(
        "remove-pat-hand-made-delete",
        "DELETE",
        f"/api/series/{hand_id}",
        note="Pat removes his empty hand-made row: 200, nothing to remember (no calendar event).",
    )
    assert gone.status_code == 200 and gone.json()["remembered"] is False

    # A connected calendar still makes its recurring rows: the removal feature changes nothing here.
    with Session(engine) as session:
        ada_user = session.scalars(select(User).where(User.email == ADA)).one()
        connection = session.scalars(
            select(CalendarConnection).where(CalendarConnection.user_id == ada_user.id)
        ).first()
        starts = datetime.now(UTC).replace(minute=0, second=0, microsecond=0) + timedelta(days=2)
        event = CalendarEvent(
            user_id=ada_user.id,
            connection_id=connection.id,
            source_event_id="rid-connected-0",
            recurring_event_id="rid-connected",
            title="Weekly planning",
            starts_at=starts,
            ends_at=starts + timedelta(minutes=30),
            platform="google_meet",
            meeting_url=MEET,
            native_id="abc-defg-hij",
            status="confirmed",
        )
        session.add(event)
        session.commit()
        session.add(
            ScheduledJoin(
                user_id=ada_user.id,
                calendar_event_id=event.id,
                platform="google_meet",
                native_id="abc-defg-hij",
                meeting_url=MEET,
                starts_at=event.starts_at,
                ends_at=event.ends_at,
                run_at=event.starts_at - timedelta(minutes=2),
                deadline_at=event.ends_at,
                state="scheduled",
            )
        )
        session.commit()
        made_now = series.sync_from_joins(session, datetime.now(UTC))
    assert made_now == 1
    note_file(
        "remove-calendar-recurring-still-makes-a-card",
        "NOT AN HTTP CALL. A recurring event on Ada's connected calendar that the scheduler will join (a join is scheduled) and that was never removed: the real hourly `series.sync_from_joins` makes its card, in Ada's PERSONAL space. It makes 1. The removal feature changes nothing here: it only skips an event that someone REMOVED for that scope (capture 70 shows 0 rows made for the removed ones).",
        {"rows_made": made_now, "recurring_event_id": "rid-connected"},
    )
    shown = ada.call(
        "remove-calendar-recurring-card-listed",
        "GET",
        "/api/series",
        team=home["ada"],
        note="Ada's PERSONAL list now has the new empty card for the event: `Weekly planning`. A card from a person's own calendar is always in their Personal space (it is not in team X). It appears once a join is scheduled for the event (within the hour of the calendar sync), or when its first recording has notes.",
    )
    assert "Weekly planning" in [c["title"] for c in shown.json()["series"]]
    in_x = ada.call(
        "remove-calendar-recurring-card-not-in-team",
        "GET",
        "/api/series",
        team=x,
        note="Ada's team X list: no `Weekly planning`. The placeholder is not made in a team, even though X is Ada's default team.",
    )
    assert "Weekly planning" not in [c["title"] for c in in_x.json()["series"]]

    model_story(base, engine, ada, pat, x, home)


def model_story(base, engine, ada, pat, x, home) -> None:
    """Part 6, the simpler model (Adebayo, 2026-11-15/16): only an owner or admin makes a team's
    recurring card; a plain member's share makes none but joins one that exists; the cleanup of the
    empty team cards the old sync made."""

    def record(email, rid, title, days_ago, **fields) -> int:
        """A recorded meeting of a recurring calendar event, linked by the real pipeline link."""
        with Session(engine) as session:
            user = session.scalars(select(User).where(User.email == email)).one()
            connection = session.scalars(
                select(CalendarConnection).where(CalendarConnection.user_id == user.id)
            ).first()
            template = session.scalars(
                select(Template).where(
                    Template.owner_user_id == user.id, Template.name == "General"
                )
            ).one()
            starts = (datetime.now(UTC) - timedelta(days=days_ago)).replace(
                minute=0, second=0, microsecond=0
            )
            event = CalendarEvent(
                user_id=user.id,
                connection_id=connection.id,
                source_event_id=f"{rid}-{days_ago}",
                recurring_event_id=rid,
                title=title,
                starts_at=starts,
                ends_at=starts + timedelta(minutes=30),
                platform="google_meet",
                meeting_url=MEET,
                native_id="abc-defg-hij",
                status="confirmed",
            )
            session.add(event)
            session.commit()
            row = Meeting(
                owner_user_id=user.id,
                url=MEET,
                platform="google_meet",
                native_id="abc-defg-hij",
                template_id=template.id,
                status="ready",
                step="done",
                source="calendar",
                notes=NOTES,
                transcript=talk(user),
                speaker_transcript=talk(user),
                recorded_seconds=1500,
                title=title,
                created_at=starts,
                **fields,
            )
            session.add(row)
            session.commit()
            session.add(
                ScheduledJoin(
                    user_id=user.id,
                    calendar_event_id=event.id,
                    platform="google_meet",
                    native_id="abc-defg-hij",
                    meeting_url=MEET,
                    starts_at=starts,
                    ends_at=event.ends_at,
                    run_at=starts,
                    deadline_at=event.ends_at,
                    state="completed",
                    meeting_id=row.id,
                )
            )
            session.commit()
            series.link_from_calendar(session, row)
            session.commit()
            return row.id

    def titles_of(response) -> list[str]:
        return [c["title"] for c in response.json()["series"]]

    pats = record(PAT, "rid-review", "Design review", 9)
    mine_before = pat.call(
        "model-pat-personal-list",
        "GET",
        "/api/series",
        team=home["pat"],
        note="Pat recorded a private meeting of a recurring event ('Design review'). The real link put it in a card in his PERSONAL space.",
    )
    assert "Design review" in titles_of(mine_before)
    pat.call(
        "model-pat-shares-no-card",
        "POST",
        f"/api/meetings/{pats}/share",
        body={"team_id": x},
        note="Pat, a plain member of X, shares that meeting to X. The share works as always.",
    )
    shared = pat.call(
        "model-pat-shared-meeting",
        "GET",
        f"/api/meetings/{pats}",
        note="The meeting is shared to X and in NO recurring meeting: `series` is null. A plain member's share makes no team card (and the meeting left his personal card, which holds only private meetings).",
    )
    assert shared.json()["series"] is None and shared.json()["shared_with_team"] is True
    no_card = pat.call(
        "model-x-list-no-card",
        "GET",
        "/api/series",
        team=x,
        note="X's list: no 'Design review' card was made by Pat's share.",
    )
    assert "Design review" not in titles_of(no_card)

    owners = record(ADA, "rid-review", "Design review", 2)
    ada.call(
        "model-ada-shares-makes-card",
        "POST",
        f"/api/meetings/{owners}/share",
        body={"team_id": x},
        note="Ada (owner of X) records the same recurring event and shares her meeting to X. An owner's or admin's share makes the team's card for the event.",
    )
    card_list = ada.call(
        "model-x-list-card-made",
        "GET",
        "/api/series",
        team=x,
        note="X's list now has 'Design review', made by Ada's share.",
    )
    assert "Design review" in titles_of(card_list)
    card_id = next(c["id"] for c in card_list.json()["series"] if c["title"] == "Design review")
    page = ada.call(
        "model-x-card-page",
        "GET",
        f"/api/series/{card_id}",
        note="The new card's page lists BOTH meetings: Ada's, and Pat's earlier one, which was already shared to X and was waiting without a card. A new team card takes in the meetings of the same event that the team already has.",
    )
    assert len(page.json()["past_meetings"]) == 2
    pat.call(
        "model-pat-creates-team-card-403",
        "POST",
        "/api/series",
        body={"title": "Pat's weekly"},
        team=x,
        note="Pat, a plain member, tries to make a recurring meeting by hand in X: 403 `team_forbidden`, the project's usual error.",
    )
    made_personal = pat.call(
        "model-pat-creates-personal-card",
        "POST",
        "/api/series",
        body={"title": "Pat's weekly"},
        team=home["pat"],
        note="In his own Personal space he may make one (it is his alone): 201.",
    )
    assert made_personal.status_code == 201
    other = record(PAT, "rid-retro", "Retro", 3, shared_team_id=x)
    pat.call(
        "model-pat-attach-403",
        "PUT",
        f"/api/meetings/{other}/series",
        body={"series_id": card_id},
        note="Pat tries to add his shared meeting 'Retro' to X's recurring meeting by hand: 403 `team_forbidden`. Putting a meeting into a team's recurring meeting is an owner's or admin's to do.",
    )
    flags = pat.call(
        "model-pat-meeting-page-flags",
        "GET",
        f"/api/meetings/{other}",
        note="The meeting page for that shared meeting says `can_make_recurring: false` and `suggest_recurring: false`: draw neither 'Add to recurring meeting' nor 'Make this a recurring meeting?'.",
    )
    assert flags.json()["can_make_recurring"] is False
    owner_flags = ada.call(
        "model-ada-meeting-page-flags",
        "GET",
        f"/api/meetings/{owners}",
        note="For Ada, the owner of X, the same kind of page says `can_make_recurring: true`.",
    )
    assert owner_flags.json()["can_make_recurring"] is True

    # The cleanup of the empty cards the old sync made in a team.
    with Session(engine) as session:
        ada_id = session.scalars(select(User.id).where(User.email == ADA)).one()
        for n in (1, 2):
            session.add(
                Series(
                    team_id=x,
                    owner_user_id=ada_id,
                    title=f"Old placeholder {n}",
                    calendar_recurring_id=f"rid-legacy-{n}",
                )
            )
        session.commit()
    before = ada.call(
        "model-cleanup-x-list-before",
        "GET",
        "/api/series",
        team=x,
        note="X's list before the cleanup: two empty placeholders the old sync made in the team (0 and 0), next to the cards that hold meetings.",
    )
    assert "Old placeholder 1" in titles_of(before)

    def cleanup(label: str, *args: str) -> None:
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = run_cleanup(list(args))
        assert code == 0
        note_file(
            f"model-cleanup-{label}",
            "The real `scripts/remove_empty_team_cards.py` "
            + (f"with {' '.join(args)}" if args else "(a dry run)")
            + ", exactly as it printed.",
            {"args": list(args), "output": out.getvalue().splitlines()},
        )

    cleanup("1-dry-run")
    cleanup("2-apply", "--apply")
    cleanup("3-dry-run-again")
    after = ada.call(
        "model-cleanup-x-list-after",
        "GET",
        "/api/series",
        team=x,
        note="X's list after: the two empty placeholders are gone; the cards that hold meetings stay. No 'removed' mark was written, so the next calendar sync makes each person's PERSONAL card.",
    )
    assert "Old placeholder 1" not in titles_of(after) and "Design review" in titles_of(after)


def main() -> None:
    if KEEP == 0 and OUT.exists():
        shutil.rmtree(OUT)
    OUT.mkdir(parents=True, exist_ok=True)
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
