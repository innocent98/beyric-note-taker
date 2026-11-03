from datetime import UTC, datetime

import pytest
from sqlalchemy.exc import IntegrityError

from app.models import Meeting, ScheduledJoin, Template, VocabularyTerm
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


# ---- calendar connections, events and scheduled joins (migration 0010) ----


def _calendar_fixture(session):
    from app.models import CalendarConnection, CalendarEvent

    user = make_user(session)
    conn = CalendarConnection(user_id=user.id, provider="google")
    session.add(conn)
    session.commit()
    event = CalendarEvent(
        user_id=user.id,
        connection_id=conn.id,
        source_event_id="evt-1",
        title="Planning",
        starts_at=datetime(2026, 11, 4, 10, 0, tzinfo=UTC),
        ends_at=datetime(2026, 11, 4, 11, 0, tzinfo=UTC),
        status="confirmed",
    )
    session.add(event)
    session.commit()
    return user, conn, event


def _scheduled_join(user, event=None, **fields):
    from app.models import ScheduledJoin

    values = {
        "user_id": user.id,
        "source": "calendar",
        "calendar_event_id": event.id if event else None,
        "platform": "google_meet",
        "native_id": "abc-defg-hij",
        "meeting_url": "https://meet.google.com/abc-defg-hij",
        "starts_at": datetime(2026, 11, 4, 10, 0, tzinfo=UTC),
        "ends_at": datetime(2026, 11, 4, 11, 0, tzinfo=UTC),
        "run_at": datetime(2026, 11, 4, 9, 59, tzinfo=UTC),
        "deadline_at": datetime(2026, 11, 4, 10, 15, tzinfo=UTC),
        **fields,
    }
    return ScheduledJoin(**values)


def test_new_calendar_rows_get_their_defaults(session):
    user, conn, event = _calendar_fixture(session)
    assert (conn.status, conn.meet_subscription_state) == ("connected", "none")
    assert (conn.refresh_token_enc, conn.sync_token, conn.last_sync_at) == (None, None, None)
    assert (event.attendee_count, event.all_day, event.event_type) == (0, False, "default")
    assert event.override is None and event.synced_at is not None

    join = _scheduled_join(user, event)
    session.add(join)
    session.commit()
    assert (join.state, join.attempts) == ("scheduled", 0)
    assert join.rechecked_at is None and join.meeting_id is None
    assert join.created_at is not None and join.updated_at is not None


def test_connection_unique_per_user_and_provider(session):
    from app.models import CalendarConnection

    user, _, _ = _calendar_fixture(session)
    other = make_user(session, "other@example.test")
    session.add(CalendarConnection(user_id=other.id, provider="google"))  # another user: fine
    session.commit()
    session.add(CalendarConnection(user_id=user.id, provider="ics"))  # another provider: fine
    session.commit()
    session.add(CalendarConnection(user_id=user.id, provider="google"))
    with pytest.raises(IntegrityError):
        session.commit()


def test_connection_rejects_unknown_provider_status_and_reason(session):
    from app.models import CalendarConnection

    user = make_user(session)
    for fields in (
        {"provider": "outlook"},
        {"provider": "google", "status": "bogus"},
        {"provider": "google", "needs_reconnect_reason": "bogus"},
        {"provider": "google", "meet_subscription_state": "bogus"},
    ):
        session.add(CalendarConnection(user_id=user.id, **fields))
        with pytest.raises(IntegrityError):
            session.commit()
        session.rollback()


def test_event_unique_per_connection_and_source_event_id(session):
    from app.models import CalendarEvent

    user, conn, event = _calendar_fixture(session)
    session.add(
        CalendarEvent(
            user_id=user.id,
            connection_id=conn.id,
            source_event_id=event.source_event_id,
            title="Duplicate",
            starts_at=event.starts_at,
            ends_at=event.ends_at,
            status="confirmed",
        )
    )
    with pytest.raises(IntegrityError):
        session.commit()


def test_event_rejects_unknown_platform_status_and_response(session):
    from app.models import CalendarEvent

    user, conn, event = _calendar_fixture(session)
    for n, fields in enumerate(
        ({"platform": "webex"}, {"status": "bogus"}, {"self_response": "bogus"})
    ):
        values = {"status": "confirmed", **fields}
        session.add(
            CalendarEvent(
                user_id=user.id,
                connection_id=conn.id,
                source_event_id=f"bad-{n}",
                title="x",
                starts_at=event.starts_at,
                ends_at=event.ends_at,
                **values,
            )
        )
        with pytest.raises(IntegrityError):
            session.commit()
        session.rollback()


def test_scheduled_join_state_check_rejects_unknown_state(session):
    user, _, event = _calendar_fixture(session)
    session.add(_scheduled_join(user, event, state="bogus"))
    with pytest.raises(IntegrityError):
        session.commit()


def test_scheduled_join_rejects_unknown_source_and_platform(session):
    user, _, _ = _calendar_fixture(session)
    for fields in ({"source": "bogus"}, {"platform": "webex"}):
        session.add(_scheduled_join(user, **fields))
        with pytest.raises(IntegrityError):
            session.commit()
        session.rollback()


def test_scheduled_join_one_row_per_event_per_user(session):
    user, _, event = _calendar_fixture(session)
    session.add(_scheduled_join(user, event))
    session.commit()
    session.add(_scheduled_join(user, event, native_id="xyz-wxyz-xyz"))
    with pytest.raises(IntegrityError):
        session.commit()


def test_instant_joins_without_an_event_do_not_collide_on_the_event_index(session):
    user = make_user(session)
    session.add(_scheduled_join(user, source="instant_meet", native_id="aaa-bbbb-ccc"))
    session.add(_scheduled_join(user, source="instant_meet", native_id="ddd-eeee-fff"))
    session.commit()  # two null calendar_event_id values are distinct


def test_live_join_unique_per_meeting_code(session):
    user = make_user(session)
    session.add(_scheduled_join(user, state="joined"))
    session.commit()
    for state in ("joined", "dispatching", "no_capacity"):  # every live state holds the code
        session.add(_scheduled_join(user, state=state))
        with pytest.raises(IntegrityError):
            session.commit()
        session.rollback()
    # A finished or waiting join does not hold the code: a recurring Meet code is free again.
    session.add(_scheduled_join(user, state="completed"))
    session.add(_scheduled_join(user, state="scheduled", source="instant_meet"))
    session.commit()


def test_one_users_live_join_never_blocks_another_users_join_of_the_same_link(session):
    # The index is per user: registering someone else's link first cannot lock them out. Two users
    # in one meeting each get a join until C12's dispatcher decides how sharing works.
    user = make_user(session)
    other = make_user(session, "other@example.test")
    session.add(_scheduled_join(user, state="joined"))
    session.commit()
    for state in ("joined", "dispatching", "no_capacity"):
        session.add(_scheduled_join(other, state=state))
        session.commit()
        session.query(ScheduledJoin).filter_by(user_id=other.id).delete()
        session.commit()


def test_deleting_a_user_removes_their_calendar_rows_and_keeps_meetings(session):
    from app.models import CalendarConnection, CalendarEvent, ScheduledJoin

    user, _, event = _calendar_fixture(session)
    session.add(_scheduled_join(user, event))
    session.commit()
    session.delete(user)
    session.commit()
    for model in (CalendarConnection, CalendarEvent, ScheduledJoin):
        assert session.query(model).count() == 0


def test_deleting_an_event_keeps_its_join_and_deleting_a_meeting_unlinks_it(session):
    user, _, event = _calendar_fixture(session)
    template = Template(owner_user_id=user.id, name="General", instructions="x")
    session.add(template)
    session.commit()
    meeting = Meeting(
        owner_user_id=user.id,
        url="https://meet.google.com/abc-defg-hij",
        platform="google_meet",
        native_id="abc-defg-hij",
        template_id=template.id,
    )
    session.add(meeting)
    session.commit()
    join = _scheduled_join(user, event, meeting_id=meeting.id, template_id=template.id)
    session.add(join)
    session.commit()

    session.delete(event)  # the 2-day event sweep: the join row stays for "Not recorded"
    session.commit()
    session.refresh(join)
    assert join.calendar_event_id is None
    session.delete(meeting)
    session.delete(template)
    session.commit()
    session.refresh(join)
    assert join.meeting_id is None and join.template_id is None
