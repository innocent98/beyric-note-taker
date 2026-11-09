"""Admin alerts about OUR Microsoft client secret (Outlook spec 4.3, 8.1 #10, 9.3).

Two situations need a person at the Azure portal, and neither is something the user can fix:

* **`secret_expired`**: Microsoft refused our client with AADSTS7000222 (the secret has expired).
  Every refresh and every connect fails until a new secret is in `.env`. Sent once per day.
* **`secret_expiring`**: from 30 days before `MICROSOFT_SECRET_EXPIRES_ON`, a reminder once per day.

Both go through `app/alerts.py` (`notify_admins`: a bell entry per admin and one mail, deduped by
key, so a repeat does nothing). The caller is code that is doing something more important (a sync,
a callback), so nothing here raises: a failure is logged by exception type only. The text names the
action and the date and never a secret, a URL or an account.
"""

import logging
from datetime import datetime

from app import alerts, db, microsoft_oauth
from app.config import Settings

logger = logging.getLogger(__name__)

ONE_DAY_MINUTES = 24 * 60


def _notify(*, kind: str, title: str, body: str, dedupe_key: str) -> None:
    try:
        with db.SessionLocal() as session:
            alerts.notify_admins(session, kind=kind, title=title, body=body, dedupe_key=dedupe_key)
            session.commit()
    except Exception as exc:
        logger.warning("microsoft secret alert not written (%s)", type(exc).__name__)


def secret_expired(now: datetime) -> None:
    """Microsoft says the client secret has expired: tell the admins (once per day)."""
    _notify(
        kind="admin.microsoft_secret_expired",
        title="The Microsoft client secret has expired",
        body=(
            "Outlook calendars cannot connect or sync until a new client secret is created in "
            "Azure and set as MICROSOFT_CLIENT_SECRET (then recreate web and scheduler). "
            "Connections keep working after that: refresh tokens belong to the user and the app."
        ),
        dedupe_key=alerts.bucket_key("ms_secret_expired", now, ONE_DAY_MINUTES),
    )


def secret_expiring(settings: Settings, now: datetime) -> None:
    """The client secret expires within 30 days (or has, by the date in the settings): remind
    the admins once per day."""
    expires = microsoft_oauth.secret_expiry(settings)
    if expires is None:
        return
    days = (expires - now.date()).days
    when = "has expired" if days < 0 else f"expires on {expires.isoformat()} ({days} days)"
    _notify(
        kind="admin.microsoft_secret_expiring",
        title="The Microsoft client secret is about to expire",
        body=(
            f"The Microsoft client secret {when}. Create a new one in Azure, set it as "
            "MICROSOFT_CLIENT_SECRET and MICROSOFT_SECRET_EXPIRES_ON, recreate web and scheduler, "
            "then delete the old secret."
        ),
        dedupe_key=alerts.bucket_key("ms_secret_expiring", now, ONE_DAY_MINUTES),
    )
