"""A local stack for walking the whole recipes feature, and the live pass over every starter.

    PYTHONPATH=. python scripts/recipes_live.py serve  ADMIN_URL [PORT]   # app + worker, for a browser
    PYTHONPATH=. python scripts/recipes_live.py pass   ADMIN_URL          # all 18 starters + the board
    PYTHONPATH=. python scripts/recipes_live.py load   ADMIN_URL          # export load, two sessions

`ADMIN_URL` is any Postgres server the script may create a scratch database on
(`postgresql+psycopg://USER:PASS@HOST:PORT/postgres`). It runs the real migrations, starts the real
app (uvicorn) and the real worker loop (`app.recipes.service.run_loop`) and seeds placeholder people
and three synthetic meetings (a sales call, a sprint planning, a lecture) plus short series for the
cross-meeting recipes. Nothing here is a real person or a real meeting.

WHAT IS REAL: the app, auth, CSRF, the eighteen real starter prompts (`app/recipes/library.py`), the
context built from the meetings, the queue and worker, the ledger (`ai_runs`), the number audit, the
exports (Word and PDF through the real render child), the permission rules.

WHAT IS NOT REAL: the model. No OpenAI key is configured on this machine, so `LiveModel` stands in
for the OpenAI client. It writes a plausible draft by copying real lines of the real prompt it was
sent (so sources, blanks and figures behave like a grounded draft), plus two deliberate seeds: a
`[need: ...]` blank where the meeting has no date, and ONE invented figure in the proposal so the
audit has something to catch. Its wording, latency and token counts say NOTHING about a real model;
the token counts in the pass come from the prompt it was sent (estimated at 4 characters a token) and
its own output, and are labelled estimates.
"""

# ruff: noqa: E501

import json
import os
import re
import sys
import threading
import time
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "docs" / "fe-captures" / "recipes" / "live-pass"
DB_NAME = "notetaker_live_recipes"
ADMIN_EMAIL = "admin@example.test"
ADMIN_PASSWORD = "-".join(["live", "admin", "placeholder"])
PEOPLE = {  # email -> (name, password); all placeholders
    "ada@example.test": ("Ada Lovelace", "-".join(["ada", "live", "password", "1"])),
    "tunde@example.test": ("Tunde Bello", "-".join(["tunde", "live", "password", "2"])),
    "eve@example.test": ("Eve Outsider", "-".join(["eve", "live", "password", "3"])),
}

mode = sys.argv[1] if len(sys.argv) > 1 else ""
admin_url = sys.argv[2] if len(sys.argv) > 2 else ""
serve_port = int(sys.argv[3]) if len(sys.argv) > 3 else 8800
scratch_url = urlparse(admin_url)._replace(path=f"/{DB_NAME}").geturl() if admin_url else ""
if scratch_url:
    os.environ["DATABASE_URL"] = scratch_url
os.environ["OWNER_EMAIL"] = ADMIN_EMAIL
os.environ["OWNER_PASSWORD"] = ADMIN_PASSWORD
os.environ.setdefault("APP_ENV", "development")
os.environ.setdefault("RECIPE_DAILY_LIMIT", "200")  # the pass makes ~25 drafts

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
    AiRun,
    AppState,
    Decision,
    Meeting,
    OpenQuestion,
    Series,
    Task,
    TeamBrief,
    TeamMember,
    Template,
    User,
)
from app.passwords import hash_password  # noqa: E402
from app.recipes import generate, library  # noqa: E402
from app.recipes import service as recipe_service  # noqa: E402

SALES = """[Ada Lovelace]: Thanks for making the time, Daniel. I wanted to go through what your team needs for onboarding.
[Daniel Okoro]: Of course. We run a logistics company with forty drivers and two dispatchers. Today everything is on WhatsApp and paper.
[Ada Lovelace]: Understood. What hurts most right now?
[Daniel Okoro]: Dispatch. Drivers get the wrong route, and customers call us to ask where their delivery is.
[Ada Lovelace]: So the first goal is route assignment and customer tracking.
[Daniel Okoro]: Yes. Second goal is the monthly report for our finance team, they currently build it by hand.
[Ada Lovelace]: We can set up both. The onboarding package is 50,000 naira and covers setup, training for two dispatchers and a fortnight of support.
[Daniel Okoro]: That works for us. Does the price include the driver app?
[Ada Lovelace]: The driver app is included for up to forty drivers. Beyond that it is 1,000 naira per driver per month.
[Daniel Okoro]: Fine. We have forty-two drivers at the moment, so I will need to check the extra two.
[Ada Lovelace]: Noted. When would you like to start?
[Daniel Okoro]: Our finance year closes at the end of the month, so after that. Let us say the first week of December.
[Ada Lovelace]: First week of December works. I will send the written plan by Friday.
[Daniel Okoro]: Good. I need my director, Mrs Adeyemi, to sign off before we pay anything.
[Ada Lovelace]: Of course. Will she join a short call?
[Daniel Okoro]: She travels next week. I will ask her assistant for a time and come back to you.
[Ada Lovelace]: Perfect. One more thing, do you need integration with your accounting software?
[Daniel Okoro]: Maybe later. For now no, but please note it as something we might want.
[Ada Lovelace]: I will note it as a possible phase two. Anything else you want me to cover in the plan?
[Daniel Okoro]: Data migration. We have about three years of delivery records in spreadsheets.
[Ada Lovelace]: We can import those. I will confirm how long that takes after I see a sample.
[Daniel Okoro]: I can send a sample file tomorrow.
[Ada Lovelace]: Great. To recap: onboarding at 50,000 naira, start in the first week of December, plan from me by Friday, sample file from you tomorrow, sign-off from Mrs Adeyemi still open.
[Daniel Okoro]: That is right. Thank you, Ada."""

SALES_NOTES = """## Summary
Discovery call with Daniel Okoro's logistics company (40 drivers, 2 dispatchers). Goals: route assignment with customer tracking, and a monthly finance report.

## Decisions
- Onboarding package at 50,000 naira, driver app included for up to 40 drivers.
- Start in the first week of December.

## Action items
- Ada: send the written plan by Friday.
- Daniel: send a sample of the delivery records tomorrow.
- Daniel: find a time for a call with Mrs Adeyemi.

## Open questions
- Who signs off on Daniel's side (Mrs Adeyemi) and when?
- Accounting integration: possible phase two."""

SPRINT = """[Ada Lovelace]: Let us plan sprint 14. Last sprint we closed eleven of fourteen tickets.
[Tunde Bello]: The three we missed were all the export work. The PDF renderer was slower than we thought.
[Ada Lovelace]: Right. The decision I want today is whether export stays in this sprint or moves.
[Tunde Bello]: I would keep it. It is the last blocker for the pilot customers.
[Ada Lovelace]: Agreed, export stays. Priority one for the sprint.
[Tunde Bello]: Then the second item is the search index. It must not slow down writes.
[Ada Lovelace]: What is the target?
[Tunde Bello]: Search under 300 milliseconds for ten thousand meetings.
[Ada Lovelace]: Okay. Who takes the index work?
[Tunde Bello]: I will take it. I need two days for the migration and one for tests.
[Ada Lovelace]: Fine. I will take the export fix. Deadline is next Wednesday for both.
[Tunde Bello]: One risk is the PDF renderer running one export at a time. If two people export together the second waits.
[Ada Lovelace]: Let us measure it before we decide to queue exports. Add that to the sprint.
[Tunde Bello]: Also we should require tests for the new permission code. Review has been rushed.
[Ada Lovelace]: Yes, no merge without tests. That is a rule from now on.
[Tunde Bello]: And documentation. Each feature needs a short write-up when it ships.
[Ada Lovelace]: Agreed. Who owns the release notes?
[Tunde Bello]: Nobody yet. Open question.
[Ada Lovelace]: I will ask the team lead. Last item: the customer wants Excel export of tasks.
[Tunde Bello]: That is small, half a day. But I do not know if they want one sheet per project.
[Ada Lovelace]: Open question for the customer. Let us end here."""

SPRINT_NOTES = """## Summary
Sprint 14 planning. Export stays in the sprint; search index under 300 ms is the second item.

## Decisions
- Export work stays in sprint 14 as priority one.
- No merge without tests for permission code.
- Each feature gets a short write-up when it ships.

## Action items
- Ada: fix the export, due next Wednesday.
- Tunde: build the search index (migration two days, tests one day), due next Wednesday.
- Ada: measure concurrent export load.

## Open questions
- Who owns the release notes?
- Does the customer want one Excel sheet per project?"""

LECTURE = """[Dr Mensah]: Today we cover cellular respiration. By the end you should be able to explain where ATP comes from.
[Dr Mensah]: Respiration has three stages: glycolysis, the Krebs cycle and the electron transport chain.
[Dr Mensah]: Glycolysis happens in the cytoplasm. One glucose molecule is split into two pyruvate molecules.
[Student]: Does glycolysis need oxygen?
[Dr Mensah]: No. Glycolysis is anaerobic. It makes a net gain of two ATP.
[Dr Mensah]: The pyruvate then enters the mitochondria. There the Krebs cycle runs in the matrix.
[Dr Mensah]: The Krebs cycle produces carbon dioxide and carries electrons to NADH and FADH2.
[Dr Mensah]: Key term: NADH is the electron carrier that feeds the electron transport chain.
[Student]: How much ATP is made in total?
[Dr Mensah]: The textbook says about thirty to thirty-two ATP per glucose. I will give you the exact accounting next class.
[Dr Mensah]: The electron transport chain sits on the inner mitochondrial membrane. Oxygen is the final electron acceptor and it forms water.
[Dr Mensah]: Without oxygen, cells use fermentation instead. Lactic acid fermentation happens in muscle cells.
[Student]: Is fermentation the same as anaerobic respiration?
[Dr Mensah]: Not exactly. I will leave that as a question for you to look up.
[Dr Mensah]: Worked example: if a cell makes two ATP in glycolysis and two in the Krebs cycle, how many ATP came before the electron transport chain?
[Dr Mensah]: Four. The rest comes from the chain.
[Dr Mensah]: Homework: answer questions one to five from chapter six. It is due next Friday.
[Dr Mensah]: There is also a lab report on enzyme activity. I have not fixed the date yet.
[Dr Mensah]: The quiz next week covers glycolysis and the Krebs cycle only.
[Student]: Will the quiz include the electron transport chain?
[Dr Mensah]: No, that comes in the following class."""

LECTURE_NOTES = """## Summary
Lecture on cellular respiration: glycolysis, the Krebs cycle and the electron transport chain.

## Decisions
- The quiz next week covers glycolysis and the Krebs cycle only.

## Action items
- Students: chapter six questions one to five, due next Friday.
- Dr Mensah: fix the date of the enzyme lab report.

## Open questions
- Is fermentation the same as anaerobic respiration?
- Exact total ATP accounting (next class)."""

BRIEF = """Acme Logistics Software: services and terms.
Onboarding package: 50,000 naira (setup, training for two dispatchers, two weeks of support).
Driver app: included for up to 40 drivers, then 1,000 naira per driver per month.
Payment terms: 50% on signing, 50% on go-live. Support hours: 8am to 6pm, Monday to Friday.
House style: plain words, short sentences, address people by first name."""


def now() -> datetime:
    return datetime.now(UTC)


# ---- the stand-in model ------------------------------------------------------------------------


def _blank_block() -> dict:
    return {"level": None, "text": None, "sources": None, "items": None, "columns": None, "rows": None, "title": None, "cards": None}  # fmt: skip


def _src(kind, meeting, line=None, ref=None):
    return {"kind": kind, "meeting_id": meeting, "line": line, "quote": None, "ref": ref}


def _para(text, sources=()):
    return {**_blank_block(), "type": "paragraph", "text": text, "sources": list(sources)}


def _heading(text, level=2):
    return {**_blank_block(), "type": "heading", "level": level, "text": text}


def _bullets(items):
    return {**_blank_block(), "type": "bullets", "items": [{"text": t, "sources": list(s)} for t, s in items]}  # fmt: skip


def _table(columns, rows):
    return {**_blank_block(), "type": "table", "columns": columns, "rows": [[{"text": c, "sources": list(s)} for c, s in row] for row in rows]}  # fmt: skip


def _column(title, cards):
    return {**_blank_block(), "type": "column", "title": title, "cards": [{"text": t, "kind": k, "owner": o, "sources": list(s)} for t, k, o, s in cards]}  # fmt: skip


# What each document is made of: (heading, shape). The model is told these in the real
# instruction; here they stand for "the model followed it".
LAYOUT = {
    "proposal": [
        ("Background", "p"),
        ("What we heard", "b"),
        ("Proposed solution", "p"),
        ("Scope and deliverables", "b"),
        ("Timeline", "t:Phase|When"),
        ("Investment", "t:Item|Price"),
        ("Terms and assumptions", "p"),
        ("Next steps", "b"),
    ],  # fmt: skip
    "requirements": [
        ("Goals", "b"),
        ("Users", "p"),
        ("Requirements", "t:Requirement|Priority|Owner"),
        ("Open questions", "b"),
        ("Out of scope", "p"),
        ("Risks", "b"),
        ("Acceptance", "p"),
    ],  # fmt: skip
    "marketing_post": [("Post", "p")],
    "decision_memo": [
        ("Context", "p"),
        ("Options discussed", "b"),
        ("Decision", "p"),
        ("Consequences", "b"),
        ("Follow-ups", "b"),
    ],  # fmt: skip
    "sop": [("Purpose", "p"), ("Steps", "b"), ("Checks", "b")],
    "status_report": [
        ("Progress", "b"),
        ("Decisions", "b"),
        ("Blockers", "b"),
        ("Next", "t:Task|Owner|Due"),
    ],  # fmt: skip
    "client_briefing": [
        ("History", "b"),
        ("What was promised", "b"),
        ("Open items", "b"),
        ("Questions for the next call", "b"),
    ],  # fmt: skip
    "study_guide": [
        ("Key ideas", "b"),
        ("Definitions", "b"),
        ("Worked example", "p"),
        ("What to revise", "b"),
    ],  # fmt: skip
    "practice_quiz": [("Questions", "b"), ("Answers", "b")],
    "revision_plan": [("Topics", "b"), ("Plan", "t:Day|Topic")],
    "class_faq": [("Questions and answers", "b"), ("Still open", "b")],
}
TABLES = {
    "action_tracker": ["Task", "Owner", "Due date", "Status"],
    "flashcards": ["Front", "Back"],
    "glossary": ["Term", "Meaning", "Example"],
    "assignments": ["Task", "Due date", "Notes"],
}


class LiveModel:
    """The OpenAI client, replaced: drafts by copying real lines of the prompt it was sent."""

    def __init__(self) -> None:
        self.calls = 0
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self._create))

    def _create(self, **kwargs):
        self.calls += 1
        user = kwargs["messages"][1]["content"]
        content = json.dumps(self.compose(user))
        usage = SimpleNamespace(prompt_tokens=_tokens(user + kwargs["messages"][0]["content"]), completion_tokens=_tokens(content))  # fmt: skip
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=content), finish_reason="stop")], usage=usage)  # fmt: skip

    def compose(self, user: str) -> dict:
        name = re.search(r"^Recipe: (.*)$", user, re.M).group(1)
        starter = next(s for s in library.STARTERS if s.name == name)
        otype = starter.output_type
        meetings = [int(n) for n in re.findall(r"^Meeting id: (\d+)", user, re.M)]
        m = meetings[0]
        lines = [(int(n), who, said) for n, who, said in re.findall(r"^\[L(\d+)\] \[([^\]]*)\]: (.*)$", user, re.M)]  # fmt: skip
        decisions = re.findall(r"^\[D(\d+)\] (.*)$", user, re.M)
        tasks = re.findall(r"^\[T(\d+)\] (.*?) \(owner: ([^;]*); due: ([^)]*)\)", user, re.M)
        has_date = bool(re.search(r"\d{4}-\d{2}-\d{2}|next \w+day|first week of", user))
        key = starter.key

        def cite(i):
            return [_src("line", m, lines[i % len(lines)][0])] if lines else [_src("meeting", m)]

        def said(i):
            return lines[i % len(lines)][2] if lines else "As discussed in the meeting."

        def many(i):
            if tasks and i % 2:
                t = tasks[i % len(tasks)]
                return f"{t[1]} ({t[2]})", [
                    _src("task", meetings[i % len(meetings)], ref=int(t[0]))
                ]
            if decisions:
                d = decisions[i % len(decisions)]
                return d[1], [_src("decision", meetings[i % len(meetings)], ref=int(d[0]))]
            return f"Notes of meeting {meetings[i % len(meetings)]}.", [_src("meeting", meetings[i % len(meetings)])]  # fmt: skip

        def point(i):
            return (said(i), cite(i)) if starter.scope == "one" else many(i)

        blocks: list[dict] = []
        title = starter.name
        if otype == "email":
            other = (
                next((w for _, w, _ in lines if w != lines[0][1]), "there") if lines else "there"
            )
            first = other.split()[0]
            blocks = [_para("Subject: Thanks for today, next steps"), _para(f"Hi {first},"), _para("Thank you for your time today. Here is a short recap of what we covered.", cite(0)), _bullets([point(2), point(5), point(8)])]  # fmt: skip
            blocks.append(_para("Next step: " + ("as agreed above." if has_date else "[need: date of the next step]")))  # fmt: skip
            blocks.append(_para("Kind regards"))
        elif otype == "board":
            blocks = [
                _column("What we decided", [(said(i), "decision", None, cite(i)) for i in (1, 6)]),
                _column(
                    "Who does what",
                    [(said(i), "action", lines[i % len(lines)][1], cite(i)) for i in (10, 12)],
                ),  # fmt: skip
                _column(
                    "Still open",
                    [
                        (said(14), "question", None, cite(14)),
                        ("Confirm the date: [need: date]", "question", None, []),
                    ],
                ),  # fmt: skip
                _column("Parked", []),
            ]
        elif otype == "table" or key in TABLES:
            cols = TABLES.get(key, ["Item", "Detail"])
            rows = []
            for i in range(0, 6):
                row = [(point(i)[0][:90], point(i)[1])]
                for c in cols[1:]:
                    if "due" in c.lower():
                        row.append(("[need: due date]" if not has_date or i % 3 == 0 else "next Friday", []))  # fmt: skip
                    elif c == "Owner":
                        row.append((lines[i % len(lines)][1] if lines else "", []))
                    elif c == "Status":
                        row.append(("Open", []))
                    else:
                        row.append((said(i + 1)[:90], cite(i + 1)))
                rows.append(row)
            blocks = [_heading(starter.name), _table(cols, rows)]
        else:
            layout = LAYOUT.get(key, [("Summary", "p"), ("Points", "b")])
            n = 0
            for heading, shape in layout:
                blocks.append(_heading(heading))
                if shape == "p":
                    text, src = point(n)
                    blocks.append(_para(text, src))
                    n += 1
                elif shape == "b":
                    blocks.append(_bullets([point(n + j) for j in range(3)]))
                    n += 3
                else:
                    cols = shape.split(":")[1].split("|")
                    rows = []
                    for j in range(3):
                        row = [(point(n + j)[0][:80], point(n + j)[1])] + [("[need: " + c.lower() + "]" if (c.lower() in ("when", "due", "owner") and j % 2 == 0) else "see notes", []) for c in cols[1:]]  # fmt: skip
                        rows.append(row)
                    if heading == "Investment":
                        # the one deliberately invented figure: the audit must flag it
                        rows[0] = [("Onboarding package", []), ("450,000 naira", [])]
                        rows[1] = [("Driver app (up to 40 drivers)", []), ("included", [])]
                        rows = rows[:2]
                    blocks.append(_table(cols, rows))
                    n += 3
        return {"title": title, "blocks": blocks}


def _tokens(text: str) -> int:
    return max(1, len(text) // 4)


# ---- the stack ---------------------------------------------------------------------------------


def prepare_database() -> None:
    admin = create_engine(admin_url, isolation_level="AUTOCOMMIT")
    with admin.connect() as connection:
        connection.execute(text(f"drop database if exists {DB_NAME} with (force)"))
        connection.execute(text(f"create database {DB_NAME}"))
    admin.dispose()
    command.upgrade(Config(str(ROOT / "alembic.ini")), "head")


def seed(engine) -> dict:
    ids: dict = {}
    with Session(engine) as session:
        for email, (name, password) in PEOPLE.items():
            user = User(email=email, name=name, password_hash=hash_password(password), email_verified_at=now())  # fmt: skip
            session.add(user)
            session.flush()
            accounts.add_user_defaults(session, user)
            ids[email] = user.id
        state = session.get(AppState, 1) or AppState(id=1)
        state.max_accounts = 10
        session.add(state)
        session.commit()
        ada, tunde, eve = (ids[e] for e in PEOPLE)
        team = teams.ensure_team(session, session.get(User, ada))
        team.is_personal, team.name = False, "Acme Sales"
        session.add(TeamMember(team_id=team.id, user_id=tunde, role="member"))
        session.add(TeamBrief(team_id=team.id, text=BRIEF, updated_by_user_id=ada))
        session.commit()
        ids["team"] = team.id
        template = session.scalars(select(Template.id).where(Template.owner_user_id == ada, Template.name == "General")).one()  # fmt: skip
        n = [0]

        def meeting(title, transcript, notes, day, *, shared=True, series=None, owner=ada):
            n[0] += 1
            row = Meeting(
                owner_user_id=owner, url="https://meet.google.com/abc-defg-hij", platform="google_meet",
                native_id=f"abc-defg-{n[0]:03d}", template_id=template, title=title, status="ready",
                step="done", notes=notes, transcript=transcript, speaker_transcript=transcript,
                created_at=datetime(2026, 11, 2, 10, 0, tzinfo=UTC) + timedelta(days=day),
                recorded_seconds=1800, shared_team_id=team.id if shared else None, series_id=series,
                source="calendar",
            )  # fmt: skip
            session.add(row)
            session.commit()
            return row

        def extras(m, decisions, tasks, questions):
            for k, d in enumerate(decisions):
                session.add(Decision(meeting_id=m.id, text=d, position=k, fingerprint=f"d{m.id}-{k}"))  # fmt: skip
            for k, (title, who) in enumerate(tasks):
                session.add(Task(team_id=team.id, meeting_id=m.id, title=title, spoken_owner=who, source="ai", ai_fingerprint=f"t{m.id}-{k}"))  # fmt: skip
            for k, q in enumerate(questions):
                session.add(OpenQuestion(meeting_id=m.id, text=q, position=k, fingerprint=f"q{m.id}-{k}"))  # fmt: skip
            session.commit()

        weekly = Series(team_id=team.id, owner_user_id=ada, title="Sprint reviews")
        course = Series(team_id=team.id, owner_user_id=ada, title="Biology 101")
        session.add_all([weekly, course])
        session.commit()
        ids["weekly"], ids["course"] = weekly.id, course.id
        sales = meeting("Acme Logistics discovery call", SALES, SALES_NOTES, 5)
        extras(sales, ["Onboarding at 50,000 naira, driver app included for up to 40 drivers.", "Start in the first week of December."], [("Send the written plan", "Ada"), ("Send a sample of the delivery records", "Daniel"), ("Find a time for Mrs Adeyemi", "Daniel")], ["Who signs off on Daniel's side, and when?"])  # fmt: skip
        sprint = meeting("Sprint 14 planning", SPRINT, SPRINT_NOTES, 8, series=weekly.id)
        extras(sprint, ["Export stays in sprint 14.", "No merge without tests for permission code."], [("Fix the export", "Ada"), ("Build the search index", "Tunde"), ("Measure concurrent export load", "Ada")], ["Who owns the release notes?"])  # fmt: skip
        lecture = meeting("Biology 101: cellular respiration", LECTURE, LECTURE_NOTES, 9, series=course.id, owner=ada)  # fmt: skip
        extras(lecture, ["The quiz covers glycolysis and the Krebs cycle only."], [("Chapter six questions one to five", "Students")], ["Is fermentation the same as anaerobic respiration?"])  # fmt: skip
        earlier = []
        for k in range(1, 4):
            m = meeting(f"Sprint {10 + k} review", f"[Ada Lovelace]: Review of sprint {10 + k}.", f"## Summary\nSprint {10 + k} review: the export work moved on and the pilot list grew by {k} customers.\n", k * 7 - 3, series=weekly.id)  # fmt: skip
            extras(m, [f"Keep pilot customers on the weekly release (sprint {10 + k})."], [(f"Follow up with pilot customer {k}", "Tunde")], [f"Who owns support for pilot customer {k}?"])  # fmt: skip
            earlier.append(m.id)
        for k in range(1, 3):
            m = meeting(f"Biology 101 week {k}", f"[Dr Mensah]: Week {k}.", f"## Summary\nWeek {k}: cell structure and enzymes.\n", k, series=course.id)  # fmt: skip
            extras(
                m, [f"Enzymes are covered in week {k}."], [(f"Read chapter {k}", "Students")], []
            )
            earlier.append(m.id)
        private = meeting("Board strategy call, private", "[Ada Lovelace]: The acquisition of Orion for 9 million dollars stays between us.", "## Summary\nPrivate call about a possible acquisition.\n", 11, shared=False)  # fmt: skip
        ids.update(sales=sales.id, sprint=sprint.id, lecture=lecture.id, earlier=earlier, private=private.id)  # fmt: skip
        eve_template = session.scalars(select(Template.id).where(Template.owner_user_id == eve, Template.name == "General")).one()  # fmt: skip
        theirs = Meeting(owner_user_id=eve, url="https://meet.google.com/xyz-xyzx-xyz", platform="google_meet", native_id="xyz-xyzx-xyz", template_id=eve_template, title="Eve's call", status="ready", step="done", notes="## Summary\nEve's private notes.\n", transcript="[Eve]: private.", speaker_transcript="[Eve]: private.", recorded_seconds=600, source="calendar")  # fmt: skip
        session.add(theirs)
        session.commit()
        ids["eves"] = theirs.id
    return ids


def start_stack(port: int):
    prepare_database()
    settings = Settings(_env_file=None, app_env="test", session_secret="live-secret-" + "x" * 30, cookie_secure=False, database_url=scratch_url)  # fmt: skip
    app = create_app(settings)
    model = LiveModel()
    generate._client = lambda: model
    stop = threading.Event()
    from app import db

    db.SessionLocal.configure(bind=create_engine(scratch_url))
    threading.Thread(target=recipe_service.run_loop, args=(stop,), kwargs={"idle_seconds": 0.2}, daemon=True).start()  # fmt: skip
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning"))
    threading.Thread(target=server.run, daemon=True).start()
    while not server.started:
        time.sleep(0.05)
    engine = create_engine(scratch_url)
    ids = seed(engine)
    return server, f"http://127.0.0.1:{port}", engine, ids, model


class Client:
    def __init__(self, base: str, email: str) -> None:
        self.http = httpx.Client(base_url=base, follow_redirects=False, timeout=300)
        self.csrf = self.http.get("/api/session").json()["csrf_token"]
        got = self.http.post("/api/session", json={"email": email, "password": PEOPLE[email][1]}, headers={"X-CSRF-Token": self.csrf})  # fmt: skip
        assert got.status_code == 200, got.text
        self.csrf = got.json()["csrf_token"]

    def call(self, method, path, body=None, headers=None):
        h = (
            {"X-CSRF-Token": self.csrf, **(headers or {})}
            if method != "GET"
            else dict(headers or {})
        )
        return self.http.request(method, path, json=body, headers=h)


def wait_ready(c: Client, doc_id: int) -> dict:
    for _ in range(300):
        got = c.call("GET", f"/api/documents/{doc_id}").json()
        if got["status"] != "writing":
            return got
        time.sleep(0.1)
    raise SystemExit(f"draft {doc_id} never finished")


def one_meeting_for(key: str, ids: dict) -> int:
    school = library.starter_by_key(key).pack == "school"
    if (
        key in ("requirements", "sop", "decision_memo", "action_tracker")
        or key == "board"
        and False
    ):
        return ids["sprint"]
    return ids["lecture"] if school else ids["sales"]


def run_pass(base: str, engine, ids: dict) -> list[dict]:
    ada = Client(base, "ada@example.test")
    rows = []
    for starter in library.STARTERS:
        started = time.time()
        if starter.scope == "many":
            meetings = (
                ids["earlier"][:3]
                if starter.pack == "general"
                else [ids["lecture"]] + ids["earlier"][3:]
            )
            if starter.key == "revision_plan":
                body = {"meeting_ids": meetings, "options": {}}
            else:
                body = {"meeting_ids": [ids["sprint"]] + ids["earlier"][:3], "options": {}}
        else:
            body = {"meeting_ids": [one_meeting_for(starter.key, ids)], "options": {}}
        got = ada.call("POST", "/api/documents", {"recipe_key": starter.key, **body})
        assert got.status_code == 202, (starter.key, got.text)
        doc = wait_ready(ada, got.json()["id"])
        seconds = time.time() - started
        exports = {}
        for fmt in ("md", "docx", "pdf") + (("csv", "xlsx") if any(b["type"] == "table" for b in doc["blocks"]) else ()):  # fmt: skip
            t0 = time.time()
            r = ada.call("GET", f"/api/documents/{doc['id']}/export?format={fmt}")
            exports[fmt] = {"status": r.status_code, "bytes": len(r.content), "seconds": round(time.time() - t0, 2)}  # fmt: skip
        with Session(engine) as session:
            run = session.scalars(select(AiRun).where(AiRun.kind == "recipe").order_by(AiRun.id.desc())).first()  # fmt: skip
            tokens = (run.input_tokens, run.output_tokens, run.status)
        flags = doc["checks"]["flags"]
        rows.append(
            {
                "key": starter.key, "pack": starter.pack, "scope": starter.scope, "output_type": starter.output_type,
                "status": doc["status"], "blocks": len(doc["blocks"]),
                "blanks": [b["what"] for b in doc["checks"]["blanks"]],
                "flags": [{"text": f["text"], "kind": f["kind"]} for f in flags],
                "input_tokens_estimated": tokens[0], "output_tokens_estimated": tokens[1],
                "cost_usd_estimated": round(tokens[0] * 0.10 / 1e6 + tokens[1] * 0.50 / 1e6, 5),
                "ledger_status": tokens[2], "seconds_fake_model": round(seconds, 2), "exports": exports,
                "document_id": doc["id"],
            }
        )  # fmt: skip
        print(f"{starter.key:18} {doc['status']:7} blanks={len(doc['checks']['blanks'])} flags={len(flags)} in={tokens[0]} out={tokens[1]}")  # fmt: skip
    return rows


def cmd_serve() -> None:
    server, base, engine, ids, _ = start_stack(serve_port)
    print(f"serving on {base}")
    for email, (name, password) in PEOPLE.items():
        print(f"  {email} / {password}   ({name})")
    print(json.dumps(ids))
    try:
        while True:
            time.sleep(3600)
    finally:
        server.should_exit = True
        engine.dispose()


def cmd_pass() -> None:
    server, base, engine, ids, model = start_stack(serve_port)
    try:
        rows = run_pass(base, engine, ids)
        OUT.mkdir(parents=True, exist_ok=True)
        (OUT / "results.json").write_text(json.dumps({"model": "LiveModel (stand-in, NOT a real model)", "rows": rows}, indent=2) + "\n")  # fmt: skip
        print(f"model calls: {model.calls}; results in {OUT / 'results.json'}")
    finally:
        server.should_exit = True
        engine.dispose()


def cmd_load() -> None:
    server, base, engine, ids, _ = start_stack(serve_port)
    try:
        sessions = [Client(base, "ada@example.test"), Client(base, "ada@example.test")]
        # a 40-row table draft and a board, made by the real path, then the table edited to 40 rows
        table_key = "action_tracker"
        made = sessions[0].call("POST", "/api/documents", {"recipe_key": table_key, "meeting_ids": [ids["sprint"]]}).json()["id"]  # fmt: skip
        doc = wait_ready(sessions[0], made)
        table = next(b for b in doc["blocks"] if b["type"] == "table")
        while len(table["rows"]) < 40:
            table["rows"].append([{"text": f"Task {len(table['rows']) + 1} for the load test", "sources": []}] + [{"text": c["text"], "sources": []} for c in table["rows"][0][1:]])  # fmt: skip
        blocks = [{**b, "sources": []} if b["type"] == "paragraph" else b for b in doc["blocks"]]
        got = sessions[0].call("PATCH", f"/api/documents/{made}", {"blocks": blocks})
        assert got.status_code == 200, got.text
        board = sessions[0].call("POST", "/api/documents", {"recipe_key": "board", "meeting_ids": [ids["sales"]]}).json()["id"]  # fmt: skip
        wait_ready(sessions[0], board)
        results = {}

        def timed(name, client, doc_id, fmt, bucket):
            t0 = time.time()
            r = client.call("GET", f"/api/documents/{doc_id}/export?format={fmt}")
            bucket.append({"what": name, "status": r.status_code, "seconds": round(time.time() - t0, 2), "bytes": len(r.content)})  # fmt: skip

        for label, jobs in (
            ("alone", [("table-pdf", made, "pdf")]),
            ("alone-docx", [("table-docx", made, "docx")]),
            ("alone-board-pdf", [("board-pdf", board, "pdf")]),
            (
                "two-at-once-table-and-board-pdf",
                [("table-pdf", made, "pdf"), ("board-pdf", board, "pdf")],
            ),
            ("two-at-once-same-pdf", [("table-pdf", made, "pdf"), ("table-pdf", made, "pdf")]),
            ("two-at-once-docx", [("table-docx", made, "docx"), ("board-docx", board, "docx")]),
        ):
            bucket: list = []
            for _ in range(5):
                threads = [threading.Thread(target=timed, args=(n, sessions[i % 2], d, f, bucket)) for i, (n, d, f) in enumerate(jobs)]  # fmt: skip
                for t in threads:
                    t.start()
                for t in threads:
                    t.join()
            results[label] = bucket
        OUT.mkdir(parents=True, exist_ok=True)
        (OUT / "export-load.json").write_text(json.dumps(results, indent=2) + "\n")
        for label, bucket in results.items():
            secs = sorted(x["seconds"] for x in bucket)
            codes = sorted({x["status"] for x in bucket})
            print(f"{label:34} n={len(bucket)} median={secs[len(secs) // 2]}s max={secs[-1]}s statuses={codes}")  # fmt: skip
    finally:
        server.should_exit = True
        engine.dispose()


if __name__ == "__main__":
    if mode not in ("serve", "pass", "load") or not admin_url:
        sys.exit(__doc__)
    {"serve": cmd_serve, "pass": cmd_pass, "load": cmd_load}[mode]()
    _ = date
