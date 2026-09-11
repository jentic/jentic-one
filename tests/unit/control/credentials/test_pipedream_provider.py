"""Tests for PipedreamProvider behaviour."""

from __future__ import annotations

import base64
import os
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest
from pydantic import SecretStr

from jentic_one.control.services.credentials.providers.base import ProviderError
from jentic_one.control.services.credentials.providers.pipedream import (
    PipedreamAPIError,
    PipedreamProvider,
)
from jentic_one.control.services.credentials.schemas.connect import (
    ConnectCallback,
    ConnectRequest,
    ConnectState,
)
from jentic_one.control.services.credentials.schemas.provision import (
    APIReference,
    OAuthTokenView,
)
from jentic_one.shared.config import (
    AppConfig,
    ConnectConfig,
    CredentialsConfig,
    DatabaseConfig,
    DatabasesConfig,
    EncryptionConfig,
    EncryptionKey,
    PipedreamProviderConfig,
)
from jentic_one.shared.context import Context
from jentic_one.shared.models.credentials import CredentialType

_KEY_MATERIAL = base64.b64encode(os.urandom(32)).decode()


def _make_config() -> AppConfig:
    return AppConfig(
        databases=DatabasesConfig(
            registry=DatabaseConfig(backend="sqlite", path=":memory:"),
            admin=DatabaseConfig(backend="sqlite", path=":memory:"),
            control=DatabaseConfig(backend="sqlite", path=":memory:"),
        ),
        credentials=CredentialsConfig(
            encryption=EncryptionConfig(
                active_id="v1",
                entries=[EncryptionKey(id="v1", material=SecretStr(_KEY_MATERIAL))],
            ),
            connect=ConnectConfig(
                state_secret=SecretStr("test-state-secret"),
                state_ttl_seconds=600,
            ),
            providers={
                "my_pipedream": PipedreamProviderConfig(
                    project_id="proj_abc123",
                    environment="development",
                    client_id="pd-client-id",
                    client_secret=SecretStr("pd-client-secret"),
                    connect_base_url="https://api.pipedream.com/v1",
                ),
            },
        ),
    )


def _make_provider() -> PipedreamProvider:
    cfg = PipedreamProviderConfig(
        project_id="proj_abc123",
        environment="development",
        client_id="pd-client-id",
        client_secret=SecretStr("pd-client-secret"),
        connect_base_url="https://api.pipedream.com/v1",
        expiry_skew_seconds=60,
    )
    return PipedreamProvider(cfg)


@asynccontextmanager
async def _fake_session():
    yield MagicMock()


def _mock_control_db() -> MagicMock:
    db = MagicMock()
    db.session = _fake_session
    return db


def test_supports_oauth2() -> None:
    provider = _make_provider()
    assert provider.supports(CredentialType.OAUTH2) is True


def test_does_not_support_other_types() -> None:
    provider = _make_provider()
    assert provider.supports(CredentialType.BEARER_TOKEN) is False
    assert provider.supports(CredentialType.API_KEY) is False
    assert provider.supports(CredentialType.BASIC) is False


def test_name_is_pipedream() -> None:
    provider = _make_provider()
    assert provider.name == "pipedream"


@pytest.mark.asyncio()
async def test_begin_connect_creates_connect_token_and_returns_link() -> None:
    provider = _make_provider()
    ctx = Context(_make_config())

    app_token_response = {"access_token": "pd-app-token-123"}
    connect_token_response = {
        "token": "ct_abc",
        "connect_link_url": "https://pipedream.com/connect/ct_abc",
    }

    request = ConnectRequest(
        scopes=["read"],
        extra={"credential_id": "cred_123", "actor_id": "user_1"},
    )
    api = APIReference(vendor="slack", name="slack-api", version="v2")

    with patch("httpx.AsyncClient") as mock_client_cls:
        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.post = AsyncMock(
            side_effect=[
                httpx.Response(200, json=app_token_response),
                httpx.Response(200, json=connect_token_response),
            ]
        )
        mock_client_cls.return_value = mock_client

        challenge = await provider.begin_connect(ctx, api=api, request=request)

    assert challenge.authorize_url == "https://pipedream.com/connect/ct_abc"
    assert challenge.state


@pytest.mark.asyncio()
async def test_begin_connect_raises_on_missing_credential_id() -> None:
    provider = _make_provider()
    ctx = Context(_make_config())
    request = ConnectRequest(scopes=[], extra={})
    api = APIReference(vendor="slack", name="", version="")

    with pytest.raises(ProviderError, match="credential_id required"):
        await provider.begin_connect(ctx, api=api, request=request)


@pytest.mark.asyncio()
async def test_begin_connect_raises_on_app_token_failure() -> None:
    provider = _make_provider()
    ctx = Context(_make_config())
    request = ConnectRequest(
        scopes=[],
        extra={"credential_id": "cred_123", "actor_id": ""},
    )
    api = APIReference(vendor="slack", name="", version="")

    with patch("httpx.AsyncClient") as mock_client_cls:
        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.post = AsyncMock(return_value=httpx.Response(401, text="Unauthorized"))
        mock_client_cls.return_value = mock_client

        with pytest.raises(PipedreamAPIError) as exc_info:
            await provider.begin_connect(ctx, api=api, request=request)
        assert exc_info.value.status == 401


@pytest.mark.asyncio()
async def test_complete_connect_maps_account_id() -> None:
    provider = _make_provider()
    ctx = Context(_make_config())

    state = ConnectState(
        credential_id="cred_123",
        provider="pipedream",
        actor_id="user_1",
        issued_at=datetime.now(UTC),
        nonce="test-nonce",
    )
    callback = ConnectCallback(account_id="acct_pd_456")

    result = await provider.complete_connect(ctx, state=state, callback=callback)

    assert result.provider_account_ref == "acct_pd_456"
    assert result.access_token is None
    assert result.refresh_token is None
    assert result.expires_at is None


@pytest.mark.asyncio()
async def test_complete_connect_raises_on_error_callback() -> None:
    provider = _make_provider()
    ctx = Context(_make_config())

    state = ConnectState(
        credential_id="cred_123",
        provider="pipedream",
        actor_id=None,
        issued_at=datetime.now(UTC),
        nonce="test-nonce",
    )
    callback = ConnectCallback(error="access_denied")

    with pytest.raises(ProviderError, match="Authorization denied"):
        await provider.complete_connect(ctx, state=state, callback=callback)


@pytest.mark.asyncio()
async def test_complete_connect_raises_on_missing_account_id() -> None:
    provider = _make_provider()
    ctx = Context(_make_config())

    state = ConnectState(
        credential_id="cred_123",
        provider="pipedream",
        actor_id=None,
        issued_at=datetime.now(UTC),
        nonce="test-nonce",
    )
    callback = ConnectCallback()

    with pytest.raises(ProviderError, match="No account_id"):
        await provider.complete_connect(ctx, state=state, callback=callback)


@pytest.mark.asyncio()
async def test_refresh_fetches_access_token_for_account() -> None:
    provider = _make_provider()
    ctx = Context(_make_config())

    app_token_response = {"access_token": "pd-app-token-123"}
    account_response = {
        "data": {
            "id": "acct_pd_456",
            "credentials": {"oauth_access_token": "vendor-at-live"},
            "expires_at": "2099-01-01T00:00:00Z",
        }
    }

    async def fake_decrypt() -> str:
        return ""

    token_view = OAuthTokenView(
        credential_id="cred_123",
        provider="pipedream",
        provider_account_ref="acct_pd_456",
        expires_at=None,
        decrypt=fake_decrypt,
    )

    with patch("httpx.AsyncClient") as mock_client_cls:
        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.post = AsyncMock(return_value=httpx.Response(200, json=app_token_response))
        mock_client.get = AsyncMock(return_value=httpx.Response(200, json=account_response))
        mock_client_cls.return_value = mock_client

        result = await provider.refresh(ctx, token=token_view)

    assert result.access_token == "vendor-at-live"
    assert result.expires_at is not None


@pytest.mark.asyncio()
async def test_refresh_falls_back_to_api_key_for_keys_apps() -> None:
    """Key-based Pipedream apps (e.g. Stripe) expose api_key, not oauth_access_token."""
    provider = _make_provider()
    ctx = Context(_make_config())

    app_token_response = {"access_token": "pd-app-token-123"}
    account_response = {
        "id": "acct_pd_456",
        "credentials": {"api_key": "sk_live_secret"},
    }

    async def fake_decrypt() -> str:
        return ""

    token_view = OAuthTokenView(
        credential_id="cred_123",
        provider="pipedream",
        provider_account_ref="acct_pd_456",
        expires_at=None,
        decrypt=fake_decrypt,
    )

    with patch("httpx.AsyncClient") as mock_client_cls:
        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.post = AsyncMock(return_value=httpx.Response(200, json=app_token_response))
        mock_client.get = AsyncMock(return_value=httpx.Response(200, json=account_response))
        mock_client_cls.return_value = mock_client

        result = await provider.refresh(ctx, token=token_view)

    assert result.access_token == "sk_live_secret"
    assert result.expires_at is None


@pytest.mark.asyncio()
async def test_refresh_raises_on_missing_account_ref() -> None:
    provider = _make_provider()
    ctx = Context(_make_config())

    async def fake_decrypt() -> str:
        return ""

    token_view = OAuthTokenView(
        credential_id="cred_123",
        provider="pipedream",
        provider_account_ref=None,
        expires_at=None,
        decrypt=fake_decrypt,
    )

    with pytest.raises(ProviderError, match="No provider_account_ref"):
        await provider.refresh(ctx, token=token_view)


@pytest.mark.asyncio()
async def test_refresh_raises_on_api_failure() -> None:
    provider = _make_provider()
    ctx = Context(_make_config())

    app_token_response = {"access_token": "pd-app-token-123"}

    async def fake_decrypt() -> str:
        return ""

    token_view = OAuthTokenView(
        credential_id="cred_123",
        provider="pipedream",
        provider_account_ref="acct_pd_456",
        expires_at=None,
        decrypt=fake_decrypt,
    )

    with patch("httpx.AsyncClient") as mock_client_cls:
        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.post = AsyncMock(return_value=httpx.Response(200, json=app_token_response))
        mock_client.get = AsyncMock(return_value=httpx.Response(500, text="Internal Server Error"))
        mock_client_cls.return_value = mock_client

        with pytest.raises(PipedreamAPIError) as exc_info:
            await provider.refresh(ctx, token=token_view)
        assert exc_info.value.status == 500


@pytest.mark.asyncio()
async def test_app_token_cached_across_calls() -> None:
    provider = _make_provider()
    ctx = Context(_make_config())

    app_token_response = {"access_token": "pd-app-token-cached"}
    connect_token_response = {
        "token": "ct_1",
        "connect_link_url": "https://pipedream.com/connect/ct_1",
    }

    request = ConnectRequest(
        scopes=[],
        extra={"credential_id": "cred_1", "actor_id": ""},
    )
    api = APIReference(vendor="v", name="", version="")

    with patch("httpx.AsyncClient") as mock_client_cls:
        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.post = AsyncMock(
            side_effect=[
                httpx.Response(200, json=app_token_response),
                httpx.Response(200, json=connect_token_response),
                httpx.Response(200, json=connect_token_response),
            ]
        )
        mock_client_cls.return_value = mock_client

        await provider.begin_connect(ctx, api=api, request=request)
        await provider.begin_connect(ctx, api=api, request=request)

        # App token fetched once (first post), connect token twice (second+third posts)
        assert mock_client.post.call_count == 3
