"""When a meeting's page may tell the owner to invite the bot to the event.

On Google Meet a signed-in bot whose address is on the event's guest list skips the waiting room
(`docs/spike/meeting-bot-vendors.md`; the bake-off measured 20-27 s with no knock against 39-46 s
with one). So while a Google Meet bot waits to be let in, or after it was not, the owner is shown
the address to add next time. The server decides, so the page needs no rule of its own:
`MeetingDetail.bot_invite_email` is the address when the tip applies and null otherwise.

Zoom and Teams bots join as guests and always wait for the host: never a tip for them.
"""

from app import services
from app.config import Settings
from app.models import Meeting
from app.pipeline import REASON_NOT_ADMITTED, REASON_NOT_LET_IN

# The live state the worker keeps on the meeting while the bot waits in the lobby.
AWAITING_ADMISSION = "awaiting_admission"
_NOT_LET_IN = {REASON_NOT_ADMITTED, REASON_NOT_LET_IN}


def meeting_invite_email(meeting: Meeting, settings: Settings) -> str | None:
    """The address to tell this meeting's owner to invite, or None.

    Needs the server to allow the tip at all (`Settings.bot_invite_email_shown`), a Google Meet
    meeting, a meeting that Skribby ran (Vexa's bot has no such address), and either a bot that
    is waiting to be let in right now (a state not seen for a while is not shown) or a meeting
    that failed because the bot was not let in."""
    address = settings.bot_invite_email_shown
    if address is None or meeting.platform != "google_meet" or meeting.bot_provider != "skribby":
        return None
    waiting = (
        meeting.status == "bot_sent"
        and meeting.completion_reason == AWAITING_ADMISSION
        and not services.bot_state_is_stale(meeting)
    )
    not_let_in = meeting.status == "failed" and meeting.failure_reason in _NOT_LET_IN
    return address if waiting or not_let_in else None
