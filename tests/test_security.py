"""Unit tests for Security_Manager encrypt/decrypt failure aborts (R21.9, R21.10)."""

from __future__ import annotations

import pytest
from cryptography.fernet import Fernet

from agent.security import (
    DecryptionError,
    EncryptionError,
    SecurityManager,
    key_from_secret,
)


class _ExplodingFernet:
    """A Fernet stand-in whose ``encrypt`` always fails."""

    def encrypt(self, data: bytes) -> bytes:  # noqa: D401 - test double
        raise RuntimeError("simulated cipher backend failure")

    def decrypt(self, token: bytes) -> bytes:  # pragma: no cover - unused here
        raise RuntimeError("simulated cipher backend failure")


def test_encrypt_failure_raises_and_writes_no_plaintext() -> None:
    """An encryption failure aborts the store without persisting plaintext."""
    manager = SecurityManager(fernet=_ExplodingFernet())
    persisted: list[bytes] = []

    def store_credential(value: str) -> None:
        # Encryption must happen *before* any write; the failure aborts here.
        token = manager.encrypt(value)
        persisted.append(token)

    with pytest.raises(EncryptionError):
        store_credential("super-secret-password")

    # No value -- and crucially no plaintext -- was ever written to the store.
    assert persisted == []


def test_decrypt_failure_aborts_dependent_operation() -> None:
    """A corrupted/invalid token aborts the operation needing the credential."""
    manager = SecurityManager(fernet_key=Fernet.generate_key())

    with pytest.raises(DecryptionError):
        manager.decrypt(b"not-a-valid-fernet-token")


def test_decrypt_with_wrong_key_aborts() -> None:
    """A token encrypted with one key cannot be decrypted with another."""
    writer = SecurityManager(fernet_key=Fernet.generate_key())
    token = writer.encrypt("api-token")

    reader = SecurityManager(fernet_key=Fernet.generate_key())
    with pytest.raises(DecryptionError):
        reader.decrypt(token)


def test_key_from_secret_is_deterministic_and_usable() -> None:
    """Deriving a key from the same secret yields a stable, working key."""
    key_a = key_from_secret("vps-master-secret")
    key_b = key_from_secret("vps-master-secret")
    assert key_a == key_b

    manager = SecurityManager(fernet_key=key_a)
    assert manager.decrypt(manager.encrypt("value")) == "value"
