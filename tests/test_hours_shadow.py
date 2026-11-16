"""`HOURS_MODE` in `placement.reserve_slot` (billing stage S1): with the flag off nothing changes;
in `shadow` one line is logged when the grants-based rule would have decided differently from the
old cap rule, and the old rule still decides. Real Postgres, like the placement tests."""

import logging
from datetime import timedelta

import pytest

from app import entitlements
from app.models import HourGrant
from app.placement import PlacementRefused, reserve_slot
from app.usage import email_hash
from tests.test_placement import HOUR, NOW, spend_hours
from tests.test_web import make_settings, make_user

LOGGER = "app.placement"


@pytest.fixture
def ada(session):
    return make_user(session, "ada@example.test")


def reserve(session, user, mode):
    reserve_slot(
        session,
        user,
        platform="google_meet",
        native_id="abc-defg-hij",
        now=NOW,
        settings=make_settings(hours_mode=mode),
    )


def give(session, user, seconds, kind="pilot"):
    session.add(
        HourGrant(
            user_id=user.id,
            email_hash=email_hash(user.email),
            kind=kind,
            seconds=seconds,
            starts_at=NOW - timedelta(days=1),
        )
    )
    session.commit()


def shadow_lines(caplog):
    return [r.getMessage() for r in caplog.records if r.getMessage().startswith("hours_shadow")]


@pytest.mark.parametrize("mode", ["off", "enforce"])
def test_with_the_flag_off_nothing_is_asked_or_logged_even_when_the_rules_differ(
    session, ada, caplog, monkeypatch, mode
):
    """No grant at all: the new rule would refuse and the old one allows. Off, and `enforce`
    (accepted, does nothing yet), never even call it."""
    monkeypatch.setattr(
        entitlements, "check_join", lambda *a, **k: pytest.fail("the new rule was asked")
    )
    with caplog.at_level(logging.INFO, logger=LOGGER):
        reserve(session, ada, mode)  # allowed: the old rule alone decides
    assert shadow_lines(caplog) == []


def test_the_default_setting_is_off():
    assert make_settings().hours_mode == "off"


def test_shadow_logs_one_line_when_the_new_rule_would_refuse_and_the_old_allows(
    session, ada, caplog
):
    with caplog.at_level(logging.INFO, logger=LOGGER):
        reserve(session, ada, "shadow")  # still allowed: nothing raised
    assert shadow_lines(caplog) == [
        f"hours_shadow disagree user={ada.id} old=allow new=refuse reason=no_plan seconds_left=0"
    ]


def test_shadow_logs_when_the_old_rule_refuses_and_the_new_would_allow_and_still_refuses(
    session, ada, caplog
):
    spend_hours(session, ada, 5 * HOUR)  # the old cap is reached
    give(session, ada, 10 * HOUR)  # a grant the old rule knows nothing about
    with caplog.at_level(logging.INFO, logger=LOGGER):
        with pytest.raises(PlacementRefused) as caught:
            reserve(session, ada, "shadow")
    assert caught.value.code == "hours_cap_reached"  # the old rule still decides
    assert shadow_lines(caplog) == [
        f"hours_shadow disagree user={ada.id} old=refuse new=allow reason=None "
        f"seconds_left={5 * HOUR}"
    ]


def test_shadow_is_silent_when_the_two_rules_agree(session, ada, caplog):
    give(session, ada, 5 * HOUR)  # what the migration would have given: the whole cap
    with caplog.at_level(logging.INFO, logger=LOGGER):
        reserve(session, ada, "shadow")
    assert shadow_lines(caplog) == []


def test_shadow_is_silent_when_both_refuse(session, ada, caplog):
    spend_hours(session, ada, 5 * HOUR)
    with caplog.at_level(logging.INFO, logger=LOGGER):
        with pytest.raises(PlacementRefused):
            reserve(session, ada, "shadow")
    assert shadow_lines(caplog) == []


def test_a_failure_in_the_shadow_never_breaks_a_join(session, ada, caplog, monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("the grants table is missing")

    monkeypatch.setattr(entitlements, "check_join", boom)
    with caplog.at_level(logging.INFO, logger=LOGGER):
        reserve(session, ada, "shadow")  # no exception: the old rule alone decided
    assert any("hours_shadow failed" in r.getMessage() for r in caplog.records)
    assert "the grants table is missing" not in caplog.text and "Traceback" not in caplog.text


def test_the_log_line_carries_no_email(session, ada, caplog):
    with caplog.at_level(logging.INFO, logger=LOGGER):
        reserve(session, ada, "shadow")
    assert "ada@example.test" not in caplog.text and email_hash(ada.email) not in caplog.text


def test_the_shadow_runs_in_a_savepoint_so_a_database_error_leaves_the_transaction_usable(
    session, ada, monkeypatch, caplog
):
    from sqlalchemy import text

    def bad_sql(sess, *a, **k):
        sess.execute(
            text("select * from table_that_does_not_exist where h = :h"),
            {"h": email_hash(ada.email)},  # the same parameter the real queries bind
        )

    monkeypatch.setattr(entitlements, "check_join", bad_sql)
    with caplog.at_level(logging.INFO, logger=LOGGER):
        reserve(session, ada, "shadow")  # logged, not raised
    assert "hours_shadow failed" in caplog.text and "ProgrammingError" in caplog.text
    assert "table_that_does_not_exist" not in caplog.text  # the database's own message stays out
    assert email_hash(ada.email) not in caplog.text  # and so do its bound parameters
    assert session.execute(text("select 1")).scalar() == 1  # the transaction is not aborted
