import base64
import subprocess
import sys

import pytest

from app import vault
from app.main import create_app
from tests.test_web import make_settings

# Placeholder keys: 32 bytes, base64, obviously not real ones.
KEY_A = base64.b64encode(b"A" * 32).decode()
KEY_B = base64.b64encode(b"B" * 32).decode()
TOKEN = "-".join(["refresh", "token", "placeholder"])


@pytest.fixture
def configure(monkeypatch):
    def _configure(key=KEY_A, previous="", version=1):
        settings = make_settings(
            token_enc_key=key, token_enc_key_previous=previous, token_enc_key_version=version
        )
        monkeypatch.setattr(vault, "get_settings", lambda: settings)

    _configure()
    return _configure


def test_round_trip(configure):
    blob, version = vault.encrypt(TOKEN, purpose="refresh_token", owner=7)
    assert version == 1 and isinstance(blob, bytes)
    secret = vault.decrypt(blob, version, purpose="refresh_token", owner=7)
    assert secret.reveal() == TOKEN


def test_the_blob_is_nonce_then_ciphertext_and_hides_the_plaintext(configure):
    blob, _ = vault.encrypt(TOKEN, purpose="refresh_token", owner=7)
    assert TOKEN.encode() not in blob
    assert len(blob) == 12 + len(TOKEN.encode()) + 16  # nonce, ciphertext, GCM tag


def test_same_plaintext_encrypts_differently(configure):
    first, _ = vault.encrypt(TOKEN, purpose="refresh_token", owner=7)
    second, _ = vault.encrypt(TOKEN, purpose="refresh_token", owner=7)
    assert first != second and first[:12] != second[:12]


def test_non_latin_text_round_trips(configure):
    text = "tökén-日本語-🔑"
    blob, version = vault.encrypt(text, purpose="ics_url", owner="host-a")
    assert vault.decrypt(blob, version, purpose="ics_url", owner="host-a").reveal() == text


def test_wrong_owner_fails(configure):
    blob, version = vault.encrypt(TOKEN, purpose="refresh_token", owner=1)
    with pytest.raises(vault.VaultError):
        vault.decrypt(blob, version, purpose="refresh_token", owner=2)


def test_wrong_purpose_fails(configure):
    blob, version = vault.encrypt(TOKEN, purpose="refresh_token", owner=1)
    with pytest.raises(vault.VaultError):
        vault.decrypt(blob, version, purpose="ics_url", owner=1)


def test_tampered_blob_fails(configure):
    blob, version = vault.encrypt(TOKEN, purpose="refresh_token", owner=1)
    for position in (0, 12, len(blob) - 1):  # the nonce, the ciphertext, the tag
        flipped = bytearray(blob)
        flipped[position] ^= 1
        with pytest.raises(vault.VaultError):
            vault.decrypt(bytes(flipped), version, purpose="refresh_token", owner=1)


@pytest.mark.parametrize("blob", [b"", b"short", b"x" * 27])
def test_truncated_blob_fails(configure, blob):
    with pytest.raises(vault.VaultError):
        vault.decrypt(blob, 1, purpose="refresh_token", owner=1)


def test_a_vault_error_never_carries_the_secret_or_the_key(configure):
    blob, version = vault.encrypt(TOKEN, purpose="refresh_token", owner=1)
    with pytest.raises(vault.VaultError) as raised:
        vault.decrypt(blob, version, purpose="refresh_token", owner=2)
    text = f"{raised.value!r} {raised.value}"
    assert TOKEN not in text and KEY_A not in text and blob.hex() not in text


def test_secret_repr_and_str_redacted(configure):
    blob, version = vault.encrypt(TOKEN, purpose="refresh_token", owner=1)
    secret = vault.decrypt(blob, version, purpose="refresh_token", owner=1)
    assert repr(secret) == "<redacted>" and str(secret) == "<redacted>"
    assert TOKEN not in f"{secret} {secret!r} {[secret]} {{'k': secret}}"
    with pytest.raises(TypeError):  # no pickling the plaintext into a cache or a log
        import pickle

        pickle.dumps(secret)


def test_previous_key_still_decrypts_and_needs_reencrypt(configure):
    configure(key=KEY_A, version=1)
    old_blob, old_version = vault.encrypt(TOKEN, purpose="refresh_token", owner=1)
    assert vault.needs_reencrypt(old_version) is False

    configure(key=KEY_B, previous=KEY_A, version=2)  # rotated: A moved to "previous"
    assert vault.decrypt(old_blob, old_version, purpose="refresh_token", owner=1).reveal() == TOKEN
    assert vault.needs_reencrypt(old_version) is True
    new_blob, new_version = vault.encrypt(TOKEN, purpose="refresh_token", owner=1)
    assert new_version == 2 and vault.needs_reencrypt(new_version) is False
    assert vault.decrypt(new_blob, new_version, purpose="refresh_token", owner=1).reveal() == TOKEN

    configure(key=KEY_B, previous="", version=2)  # the old key is removed: it is gone for good
    with pytest.raises(vault.VaultError):
        vault.decrypt(old_blob, old_version, purpose="refresh_token", owner=1)


def test_a_version_with_no_key_fails(configure):
    blob, _ = vault.encrypt(TOKEN, purpose="refresh_token", owner=1)
    with pytest.raises(vault.VaultError):
        vault.decrypt(blob, 5, purpose="refresh_token", owner=1)


def test_the_wrong_key_fails(configure):
    blob, version = vault.encrypt(TOKEN, purpose="refresh_token", owner=1)
    configure(key=KEY_B, version=1)
    with pytest.raises(vault.VaultError):
        vault.decrypt(blob, version, purpose="refresh_token", owner=1)


def test_missing_key_raises_vaultunavailable_on_use_not_import(configure):
    # Importing the module with no key configured must not raise.
    result = subprocess.run(
        [sys.executable, "-c", "import app.vault"],
        env={"PATH": "/usr/bin:/bin", "TOKEN_ENC_KEY": ""},
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    configure(key="")
    with pytest.raises(vault.VaultUnavailable):
        vault.encrypt(TOKEN, purpose="refresh_token", owner=1)
    with pytest.raises(vault.VaultUnavailable):
        vault.decrypt(b"x" * 40, 1, purpose="refresh_token", owner=1)
    assert issubclass(vault.VaultUnavailable, vault.VaultError)


@pytest.mark.parametrize(
    "key",
    [
        "not base64 at all!",
        base64.b64encode(b"short").decode(),
        base64.b64encode(b"A" * 33).decode(),
    ],
)
def test_a_malformed_key_is_unavailable(configure, key):
    configure(key=key)
    with pytest.raises(vault.VaultUnavailable) as raised:
        vault.encrypt(TOKEN, purpose="refresh_token", owner=1)
    assert key not in str(raised.value)


def test_production_start_refuses_without_key_when_google_client_id_set():
    google = {"google_client_id": "client-id", "google_client_secret": "client-secret"}
    with pytest.raises(RuntimeError, match="TOKEN_ENC_KEY"):
        create_app(make_settings(app_env="production", session_secret="x" * 40, **google))
    assert create_app(
        make_settings(app_env="production", session_secret="x" * 40, token_enc_key=KEY_A, **google)
    )


def test_start_without_google_or_outside_production_needs_no_key():
    assert create_app(make_settings(app_env="production", session_secret="x" * 40))
    assert create_app(make_settings(app_env="development", session_secret="x" * 40))
    assert create_app(make_settings(google_client_id="client-id"))  # app_env is test here


@pytest.mark.parametrize("env", ["production", "development", "test"])
def test_a_key_that_is_set_but_malformed_never_starts(env):
    with pytest.raises(RuntimeError, match="TOKEN_ENC_KEY"):
        create_app(make_settings(app_env=env, session_secret="x" * 40, token_enc_key="nope"))


def test_a_malformed_previous_key_never_starts():
    with pytest.raises(RuntimeError, match="TOKEN_ENC_KEY_PREVIOUS"):
        create_app(make_settings(token_enc_key=KEY_A, token_enc_key_previous="nope"))
