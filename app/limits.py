"""Rate limits on the public account routes. The hits are kept by a `Store` (`app/ratelimit.py`),
in the database for the app: a restart does not forget them and a second `web` replica shares them.

These sit behind nginx's own zones (`deploy/nginx.conf`) and exist to protect what nginx cannot
see: one email address (a mail inbox) and the 10 account slots. A refused call is answered with
the same 202 as a served one where the route promises that (resend, forgot), so the limit never
reveals whether an address has an account.
"""

from app.auth import account_key
from app.ratelimit import Gate, MemoryStore, Store

HOUR = 3600.0
# Sign-ups one client address may make while its rows could still be unconfirmed (48 hours, the
# life of an unconfirmed account): counted for EVERY submission that gets as far as the pilot
# checks, whether the email is new or already has an account, so the cap is never a way to tell
# which. Confirming an account gives its hit back.
SIGNUP_ORIGIN_CAP = 3
SIGNUP_ORIGIN_WINDOW = 48 * HOUR


class Limit:
    """`limit` hits per key in `window` seconds, counted in `bucket`. `hit` counts and answers
    whether it was allowed; a refused hit is not counted (a client that keeps hammering does not
    extend its wait)."""

    def __init__(self, store: Store, bucket: str, limit: int, window: float) -> None:
        self._store = store
        self._bucket, self._limit, self._window = bucket, limit, window

    def _gate(self, key: str) -> Gate:
        return Gate("limit", self._bucket, key, self._limit, self._window)

    def hit(self, key: str, now: float | None = None) -> bool:
        return self._store.acquire([self._gate(key)], now) is None

    def full(self, key: str, now: float | None = None) -> bool:
        """Whether `key` is over its limit, without counting anything."""
        return self._store.full(self._gate(key), now)

    def add(self, key: str, now: float | None = None) -> None:
        """Count one hit unconditionally (for events counted after the fact, like a wrong guess)."""
        self._store.add(self._gate(key), now)

    def refund(self, key: str) -> None:
        """Give back the newest hit for `key`."""
        self._store.refund(self._bucket, key)


class PublicLimits:
    def __init__(self, store: Store | None = None) -> None:
        # The app passes a `DbStore`; with none (unit tests) the counters are in this process.
        self.store: Store = store if store is not None else MemoryStore()
        s = self.store
        self.signup_address = Limit(s, "signup_address", 5, HOUR)
        self.signup_email = Limit(s, "signup_email", 1, 60.0)
        self.signup_origin = Limit(s, "signup_origin", SIGNUP_ORIGIN_CAP, SIGNUP_ORIGIN_WINDOW)
        # Wrong passwords per verification link: after 5 the link is dead, so its password
        # cannot be guessed (the link itself is a 256-bit secret, so this is a second lock).
        self.verify_wrong = Limit(s, "verify_wrong", 5, HOUR)
        self.resend_email = Limit(s, "resend_email", 1, 60.0)
        self.resend_address = Limit(s, "resend_address", 5, HOUR)
        self.forgot_email = Limit(s, "forgot_email", 1, 60.0)
        self.forgot_address = Limit(s, "forgot_address", 5, HOUR)
        self.waitlist_address = Limit(s, "waitlist_address", 5, HOUR)
        self.waitlist_email = Limit(s, "waitlist_email", 1, 60.0)

    def reset(self) -> None:
        self.store.reset()  # tests only


def email_key(email: str) -> str:
    return account_key(email)
