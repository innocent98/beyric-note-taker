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

# One sentence for each reason `will_join` can give. "Will join" is the only yes.
JOIN_TEXT = {
    "ok": "Will join",
    "no_link": "No video link",
    "teams_unsupported": "Microsoft Teams meetings cannot be joined yet",
    "declined": "You declined this invite",
    "cancelled": "This event was cancelled",
    "ended": "Already over",
    "too_long": "Too long to join (over 6 hours)",
    "all_day": "All-day events are not joined",
    "event_type": "This kind of event is not joined",
    "not_organizer": "Skipped: you are not the organiser",
    "not_accepted": "Waiting: accept the invite to join",
    "rule_none": "Off: joining only meetings you switch on",
    "override_off": "Off: you switched this one off",
}
UNKNOWN_JOIN_TEXT = "Will not join"  # a reason added to the rules before it was given words here

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


def join_text(decision: JoinDecision) -> str:
    """The answer to "will the bot join this?" for one event, from the rules' decision."""
    return JOIN_TEXT.get(decision.reason, UNKNOWN_JOIN_TEXT)


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
