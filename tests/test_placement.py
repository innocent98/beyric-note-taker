"""`place_bot()`: the one function that sends a bot (plan task C11).

These run on a real Postgres: the lock, the capacity count and the insert are the point, and
SQLite has neither advisory locks nor the isolation the race depends on. The threaded tests give
each thread its own session and connection, like two web requests.

Plan note: the plan lists `test_same_code_by_other_user_is_already_in_meeting`. The lead decided
(after the #45 review) that a live bot per meeting code is PER USER, so another account in the same
meeting gets its own bot until C12 decides sharing. That test is therefore split into the two below:
`..._same_user_...` (refused) and `..._other_user_...` (not refused). Every other plan name is kept.
"""

import inspect
import threading
import time
from datetime import UTC, datetime

import pytest
from sqlalchemy import select, text
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from app import placement
from app.models import Meeting, Template, UsageEntry
from app.placement import PlacementRefused, PlacementRequest, place_bot
from app.usage import email_hash
from app.vexa import VexaClient, VexaError
from tests.fakes.vexa_host import FakeVexaHost, unreachable
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


def test_same_code_by_other_user_gets_its_own_bot_until_sharing_is_decided(
    session, host, ada, ada_template, ben, ben_template
):
    add_live(session, ada, ada_template, CODE)
    meeting = place(session, host, request_for(ben, ben_template), bot_max_local=3)
    assert meeting.owner_user_id == ben.id
    assert host.bots_sent == 1  # one bot each; C12 decides whether the second should share


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
    # The longest a request waits for the lock is the longest a send can take, plus a margin.
    send_timeout = inspect.signature(VexaClient).parameters["timeout"].default
    assert placement.LOCK_WAIT_SECONDS > send_timeout
    assert placement.LOCK_WAIT_SECONDS <= send_timeout + 15

    monkeypatch.setattr(placement, "LOCK_WAIT_SECONDS", 0.5)
    with engine.connect() as holder:  # a request that is stuck inside its send
        holder.execute(
            text("select pg_advisory_xact_lock(cast(:a as int), cast(:b as int))"),
            {"a": placement.PLACEMENT_LOCK, "b": placement.LOCAL_HOST_ID},
        )
        began = time.monotonic()
        code = refusal_code(session, host, request_for(ada, ada_template))
        waited = time.monotonic() - began
        holder.rollback()
    assert code == "bot_busy"  # never hangs, never names the other request
    assert 0.4 <= waited < 5
    assert host.bots_sent == 0
    assert session.scalars(select(Meeting)).first() is None


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
