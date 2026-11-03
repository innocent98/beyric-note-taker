"""Encrypts secrets at rest (Google refresh tokens, a pasted calendar link) with AES-256-GCM.

A value is stored as `nonce || ciphertext` (12-byte random nonce, the GCM tag at the end of the
ciphertext) next to the `key_version` that made it. The authenticated data is `purpose:owner`, so a
blob copied to another user, or used for another purpose, fails to decrypt instead of yielding
someone else's secret. The key is `TOKEN_ENC_KEY` in the server `.env`: never in the database,
never in a backup. Rotation: put the new key in `TOKEN_ENC_KEY`, move the old one to
`TOKEN_ENC_KEY_PREVIOUS`, raise `TOKEN_ENC_KEY_VERSION` by one, re-encrypt every row whose version
`needs_reencrypt`, then remove the previous key.

Nothing here logs, and no error message holds a key, a plaintext or a blob.
"""

import base64
import binascii
import json
import os

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from app.config import Settings, get_settings

# Every kind of secret the vault holds. Part of the authenticated data, so a blob made for one
# purpose never opens for another; a new kind is added here on purpose.
PURPOSES = ("refresh_token", "ics_url", "host_api_key")
NONCE_BYTES = 12
KEY_BYTES = 32
TAG_BYTES = 16


class VaultError(Exception):
    """A value could not be encrypted or decrypted. Callers treat it as "reconnect", never as a
    crash, and never show the cause."""


class VaultUnavailable(VaultError):
    """No usable key is configured. Raised on use, not on import, so the app and its tests start
    without one."""


class Secret:
    """A decrypted value that cannot leak by accident: `repr` and `str` are `<redacted>`, and it
    refuses to be pickled. Read it with `reveal()` at the one place that needs it."""

    __slots__ = ("_value",)

    def __init__(self, value: str) -> None:
        self._value = value

    def reveal(self) -> str:
        return self._value

    def __repr__(self) -> str:
        return "<redacted>"

    __str__ = __repr__

    def __reduce__(self):
        raise TypeError("a Secret cannot be pickled")


def _load_key(value: str, name: str) -> bytes:
    try:
        key = base64.b64decode(value, validate=True)
    except (binascii.Error, ValueError):
        raise VaultUnavailable(f"{name} is not valid base64") from None
    if len(key) != KEY_BYTES:
        raise VaultUnavailable(f"{name} must decode to {KEY_BYTES} bytes")
    return key


def _key_for(version: int, settings: Settings) -> bytes:
    """The key that made `version`: the current one, or the previous one (`version - 1`) while a
    rotation is in progress."""
    current = settings.token_enc_key_version
    if version == current:
        if not settings.token_enc_key:
            raise VaultUnavailable("TOKEN_ENC_KEY is not set")
        return _load_key(settings.token_enc_key, "TOKEN_ENC_KEY")
    if version == current - 1 and settings.token_enc_key_previous:
        return _load_key(settings.token_enc_key_previous, "TOKEN_ENC_KEY_PREVIOUS")
    raise VaultError(f"no key for key version {version}")


def _aad(purpose: str, owner: str | int) -> bytes:
    """Authenticated data: a JSON array of the purpose and the owner, so no choice of text can run
    one field into the other, and an int owner (a user id) differs from a str owner (a host name)
    that spells the same."""
    if purpose not in PURPOSES:
        raise ValueError(f"unknown vault purpose: {purpose!r}")
    if isinstance(owner, bool) or not isinstance(owner, (int, str)):
        raise ValueError("the vault owner must be a user id (int) or a name (str)")
    return json.dumps([purpose, owner], separators=(",", ":")).encode()


def encrypt(plaintext: str, *, purpose: str, owner: str | int) -> tuple[bytes, int]:
    """Returns `(nonce || ciphertext, key_version)`, always under the current key."""
    settings = get_settings()
    version = settings.token_enc_key_version
    key = _key_for(version, settings)
    nonce = os.urandom(NONCE_BYTES)
    ciphertext = AESGCM(key).encrypt(nonce, plaintext.encode(), _aad(purpose, owner))
    return nonce + ciphertext, version


def decrypt(blob: bytes, key_version: int, *, purpose: str, owner: str | int) -> Secret:
    key = _key_for(key_version, get_settings())
    if len(blob) < NONCE_BYTES + TAG_BYTES:
        raise VaultError("the stored value is too short to be valid")
    nonce, ciphertext = bytes(blob[:NONCE_BYTES]), bytes(blob[NONCE_BYTES:])
    try:
        return Secret(AESGCM(key).decrypt(nonce, ciphertext, _aad(purpose, owner)).decode())
    except (InvalidTag, UnicodeDecodeError):
        raise VaultError("the stored value could not be decrypted") from None


def needs_reencrypt(key_version: int) -> bool:
    """True for a row written under an older key version; the re-encrypt script moves it."""
    return key_version != get_settings().token_enc_key_version


def check_settings(settings: Settings) -> None:
    """Refuse to start on a key setup that cannot work: a key that is set but unusable, a version
    below 1, a previous key that is the current one (or has no version before it to belong to),
    and, in production, no key at all once Google or the calendar features are switched on (the
    first thing stored is a refresh token or a pasted link)."""
    for value, name in (
        (settings.token_enc_key, "TOKEN_ENC_KEY"),
        (settings.token_enc_key_previous, "TOKEN_ENC_KEY_PREVIOUS"),
    ):
        if value:
            try:
                _load_key(value, name)
            except VaultUnavailable as exc:
                raise RuntimeError(f"{exc} (generate one with: openssl rand -base64 32)") from None
    if settings.token_enc_key_version < 1:
        raise RuntimeError("TOKEN_ENC_KEY_VERSION must be 1 or more")
    if settings.token_enc_key_previous:
        if settings.token_enc_key_previous == settings.token_enc_key:
            raise RuntimeError("TOKEN_ENC_KEY_PREVIOUS must be a different key from TOKEN_ENC_KEY")
        if settings.token_enc_key_version < 2:
            raise RuntimeError(
                "TOKEN_ENC_KEY_VERSION must be 2 or more when TOKEN_ENC_KEY_PREVIOUS is set "
                "(the previous key is the one before the current version)"
            )
    wants_key = settings.google_client_id or settings.calendar_enabled
    if settings.app_env == "production" and wants_key and not settings.token_enc_key:
        raise RuntimeError(
            "TOKEN_ENC_KEY is required when GOOGLE_CLIENT_ID is set or CALENDAR_ENABLED is true: "
            "it encrypts the calendar refresh tokens and pasted calendar links "
            "(generate one with: openssl rand -base64 32)"
        )
