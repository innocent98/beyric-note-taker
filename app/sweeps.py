"""The scheduler's periodic jobs (plan C10b): the hourly sweeps and the mail sender.

Nothing here is new logic. Each sweep is a function that already lived in `app/maintenance.py`,
`app/accounts.py` or `app/mail.py`; this module only puts them on the scheduler, each as its own
job so that one that fails (logged by type only, by the scheduler) never stops the others.

* **Hourly, on the sync loop** (a slow delete must never delay a dispatch tick): expired sign-in
  and connect flows, used or expired tokens past their keep window, notifications over 90 days,
  calendar events that ended over two days ago, sent and skipped mail over 30 days (the privacy
  policy's mail log), unverified accounts 48 hours after their last sign-up submission. The first
  run after a start is immediate, then every hour. Each is idempotent: a leader that loses its lock
  mid-job and a new one starting the same job cannot disagree.
* **Every dispatch tick:** the outbox sender, `mail.deliver_pending`. The tick is 10 s, so a
  verification mail waits 10 s at most. Registered only when a mail provider is configured, as the
  worker does: without one mail waits in the outbox until the sweep fails it.

Not calendar-gated: these tables exist whether or not `CALENDAR_ENABLED` is on, and an empty
`calendar_events` table costs one cheap delete an hour.

The worker (`app.worker`) still runs the same mail send and the same hourly housekeeping next to
its meeting passes. That is safe (row locks and idempotent deletes) and is kept on purpose until
the scheduler has been seen doing the work in production; removing it is a follow-up.
"""

import logging

from app import accounts, mail, maintenance
from app.config import Settings, get_settings
from app.maintenance import TOKEN_KEEP  # noqa: F401  (the window the token sweep keeps)
from app.scheduler import Scheduler

logger = logging.getLogger(__name__)

HOURLY = 3600
EVERY_TICK = 1  # the shortest interval the scheduler allows: due on every dispatch tick


def register_default_sweeps(
    scheduler: Scheduler, settings: Settings | None = None, *, mailer: mail.Mailer | None = None
) -> None:
    """Register the hourly sweeps and, when mail can be sent, the outbox sender. `mailer` is for
    tests; by default the one `settings` call for (none in production without a provider)."""
    settings = settings or get_settings()
    for name, sweep in (
        ("sweep_oauth_flows", maintenance.sweep_oauth_flows),
        ("sweep_auth_tokens", maintenance.sweep_auth_tokens),
        ("sweep_notifications", maintenance.sweep_notifications),
        ("sweep_calendar_events", maintenance.sweep_calendar_events),
        ("sweep_mail_outbox", mail.sweep_outbox),
        ("sweep_unverified_accounts", accounts.sweep_unverified),
    ):
        scheduler.register(name, HOURLY, sweep, loop="sync")

    mailer = mailer or mail.build_mailer(settings)
    if mailer is None:
        logger.warning("no mail provider is configured: mail stays in the outbox")
        return
    scheduler.register(
        "mail_outbox_send",
        EVERY_TICK,
        lambda session, now: mail.deliver_pending(session, mailer, settings, now),
        loop="dispatch",
    )
