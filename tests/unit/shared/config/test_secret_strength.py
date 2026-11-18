"""Tests for HMAC secret-strength startup warnings."""

from __future__ import annotations

import os
from collections.abc import MutableMapping
from typing import Any
from unittest.mock import patch

import pytest
import structlog.testing
from pydantic import SecretStr

from jentic_one.shared.config import AdminAuthConfig, AdminInviteConfig, ConnectConfig


def _secret_warnings(logs: list[MutableMapping[str, Any]]) -> list[MutableMapping[str, Any]]:
    return [
        entry
        for entry in logs
        if entry.get("log_level") == "warning" and entry.get("event") == "insecure_jwt_secret"
    ]


@pytest.mark.parametrize("env", ["production", "development"])
def test_short_jwt_secret_logs_structured_warning_and_continues(env: str) -> None:
    short_secret = "s" * 31
    with (
        patch.dict(os.environ, {"JENTIC_ENV": env}, clear=False),
        structlog.testing.capture_logs() as logs,
    ):
        cfg = AdminAuthConfig(jwt_secret=SecretStr(short_secret))

    assert cfg.jwt_secret.get_secret_value() == short_secret
    warnings = _secret_warnings(logs)
    assert len(warnings) == 1
    assert warnings[0]["field_path"] == "admin.auth.jwt_secret"
    serialized = repr(warnings[0])
    assert short_secret not in serialized
    assert "31" not in serialized


@pytest.mark.parametrize("env", ["production", "development"])
def test_32_byte_jwt_secret_emits_no_warning(env: str) -> None:
    strong_secret = "k" * 32
    with (
        patch.dict(os.environ, {"JENTIC_ENV": env}, clear=False),
        structlog.testing.capture_logs() as logs,
    ):
        cfg = AdminAuthConfig(jwt_secret=SecretStr(strong_secret))

    assert cfg.jwt_secret.get_secret_value() == strong_secret
    assert _secret_warnings(logs) == []


def test_other_guarded_secrets_do_not_emit_jwt_secret_warning() -> None:
    with (
        patch.dict(os.environ, {"JENTIC_ENV": "production"}, clear=False),
        structlog.testing.capture_logs() as logs,
    ):
        AdminInviteConfig(pepper=SecretStr("short-pepper"))
        ConnectConfig(state_secret=SecretStr("short-state"))

    assert _secret_warnings(logs) == []
