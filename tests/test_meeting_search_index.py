"""Migration 0057 and the full-text index of meeting search.

* the migration (function, concurrent GIN index, round trip), run for real on the scratch database;
* `meeting_search_text()` gives the vector the search used to build inline;
* both readers, Ask Beyric's `candidates` and the Meetings list's `q`, call that one function, and
  Postgres plans both on the index (the SQL is captured from the real calls, then EXPLAINed).
"""

# ruff: noqa: F811  (fixtures are imported by name and used as arguments)
import importlib.util
import unicodedata
from pathlib import Path

from alembic import command
from alembic.script import ScriptDirectory
from sqlalchemy import create_engine, event, text

from app import ask, meeting_search, services, visibility
from app.models import MEETING_SEARCH_TEXT_SQL, User
from app.words import stems
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


def vector_of(session, notes, spoken, plain) -> str:
    return session.execute(
        text(
            "select meeting_search_text(notes, speaker_transcript, transcript)::text from "
            "(select cast(:a as text) as notes, cast(:b as text) as speaker_transcript, "
            "cast(:c as text) as transcript) t"
        ),
        {"a": notes, "b": spoken, "c": plain},
    ).scalar()


def test_without_accents_the_function_is_the_vector_the_search_built_inline(session):
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
        ("Tosin's plan, 50% done", "[Zoe]: the follow-up", "x"),
    ]
    for notes, spoken, plain in cases:
        row = session.execute(
            text(
                f"select {old} = {new} from (select cast(:a as text) as notes, "
                "cast(:b as text) as speaker_transcript, cast(:c as text) as transcript) t"
            ),
            {"a": notes, "b": spoken, "c": plain},
        ).one()
        assert row[0] is True, (notes, spoken, plain)


ACCENTED = [
    "Précis: l'équipe a décidé",
    "Zoë et François, à Málaga, señor Peña",
    "Ọlá Ṣọlá Adéwálé Ọ̀gùn",  # Yoruba: dots below and tone marks
    "Nguyễn Văn Ạnh",  # Vietnamese, with stacked marks
    "ﬁnancial ½ plan",  # a ligature and a vulgar fraction (the K in NFKD)
]


def test_the_stored_text_is_folded_the_way_a_typed_word_is(session):
    """What `normalise` does to a typed word is done to the stored text: the vector of an accented
    text equals the vector of its Python-folded text."""
    for sentence in ACCENTED:
        decomposed = unicodedata.normalize("NFKD", sentence)
        folded = "".join(c for c in decomposed if not unicodedata.combining(c))
        expected = session.execute(
            text("select to_tsvector('simple', :t)::text"), {"t": folded}
        ).scalar()
        assert vector_of(session, sentence, None, None) == expected, sentence
        # a decomposed (NFD) spelling gives the same vector as the composed one
        assert vector_of(session, unicodedata.normalize("NFD", sentence), None, None) == (
            vector_of(session, sentence, None, None)
        )


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


# ---- accents: what a person types and what is stored meet in the middle ----


def found_ids(session, user, words):
    rows = services.list_meetings(session, user, 51, None, None, None, None, words)
    return sorted(m.id for m in rows)


def test_equipe_is_found_by_equipe_and_by_equipe_with_its_accent(session, owner, template):
    hit = ready(
        session,
        template,
        title="Weekly sync",
        notes="## Summary\nL'équipe a décidé de livrer.",
        speaker_transcript="[Zoë]: tout est prêt",
    )
    ready(session, template, title="Other", notes="## Summary\nHiring only")
    user = session.get(User, owner.id)
    for typed in (
        "equipe",
        "équipe",
        "ÉQUIPE",
        unicodedata.normalize("NFD", "équipe"),
        "decide",
        "décidé",
        "pret",
        "prêt",
        "zoe",
        "Zoë",
    ):
        assert found_ids(session, user, typed) == [hit.id], typed


def test_yoruba_names_with_dots_below_are_found_with_or_without_them(session, owner, template):
    hit = ready(
        session,
        template,
        title="Planning",
        notes="## Action items\n- Ọlá and Ṣọlá will lead the launch",
        speaker_transcript="[Adéwálé Ọ̀gùn]: I agree",
    )
    ready(session, template, title="Other", notes="## Summary\nNone noted")
    user = session.get(User, owner.id)
    for typed in ("Ọlá", "ola", "Ṣọlá", "sola", "Adéwálé", "adewale", "Ọ̀gùn", "ogun"):
        assert found_ids(session, user, typed) == [hit.id], typed


def test_the_title_is_matched_without_regard_to_accents_either_way(session, owner, template):
    accented = ready(session, template, title="Équipe produit", notes="x")
    plain = ready(session, template, title="equipe design", notes="x")
    ready(session, template, title="Budget", notes="x")
    user = session.get(User, owner.id)
    assert found_ids(session, user, "equipe") == [accented.id, plain.id]
    assert found_ids(session, user, "ÉQUIPE") == [accented.id, plain.id]
    assert found_ids(session, user, "équipe produit") == [accented.id]
    assert found_ids(session, user, "%") == []  # still literal


def test_ask_candidates_find_accented_text_by_the_unaccented_question(session, owner, template):
    hit = ready(session, template, notes="## Summary\nL'équipe a décidé", speaker_transcript=None)
    ready(session, template, notes="## Summary\nHiring only")
    user = session.get(User, owner.id)
    for question in ("equipe", "équipe", "decide"):
        found, _ = ask.candidates(session, user, stems(question), None, None, False)
        assert [m.id for m in found] == [hit.id], question


def test_both_subqueries_look_only_at_meetings_the_caller_may_read(session, owner):
    from sqlalchemy import select

    from app.models import Meeting

    readable = Meeting.owner_user_id == owner.id
    sql = str(
        select(Meeting.id)
        .where(meeting_search.keyword_clause("pricing", readable))
        .compile(dialect=session.get_bind().dialect)
    )
    inner = sql.split(" IN (SELECT")[1:]
    assert len(inner) == 2, sql
    assert all("owner_user_id" in part for part in inner), sql
