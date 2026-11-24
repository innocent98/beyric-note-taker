"""Time the Meetings list's keyword search on a scratch database of a few thousand meetings.

    PYTHONPATH=. python scripts/time_meeting_search.py \
        postgresql+psycopg://USER:PASS@HOST:PORT/postgres [meetings, default 4000]

Makes the tables from the models (no migrations: nothing here depends on one), fills them with
meetings whose notes (about 400 words) and speaker-labelled transcripts (4,000 to 9,000 words,
a half-hour to a one-hour call) are made from a business-words vocabulary with a long tail of
rare words, then times `services.list_meetings(..., keywords=...)` (the call behind
`GET /api/meetings?q=`) for typical searches, a no-search baseline and, for comparison only, the
same searches with a GIN expression index in a scratch copy. Prints a table and the plans; it
changes nothing outside the scratch database and adds no index to the app.
"""

import random
import statistics
import sys
import time
from datetime import UTC, datetime, timedelta
from urllib.parse import urlparse

DB_NAME = "notetaker_time_search"
admin_url = sys.argv[1] if len(sys.argv) > 1 else ""
COUNT = int(sys.argv[2]) if len(sys.argv) > 2 else 4000

from sqlalchemy import create_engine, insert, select, text  # noqa: E402
from sqlalchemy.orm import Session  # noqa: E402

from app import meeting_search, services, visibility  # noqa: E402
from app.models import Base, Meeting, Team, TeamMember, Template, User  # noqa: E402

BUSINESS = (
    "the project budget timeline client portal payment checkout design review launch release "
    "customer feedback priority risk blocker estimate sprint backlog update deadline Friday "
    "Monday decision approve schedule meeting notes action owner marketing engineering revenue "
    "forecast quarter hiring contract invoice vendor security privacy pricing roadmap "
    "onboarding migration support incident retention growth partner proposal discount renewal "
    "we should think about the next steps and then agree that it makes sense to move on"
).split()
SYLLABLES = (
    "ba be bi bo bu da de di do du ka ke ki ko ku la le li lo lu ma me mi mo mu na ne ni no nu "
    "ra re ri ro ru sa se si so su ta te ti to tu"
).split()
RARE_TERMS = ["ingestion", "hedgehog", "zebra", "kubernetes", "ledger"]


def build_text(rng: random.Random, words: int, tail: list[str]) -> str:
    out = []
    for _ in range(words):
        out.append(rng.choice(BUSINESS) if rng.random() < 0.72 else rng.choice(tail))
    return " ".join(out)


def prepare(url_for_scratch: str):
    admin = create_engine(admin_url, isolation_level="AUTOCOMMIT")
    with admin.connect() as connection:
        connection.execute(text(f"drop database if exists {DB_NAME} with (force)"))
        connection.execute(text(f"create database {DB_NAME}"))
    admin.dispose()
    engine = create_engine(url_for_scratch)
    Base.metadata.create_all(engine)
    return engine


def seed(engine) -> tuple[int, visibility.ActiveTeam]:
    rng = random.Random(11)
    tail = ["".join(rng.choice(SYLLABLES) for _ in range(rng.randint(2, 4))) for _ in range(4000)]
    with Session(engine) as session:
        user = User(email="owner@example.test", name="Owner Example", password_hash="x")
        session.add(user)
        session.flush()
        team = Team(owner_user_id=user.id, name="Personal", is_personal=True)
        session.add(team)
        session.flush()
        session.add(TeamMember(team_id=team.id, user_id=user.id, role="owner"))
        template = Template(owner_user_id=user.id, name="General", instructions="x")
        session.add(template)
        session.commit()
        user_id, team_id, template_id = user.id, team.id, template.id
        now = datetime(2026, 11, 24, tzinfo=UTC)
        rows = []
        for n in range(COUNT):
            words = rng.randint(4000, 9000)
            turns, used = [], 0
            while used < words:
                size = min(rng.randint(40, 120), words - used)
                speaker = rng.choice(["Ada Example", "Ben Sample", "Kemi Adeyemi"])
                turns.append(f"[{speaker}]: " + build_text(rng, size, tail))
                used += size
            transcript = "\n".join(turns)
            if n % 400 == 7:
                transcript += " " + rng.choice(RARE_TERMS)  # a handful of rare words
            notes = "\n\n".join(
                f"## {heading}\n" + build_text(rng, 60, tail)
                for heading in (
                    "Summary",
                    "Key points",
                    "Decisions",
                    "Action items",
                    "Open questions",
                )
            )
            rows.append(
                {
                    "owner_user_id": user_id,
                    "url": "https://meet.google.com/abc-defg-hij",
                    "platform": "google_meet",
                    "native_id": f"m{n:05d}",
                    "template_id": template_id,
                    "status": "ready" if n % 20 else "processing",
                    "step": "done",
                    "title": f"{rng.choice(BUSINESS).title()} {rng.choice(BUSINESS)} sync {n}",
                    "notes": notes,
                    "transcript": transcript,
                    "speaker_transcript": transcript,
                    "created_at": now - timedelta(hours=n * 3),
                }
            )
            if len(rows) == 200:
                session.execute(insert(Meeting), rows)
                session.commit()
                rows = []
        if rows:
            session.execute(insert(Meeting), rows)
            session.commit()
    return user_id, visibility.ActiveTeam(id=team_id, personal=True)


def timed(
    engine, user_id: int, active, keywords, before_id=None, runs=3
) -> tuple[float, float, int]:
    seconds = []
    found = 0
    for _ in range(runs):
        with Session(engine) as session:
            user = session.get(User, user_id)
            started = time.perf_counter()
            rows = services.list_meetings(
                session, user, 51, before_id, None, None, active, keywords
            )
            found = len(rows)
            seconds.append(time.perf_counter() - started)
    return statistics.median(seconds) * 1000, max(seconds) * 1000, found


QUERIES = [
    ("no search (baseline)", None),
    ("title only (filler words)", "meeting"),
    ("common word: pricing", "pricing"),
    ("two common words: pricing roadmap", "pricing roadmap"),
    ("rare word: ingestion", "ingestion"),
    ("word that matches nothing: qqzzxx", "qqzzxx"),
    ("a title phrase: Budget review sync", "Budget review sync"),
]


def report(engine, user_id: int, active, label: str) -> None:
    print(f"\n{label}")
    print(f"{'search':45} {'median ms':>10} {'max ms':>8} {'rows':>5}")
    for name, keywords in QUERIES:
        median, worst, found = timed(engine, user_id, active, keywords)
        print(f"{name:45} {median:10.0f} {worst:8.0f} {found:5d}")


def main() -> None:
    if not admin_url:
        sys.exit(__doc__)
    parsed = urlparse(admin_url)
    scratch = parsed._replace(path=f"/{DB_NAME}").geturl()
    engine = prepare(scratch)
    user_id, active = seed(engine)
    with engine.connect() as connection:
        connection.execute(text("analyze meetings"))
        connection.commit()
        size = connection.execute(
            text(
                "select pg_size_pretty(pg_total_relation_size('meetings')), count(*) from meetings"
            )
        ).one()
    print(f"{size[1]} meetings, table and TOAST {size[0]}")
    compare_only = "compare-only" in sys.argv[3:]
    if not compare_only:
        report(engine, user_id, active, "As shipped (no index)")
        compiled = (
            select(Meeting.id)
            .where(meeting_search.keyword_clause("pricing roadmap"))
            .order_by(Meeting.id.desc())
            .limit(51)
            .compile(dialect=engine.dialect)
        )
        with engine.connect() as connection:
            plan = connection.exec_driver_sql(
                "explain (analyze, buffers) " + str(compiled), compiled.params
            ).all()
        print("\nPlan for `pricing roadmap`, no index:")
        print("\n".join(row[0] for row in plan[:14]))

    # For comparison only (nothing here is in the app). A GIN index must be on an IMMUTABLE
    # expression: `concat_ws` is only STABLE, so the index needs a wrapper function marked
    # IMMUTABLE, and `meeting_search.document()` would call it. And the title match must not sit
    # in the same OR as the full-text match, or Postgres cannot use the index and scans every row;
    # `id IN (title matches) OR id IN (full-text matches)` lets each side use its own scan.
    inline = (
        "to_tsvector('simple', concat_ws(' ', notes, "
        "coalesce(nullif(speaker_transcript, ''), transcript)))"
    )
    wrapper = "meeting_text(notes, speaker_transcript, transcript)"
    shipped = (
        "select id from meetings where (title ilike :p) or (status = 'ready' and {doc} @@ "
        "to_tsquery('simple', :q)) order by id desc limit 51"
    )
    split = (
        "select id from meetings where id in (select id from meetings where title ilike :p) "
        "or id in (select id from meetings where status = 'ready' and {doc} @@ "
        "to_tsquery('simple', :q)) order by id desc limit 51"
    )
    cases = (
        ("common word: pricing", "%pricing%", "pricing:*"),
        ("two words: pricing roadmap", "%pricing roadmap%", "pricing:* & roadmap:*"),
        ("rare word: ingestion", "%ingestion%", "ingestion:*"),
        ("nothing: qqzzxx", "%qqzzxx%", "qqzzxx:*"),
    )

    def run(title: str, sql: str) -> None:
        print(f"\n{title:48} {'median ms':>10} {'max ms':>8}")
        for name, pattern, query in cases:
            seconds = []
            for _ in range(3):
                with engine.connect() as connection:
                    started = time.perf_counter()
                    connection.execute(text(sql), {"p": pattern, "q": query}).all()
                    seconds.append(time.perf_counter() - started)
            print(
                f"  {name:46} {statistics.median(seconds) * 1000:10.0f} {max(seconds) * 1000:8.0f}"
            )

    run("split shape, inline expression, NO index", split.format(doc=inline))
    with engine.begin() as connection:
        connection.execute(
            text(
                "create function meeting_text(notes text, spoken text, plain text) "
                "returns tsvector "
                "language sql immutable as $$ select to_tsvector('simple', "
                "coalesce(notes, '') || ' ' || coalesce(nullif(spoken, ''), plain, '')) $$"
            )
        )
        connection.execute(text(f"create index ix_meetings_text on meetings using gin ({wrapper})"))
        connection.execute(text("analyze meetings"))
    with engine.connect() as connection:
        size = connection.execute(
            text("select pg_size_pretty(pg_relation_size('ix_meetings_text'))")
        ).scalar()
    print(f"\nGIN index size: {size}")
    run("shipped shape (one OR), WITH the index", shipped.format(doc=wrapper))
    run("split shape, WITH the index", split.format(doc=wrapper))
    with engine.connect() as connection:
        plan = connection.exec_driver_sql(
            "explain (analyze) "
            + split.format(doc=wrapper).replace(":p", "%(p)s").replace(":q", "%(q)s"),
            {"p": "%ingestion%", "q": "ingestion:*"},
        ).all()
    print("\nPlan, split shape with the index, `ingestion`:")
    print("\n".join(row[0] for row in plan))
    engine.dispose()


if __name__ == "__main__":
    main()
