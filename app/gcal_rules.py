"""Whether the bot joins a calendar event: one pure function (spec 7.5).

No database, no clock of its own (the caller passes `now`), no settings lookup: the rule, the
Teams switch and the user's per-meeting override come in as arguments, so a rule change can
re-run it over stored rows and a test can drive every combination.
"""

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Protocol

from app.models import AUTO_JOIN_RULES

# Longer than this is not joined; the same limit as `max_poll_hours`, which stops the bot anyway.
MAX_EVENT_DURATION = timedelta(hours=6)

OK = "ok"
REASONS = (
    OK,
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
)


class ParsedEventLike(Protocol):
    """What `will_join` reads. Both `ParsedEvent` and a stored `CalendarEvent` row have it."""

    starts_at: datetime
    ends_at: datetime
    platform: str | None
    organizer_self: bool | None
    self_response: str | None
    status: str
    event_type: str
    all_day: bool
    organizer_same_org: bool


@dataclass(frozen=True)
class JoinSettings:
    rule: str  # all, organizer or none
    teams_join_enabled: bool


@dataclass(frozen=True)
class JoinDecision:
    join: bool
    reason: str  # one of REASONS


def will_join(
    event: ParsedEventLike, override: bool | None, settings: JoinSettings, *, now: datetime
) -> JoinDecision:
    """Decide in priority order; the first check that fails gives the reason.

    What the event is comes first (cancelled, all-day, not a normal event, over, too long,
    declined, no link we can use): nothing the user switches on can change it. Then the user's
    switch for this meeting (`override`), which beats the rule in both directions. Then the rule,
    and under `all` an unanswered invite from outside the user's organisation still waits.
    """
    if settings.rule not in AUTO_JOIN_RULES:
        raise ValueError(f"unknown auto-join rule: {settings.rule!r}")
    if event.status == "cancelled":
        return JoinDecision(False, "cancelled")
    if event.all_day:
        return JoinDecision(False, "all_day")
    if event.event_type != "default":
        return JoinDecision(False, "event_type")
    if event.ends_at <= now:
        return JoinDecision(False, "ended")
    if event.ends_at - event.starts_at > MAX_EVENT_DURATION:
        return JoinDecision(False, "too_long")
    if event.self_response == "declined":
        return JoinDecision(False, "declined")
    if event.platform is None:
        return JoinDecision(False, "no_link")
    if event.platform == "teams" and not settings.teams_join_enabled:
        return JoinDecision(False, "teams_unsupported")

    if override is False:
        return JoinDecision(False, "override_off")
    if override is True:
        return JoinDecision(True, OK)
    if settings.rule == "none":
        return JoinDecision(False, "rule_none")
    if settings.rule == "organizer" and event.organizer_self is not True:
        return JoinDecision(False, "not_organizer")
    if (
        event.self_response == "needsAction"
        and event.organizer_self is not True
        and not event.organizer_same_org
    ):
        # An invite nobody answered, from someone outside the user's own organisation: anyone can
        # send one with a meeting link, so it must not send the bot (and take the only slot)
        # until the user accepts it or switches it on.
        return JoinDecision(False, "not_accepted")
    return JoinDecision(True, OK)
