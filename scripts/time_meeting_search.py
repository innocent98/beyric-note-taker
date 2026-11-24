"""Time meeting search on a scratch database of a few thousand meetings, before and after the
full-text index (migration 0057).

    PYTHONPATH=. python scripts/time_meeting_search.py \
        postgresql+psycopg://USER:PASS@HOST:PORT/postgres [meetings, default 4000]

Makes the tables from the models, drops the index, fills them with meetings whose notes (about
400 words) and speaker-labelled transcripts (4,000 to 9,000 words, a half-hour to a one-hour
call) are made from a business-words vocabulary with a long tail of rare words, and times the two
real readers (`services.list_meetings(..., keywords=...)`, the call behind `GET /api/meetings?q=`,
and `ask.candidates`, behind Ask Beyric) for typical searches:

1. the shape PR 261 shipped (one `OR`, the vector built inline), as the starting point;
2. the code as it is now, with NO index;
3. the same code after `CREATE INDEX CONCURRENTLY` (the SQL of migration 0057).

For (2) and (3) it also prints `EXPLAIN (ANALYZE)` of the exact SQL each reader sends. Everything
happens in the scratch database.
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

from sqlalchemy import create_engine, event, insert, text  # noqa: E402
from sqlalchemy.orm import Session  # noqa: E402

from app import ask, services, visibility  # noqa: E402
from app.models import Base, Meeting, Team, TeamMember, Template, User  # noqa: E402
from app.words import stems  # noqa: E402

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
    with engine.begin() as connection:  # the 'before': seed with no index, build it afterwards
        connection.execute(text("drop index ix_meetings_search_text"))
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


def reader_calls(engine, user_id: int, active, keywords: str):
    """The two real readers, as callables that run on a fresh session."""

    def list_search() -> int:
        with Session(engine) as session:
            user = session.get(User, user_id)
            return len(
                services.list_meetings(session, user, 51, None, None, None, active, keywords)
            )

    def ask_candidates() -> int:
        with Session(engine) as session:
            user = session.get(User, user_id)
            found, _ = ask.candidates(session, user, stems(keywords), None, None, False)
            return len(found)

    return {"list search (q=)": list_search, "Ask candidates": ask_candidates}


CASES = [
    ("common word: pricing", "pricing"),
    ("two words: pricing roadmap", "pricing roadmap"),
    ("rare word: ingestion", "ingestion"),
    ("nothing matches: qqzzxx", "qqzzxx"),
]


def measure(call, runs: int = 3) -> tuple[float, float, int]:
    seconds, found = [], 0
    for _ in range(runs):
        started = time.perf_counter()
        found = call()
        seconds.append(time.perf_counter() - started)
    return statistics.median(seconds) * 1000, max(seconds) * 1000, found


def report(engine, user_id: int, active, label: str) -> None:
    print(f"\n{label}")
    print(f"  {'reader and search':52} {'median ms':>10} {'max ms':>8} {'rows':>5}")
    for name, keywords in CASES:
        for reader, call in reader_calls(engine, user_id, active, keywords).items():
            median, worst, found = measure(call)
            print(f"  {reader + ': ' + name:52} {median:10.0f} {worst:8.0f} {found:5d}")


def explain(engine, user_id: int, active, label: str, keywords: str) -> None:
    """EXPLAIN (ANALYZE) of the exact SQL each reader sends for `keywords`."""
    for reader, call in reader_calls(engine, user_id, active, keywords).items():
        seen: list[tuple[str, object]] = []

        def grab(conn, cursor, statement, parameters, context, executemany, seen=seen):
            if "meeting_search_text" in statement or "to_tsvector" in statement:
                seen.append((statement, parameters))

        event.listen(engine, "before_cursor_execute", grab)
        try:
            call()
        finally:
            event.remove(engine, "before_cursor_execute", grab)
        statement, parameters = seen[-1]
        with engine.connect() as connection:
            rows = connection.exec_driver_sql(
                "explain (analyze, buffers off, costs off) " + statement, parameters
            ).all()
        print(f"\nPlan, {reader}, `{keywords}`, {label}:")
        print("\n".join("  " + row[0] for row in rows))


def shipped_in_261(engine) -> None:
    """The query shape PR 261 shipped: one OR, the vector built inline. Raw SQL, same data."""
    inline = (
        "to_tsvector('simple', concat_ws(' ', notes, "
        "coalesce(nullif(speaker_transcript, ''), transcript)))"
    )
    sql = (
        "select id from meetings where (title ilike :p) or (status = 'ready' and "
        + inline
        + " @@ to_tsquery('simple', :q)) order by id desc limit 51"
    )
    print("\nAs shipped in PR 261 (one OR, vector built inline, no index)")
    print(f"  {'search':52} {'median ms':>10} {'max ms':>8}")
    for name, pattern, query in (
        ("common word: pricing", "%pricing%", "pricing:*"),
        ("two words: pricing roadmap", "%pricing roadmap%", "pricing:* & roadmap:*"),
        ("rare word: ingestion", "%ingestion%", "ingestion:*"),
        ("nothing matches: qqzzxx", "%qqzzxx%", "qqzzxx:*"),
    ):

        def run(pattern=pattern, query=query) -> int:
            with engine.connect() as connection:
                return len(connection.execute(text(sql), {"p": pattern, "q": query}).all())

        median, worst, _ = measure(run)
        print(f"  {name:52} {median:10.0f} {worst:8.0f}")


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
    if "skip-261" not in sys.argv[3:]:
        shipped_in_261(engine)
    report(engine, user_id, active, "The code now, NO index")
    explain(engine, user_id, active, "no index", "ingestion")
    # The migration's own statement, in autocommit as `autocommit_block` runs it.
    started = time.perf_counter()
    with engine.connect().execution_options(isolation_level="AUTOCOMMIT") as connection:
        connection.execute(
            text(
                "CREATE INDEX CONCURRENTLY ix_meetings_search_text ON meetings "
                "USING gin (meeting_search_text(notes, speaker_transcript, transcript))"
            )
        )
    built = time.perf_counter() - started
    with engine.begin() as connection:
        connection.execute(text("analyze meetings"))
    with engine.connect() as connection:
        index_size = connection.execute(
            text("select pg_size_pretty(pg_relation_size('ix_meetings_search_text'))")
        ).scalar()
    print(f"\nIndex built CONCURRENTLY in {built:.1f} s, {index_size}")
    report(engine, user_id, active, "The code now, WITH the index")
    explain(engine, user_id, active, "with the index", "ingestion")
    explain(engine, user_id, active, "with the index", "pricing")
    engine.dispose()


if __name__ == "__main__":
    main()
