"""`scripts/ai_cost_report.py`: the arithmetic on a small made-up fixture, and the read-only rule.

The report prices `ai_runs` rows from tokens and audio seconds (the table has no cost column),
adds labelled estimates for the Skribby bot and ElevenLabs, and must never print a person's text or
name. Only that script's own functions are tested here.
"""

import importlib.util
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from sqlalchemy.exc import DBAPIError

from app.models import AiRun, Meeting, Team, TeamMember, Template, UsageEntry, User

_spec = importlib.util.spec_from_file_location(
    "ai_cost_report", Path(__file__).parent.parent / "scripts" / "ai_cost_report.py"
)
report = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(report)

NOW = datetime(2026, 11, 14, 12, 0, tzinfo=UTC)
AFTER_FLIP = datetime(2026, 11, 14, 8, 0, tzinfo=UTC)
BEFORE_FLIP = datetime(2026, 11, 10, 8, 0, tzinfo=UTC)
HOUR = 3600


def run(**kw):
    base = dict(
        meeting_id=None,
        user_id=None,
        kind="notes",
        model="gpt-6-luna",
        input_tokens=None,
        output_tokens=None,
        audio_seconds=None,
        status="ok",
    )
    return report.RunRow(**{**base, **kw})


def meeting(seconds, *, provider="skribby", at=AFTER_FLIP, owner=1, size=1):
    return report.MeetingRow(owner, at, seconds, provider, size)


# ---- pricing one row ----


def test_tokens_are_priced_per_million_at_the_table_prices():
    # gpt-6-luna: $0.10 per million in, $0.50 per million out.
    low, high = report.price_run(run(input_tokens=1_000_000, output_tokens=1_000_000))
    assert low == high == pytest.approx(0.60)


def test_audio_is_priced_per_minute_as_a_range():
    low, high = report.price_run(run(kind="transcribe", model="gpt-transcribe", audio_seconds=HOUR))
    assert low == pytest.approx(60 * 0.0045)
    assert high == pytest.approx(60 * 0.006)


def test_a_model_without_a_price_is_unpriced_not_guessed():
    assert report.price_run(run(model="mystery-model", input_tokens=10, output_tokens=10)) is None
    assert report.price_run(run(model="mystery-model", audio_seconds=60)) is None


def test_a_row_with_no_tokens_and_no_audio_costs_nothing():
    assert report.price_run(run(model="mystery-model", status="failed")) == (0.0, 0.0)


def test_a_missing_token_count_is_zero_for_that_side():
    low, _ = report.price_run(run(input_tokens=2_000_000))
    assert low == pytest.approx(0.20)


# ---- totals and the split ----


def test_split_by_kind_and_model_with_failed_and_unpriced_counted():
    runs = [
        run(input_tokens=1_000_000, output_tokens=0),
        run(input_tokens=1_000_000, output_tokens=0, status="failed"),
        run(kind="ask", input_tokens=0, output_tokens=2_000_000),
        run(kind="topics", model="mystery-model", input_tokens=5),
    ]
    total, split = report.ai_spend(runs)
    assert total.calls == 4 and total.failed == 1 and total.unpriced == 1
    assert total.low == pytest.approx(0.10 + 0.10 + 1.00)  # a failed row's tokens were paid
    assert split[("notes", "gpt-6-luna")].calls == 2
    assert split[("ask", "gpt-6-luna")].high == pytest.approx(1.00)
    assert split[("topics", "mystery-model")].unpriced == 1
    assert total.input_tokens == 2_000_005


# ---- the estimate block ----


def test_bot_estimate_counts_only_skribby_hours_and_splits_the_model_line_at_the_flip():
    meetings = [
        meeting(2 * HOUR),  # after the flip: ElevenLabs
        meeting(HOUR, at=BEFORE_FLIP),  # before: the old Skribby model
        meeting(5 * HOUR, provider="vexa"),  # no vendor money
    ]
    e = report.bot_estimate(meetings)
    assert e.skribby_hours == pytest.approx(3.0)
    assert e.elevenlabs_hours == pytest.approx(2.0)
    assert e.old_model_hours == pytest.approx(1.0)
    assert e.bot_low == pytest.approx(3 * 0.35)
    assert e.bot_high == pytest.approx(3 * 0.35 * 1.25)
    assert e.model_cost == pytest.approx(1 * 0.10 + 2 * 0.20)
    assert e.elevenlabs_known


def test_an_unknown_elevenlabs_price_is_flagged_and_makes_the_total_a_floor(monkeypatch):
    monkeypatch.setattr(report, "ELEVENLABS_PER_HOUR", None)
    e = report.bot_estimate([meeting(2 * HOUR)])
    assert e.model_cost is None and not e.elevenlabs_known
    c = report.cost([meeting(2 * HOUR)], [])
    assert not c.complete
    assert c.total_low == pytest.approx(2 * 0.35)  # the unknown part adds nothing: a floor
    text = report.render(
        days=30,
        now=NOW,
        meetings=[report.MeetingWithId(1, meeting(2 * HOUR))],
        runs=[],
        usage=(0, 0),
    )
    assert "price unknown" in text and "INCOMPLETE" in text


# ---- the total per hour ----


def test_total_per_hour_is_a_range_from_the_database_and_the_estimate():
    meetings = [meeting(2 * HOUR)]  # 2 h, after the flip, Skribby
    runs = [run(input_tokens=1_000_000, output_tokens=1_000_000)]  # $0.60
    c = report.cost(meetings, runs)
    assert c.hours == pytest.approx(2.0)
    # database 0.60 + bot 0.70 to 0.875 + model 2 x 0.20 = 0.40
    assert c.total_low == pytest.approx(0.60 + 0.70 + 0.40)
    assert c.total_high == pytest.approx(0.60 + 0.875 + 0.40)
    assert c.per_hour(c.total_low) == pytest.approx(0.85)
    assert c.per_hour(c.total_high) == pytest.approx(0.9375)
    assert c.complete


def test_no_recorded_hours_gives_no_per_hour_figure():
    c = report.cost([meeting(None), meeting(0)], [run(input_tokens=100)])
    assert c.hours == 0 and c.per_hour(c.total_low) is None
    assert "n/a" in report.span(c.per_hour(1.0), c.per_hour(2.0))


# ---- long recordings ----


def test_exactly_three_hours_is_not_long_one_second_more_is():
    count, seconds = report.long_meetings(
        [meeting(3 * HOUR), meeting(3 * HOUR + 1), meeting(5 * HOUR), meeting(None)]
    )
    assert (count, seconds) == (2, 8 * HOUR + 1)


def test_without_long_drops_the_long_meetings_and_their_own_runs_only():
    short = report.MeetingWithId(1, meeting(HOUR))
    long = report.MeetingWithId(2, meeting(6 * HOUR))
    runs = [
        run(meeting_id=1, input_tokens=1),
        run(meeting_id=2, input_tokens=1),
        run(meeting_id=None, input_tokens=1),  # Ask Beyric: stays
        run(meeting_id=99, input_tokens=1),  # a meeting outside the window: stays
    ]
    kept, kept_runs = report.without_long([short, long], runs)
    assert kept == [short.row]
    assert [r.meeting_id for r in kept_runs] == [1, None, 99]


# ---- grouping, counts only ----


def test_only_provider_keeps_that_providers_meetings_and_their_runs():
    sk = report.MeetingWithId(1, meeting(HOUR))
    vx = report.MeetingWithId(2, meeting(4 * HOUR, provider="vexa"))
    runs = [
        run(meeting_id=1, input_tokens=1),
        run(meeting_id=2, input_tokens=1),
        run(meeting_id=None, input_tokens=1),
    ]
    rows, kept = report.only_provider([sk, vx], runs, "skribby")
    assert rows == [sk.row] and [r.meeting_id for r in kept] == [1]


def test_team_size_buckets():
    labels = {n: report.bucket_label(n) for n in (1, 2, 3, 4, 9, 10, 50)}
    assert labels[1] == "1 (private or alone)"
    assert labels[2] == labels[3] == "2-3"
    assert labels[4] == labels[9] == "4-9"
    assert labels[10] == labels[50] == "10 or more"


def test_a_run_goes_to_its_own_user_else_its_meetings_owner_else_nowhere():
    m1 = report.MeetingWithId(1, meeting(HOUR, owner=7))
    runs = [
        run(meeting_id=1, input_tokens=1),  # no user_id: the meeting's owner, 7
        run(meeting_id=1, user_id=8, input_tokens=1),  # its own user, 8
        run(meeting_id=None, input_tokens=1),  # nobody
    ]
    groups = report.split_by_user([m1], runs)
    assert set(groups) == {7, 8}
    assert len(groups[7][1]) == 1 and len(groups[8][1]) == 1 and groups[8][0] == []


def test_runs_follow_their_meetings_team_size_bucket():
    solo = report.MeetingWithId(1, meeting(HOUR, size=1))
    team = report.MeetingWithId(2, meeting(2 * HOUR, size=5))
    groups = report.split_by_team_size(
        [solo, team], [run(meeting_id=2, input_tokens=1), run(meeting_id=None, input_tokens=1)]
    )
    assert recorded(groups["4-9"][0]) == 2 * HOUR and len(groups["4-9"][1]) == 1
    assert len(groups["1 (private or alone)"][1]) == 0


def recorded(meetings):
    return report.recorded_seconds(meetings)


# ---- the command line ----


def test_days_below_one_is_refused_before_any_database_is_touched():
    with pytest.raises(SystemExit):
        report.main(["--days", "0"])


# ---- against a scratch database ----

SECRET_TITLE = "SECRET-TITLE the merger"
SECRET_TEXT = "SECRET-TRANSCRIPT we will fire everyone"
SECRET_NAME = "Secret Person Name"
SECRET_EMAIL = "secret.person@example.test"


def _user(session, email, name=None):
    user = User(email=email, name=name)
    session.add(user)
    session.flush()
    session.add(Template(owner_user_id=user.id, name="t", instructions="i"))
    session.flush()
    return user


def _meeting(session, owner, template_id, *, seconds, provider, at, **kw):
    m = Meeting(
        owner_user_id=owner.id,
        url="https://meet.google.com/aaa-bbbb-ccc",
        platform="google_meet",
        native_id=f"n{seconds}{at.day}{provider}",
        template_id=template_id,
        recorded_seconds=seconds,
        bot_provider=provider,
        title=SECRET_TITLE,
        transcript=SECRET_TEXT,
        **kw,
    )
    session.add(m)
    session.flush()
    m.created_at = at
    session.flush()
    return m


@pytest.fixture
def fixture_db(session):
    ana = _user(session, SECRET_EMAIL, SECRET_NAME)
    bo = _user(session, "bo@example.test")
    cy = _user(session, "cy@example.test")
    team = Team(name="Secret Team Name", owner_user_id=ana.id)
    session.add(team)
    session.flush()
    for u in (ana, bo, cy):
        session.add(TeamMember(team_id=team.id, user_id=u.id))
    tpl = {u.id: session.query(Template).filter_by(owner_user_id=u.id).one().id for u in (ana, bo)}
    m1 = _meeting(
        session, ana, tpl[ana.id], seconds=HOUR, provider="skribby", at=NOW - timedelta(days=1)
    )
    m2 = _meeting(
        session,
        bo,
        tpl[bo.id],
        seconds=2 * HOUR,
        provider="skribby",
        at=NOW - timedelta(days=5),
        shared_team_id=team.id,
    )
    m3 = _meeting(
        session, ana, tpl[ana.id], seconds=4 * HOUR, provider="vexa", at=NOW - timedelta(days=3)
    )
    _meeting(  # outside a 30-day window
        session, ana, tpl[ana.id], seconds=9 * HOUR, provider="vexa", at=NOW - timedelta(days=60)
    )
    session.add_all(
        [
            AiRun(
                meeting_id=m1.id,
                user_id=ana.id,
                kind="notes",
                model="gpt-6-luna",
                input_tokens=1_000_000,
                output_tokens=1_000_000,
            ),
            AiRun(
                meeting_id=m2.id,
                user_id=bo.id,
                kind="transcribe",
                model="gpt-transcribe",
                audio_seconds=HOUR,
            ),
            AiRun(
                meeting_id=m3.id,
                kind="topics",
                model="mystery-model",
                input_tokens=10,
                output_tokens=10,
            ),
            AiRun(user_id=bo.id, kind="ask", model="gpt-6-luna", input_tokens=1_000_000),
            UsageEntry(
                user_id=ana.id,
                email_hash="h1",
                meeting_id=m1.id,
                seconds=HOUR,
                counted_at=NOW - timedelta(days=1),
            ),
        ]
    )
    session.flush()
    for row in session.query(AiRun).all():
        row.created_at = NOW - timedelta(days=1)
    session.flush()
    return session


def test_fetch_reads_only_the_window_and_the_right_numbers(fixture_db):
    since = NOW - timedelta(days=30)
    meetings = report.fetch_meetings(fixture_db, since)
    assert sorted(m.row.recorded_seconds for m in meetings) == [HOUR, 2 * HOUR, 4 * HOUR]
    sizes = {m.row.recorded_seconds: m.row.team_size for m in meetings}
    assert sizes == {HOUR: 1, 2 * HOUR: 3, 4 * HOUR: 1}  # shared with a team of three
    assert len(report.fetch_runs(fixture_db, since)) == 4
    assert report.fetch_usage_seconds(fixture_db, since) == (1, HOUR)


def test_the_report_end_to_end_has_the_numbers_and_no_text_or_names(fixture_db):
    since = NOW - timedelta(days=30)
    text = report.render(
        days=30,
        now=NOW,
        meetings=report.fetch_meetings(fixture_db, since),
        runs=report.fetch_runs(fixture_db, since),
        usage=report.fetch_usage_seconds(fixture_db, since),
    )
    assert "Recorded hours: 7.00" in text
    assert "Meetings created in the window: 3" in text
    assert "UNPRICED rows (model not in the table): 1" in text
    assert "1 meeting(s), 4.00 h of the 7.00 h total" in text  # the 4 h Vexa recording
    assert "team of 4-9" not in text and "team of 2-3: 1 meeting(s), 2.00 h" in text
    assert "Accounts with a meeting or an AI call: 2" in text
    for secret in (SECRET_TITLE, SECRET_TEXT, SECRET_NAME, SECRET_EMAIL, "Secret Team Name"):
        assert secret not in text
    assert "example.test" not in text


def test_the_transaction_is_read_only(session):
    report.open_read_only(session)
    with pytest.raises(DBAPIError):
        session.add(User(email="new@example.test"))
        session.flush()
    session.rollback()
