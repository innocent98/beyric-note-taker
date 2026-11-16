"""Shared test helpers."""

from datetime import UTC, datetime, timedelta


class FakeClock:
    """A clock the test moves by hand, so a scheduler can run a whole day in milliseconds."""

    def __init__(self, start: datetime | None = None) -> None:
        self._now = start or datetime(2026, 11, 3, 12, 0, tzinfo=UTC)

    def now(self) -> datetime:
        return self._now

    def advance(self, seconds: float) -> datetime:
        self._now += timedelta(seconds=seconds)
        return self._now


# The fields billing stage S2 adds to `usage` in the session: all null unless HOURS_MODE=enforce
# (`tests/test_hours_enforce.py` covers the filled ones).
NO_NEW_USAGE_FIELDS = dict.fromkeys(
    (
        "state",
        "seconds_left",
        "warning",
        "can_record",
        "unlimited",
        "period_ends_at",
        "trial_ends_at",
    )
)
