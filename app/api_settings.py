"""The settings routes of the JSON API. Included into `app.api.router`, so they share its `/api`
prefix, its login and CSRF rules, its error shape and its OpenAPI file.

Only the caller's own row is ever read or written: the account is the logged-in user, never an id
from the path or the body.
"""

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app import auth
from app.api_schemas import EmailPreferencesIn, EmailPreferencesOut
from app.db import get_session
from app.models import User, UserSettings

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
