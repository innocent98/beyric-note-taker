"""The one way billing tells the admins about money that needs a person (stage S9).

By ids only, never an email or a provider's text: a bell entry and one mail per `key`. Best effort,
in its own savepoint: an alert that cannot be written must never stop the money step it describes.
"""

import logging

from sqlalchemy.orm import Session

from app import alerts

logger = logging.getLogger(__name__)


def tell_admins(db: Session, key: str, title: str, body: str) -> None:
    """One admin alert per `key` (`admin.<kind>:<what it is about>`)."""
    try:
        with db.begin_nested():
            alerts.notify_admins(db, kind=key.split(":")[0], title=title, body=body, dedupe_key=key)
    except Exception as exc:
        logger.error("admin alert %s failed: %s", key.split(":")[0], type(exc).__name__)
