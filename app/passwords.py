"""Password rules and hashing.

Rules (NIST SP 800-63B, as decided for the pilot): at least 8 and at most 128 characters, not on
the common-password list, and nothing else: no composition rules, no expiry. The upper bound
keeps argon2 work bounded on public endpoints. Hashes are argon2id with the library's current
default parameters (`argon2.PasswordHasher()`, the RFC 9106 low-memory profile). Migration 0006
hashes the owner password with the same defaults.

`verify_password` always costs one hash check, also when there is no account or no password to
compare with (it checks against a dummy hash), so the time a login takes does not reveal which
emails have accounts.
"""

from functools import lru_cache
from pathlib import Path

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError

MIN_LENGTH = 8
MAX_LENGTH = 128
MAX_LOGIN_LENGTH = 1024  # a login attempt longer than this cannot match; do not hash it
COMMON_PASSWORDS = Path(__file__).parent / "data" / "common-passwords.txt"

_hasher = PasswordHasher()
# Hash of a random password nobody knows; only ever compared against.
DUMMY_HASH = _hasher.hash("dummy-password-for-unknown-accounts-3f9a1c")


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
    return _hasher.hash(password)


def verify_password(stored_hash: str | None, password: str) -> bool:
    """Whether `password` matches `stored_hash`. With no hash (an unknown account, or a Google-only
    one) the password is checked against a dummy and the answer is False."""
    if len(password) > MAX_LOGIN_LENGTH:
        return False
    try:
        return _hasher.verify(stored_hash or DUMMY_HASH, password) and bool(stored_hash)
    except (VerificationError, InvalidHashError):
        return False
