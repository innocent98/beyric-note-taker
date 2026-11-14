# ruff: noqa: F811
"""The note taker name on the paths that send a bot without going through `services.create_meeting`:
the calendar join runner, "Send now", the worker's retry, the hand retry, an unconfirmed send and an
adopted orphan. One fake host that takes a name and records it; everything else is the real code on
a real Postgres."""

from datetime import UTC, datetime, timedelta

import pytest

from app import pipeline
from app.models import Meeting
from app.placement import PlacementRequest, place_bot
from tests.fakes.vexa_host import FakeVexaHost
from tests.test_api import add_meeting, anon, api  # noqa: F401
from tests.test_calendar_connect import google, settings, vault_key  # noqa: F401
from tests.test_join_runner import (  # noqa: F401
    SETTINGS,
    T0,
    ada,
    alert_settings,
    cal,
    materialise,
    meetings,
    run,
    runner_has_led,
)
from tests.test_send_now import (  # noqa: F401
    SEND,
    fresh,
    join_row,
    owner_world,
    send_settings,
    web,
)
from tests.test_web import (  # noqa: F401
    anon_client,
    app,
    fake_openai,
    fake_vexa,
    make_settings,
    make_user,
    owner,
    template,
)

NAME = "Alex from Product"
MEET = "https://meet.google.com/abc-defg-hij"


class NamedHost(FakeVexaHost):
    """A host that takes a name (Skribby's shape): `names` is what each send carried."""

    name = "skribby"
    accepts_bot_name = True

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.names: list[str | None] = []

    def send_bot(self, meeting_url, bot_name=None):
        self.names.append(bot_name)
        return super().send_bot(meeting_url)


@pytest.fixture
def host():
    return NamedHost()


@pytest.fixture
def fake_vexa(host):
    return host


# ---- the calendar join runner ----


def test_a_calendar_join_sends_and_stores_the_owners_name(session, host, cal, ada):
    ada.user.bot_name = NAME
    session.commit()
    materialise(session, ada, cal, summary="Weekly planning")
    outcomes = run(session, host, cal, T0 - timedelta(seconds=60))
    assert [o.result for o in outcomes] == ["joined"]
    assert host.names == [NAME]
    [meeting] = meetings(session)
    assert meeting.source == "calendar" and meeting.bot_name == NAME


def test_a_calendar_join_of_a_person_with_no_name_sends_and_stores_the_default(
    session, host, cal, ada
):
    materialise(session, ada, cal)
    run(session, host, cal, T0 - timedelta(seconds=60))
    [meeting] = meetings(session)
    default = SETTINGS.skribby_bot_name
    assert host.names == [default] and meeting.bot_name == default


# ---- Send now ----


def test_send_now_sends_and_stores_the_owners_name(web, session, host, owner_world):
    owner_world.user.bot_name = NAME
    session.commit()
    join = join_row(session, owner_world)
    response = web.post(SEND.format(join.id))
    assert response.status_code == 200, response.text
    assert host.names == [NAME]
    assert session.get(Meeting, response.json()["meeting_id"]).bot_name == NAME


# ---- the hand retry and the worker's retry ----


def test_the_hand_retry_sends_the_owners_current_name_and_stores_it(
    api, session, template, host, owner
):
    from app.pipeline import REASON_JOIN_REFUSED

    owner.bot_name = "Second Name"
    session.commit()
    refused = add_meeting(
        session,
        template,
        status="failed",
        failure_reason=REASON_JOIN_REFUSED,
        completion_reason="google_meet_join_refused",
        terminal_seen_at=datetime.now(UTC) - timedelta(minutes=6),
        join_attempts=1,
        bot_name="First Name",
    )
    body = api.post(f"/api/meetings/{refused.id}/retry").json()
    assert body["status"] == "bot_sent", body
    assert host.names == ["Second Name"]
    session.expire_all()
    assert session.get(Meeting, refused.id).bot_name == "Second Name"


def test_the_workers_retry_after_a_refusal_sends_the_current_name(session, template, host, owner):
    owner.bot_name = NAME
    session.commit()
    meeting = add_meeting(session, template, status="bot_sent", join_attempts=1)
    pipeline._resend(session, meeting, host, failed_reason="join_refused")
    session.expire_all()
    assert host.names == [NAME]
    assert session.get(Meeting, meeting.id).bot_name == NAME


# ---- an unconfirmed send and an adopted orphan ----


def request_for(user, template, source="calendar", adopt_orphan=False):
    return PlacementRequest(
        user_id=user.id,
        platform="google_meet",
        native_id="abc-defg-hij",
        meeting_url=MEET,
        template_id=template.id,
        source=source,
        adopt_orphan=adopt_orphan,
    )


def test_an_unconfirmed_send_keeps_the_name_it_was_sent_with(session, template, host, owner):
    owner.bot_name = NAME
    session.commit()
    host.timeout_after_accept = True
    meeting = place_bot(
        session, host, request_for(owner, template), now=datetime.now(UTC), settings=make_settings()
    )
    assert meeting.vexa_meeting_id is None  # the answer was lost: an unconfirmed start
    assert host.names == [NAME] and meeting.bot_name == NAME


def test_an_adopted_orphan_stores_the_current_name(session, template, host, owner):
    # The orphan was sent earlier by us and is adopted; its real name is not known, so the meeting
    # records the owner's current name (a follow-up in the SOP).
    owner.bot_name = NAME
    session.commit()
    host.seed_live_bot("google_meet", "abc-defg-hij")
    meeting = place_bot(
        session,
        host,
        request_for(owner, template, adopt_orphan=True),
        now=datetime.now(UTC),
        settings=make_settings(),
    )
    assert meeting.vexa_meeting_id is not None and meeting.bot_name == NAME
