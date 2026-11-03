"""Rate limits on the public account routes, in memory (the single `web` replica, like the login
limiter: note it before ever adding a second one).

These sit behind nginx's own zones (`deploy/nginx.conf`) and exist to protect what nginx cannot
see: one email address (a mail inbox) and the 10 account slots. A refused call is answered with
the same 202 as a served one where the route promises that (resend, forgot), so the limit never
reveals whether an address has an account.
"""

import threading
import time

from app.auth import _Window, account_key

HOUR = 3600.0


class Limit:
    """`limit` hits per key in `window` seconds. `hit` counts and answers whether it was allowed;
    a refused hit is not counted (a client that keeps hammering does not extend its wait)."""

    def __init__(self, limit: int, window: float) -> None:
        self._window = _Window(limit, window)
        self._lock = threading.Lock()

    def hit(self, key: str, now: float | None = None) -> bool:
        now = time.monotonic() if now is None else now
        with self._lock:
            self._window.sweep(now)
            if self._window.full(key, now):
                return False
            self._window.add(key, now)
            return True


class PublicLimits:
    def __init__(self) -> None:
        self.signup_address = Limit(5, HOUR)
        self.signup_email = Limit(1, 60.0)
        self.resend_email = Limit(1, 60.0)
        self.resend_address = Limit(5, HOUR)
        self.forgot_email = Limit(1, 60.0)
        self.forgot_address = Limit(5, HOUR)
        self.waitlist_address = Limit(5, HOUR)
        self.waitlist_email = Limit(1, 60.0)

    def reset(self) -> None:
        self.__init__()  # tests only


def email_key(email: str) -> str:
    return account_key(email)
