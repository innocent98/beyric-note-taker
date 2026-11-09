"""The "add the note taker to your event to skip the wait" tip: the setting, the rule that decides
when the server may say it, and the two responses that carry it (`GET /api/calendar` for Settings,
the meeting detail for a meeting's page).

The rule is: the address is told only when it is true that inviting it helps, that is the bot is
Skribby's, signed in (`SKRIBBY_ACCOUNT_ID`) and the operator has set `BOT_INVITE_EMAIL`. A meeting
adds its own conditions on top: Google Meet, run by Skribby, waiting to be let in or not let in.
"""

from datetime import UTC, datetime, timedelta

import pytest

from app import api as api_module
from app.bot_invite import meeting_invite_email
from app.config import Settings
from app.models import Meeting
from app.pipeline import REASON_NOT_ADMITTED, REASON_NOT_LET_IN
from tests import test_calendar_connect as cc
from tests.test_api import anon, api  # noqa: F401  (fixtures used by name)
from tests.test_calendar_connect import (  # noqa: F401  (fixtures used by name)
    browser,
    build_app,
    capp,
    connect_google,
    google,
    settings,
    vault_key,
)
from tests.test_web import (  # noqa: F401  (fixtures used by name)
    add_meeting,
    anon_client,
    app,
    client,
    fake_openai,
    fake_vexa,
    make_settings,
    owner,
    template,
)

URL = "/api/calendar"
KEY = "sk-test-key"
ACCOUNT = "11111111-2222-3333-4444-555555555555"  # placeholder Skribby account id
ADDRESS = "note@example.test"  # placeholder, never a real mailbox


def tip_settings(**overrides) -> Settings:
    """Skribby, signed in, with the address set: every condition of the rule met."""
    values = {
        "bot_provider": "skribby",
        "skribby_api_key": KEY,
        "skribby_account_id": ACCOUNT,
        "bot_invite_email": ADDRESS,
    }
    return Settings(_env_file=None, **{**values, **overrides})


# ---- the setting ----


def test_it_is_off_by_default():
    settings = Settings(_env_file=None)
    assert settings.bot_invite_email == ""
    assert settings.bot_invite_email_shown is None


def test_an_empty_value_is_fine_even_without_skribby():
    assert Settings(_env_file=None, bot_invite_email="").bot_invite_email == ""
    assert Settings(_env_file=None, bot_invite_email="   ").bot_invite_email == ""


def test_one_plain_address_is_kept_trimmed_and_lower_cased():
    assert tip_settings(bot_invite_email=" Note@Example.TEST ").bot_invite_email == ADDRESS


@pytest.mark.parametrize(
    "bad",
    [
        "note",
        "note@example",
        "@example.test",
        "note@",
        "note @example.test",
        "note@example.test, other@example.test",
        "Note Taker <note@example.test>",
        "note@example..test",
        "-note@example.test",
        "note@-example.test",
        "mailto:note@example.test",
    ],
)
def test_a_bad_value_is_refused_naming_the_setting(bad):
    with pytest.raises(ValueError, match="BOT_INVITE_EMAIL") as caught:
        Settings(_env_file=None, bot_invite_email=bad)
    assert "one email address" in str(caught.value)


def test_a_bad_value_stops_the_process_at_startup(monkeypatch):
    from app.config import get_settings

    monkeypatch.setenv("BOT_INVITE_EMAIL", "not an address")
    get_settings.cache_clear()
    try:
        with pytest.raises(ValueError, match="BOT_INVITE_EMAIL"):
            get_settings()
    finally:
        monkeypatch.delenv("BOT_INVITE_EMAIL")
        get_settings.cache_clear()


# ---- when the server may say it ----


def test_skribby_signed_in_with_the_address_shows_it():
    assert tip_settings().bot_invite_email_shown == ADDRESS


@pytest.mark.parametrize(
    "missing",
    [
        {"bot_provider": "vexa"},  # Vexa's bot is nobody's guest
        {"skribby_account_id": ""},  # a guest bot has no address to invite
        {"bot_invite_email": ""},  # the operator has not set it
    ],
    ids=["vexa", "no-account", "no-address"],
)
def test_any_one_condition_missing_hides_it(missing):
    assert tip_settings(**missing).bot_invite_email_shown is None


# ---- what a meeting's page may say ----


def meeting(**fields) -> Meeting:
    values = {
        "platform": "google_meet",
        "bot_provider": "skribby",
        "status": "bot_sent",
        "step": "waiting",
        "completion_reason": "awaiting_admission",
        "bot_state_seen_at": datetime.now(UTC),
    }
    return Meeting(**{**values, **fields})


def test_a_meeting_waiting_in_the_lobby_gets_the_address():
    assert meeting_invite_email(meeting(), tip_settings()) == ADDRESS


@pytest.mark.parametrize("reason", [REASON_NOT_ADMITTED, REASON_NOT_LET_IN])
def test_a_meeting_the_bot_was_not_let_into_gets_the_address(reason):
    failed = meeting(status="failed", completion_reason=None, failure_reason=reason)
    assert meeting_invite_email(failed, tip_settings()) == ADDRESS


@pytest.mark.parametrize(
    "fields",
    [
        {"platform": "zoom"},
        {"platform": "teams"},
        {"platform": None},
        {"bot_provider": "vexa"},
        {"completion_reason": "joining"},
        {"completion_reason": "active"},
        {"status": "ready", "step": "done", "completion_reason": None},
        {"status": "processing", "completion_reason": None},
        {"bot_state_seen_at": datetime.now(UTC) - timedelta(hours=1)},  # a state not seen lately
        {"bot_state_seen_at": None},
        {"status": "failed", "completion_reason": None, "failure_reason": "Something else."},
    ],
    ids=[
        "zoom",
        "teams",
        "no-platform",
        "vexa-ran-it",
        "joining",
        "in-the-call",
        "ready",
        "processing",
        "stale-state",
        "no-state-time",
        "failed-another-way",
    ],
)
def test_the_page_says_nothing_otherwise(fields):
    assert meeting_invite_email(meeting(**fields), tip_settings()) is None


def test_a_meeting_gets_nothing_while_the_server_is_not_set_up_for_it():
    for off in ({"bot_provider": "vexa"}, {"skribby_account_id": ""}, {"bot_invite_email": ""}):
        assert meeting_invite_email(meeting(), tip_settings(**off)) is None


# ---- the meeting detail response ----


@pytest.fixture
def tip_on(monkeypatch):
    monkeypatch.setattr(api_module, "get_settings", lambda: tip_settings())


def test_the_detail_carries_the_address_while_the_bot_waits(api, session, template, tip_on):
    waiting = add_meeting(
        session,
        template,
        bot_provider="skribby",
        status="bot_sent",
        completion_reason="awaiting_admission",
        bot_state_seen_at=datetime.now(UTC),
    )
    body = api.get(f"/api/meetings/{waiting.id}").json()
    assert body["bot_invite_email"] == ADDRESS
    assert body["status"] == "bot_sent"


def test_the_detail_carries_the_address_when_the_bot_was_not_let_in(api, session, template, tip_on):
    failed = add_meeting(
        session,
        template,
        bot_provider="skribby",
        status="failed",
        step="waiting",
        failure_reason=REASON_NOT_LET_IN,
    )
    assert api.get(f"/api/meetings/{failed.id}").json()["bot_invite_email"] == ADDRESS


def test_the_detail_has_the_field_as_null_for_other_meetings(api, session, template, tip_on):
    zoom = add_meeting(
        session,
        template,
        platform="zoom",
        bot_provider="skribby",
        status="bot_sent",
        completion_reason="awaiting_admission",
        bot_state_seen_at=datetime.now(UTC),
    )
    body = api.get(f"/api/meetings/{zoom.id}").json()
    assert "bot_invite_email" in body and body["bot_invite_email"] is None


def test_the_detail_is_null_while_the_server_is_not_set_up_for_it(api, session, template):
    waiting = add_meeting(
        session,
        template,
        bot_provider="skribby",
        status="bot_sent",
        completion_reason="awaiting_admission",
        bot_state_seen_at=datetime.now(UTC),
    )
    assert api.get(f"/api/meetings/{waiting.id}").json()["bot_invite_email"] is None


# ---- the calendar response ----


def app_with(engine, google, fake_vexa, fake_openai, **overrides):
    values = {
        "google_client_id": cc.CLIENT_ID,
        "google_client_secret": "s3cret",
        "calendar_enabled": True,
        "token_enc_key": cc.KEY,
        "bot_provider": "skribby",
        "skribby_api_key": KEY,
        "skribby_account_id": ACCOUNT,
        "bot_invite_email": ADDRESS,
    }
    return build_app(
        engine, make_settings(**{**values, **overrides}), google, fake_vexa, fake_openai
    )


def test_the_calendar_answer_carries_the_address_connected_or_not(
    engine, google, fake_vexa, fake_openai, owner
):
    web = browser(app_with(engine, google, fake_vexa, fake_openai))
    assert web.get(URL).json()["bot_invite_email"] == ADDRESS
    connect_google(web, google)
    connected = web.get(URL).json()
    assert connected["connected"] is True and connected["bot_invite_email"] == ADDRESS
    # the write answers with the same whole state
    changed = web.put(URL + "/settings", json={"rule": "organizer"}).json()
    assert changed["bot_invite_email"] == ADDRESS


@pytest.mark.parametrize(
    "off",
    [{"bot_provider": "vexa"}, {"skribby_account_id": ""}, {"bot_invite_email": ""}],
    ids=["vexa", "no-account", "no-address"],
)
def test_the_calendar_answer_has_null_when_a_condition_is_missing(
    engine, google, fake_vexa, fake_openai, owner, off
):
    web = browser(app_with(engine, google, fake_vexa, fake_openai, **off))
    connect_google(web, google)
    body = web.get(URL).json()
    assert "bot_invite_email" in body and body["bot_invite_email"] is None
