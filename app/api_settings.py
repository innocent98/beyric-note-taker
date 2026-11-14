"""The settings routes of the JSON API. Included into `app.api.router`, so they share its `/api`
prefix, its login and CSRF rules, its error shape and its OpenAPI file.

Only the caller's own row is ever read or written: the account is the logged-in user, never an id
from the path or the body.
"""

from datetime import UTC, datetime

from fastapi import APIRouter, Depends, Request
from sqlalchemy import select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app import auth, bot_names, services, signup, teams, timezones, translation
from app.api_schemas import (
    AutoShareIn,
    AutoShareOut,
    BotNameIn,
    BotNameOut,
    EmailPreferencesIn,
    EmailPreferencesOut,
    ErrorResponse,
    LanguagePreferencesIn,
    LanguagePreferencesOut,
    RecordingAckOut,
    TimeZoneIn,
    TimeZoneOut,
)
from app.db import get_session
from app.models import Team, User, UserSettings

router = APIRouter(prefix="/settings", tags=["settings"])


@router.get("/email")
def read_email_preferences(
    session: Session = Depends(get_session), user: User = Depends(auth.current_user)
) -> EmailPreferencesOut:
    """Whether the caller is emailed when the notes of their meeting are ready (on by default,
    also for an account that has no settings row yet)."""
    value = session.scalar(select(UserSettings.notify_email).where(UserSettings.user_id == user.id))
    return EmailPreferencesOut(notify_email=True if value is None else value)


@router.put("/email")
def write_email_preferences(
    body: EmailPreferencesIn,
    session: Session = Depends(get_session),
    user: User = Depends(auth.current_user),
) -> EmailPreferencesOut:
    """Turn the notes-ready email on or off for the caller. The mail is checked against this
    setting when it is sent, so turning it off also stops one already queued."""
    # One statement, so two first writes racing each other cannot collide on the key.
    session.execute(
        insert(UserSettings)
        .values(user_id=user.id, notify_email=body.notify_email)
        .on_conflict_do_update(index_elements=["user_id"], set_={"notify_email": body.notify_email})
    )
    session.commit()
    return EmailPreferencesOut(notify_email=body.notify_email)


@router.get("/language")
def read_language_preferences(
    session: Session = Depends(get_session), user: User = Depends(auth.current_user)
) -> LanguagePreferencesOut:
    """The language meetings open in when a translation exists: `en` (the original, the default,
    also for an account that has no settings row yet), `fr` or `es`."""
    value = session.scalar(
        select(UserSettings.default_language).where(UserSettings.user_id == user.id)
    )
    return LanguagePreferencesOut(default_language=value or "en")


@router.put(
    "/language",
    responses={422: {"model": ErrorResponse, "description": "`language_unsupported`"}},
)
def write_language_preferences(
    body: LanguagePreferencesIn,
    session: Session = Depends(get_session),
    user: User = Depends(auth.current_user),
) -> LanguagePreferencesOut:
    """Set the default notes language. Nothing is translated by this: a meeting opens in it only
    when that translation already exists, otherwise it opens in the original with an offer."""
    if body.default_language not in translation.LANGUAGES:
        raise services.Refused(422, "language_unsupported", translation.unsupported_message())
    session.execute(
        insert(UserSettings)
        .values(user_id=user.id, default_language=body.default_language)
        .on_conflict_do_update(
            index_elements=["user_id"], set_={"default_language": body.default_language}
        )
    )
    session.commit()
    return LanguagePreferencesOut(default_language=body.default_language)


@router.post("/recording-ack")
def acknowledge_recording(
    session: Session = Depends(get_session), user: User = Depends(auth.current_user)
) -> RecordingAckOut:
    """The one-time card on the Meetings page for accounts older than the sign-up box: the person
    confirms they will tell the people in their meetings that Beyric records them. Stores the time
    and the wording's version the first time; later calls change nothing. It never gates anything
    else, so the bot, auto-join and every other route work whether or not it was ever called."""
    # One conditional statement: the first call wins, a second (or a double click) changes nothing.
    session.execute(
        update(User)
        .where(User.id == user.id, User.recording_ack_at.is_(None))
        .values(
            recording_ack_at=datetime.now(UTC), recording_ack_version=signup.RECORDING_ACK_VERSION
        )
    )
    session.commit()
    return RecordingAckOut(recording_acknowledged=True)


@router.get("/time-zone")
def read_time_zone(
    session: Session = Depends(get_session), user: User = Depends(auth.current_user)
) -> TimeZoneOut:
    """The zone meeting times, due dates and reminder mails are written in. `UTC` with `is_set`
    false until the person chooses one."""
    value = session.scalar(select(UserSettings.time_zone).where(UserSettings.user_id == user.id))
    return TimeZoneOut(time_zone=value or "UTC", is_set=value is not None)


@router.put(
    "/time-zone",
    responses={422: {"model": ErrorResponse, "description": "`time_zone_unsupported`"}},
)
def write_time_zone(
    body: TimeZoneIn,
    session: Session = Depends(get_session),
    user: User = Depends(auth.current_user),
) -> TimeZoneOut:
    """Set the caller's time zone (a name from the IANA database, e.g. `Africa/Lagos`)."""
    name = body.time_zone.strip()
    if not timezones.is_known(name):
        raise services.Refused(
            422,
            "time_zone_unsupported",
            "That time zone is not one we know. Pick one from the list.",
        )
    session.execute(
        insert(UserSettings)
        .values(user_id=user.id, time_zone=name)
        .on_conflict_do_update(index_elements=["user_id"], set_={"time_zone": name})
    )
    session.commit()
    return TimeZoneOut(time_zone=name, is_set=True)


def _bot_name_out(request: Request, user: User) -> BotNameOut:
    """The name and the default beside it, read from the server's own settings: the default is what
    the bot provider in use is called when the person sets none (`SKRIBBY_BOT_NAME`)."""
    settings = request.app.state.settings
    return BotNameOut(
        bot_name=user.bot_name,
        bot_name_default=bot_names.default_name(settings, None),
        warning=bot_names.recorder_warning(user.bot_name),
        bot_name_applies_when_signed_in=settings.bot_name_applies_when_signed_in,
    )


@router.get("/bot-name")
def read_bot_name(request: Request, user: User = Depends(auth.current_user)) -> BotNameOut:
    """The name the note taker joins this person's meetings with: their own when set, else null with
    the server's default beside it (`bot_name_default`)."""
    return _bot_name_out(request, user)


@router.put(
    "/bot-name",
    responses={422: {"model": ErrorResponse, "description": "`bot_name_invalid`"}},
)
def write_bot_name(
    request: Request,
    body: BotNameIn,
    session: Session = Depends(get_session),
    user: User = Depends(auth.current_user),
) -> BotNameOut:
    """Set the caller's note taker name, or clear it (null or empty = the server default). It
    applies to the meetings made from now on; a bot already sent keeps the name it joined with."""
    try:
        name = bot_names.clean_bot_name(body.bot_name)
    except bot_names.BotNameInvalid as exc:
        raise services.Refused(422, "bot_name_invalid", str(exc)) from None
    session.execute(update(User).where(User.id == user.id).values(bot_name=name))
    session.commit()
    session.refresh(user)
    return _bot_name_out(request, user)


@router.get("/auto-share")
def read_auto_share(
    session: Session = Depends(get_session), user: User = Depends(auth.current_user)
) -> AutoShareOut:
    """The one team this person's new meetings are shared to when their notes are first written
    (never when the bot joins), or null. A team they have since left reads as null."""
    return AutoShareOut(team_id=teams.auto_share_team(session, user))


@router.put(
    "/auto-share",
    responses={
        404: {"model": ErrorResponse, "description": "A team the caller is not in"},
        409: {"model": ErrorResponse, "description": "`personal_team`, `multi_team_disabled`"},
    },
)
def write_auto_share(
    body: AutoShareIn,
    session: Session = Depends(get_session),
    user: User = Depends(auth.current_user),
) -> AutoShareOut:
    """Choose the team new meetings are shared to automatically (one at most), or null for none. The
    caller must belong to it and it must not be their personal space. It applies once, when a
    meeting's notes are first written; a meeting already written is not touched. 409
    `multi_team_disabled` while the switch is off."""
    if not teams.multi_team_enabled():
        raise services.Refused(
            409, "multi_team_disabled", "Choosing a team for new meetings is not available yet."
        )
    if body.team_id is not None:
        member = teams.membership(session, user.id, body.team_id)
        team = session.get(Team, body.team_id) if member is not None else None
        if team is None:
            raise services.Refused(404, "not_found", "No such team.")
        if team.is_personal:
            raise services.Refused(
                409, "personal_team", "Choose a team. Your personal space is only yours."
            )
    session.execute(
        insert(UserSettings)
        .values(user_id=user.id, auto_share_team_id=body.team_id)
        .on_conflict_do_update(
            index_elements=["user_id"], set_={"auto_share_team_id": body.team_id}
        )
    )
    session.commit()
    return AutoShareOut(team_id=body.team_id)
