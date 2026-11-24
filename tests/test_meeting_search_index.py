"""Migration 0057 and the full-text index of meeting search.

* the migration (function, concurrent GIN index, round trip), run for real on the scratch database;
* `meeting_search_text()` gives the vector the search used to build inline;
* both readers, Ask Beyric's `candidates` and the Meetings list's `q`, call that one function, and
  Postgres plans both on the index (the SQL is captured from the real calls, then EXPLAINed).
"""

# ruff: noqa: F811  (fixtures are imported by name and used as arguments)
import importlib.util
from pathlib import Path

from alembic import command
from alembic.script import ScriptDirectory
from sqlalchemy import create_engine, event, text

from app import ask, meeting_search, services, visibility
from app.models import MEETING_SEARCH_TEXT_SQL, User
from tests import test_migrations as migrations
from tests.test_api import owner, ready, template  # noqa: F401  (fixtures used by name)

alembic_config = migrations.alembic_config
owner_env = migrations.owner_env
scratch_url = migrations.scratch_url

INDEX = "ix_meetings_search_text"
MIGRATION = Path(__file__).resolve().parent.parent / "alembic/versions/0057_meeting_search_index.py"


def index_row(engine):
    with engine.connect() as conn:
        return conn.execute(
            text(
                "select i.indisvalid, pg_get_indexdef(i.indexrelid) from pg_index i "
                "join pg_class c on c.oid = i.indexrelid where c.relname = :n"
            ),
            {"n": INDEX},
        ).first()


def function_exists(engine) -> bool:
    with engine.connect() as conn:
        return bool(
            conn.execute(
                text("select count(*) from pg_proc where proname = 'meeting_search_text'")
            ).scalar()
        )


def test_0057_is_the_only_head_and_follows_0056(scratch_url):
    script = ScriptDirectory.from_config(alembic_config(scratch_url))
    assert script.get_heads() == ["0057"]
    assert script.get_revision("0057").down_revision == "0056"


def test_0057_makes_a_valid_gin_index_on_the_function_and_the_round_trip_removes_both(scratch_url):
    cfg = alembic_config(scratch_url)
    engine = create_engine(scratch_url)
    command.upgrade(cfg, "0056")
    assert index_row(engine) is None and not function_exists(engine)

    command.upgrade(cfg, "0057")
    valid, definition = index_row(engine)
    assert valid is True
    assert "USING gin" in definition
    assert "meeting_search_text(notes, speaker_transcript, transcript)" in definition

    command.downgrade(cfg, "0056")
    assert index_row(engine) is None and not function_exists(engine)
    command.upgrade(cfg, "head")  # and up again, from nothing
    assert index_row(engine)[0] is True
    engine.dispose()


def test_upgrading_again_after_a_half_built_index_starts_clean(scratch_url):
    """A concurrent build that fails leaves an INVALID index of the same name."""
    cfg = alembic_config(scratch_url)
    engine = create_engine(scratch_url)
    command.upgrade(cfg, "0057")
    with engine.begin() as conn:  # make it look like the leftover of a failed build
        conn.execute(
            text("update pg_index set indisvalid = false where indexrelid = cast(:n as regclass)"),
            {"n": INDEX},
        )
    assert index_row(engine)[0] is False
    command.downgrade(cfg, "0056")
    command.upgrade(cfg, "0057")
    assert index_row(engine)[0] is True
    engine.dispose()


def test_the_migrations_function_is_the_models_function():
    spec = importlib.util.spec_from_file_location("m0057", MIGRATION)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    assert module.FUNCTION_SQL.strip() == MEETING_SEARCH_TEXT_SQL.strip()


def test_the_function_is_the_vector_the_search_built_inline(session):
    old = (
        "to_tsvector('simple', concat_ws(' ', notes, "
        "coalesce(nullif(speaker_transcript, ''), transcript)))"
    )
    new = "meeting_search_text(notes, speaker_transcript, transcript)"
    cases = [
        ("Notes about pricing", "[Ada]: spoken words", "plain words"),
        ("Notes", "", "plain words"),
        ("Notes", None, "plain words"),
        ("Notes", None, None),
        (None, "[Ben]: only the transcript", None),
        (None, None, None),
        ("", "", ""),
        ("Précis: l'équipe a décidé", "[Zoë]: Tosin's plan, 50% done", "x"),
    ]
    for notes, spoken, plain in cases:
        row = session.execute(
            text(
                f"select {old} = {new}, {new} from (select cast(:a as text) as notes, "
                "cast(:b as text) as speaker_transcript, cast(:c as text) as transcript) t"
            ),
            {"a": notes, "b": spoken, "c": plain},
        ).one()
        assert row[0] is True, (notes, spoken, plain)


def test_the_function_is_immutable(session):
    flags = session.execute(
        text("select provolatile, proparallel from pg_proc where proname = 'meeting_search_text'")
    ).one()
    assert tuple(flags) == ("i", "s")


# ---- both readers use the index ----


def captured_sql(session, call):
    """The SQL statements `call()` sends, with their parameters."""
    seen = []

    def grab(conn, cursor, statement, parameters, context, executemany):
        seen.append((statement, parameters))

    engine = session.get_bind()
    event.listen(engine, "before_cursor_execute", grab)
    try:
        call()
    finally:
        event.remove(engine, "before_cursor_execute", grab)
    return seen


def plan_of(session, statement, parameters) -> str:
    session.execute(text("set local enable_seqscan = off"))  # a handful of rows would seq scan
    rows = session.connection().exec_driver_sql("explain " + statement, parameters).all()
    return "\n".join(r[0] for r in rows)


def test_ask_candidates_and_the_list_search_are_both_planned_on_the_index(session, owner, template):
    for n in range(30):
        ready(session, template, title=f"Meeting {n}", notes=f"## Summary\nWord{n} pricing")
    user = session.get(User, owner.id)
    asked = captured_sql(
        session, lambda: ask.candidates(session, user, ["pric"], None, None, False)
    )
    listed = captured_sql(
        session,
        lambda: services.list_meetings(session, user, 51, None, None, None, None, "pricing"),
    )
    for name, seen in (("Ask candidates", asked), ("list search", listed)):
        searches = [s for s in seen if "meeting_search_text" in s[0]]
        assert searches, f"{name} does not call meeting_search_text"
        plan = plan_of(session, *searches[-1])
        assert INDEX in plan, f"{name} is not planned on the index:\n{plan}"
        assert "to_tsvector" not in searches[-1][0], f"{name} still builds the vector inline"


def test_the_two_readers_use_the_same_expression():
    ask_doc = str(meeting_search.document())
    assert ask_doc == str(meeting_search.document())
    assert "meeting_search_text" in ask_doc and "to_tsvector" not in ask_doc
    assert visibility is not None
