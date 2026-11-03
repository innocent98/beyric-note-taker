import pytest
from sqlalchemy.exc import IntegrityError

from app.models import Meeting, Template, VocabularyTerm
from tests.test_web import make_user


def make_template(session) -> Template:
    owner = make_user(session)
    template = Template(
        owner_user_id=owner.id, name="General", instructions="Summarise the meeting."
    )
    session.add(template)
    session.commit()
    return template


def test_new_meeting_defaults(session):
    template = make_template(session)
    m = Meeting(
        owner_user_id=template.owner_user_id,
        url="https://meet.google.com/abc-defg-hij",
        platform="google_meet",
        native_id="abc-defg-hij",
        template_id=template.id,
    )
    session.add(m)
    session.commit()
    assert m.status == "bot_sent"
    assert m.step == "waiting"
    assert m.failure_reason is None
    assert m.created_at is not None
    assert m.transcript is None
    assert m.notes is None
    assert m.audio_deleted_at is None


def test_meeting_rejects_unknown_status(session):
    template = make_template(session)
    session.add(
        Meeting(
            owner_user_id=template.owner_user_id,
            url="u",
            platform="zoom",
            native_id="1",
            template_id=template.id,
            status="bogus",
        )
    )
    with pytest.raises(IntegrityError):
        session.commit()


def test_meeting_rejects_unknown_step(session):
    template = make_template(session)
    session.add(
        Meeting(
            owner_user_id=template.owner_user_id,
            url="u",
            platform="zoom",
            native_id="1",
            template_id=template.id,
            step="bogus",
        )
    )
    with pytest.raises(IntegrityError):
        session.commit()


def test_vocabulary_term_is_unique_per_owner(session):
    owner, other = make_user(session), make_user(session, "other@example.test")
    session.add(VocabularyTerm(owner_user_id=owner.id, term="Beyric"))
    session.commit()
    session.add(VocabularyTerm(owner_user_id=other.id, term="Beyric"))  # another owner: fine
    session.commit()
    session.add(VocabularyTerm(owner_user_id=owner.id, term="Beyric"))
    with pytest.raises(IntegrityError):
        session.commit()


def test_get_session_yields_working_session(monkeypatch, engine):
    import app.db as db

    monkeypatch.setattr(db, "SessionLocal", db.make_session_factory(engine))
    gen = db.get_session()
    s = next(gen)
    assert s.execute(db.text("select 1")).scalar() == 1
    gen.close()
