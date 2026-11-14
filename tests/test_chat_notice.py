"""The line the bot posts in the meeting chat when it joins (C18): off by default, Skribby only, the
approved words with both names cleaned and cut, and never able to stop a bot from being sent."""

import json
import logging

import pytest

from app import chat_notice, transcript_source
from app.chat_notice import FALLBACK_OWNER, NAME_LIMIT, notice_text
from app.config import Settings
from app.models import VocabularyTerm
from app.skribby import SkribbyClient, SkribbyProvider
from app.vexa import VexaProvider
from tests.test_skribby_provider import BASE, BOT, OTHER, URL, accept, make, sent_body
from tests.test_web import make_settings, make_user

pytestmark = pytest.mark.httpx_mock(
    assert_all_responses_were_requested=False, can_send_already_matched_responses=True
)


def skribby_provider() -> SkribbyProvider:
    return make()[0]


APPROVED = (
    "Hi, I'm {bot}. {owner} added me to record this meeting and write notes. "
    "If you'd rather not be recorded, please tell {owner} before we start."
)


# ---- the words ----


def test_the_approved_line_with_both_names():
    text = notice_text("Beyric Meeting Support", "Ada Lovelace")
    assert text == APPROVED.format(bot="Beyric Meeting Support", owner="Ada Lovelace")


def test_no_owner_name_says_the_host_with_a_capital_where_it_starts_a_sentence():
    for empty in (None, "", "   ", "​\n"):
        assert notice_text("Bot Name", empty) == (
            "Hi, I'm Bot Name. The host added me to record this meeting and write notes. "
            "If you'd rather not be recorded, please tell the host before we start."
        )
    assert FALLBACK_OWNER == "the host"


def test_names_are_one_plain_line_without_links_and_cut_short():
    odd = "  Ada\n\tLovelace‮  https://evil.example/x?p=1 and evil.example " + "N" * 300
    text = notice_text("B" * 300, odd)
    assert "\n" not in text and "‮" not in text
    assert "https://" not in text and "evil.example" not in text.replace("evil[.]example", "")
    assert "[link removed]" in text and "evil[.]example" in text
    assert ("B" * (NAME_LIMIT - 1) + "…") in text and "B" * NAME_LIMIT not in text
    assert "N" * NAME_LIMIT not in text
    assert len(text) < 400  # two cut names and the fixed words: a short chat line


def test_the_bot_name_comes_from_one_helper():
    settings = make_settings(skribby_bot_name="Service Desk")
    assert (
        chat_notice.notice_bot_name(settings) == settings.bot_name_for("skribby") == "Service Desk"
    )
    signed_in = make_settings(
        skribby_bot_name="Guest Name", skribby_account_id=OTHER, skribby_account_name="Signed In"
    )
    assert chat_notice.notice_bot_name(signed_in) == "Signed In"  # what the host sees in the room


# ---- off by default ----


def test_the_setting_is_off_by_default_and_nothing_is_sent_without_it(session, httpx_mock):
    assert Settings.model_fields["bot_chat_notice_enabled"].default is False
    assert make_settings().bot_chat_notice_enabled is False
    owner = make_user(session, name="Ada Lovelace")
    options = transcript_source.send_options(session, skribby_provider(), owner.id, make_settings())
    assert options == {}
    accept(httpx_mock)
    provider, _ = make()
    provider.send_bot(URL, **options)
    assert "initial_chat_message" not in sent_body(httpx_mock)


# ---- on: the parameter reaches Skribby ----


def test_enabled_the_text_goes_to_skribby_as_initial_chat_message(session, httpx_mock):
    owner = make_user(session, name="Ada Lovelace")
    settings = make_settings(bot_chat_notice_enabled=True, skribby_bot_name="Service Desk")
    options = transcript_source.send_options(session, skribby_provider(), owner.id, settings)
    assert options == {"chat_notice": APPROVED.format(bot="Service Desk", owner="Ada Lovelace")}
    accept(httpx_mock)
    provider, _ = make()
    provider.send_bot(URL, **options)
    body = sent_body(httpx_mock)
    assert body["initial_chat_message"] == options["chat_notice"]
    assert isinstance(body["initial_chat_message"], str)


@pytest.mark.parametrize(
    "link",
    [URL, "https://us05web.zoom.us/j/123456789?pwd=x", "https://teams.live.com/meet/9876543210"],
)
def test_the_same_line_is_sent_for_every_platform(session, httpx_mock, link):
    owner = make_user(session, name="Ada")
    settings = make_settings(bot_chat_notice_enabled=True)
    options = transcript_source.send_options(session, skribby_provider(), owner.id, settings)
    accept(httpx_mock)
    provider, _ = make()
    provider.send_bot(link, **options)
    assert sent_body(httpx_mock)["initial_chat_message"] == options["chat_notice"]


def test_an_owner_with_no_name_gets_the_host_and_an_odd_name_is_cleaned(session):
    settings = make_settings(bot_chat_notice_enabled=True)
    nameless = make_user(session, "nameless@example.test", name=None)
    odd = make_user(session, "odd@example.test", name="Eve\n" + "x" * 150 + " https://a.example")
    plain = transcript_source.send_options(session, skribby_provider(), nameless.id, settings)
    assert "The host added me" in plain["chat_notice"]
    cut = transcript_source.send_options(session, skribby_provider(), odd.id, settings)[
        "chat_notice"
    ]
    assert "\n" not in cut and "https://" not in cut and "x" * 100 not in cut


def test_the_notice_and_the_vocabulary_ride_together(session):
    owner = make_user(session, name="Ada")
    session.add(VocabularyTerm(owner_user_id=owner.id, term="Zorbl"))
    session.commit()
    # The two settings that switch the vocabulary on cannot both validate with a model that takes
    # it, so the combination is forced: the point is that neither option hides the other.
    settings = make_settings(bot_chat_notice_enabled=True).model_copy(
        update={
            "transcript_source": "skribby",
            "skribby_transcription_model": "soniox/stt-async-v6",
        }
    )
    options = transcript_source.send_options(session, skribby_provider(), owner.id, settings)
    assert options["vocabulary"] == ["Zorbl"] and options["chat_notice"].startswith("Hi, I'm ")
    client = SkribbyClient(
        api_key="k" * 12, base_url=BASE, transcription_model=settings.skribby_transcription_model
    )
    body = client.payload(
        "google_meet",
        "abc-defg-hij",
        "https://meet.google.com/x",
        **{"vocabulary": options["vocabulary"], "chat_notice": options["chat_notice"]},
    )
    assert body["custom_vocabulary"] == ["Zorbl"] and "initial_chat_message" in body


# ---- other providers get nothing and no error ----


def test_vexa_is_never_given_a_notice_even_when_the_setting_is_on(session):
    owner = make_user(session, name="Ada")
    settings = make_settings(bot_chat_notice_enabled=True)
    assert (
        transcript_source.send_options(
            session, VexaProvider.__new__(VexaProvider), owner.id, settings
        )
        == {}
    )


# ---- a bad parameter cannot stop a bot ----


def test_a_refusal_of_the_parameter_is_retried_once_without_it_and_not_logged(httpx_mock, caplog):
    text = notice_text("Service Desk", "Ada Lovelace")
    client = SkribbyClient(api_key="k" * 12, base_url=BASE)
    httpx_mock.add_response(method="POST", url=f"{BASE}/bot", status_code=422, json={"m": "bad"})
    httpx_mock.add_response(method="POST", url=f"{BASE}/bot", status_code=201, json={"id": BOT})
    payload = client.payload("google_meet", "abc-defg-hij", "https://meet.google.com/x", None, text)
    with caplog.at_level(logging.INFO):
        assert client.create(payload) == BOT
    first, second = httpx_mock.get_requests()
    assert "initial_chat_message" in json.loads(first.content)
    assert "initial_chat_message" not in json.loads(second.content)
    assert "retrying once without the chat notice" in caplog.text
    assert "Ada Lovelace" not in caplog.text  # the line is not logged


def test_both_optional_fields_are_dropped_together_on_the_retry(httpx_mock):
    client = SkribbyClient(
        api_key="k" * 12, base_url=BASE, transcription_model="soniox/stt-async-v6"
    )
    httpx_mock.add_response(method="POST", url=f"{BASE}/bot", status_code=400, json={"m": "bad"})
    httpx_mock.add_response(method="POST", url=f"{BASE}/bot", status_code=201, json={"id": BOT})
    payload = client.payload("zoom", "123", "https://zoom.us/j/123", ["Zorbl"], "Hi")
    assert "custom_vocabulary" in payload and "initial_chat_message" in payload
    assert client.create(payload) == BOT
    _, second = httpx_mock.get_requests()
    sent = json.loads(second.content)
    assert "custom_vocabulary" not in sent and "initial_chat_message" not in sent
    assert sent["meeting_url"] == "https://zoom.us/j/123"  # the rest of the create is unchanged


def test_other_failures_are_not_retried_without_the_notice(httpx_mock):
    client = SkribbyClient(api_key="k" * 12, base_url=BASE)
    httpx_mock.add_response(method="POST", url=f"{BASE}/bot", status_code=403, json={"m": "plan"})
    payload = client.payload("google_meet", "a", "https://meet.google.com/x", None, "Hi")
    with pytest.raises(Exception):  # noqa: B017  (a BotProviderError; the point is no second call)
        client.create(payload)
    assert len(httpx_mock.get_requests()) == 1


def test_an_unreadable_owner_name_still_sends_the_notice(session):
    class Broken:
        def begin_nested(self):
            raise RuntimeError("database hiccup")

    settings = make_settings(bot_chat_notice_enabled=True)
    options = chat_notice.send_options(Broken(), skribby_provider(), 1, settings)
    assert "The host added me" in options["chat_notice"]
