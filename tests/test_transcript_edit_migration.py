"""Migration 0058: the transcript editor's columns on `meetings`. Runs the real migration on the
scratch database, like the other migration tests."""

from alembic import command
from alembic.script import ScriptDirectory
from sqlalchemy import create_engine, inspect, text

from tests import test_migrations as migrations

alembic_config = migrations.alembic_config
owner_env = migrations.owner_env
scratch_url = migrations.scratch_url

NEW = (
    "transcript_original",
    "transcript_edited_at",
    "transcript_edited_by_user_id",
    "notes_source_version",
)


def columns(engine) -> dict:
    return {c["name"]: c for c in inspect(engine).get_columns("meetings")}


def test_0058_is_the_only_head_and_follows_0057(scratch_url):
    script = ScriptDirectory.from_config(alembic_config(scratch_url))
    assert len(script.get_heads()) == 1
    assert script.get_revision("0058").down_revision == "0057"


def test_0058_adds_the_columns_untouched_for_every_meeting_and_the_round_trip_removes_them(
    scratch_url,
):
    cfg = alembic_config(scratch_url)
    engine = create_engine(scratch_url)
    command.upgrade(cfg, "0057")
    assert not set(NEW) & set(columns(engine))
    with engine.begin() as conn:
        conn.execute(
            text(
                "insert into users (email, password_hash, status) "
                "values ('old@example.test', 'x', 'active')"
            )
        )
        uid = conn.execute(text("select id from users where email = 'old@example.test'")).scalar()
        tid = conn.execute(
            text(
                "insert into templates (owner_user_id, name, instructions) "
                "values (:u, 't', 'i') returning id"
            ),
            {"u": uid},
        ).scalar()
        conn.execute(
            text(
                "insert into meetings (owner_user_id, url, platform, native_id, template_id, "
                "status, step, transcript, speaker_transcript) values "
                "(:u, 'https://meet.google.com/abc-defg-hij', 'google_meet', 'abc-defg-hij', :t, "
                "'ready', 'done', 'plain', '[Ada]: hi')"
            ),
            {"u": uid, "t": tid},
        )

    command.upgrade(cfg, "0058")
    cols = columns(engine)
    assert all(name in cols and cols[name]["nullable"] for name in NEW)
    with engine.connect() as conn:
        row = conn.execute(
            text(
                "select transcript_original, transcript_edited_at, transcript_edited_by_user_id, "
                "notes_source_version, speaker_transcript from meetings"
            )
        ).one()
    assert tuple(row) == (None, None, None, None, "[Ada]: hi")  # nothing changes for old rows
    foreign = inspect(engine).get_foreign_keys("meetings")
    edited_by = [f for f in foreign if f["constrained_columns"] == ["transcript_edited_by_user_id"]]
    assert edited_by and edited_by[0]["options"].get("ondelete") == "SET NULL"
    with engine.begin() as conn:  # an account's deletion leaves the edit, without the person
        editor = conn.execute(
            text(
                "insert into users (email, password_hash, status) "
                "values ('editor@example.test', 'x', 'active') returning id"
            )
        ).scalar()
        conn.execute(text("update meetings set transcript_edited_by_user_id = :u"), {"u": editor})
    with engine.begin() as conn:
        conn.execute(text("delete from users where id = :u"), {"u": editor})
    with engine.connect() as conn:
        assert (
            conn.execute(text("select transcript_edited_by_user_id from meetings")).scalar() is None
        )

    command.downgrade(cfg, "0057")
    assert not set(NEW) & set(columns(engine))
    command.upgrade(cfg, "head")
    assert set(NEW) <= set(columns(engine))
    engine.dispose()
