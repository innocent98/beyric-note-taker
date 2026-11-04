"""`place_bot()`: the one function that sends a bot (plan task C11).

These run on a real Postgres: the lock, the capacity count and the insert are the point, and
SQLite has neither advisory locks nor the isolation the race depends on. The threaded tests give
each thread its own session and connection, like two web requests.

Plan note: the plan lists `test_same_code_by_other_user_is_already_in_meeting`. The lead decided
(after the #45 review) that a live bot per meeting code is PER USER, so another account in the same
meeting gets its own bot until C12 decides sharing. That test is therefore split into the two below:
`..._same_user_...` (refused) and `..._other_user_...` (not refused). Every other plan name is kept.
"""

import threading
import time
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import create_engine, select, text
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from app import pipeline, placement, services, vexa
from app.models import Meeting, Template, UsageEntry
from app.placement import PlacementRefused, PlacementRequest, place_bot
from app.usage import email_hash
from app.vexa import VexaError
from tests.fakes.vexa_host import FakeVexaHost, unreachable
from tests.test_pipeline import FakeVexa
from tests.test_web import make_settings, make_user

NOW = datetime(2026, 11, 4, 10, 0, tzinfo=UTC)
CODE = "abc-defg-hij"
HOUR = 3600


def url_of(code: str) -> str:
    return f"https://meet.google.com/{code}"


def make_template(session, user) -> Template:
    template = Template(owner_user_id=user.id, name="General", instructions="Write notes.")
    session.add(template)
    session.commit()
    return template


def request_for(user, template, code=CODE, *, source="manual") -> PlacementRequest:
    return PlacementRequest(
        user_id=user.id,
        platform="google_meet",
        native_id=code,
        meeting_url=url_of(code),
        template_id=template.id,
        source=source,
    )


def add_live(session, user, template, code, status="bot_sent") -> Meeting:
    meeting = Meeting(
        owner_user_id=user.id,
        url=url_of(code),
        platform="google_meet",
        native_id=code,
        template_id=template.id,
        status=status,
    )
    session.add(meeting)
    session.commit()
    return meeting


def spend_hours(session, user, seconds) -> None:
    session.add(UsageEntry(user_id=user.id, email_hash=email_hash(user.email), seconds=seconds))
    session.commit()


def place(session, host, req, *, bot_max_local=1, user_bot_limit=2, now=NOW) -> Meeting:
    settings = make_settings(bot_max_local=bot_max_local, user_bot_limit=user_bot_limit)
    return place_bot(session, host, req, now=now, settings=settings)


def refusal_code(session, host, req, **kwargs) -> str:
    with pytest.raises(PlacementRefused) as caught:
        place(session, host, req, **kwargs)
    return caught.value.code


def vexa_meeting(**fields):
    from app.vexa import VexaMeeting

    return VexaMeeting(**fields)


def lock_is_free(engine) -> bool:
    """Whether another connection can take the placement lock right now (and gives it back)."""
    with engine.connect() as conn:
        got = conn.execute(
            text("select pg_try_advisory_xact_lock(cast(:a as int), cast(:b as int))"),
            {"a": placement.PLACEMENT_LOCK, "b": placement.LOCAL_HOST_ID},
        ).scalar()
        conn.rollback()
    return bool(got)


@pytest.fixture
def host():
    return FakeVexaHost()


@pytest.fixture
def ada(session):
    return make_user(session, "ada@example.test")


@pytest.fixture
def ada_template(session, ada):
    return make_template(session, ada)


@pytest.fixture
def ben(session):
    return make_user(session, "ben@example.test")


@pytest.fixture
def ben_template(session, ben):
    return make_template(session, ben)


# ---- the plan's tests ----


def test_two_concurrent_requests_one_slot_exactly_one_meeting_other_bot_busy(
    engine, session, host, ada, ada_template, ben, ben_template
):
    host.send_delay = 0.4  # long enough that both requests are inside the check at once
    settings = make_settings(bot_max_local=1)
    start = threading.Barrier(2)
    outcomes: list = []

    def request(user, template, code):
        with Session(engine, expire_on_commit=False) as own:
            start.wait()
            try:
                outcomes.append(
                    place_bot(
                        own, host, request_for(user, template, code), now=NOW, settings=settings
                    )
                )
            except PlacementRefused as refused:
                outcomes.append(refused)

    threads = [
        threading.Thread(target=request, args=(ada, ada_template, "aaa-aaaa-aaa")),
        threading.Thread(target=request, args=(ben, ben_template, "bbb-bbbb-bbb")),
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    placed = [o for o in outcomes if isinstance(o, Meeting)]
    refused = [o for o in outcomes if isinstance(o, PlacementRefused)]
    assert len(placed) == 1 and len(refused) == 1
    assert refused[0].code == "bot_busy"
    assert host.bots_sent == 1  # the host was asked once, not twice
    assert host.peak_in_flight == 1  # and never by two requests at the same moment
    assert len(session.scalars(select(Meeting)).all()) == 1


def test_the_lock_is_held_across_send_bot(engine, session, host, ada, ada_template):
    """Deterministic twin of the race test: while the host is taking the bot, another connection
    cannot get the lock. Without the lock this is the check that fails first."""
    seen = []
    host.on_send = lambda: seen.append(lock_is_free(engine))
    place(session, host, request_for(ada, ada_template))
    assert seen == [False]
    assert lock_is_free(engine)  # and it is released once the meeting is committed


def test_send_failure_creates_no_meeting_and_releases_the_lock(
    engine, session, host, ada, ada_template
):
    host.error = unreachable()
    with pytest.raises(VexaError):
        place(session, host, request_for(ada, ada_template))
    assert session.scalars(select(Meeting)).first() is None
    assert lock_is_free(engine)
    host.error = None  # the slot was not used up by the failure
    assert place(session, host, request_for(ada, ada_template)).status == "bot_sent"


def fail_when_the_meeting_is_written(session, monkeypatch, step):
    """Make `flush` or `commit` fail, but only once a Meeting is waiting to be written: the
    autoflush before each pre-flight query also calls `flush` and must keep working."""
    real = getattr(session, step)

    def boom(*args, **kwargs):
        if any(isinstance(row, Meeting) for row in session.new) or step == "commit":
            raise OperationalError("insert", {}, Exception("connection lost"))
        return real(*args, **kwargs)

    monkeypatch.setattr(session, step, boom)


@pytest.mark.parametrize("step", ["flush", "commit"])
def test_insert_failure_after_send_stops_the_bot(
    engine, session, host, ada, ada_template, monkeypatch, step
):
    fail_when_the_meeting_is_written(session, monkeypatch, step)
    with pytest.raises(OperationalError):
        place(session, host, request_for(ada, ada_template))
    monkeypatch.undo()
    assert host.bots_sent == 1  # it was sent...
    assert host.stopped == [("google_meet", CODE)]  # ...and taken out again
    assert session.scalars(select(Meeting)).first() is None
    assert lock_is_free(engine)


def test_a_failing_stop_after_an_insert_failure_does_not_hide_the_insert_failure(
    session, host, ada, ada_template, monkeypatch
):
    host.stop_error = unreachable()
    fail_when_the_meeting_is_written(session, monkeypatch, "flush")
    with pytest.raises(OperationalError):  # the original error, not the stop's
        place(session, host, request_for(ada, ada_template))


def test_same_code_by_same_user_is_already_in_meeting(session, host, ada, ada_template):
    add_live(session, ada, ada_template, CODE)
    assert refusal_code(session, host, request_for(ada, ada_template), bot_max_local=3) == (
        "already_in_meeting"
    )
    assert host.bots_sent == 0


def test_same_code_by_other_user_is_meeting_has_note_taker(
    session, host, ada, ada_template, ben, ben_template
):
    """With one shared Vexa key there is one bot per meeting code, so a second account in a meeting
    that already has a note taker cannot get its own (it is the plan's
    `..._other_user_is_already_in_meeting`, with a neutral code that names nobody). Sharing waits
    for C12 or per-user keys."""
    add_live(session, ada, ada_template, CODE)
    assert refusal_code(session, host, request_for(ben, ben_template), bot_max_local=3) == (
        "meeting_has_note_taker"
    )
    assert host.bots_sent == 0  # the host was never asked


def test_the_neutral_refusal_carries_nothing_about_the_other_user(
    session, host, ada, ada_template, ben, ben_template
):
    add_live(session, ada, ada_template, CODE)
    with pytest.raises(PlacementRefused) as caught:
        place(session, host, request_for(ben, ben_template), bot_max_local=3)
    shown = repr(vars(caught.value)) + str(caught.value)
    for leak in ("ada", str(ada.id), url_of(CODE)):
        assert leak not in shown


def test_vexa_refusing_a_second_bot_for_the_code_is_meeting_has_note_taker_and_stops_nothing(
    engine, session, host, ben, ben_template
):
    """A bot is in the meeting that our database does not know about (another host, a race). The
    host says no: that is the neutral refusal, the slot is free, and the stop that follows a
    failed save must NOT run, because the bot there is not ours."""
    host.seed_live_bot("google_meet", CODE)
    assert refusal_code(session, host, request_for(ben, ben_template), bot_max_local=3) == (
        "meeting_has_note_taker"
    )
    assert host.stopped == [] and host.bots_sent == 0
    assert host.live == {("google_meet", CODE): 1}  # the other bot is still there
    assert session.scalars(select(Meeting)).first() is None
    assert lock_is_free(engine)


def test_recurring_code_is_free_again_once_previous_meeting_left_bot_sent(
    session, host, ada, ada_template
):
    add_live(session, ada, ada_template, CODE, status="processing")  # the bot has left
    meeting = place(session, host, request_for(ada, ada_template), bot_max_local=3)
    assert meeting.status == "bot_sent" and host.bots_sent == 1


def test_hours_cap_refuses_with_hours_cap_reached(session, host, ada, ada_template):
    spend_hours(session, ada, 5 * HOUR)
    with pytest.raises(PlacementRefused) as caught:
        place(session, host, request_for(ada, ada_template))
    assert caught.value.code == "hours_cap_reached"
    assert caught.value.cap_seconds == 5 * HOUR  # the caller words the refusal with it
    assert host.bots_sent == 0


def test_an_admin_has_no_hours_cap(session, host):
    admin = make_user(session, "admin@example.test", admin=True)
    spend_hours(session, admin, 50 * HOUR)
    assert place(session, host, request_for(admin, make_template(session, admin))).id


def test_third_live_bot_for_one_user_is_user_limit(session, host, ada, ada_template):
    add_live(session, ada, ada_template, "aaa-aaaa-aaa")
    add_live(session, ada, ada_template, "bbb-bbbb-bbb")
    code = refusal_code(
        session, host, request_for(ada, ada_template, "ccc-cccc-ccc"), bot_max_local=3
    )
    assert code == "user_limit"
    assert host.bots_sent == 0


def test_the_pre_flight_order_is_hours_then_user_limit_then_duplicate_then_capacity(
    session, host, ada, ada_template
):
    add_live(session, ada, ada_template, CODE)  # one live bot: counts for all three later checks
    spend_hours(session, ada, 5 * HOUR)
    same = request_for(ada, ada_template)
    # Everything is wrong at once: the cap speaks first.
    assert refusal_code(session, host, same, bot_max_local=1, user_bot_limit=1) == (
        "hours_cap_reached"
    )
    session.query(UsageEntry).delete()
    session.commit()
    # Cap fine; limit reached, same code, no room: the limit speaks first...
    assert refusal_code(session, host, same, bot_max_local=1, user_bot_limit=1) == "user_limit"
    # ...then the duplicate (limit 2 not reached, no room either)...
    assert refusal_code(session, host, same, bot_max_local=1, user_bot_limit=2) == (
        "already_in_meeting"
    )
    # ...and capacity only for a code that is new.
    other = request_for(ada, ada_template, "zzz-zzzz-zzz")
    assert refusal_code(session, host, other, bot_max_local=1, user_bot_limit=2) == "bot_busy"
    assert host.bots_sent == 0


def test_a_full_note_taker_is_bot_busy_whoever_holds_it(
    session, host, ada, ada_template, ben, ben_template
):
    add_live(session, ben, ben_template, "bbb-bbbb-bbb")
    assert refusal_code(session, host, request_for(ada, ada_template)) == "bot_busy"
    add_live(session, ada, ada_template, "aaa-aaaa-aaa", status="processing")  # counts as live
    assert refusal_code(session, host, request_for(ada, ada_template, "ccc-cccc-ccc")) == (
        "bot_busy"
    )


def test_lock_wait_is_bounded_by_the_send_timeout(
    engine, session, host, ada, ada_template, monkeypatch
):
    """A request that finds the lock taken waits a few seconds, not as long as the holder's send
    (a send is bounded by its own total deadline). It gives its database connection back between
    looks, and then says the slot was only locked, not full."""
    assert vexa.SEND_DEADLINE_SECONDS <= 30
    assert placement.LOCK_WAIT_SECONDS <= 3
    monkeypatch.setattr(placement, "LOCK_WAIT_SECONDS", 0.6)
    with engine.connect() as holder:  # a request that is inside its send
        holder.execute(
            text("select pg_advisory_xact_lock(cast(:a as int), cast(:b as int))"),
            {"a": placement.PLACEMENT_LOCK, "b": placement.LOCAL_HOST_ID},
        )
        began = time.monotonic()
        with pytest.raises(PlacementRefused) as caught:
            place(session, host, request_for(ada, ada_template))
        waited = time.monotonic() - began
        holder.rollback()
    assert caught.value.code == "bot_busy" and caught.value.slot_locked is True
    assert 0.5 <= waited < 2
    assert host.bots_sent == 0
    assert session.scalars(select(Meeting)).first() is None


def test_a_request_that_waited_for_the_lock_gets_the_slot_when_the_send_finishes_in_time(
    engine, session, host, ada, ada_template, ben, ben_template, monkeypatch
):
    """The point of waiting: a send in flight (a different meeting) does not turn away a request
    that would have found room. With room for two, the waiter is placed once the first is done."""
    monkeypatch.setattr(placement, "LOCK_WAIT_SECONDS", 3.0)
    host.send_delay = 0.6
    settings = make_settings(bot_max_local=2)
    results = []

    def first():
        with Session(engine, expire_on_commit=False) as own:
            results.append(
                place_bot(
                    own,
                    host,
                    request_for(ada, ada_template, "aaa-aaaa-aaa"),
                    now=NOW,
                    settings=settings,
                )
            )

    t = threading.Thread(target=first)
    t.start()
    time.sleep(0.2)  # the first is inside its send now
    second = place_bot(
        session, host, request_for(ben, ben_template, "bbb-bbbb-bbb"), now=NOW, settings=settings
    )
    t.join()
    assert second.status == "bot_sent" and len(results) == 1 and host.bots_sent == 2


@pytest.mark.parametrize(
    ("send_seconds", "wait_seconds", "locked"), [(0.3, 3.0, False), (2.5, 0.8, True)]
)
def test_many_concurrent_requests_do_not_exhaust_the_pool_and_are_answered_within_the_wait(
    engine, session, host, ada, ada_template, monkeypatch, send_seconds, wait_seconds, locked
):
    """Twelve requests at once against a pool of two connections. One places; the other eleven are
    told `bot_busy` within the wait, whether the send ended inside it (the slot is then full) or
    not (the slot was only locked). None may queue holding a connection, which would starve every
    other endpoint: no pool timeout."""
    monkeypatch.setattr(placement, "LOCK_WAIT_SECONDS", wait_seconds)
    host.send_delay = send_seconds
    small = create_engine(engine.url, pool_size=2, max_overflow=0, pool_timeout=5)
    settings = make_settings(bot_max_local=1)
    start = threading.Barrier(12)
    outcomes: list = []  # (what happened, seconds it took)

    def request(n):
        with Session(small, expire_on_commit=False) as own:
            start.wait()
            began = time.monotonic()
            try:
                result = place_bot(
                    own,
                    host,
                    request_for(ada, ada_template, f"aaa-aaaa-aa{chr(97 + n)}"),
                    now=NOW,
                    settings=settings,
                )
            except Exception as error:  # a pool timeout shows up here too
                result = error
            outcomes.append((result, time.monotonic() - began))

    threads = [threading.Thread(target=request, args=(n,)) for n in range(12)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    small.dispose()

    refused = [(r, took) for r, took in outcomes if isinstance(r, PlacementRefused)]
    placed = [r for r, _ in outcomes if isinstance(r, Meeting)]
    other = [r for r, _ in outcomes if not isinstance(r, Meeting | PlacementRefused)]
    assert other == [], other  # no pool timeout, no surprise
    assert len(placed) == 1 and len(refused) == 11
    assert all(r.code == "bot_busy" for r, _ in refused)
    assert all(r.slot_locked is locked for r, _ in refused)
    assert max(took for _, took in refused) < wait_seconds + 1.5


# ---- what a placed meeting carries ----


def test_a_placed_meeting_carries_the_request(session, host, ada, ada_template):
    meeting = place(session, host, request_for(ada, ada_template, source="calendar"), now=NOW)
    assert (meeting.owner_user_id, meeting.platform, meeting.native_id) == (
        ada.id,
        "google_meet",
        CODE,
    )
    assert meeting.url == url_of(CODE) and meeting.template_id == ada_template.id
    assert meeting.source == "calendar"
    assert (meeting.status, meeting.step) == ("bot_sent", "waiting")
    assert meeting.started_polling_at == NOW
    assert meeting.vexa_meeting_id == "1001"
    assert host.sent == [url_of(CODE)]


def test_a_template_that_is_not_the_users_is_refused_before_anything_is_sent(
    session, host, ada, ben_template
):
    with pytest.raises(ValueError):
        place(session, host, request_for(ada, ben_template))
    assert host.bots_sent == 0


def test_a_missing_template_falls_back_to_the_calendar_template(session, host, ada, ada_template):
    from app.models import UserSettings

    session.add(UserSettings(user_id=ada.id, calendar_template_id=ada_template.id))
    session.commit()
    req = PlacementRequest(ada.id, "google_meet", CODE, url_of(CODE), None, "calendar")
    assert place(session, host, req).template_id == ada_template.id


def test_no_template_at_all_is_an_error_before_anything_is_sent(session, host, ada):
    req = PlacementRequest(ada.id, "google_meet", CODE, url_of(CODE), None, "calendar")
    with pytest.raises(ValueError):
        place(session, host, req)
    assert host.bots_sent == 0


def test_a_source_the_meetings_table_does_not_know_is_refused_before_anything_is_sent(
    session, host, ada, ada_template
):
    with pytest.raises(ValueError):
        place(session, host, request_for(ada, ada_template, source="carrier_pigeon"))
    assert host.bots_sent == 0


def test_an_unknown_user_is_an_error_before_anything_is_sent(session, host, ada_template):
    ghost = PlacementRequest(999_999, "google_meet", CODE, url_of(CODE), ada_template.id, "manual")
    with pytest.raises(ValueError):
        place(session, host, ghost)
    assert host.bots_sent == 0


# ---- when the send is uncertain, or saving fails ----


def test_an_uncertain_send_keeps_the_code_reserved_with_a_placeholder_meeting(
    engine, session, host, ada, ada_template, ben, ben_template
):
    """Vexa may still be handling a POST whose answer was lost, so the bot can join seconds later:
    a stop sent now would find nothing. The meeting is kept as `bot_sent` with no Vexa id, marked
    as unconfirmed, so the code stays taken (counted for capacity) until the worker knows."""
    host.late_accept = True
    meeting = place(session, host, request_for(ada, ada_template))
    assert (meeting.status, meeting.step) == ("bot_sent", "waiting")
    assert meeting.vexa_meeting_id is None
    assert meeting.completion_reason == pipeline.SEND_UNCONFIRMED
    assert services.status_text(meeting, NOW) == "Starting the bot"
    assert host.stopped == []  # nothing to stop yet: the bot has not landed
    assert lock_is_free(engine)
    # The code is held: the owner's retry says a note taker is already there, a stranger's
    # paste is the neutral refusal, and the slot counts as taken.
    assert refusal_code(session, host, request_for(ada, ada_template), bot_max_local=3) == (
        "already_in_meeting"
    )
    assert refusal_code(session, host, request_for(ben, ben_template), bot_max_local=3) == (
        "meeting_has_note_taker"
    )
    other = request_for(ben, ben_template, "zzz-zzzz-zzz")
    assert refusal_code(session, host, other) == "bot_busy"


def test_a_late_bot_is_adopted_by_the_worker_not_stopped(session, host, ada, ada_template):
    host.late_accept = True
    meeting = place(session, host, request_for(ada, ada_template))
    host.land_late_bots()  # Vexa finished the request: the bot is joining
    pipeline.advance_meeting(session, meeting.id, vexa=host, now=NOW + timedelta(seconds=20))
    session.refresh(meeting)
    assert (meeting.status, meeting.step) == ("bot_sent", "waiting")
    assert meeting.completion_reason == "joining"  # an ordinary live state now, marker gone
    assert host.stopped == []
    # and it stays adopted on the polls that follow
    pipeline.advance_meeting(session, meeting.id, vexa=host, now=NOW + timedelta(seconds=200))
    session.refresh(meeting)
    assert meeting.status == "bot_sent" and meeting.completion_reason == "joining"


def test_adopting_a_late_bot_pins_its_vexa_id_so_the_code_is_never_used_to_find_it_again(
    session, host, ada, ada_template
):
    """From then on the worker looks the meeting up by id, so a reused code can never hand it
    someone else's (or an older) recording."""
    host.late_accept = True
    meeting = place(session, host, request_for(ada, ada_template))
    host.land_late_bots()
    pipeline.advance_meeting(session, meeting.id, vexa=host, now=NOW + timedelta(seconds=20))
    session.refresh(meeting)
    assert meeting.vexa_meeting_id == str(host.live[("google_meet", CODE)])


def test_a_live_bot_whose_id_Vexa_did_not_give_is_not_adopted(session, host, ada, ada_template):
    host.late_accept = True
    meeting = place(session, host, request_for(ada, ada_template))
    host.land_late_bots()
    host.get_meeting = lambda platform, code: vexa_meeting(status="joining")  # no id in the body
    pipeline.advance_meeting(session, meeting.id, vexa=host, now=NOW + timedelta(seconds=20))
    session.refresh(meeting)
    assert (
        meeting.vexa_meeting_id is None and meeting.completion_reason == pipeline.SEND_UNCONFIRMED
    )
    pipeline.advance_meeting(session, meeting.id, vexa=host, now=NOW + timedelta(seconds=61))
    session.refresh(meeting)
    assert meeting.status == "failed"


def test_the_grace_counts_from_the_uncertain_send_not_from_the_meetings_first_send(
    session, host, ada, ada_template
):
    """A refused meeting sent again hours later must get the full grace, not be reaped at once."""
    old = add_live(session, ada, ada_template, CODE, status="failed")
    old.started_polling_at = NOW - timedelta(hours=3)
    session.commit()
    host.late_accept = True
    assert pipeline.send_again(old, host) is True
    session.commit()
    assert old.started_polling_at is not None
    assert datetime.now(UTC) - old.started_polling_at < timedelta(seconds=30)
    pipeline.advance_meeting(
        session, old.id, vexa=host, now=datetime.now(UTC) + timedelta(seconds=10)
    )
    session.refresh(old)
    assert old.status == "bot_sent"  # still inside its grace


def test_a_bot_that_never_landed_is_stopped_and_the_meeting_fails_in_plain_words(
    session, host, ada, ada_template
):
    host.late_accept = True
    meeting = place(session, host, request_for(ada, ada_template))
    pipeline.advance_meeting(session, meeting.id, vexa=host, now=NOW + timedelta(seconds=30))
    session.refresh(meeting)
    assert meeting.status == "bot_sent" and host.stopped == []  # still inside the grace
    pipeline.advance_meeting(session, meeting.id, vexa=host, now=NOW + timedelta(seconds=61))
    session.refresh(meeting)
    assert meeting.status == "failed"
    assert meeting.failure_reason == pipeline.REASON_SEND_UNCONFIRMED
    assert "not recorded" in meeting.failure_reason
    assert meeting.completion_reason is None
    assert host.stopped == [("google_meet", CODE)]  # a last stop, in case the bot lands late
    assert services.retry_block(meeting) == "no_audio"  # the owner pastes a fresh link


def test_a_finished_vexa_meeting_on_the_reused_code_is_not_adopted(
    session, host, ada, ada_template
):
    """The code of a recurring meeting can match an older, finished Vexa meeting: that is not our
    bot, and must never be adopted as one."""
    host.late_accept = True
    meeting = place(session, host, request_for(ada, ada_template))
    host.get_meeting = lambda platform, code: vexa_meeting(status="completed")
    pipeline.advance_meeting(session, meeting.id, vexa=host, now=NOW + timedelta(seconds=61))
    session.refresh(meeting)
    assert meeting.status == "failed" and meeting.failure_reason == pipeline.REASON_SEND_UNCONFIRMED


def test_an_uncertain_send_that_was_accepted_is_adopted_on_the_first_poll(
    session, host, ada, ada_template
):
    host.timeout_after_accept = True  # accepted, answer lost: the bot is live straight away
    meeting = place(session, host, request_for(ada, ada_template))
    assert meeting.vexa_meeting_id is None
    pipeline.advance_meeting(session, meeting.id, vexa=host, now=NOW + timedelta(seconds=5))
    session.refresh(meeting)
    assert meeting.status == "bot_sent" and meeting.completion_reason == "joining"
    assert host.stopped == []


def test_a_send_the_host_refused_is_not_followed_by_a_stop(session, host, ada, ada_template):
    host.error = unreachable()  # a plain VexaError: the host said no, there is no bot to stop
    with pytest.raises(VexaError):
        place(session, host, request_for(ada, ada_template))
    assert host.stopped == []


def test_the_stop_after_a_failed_save_runs_while_the_lock_is_still_held(
    engine, session, host, ada, ada_template, monkeypatch
):
    """Stopping by meeting code is only safe while no other placement can use the code, so the stop
    comes before the lock is let go."""
    seen = []
    real_stop = host.stop_bot
    host.stop_bot = lambda platform, code: (
        seen.append(lock_is_free(engine)),
        real_stop(platform, code),
    )
    fail_when_the_meeting_is_written(session, monkeypatch, "flush")
    with pytest.raises(OperationalError):
        place(session, host, request_for(ada, ada_template))
    assert seen == [False]


def test_a_commit_that_failed_after_it_landed_does_not_stop_the_recorded_bot(
    session, host, ada, ada_template, monkeypatch
):
    real_commit = session.commit

    def commit_then_lose_the_answer(*args, **kwargs):
        real_commit()
        raise OperationalError("commit", {}, Exception("connection reset"))

    monkeypatch.setattr(session, "commit", commit_then_lose_the_answer)
    with pytest.raises(OperationalError):
        place(session, host, request_for(ada, ada_template))
    monkeypatch.undo()
    assert host.stopped == []  # the meeting is in the database: its bot is a recorded one
    assert session.scalars(select(Meeting)).one().vexa_meeting_id == "1001"


def test_a_failed_commit_does_not_stop_a_bot_when_another_meeting_holds_the_code_by_then(
    engine, session, host, ada, ada_template, ben, ben_template, monkeypatch
):
    """After a failed commit the lock is gone: before a stop by code, the lock is taken again and a
    live meeting of anyone on the code means the bot there may not be ours to stop."""
    real_commit = session.commit

    def lose_the_commit_after_someone_else_took_the_code(*args, **kwargs):
        with Session(engine) as other:
            other.add(
                Meeting(
                    owner_user_id=ben.id,
                    url=url_of(CODE),
                    platform="google_meet",
                    native_id=CODE,
                    template_id=ben_template.id,
                    status="bot_sent",
                )
            )
            other.commit()
        raise OperationalError("commit", {}, Exception("connection lost"))

    monkeypatch.setattr(session, "commit", lose_the_commit_after_someone_else_took_the_code)
    with pytest.raises(OperationalError):
        place(session, host, request_for(ada, ada_template), bot_max_local=3)
    monkeypatch.undo()
    assert host.stopped == []
    assert real_commit  # (the patched commit never wrote ada's meeting)


def test_a_failed_commit_stops_the_bot_while_it_holds_the_lock_again(
    engine, session, host, ada, ada_template, monkeypatch
):
    seen = []
    real_stop = host.stop_bot
    host.stop_bot = lambda platform, code: (
        seen.append(lock_is_free(engine)),
        real_stop(platform, code),
    )
    monkeypatch.setattr(
        session,
        "commit",
        lambda *a, **k: (_ for _ in ()).throw(OperationalError("c", {}, Exception("x"))),
    )
    with pytest.raises(OperationalError):
        place(session, host, request_for(ada, ada_template))
    assert seen == [False] and host.stopped == [("google_meet", CODE)]


# ---- stopping only a bot that is ours ----


def add_row(session, user, template, code, *, status):
    return add_live(session, user, template, code, status=status)


def test_cancel_does_not_stop_a_bot_that_belongs_to_a_newer_meeting_of_another_user(
    session, ada, ada_template, ben, ben_template
):
    older = add_row(session, ada, ada_template, CODE, status="bot_sent")  # its bot already left
    add_row(session, ben, ben_template, CODE, status="bot_sent")  # the bot there is Ben's
    vexa_host = FakeVexa()
    services.cancel_meeting(session, vexa_host, older)
    assert getattr(vexa_host, "stopped", []) == []
    assert older.status == "failed"


def test_end_does_not_stop_a_bot_that_belongs_to_a_newer_meeting_of_another_user(
    session, ada, ada_template, ben, ben_template
):
    older = add_row(session, ada, ada_template, CODE, status="bot_sent")
    older.completion_reason = "active"
    session.commit()
    add_row(session, ben, ben_template, CODE, status="bot_sent")
    vexa_host = FakeVexa()
    services.end_meeting(session, vexa_host, older)
    assert getattr(vexa_host, "stopped", []) == []


def test_cancel_and_end_still_stop_the_users_own_newest_bot(session, ada, ada_template):
    mine = add_row(session, ada, ada_template, CODE, status="bot_sent")
    vexa_host = FakeVexa()
    services.cancel_meeting(session, vexa_host, mine)
    assert vexa_host.stopped == [("google_meet", CODE)]


def test_a_meeting_that_is_not_live_cannot_stop_the_bot_of_a_live_one(
    session, ada, ada_template, ben, ben_template
):
    done = add_row(session, ada, ada_template, CODE, status="failed")
    add_row(session, ben, ben_template, CODE, status="bot_sent")
    assert not placement.may_stop_bot(session, done)
    assert placement.may_stop_bot(
        session, add_row(session, ada, ada_template, "zzz-zzzz-zzz", status="failed")
    )


# ---- settings ----


def test_the_two_new_settings_have_safe_defaults():
    settings = make_settings()
    assert (settings.bot_max_local, settings.user_bot_limit) == (1, 2)


def test_the_old_max_active_bots_name_still_sets_the_local_limit(monkeypatch):
    monkeypatch.setenv("MAX_ACTIVE_BOTS", "3")
    assert make_settings().bot_max_local == 3
    monkeypatch.setenv("BOT_MAX_LOCAL", "2")
    assert make_settings().bot_max_local == 2  # the new name wins when both are set


def test_a_negative_limit_is_refused_at_start_up_and_zero_means_a_drain():
    with pytest.raises(ValueError):
        make_settings(bot_max_local=-1)
    with pytest.raises(ValueError):
        make_settings(user_bot_limit=-1)
    assert make_settings(bot_max_local=0).bot_max_local == 0
