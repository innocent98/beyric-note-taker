"""The rule that sends our bot out of a call when only bots are left (`app/bot_presence.py`).

Meeting 38 (2026-11-16): every person left, a Fireflies note taker stayed, Skribby's own
last-person rule never fired, and our bot sat there for 33 minutes. These tests pin the pure
decision, the name heuristic (one-sided: unsure = person) and how Skribby's participant records are
read. Participant shapes follow the field list the live final bot object had in meeting 38
(`name`, `first_seen_at`, `last_seen_at`, `avatar`, `presence_intervals[{joined_at, left_at}]`,
`left_at`); the names here are placeholders.
"""

from datetime import UTC, datetime, timedelta

import pytest

from app.bot_presence import (
    NOBODY_CAME,
    ONLY_BOTS,
    Who,
    decide,
    is_bot_name,
    parse_time,
    read_participants,
    summarize,
)

NOW = datetime(2026, 11, 16, 12, 0, tzinfo=UTC)
ONLY = 300  # five minutes
WAIT = 900  # fifteen minutes
OWN = ("Beyric Meeting Support",)


def ago(**kw):
    return NOW - timedelta(**kw)


def go(people, in_since=None, now=NOW, own=OWN):
    return decide(people, in_since, now, only_bots_after=ONLY, nobody_after=WAIT, own_names=own)


def person(name="Ada Lovelace", left=None):
    return Who(name, left is None, left)


def bot(name="Fireflies.ai Notetaker Ada Lovelace"):
    return Who(name, True)


# ---- names: bots are recognised only by words that say so; unsure means person ----


@pytest.mark.parametrize(
    "name",
    [
        "MyWay's Assistant",  # meeting 38
        "Fireflies.ai Notetaker Adebayo Tosin",  # meeting 38
        "Otter.ai Notetaker",
        "Ada's Otter.ai Assistant",
        "Read.ai meeting notes",
        "Read AI",
        "tl;dv Recorder",
        "Fathom Notetaker",
        "Note Taker (Acme)",
        "note-taker",
        "Meeting Bot",
        "Zoom Bot",
        "AI Assistant",
        "Notes Assistant",
        "Meeting Assistant",
        "Bot",  # the whole name
        "Assistant",  # the whole name
        "Adebayo's Bot",
        "Ébène's Assistant",  # accented, possessive
        "FIREFLIES.AI NOTETAKER",
        "Avoma",
        "Krisp",
        "Supernormal Notes",
        "meetgeek",
        "Circleback",
        "Tactiq transcriber",
        "Beyric Meeting Support",  # our own, by the own-name rule below
    ],
)
def test_names_that_say_bot_are_bots(name):
    assert is_bot_name(name, OWN)


@pytest.mark.parametrize(
    "name",
    [
        "Ada Lovelace",
        "Adebayo Tosin",
        "Abbott Smith",  # contains "bot"
        "Robotham Jones",
        "Botha van der Merwe",  # a real surname
        "Bottomley",
        "Assistance Okafor",  # contains "assist"
        "Assistant Professor Jane Doe",  # a job title, not a bot
        "Marketing Assistant",
        "Sales Assistant",
        "HR Assistant",
        "Care Assistant",
        "Assistant Vice President",
        "Assistant Branch Manager",
        "Virtual Assistant Sam",
        "Jan Bot",  # a real surname
        "Bot Smith",
        "Ana Bot",
        "Ébot Nkwe",  # "bot" inside an accented name
        "Ébot",
        "Jane Doe, Executive Assistant",
        "Sam (Assistant Manager)",
        "Personal Assistant to the CEO",
        "Otterbein Hall",  # contains "otter"
        "Grain Elevator Co",  # not on the list: grain
        "Jamie Fellow",  # not on the list: jamie, fellow
        "Ai Nguyen",  # a given name
        "Dr. Read",
        "A. Note",
        "Fire Fly",
        "",
        "   ",
        "J",
    ],
)
def test_ordinary_names_are_people(name):
    assert not is_bot_name(name, OWN)


@pytest.mark.parametrize("name", [None, 7, ["Fireflies"], {"name": "Fireflies"}])
def test_a_name_that_is_not_text_is_a_person(name):
    assert not is_bot_name(name, OWN)


def test_our_own_bot_matches_by_its_whole_name_not_by_a_word():
    own = ("Adebayo's Scribe",)
    assert is_bot_name("Adebayo's Scribe", own)
    assert is_bot_name("adebayo’s  scribe", own)  # case, spacing and the curly apostrophe
    assert not is_bot_name("Adebayo", own)
    assert not is_bot_name("Scribe", own)
    assert not is_bot_name("Adebayo's Scribe Jr", own)
    assert not is_bot_name("Adebayo's Scribe", ())


# ---- reading Skribby's participant records ----


def ms(when):
    return int(when.timestamp() * 1000)


def record(name, joined, left=None, **extra):
    interval = {"joined_at": ms(joined)}
    if left is not None:
        interval["left_at"] = ms(left)
    return {
        "name": name,
        "first_seen_at": joined.isoformat(),
        "last_seen_at": ms(left or NOW),
        "avatar": None,
        "presence_intervals": [interval],
        "left_at": ms(left) if left else 0,
        **extra,
    }


def test_an_open_interval_is_in_the_call_and_a_closed_one_has_left_at_its_time():
    left = ago(minutes=7)
    people = read_participants(
        [record("Ada Lovelace", ago(minutes=30)), record("Bo Smith", ago(minutes=30), left)]
    )
    assert people[0].here and people[0].last_left is None
    assert not people[1].here and abs((people[1].last_left - left).total_seconds()) < 1


def test_the_last_interval_decides_so_someone_who_came_back_is_here():
    rec = record("Ada", ago(minutes=30), ago(minutes=20))
    rec["presence_intervals"].append({"joined_at": ms(ago(minutes=5))})
    rec["left_at"] = ms(ago(minutes=20))  # the record's own field is stale after a rejoin
    (who,) = read_participants([rec])
    assert who.here


def test_without_intervals_the_records_own_left_at_says_it():
    (gone,) = read_participants([{"name": "Ada", "left_at": ms(ago(minutes=9))}])
    (here,) = read_participants([{"name": "Ada", "left_at": 0}])
    (also_here,) = read_participants([{"name": "Ada"}])
    assert not gone.here and gone.last_left is not None
    assert here.here and also_here.here


@pytest.mark.parametrize(
    "left",
    ["not a time", "", [], {}, True, -5, 10**30],
)
def test_a_leaving_time_that_cannot_be_read_counts_as_here(left):
    (who,) = read_participants([{"name": "Ada", "presence_intervals": [{"left_at": left}]}])
    assert who.here


def test_a_broken_entry_is_a_person_who_is_here():
    people = read_participants(["x", None, {"name": 5, "presence_intervals": "?"}, {"name": "Ada"}])
    assert [who.here for who in people] == [True, True, True, True]
    assert all(not is_bot_name(who.name) for who in people)


@pytest.mark.parametrize("raw", [None, "x", {}, 5])
def test_no_list_is_not_judged(raw):
    assert read_participants(raw) is None


def test_times_read_as_iso_text_or_as_milliseconds_or_seconds():
    iso = parse_time("2026-11-16T12:00:00Z")
    assert iso == NOW
    assert parse_time(ms(NOW)) == NOW
    assert parse_time(int(NOW.timestamp())) == NOW
    assert parse_time("2026-11-16T12:00:00") == NOW  # no zone: taken as UTC
    for bad in (None, 0, "", "  ", "soon", True, [], float("inf")):
        assert parse_time(bad) is None


# ---- the decision ----


def test_only_bots_for_long_enough_after_a_person_leaves_means_leave():
    people = [person("Ada", ago(minutes=6)), bot()]
    assert go(people) == ONLY_BOTS


def test_not_yet_long_enough_means_stay():
    people = [person("Ada", ago(minutes=4, seconds=59)), bot()]
    assert go(people) is None


def test_the_edge_is_inclusive():
    assert go([person("Ada", ago(seconds=ONLY)), bot()]) == ONLY_BOTS
    assert go([person("Ada", ago(seconds=ONLY - 1)), bot()]) is None


def test_one_person_still_in_the_call_means_stay_whoever_else_left():
    people = [person("Ada", ago(hours=1)), person("Bo"), bot()]
    assert go(people) is None


def test_the_wait_runs_from_the_last_person_to_leave():
    people = [person("Ada", ago(hours=1)), person("Bo", ago(minutes=2)), bot()]
    assert go(people) is None  # Bo left two minutes ago
    people = [person("Ada", ago(hours=1)), person("Bo", ago(minutes=8)), bot()]
    assert go(people) == ONLY_BOTS


def test_a_person_who_comes_back_resets_it():
    gone = [person("Ada", ago(minutes=20)), bot()]
    assert go(gone) == ONLY_BOTS
    back = [Who("Ada", True), bot()]  # the last interval is open again
    assert go(back) is None
    left_again = [person("Ada", ago(minutes=1)), bot()]
    assert go(left_again) is None  # the wait starts from the second leaving


def test_a_person_with_an_unreadable_leaving_time_is_never_judged():
    people = [Who("Ada", False, None), bot()]
    assert go(people) is None
    assert go([Who("Ada", False, None), person("Bo", ago(hours=1)), bot()]) is None


def test_bots_alone_with_no_person_ever_waits_for_the_nobody_came_window():
    people = [bot(), Who("Beyric Meeting Support", True)]
    assert go(people, in_since=ago(minutes=14, seconds=59)) is None
    assert go(people, in_since=ago(seconds=WAIT)) == NOBODY_CAME
    assert go(people, in_since=ago(hours=2)) == NOBODY_CAME


def test_bot_first_rule_a_never_fires_before_a_person_has_been_seen():
    people = [bot(), bot("MyWay's Assistant")]
    # Only bots, for hours, but no person was ever there: rule A (ONLY_BOTS) is not the answer.
    assert go(people, in_since=ago(minutes=10)) is None
    assert go(people, in_since=ago(minutes=10), now=NOW) is None


def test_a_person_arriving_before_the_window_ends_ends_the_nobody_came_wait():
    people = [bot(), Who("Ada Lovelace", True)]
    assert go(people, in_since=ago(hours=1)) is None


def test_nobody_came_waits_while_the_time_the_bot_got_in_is_unknown():
    assert go([bot()], in_since=None) is None


def test_no_list_or_an_empty_list_is_never_judged():
    # An empty list is what a provider that does not fill the list while the call runs would show
    # for every call: it must not end a call with people in it.
    assert go(None, in_since=ago(hours=3)) is None
    assert go([], in_since=ago(hours=3)) is None


def test_our_own_bot_counts_as_a_bot_by_its_name():
    people = [person("Ada", ago(minutes=9)), Who("Beyric Meeting Support", True)]
    assert go(people) == ONLY_BOTS
    # With another own name the same entry is a person here, so the bot stays.
    assert go(people, own=("Something Else",)) is None


def test_an_unrecognised_name_is_a_person_so_the_bot_stays():
    people = [person("Ada", ago(minutes=9)), Who("Sam's Scribe", True)]
    assert go(people, own=()) is None


def test_a_name_that_contains_bot_or_assistant_is_a_person_and_keeps_the_bot_there():
    for name in ("Abbott Smith", "Assistant Professor Jane Doe"):
        people = [person("Ada", ago(minutes=9)), Who(name, True)]
        assert go(people) is None


def test_meeting_38_shape_would_have_ended_the_call_five_minutes_after_the_last_person():
    # Real names from meeting 38 (bots), people with placeholder names; they left 56 minutes in,
    # the bots stayed another 33.
    people = [
        person("Person One", ago(minutes=33)),
        person("Person Two", ago(minutes=35)),
        Who("Fireflies.ai Notetaker Adebayo Tosin", True),
        Who("MyWay's Assistant", True),
        Who("Beyric Meeting Support", True),
    ]
    assert go(people) == ONLY_BOTS
    # Person One was the last to go (33 minutes before NOW): the five minutes end at NOW - 28 min.
    assert go(people, now=NOW - timedelta(minutes=28)) == ONLY_BOTS
    assert go(people, now=NOW - timedelta(minutes=28, seconds=1)) is None


def test_any_open_interval_is_here_whatever_order_skribby_lists_them_in():
    closed = {"joined_at": ms(ago(minutes=50)), "left_at": ms(ago(minutes=40))}
    open_one = {"joined_at": ms(ago(minutes=5))}
    for intervals in ([closed, open_one], [open_one, closed]):
        (who,) = read_participants([{"name": "Ada", "presence_intervals": intervals}])
        assert who.here


def test_all_closed_intervals_leave_at_the_latest_left_at_in_any_order():
    early = {"joined_at": ms(ago(minutes=50)), "left_at": ms(ago(minutes=40))}
    late = {"joined_at": ms(ago(minutes=30)), "left_at": ms(ago(minutes=20))}
    for intervals in ([early, late], [late, early]):
        (who,) = read_participants([{"name": "Ada", "presence_intervals": intervals}])
        assert not who.here
        assert abs((who.last_left - ago(minutes=20)).total_seconds()) < 1


def test_one_unreadable_closed_interval_makes_the_person_here():
    early = {"joined_at": ms(ago(minutes=50)), "left_at": "garbled"}
    late = {"joined_at": ms(ago(minutes=30)), "left_at": ms(ago(minutes=20))}
    (who,) = read_participants([{"name": "Ada", "presence_intervals": [early, late]}])
    assert who.here


def test_the_counts_for_the_log_hold_numbers_only():
    people = [Who("Ada", True), Who("MyWay's Assistant", True), Who("Bo", False, NOW)]
    count = summarize(people, OWN)
    assert (count.entries, count.here, count.bot_named) == (3, 2, 1)
    assert summarize(None) == summarize([])
