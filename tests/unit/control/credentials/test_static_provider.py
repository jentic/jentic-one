"""Tests for StaticProvider behaviour."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from jentic_one.control.services.credentials.providers import (
    NotConnectableError,
    NotRefreshableError,
    StaticProvider,
)
from jentic_one.control.services.credentials.schemas import (
    APIReference,
    ConnectRequest,
    OAuthTokenView,
)
from jentic_one.shared.config import AppConfig, DatabaseConfig, DatabasesConfig
from jentic_one.shared.context import Context
from jentic_one.shared.models.credentials import CredentialType


def _make_context() -> Context:
    cfg = AppConfig(
        databases=DatabasesConfig(
            registry=DatabaseConfig(backend="sqlite", path=":memory:"),
            admin=DatabaseConfig(backend="sqlite", path=":memory:"),
            control=DatabaseConfig(backend="sqlite", path=":memory:"),
        )
    )
    return Context(cfg)


def test_supports_bearer_token() -> None:
    provider = StaticProvider()
    assert provider.supports(CredentialType.BEARER_TOKEN) is True


def test_supports_api_key() -> None:
    provider = StaticProvider()
    assert provider.supports(CredentialType.API_KEY) is True


def test_supports_basic() -> None:
    provider = StaticProvider()
    assert provider.supports(CredentialType.BASIC) is True


def test_supports_oauth2() -> None:
    provider = StaticProvider()
    assert provider.supports(CredentialType.OAUTH2) is True


def test_supports_sigv4() -> None:
    provider = StaticProvider()
    assert provider.supports(CredentialType.SIGV4) is True


@pytest.mark.asyncio()
async def test_begin_connect_raises() -> None:
    provider = StaticProvider()
    ctx = _make_context()
    api = APIReference(vendor="test", name="api", version="v1")
    req = ConnectRequest()
    with pytest.raises(NotConnectableError):
        await provider.begin_connect(ctx, api=api, request=req)


@pytest.mark.asyncio()
async def test_refresh_raises() -> None:
    provider = StaticProvider()
    ctx = _make_context()

    async def _fake_decrypt() -> str:
        return "secret"

    token_view = OAuthTokenView(
        credential_id="cred_123",
        provider="static",
        expires_at=datetime.now(UTC),
        decrypt=_fake_decrypt,
    )
    with pytest.raises(NotRefreshableError):
        await provider.refresh(ctx, token=token_view)
