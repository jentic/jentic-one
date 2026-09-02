"""Unit tests for the EncryptionService."""

from __future__ import annotations

import base64
import os

import pytest
from pydantic import SecretStr, ValidationError

from jentic_one.shared.config import ConfigError, EncryptionConfig, EncryptionKey
from jentic_one.shared.crypto import DecryptionError, EncryptionService


def _make_key(key_id: str = "v1") -> EncryptionKey:
    material = base64.b64encode(os.urandom(32)).decode()
    return EncryptionKey(id=key_id, material=SecretStr(material))


def _make_config(key_id: str = "v1", keys: list[EncryptionKey] | None = None) -> EncryptionConfig:
    if keys is None:
        keys = [_make_key(key_id)]
    return EncryptionConfig(active_id=key_id, entries=keys)


# --- Round-trip ---


def test_encrypt_decrypt_roundtrip():
    svc = EncryptionService(_make_config())
    plaintext = "super-secret-token-value"
    blob = svc.encrypt(plaintext)
    assert svc.decrypt(blob) == plaintext


def test_roundtrip_unicode():
    svc = EncryptionService(_make_config())
    plaintext = "пароль-密码-🔑"
    assert svc.decrypt(svc.encrypt(plaintext)) == plaintext


def test_roundtrip_empty_string():
    svc = EncryptionService(_make_config())
    assert svc.decrypt(svc.encrypt("")) == ""


def test_roundtrip_with_env_sourced_key(monkeypatch):
    material = base64.b64encode(os.urandom(32)).decode()
    monkeypatch.setenv("JENTIC_TEST_ENC_KEY", material)
    entry = EncryptionKey(id="v1", material_env="JENTIC_TEST_ENC_KEY")
    svc = EncryptionService(EncryptionConfig(active_id="v1", entries=[entry]))
    assert svc.decrypt(svc.encrypt("secret")) == "secret"


# --- Blob format ---


def test_blob_has_key_id_prefix():
    svc = EncryptionService(_make_config(key_id="v2", keys=[_make_key("v2")]))
    blob = svc.encrypt("hello")
    assert blob.startswith("v2:")


def test_blob_is_nondeterministic():
    svc = EncryptionService(_make_config())
    blob1 = svc.encrypt("same")
    blob2 = svc.encrypt("same")
    assert blob1 != blob2


# --- Decryption errors ---


def test_tampered_blob_raises():
    svc = EncryptionService(_make_config())
    blob = svc.encrypt("secret")
    parts = blob.split(":", 1)
    payload = list(parts[1])
    payload[5] = "A" if payload[5] != "A" else "B"
    tampered = parts[0] + ":" + "".join(payload)
    with pytest.raises(DecryptionError):
        svc.decrypt(tampered)


def test_unknown_key_id_raises():
    svc = EncryptionService(_make_config())
    with pytest.raises(DecryptionError, match="Unknown key_id"):
        svc.decrypt("nonexistent_key:AAAA")


def test_missing_separator_raises():
    svc = EncryptionService(_make_config())
    with pytest.raises(DecryptionError, match="missing key_id separator"):
        svc.decrypt("nocolonhere")


def test_short_payload_raises():
    svc = EncryptionService(_make_config())
    short = base64.b64encode(b"short").decode()
    with pytest.raises(DecryptionError):
        svc.decrypt(f"v1:{short}")


# --- Multi-key rotation ---


def test_old_key_blobs_still_decrypt():
    old_key = _make_key("v1")
    new_key = _make_key("v2")

    old_svc = EncryptionService(EncryptionConfig(active_id="v1", entries=[old_key]))
    old_blob = old_svc.encrypt("legacy-secret")

    rotated_svc = EncryptionService(EncryptionConfig(active_id="v2", entries=[old_key, new_key]))
    assert rotated_svc.decrypt(old_blob) == "legacy-secret"


def test_new_writes_use_current_key():
    old_key = _make_key("v1")
    new_key = _make_key("v2")

    svc = EncryptionService(EncryptionConfig(active_id="v2", entries=[old_key, new_key]))
    blob = svc.encrypt("new-secret")
    assert blob.startswith("v2:")


# --- Preview ---


def test_preview_long_string():
    assert EncryptionService.preview("my-api-token") == "…ken"


def test_preview_short_string_masked():
    assert EncryptionService.preview("ab") == "…***"


def test_preview_exactly_three_masked():
    assert EncryptionService.preview("abc") == "…***"


def test_preview_four_chars():
    assert EncryptionService.preview("abcd") == "…bcd"


# --- Boot validation ---


def test_empty_entries_raises():
    cfg = EncryptionConfig(active_id="v1", entries=[])
    with pytest.raises(ConfigError, match="must not be empty"):
        EncryptionService(cfg)


def test_active_id_not_in_entries_raises():
    cfg = EncryptionConfig(active_id="missing", entries=[_make_key("v1")])
    with pytest.raises(ConfigError, match="not found in entries"):
        EncryptionService(cfg)


def test_short_key_material_raises():
    short_material = base64.b64encode(b"tooshort").decode()
    entry = EncryptionKey(id="v1", material=SecretStr(short_material))
    cfg = EncryptionConfig(active_id="v1", entries=[entry])
    with pytest.raises(ConfigError, match="must be 32 bytes"):
        EncryptionService(cfg)


def test_duplicate_key_id_raises():
    key1 = _make_key("v1")
    key2 = _make_key("v1")
    cfg = EncryptionConfig(active_id="v1", entries=[key1, key2])
    with pytest.raises(ConfigError, match="Duplicate encryption key id"):
        EncryptionService(cfg)


def test_key_id_with_colon_rejected():
    with pytest.raises(ValidationError, match="key id must match"):
        EncryptionKey(id="aws:v1", material=SecretStr("dummy"))


def test_key_id_with_space_rejected():
    with pytest.raises(ValidationError, match="key id must match"):
        EncryptionKey(id="key 1", material=SecretStr("dummy"))
