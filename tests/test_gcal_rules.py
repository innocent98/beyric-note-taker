import itertools
from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest

from app.config import Settings
from app.gcal_events import ParsedEvent
from app.gcal_rules import JoinDecision, JoinSettings, will_join

NOW = datetime(2026, 11, 4, 8, 0, tzinfo=UTC)
ANY = object()

BASE = ParsedEvent(
    source_event_id="evt-1",
    recurring_event_id=None,
    title="Planning",
    starts_at=NOW + timedelta(hours=1),
    ends_at=NOW + timedelta(hours=2),
    platform="google_meet",
    meeting_url="https://meet.google.com/abc-defg-hij",
    native_id="abc-defg-hij",
    organizer_self=True,
    attendee_count=3,
    self_response="accepted",
    status="confirmed",
    event_type="default",
    all_day=False,
)


def make(**fields) -> ParsedEvent:
    return replace(BASE, **fields)


def settings(rule="all", teams=False) -> JoinSettings:
    return JoinSettings(rule=rule, teams_join_enabled=teams)


# ---- the decision table (spec 7.5) ----
#
# Rows are read top to bottom and the FIRST row whose cells all match gives the answer: the order
# is the priority. `ANY` matches every value. A platform cell is (platform, teams flag on).

MEET, ZOOM = ("google_meet", False), ("zoom", False)
TEAMS_ON, TEAMS_OFF, NO_LINK = ("teams", True), ("teams", False), (None, False)

# rule, organiser, response, platform, override -> (join, reason)
TABLE = [
    (ANY, ANY, "declined", ANY, ANY, (False, "declined")),
    (ANY, ANY, ANY, NO_LINK, ANY, (False, "no_link")),
    (ANY, ANY, ANY, TEAMS_OFF, ANY, (False, "teams_unsupported")),
    (ANY, ANY, ANY, ANY, False, (False, "override_off")),
    (ANY, ANY, ANY, ANY, True, (True, "ok")),
    ("none", ANY, ANY, ANY, None, (False, "rule_none")),
    ("organizer", True, ANY, ANY, None, (True, "ok")),
    ("organizer", False, ANY, ANY, None, (False, "not_organizer")),
    ("organizer", None, ANY, ANY, None, (False, "not_organizer")),
    # An invite nobody has answered is joined only when it is the user's own meeting (the organiser
    # is the user) or comes from their own organisation; a stranger's invite waits for a switch.
    ("all", False, "needsAction", ANY, None, (False, "not_accepted")),
    ("all", None, "needsAction", ANY, None, (False, "not_accepted")),
    ("all", ANY, ANY, ANY, None, (True, "ok")),
]


def expected(rule, organizer, response, platform, override):
    for t_rule, t_org, t_resp, t_platform, t_override, answer in TABLE:
        cells = ((t_rule, rule), (t_org, organizer), (t_resp, response))
        cells += ((t_platform, platform), (t_override, override))
        if all(want is ANY or want == got for want, got in cells):
            return answer
    raise AssertionError("the table does not cover this combination")


RULES = ["all", "organizer", "none"]
ORGANIZERS = [True, False, None]
RESPONSES = ["accepted", "tentative", "needsAction", "declined", None]
PLATFORMS = [MEET, ZOOM, TEAMS_ON, TEAMS_OFF, NO_LINK]
OVERRIDES = [None, True, False]


@pytest.mark.parametrize(
    ("rule", "organizer", "response", "platform", "override"),
    list(itertools.product(RULES, ORGANIZERS, RESPONSES, PLATFORMS, OVERRIDES)),
)
def test_will_join_matrix(rule, organizer, response, platform, override):
    event = make(organizer_self=organizer, self_response=response, platform=platform[0])
    got = will_join(event, override, settings(rule, platform[1]), now=NOW)
    assert got == JoinDecision(*expected(rule, organizer, response, platform, override))


def test_the_matrix_spot_checks_read_as_the_spec_says():
    # A few rows written out by hand, so the table above is not the only statement of the rules.
    cases = [
        (make(), None, settings("all"), True, "ok"),
        (make(), None, settings("none"), False, "rule_none"),
        (make(), True, settings("none"), True, "ok"),  # the switch beats the rule, both ways
        (make(), False, settings("all"), False, "override_off"),
        (make(organizer_self=False), None, settings("organizer"), False, "not_organizer"),
        (make(organizer_self=True), None, settings("organizer"), True, "ok"),
        (make(self_response="tentative"), None, settings("all"), True, "ok"),
        (make(self_response="needsAction"), None, settings("all"), True, "ok"),  # you organise it
        (
            make(self_response="needsAction", organizer_self=False),
            None,
            settings("all"),
            False,
            "not_accepted",
        ),
        (make(platform=None), None, settings("all"), False, "no_link"),
        (make(platform="teams"), None, settings("all", teams=False), False, "teams_unsupported"),
        (make(platform="teams"), None, settings("all", teams=True), True, "ok"),
        (make(platform="zoom"), None, settings("all"), True, "ok"),
    ]
    for event, override, join_settings, join, reason in cases:
        assert will_join(event, override, join_settings, now=NOW) == JoinDecision(join, reason)


# ---- unanswered invites (a stranger must not be able to send the bot) ----


def test_an_unanswered_invite_from_the_users_own_organisation_is_joined():
    invite = make(self_response="needsAction", organizer_self=False, organizer_same_org=True)
    assert will_join(invite, None, settings("all"), now=NOW) == JoinDecision(True, "ok")
    stranger = make(self_response="needsAction", organizer_self=False, organizer_same_org=False)
    assert will_join(stranger, None, settings("all"), now=NOW) == JoinDecision(
        False, "not_accepted"
    )


@pytest.mark.parametrize("response", ["accepted", "tentative", None])
def test_only_an_unanswered_invite_waits_for_the_user(response):
    event = make(self_response=response, organizer_self=False, organizer_same_org=False)
    assert will_join(event, None, settings("all"), now=NOW) == JoinDecision(True, "ok")


def test_the_one_click_override_joins_an_unanswered_invite_from_a_stranger():
    stranger = make(self_response="needsAction", organizer_self=False, organizer_same_org=False)
    assert will_join(stranger, True, settings("all"), now=NOW) == JoinDecision(True, "ok")
    assert will_join(stranger, None, settings("none"), now=NOW).reason == "rule_none"


# ---- what an override can never beat ----


def test_override_true_beats_rule_none_but_not_declined_or_ended():
    assert will_join(make(), True, settings("none"), now=NOW) == JoinDecision(True, "ok")
    declined = make(self_response="declined")
    assert will_join(declined, True, settings("all"), now=NOW) == JoinDecision(False, "declined")
    ended = make(starts_at=NOW - timedelta(hours=2), ends_at=NOW - timedelta(minutes=1))
    assert will_join(ended, True, settings("all"), now=NOW) == JoinDecision(False, "ended")


@pytest.mark.parametrize(
    ("fields", "reason"),
    [
        ({"status": "cancelled"}, "cancelled"),
        ({"all_day": True}, "all_day"),
        ({"event_type": "focusTime"}, "event_type"),
        ({"event_type": "outOfOffice"}, "event_type"),
        ({"event_type": "workingLocation"}, "event_type"),
        ({"event_type": "birthday"}, "event_type"),
        ({"platform": None, "meeting_url": None, "native_id": None}, "no_link"),
    ],
)
def test_nothing_the_user_switches_on_makes_an_unjoinable_event_joinable(fields, reason):
    for override in (None, True):
        got = will_join(make(**fields), override, settings("all"), now=NOW)
        assert got == JoinDecision(False, reason)


def test_a_tentative_status_is_not_a_cancellation():
    assert will_join(make(status="tentative"), None, settings("all"), now=NOW).join is True


# ---- time ----


def test_ended_event_is_not_joined_and_the_instant_it_ends_counts_as_ended():
    just_ended = make(starts_at=NOW - timedelta(hours=1), ends_at=NOW)
    assert will_join(just_ended, None, settings(), now=NOW) == JoinDecision(False, "ended")
    running = make(starts_at=NOW - timedelta(hours=1), ends_at=NOW + timedelta(seconds=1))
    assert will_join(running, None, settings(), now=NOW) == JoinDecision(True, "ok")  # in progress


def test_event_longer_than_six_hours_is_too_long():
    six = make(ends_at=BASE.starts_at + timedelta(hours=6))
    assert will_join(six, None, settings(), now=NOW) == JoinDecision(True, "ok")
    over = make(ends_at=BASE.starts_at + timedelta(hours=6, seconds=1))
    assert will_join(over, None, settings(), now=NOW) == JoinDecision(False, "too_long")
    assert will_join(over, True, settings(), now=NOW) == JoinDecision(False, "too_long")


def test_solo_event_with_link_joins_under_rule_all():
    solo = make(attendee_count=0, organizer_self=True, self_response=None)
    assert will_join(solo, None, settings("all"), now=NOW) == JoinDecision(True, "ok")
    no_organizer = make(attendee_count=0, organizer_self=None, self_response=None)
    assert will_join(no_organizer, None, settings("all"), now=NOW) == JoinDecision(True, "ok")


# ---- the contract ----


def test_the_reasons_are_exactly_the_documented_set():
    seen = {
        will_join(event, override, join_settings, now=NOW).reason
        for event, override, join_settings in [
            (make(), None, settings("all")),
            (make(platform=None), None, settings()),
            (make(platform="teams"), None, settings()),
            (make(self_response="declined"), None, settings()),
            (make(status="cancelled"), None, settings()),
            (make(ends_at=NOW - timedelta(minutes=1)), None, settings()),
            (make(ends_at=BASE.starts_at + timedelta(hours=7)), None, settings()),
            (make(all_day=True), None, settings()),
            (make(event_type="focusTime"), None, settings()),
            (make(organizer_self=False), None, settings("organizer")),
            (make(organizer_self=False, self_response="needsAction"), None, settings("all")),
            (make(), None, settings("none")),
            (make(), False, settings()),
        ]
    }
    assert seen == {
        "ok",
        "no_link",
        "teams_unsupported",
        "declined",
        "cancelled",
        "ended",
        "too_long",
        "all_day",
        "event_type",
        "not_organizer",
        "not_accepted",
        "rule_none",
        "override_off",
    }


def test_an_unknown_rule_is_refused_rather_than_guessed():
    with pytest.raises(ValueError, match="rule"):
        will_join(make(), None, settings("sometimes"), now=NOW)


def test_it_accepts_a_stored_event_row_not_only_a_parsed_event():
    from types import SimpleNamespace

    row = SimpleNamespace(**{name: getattr(BASE, name) for name in BASE.__dataclass_fields__})
    assert will_join(row, None, settings(), now=NOW) == JoinDecision(True, "ok")


def test_teams_join_enabled_defaults_to_false_in_config():
    assert Settings(_env_file=None).teams_join_enabled is False
