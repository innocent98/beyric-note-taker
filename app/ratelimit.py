"""The counters behind the login and sign-up limits: sliding windows of recent hits per key.

Two stores with one interface. **`DbStore` is what the app uses**: the hits live in `rate_hits`, so
they survive a restart (a lockout lasts its hour, however often the container is replaced), they
are shared by every `web` and `worker` process (a second replica cannot double what an attacker
may try), and a check-and-count is one transaction under advisory locks, so parallel requests
cannot all slip through. `MemoryStore` has the same behaviour in one process and is for unit tests.

A `Gate` is one limit: `limit` hits per `key` in `window` seconds, counted in `bucket`. `acquire`
checks gates in the order given and counts a hit on **every** gate only when none is full, so a
refused attempt costs nothing and a client over its limit does not spend an account's budget.

What is stored is no more than a login or sign-up limit needs: the bucket's name, a keyed hash of
the key (an email address or a client address is personal data: the hash uses a key derived from
the session secret, so someone reading the table cannot check a guess) and the time of the hit.
Rows older than their window are deleted when the key is next touched; the hourly sweep
(`maintenance.sweep_rate_hits`) deletes the rest, bucket by bucket.

A database failure raises: the callers (login, sign-up) need the database anyway, and a limiter
that failed open would let a guessing attack through during an outage.
"""

import hashlib
import hmac
import threading
import time
from collections import deque
from collections.abc import Callable, Sequence
from contextlib import AbstractContextManager
from dataclasses import dataclass
from typing import Protocol

from sqlalchemy import text
from sqlalchemy.orm import Session

MAX_LIMITER_KEYS = 50_000  # per bucket, in memory; see Window
LOCK_CLASS = 7001  # first half of the two-int advisory lock (never collides with 6002 and friends)
KEY_HEX = 32  # hex characters of the keyed hash that are stored


@dataclass(frozen=True)
class Gate:
    """One limit. `label` is what `acquire` answers when this gate refuses."""

    label: str
    bucket: str
    key: str
    limit: int
    window: float


class Store(Protocol):
    """What the limiters need: `MemoryStore` and `DbStore` both have it."""

    def acquire(self, gates: Sequence[Gate], now: float | None = None) -> str | None: ...

    def full(self, gate: Gate, now: float | None = None) -> bool: ...

    def add(self, gate: Gate, now: float | None = None) -> None: ...

    def refund(self, bucket: str, key: str) -> None: ...

    def clear(self, bucket: str, key: str) -> int: ...

    def size(self) -> int: ...

    def reset(self) -> None: ...


class Window:
    """Timestamps of recent hits per key; entries older than `window` seconds expire.

    Memory is bounded: expired keys are dropped as they are touched and by a sweep every
    `SWEEP_SECONDS`, and past `max_keys` the key created longest ago is forgotten (a flood of
    distinct keys can then weaken the limit for the forgotten ones, but cannot grow memory or
    lock anyone out)."""

    SWEEP_SECONDS = 30.0

    def __init__(self, limit: int, window: float, max_keys: int = MAX_LIMITER_KEYS) -> None:
        self.limit = limit
        self.window = window
        self.max_keys = max_keys
        self.attempts: dict[str, deque[float]] = {}
        self._swept = float("-inf")

    def _trim(self, key: str, now: float) -> deque[float] | None:
        attempts = self.attempts.get(key)
        if attempts is None:
            return None
        while attempts and now - attempts[0] > self.window:
            attempts.popleft()
        if not attempts:
            del self.attempts[key]
            return None
        return attempts

    def sweep(self, now: float) -> None:
        if now - self._swept < self.SWEEP_SECONDS:
            return
        self._swept = now
        for key in list(self.attempts):
            self._trim(key, now)

    def full(self, key: str, now: float) -> bool:
        attempts = self._trim(key, now)
        return len(attempts or ()) >= self.limit  # a limit of 0 refuses everything

    def add(self, key: str, now: float) -> None:
        if key not in self.attempts and len(self.attempts) >= self.max_keys:
            del self.attempts[next(iter(self.attempts))]  # insertion order: the oldest key
        self.attempts.setdefault(key, deque()).append(now)

    def refund(self, key: str) -> None:
        attempts = self.attempts.get(key)
        if attempts:
            attempts.pop()
            if not attempts:
                del self.attempts[key]


class MemoryStore:
    """In one process, under one lock. Unit tests and tools only: a restart forgets everything and
    a second process has its own counters."""

    def __init__(self, max_keys: int = MAX_LIMITER_KEYS) -> None:
        self._max_keys = max_keys
        self._windows: dict[str, Window] = {}
        self._lock = threading.Lock()

    def _window(self, gate: Gate) -> Window:
        window = self._windows.get(gate.bucket)
        if window is None:
            window = self._windows[gate.bucket] = Window(gate.limit, gate.window, self._max_keys)
        window.limit, window.window = gate.limit, gate.window
        return window

    def acquire(self, gates: Sequence[Gate], now: float | None = None) -> str | None:
        now = time.time() if now is None else now
        with self._lock:
            windows = [self._window(g) for g in gates]
            for window in windows:
                window.sweep(now)
            for g, window in zip(gates, windows, strict=True):
                if window.full(g.key, now):
                    return g.label
            for g, window in zip(gates, windows, strict=True):
                window.add(g.key, now)
            return None

    def full(self, gate: Gate, now: float | None = None) -> bool:
        now = time.time() if now is None else now
        with self._lock:
            return self._window(gate).full(gate.key, now)

    def add(self, gate: Gate, now: float | None = None) -> None:
        now = time.time() if now is None else now
        with self._lock:
            self._window(gate).add(gate.key, now)

    def refund(self, bucket: str, key: str) -> None:
        with self._lock:
            window = self._windows.get(bucket)
            if window is not None:
                window.refund(key)

    def clear(self, bucket: str, key: str) -> int:
        """Forget every hit for the key; how many were forgotten."""
        with self._lock:
            window = self._windows.get(bucket)
            return len(window.attempts.pop(key, ())) if window is not None else 0

    def size(self) -> int:
        with self._lock:
            return sum(len(w.attempts) for w in self._windows.values())

    def reset(self) -> None:
        with self._lock:
            self._windows.clear()


class DbStore:
    """The same, in `rate_hits`. `open_session` gives a session of its own (a context manager),
    so a count commits at once and never stands or falls with the request's transaction; `secret`
    keys the hash of the stored key."""

    def __init__(
        self, open_session: Callable[[], AbstractContextManager[Session]], secret: bytes
    ) -> None:
        self._open = open_session
        self._secret = secret

    def _hash(self, bucket: str, key: str) -> str:
        digest = hmac.new(self._secret, f"{bucket}\0{key}".encode(), hashlib.sha256)
        return digest.hexdigest()[:KEY_HEX]

    @staticmethod
    def _lock(session: Session, bucket: str, hashed: str) -> None:
        session.execute(
            text("select pg_advisory_xact_lock(:cls, hashtext(:name))"),
            {"cls": LOCK_CLASS, "name": f"{bucket}:{hashed}"},
        )

    def acquire(self, gates: Sequence[Gate], now: float | None = None) -> str | None:
        now = time.time() if now is None else now
        keyed = [(g, self._hash(g.bucket, g.key)) for g in gates]
        with self._open() as session:
            # Locks in one fixed order, so two calls that share keys cannot wait on each other.
            for bucket, hashed in sorted({(g.bucket, h) for g, h in keyed}):
                self._lock(session, bucket, hashed)
            for g, hashed in keyed:
                session.execute(
                    text("delete from rate_hits where bucket = :b and key = :k and at <= :cut"),
                    {"b": g.bucket, "k": hashed, "cut": now - g.window},
                )
            for g, hashed in keyed:
                count = session.scalar(
                    text("select count(*) from rate_hits where bucket = :b and key = :k"),
                    {"b": g.bucket, "k": hashed},
                )
                if count >= g.limit:
                    session.commit()  # the expired rows deleted above stay deleted
                    return g.label
            for g, hashed in keyed:
                session.execute(
                    text("insert into rate_hits (bucket, key, at) values (:b, :k, :at)"),
                    {"b": g.bucket, "k": hashed, "at": now},
                )
            session.commit()
            return None

    def full(self, gate: Gate, now: float | None = None) -> bool:
        now = time.time() if now is None else now
        with self._open() as session:
            count = session.scalar(
                text("select count(*) from rate_hits where bucket = :b and key = :k and at > :cut"),
                {
                    "b": gate.bucket,
                    "k": self._hash(gate.bucket, gate.key),
                    "cut": now - gate.window,
                },
            )
        return count >= gate.limit

    def add(self, gate: Gate, now: float | None = None) -> None:
        now = time.time() if now is None else now
        with self._open() as session:
            session.execute(
                text("insert into rate_hits (bucket, key, at) values (:b, :k, :at)"),
                {"b": gate.bucket, "k": self._hash(gate.bucket, gate.key), "at": now},
            )
            session.commit()

    def refund(self, bucket: str, key: str) -> None:
        hashed = self._hash(bucket, key)
        with self._open() as session:
            self._lock(session, bucket, hashed)
            session.execute(
                text(
                    "delete from rate_hits where id = (select id from rate_hits "
                    "where bucket = :b and key = :k order by at desc, id desc limit 1)"
                ),
                {"b": bucket, "k": hashed},
            )
            session.commit()

    def clear(self, bucket: str, key: str) -> int:
        """Delete every hit for the key; how many were deleted."""
        hashed = self._hash(bucket, key)
        with self._open() as session:
            self._lock(session, bucket, hashed)
            result = session.execute(
                text("delete from rate_hits where bucket = :b and key = :k"),
                {"b": bucket, "k": hashed},
            )
            session.commit()
            return result.rowcount

    def size(self) -> int:
        with self._open() as session:
            return session.scalar(
                text("select count(*) from (select distinct bucket, key from rate_hits) k")
            )

    def reset(self) -> None:
        with self._open() as session:
            session.execute(text("delete from rate_hits"))
            session.commit()


def origin_key(secret: bytes, client: str) -> str:
    """A short keyed hash of a client address, to remember where an account was made from without
    keeping the address (`users.signup_origin`). Its own label, so it matches no rate-limit key."""
    return hmac.new(secret, f"origin\0{client}".encode(), hashlib.sha256).hexdigest()[:KEY_HEX]


def store_secret(settings) -> bytes:
    """The key for hashing stored keys: derived from the session secret with its own label, so it
    is not the session or device key. A new session secret changes it, which only resets the
    counters."""
    return hmac.new(settings.session_secret.encode(), b"ratelimit", hashlib.sha256).digest()
