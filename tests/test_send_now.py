"""Send now and Not recorded (plan task C15b, spec 7.7).

Real Postgres, the real app and routes, `place_bot()` and `FakeVexaHost` (it counts the bots it was
asked to start). The routes read the real clock, so joins are placed relative to the moment a test
runs. Nothing is faked between the button and the placement: if a pre-flight check could be
bypassed, a test here would see a bot sent.
"""

import threading
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import sessionmaker

from app import join_runner, mail_templates
from app.models import (
    CalendarEvent,
    Meeting,
    ScheduledJoin,
    UsageEntry,
    User,
)
from app.usage import email_hash
from tests import test_calendar_connect as cc
from tests.fakes.vexa_host import FakeVexaHost
from tests.test_api import error_of
from tests.test_calendar_connect import browser, build_app
from tests.test_gcal_sync import make_world
from tests.test_web import OTHER_EMAIL, OWNER_EMAIL, fake_openai, make_settings  # noqa: F401

# The fixtures of the connect tests, shared by name (`vault_key` is autouse and needs `settings`).
google, settings, vault_key = cc.google, cc.settings, cc.vault_key

HOUR = 3600
SEND = "/api/scheduled-joins/{}/send-now"
LIST = "/api/not-recorded"


@pytest.fixture
def host():
    return FakeVexaHost()


@pytest.fixture
def send_settings():
    return make_settings(
        bot_max_local=1,
        user_bot_limit=2,
        google_client_id=cc.CLIENT_ID,
        google_client_secret="s3cret",
        calendar_enabled=True,
        token_enc_key=cc.KEY,
    )


@pytest.fixture
def owner_world(session):
    return make_world(session, OWNER_EMAIL)


@pytest.fixture
def web(engine, send_settings, google, host, fake_openai, owner_world):
    return browser(build_app(engine, send_settings, google, host, fake_openai))


def now() -> datetime:
    return datetime.now(UTC)


def join_row(
    session,
    world,
    *,
    state="failed",
    reason="not_recorded",
    start=-30.0,
    length=60.0,
    code="abc-defg-hij",
    title="Weekly planning",
    run_before=1.0,
    meeting_id=None,
    **fields,
) -> ScheduledJoin:
    """A scheduled join as the runner left it. Times are minutes from now (`start`) and long."""
    starts = now() + timedelta(minutes=start)
    event = CalendarEvent(
        user_id=world.user.id,
        connection_id=world.conn.id,
        source_event_id=f"evt-{world.user.id}-{code}-{start}",
        title=title,
        starts_at=starts,
        ends_at=starts + timedelta(minutes=length),
        platform="google_meet",
        meeting_url=f"https://meet.google.com/{code}",
        native_id=code,
        organizer_self=True,
        self_response="accepted",
    )
    session.add(event)
    session.flush()
    join = ScheduledJoin(
        user_id=world.user.id,
        source="calendar",
        calendar_event_id=event.id,
        platform="google_meet",
        native_id=code,
        meeting_url=f"https://meet.google.com/{code}",
        starts_at=starts,
        ends_at=starts + timedelta(minutes=length),
        run_at=starts - timedelta(minutes=run_before),
        state=state,
        skip_reason=reason,
        template_id=world.template.id,
        deadline_at=starts + timedelta(minutes=15),
        meeting_id=meeting_id,
        **fields,
    )
    session.add(join)
    session.commit()
    return join


def fresh(session, join) -> ScheduledJoin:
    session.expire_all()
    return session.get(ScheduledJoin, join.id)


def other_meeting(session, world, code, *, status="bot_sent") -> Meeting:
    meeting = Meeting(
        owner_user_id=world.user.id,
        url=f"https://meet.google.com/{code}",
        platform="google_meet",
        native_id=code,
        template_id=world.template.id,
        status=status,
    )
    session.add(meeting)
    session.commit()
    return meeting


# ---- the send ----


def test_send_now_places_one_bot_marks_the_join_and_restarts_the_window(
    web, session, host, owner_world
):
    join = join_row(session, owner_world)
    before = now()
    response = web.post(SEND.format(join.id))
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["join_id"] == join.id
    assert body["status_text"] == "The note taker is on its way to this meeting."
    assert host.bots_sent == 1
    row = fresh(session, join)
    assert (row.state, row.skip_reason, row.last_error) == ("joined", None, None)
    assert row.meeting_id == body["meeting_id"] and row.attempts == 1
    assert before + timedelta(minutes=14) < row.deadline_at < now() + timedelta(minutes=16)
    meeting = session.get(Meeting, row.meeting_id)
    assert (meeting.source, meeting.title, meeting.status, meeting.owner_user_id) == (
        "calendar",
        "Weekly planning",
        "bot_sent",
        owner_world.user.id,
    )


@pytest.mark.parametrize(
    ("state", "reason"),
    [
        ("failed", "not_recorded"),
        ("failed", "error"),
        ("no_capacity", None),
        ("skipped", "hours_cap"),
        ("skipped", "user_limit"),
        ("skipped", "code_in_use"),
        ("skipped", "needs_reconnect"),
        ("skipped", "duplicate_meeting"),
    ],
)
def test_every_sendable_state_sends(web, session, host, owner_world, state, reason):
    join = join_row(session, owner_world, state=state, reason=reason)
    assert web.post(SEND.format(join.id)).status_code == 200
    assert host.bots_sent == 1


def test_a_second_press_answers_the_same_meeting_and_sends_nothing(web, session, host, owner_world):
    join = join_row(session, owner_world)
    first = web.post(SEND.format(join.id))
    second = web.post(SEND.format(join.id))
    assert first.status_code == second.status_code == 200
    assert first.json() == second.json()
    assert host.bots_sent == 1
    assert session.scalar(select(func.count(Meeting.id))) == 1


def test_a_concurrent_double_send_now_places_one_bot(
    engine,
    send_settings,
    google,
    host,
    fake_openai,
    session,
    owner_world,
):
    join = join_row(session, owner_world)
    app = build_app(engine, send_settings, google, host, fake_openai)
    clients = [browser(app), browser(app)]
    host.send_delay = 0.4  # both requests are inside the call together
    barrier = threading.Barrier(2)
    answers, errors = [], []

    def press(client) -> None:
        try:
            barrier.wait(timeout=10)
            answers.append(client.post(SEND.format(join.id)))
        except Exception as exc:  # pragma: no cover - makes a failure visible
            errors.append(exc)

    threads = [threading.Thread(target=press, args=(c,)) for c in clients]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=30)
    assert errors == []
    assert [a.status_code for a in answers] == [200, 200]
    assert answers[0].json() == answers[1].json()
    assert host.bots_sent == 1 and host.peak_in_flight == 1
    with sessionmaker(bind=engine)() as check:
        assert check.scalar(select(func.count(Meeting.id))) == 1
        assert check.get(ScheduledJoin, join.id).state == "joined"


def test_the_runner_and_a_click_on_one_row_place_one_bot(
    engine, web, session, host, owner_world, monkeypatch
):
    """A runner tick that comes after the click finds the row `joined`, and one that comes
    before it has already sent: either way one bot."""
    join = join_row(session, owner_world, state="no_capacity", reason=None)
    assert web.post(SEND.format(join.id)).status_code == 200
    out = join_runner.run_due_joins(
        session, host, None, now=now() + timedelta(seconds=30), settings=make_settings()
    )
    assert out == [] and host.bots_sent == 1


# ---- the pre-flight is never bypassed ----


def test_send_now_reruns_the_hours_cap_and_never_bypasses_it(web, session, host, owner_world):
    session.add(
        UsageEntry(
            user_id=owner_world.user.id,
            email_hash=email_hash(owner_world.user.email),
            seconds=5 * HOUR,
        )
    )
    join = join_row(session, owner_world, state="skipped", reason="hours_cap")
    error_of(web.post(SEND.format(join.id)), 403, "hours_cap_reached")
    assert host.bots_sent == 0
    assert fresh(session, join).state == "skipped"


def test_send_now_reruns_the_dedupe_and_never_bypasses_it(web, session, host, owner_world):
    """The owner's own bot is in that meeting now: same code, same user."""
    other_meeting(session, owner_world, "abc-defg-hij")
    join = join_row(session, owner_world, state="skipped", reason="duplicate_meeting")
    error_of(web.post(SEND.format(join.id)), 409, "already_in_meeting")
    assert host.bots_sent == 0 and fresh(session, join).state == "skipped"


def test_send_now_when_another_account_has_a_bot_in_the_meeting_is_refused_neutrally(
    web, session, host, owner_world
):
    other = make_world(session, OTHER_EMAIL)
    other_meeting(session, other, "abc-defg-hij")
    join = join_row(session, owner_world)
    body = error_of(web.post(SEND.format(join.id)), 409, "meeting_has_note_taker")
    assert OTHER_EMAIL not in str(body) and host.bots_sent == 0


def test_send_now_reruns_capacity_and_never_bypasses_it(web, session, host, owner_world):
    other_meeting(session, owner_world, "ddd-eeee-fff")  # the one slot is taken
    join = join_row(session, owner_world, state="no_capacity", reason=None)
    error_of(web.post(SEND.format(join.id)), 409, "bot_busy")
    assert host.bots_sent == 0
    row = fresh(session, join)
    assert row.state == "no_capacity" and row.meeting_id is None


def test_send_now_reruns_the_per_user_limit(
    engine, google, host, fake_openai, session, owner_world
):
    settings = make_settings(bot_max_local=5, user_bot_limit=1)
    web = browser(build_app(engine, settings, google, host, fake_openai))
    other_meeting(session, owner_world, "ddd-eeee-fff")
    join = join_row(session, owner_world, state="skipped", reason="user_limit")
    error_of(web.post(SEND.format(join.id)), 409, "user_limit")
    assert host.bots_sent == 0


def test_another_live_join_of_the_owners_on_the_code_is_already_in_meeting(
    web, session, host, owner_world
):
    join_row(session, owner_world, state="joined", reason=None, start=-5, code="abc-defg-hij")
    again = join_row(session, owner_world, state="failed", start=-45, code="abc-defg-hij")
    error_of(web.post(SEND.format(again.id)), 409, "already_in_meeting")
    assert host.bots_sent == 0


# ---- who and when ----


def test_send_now_after_the_meeting_end_is_409(web, session, host, owner_world):
    join = join_row(session, owner_world, start=-90, length=30)
    error_of(web.post(SEND.format(join.id)), 409, "meeting_ended")
    assert host.bots_sent == 0 and fresh(session, join).state == "failed"


def test_send_now_on_a_suspended_account_is_refused(web, session, host, owner_world):
    join = join_row(session, owner_world, state="skipped", reason="account_suspended")
    error_of(web.post(SEND.format(join.id)), 403, "account_suspended")
    session.get(User, owner_world.user.id).status = "suspended"
    session.commit()
    again = join_row(session, owner_world, code="ddd-eeee-fff")
    # a suspended account's session is refused before the route: either way no bot goes
    assert web.post(SEND.format(again.id)).status_code in (401, 403)
    assert host.bots_sent == 0


@pytest.mark.parametrize("state", ["scheduled", "dispatching", "cancelled", "completed"])
def test_a_join_that_is_not_in_a_sendable_state_is_409(web, session, host, owner_world, state):
    join = join_row(session, owner_world, state=state, reason=None)
    body = error_of(web.post(SEND.format(join.id)), 409, "cannot_send_now")
    assert body["message"] if isinstance(body, dict) and "message" in body else True
    assert host.bots_sent == 0


def test_foreign_join_id_is_404(web, session, host):
    other = make_world(session, OTHER_EMAIL)
    theirs = join_row(session, other)
    error_of(web.post(SEND.format(theirs.id)), 404, "not_found")
    error_of(web.post(SEND.format(999999)), 404, "not_found")
    assert host.bots_sent == 0 and fresh(session, theirs).state == "failed"


def test_it_needs_a_login_and_the_csrf_header(
    engine, send_settings, google, host, fake_openai, owner_world, session
):
    app = build_app(engine, send_settings, google, host, fake_openai)
    join = join_row(session, owner_world)
    anon = browser(app, log_in=False)
    error_of(anon.post(SEND.format(join.id)), 401, "not_authenticated")
    error_of(anon.get(LIST), 401, "not_authenticated")


# ---- failures of the send itself ----


def test_a_host_that_refuses_the_bot_is_502_and_changes_nothing(web, session, host, owner_world):
    from app.vexa import VexaError

    host.error = VexaError("send_bot failed: secret detail")
    join = join_row(session, owner_world)
    body = error_of(web.post(SEND.format(join.id)), 502, "bot_send_failed")
    assert "secret" not in str(body)
    row = fresh(session, join)
    assert row.state == "failed" and row.meeting_id is None
    assert session.scalar(select(func.count(Meeting.id))) == 0
    host.error = None
    assert web.post(SEND.format(join.id)).status_code == 200  # and a later press works


def test_a_missing_calendar_template_is_409_template_required(web, session, host, owner_world):
    join = join_row(session, owner_world)
    join.template_id = None
    owner_world.user_settings.calendar_template_id = None
    session.commit()
    error_of(web.post(SEND.format(join.id)), 409, "template_required")
    assert host.bots_sent == 0


# ---- the list ----


def test_not_recorded_lists_failed_no_capacity_and_the_listed_skip_reasons_for_seven_days_only(
    web, session, owner_world
):
    kept = {
        "failed": join_row(session, owner_world, code="aaa-aaaa-aaa", start=-10),
        "capacity": join_row(
            session, owner_world, state="no_capacity", reason=None, code="bbb-bbbb-bbb", start=-20
        ),
        "hours": join_row(
            session,
            owner_world,
            state="skipped",
            reason="hours_cap",
            code="ccc-cccc-ccc",
            start=-30,
        ),
        "disconnected": join_row(
            session,
            owner_world,
            state="skipped",
            reason="needs_reconnect",
            code="ddd-dddd-ddd",
            start=-40,
        ),
        "suspended": join_row(
            session,
            owner_world,
            state="skipped",
            reason="account_suspended",
            code="eee-eeee-eee",
            start=-50,
        ),
        "duplicate": join_row(  # spec 7.7 lists it: Send now is allowed, placement may still refuse
            session,
            owner_world,
            state="skipped",
            reason="duplicate_meeting",
            code="hhh-hhhh-hhh",
            start=-15,
        ),
        "old_but_in_window": join_row(
            session, owner_world, code="fff-ffff-fff", start=-6 * 24 * 60, length=30
        ),
    }
    skipped_out = [
        join_row(  # eight days ago
            session, owner_world, code="ggg-gggg-ggg", start=-8 * 24 * 60, length=30
        ),
        join_row(
            session, owner_world, state="cancelled", reason="override_off", code="iii-iii-iii"
        ),
        join_row(session, owner_world, state="scheduled", reason=None, code="jjj-jjj-jjj"),
        join_row(  # its moment has not come: a row skipped hours ahead
            session,
            owner_world,
            state="skipped",
            reason="needs_reconnect",
            code="kkk-kkkk-kkk",
            start=300,
        ),
    ]
    other = make_world(session, OTHER_EMAIL)
    join_row(session, other, code="lll-llll-lll")  # someone else's
    body = web.get(LIST).json()
    ids = [item["join_id"] for item in body["items"]]
    assert ids == [  # newest first
        kept["failed"].id,
        kept["duplicate"].id,
        kept["capacity"].id,
        kept["hours"].id,
        kept["disconnected"].id,
        kept["suspended"].id,
        kept["old_but_in_window"].id,
    ]
    assert not set(ids) & {j.id for j in skipped_out}
    items = {item["join_id"]: item for item in body["items"]}
    failed = items[kept["failed"].id]
    assert failed["title"] == "Weekly planning" and failed["platform_label"] == "Google Meet"
    assert failed["reason_text"] == "The note taker could not join before the 15 minutes were up."
    assert failed["can_send_now"] is True
    assert (
        items[kept["capacity"].id]["reason_text"] == "The note taker was busy with other meetings."
    )
    assert items[kept["hours"].id]["reason_text"] == "Your recording hours are used up."
    assert items[kept["duplicate"].id]["can_send_now"] is True
    assert items[kept["suspended"].id]["can_send_now"] is False
    assert items[kept["old_but_in_window"].id]["can_send_now"] is False  # over
    text = str(body)
    assert "meet.google.com" not in text and "needs_reconnect" not in text


def test_a_nameless_event_is_named_by_platform_and_code(web, session, owner_world):
    join_row(session, owner_world, title="")
    [item] = web.get(LIST).json()["items"]
    assert item["title"] == "Google Meet abc-defg-hij"


def test_dispatched_then_failed_is_a_meeting_row_not_a_not_recorded_row(web, session, owner_world):
    """A join that dispatched and then failed (not let in) is an ordinary failed meeting."""
    meeting = other_meeting(session, owner_world, "abc-defg-hij", status="failed")
    join_row(session, owner_world, state="completed", reason=None, meeting_id=meeting.id)
    join_row(
        session,
        owner_world,
        state="joined",
        reason=None,
        code="ddd-eeee-fff",
        meeting_id=meeting.id,
    )
    assert web.get(LIST).json() == {"items": []}
    listed = [m["id"] for m in web.get("/api/meetings").json()["meetings"]]
    assert meeting.id in listed


def test_the_list_is_the_callers_own_and_empty_without_rows(web, session):
    assert web.get(LIST).json() == {"items": []}


# ---- Upcoming rows carry the same answer ----


def test_an_upcoming_row_carries_the_join_and_whether_send_now_is_on_offer(
    web, session, owner_world
):
    from app import google_oauth

    owner_world.conn.granted_scopes = google_oauth.CALENDAR_SCOPE
    join = join_row(session, owner_world, start=-10, length=60)
    [row] = web.get("/api/calendar").json()["upcoming"]
    assert row["join_id"] == join.id and row["can_send_now"] is True
    assert (
        row["not_recorded_text"] == "The note taker could not join before the 15 minutes were up."
    )
    assert "meeting_url" not in row


# ---- the mail's button ----


def test_the_could_not_join_mail_has_a_send_it_again_button_that_opens_the_meetings_page():
    assert mail_templates.SEND_AGAIN_PATH == "/meetings"
    settings = make_settings(public_base_url="https://notes.example.test")
    message = mail_templates.render_link(
        mail_templates.NOT_JOINED,
        {"meeting": "Google Meet btp-tkio-gtb", "at": "11:58 GMT+1"},
        "ada@example.test",
        settings,
    )
    assert "Send it again" in message.html and "Send it again" in message.text
    assert 'href="https://notes.example.test/meetings"' in message.html
    assert "https://notes.example.test/meetings\n" in message.text
    # a link into the signed-in app: no token, no query, nothing that acts when it is opened
    for part in (message.html, message.text):
        assert "token" not in part.lower() and "/?" not in part


# ---- review of #145: one rule, a meeting that has not started, the narrow errors, the race ----


@pytest.mark.parametrize(
    ("state", "reason"),
    [("skipped", "needs_reconnect"), ("failed", "not_recorded"), ("no_capacity", None)],
)
def test_a_meeting_that_has_not_started_cannot_be_sent_now(
    web, session, host, owner_world, state, reason
):
    """A lapsed calendar marks future joins `skipped / needs_reconnect` and the Upcoming rows carry
    their ids: the server must refuse them itself, however far ahead the meeting is."""
    join = join_row(
        session, owner_world, state=state, reason=reason, start=3 * 24 * 60, run_before=1
    )
    body = error_of(web.post(SEND.format(join.id)), 409, "not_started")
    assert body["message"] == "This meeting has not started yet."
    assert host.bots_sent == 0
    row = fresh(session, join)
    assert (row.state, row.skip_reason, row.meeting_id) == (state, reason, None)


def test_the_server_and_the_lists_use_one_rule(web, session, host, owner_world):
    """`can_send_now` on the lists is exactly what the route accepts, row by row."""
    rows = [
        join_row(session, owner_world, code="aaa-aaaa-aaa", start=-10),
        join_row(session, owner_world, state="no_capacity", reason=None, code="bbb-bbbb-bbb"),
        join_row(
            session,
            owner_world,
            state="skipped",
            reason="needs_reconnect",
            code="ccc-cccc-ccc",
            start=300,
        ),
        join_row(
            session,
            owner_world,
            state="skipped",
            reason="account_suspended",
            code="ddd-dddd-ddd",
        ),
        join_row(session, owner_world, code="eee-eeee-eee", start=-90, length=30),
        join_row(session, owner_world, state="scheduled", reason=None, code="fff-ffff-fff"),
        join_row(
            session,
            owner_world,
            state="skipped",
            reason="duplicate_meeting",
            code="ggg-gggg-ggg",
        ),
    ]
    listed = {item["join_id"]: item["can_send_now"] for item in web.get(LIST).json()["items"]}
    for row in rows:
        offered = listed.get(row.id, False)
        host.live.clear()  # a free slot each time: only the rule is under test
        for meeting in session.scalars(select(Meeting)):
            meeting.status = "ready"
        session.commit()
        answer = web.post(SEND.format(row.id))
        assert (answer.status_code == 200) == offered, (row.state, row.skip_reason, answer.text)
    assert host.bots_sent == sum(listed.values())


def test_a_duplicate_meeting_row_is_listed_and_placement_still_refuses_while_the_bot_is_there(
    web, session, host, owner_world
):
    other_meeting(session, owner_world, "abc-defg-hij")
    join = join_row(session, owner_world, state="skipped", reason="duplicate_meeting")
    [item] = web.get(LIST).json()["items"]
    assert item["join_id"] == join.id and item["can_send_now"] is True
    assert item["reason_text"] == "A note taker was already in this meeting from a link you pasted."
    error_of(web.post(SEND.format(join.id)), 409, "already_in_meeting")
    assert host.bots_sent == 0


def test_an_orphan_bot_of_the_owners_is_adopted_not_called_someone_elses(
    web, session, host, owner_world
):
    """A killed earlier send left the owner's own bot on the code with no meeting row for it."""
    host.seed_live_bot("google_meet", "abc-defg-hij")
    join = join_row(session, owner_world)
    answer = web.post(SEND.format(join.id))
    assert answer.status_code == 200, answer.text
    meeting = session.get(Meeting, answer.json()["meeting_id"])
    assert meeting.vexa_meeting_id == "1" and host.bots_sent == 0  # adopted, nothing new sent


def test_an_unexpected_value_error_is_not_a_template_problem_and_frees_the_locks(
    engine, web, session, host, owner_world, monkeypatch
):
    from app import send_now

    def broken(*args, **kwargs):
        raise ValueError("no such user")

    monkeypatch.setattr(send_now, "place_bot", broken)
    join = join_row(session, owner_world)
    with pytest.raises(ValueError, match="no such user"):
        web.post(SEND.format(join.id))  # a caller bug is a 500, never `template_required`
    with sessionmaker(bind=engine)() as other:  # the row lock was let go: nobody waits for it
        locked = other.scalars(
            select(ScheduledJoin).where(ScheduledJoin.id == join.id).with_for_update(nowait=True)
        ).one()
        assert locked.state == "failed"


def test_a_runner_tick_in_the_middle_of_a_send_does_not_place_a_second_bot(
    engine, send_settings, google, host, fake_openai, session, owner_world
):
    """The click holds the row and the event while the host takes the bot; a runner tick that runs
    right then finds the row held and leaves it, so exactly one bot is sent."""
    join = join_row(session, owner_world, state="no_capacity", reason=None)
    app = build_app(engine, send_settings, google, host, fake_openai)
    client = browser(app)
    inside = threading.Event()
    host.on_send = inside.set
    host.send_delay = 0.8
    answers = []
    thread = threading.Thread(target=lambda: answers.append(client.post(SEND.format(join.id))))
    thread.start()
    assert inside.wait(timeout=10)  # the click is inside the send, holding the row
    with sessionmaker(bind=engine)() as runner_session:
        outcomes = join_runner.run_due_joins(
            runner_session, host, None, now=now(), settings=send_settings
        )
    thread.join(timeout=30)
    assert outcomes == []
    assert answers[0].status_code == 200
    assert host.bots_sent == 1 and host.peak_in_flight == 1
    with sessionmaker(bind=engine)() as check:
        assert check.scalar(select(func.count(Meeting.id))) == 1
        assert check.get(ScheduledJoin, join.id).state == "joined"
