"""Unit tests for encryption-key material resolution (inline / env / keychain)."""

from __future__ import annotations

import base64
import os
import subprocess

import pytest
from pydantic import SecretStr, ValidationError

from jentic_one.shared.config import ConfigError, EncryptionKey
from jentic_one.shared.crypto import key_material
from jentic_one.shared.crypto.key_material import resolve_key_material


def _material() -> str:
    return base64.b64encode(os.urandom(32)).decode()


# --- Source validation (exactly one) ---


def test_no_source_rejected():
    with pytest.raises(ValidationError, match="exactly one of"):
        EncryptionKey(id="v1")


def test_two_sources_rejected():
    with pytest.raises(ValidationError, match="exactly one of"):
        EncryptionKey(id="v1", material=SecretStr(_material()), material_env="SOME_VAR")


def test_inline_and_keychain_rejected():
    with pytest.raises(ValidationError, match="exactly one of"):
        EncryptionKey(id="v1", material=SecretStr(_material()), material_keychain="svc")


# --- Inline source ---


def test_inline_material_resolves():
    encoded = _material()
    entry = EncryptionKey(id="v1", material=SecretStr(encoded))
    assert resolve_key_material(entry) == base64.b64decode(encoded)


def test_inline_invalid_base64_raises_config_error():
    entry = EncryptionKey(id="v1", material=SecretStr("not-base64!!"))
    with pytest.raises(ConfigError, match="not valid base64"):
        resolve_key_material(entry)


# --- Env source ---


def test_env_material_resolves(monkeypatch):
    encoded = _material()
    monkeypatch.setenv("JENTIC_TEST_KEK", encoded)
    entry = EncryptionKey(id="v1", material_env="JENTIC_TEST_KEK")
    assert resolve_key_material(entry) == base64.b64decode(encoded)


def test_env_missing_raises_config_error(monkeypatch):
    monkeypatch.delenv("JENTIC_TEST_KEK", raising=False)
    entry = EncryptionKey(id="v1", material_env="JENTIC_TEST_KEK")
    with pytest.raises(ConfigError, match="JENTIC_TEST_KEK"):
        resolve_key_material(entry)


def test_env_empty_raises_config_error(monkeypatch):
    monkeypatch.setenv("JENTIC_TEST_KEK", "")
    entry = EncryptionKey(id="v1", material_env="JENTIC_TEST_KEK")
    with pytest.raises(ConfigError, match="not set or empty"):
        resolve_key_material(entry)


# --- Keychain source ---


def _fake_security(stdout: str = "", returncode: int = 0):
    def run(service: str) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(
            args=["/usr/bin/security", "find-generic-password", "-s", service, "-w"],
            returncode=returncode,
            stdout=stdout,
            stderr="",
        )

    return run


def test_keychain_material_resolves(monkeypatch):
    encoded = _material()
    monkeypatch.setattr("jentic_one.shared.crypto.key_material.sys.platform", "darwin")
    monkeypatch.setattr(key_material, "_run_security", _fake_security(stdout=encoded + "\n"))
    entry = EncryptionKey(id="v1", material_keychain="jentic-one-credentials-encryption-v1")
    assert resolve_key_material(entry) == base64.b64decode(encoded)


def test_keychain_lookup_failure_raises_config_error(monkeypatch):
    monkeypatch.setattr("jentic_one.shared.crypto.key_material.sys.platform", "darwin")
    monkeypatch.setattr(key_material, "_run_security", _fake_security(returncode=44))
    entry = EncryptionKey(id="v1", material_keychain="missing-item")
    with pytest.raises(ConfigError, match="missing-item"):
        resolve_key_material(entry)


def test_keychain_on_non_darwin_raises_config_error(monkeypatch):
    monkeypatch.setattr("jentic_one.shared.crypto.key_material.sys.platform", "linux")
    entry = EncryptionKey(id="v1", material_keychain="svc")
    with pytest.raises(ConfigError, match=r"only\s+supported on macOS"):
        resolve_key_material(entry)
