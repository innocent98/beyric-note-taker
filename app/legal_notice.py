"""Telling the people who already have an account that the privacy policy changed.

The policy promises notice "by email or in the app" before a material change takes effect. The
notice is one in-app notification per account (the notifications API, `app/notifications.py`), not
a screen that must be accepted: nothing here asks for consent or locks anyone out, because the
change describes service email the person can turn off (Settings, Email preferences), not a new
purpose that needs their agreement. Accounts made after the update accepted the new pages at
sign-up (`users.terms_version`) and are not told.

`VERSION` must be the pages' "Last updated" date (`signup.TERMS_VERSION`; a test keeps them equal),
so bumping the version without writing a new notice fails the tests. When it is bumped, change
`TITLE` and `BODY` to say what changed this time. The hourly sweep calls `announce`, which writes
one row per account, once: the row's `dedupe_key` names the version, so repeating it does nothing
and a read notice is not made again.
"""

from datetime import UTC, datetime, timedelta

from sqlalchemy import DateTime, literal, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.models import Notification, User

VERSION = "2026-11-04"
KIND = "legal.updated"
DEDUPE_KEY = f"{KIND}:{VERSION}"
TITLE = "We updated our Privacy Policy"
BODY = (
    "We now email you when the notes of a meeting are ready, and the Privacy Policy says what "
    "that email carries, that our email provider (Resend) receives it, that we keep a log of the "
    "emails we send for 30 days, and that we keep a one-way fingerprint of any address we must "
    "not mail again. You can turn the notes email off in Settings, under Email preferences. "
    "Read the policy at /privacy."
)
# The sweep deletes notifications after 90 days; it must never make this one again then, so it
# only makes it for a month after the version's date.
WINDOW = timedelta(days=30)


def announce(session: Session, now: datetime) -> None:
    """Write the notice for every confirmed, active account that did not accept this version at
    sign-up and has not been told. Safe to repeat."""
    start = datetime.strptime(VERSION, "%Y-%m-%d").replace(tzinfo=UTC)
    if now >= start + WINDOW:
        return
    session.execute(
        insert(Notification)
        .from_select(
            ["user_id", "kind", "severity", "title", "body", "dedupe_key", "created_at"],
            select(
                User.id,
                literal(KIND),
                literal("info"),
                literal(TITLE),
                literal(BODY),
                literal(DEDUPE_KEY),
                literal(now, DateTime(timezone=True)),
            ).where(
                User.status == "active",
                User.email_verified_at.is_not(None),
                User.terms_version.is_distinct_from(VERSION),
            ),
        )
        .on_conflict_do_nothing(index_elements=["user_id", "dedupe_key"])
    )
    session.commit()
