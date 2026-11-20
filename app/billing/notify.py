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


def grant_failed_key(payment_id: int) -> str:
    """The ONE alert key for "the provider says this payment is paid and its grant failed". The
    cancel button, the webhook and both sweepers all use it, so a payment is announced once
    whichever path sees the failure first (`alerts._write` keeps one row per key and admin)."""
    return f"admin.billing_grant_failed:{payment_id}"


def tell_grant_failed(db: Session, payment_id: int, exc: BaseException) -> None:
    """A payment the provider reports paid whose grant keeps failing in the background: tell the
    admins once. Ids and the exception TYPE only, never `str(exc)` (it can hold an email, an amount
    or the provider's own words). It COMMITS, in its own transaction: the caller has already rolled
    back the failed step, so the alert is the only thing this commit writes. Best effort like
    `tell_admins`: a failure to save it is logged by type and swallowed, never raised into the
    sweep or the webhook answer it rides on."""
    tell_admins(
        db,
        grant_failed_key(payment_id),
        "A paid payment could not be granted",
        f"The provider reports payment {payment_id} paid, but it could not be settled "
        f"({type(exc).__name__}). Look at it.",
    )
    try:
        db.commit()
    except Exception as commit_exc:
        logger.error(
            "admin alert for payment %s not saved: %s", payment_id, type(commit_exc).__name__
        )
        try:
            db.rollback()
        except Exception as rollback_exc:  # a dead connection: the caller carries on
            logger.error(
                "rollback after the alert for payment %s failed: %s",
                payment_id,
                type(rollback_exc).__name__,
            )
