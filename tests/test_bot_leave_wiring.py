"""The worker tells the bot to leave when only bots are left (`pipeline._leave_if_alone`), how a
Skribby bot body becomes the participant list the rule reads, what the bot is sent with, and how a
call nobody came to ends. The decision itself is tested in `tests/test_bot_presence.py`."""

from datetime import timedelta

import pytest

from app import audio, pipeline, services
from app.bot_presence import Who
from app.config import Settings
from app.skribby import SkribbyClient, map_bot
from app.vexa import VexaError, VexaMeeting
from tests.test_pipeline import (  # noqa: F401  (fixtures used by name)
    DONE,
    NOW,
    FakeVexa,
    env,
    meeting,
    run,
)
from tests.test_web import owner  # noqa: F401

OWN = "Beyric Note Taker (recording)"  # the default name for the test meeting's provider
IN = NOW - timedelta(minutes=90)


def alone(left_minutes_ago=10, **kw):
    """A bot in the call with one person who left that long ago and two other bots."""
    people = [
        Who("Ada Lovelace", False, NOW - timedelta(minutes=left_minutes_ago)),
        Who("Fireflies.ai Notetaker Ada Lovelace", True),
        Who(OWN, True),
    ]
    return VexaMeeting(status="active", participants=people, in_since=IN, **kw)


def only_bots_never_a_person(**kw):
    return VexaMeeting(
        status="active",
        participants=[Who("Fireflies.ai Notetaker", True), Who(OWN, True)],
        in_since=kw.pop("in_since", NOW - timedelta(minutes=20)),
        **kw,
    )


@pytest.fixture
def settings(monkeypatch, tmp_path):
    def apply(**fields):
        value = Settings(audio_dir=str(tmp_path / "a"), **fields)
        monkeypatch.setattr(pipeline, "get_settings", lambda: value)
        return value

    return apply


# ---- the worker ----


def test_only_bots_left_after_a_person_stops_the_bot_and_has_the_notes_written(
    session, meeting, env
):
    vexa = FakeVexa(alone())
    run(session, meeting, vexa)
    assert vexa.stopped == [("google_meet", "abc-defg-hij")]
    assert meeting.status == "bot_sent"
    assert meeting.completion_reason == pipeline.BOT_END_REQUESTED
    assert services.status_text(meeting, NOW) == services.BOT_ENDING_TEXT
    # The bot is gone a poll later: the recording is used as for an owner's "End now".
    run(session, meeting, FakeVexa(DONE), now=NOW + timedelta(seconds=30))
    assert meeting.status == "ready"


def test_the_bot_is_stopped_once_not_on_every_poll(session, meeting, env):
    vexa = FakeVexa(alone())
    run(session, meeting, vexa)
    run(session, meeting, vexa, now=NOW + timedelta(seconds=30))
    assert len(vexa.stopped) == 1


def test_a_person_who_left_a_moment_ago_does_not_send_the_bot_away(session, meeting, env):
    vexa = FakeVexa(alone(left_minutes_ago=2))
    run(session, meeting, vexa)
    assert not hasattr(vexa, "stopped")
    assert meeting.completion_reason != pipeline.BOT_END_REQUESTED


def test_a_person_still_in_the_call_keeps_the_bot_there(session, meeting, env):
    people = [Who("Ada Lovelace", True), Who("Fireflies.ai Notetaker", True)]
    vexa = FakeVexa(VexaMeeting(status="active", participants=people, in_since=IN))
    run(session, meeting, vexa)
    assert not hasattr(vexa, "stopped")


def test_a_provider_that_gives_no_list_changes_nothing(session, meeting, env):
    vexa = FakeVexa(VexaMeeting(status="active"), VexaMeeting(status="active", participants=[]))
    run(session, meeting, vexa)
    run(session, meeting, vexa, now=NOW + timedelta(hours=3))
    assert not hasattr(vexa, "stopped")


def test_bot_first_waits_for_the_nobody_came_window_then_leaves(session, meeting, env):
    early = FakeVexa(only_bots_never_a_person(in_since=NOW - timedelta(minutes=14)))
    run(session, meeting, early)
    assert not hasattr(early, "stopped")
    late = FakeVexa(only_bots_never_a_person(in_since=NOW - timedelta(minutes=16)))
    run(session, meeting, late, now=NOW + timedelta(seconds=30))
    assert late.stopped == [("google_meet", "abc-defg-hij")]
    assert meeting.completion_reason == pipeline.BOT_NOBODY_CAME
    assert services.status_text(meeting, NOW + timedelta(seconds=30)) == (
        "The bot is leaving: nobody else joined the meeting"
    )
    assert not services.can_cancel(meeting)


FINISHED = VexaMeeting(
    status="completed",
    recording_id=7,
    media_id=9,
    recording_status="completed",
    completion_reason="stopped",
)


def marked_nobody_came(session, meeting):
    meeting.completion_reason = pipeline.BOT_NOBODY_CAME
    session.commit()


def test_a_recording_with_no_speech_ends_a_nobody_came_call_calmly_and_it_can_be_retried(
    session, meeting, env, monkeypatch
):
    def silent(source, out_dir):
        raise audio.NoSpeechError("silent")

    monkeypatch.setattr(audio, "prepare_recording", silent)
    marked_nobody_came(session, meeting)
    vexa = FakeVexa(FINISHED)
    run(session, meeting, vexa, now=NOW + timedelta(minutes=1))
    assert meeting.status == "failed"
    assert meeting.failure_reason == pipeline.REASON_NOBODY_CAME
    assert services.status_text(meeting) == "Nobody joined"
    assert services.status_tone(meeting) == "neutral"
    assert "failed" not in meeting.failure_reason.lower()
    # The recording was fetched and checked, never deleted on the strength of the list, and the
    # owner can try again.
    assert vexa.downloads == 1
    assert not getattr(vexa, "recording_deletes", [])
    assert services.retry_block(meeting) is None


def test_a_person_who_turns_up_after_the_marker_still_gets_their_notes(session, meeting, env):
    # The list was wrong (or someone arrived as the bot left): the recording has speech in it.
    marked_nobody_came(session, meeting)
    run(session, meeting, FakeVexa(DONE), now=NOW + timedelta(minutes=1))
    assert meeting.status == "ready"
    assert meeting.transcript
    assert meeting.failure_reason is None


def test_a_nobody_came_call_with_no_recording_at_all_says_so_in_calm_words(session, meeting, env):
    marked_nobody_came(session, meeting)
    gone = VexaMeeting(status="completed", completion_reason="stopped")
    for poll in range(1, pipeline.NO_AUDIO_GRACE_POLLS + 1):
        run(session, meeting, FakeVexa(gone), now=NOW + timedelta(minutes=poll))
    assert meeting.status == "failed"
    assert meeting.failure_reason == pipeline.REASON_NOBODY_CAME
    assert services.status_text(meeting) == "Nobody joined"


def test_a_stop_that_keeps_failing_never_writes_the_marker(session, meeting, env):
    vexa = FakeVexa(only_bots_never_a_person(in_since=NOW - timedelta(hours=1)))
    vexa.stop_error = VexaError("stop_bot failed: HTTP 500")
    for poll in range(pipeline.STOP_TRIES + 3):  # well past the tries `_stop_bot` gives up after
        run(session, meeting, vexa, now=NOW + timedelta(seconds=30 * poll))
        assert meeting.completion_reason != pipeline.BOT_NOBODY_CAME
        assert meeting.status == "bot_sent"
    assert len(vexa.stopped) == pipeline.STOP_TRIES + 3  # it kept trying
    vexa.stop_error = None
    run(session, meeting, vexa, now=NOW + timedelta(minutes=10))
    assert meeting.completion_reason == pipeline.BOT_NOBODY_CAME


def test_a_bot_that_is_not_this_meetings_to_stop_gets_no_marker(session, meeting, env, monkeypatch):
    monkeypatch.setattr(pipeline.placement, "may_stop_bot", lambda session, meeting: False)
    vexa = FakeVexa(only_bots_never_a_person(in_since=NOW - timedelta(hours=1)))
    run(session, meeting, vexa)
    assert not hasattr(vexa, "stopped")
    assert meeting.completion_reason != pipeline.BOT_NOBODY_CAME


def test_the_first_look_at_the_list_is_logged_once_as_counts_even_when_nothing_is_done(
    session, meeting, env, caplog
):
    pipeline._presence_logged.clear()
    caplog.set_level("INFO", logger="app.pipeline")
    people = [Who("Ada Lovelace", True), Who("Fireflies.ai Notetaker", True)]
    seen = VexaMeeting(status="active", participants=people, in_since=IN)
    vexa = FakeVexa(seen)
    run(session, meeting, vexa)
    run(session, meeting, vexa, now=NOW + timedelta(seconds=30))
    lines = [r.getMessage() for r in caplog.records if "who is in the call" in r.getMessage()]
    assert len(lines) == 1
    assert "entries=2 here=2 bot_named=1 in_since=set rule=none" in lines[0]
    assert "list=present" in lines[0]
    assert "Ada" not in lines[0] and "Fireflies" not in lines[0]


def test_a_missing_list_is_logged_once_and_the_filled_list_once_more(session, meeting, env, caplog):
    pipeline._presence_logged.clear()
    caplog.set_level("INFO", logger="app.pipeline")
    run(session, meeting, FakeVexa(VexaMeeting(status="active")))
    run(session, meeting, FakeVexa(VexaMeeting(status="active")), now=NOW + timedelta(seconds=30))
    people = [Who("Ada Lovelace", True)]
    filled = VexaMeeting(status="active", participants=people, in_since=IN)
    run(session, meeting, FakeVexa(filled), now=NOW + timedelta(seconds=60))
    lines = [r.getMessage() for r in caplog.records if "who is in the call" in r.getMessage()]
    assert len(lines) == 2
    assert "list=missing entries=0" in lines[0] and "in_since=unset" in lines[0]
    assert "list=present entries=1" in lines[1]


def test_the_leave_windows_are_never_below_a_minute_and_a_zero_does_not_crash_start():
    assert Settings(bot_nobody_came_seconds=0).bot_nobody_came_seconds == 60
    assert Settings(bot_only_bots_leave_seconds=5).bot_only_bots_leave_seconds == 60
    assert Settings(bot_only_bots_leave_seconds=120).bot_only_bots_leave_seconds == 120


def test_other_failures_still_read_failed_and_danger(session, meeting, env):
    meeting.status, meeting.failure_reason = "failed", pipeline.REASON_NO_SPEECH
    assert services.status_text(meeting) == "Failed"
    assert services.status_tone(meeting) == "danger"


def test_the_kill_switch_turns_the_whole_rule_off(session, meeting, env, settings):
    settings(bot_leave_when_only_bots=False)
    vexa = FakeVexa(alone())
    run(session, meeting, vexa)
    assert not hasattr(vexa, "stopped")
    vexa = FakeVexa(only_bots_never_a_person(in_since=NOW - timedelta(hours=2)))
    run(session, meeting, vexa)
    assert not hasattr(vexa, "stopped")


def test_the_windows_are_the_settings(session, meeting, env, settings):
    settings(bot_only_bots_leave_seconds=900)
    vexa = FakeVexa(alone(left_minutes_ago=10))
    run(session, meeting, vexa)
    assert not hasattr(vexa, "stopped")  # ten minutes is under the fifteen asked for


def test_the_owners_own_end_request_is_not_overwritten(session, meeting, env):
    meeting.completion_reason = pipeline.BOT_END_REQUESTED
    session.commit()
    vexa = FakeVexa(alone())
    run(session, meeting, vexa)
    assert not hasattr(vexa, "stopped")


def test_a_stop_that_fails_is_tried_again_and_never_fails_the_meeting(session, meeting, env):
    vexa = FakeVexa(alone())
    vexa.stop_error = VexaError("stop_bot failed: HTTP 500")
    run(session, meeting, vexa)
    assert meeting.status == "bot_sent"
    assert meeting.completion_reason != pipeline.BOT_END_REQUESTED  # not marked as told
    vexa.stop_error = None
    run(session, meeting, vexa, now=NOW + timedelta(seconds=30))
    assert meeting.completion_reason == pipeline.BOT_END_REQUESTED


def test_the_leave_log_holds_a_count_and_the_rule_never_a_name(session, meeting, env, caplog):
    caplog.set_level("INFO", logger="app.pipeline")
    run(session, meeting, FakeVexa(alone()))
    text = " ".join(record.getMessage() for record in caplog.records)
    assert "only_bots" in text and "3 in the call" in text
    for name in ("Ada", "Fireflies", OWN):
        assert name not in text


# ---- Skribby's body into the list ----


def skribby_body(status, *, participants, steps):
    def at(minutes_ago):
        return (NOW - timedelta(minutes=minutes_ago)).isoformat().replace("+00:00", "Z")

    return {
        "id": "0b0c3b0e-0000-4000-8000-000000000001",
        "service": "gmeet",
        "status": status,
        "participants": participants,
        "events": [
            {
                "event": "status_update",
                "data": {"old_status": "x", "new_status": name},
                "created_at": at(minutes_ago),
            }
            for name, minutes_ago in steps
        ],
    }


def ms(minutes_ago):
    return int((NOW - timedelta(minutes=minutes_ago)).timestamp() * 1000)


def test_a_recording_bot_carries_who_is_in_the_call_and_when_it_got_in():
    body = skribby_body(
        "recording",
        participants=[
            {
                "name": "Ada Lovelace",
                "first_seen_at": (NOW - timedelta(minutes=60)).isoformat(),
                "last_seen_at": ms(30),
                "avatar": None,
                "presence_intervals": [{"joined_at": ms(60), "left_at": ms(30)}],
                "left_at": ms(30),
            },
            {"name": "MyWay's Assistant", "presence_intervals": [{"joined_at": ms(60)}]},
        ],
        steps=[("booting", 70), ("joining", 65), ("waiting_to_record", 61), ("recording", 60)],
    )
    info = map_bot(body, NOW)
    assert info.status == "active"
    assert info.in_since == NOW - timedelta(minutes=61)
    assert [(who.name, who.here) for who in info.participants] == [
        ("Ada Lovelace", False),
        ("MyWay's Assistant", True),
    ]


@pytest.mark.parametrize("raw", ["joining", "booting", "breakout_waiting_room", "leaving"])
def test_a_bot_that_is_not_in_the_call_carries_no_list(raw):
    body = skribby_body(raw, participants=[{"name": "Ada"}], steps=[("joining", 5)])
    assert map_bot(body, NOW).participants is None


def test_a_body_with_no_participants_field_or_no_events_leaves_the_rule_idle():
    body = skribby_body("recording", participants=None, steps=[])
    del body["participants"]
    info = map_bot(body, NOW)
    assert info.participants is None and info.in_since is None


def test_a_participants_field_that_is_not_a_list_is_not_judged():
    body = skribby_body("recording", participants="nope", steps=[("recording", 5)])
    assert map_bot(body, NOW).participants is None


# ---- what the bot is sent with ----


def payload(**options):
    client = SkribbyClient(api_key="k" * 12, **options)
    return client.payload("google_meet", "abc-defg-hij", "https://meet.google.com/abc-defg-hij")[
        "stop_options"
    ]


def test_the_nobody_came_window_is_sent_as_skribbys_empty_meeting_timeout():
    assert payload(nobody_came_seconds=900)["empty_meeting_timeout"] == 15
    assert payload(nobody_came_seconds=61)["empty_meeting_timeout"] == 2  # rounded up


def test_with_the_rule_off_nothing_new_is_sent_and_skribbys_default_applies():
    assert "empty_meeting_timeout" not in payload(nobody_came_seconds=0)
    assert "empty_meeting_timeout" not in payload()


def test_the_settings_reach_the_bot_that_is_sent(monkeypatch):
    from app.skribby import SkribbyProvider

    base = {"skribby_api_key": "k" * 12}
    on = SkribbyProvider.from_settings(Settings(**base))
    off = SkribbyProvider.from_settings(Settings(**base, bot_leave_when_only_bots=False))
    url = "https://meet.google.com/abc-defg-hij"
    assert (
        on._client.payload("google_meet", "abc-defg-hij", url)["stop_options"][
            "empty_meeting_timeout"
        ]
        == 15
    )
    assert (
        "empty_meeting_timeout"
        not in off._client.payload("google_meet", "abc-defg-hij", url)["stop_options"]
    )


def test_the_defaults_are_on_five_minutes_and_fifteen_minutes():
    s = Settings()
    assert s.bot_leave_when_only_bots is True
    assert (s.bot_only_bots_leave_seconds, s.bot_nobody_came_seconds) == (300, 900)
