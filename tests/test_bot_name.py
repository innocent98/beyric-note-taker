# ruff: noqa: F811
"""A per-user note taker name (`users.bot_name`, `meetings.bot_name`, `app/bot_names.py`): the
validation, the server default when empty, the name stored on the meeting and sent to Skribby for a
manual meeting and for a calendar join, the speaker naming by elimination ignoring it, the owner's
"Admit" line, the chat notice, and old meetings with a null name."""

import json
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest
from sqlalchemy import select

from app import bot_names, chat_notice, pipeline, services, transcript_source
from app.bot_names import NAME_LIMIT, BotNameInvalid, clean_bot_name
from app.models import Meeting, Template, User
from app.placement import PlacementRequest, place_bot
from app.skribby import SkribbyClient, SkribbyProvider, map_bot
from tests.test_api import Api, anon, api, error_of  # noqa: F401  (fixtures used by name)
from tests.test_skribby_provider import (
    BASE,
    BOT,
    NOW,
    OTHER,
    URL,
    accept,
    bot,
    make,
    segment,
)
from tests.test_web import (  # noqa: F401
    OTHER_EMAIL,
    anon_client,
    app,
    fake_openai,
    fake_vexa,
    make_settings,
    make_user,
    owner,
    stranger,
)

pytestmark = pytest.mark.httpx_mock(
    assert_all_responses_were_requested=False, can_send_already_matched_responses=True
)

ROUTE = "/api/settings/bot-name"
DEFAULT = "Beyric Meeting Support"
ZOOM = "https://us05web.zoom.us/j/123456789?pwd=x"
TEAMS = "https://teams.live.com/meet/9876543210"


def posted(httpx_mock) -> dict:
    (request,) = [r for r in httpx_mock.get_requests() if r.method == "POST"]
    return json.loads(request.content)


# ---- the validation ----


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("Alex from Product", "Alex from Product"),
        ("  Alex   from \t Product \n", "Alex from Product"),  # trimmed, inner whitespace collapsed
        ("Notes 📝 for Ada", "Notes 📝 for Ada"),  # an emoji is a plain character
        ("Zoë Müller", "Zoë Müller"),
        ("Ada (Notes)", "Ada (Notes)"),
        ("A" * NAME_LIMIT, "A" * NAME_LIMIT),  # exactly the limit is fine
        ("", None),
        ("   \n ", None),  # clearing the setting
        (None, None),
    ],
)
def test_a_good_name_is_cleaned_and_an_empty_one_clears(raw, expected):
    assert clean_bot_name(raw) == expected


@pytest.mark.parametrize(
    "raw",
    [
        "A" * (NAME_LIMIT + 1),  # Teams' limit is 50
        "Alex \u202efrom Product",  # a bidirectional override
        "Alex \u2066Product\u2069",  # bidirectional isolates
        "Alex\u200bProduct",  # a zero-width space
        "Alex\x00Product",  # a control character (not whitespace)
        "alex@acme.com",
        "Alex <alex@acme.com>",
        "alex@acme",
        "https://acme.com/alex",
        "www.acme.com",
        "www.x",
        "Notes.ai",
        "\u8bcd.com",  # a label in another script, a TLD from the list
        "\u2800",  # Braille blank: draws nothing
        "\u034f\u034f",  # combining grapheme joiner only
        "  \u2800 \u200d ",  # invisible characters and spaces only
        "\ufe0f",  # a bare variation selector
        "Alex from acme.com",
        "meet.google.com/abc-defg-hij",
    ],
)
def test_a_bad_name_is_refused_with_plain_words(raw):
    with pytest.raises(BotNameInvalid) as caught:
        clean_bot_name(raw)
    assert str(caught.value).endswith(".") and "\n" not in str(caught.value)


def test_each_refusal_says_what_is_wrong():
    assert str(pytest.raises(BotNameInvalid, clean_bot_name, "A" * 51).value) == bot_names.TOO_LONG
    assert str(pytest.raises(BotNameInvalid, clean_bot_name, "a@b.co").value) == bot_names.HAS_EMAIL
    assert str(pytest.raises(BotNameInvalid, clean_bot_name, "x.com").value) == (bot_names.HAS_LINK)
    junk = pytest.raises(BotNameInvalid, clean_bot_name, "a\u202eb")
    assert str(junk.value) == bot_names.HAS_JUNK


def test_a_zero_width_joiner_in_an_emoji_sequence_is_kept():
    family = "Team \U0001f468\u200d\U0001f469\u200d\U0001f467"
    assert clean_bot_name(family) == family


# ---- the one helper ----


def test_the_default_is_the_server_one_and_a_chosen_name_wins():
    settings = make_settings(skribby_bot_name="Service Desk")
    nobody = SimpleNamespace(bot_name=None)
    chosen = SimpleNamespace(bot_name="Alex from Product")
    assert bot_names.bot_name_for_user(settings, nobody, "skribby") == "Service Desk"
    assert bot_names.bot_name_for_user(settings, None, "skribby") == "Service Desk"
    assert bot_names.bot_name_for_user(settings, chosen, "skribby") == "Alex from Product"
    assert bot_names.default_name(settings, "skribby") == "Service Desk"
    # Vexa takes no per-user name: it keeps its own, and stores none.
    assert bot_names.bot_name_for_user(settings, chosen, "vexa") == settings.bot_name
    assert bot_names.stored_name(settings, chosen, "vexa") is None
    assert bot_names.stored_name(settings, chosen, "skribby") == "Alex from Product"


@pytest.mark.parametrize("platform", ["zoom", "teams"])
def test_the_name_shown_is_ours_on_zoom_and_teams_even_with_an_account(platform):
    # Zoom and Teams bots join as guests (only a Meet bot signs in), whatever the account setting.
    settings = make_settings(
        skribby_bot_name=DEFAULT, skribby_account_id=OTHER, skribby_account_name="Signed In"
    )
    assert (
        bot_names.shown_name(settings, provider="skribby", platform=platform, stored="Alex")
        == "Alex"
    )
    assert (
        bot_names.shown_name(settings, provider="skribby", platform=platform, stored=None)
        == DEFAULT
    )


def test_a_signed_in_meet_bot_keeps_naming_the_account_until_the_name_is_proven():
    settings = make_settings(
        skribby_bot_name=DEFAULT, skribby_account_id=OTHER, skribby_account_name="Signed In"
    )
    kwargs = {"provider": "skribby", "platform": "google_meet", "stored": "Alex"}
    assert bot_names.shown_name(settings, **kwargs) == "Signed In"  # today's behaviour, kept
    proven = settings.model_copy(update={"bot_name_applies_when_signed_in": True})
    assert bot_names.shown_name(proven, **kwargs) == "Alex"
    # With no account name set, the best guess is the name that was sent.
    nameless = make_settings(skribby_bot_name=DEFAULT, skribby_account_id=OTHER)
    assert bot_names.shown_name(nameless, **kwargs) == "Alex"
    # A guest Meet bot (no account) shows the name that was sent.
    guest = make_settings(skribby_bot_name=DEFAULT)
    assert bot_names.shown_name(guest, **kwargs) == "Alex"


def test_the_flag_is_off_by_default():
    assert make_settings().bot_name_applies_when_signed_in is False


def test_own_names_are_the_stored_name_and_the_account_name():
    settings = make_settings(skribby_bot_name=DEFAULT, skribby_account_name="Signed In")
    assert bot_names.own_names(settings, "Alex") == ("Alex", "Signed In")
    assert bot_names.own_names(settings, None) == (DEFAULT, "Signed In")


# ---- the settings route ----


def test_the_default_is_shown_from_the_server_and_nothing_is_set(api, app, session, owner):
    # Under Skribby (the provider in production) the default is SKRIBBY_BOT_NAME; under Vexa,
    # BOT_NAME.
    app.state.settings = make_settings(
        bot_provider="skribby", skribby_api_key="key-for-tests", skribby_bot_name="Service Desk"
    )
    assert api.get(ROUTE).json() == {
        "bot_name": None,
        "bot_name_default": "Service Desk",
        "warning": None,
        "bot_name_applies_when_signed_in": False,
        "meet_account_name": None,
    }
    app.state.settings = make_settings(bot_provider="vexa")
    assert api.get(ROUTE).json()["bot_name_default"] == make_settings().bot_name


def test_saving_trims_stores_and_reads_back(api, session, owner):
    response = api.put(ROUTE, {"bot_name": "  Alex   from Product "})
    assert response.status_code == 200
    assert response.json()["bot_name"] == "Alex from Product"
    session.expire_all()
    assert session.scalar(select(User.bot_name).where(User.id == owner.id)) == "Alex from Product"
    assert api.get(ROUTE).json()["bot_name"] == "Alex from Product"


@pytest.mark.parametrize("empty", [None, "", "   "])
def test_clearing_goes_back_to_the_default(api, session, owner, empty):
    api.put(ROUTE, {"bot_name": "Alex"})
    assert api.put(ROUTE, {"bot_name": empty}).json()["bot_name"] is None
    session.expire_all()
    assert session.scalar(select(User.bot_name).where(User.id == owner.id)) is None


@pytest.mark.parametrize(
    ("value", "message"),
    [
        ("A" * 51, bot_names.TOO_LONG),
        ("alex@acme.com", bot_names.HAS_EMAIL),
        ("https://acme.com", bot_names.HAS_LINK),
        ("Alex \u202eProduct", bot_names.HAS_JUNK),
    ],
)
def test_a_bad_name_is_422_with_a_plain_message_and_changes_nothing(
    api, session, owner, value, message
):
    api.put(ROUTE, {"bot_name": "Kept Name"})
    response = api.put(ROUTE, {"bot_name": value})
    assert response.status_code == 422
    assert error_of(response, 422, "bot_name_invalid")["message"] == message
    session.expire_all()
    assert session.scalar(select(User.bot_name).where(User.id == owner.id)) == "Kept Name"


def test_one_person_cannot_read_or_change_another_persons_name(api, session, owner, stranger):
    stranger.bot_name = "Other Person"
    session.commit()
    assert api.get(ROUTE).json()["bot_name"] is None  # the caller's own row only
    api.put(ROUTE, {"bot_name": "Mine"})
    session.expire_all()
    assert session.get(User, stranger.id).bot_name == "Other Person"
    assert session.get(User, owner.id).bot_name == "Mine"


def test_the_route_needs_a_login(anon):
    assert anon.get(ROUTE).status_code == 401
    assert anon.put(ROUTE, {"bot_name": "X"}).status_code in (401, 403)


def test_a_write_without_the_csrf_token_is_refused(api, owner):
    assert api.put(ROUTE, {"bot_name": "X"}, csrf=False).status_code == 403


# ---- Skribby is sent the name ----


@pytest.mark.parametrize("link", [URL, ZOOM, TEAMS])
def test_the_name_is_sent_as_bot_name_on_every_platform(httpx_mock, link):
    accept(httpx_mock)
    provider, _ = make()
    provider.send_bot(link, bot_name="Alex from Product")
    assert posted(httpx_mock)["bot_name"] == "Alex from Product"


def test_without_a_name_the_server_default_is_sent(httpx_mock):
    accept(httpx_mock)
    provider, _ = make(bot_name="Service Desk")
    provider.send_bot(URL)
    assert posted(httpx_mock)["bot_name"] == "Service Desk"
    httpx_mock.reset()
    accept(httpx_mock)
    again, _ = make(bot_name="Service Desk")
    again.send_bot(URL, bot_name="   ")  # a blank name never replaces the default
    assert posted(httpx_mock)["bot_name"] == "Service Desk"


def test_a_signed_in_meet_bot_is_still_sent_the_name_but_authentication_is_unchanged(httpx_mock):
    accept(httpx_mock)
    provider, _ = make(account_id=OTHER)
    provider.send_bot(URL, bot_name="Alex from Product")
    body = posted(httpx_mock)
    assert body["bot_name"] == "Alex from Product"
    assert body["authentication"] == {"account_id": OTHER, "always_authenticate": True}


# ---- a manual meeting and a calendar join record and send the name ----


def make_template(session, user) -> Template:
    template = Template(owner_user_id=user.id, name="General", instructions="Write notes.")
    session.add(template)
    session.commit()
    return template


def request_for(user, template, code="abc-defg-hij", source="manual", platform="google_meet"):
    return PlacementRequest(
        user_id=user.id,
        platform=platform,
        native_id=code,
        meeting_url=f"https://meet.google.com/{code}",
        template_id=template.id,
        source=source,
    )


@pytest.mark.parametrize("source", ["manual", "calendar"])
def test_the_name_is_sent_and_stored_on_the_meeting(session, httpx_mock, source):
    user = make_user(session, bot_name="Alex from Product")
    template = make_template(session, user)
    accept(httpx_mock)
    provider, _ = make(bot_name=DEFAULT)
    meeting = place_bot(
        session,
        provider,
        request_for(user, template, source=source),
        now=datetime.now(UTC),
        settings=make_settings(),
    )
    assert posted(httpx_mock)["bot_name"] == "Alex from Product"
    session.expire_all()
    assert session.get(Meeting, meeting.id).bot_name == "Alex from Product"


def test_a_person_without_a_name_gets_the_server_default_sent_and_stored(session, httpx_mock):
    user = make_user(session)
    template = make_template(session, user)
    accept(httpx_mock)
    provider, _ = make(bot_name=DEFAULT)
    meeting = place_bot(
        session,
        provider,
        request_for(user, template),
        now=datetime.now(UTC),
        settings=make_settings(skribby_bot_name=DEFAULT),
    )
    assert posted(httpx_mock)["bot_name"] == DEFAULT
    assert meeting.bot_name == DEFAULT  # what was actually used, so a later change of default
    # cannot rewrite which name this bot joined with


def test_manual_create_meeting_uses_the_owners_name(session, httpx_mock):
    user = make_user(session, bot_name="Sam from Sales")
    template = make_template(session, user)
    accept(httpx_mock)
    provider, _ = make()
    meeting = services.create_meeting(
        session, provider, user, URL, template.id, make_settings(skribby_bot_name=DEFAULT)
    )
    assert posted(httpx_mock)["bot_name"] == "Sam from Sales"
    assert meeting.bot_name == "Sam from Sales"


def test_changing_the_setting_later_does_not_change_a_meeting_already_sent(session, httpx_mock):
    user = make_user(session, bot_name="First Name")
    template = make_template(session, user)
    accept(httpx_mock)
    provider, _ = make()
    meeting = place_bot(
        session,
        provider,
        request_for(user, template),
        now=datetime.now(UTC),
        settings=make_settings(),
    )
    user.bot_name = "Second Name"
    session.commit()
    session.expire_all()
    assert session.get(Meeting, meeting.id).bot_name == "First Name"


def test_a_provider_that_takes_no_name_is_sent_none_and_stores_none(session, fake_vexa):
    user = make_user(session, bot_name="Alex from Product")
    template = make_template(session, user)
    meeting = place_bot(
        session,
        fake_vexa,
        request_for(user, template),
        now=datetime.now(UTC),
        settings=make_settings(),
    )
    assert meeting.bot_name is None
    assert fake_vexa.sent  # sent without a name argument: the fake takes only the link


class NamedProvider:
    """A provider that takes a name and records it (the shape of Skribby's, no network)."""

    name = "skribby"
    accepts_bot_name = True
    accepts_chat_notice = True
    accepts_own_names = True
    region = "eu"

    def __init__(self):
        self.sent: list[dict] = []
        self.next = 0

    def send_bot(
        self,
        meeting_url,
        vocabulary=None,
        chat_notice=None,
        bot_name=None,
        chat_notice_fallback=None,
    ):
        self.sent.append(
            {
                "url": meeting_url,
                "bot_name": bot_name,
                "chat_notice": chat_notice,
                "chat_notice_fallback": chat_notice_fallback,
            }
        )
        self.next += 1
        return f"00000000-0000-0000-0000-{self.next:012d}"

    def get_meeting_by_id(self, vexa_meeting_id, own_names=()):
        self.looked = own_names
        return SimpleNamespace(own=own_names)

    def get_meeting(self, platform, native_id, own_names=()):
        self.looked = own_names
        return SimpleNamespace(own=own_names)


def test_a_resend_uses_the_owners_current_name_and_stores_it(session):
    user = make_user(session, bot_name="Old Name")
    template = make_template(session, user)
    provider = NamedProvider()
    meeting = place_bot(
        session,
        provider,
        request_for(user, template),
        now=datetime.now(UTC),
        settings=make_settings(),
    )
    assert provider.sent[0]["bot_name"] == "Old Name"
    user.bot_name = "New Name"
    session.commit()
    assert pipeline.send_again(meeting, provider) is True
    session.commit()
    assert provider.sent[1]["bot_name"] == "New Name"
    session.expire_all()
    assert session.get(Meeting, meeting.id).bot_name == "New Name"


# ---- the elimination step never names the bot as a speaker ----

ADA_AND_BOT = [("Ada Example", 1), ("Alex from Product", 0.8)]


def test_a_custom_bot_name_is_never_a_speaker_by_elimination():
    items = [
        segment("Ada Example", "one", tag="1"),
        segment(None, "two", tag="2", candidates=ADA_AND_BOT),
    ]
    from unittest.mock import patch

    from app.config import get_settings

    with patch.object(get_settings(), "skribby_name_by_elimination", True):
        # Without the custom name in the list, the bot's own name WOULD be the only name left.
        leaked = map_bot(bot("finished", transcript=items), NOW)
        assert [s["speaker"] for s in leaked.segments] == ["Ada Example", "Alex from Product"]
        # With the meeting's own stored name, it is removed and the tag stays unknown.
        safe = map_bot(bot("finished", transcript=items), NOW, own_names=("Alex from Product",))
        assert [s["speaker"] for s in safe.segments] == ["Ada Example", ""]


def test_the_provider_adds_the_name_skribby_reports_and_the_one_the_caller_knows(httpx_mock):
    from unittest.mock import patch

    from app.config import get_settings

    items = [
        segment("Ada Example", "one", tag="1"),
        segment(None, "two", tag="2", candidates=ADA_AND_BOT),
    ]
    provider, _ = make()
    # (a) Skribby's own get-bot body carries `bot_name`: that alone is enough.
    httpx_mock.add_response(
        method="GET",
        url=f"{BASE}/bot/{BOT}",
        json=bot("finished", transcript=items, bot_name="Alex from Product"),
    )
    with patch.object(get_settings(), "skribby_name_by_elimination", True):
        info = provider.get_meeting_by_id(BOT)
        assert [s["speaker"] for s in info.segments] == ["Ada Example", ""]
    # (b) The caller's stored name works even when the body says nothing about the name.
    httpx_mock.reset()
    httpx_mock.add_response(
        method="GET", url=f"{BASE}/bot/{BOT}", json=bot("finished", transcript=items)
    )
    with patch.object(get_settings(), "skribby_name_by_elimination", True):
        info = provider.get_meeting_by_id(BOT, own_names=("Alex from Product",))
        assert [s["speaker"] for s in info.segments] == ["Ada Example", ""]


def test_the_pipeline_passes_the_meetings_stored_name_to_a_provider_that_takes_it():
    provider = NamedProvider()
    meeting = SimpleNamespace(
        bot_name="Alex from Product",
        vexa_meeting_id="00000000-0000-0000-0000-000000000001",
        platform="google_meet",
        native_id="abc-defg-hij",
    )
    pipeline._lookup(provider, meeting)
    assert provider.looked == ("Alex from Product",)
    meeting.vexa_meeting_id = None
    provider.looked = ()
    pipeline._lookup(provider, meeting)
    assert provider.looked == ("Alex from Product",)
    meeting.bot_name = None  # an old meeting: nothing extra, the provider's own names apply
    provider.looked = ("unset",)
    pipeline._lookup(provider, meeting)
    assert provider.looked == ()


def test_a_provider_without_the_option_is_never_given_it():
    from tests.test_pipeline import FakeVexa

    meeting = SimpleNamespace(
        bot_name="Alex", vexa_meeting_id="7", platform="google_meet", native_id="abc"
    )
    # FakeVexa's lookups take no `own_names`: a call that passed it would raise a TypeError.
    fake = FakeVexa(SimpleNamespace(status="active"))
    assert pipeline._lookup(fake, meeting).status == "active"
    meeting.vexa_meeting_id = None
    assert pipeline._lookup(fake, meeting).status == "active"


# ---- the chat notice and the "Admit" line ----


def test_the_chat_notice_names_the_bot_with_the_name_it_joins_with(session):
    user = make_user(session, name="Ada Lovelace")
    settings = make_settings(bot_chat_notice_enabled=True, skribby_bot_name=DEFAULT)
    provider, _ = make()
    options = transcript_source.send_options(
        session,
        provider,
        user.id,
        settings,
        bot_name="Alex from Product",
        shown_bot_name="Alex from Product",
    )
    assert options["bot_name"] == "Alex from Product"
    assert options["chat_notice"].startswith("Hi, I'm Alex from Product. Ada Lovelace added me")
    # No name given: the server default, as before this feature.
    plain = transcript_source.send_options(session, provider, user.id, settings)
    assert plain["chat_notice"].startswith(f"Hi, I'm {DEFAULT}.")
    assert "bot_name" not in plain
    assert chat_notice.notice_bot_name(settings) == DEFAULT


def test_the_notice_in_a_placed_meeting_carries_the_owners_name(session):
    user = make_user(session, name="Ada Lovelace", bot_name="Alex from Product")
    template = make_template(session, user)
    provider = NamedProvider()
    settings = make_settings(bot_chat_notice_enabled=True, skribby_bot_name=DEFAULT)
    place_bot(
        session, provider, request_for(user, template), now=datetime.now(UTC), settings=settings
    )
    assert provider.sent[0]["chat_notice"].startswith("Hi, I'm Alex from Product.")


def meeting_row(**fields) -> Meeting:
    base = {
        "owner_user_id": 1,
        "url": URL,
        "platform": "google_meet",
        "native_id": "abc-defg-hij",
        "template_id": 1,
        "status": "bot_sent",
        "bot_provider": "skribby",
        "completion_reason": "awaiting_admission",
        "bot_state_seen_at": datetime.now(UTC),
    }
    return Meeting(**{**base, **fields})


def test_the_admit_line_names_the_bot_of_that_meeting(monkeypatch):
    settings = make_settings(skribby_bot_name=DEFAULT)
    monkeypatch.setattr(services, "get_settings", lambda: settings)
    monkeypatch.setattr(services, "BOT_STATE_STALE_POLLS", 10**9)
    line = services.status_text(meeting_row(platform="zoom", bot_name="Alex from Product"))
    assert line == "Waiting to be let in. Admit 'Alex from Product' in the meeting"
    # An old meeting (null name) reads the server default, as before.
    old = services.status_text(meeting_row(platform="zoom", bot_name=None))
    assert old == f"Waiting to be let in. Admit '{DEFAULT}' in the meeting"


def test_the_admit_line_keeps_the_account_name_on_a_signed_in_meet_bot(monkeypatch):
    settings = make_settings(
        skribby_bot_name=DEFAULT, skribby_account_id=OTHER, skribby_account_name="Signed In"
    )
    monkeypatch.setattr(services, "get_settings", lambda: settings)
    monkeypatch.setattr(services, "BOT_STATE_STALE_POLLS", 10**9)
    meet = meeting_row(bot_name="Alex from Product")
    assert services.status_text(meet) == "Waiting to be let in. Admit 'Signed In' in the meeting"
    proven = settings.model_copy(update={"bot_name_applies_when_signed_in": True})
    monkeypatch.setattr(services, "get_settings", lambda: proven)
    assert "Admit 'Alex from Product'" in services.status_text(meet)


def test_an_old_meeting_has_a_null_name_and_the_default_is_used(session):
    user = make_user(session)
    template = make_template(session, user)
    old = Meeting(
        owner_user_id=user.id,
        url=URL,
        platform="google_meet",
        native_id="old-aaaa-bbb",
        template_id=template.id,
    )
    session.add(old)
    session.commit()
    assert old.bot_name is None
    assert bot_names.own_names(make_settings(skribby_bot_name=DEFAULT), old.bot_name)[0] == DEFAULT


def test_the_clients_own_names_still_include_the_server_default_and_the_account():
    client = SkribbyClient(
        api_key="k" * 8, base_url=BASE, bot_name=DEFAULT, account_name="Signed In"
    )
    assert client.own_names == (DEFAULT, "Signed In")
    provider = SkribbyProvider(client)
    assert provider is not None


# ---- the narrower "web address" rule ----


@pytest.mark.parametrize(
    "raw", ["Dr.Smith", "Mr.Jones", "St.Mary", "J.Smith", "词.词", "Alex.Coach", "v2.1 notes"]
)
def test_a_name_with_a_dot_is_fine_unless_it_ends_in_one_of_the_listed_domains(raw):
    assert clean_bot_name(raw) == raw


@pytest.mark.parametrize("raw", ["acme.com", "Notes.AI", "www.x", "Alex at acme.io", "a.b.co"])
def test_a_listed_domain_is_still_a_web_address(raw):
    with pytest.raises(BotNameInvalid) as caught:
        clean_bot_name(raw)
    assert str(caught.value) == bot_names.HAS_LINK


def test_the_listed_domains_are_the_ones_the_sop_names():
    from app.titles import LINK_TLDS

    assert (
        LINK_TLDS
        == "com net org io ai co app dev me us uk ng info biz xyz link live gg tv ly".split()
    )


def test_the_notice_keeps_a_surname_dot_but_defangs_a_domain_in_the_bot_name_and_the_owner():
    text = chat_notice.notice_text("Dr.Smith", "Ada from acme.example")
    assert text.startswith("Hi, I'm Dr.Smith. ")
    assert "Dr[.]Smith" not in text and "acme[.]example" in text
    for name in ("St.Mary", "J.Smith", "Mr.Jones"):
        assert chat_notice.notice_text(name, "Ada").startswith(f"Hi, I'm {name}. ")
    # A domain the validation's short list does not know (the operator's own name, or an old row)
    # never reaches the chat as a link: its dots are written [.].
    assert chat_notice.notice_text("evil.click", "Ada").startswith("Hi, I'm evil[.]click. ")
    # The case of what follows the dot is no exemption.
    assert chat_notice.notice_text("Evil.Click", "Ada").startswith("Hi, I'm Evil[.]Click. ")
    assert chat_notice.notice_text("Dr.Evil.Click", "Ada").startswith("Hi, I'm Dr[.]Evil[.]Click. ")
    assert chat_notice.notice_text("Alex.Smith", "Ada").startswith("Hi, I'm Alex[.]Smith. ")
    # A single letter or a title in front of a real domain is still a domain.
    for name, shown in (
        ("x.click", "x[.]click"),
        ("t.co", "t[.]co"),
        ("dr.click", "dr[.]click"),
        ("Dr.click", "Dr[.]click"),
        ("J.Co", "J[.]Co"),
        ("a.b", "a.b"),  # nothing that reads as a domain: no letters after the dot
    ):
        assert chat_notice.notice_text(name, "Ada").startswith(f"Hi, I'm {shown}. "), name
    assert chat_notice.notice_text("Visit a.b.zip now", "Ada").startswith(
        "Hi, I'm Visit a[.]b[.]zip now. "
    )
    # Dot look-alikes count as dots, in the check and in the notice.
    for dot in ("\u3002", "\uff0e", "\uff61", "\ufe52", "\u2024"):
        assert chat_notice.notice_text(f"evil{dot}click", "Ada").startswith(
            "Hi, I'm evil[.]click. "
        )


@pytest.mark.parametrize(
    "raw",
    [
        "evil\u3002com",
        "evil\uff0ecom",
        "evil\uff61com",
        "evil\ufe52com",
        "evil\u2024com",
        "Notes\u3002ai",
    ],
)
def test_a_dot_look_alike_before_a_listed_domain_is_a_web_address(raw):
    with pytest.raises(BotNameInvalid) as caught:
        clean_bot_name(raw)
    assert str(caught.value) == bot_names.HAS_LINK


# ---- the soft warning ----


@pytest.mark.parametrize(
    "name",
    [
        "Meeting Bot",
        "bot",
        "Alex's Notetaker",
        "Alex Note taker",
        "Alex NoteTaker",
        "Recording Team",
        "Transcription",
        "Alex AI",
        "ai helper",
        "Alex (AI)",
    ],
)
def test_a_name_that_sounds_like_a_recorder_gets_the_soft_warning(name):
    assert (
        bot_names.recorder_warning(name)
        == "Google Meet and Zoom may refuse bots with names like this."
    )
    assert clean_bot_name(name) == name  # a warning, never a refusal


@pytest.mark.parametrize(
    "name", ["Alex from Product", "Robot Ada", "Aida", "Sam's Notes", None, ""]
)
def test_an_ordinary_name_has_no_warning(name):
    assert bot_names.recorder_warning(name) is None


def test_the_answer_carries_the_warning_and_the_save_still_works(api, session, owner):
    response = api.put(ROUTE, {"bot_name": "Alex's AI Notetaker"})
    assert response.status_code == 200
    assert response.json()["bot_name"] == "Alex's AI Notetaker"
    assert response.json()["warning"] == bot_names.RECORDER_WARNING
    assert api.get(ROUTE).json()["warning"] == bot_names.RECORDER_WARNING
    plain = api.put(ROUTE, {"bot_name": "Alex from Product"}).json()
    assert plain["warning"] is None
    assert api.put(ROUTE, {"bot_name": None}).json()["warning"] is None


def test_the_flag_is_in_the_answer_read_only_and_follows_the_server(api, app, owner):
    app.state.settings = make_settings(bot_name_applies_when_signed_in=False)
    assert api.get(ROUTE).json()["bot_name_applies_when_signed_in"] is False
    app.state.settings = make_settings(bot_name_applies_when_signed_in=True)
    assert api.get(ROUTE).json()["bot_name_applies_when_signed_in"] is True
    # Read only: the body takes no such field.
    assert (
        api.put(ROUTE, {"bot_name": "X", "bot_name_applies_when_signed_in": False}).status_code
        == 422
    )


# ---- Skribby refuses the name ----

REFUSED = {"method": "POST", "url": f"{BASE}/bot"}


def posts(httpx_mock) -> list[dict]:
    return [json.loads(r.content) for r in httpx_mock.get_requests() if r.method == "POST"]


def refuse(httpx_mock, text='{"detail":"invalid value"}', status=422):
    httpx_mock.add_response(**REFUSED, status_code=status, text=text)


def test_a_refused_name_is_retried_once_with_the_default_and_the_notice_that_named_it_goes(
    httpx_mock, caplog
):
    refuse(httpx_mock, '{"detail":[{"loc":["body","bot_name"],"msg":"invalid"}]}')
    accept(httpx_mock)
    provider, _ = make(bot_name="Service Desk")
    with caplog.at_level("WARNING"):
        bot_id = provider.send_bot(
            URL,
            bot_name="Alex from Product",
            chat_notice="Hi, I'm Alex from Product. Ada added me.",
        )
    first, second = posts(httpx_mock)
    assert first["bot_name"] == "Alex from Product" and "initial_chat_message" in first
    assert second["bot_name"] == "Service Desk"
    assert "initial_chat_message" not in second  # it named the refused name
    assert bot_id.bot_name == "Service Desk"  # the name the accepted create really carried
    assert (
        "Alex from Product" not in caplog.text
        and "without the person's own note taker name" in caplog.text
    )


def test_a_refusal_that_blames_only_the_notice_keeps_the_name(httpx_mock):
    refuse(httpx_mock, "initial_chat_message is not supported")
    accept(httpx_mock)
    provider, _ = make(bot_name="Service Desk")
    bot_id = provider.send_bot(URL, bot_name="Alex from Product", chat_notice="Hi there")
    first, second = posts(httpx_mock)
    assert second["bot_name"] == "Alex from Product" and "initial_chat_message" not in second
    assert getattr(bot_id, "bot_name", None) is None  # the name asked for was used


def test_a_refusal_that_blames_nothing_in_particular_drops_both(httpx_mock):
    refuse(httpx_mock, "bad request", status=400)
    accept(httpx_mock)
    provider, _ = make(bot_name="Service Desk")
    bot_id = provider.send_bot(URL, bot_name="Alex from Product", chat_notice="Hi, I'm Alex.")
    _, second = posts(httpx_mock)
    assert second["bot_name"] == "Service Desk" and "initial_chat_message" not in second
    assert bot_id.bot_name == "Service Desk"


def test_a_notice_that_does_not_name_the_bot_is_kept_when_only_the_name_goes(httpx_mock):
    refuse(httpx_mock, "bot_name too odd")
    accept(httpx_mock)
    provider, _ = make(bot_name="Service Desk")
    provider.send_bot(URL, bot_name="Alex from Product", chat_notice="Hi, I'm the account name.")
    _, second = posts(httpx_mock)
    assert second["initial_chat_message"] == "Hi, I'm the account name."


def test_the_default_name_is_never_retried(httpx_mock):
    refuse(httpx_mock)
    provider, _ = make(bot_name="Service Desk")
    with pytest.raises(Exception, match="HTTP 422"):
        provider.send_bot(URL)
    assert len(posts(httpx_mock)) == 1


def test_it_is_retried_once_only(httpx_mock):
    refuse(httpx_mock)
    refuse(httpx_mock)
    provider, _ = make(bot_name="Service Desk")
    with pytest.raises(Exception, match="HTTP 422"):
        provider.send_bot(URL, bot_name="Alex from Product")
    assert len(posts(httpx_mock)) == 2


def test_a_placed_meeting_stores_the_name_that_was_really_used(session, httpx_mock):
    user = make_user(session, bot_name="Alex from Product")
    template = make_template(session, user)
    refuse(httpx_mock, "bot_name invalid")
    accept(httpx_mock)
    provider, _ = make(bot_name=DEFAULT)
    meeting = place_bot(
        session,
        provider,
        request_for(user, template),
        now=datetime.now(UTC),
        settings=make_settings(),
    )
    assert [p["bot_name"] for p in posts(httpx_mock)] == ["Alex from Product", DEFAULT]
    assert meeting.bot_name == DEFAULT
    assert session.get(User, user.id).bot_name == "Alex from Product"  # the setting is untouched


def test_a_resend_stores_the_name_that_was_really_used(session):
    from app.skribby import SentBotId

    class Refusing(NamedProvider):
        def send_bot(self, meeting_url, **kwargs):
            super().send_bot(meeting_url, **kwargs)
            sent = SentBotId("00000000-0000-0000-0000-000000000009")
            sent.bot_name = "Service Desk"
            return sent

    user = make_user(session, bot_name="Old Name")
    template = make_template(session, user)
    provider = NamedProvider()
    meeting = place_bot(
        session,
        provider,
        request_for(user, template),
        now=datetime.now(UTC),
        settings=make_settings(),
    )
    assert pipeline.send_again(meeting, Refusing()) is True
    assert meeting.bot_name == "Service Desk"


# ---- the owner's name for a resend ----


def test_with_no_session_the_default_is_sent_and_a_failed_read_keeps_the_stored_name():
    from contextlib import contextmanager

    settings = make_settings(skribby_bot_name=DEFAULT)
    meeting = SimpleNamespace(id=1, bot_name="Stored Name", owner_user_id=1)
    assert pipeline._owner_bot_name(None, meeting, settings, "skribby") == DEFAULT
    assert pipeline._owner_bot_name(None, meeting, settings, "vexa") is None

    class Broken:
        @contextmanager
        def begin_nested(self):
            yield

        def get(self, *args):
            raise RuntimeError("database hiccup")

    assert pipeline._owner_bot_name(Broken(), meeting, settings, "skribby") == "Stored Name"
    meeting.bot_name = None
    assert pipeline._owner_bot_name(Broken(), meeting, settings, "skribby") == DEFAULT


# ---- an Admit line with HTML characters ----


def test_a_name_with_html_characters_is_text_in_the_line_and_escaped_where_it_is_drawn(monkeypatch):
    from app.web import templates

    settings = make_settings(skribby_bot_name=DEFAULT)
    monkeypatch.setattr(services, "get_settings", lambda: settings)
    monkeypatch.setattr(services, "BOT_STATE_STALE_POLLS", 10**9)
    meeting = meeting_row(platform="zoom", bot_name='Tom & <b>Jerry</b> {x} "q"')
    line = services.status_text(meeting)
    assert line == "Waiting to be let in. Admit 'Tom & <b>Jerry</b> {x} \"q\"' in the meeting"
    drawn = templates.env.from_string("{{ status_text(m) }}").render(m=meeting)
    assert "<b>" not in drawn and "&lt;b&gt;Jerry&lt;/b&gt;" in drawn and "&amp;" in drawn


# ---- what the Settings help says about Google Meet ----


def test_meet_account_name_is_the_server_account_name_only_when_it_is_true():
    signed_in = make_settings(skribby_account_id=OTHER, skribby_account_name="Beyric Note Taker")
    assert bot_names.meet_account_name(signed_in) == "Beyric Note Taker"
    # No account name set: the page says nothing about Meet's name.
    assert bot_names.meet_account_name(make_settings(skribby_account_id=OTHER)) is None
    assert (
        bot_names.meet_account_name(
            make_settings(skribby_account_id=OTHER, skribby_account_name="  ")
        )
        is None
    )
    # A guest bot (no signed-in account): the name does show, so nothing to caveat.
    assert (
        bot_names.meet_account_name(make_settings(skribby_account_name="Beyric Note Taker")) is None
    )
    # The switch on (a later live call showed the custom name): the sentence goes.
    proven = signed_in.model_copy(update={"bot_name_applies_when_signed_in": True})
    assert bot_names.meet_account_name(proven) is None


def test_the_answer_carries_the_meet_account_name_from_the_server(api, app, owner):
    app.state.settings = make_settings(
        skribby_account_id=OTHER, skribby_account_name="Beyric Note Taker"
    )
    assert api.get(ROUTE).json()["meet_account_name"] == "Beyric Note Taker"
    assert api.put(ROUTE, {"bot_name": "Alex from Product"}).json()["meet_account_name"] == (
        "Beyric Note Taker"
    )
    app.state.settings = make_settings(skribby_account_id=OTHER)
    assert api.get(ROUTE).json()["meet_account_name"] is None


# ---- the consent line survives a refused name, and cannot name a bot that did not join ----


def test_on_a_name_fallback_the_notice_is_rebuilt_with_the_default_never_dropped(httpx_mock):
    # The notice holds the CLEANED AND DEFANGED name, which differs from the name sent.
    sent_name = "Alex.Coach"
    notice = chat_notice.notice_text(sent_name, "Ada Lovelace")
    assert "Alex[.]Coach" in notice and sent_name not in notice
    fallback = chat_notice.notice_text("Service Desk", "Ada Lovelace")
    refuse(httpx_mock, "bot_name invalid")
    accept(httpx_mock)
    provider, _ = make(bot_name="Service Desk")
    bot_id = provider.send_bot(
        URL, bot_name=sent_name, chat_notice=notice, chat_notice_fallback=fallback
    )
    first, second = posts(httpx_mock)
    assert first["initial_chat_message"] == notice and first["bot_name"] == sent_name
    assert second["bot_name"] == "Service Desk"
    assert second["initial_chat_message"] == fallback  # the consent line still goes out
    assert "Service Desk" in fallback and "Alex" not in second["initial_chat_message"]
    assert bot_id.bot_name == "Service Desk"


def test_a_placed_meeting_sends_the_rebuilt_notice_when_the_name_is_refused(session, httpx_mock):
    user = make_user(session, name="Ada Lovelace", bot_name="Alex.Coach")
    template = make_template(session, user)
    refuse(httpx_mock, "bot_name invalid")
    accept(httpx_mock)
    provider, _ = make(bot_name=DEFAULT)
    settings = make_settings(bot_chat_notice_enabled=True, skribby_bot_name=DEFAULT)
    meeting = place_bot(
        session, provider, request_for(user, template), now=datetime.now(UTC), settings=settings
    )
    first, second = posts(httpx_mock)
    assert first["initial_chat_message"].startswith("Hi, I'm Alex[.]Coach. Ada Lovelace added me")
    assert second["initial_chat_message"].startswith(f"Hi, I'm {DEFAULT}. Ada Lovelace added me")
    assert second["bot_name"] == DEFAULT and meeting.bot_name == DEFAULT


def test_no_fallback_key_when_the_default_is_what_the_notice_already_says():
    settings = make_settings(bot_chat_notice_enabled=True)
    plain = chat_notice.send_options(
        None, make()[0], 1, settings, bot_name="Service Desk", fallback_bot_name="Service Desk"
    )
    assert "chat_notice_fallback" not in plain
    other = chat_notice.send_options(
        None, make()[0], 1, settings, bot_name="Alex", fallback_bot_name="Service Desk"
    )
    assert other["chat_notice_fallback"].startswith("Hi, I'm Service Desk.")


# ---- dot look-alikes and joiners next to a dot, bot name and owner name alike ----

JOINERS = ("‍", "‌")


@pytest.mark.parametrize("joiner", JOINERS)
@pytest.mark.parametrize("tail", [".com", "。com", ".ai"])
def test_a_joiner_next_to_the_dot_does_not_hide_a_web_address_from_the_refusal(joiner, tail):
    for raw in (f"evil{joiner}{tail}", f"evil{tail[0]}{joiner}{tail[1:]}"):
        with pytest.raises(BotNameInvalid) as caught:
            clean_bot_name(raw)
        assert str(caught.value) == bot_names.HAS_LINK


@pytest.mark.parametrize("joiner", JOINERS)
def test_a_joiner_next_to_the_dot_does_not_survive_in_the_notice_for_either_name(joiner):
    text = chat_notice.notice_text(f"evil{joiner}.click", f"Ada evil{joiner}.com")
    assert "evil[.]click" in text and "evil[.]com" in text
    assert "evil.click" not in text and "evil.com" not in text


@pytest.mark.parametrize("dot", ["。", "．", "｡", "﹒", "․"])
def test_the_owner_name_dot_look_alikes_are_defanged_too(dot):
    text = chat_notice.notice_text("Service Desk", f"Ada evil{dot}com")
    assert "Ada evil[.]com added me" in text


def test_the_mail_cleaning_handles_look_alikes_but_leaves_cjk_sentences_alone():
    from app import titles

    assert titles.mail_line("see evil。com now", 80) == "see evil[.]com now"
    assert titles.mail_line("see evil‍.com now", 80) == "see evil[.]com now"
    assert titles.alert_name("Visit evil｡com", "zoom", "1") == "Visit evil[.]com"
    # A CJK full stop between two sentences is ordinary text: no listed domain follows it.
    assert titles.mail_line("你好。再见", 80) == "你好。再见"


def test_a_joiner_inside_an_emoji_sequence_is_kept_in_the_notice():
    family = "Team \U0001f468‍\U0001f469‍\U0001f467"
    assert chat_notice.notice_text(family, "Ada").startswith(f"Hi, I'm {family}. ")


# ---- hardening round 2: the consent line on every platform, joiners anywhere, "@", max_length ----

ZWJ = "‍"
PLATFORM_LINKS = {
    "google_meet": "https://meet.google.com/abc-defg-hij",
    "zoom": ZOOM,
    "teams": TEAMS,
}


@pytest.mark.parametrize("platform", sorted(PLATFORM_LINKS))
@pytest.mark.parametrize("typed", ["Alex.Coach", "A"])
def test_a_refused_name_never_costs_the_consent_line_on_any_platform(
    session, httpx_mock, platform, typed
):
    user = make_user(session, name="Ada Lovelace", bot_name=typed)
    template = make_template(session, user)
    refuse(httpx_mock, "bot_name invalid")
    accept(httpx_mock)
    provider, _ = make(bot_name="Service Desk")
    settings = make_settings(bot_chat_notice_enabled=True, skribby_bot_name="Service Desk")
    req = PlacementRequest(
        user_id=user.id,
        platform=platform,
        native_id="room-1",
        meeting_url=PLATFORM_LINKS[platform],
        template_id=template.id,
        source="manual",
    )
    meeting = place_bot(session, provider, req, now=datetime.now(UTC), settings=settings)
    sent = posts(httpx_mock)
    final = sent[-1]
    assert final["bot_name"] == "Service Desk" and meeting.bot_name == "Service Desk"
    # The consent line is there, names the bot that really joined, and still names the owner.
    assert final["initial_chat_message"].startswith("Hi, I'm Service Desk. Ada Lovelace added me")
    assert "If you'd rather not be recorded" in final["initial_chat_message"]


def test_a_direct_caller_without_a_fallback_keeps_a_notice_that_only_contains_the_letters(
    httpx_mock,
):
    # No fallback line given: a notice that does not NAME the refused name is kept, even when the
    # name is one letter that appears somewhere inside it ("Ada", "added", "rather").
    refuse(httpx_mock, "bot_name too odd")
    accept(httpx_mock)
    provider, _ = make(bot_name="Service Desk")
    notice = chat_notice.notice_text("Beyric Note Taker", "Ada")
    provider.send_bot(URL, bot_name="A", chat_notice=notice)
    _, second = posts(httpx_mock)
    assert second["bot_name"] == "Service Desk" and second["initial_chat_message"] == notice


def test_a_direct_caller_without_a_fallback_drops_a_notice_that_names_the_refused_name(
    httpx_mock,
):
    # The notice holds the cleaned and defanged name ("Alex[.]Coach"), not the typed one, so it
    # must be recognised as naming the bot without a plain substring match.
    refuse(httpx_mock, "bot_name too odd")
    accept(httpx_mock)
    provider, _ = make(bot_name="Service Desk")
    notice = chat_notice.notice_text("Alex.Coach", "Ada")
    provider.send_bot(URL, bot_name="Alex.Coach", chat_notice=notice)
    _, second = posts(httpx_mock)
    assert second["bot_name"] == "Service Desk" and "initial_chat_message" not in second


@pytest.mark.parametrize("tail", ["evil.c‍om", "evil.co‌m", "e‍vil.com", "evil.com".replace(".", ".‍")])
def test_a_joiner_anywhere_in_a_web_address_is_refused_as_a_bot_name(tail):
    with pytest.raises(BotNameInvalid) as caught:
        clean_bot_name(tail)
    assert str(caught.value) == bot_names.HAS_LINK


def test_a_joiner_anywhere_in_a_domain_is_defanged_in_the_notice_for_both_names():
    text = chat_notice.notice_text("evil.c‍lick", "Ada evil.c‌om")
    assert "evil[.]click" in text and "evil[.]com" in text
    assert ZWJ not in text and "‌" not in text


def test_a_name_with_a_joiner_and_no_domain_keeps_it_in_the_notice():
    family = "Team \U0001f468‍\U0001f469‍\U0001f467"
    text = chat_notice.notice_text(family, f"Ada {family}")
    assert text.count(family) == 3  # the bot once, the owner twice


def test_an_at_sign_is_written_out_in_the_notice_only():
    text = chat_notice.notice_text("@here", "@everyone Ada ＠all")
    assert "@" not in text and "＠" not in text
    assert "I'm (at)here." in text and "(at)everyone Ada (at)all added me" in text
    # The stored name stays as typed: only the line posted into the chat changes.
    assert clean_bot_name("@here") == "@here"


def test_the_name_models_have_a_generous_max_length_and_keep_their_own_codes(api, owner):
    # Between the rule (50) and the model's cap: still the plain `bot_name_invalid`.
    assert error_of(api.put(ROUTE, {"bot_name": "N" * 500}), 422, "bot_name_invalid")
    # Far beyond any name: refused by the model before any cleaning runs.
    huge = api.put(ROUTE, {"bot_name": "N" * 5000})
    assert error_of(huge, 422, "validation_error")["fields"][0]["field"] == "bot_name"
    assert error_of(api.put("/api/settings/name", {"name": "N" * 500}), 422, "name_invalid")
    huge = api.put("/api/settings/name", {"name": "N" * 5000})
    assert error_of(huge, 422, "validation_error")["fields"][0]["field"] == "name"


@pytest.mark.parametrize("mark", ["͏", "️", "︀"])
def test_an_invisible_mark_inside_a_web_address_cannot_hide_it(mark):
    # They draw nothing, so a person still reads "evil.com" while a regex sees a split word.
    with pytest.raises(BotNameInvalid) as caught:
        clean_bot_name(f"evil.c{mark}om")
    assert str(caught.value) == bot_names.HAS_LINK
    text = chat_notice.notice_text(f"evil.c{mark}lick", f"Ada evil.c{mark}om")
    assert "evil[.]click" in text and "evil[.]com" in text and mark not in text
