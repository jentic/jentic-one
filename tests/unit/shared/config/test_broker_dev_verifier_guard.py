"""Tests for broker development-verifier and shared-secret startup warnings."""

from __future__ import annotations

import os
from collections.abc import MutableMapping
from typing import Any
from unittest.mock import patch

import pytest
import structlog.testing
from pydantic import SecretStr

from jentic_one.shared.config import (
    AppConfig,
    BrokerConfig,
    JwtVerificationConfig,
    TrustedIssuerConfig,
)


def _minimal_databases() -> dict[str, Any]:
    return {
        "registry": {"name": "reg"},
        "admin": {"name": "admin"},
        "control": {"name": "ctrl"},
    }


def _dev_verifier_warnings(
    logs: list[MutableMapping[str, Any]],
) -> list[MutableMapping[str, Any]]:
    return [
        entry
        for entry in logs
        if entry.get("log_level") == "warning"
        and entry.get("event") == "broker_dev_jwt_secret_in_production"
    ]


def _shared_secret_warnings(
    logs: list[MutableMapping[str, Any]],
) -> list[MutableMapping[str, Any]]:
    return [
        entry
        for entry in logs
        if entry.get("log_level") == "warning"
        and entry.get("event") == "broker_jwt_secret_matches_admin_jwt_secret"
    ]


def test_broker_jwt_secret_in_production_without_trusted_issuers_warns_and_continues() -> None:
    secret_val = "b" * 32
    with (
        patch.dict(os.environ, {"JENTIC_ENV": "production"}, clear=False),
        structlog.testing.capture_logs() as logs,
    ):
        cfg = BrokerConfig(jwt_secret=SecretStr(secret_val))

    assert cfg.jwt_secret is not None
    assert cfg.jwt_secret.get_secret_value() == secret_val
    warnings = _dev_verifier_warnings(logs)
    assert len(warnings) == 1
    serialized = repr(warnings[0])
    assert secret_val not in serialized
    assert "32" not in serialized


def test_broker_jwt_secret_in_production_with_trusted_issuers_does_not_warn() -> None:
    secret_val = "b" * 32
    with (
        patch.dict(os.environ, {"JENTIC_ENV": "production"}, clear=False),
        structlog.testing.capture_logs() as logs,
    ):
        BrokerConfig(
            jwt_secret=SecretStr(secret_val),
            jwt_verification=JwtVerificationConfig(
                trusted_issuers=[
                    TrustedIssuerConfig(
                        issuer="https://idp.example.com",
                        jwks_url="https://idp.example.com/jwks.json",
                    )
                ]
            ),
        )

    assert _dev_verifier_warnings(logs) == []


def test_broker_jwt_secret_in_development_does_not_warn() -> None:
    secret_val = "b" * 32
    with (
        patch.dict(os.environ, {"JENTIC_ENV": "development"}, clear=False),
        structlog.testing.capture_logs() as logs,
    ):
        BrokerConfig(jwt_secret=SecretStr(secret_val))

    assert _dev_verifier_warnings(logs) == []


@pytest.mark.parametrize("env", ["production", "development"])
def test_broker_jwt_secret_matching_admin_jwt_secret_warns_and_continues(env: str) -> None:
    shared_val = "x" * 36
    with (
        patch.dict(os.environ, {"JENTIC_ENV": env}, clear=False),
        structlog.testing.capture_logs() as logs,
    ):
        cfg = AppConfig.model_validate(
            {
                "databases": _minimal_databases(),
                "admin": {
                    "auth": {"jwt_secret": shared_val},
                    "invite": {"pepper": "p" * 32},
                },
                "credentials": {"connect": {"state_secret": "c" * 32}},
                "broker": {"jwt_secret": shared_val},
            }
        )

    assert cfg.broker.jwt_secret is not None
    assert cfg.broker.jwt_secret.get_secret_value() == shared_val
    warnings = _shared_secret_warnings(logs)
    assert len(warnings) == 1
    serialized = repr(warnings[0])
    assert shared_val not in serialized
    assert "36" not in serialized


def test_distinct_broker_and_admin_jwt_secrets_do_not_warn() -> None:
    with (
        patch.dict(os.environ, {"JENTIC_ENV": "production"}, clear=False),
        structlog.testing.capture_logs() as logs,
    ):
        AppConfig.model_validate(
            {
                "databases": _minimal_databases(),
                "admin": {
                    "auth": {"jwt_secret": "a" * 32},
                    "invite": {"pepper": "p" * 32},
                },
                "credentials": {"connect": {"state_secret": "c" * 32}},
                "broker": {"jwt_secret": "b" * 32},
            }
        )

    assert _shared_secret_warnings(logs) == []
