"""Password rules and hashing.

Rules (NIST SP 800-63B, as decided for the pilot): at least 8 and at most 128 characters, not on
the common-password list, and nothing else: no composition rules, no expiry. The upper bound
keeps argon2 work bounded on public endpoints. Hashes are argon2id with the library's current
default parameters (`argon2.PasswordHasher()`, the RFC 9106 low-memory profile). Migration 0006
hashes the owner password with the same defaults.

`verify_password` always costs one hash check, also when there is no account or no password to
compare with (it checks against a dummy hash), so the time a login takes does not reveal which
emails have accounts.

Cost bound. One argon2id check with the default parameters (t=3, m=64 MiB, p=4) is the RFC 9106
"low memory" profile and is kept as it is: OWASP's minimum is far lower, and weakening the hash
protects nothing here. What needs a bound is how many run at once, because the login route is
public and the server is shared and memory-capped: 40 parallel requests would hold about 2.5 GB.
Every hash or check therefore takes one of `MAX_CONCURRENT` slots (about 64 MiB each); a request
that cannot get one within `SLOT_WAIT_SECONDS` raises `HashBusy` and the caller answers 429.
"""

import secrets
import threading
from functools import lru_cache
from pathlib import Path

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError

MIN_LENGTH = 8
MAX_LENGTH = 128
MAX_LOGIN_LENGTH = 1024  # a login attempt longer than this cannot match; do not hash it
MAX_CONCURRENT = 3  # hash computations at once: 3 x 64 MiB at the default parameters
SLOT_WAIT_SECONDS = 5.0  # how long a request may wait for a slot before it is told to retry
COMMON_PASSWORDS = Path(__file__).parent / "data" / "common-passwords.txt"

_hasher = PasswordHasher()
_slots = threading.BoundedSemaphore(MAX_CONCURRENT)
# Hash of a random password nobody knows (new at every start); only ever compared against.
DUMMY_HASH = _hasher.hash(secrets.token_urlsafe(24))


class HashBusy(Exception):
    """Every hash slot stayed taken for `SLOT_WAIT_SECONDS`: the server is busy, try again."""


class _Slot:
    """`with _Slot():` runs the body holding one hash slot, or raises `HashBusy`."""

    def __enter__(self) -> None:
        if not _slots.acquire(timeout=SLOT_WAIT_SECONDS):
            raise HashBusy

    def __exit__(self, *exc: object) -> None:
        _slots.release()


class PasswordRejected(ValueError):
    """A new password that does not meet the rules. `message` is safe to show as is."""

    code = "password_too_weak"

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


@lru_cache
def common_passwords() -> frozenset[str]:
    """Lower-cased entries of the common-password list (one per line, at least MIN_LENGTH long)."""
    lines = COMMON_PASSWORDS.read_text(encoding="utf-8").splitlines()
    return frozenset(line.strip().casefold() for line in lines if line.strip())


def check_new_password(password: str) -> None:
    """Raise `PasswordRejected` unless `password` may be used. Counts characters, not bytes."""
    if len(password) < MIN_LENGTH:
        raise PasswordRejected(f"Use at least {MIN_LENGTH} characters.")
    if len(password) > MAX_LENGTH:
        raise PasswordRejected(f"Use at most {MAX_LENGTH} characters.")
    if password.casefold() in common_passwords():
        raise PasswordRejected("That password is too common. Choose one that is harder to guess.")


def hash_password(password: str) -> str:
    with _Slot():
        return _hasher.hash(password)


def verify_password(stored_hash: str | None, password: str) -> bool:
    """Whether `password` matches `stored_hash`. With no hash (an unknown account, or a Google-only
    one) the password is checked against a dummy and the answer is False. Raises `HashBusy` when
    no hash slot frees up in time."""
    if len(password) > MAX_LOGIN_LENGTH:
        return False
    try:
        with _Slot():
            return _hasher.verify(stored_hash or DUMMY_HASH, password) and bool(stored_hash)
    except (VerificationError, InvalidHashError):
        return False
