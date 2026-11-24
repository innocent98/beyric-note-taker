"""Candidate only, NOT in the app: a stored generated `tsvector` column with its own GIN index.

Times the two readers' queries on it, on the scratch database that
`scripts/time_meeting_search.py` leaves behind (run that first):

    PYTHONPATH=. python scripts/time_search_stored_column.py [database url]

Adds `meetings.search_tsv` (GENERATED ALWAYS AS meeting_search_text(...) STORED), builds a GIN
index on it, then times the list's split query and Ask's ranked query as raw SQL. It changes only
the scratch database `notetaker_time_search`.
"""

import statistics
import sys
import time

from sqlalchemy import create_engine, text

URL = (
    sys.argv[1]
    if len(sys.argv) > 1
    else "postgresql+psycopg://postgres:postgres@localhost:55432/notetaker_time_search"
)
engine = create_engine(URL)

started = time.perf_counter()
with engine.begin() as connection:  # takes an ACCESS EXCLUSIVE lock while the table is rewritten
    connection.execute(
        text(
            "alter table meetings add column search_tsv tsvector generated always as "
            "(meeting_search_text(notes, speaker_transcript, transcript)) stored"
        )
    )
print(f"add column (table rewrite): {time.perf_counter() - started:.1f} s")
started = time.perf_counter()
with engine.connect().execution_options(isolation_level="AUTOCOMMIT") as connection:
    connection.execute(
        text("create index concurrently ix_meetings_search_tsv on meetings using gin (search_tsv)")
    )
    connection.execute(text("analyze meetings"))
print(f"index: {time.perf_counter() - started:.1f} s")
with engine.connect() as connection:
    sizes = connection.execute(
        text(
            "select pg_size_pretty(pg_total_relation_size('meetings')), "
            "pg_size_pretty(pg_relation_size('ix_meetings_search_tsv'))"
        )
    ).one()
print(f"table with the column and its TOAST: {sizes[0]}; the new index: {sizes[1]}")

LIST = (
    "select id from meetings where owner_user_id = 1 and ("
    "id in (select id from meetings where title ilike :p) or id in "
    "(select id from meetings where status = 'ready' and search_tsv @@ to_tsquery('simple', :q))"
    ") order by id desc limit 51"
)
ASK = (
    "select id from meetings where status = 'ready' and owner_user_id = 1 "
    "and search_tsv @@ to_tsquery('simple', :q) "
    "order by ts_rank(search_tsv, to_tsquery('simple', :q)) desc, created_at desc, id desc "
    "limit 12"
)
CASES = (
    ("common pricing", "%pricing%", "pric:*"),
    ("two words", "%pricing roadmap%", "pric:* & roadmap:*"),
    ("rare ingestion", "%ingestion%", "ingestion:*"),
    ("none", "%qqzzxx%", "qqzzxx:*"),
)
for name, pattern, query in CASES:
    for label, sql in (("list", LIST), ("ask", ASK)):
        seconds = []
        rows = 0
        for _ in range(3):
            started = time.perf_counter()
            with engine.connect() as connection:
                rows = len(connection.execute(text(sql), {"p": pattern, "q": query}).all())
            seconds.append(time.perf_counter() - started)
        median, worst = statistics.median(seconds) * 1000, max(seconds) * 1000
        print(f"{label:5}{name:18}{median:8.0f} ms  max {worst:6.0f}  rows {rows}")
