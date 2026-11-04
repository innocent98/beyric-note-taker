"""The words the calendar screens show (plan C15a: the server owns the wording, so the frontend
never invents a reason and no internal code reaches a person).

Pure functions over the codes the rest of the calendar code uses: `gcal_rules` reasons, connection
states, the sync's recorded error. This is the first slice of the module; the full
`state_text(state, reason, ctx)` for scheduled joins (hours used, duplicate, not recorded...) is
added with the rest of C15a.
"""

from app.gcal_rules import JoinDecision
from app.models import CalendarConnection

PLATFORM_LABELS = {"google_meet": "Google Meet", "zoom": "Zoom", "teams": "Microsoft Teams"}

NOT_CONNECTED = "Google Calendar is not connected."

# The designer's three answers (Beyric Note Taker.dc.html, the Upcoming rows): "Will join", "Off"
# (the person could switch it on: the rule or their own switch says no) and "Cannot join" (nothing
# they switch changes it: no link, over, declined, ...).
WILL_JOIN = "Will join"
OFF = "Off"
CANNOT_JOIN = "Cannot join"
OFF_REASONS = frozenset({"rule_none", "not_organizer", "not_accepted", "override_off"})

# The plain reason behind a "no", one sentence for each reason `will_join` can give. The first is
# the designer's own hint for an event without a link.
REASON_TEXT = {
    "no_link": "Add a meeting link to this event to record it",
    "teams_unsupported": "Microsoft Teams meetings cannot be joined yet",
    "declined": "You declined this invite",
    "cancelled": "This event was cancelled",
    "ended": "Already over",
    "too_long": "Too long to join (over 6 hours)",
    "all_day": "All-day events are not joined",
    "event_type": "This kind of event is not joined",
    "not_organizer": "Skipped because you are not the organiser",
    "not_accepted": "Waiting for you to accept the invite",
    "rule_none": "Auto-join is set to only when you turn it on",
    "override_off": "You switched this meeting off",
}
UNKNOWN_REASON_TEXT = "Will not join"  # a reason added to the rules before it was given words

# The designer's label for the rule (the Meetings page's Upcoming header).
RULE_TEXT = {
    "all": "Joining all meetings",
    "organizer": "Joining meetings you organise",
    "none": "Auto-join is off",
}

RECONNECT_TEXT = {
    "revoked": "Google access was removed. Connect again to carry on.",
    "expired": "Google access ran out. Connect again to carry on.",
    "error": "Google Calendar stopped working. Connect again to carry on.",
}
GENERIC_RECONNECT_TEXT = "Google needs you to connect again."

DEGRADED_TEXT = "Connected, but your calendar has not been read lately."
CONNECTED_TEXT = "Connected"

REVOKED_ERROR_TEXT = "Google no longer lets Beyric read your calendar. Connect again to fix this."
GENERIC_ERROR_TEXT = "The last check of your calendar did not work. Beyric will try again soon."


def join_label(decision: JoinDecision) -> str:
    """The short answer to "will the bot join this?": Will join, Off or Cannot join."""
    if decision.join:
        return WILL_JOIN
    return OFF if decision.reason in OFF_REASONS else CANNOT_JOIN


def join_reason(decision: JoinDecision) -> str | None:
    """Why not, in one sentence; None when it will join."""
    if decision.join:
        return None
    return REASON_TEXT.get(decision.reason, UNKNOWN_REASON_TEXT)


def rule_text(rule: str) -> str:
    return RULE_TEXT.get(rule, RULE_TEXT["all"])


def platform_label(platform: str | None) -> str | None:
    return PLATFORM_LABELS.get(platform) if platform else None


def state_text(conn: CalendarConnection | None, state: str) -> str:
    """One line for the connection's state (`not_connected`, `connected`, `degraded` or
    `needs_reconnect`, as `calendar_api` names them)."""
    if state == "connected":
        return CONNECTED_TEXT
    if state == "degraded":
        return DEGRADED_TEXT
    if state == "needs_reconnect":
        reason = conn.needs_reconnect_reason if conn is not None else None
        return RECONNECT_TEXT.get(reason or "", GENERIC_RECONNECT_TEXT)
    return NOT_CONNECTED


def error_text(recorded: str | None) -> str | None:
    """The sync's last recorded failure (`ExcType: message`, see `gcal_sync._record_error`) in
    plain words. Never the type, status or message: a revoked grant says to connect again, anything
    else says the last check did not work."""
    if not recorded:
        return None
    if recorded.startswith("AuthRevoked"):
        return REVOKED_ERROR_TEXT
    return GENERIC_ERROR_TEXT
