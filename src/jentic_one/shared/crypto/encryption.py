"""Envelope encryption service using AES-256-GCM.

This is the ONLY module in the codebase permitted to import ``cryptography``.
An architecture test enforces this constraint.
"""

from __future__ import annotations

import base64
import os

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from jentic_one.shared.config import ConfigError, EncryptionConfig
from jentic_one.shared.crypto.key_material import resolve_key_material

_NONCE_BYTES = 12
_KEY_BYTES = 32


class DecryptionError(Exception):
    """Raised on unknown key_id or authentication failure."""


class EncryptionService:
    """AES-256-GCM envelope encryption with versioned key selection."""

    def __init__(self, cfg: EncryptionConfig) -> None:
        if not cfg.entries:
            raise ConfigError("EncryptionConfig.entries must not be empty")

        self._current_key_id = cfg.active_id
        self._ciphers: dict[str, AESGCM] = {}

        for entry in cfg.entries:
            if entry.id in self._ciphers:
                raise ConfigError(f"Duplicate encryption key id: '{entry.id}'")
            raw = resolve_key_material(entry)
            if len(raw) != _KEY_BYTES:
                raise ConfigError(
                    f"Encryption key '{entry.id}' must be {_KEY_BYTES} bytes, got {len(raw)}"
                )
            self._ciphers[entry.id] = AESGCM(raw)

        if self._current_key_id not in self._ciphers:
            raise ConfigError(
                f"EncryptionConfig.active_id '{self._current_key_id}' not found in entries"
            )

    def encrypt(self, plaintext: str) -> str:
        """Encrypt plaintext with the current key. Returns ``'<key_id>:<b64(nonce||ct||tag)>'``."""
        nonce = os.urandom(_NONCE_BYTES)
        ct_with_tag = self._ciphers[self._current_key_id].encrypt(nonce, plaintext.encode(), None)
        payload = base64.b64encode(nonce + ct_with_tag).decode()
        return f"{self._current_key_id}:{payload}"

    def decrypt(self, blob: str) -> str:
        """Decrypt a blob produced by :meth:`encrypt`. Raises :exc:`DecryptionError`."""
        sep = blob.find(":")
        if sep == -1:
            raise DecryptionError("Invalid blob format: missing key_id separator")

        key_id = blob[:sep]
        cipher = self._ciphers.get(key_id)
        if cipher is None:
            raise DecryptionError(f"Unknown key_id: '{key_id}'")

        try:
            raw = base64.b64decode(blob[sep + 1 :])
        except Exception as exc:
            raise DecryptionError("Invalid base64 payload") from exc

        if len(raw) < _NONCE_BYTES + 1:
            raise DecryptionError("Payload too short")

        nonce = raw[:_NONCE_BYTES]
        ciphertext = raw[_NONCE_BYTES:]

        try:
            plaintext_bytes = cipher.decrypt(nonce, ciphertext, None)
        except Exception as exc:
            raise DecryptionError("Decryption failed: authentication error") from exc

        return plaintext_bytes.decode()

    @staticmethod
    def preview(cleartext: str) -> str:
        """Return a redacted preview: ellipsis + last 3 chars (or masked if too short)."""
        if len(cleartext) <= 3:
            return "…***"
        return "…" + cleartext[-3:]
