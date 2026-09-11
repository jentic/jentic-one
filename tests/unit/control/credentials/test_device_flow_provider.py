"""Tests for DeviceFlowConnectProvider.

Mirrors the shape of test_direct_oauth2_provider: patches
``httpx.AsyncClient`` for token-endpoint round-trips and repositories
for the aux-row reads. The device-flow provider is a *public* OAuth 2.0
client — no ``client_secret`` at the token endpoint — so the refresh
payload MUST omit it. That's the load-bearing invariant these tests pin.
"""

from __future__ import annotations

import base64
import os
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest
from pydantic import SecretStr

from jentic_one.control.services.credentials.providers.base import (
    NotConnectableError,
    NotRefreshableError,
    ProviderError,
)
from jentic_one.control.services.credentials.providers.device_flow import (
    DeviceFlowConnectProvider,
)
from jentic_one.control.services.credentials.providers.oauth2 import (
    InvalidGrantError,
    TokenExchangeError,
)
from jentic_one.control.services.credentials.schemas.connect import (
    ConnectCallback,
    ConnectRequest,
    ConnectState,
    DeviceCodeChallenge,
)
from jentic_one.control.services.credentials.schemas.provision import OAuthTokenView
from jentic_one.control.services.integrations.device_flow import BeginResult
from jentic_one.shared.config import (
    AppConfig,
    ConnectConfig,
    CredentialsConfig,
    DatabaseConfig,
    DatabasesConfig,
    EncryptionConfig,
    EncryptionKey,
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
        ),
    )


@asynccontextmanager
async def _fake_session():
    yield MagicMock()


@asynccontextmanager
async def _fake_transaction():
    yield MagicMock()


def _mock_control_db() -> MagicMock:
    db = MagicMock()
    db.session = _fake_session
    db.transaction = _fake_transaction
    return db


class _FakeDFC:
    """Stand-in for a ``DeviceFlowCredential`` ORM row."""

    def __init__(
        self,
        *,
        client_id: str = "public-client",
        token_url: str = "https://idp.example.com/token",
        authorization_endpoint: str = "https://idp.example.com/device/code",
        requested_scopes: list[str] | None = None,
    ) -> None:
        self.client_id = client_id
        self.token_url = token_url
        self.authorization_endpoint = authorization_endpoint
        self.requested_scopes = requested_scopes or []


def test_supports_oauth2() -> None:
    provider = DeviceFlowConnectProvider()
    assert provider.supports(CredentialType.OAUTH2)
    assert not provider.supports(CredentialType.BEARER_TOKEN)
    assert provider.supported_types == [CredentialType.OAUTH2]


def test_managed_false() -> None:
    # Device flow is user-connectable, not managed like Pipedream — the
    # discovery UI classifies it under the same "user has to connect"
    # bucket as static, not managed-by-us.
    assert DeviceFlowConnectProvider().managed is False


def test_name_is_device_flow() -> None:
    # The registry looks providers up by ``credential.provider``; the
    # scanner + connect router both dispatch on this name, so it's a
    # wire contract, not a display string.
    assert DeviceFlowConnectProvider().name == "device_flow"


@pytest.mark.asyncio()
async def test_complete_connect_raises_not_connectable() -> None:
    # Device flow completes server-side via the poll scanner, never via
    # a callback — the router should never reach here, but pin the
    # explicit refusal so a routing regression can't silently succeed.
    provider = DeviceFlowConnectProvider()
    ctx = Context(_make_config())
    state = ConnectState(
        credential_id="cred_123",
        provider="device_flow",
        actor_id="user_1",
        issued_at=datetime.now(UTC),
        nonce="nonce",
    )
    with pytest.raises(NotConnectableError):
        await provider.complete_connect(ctx, state=state, callback=ConnectCallback())


@pytest.mark.asyncio()
async def test_begin_connect_requires_credential_id() -> None:
    provider = DeviceFlowConnectProvider()
    ctx = Context(_make_config())
    with pytest.raises(ProviderError, match="credential_id required"):
        await provider.begin_connect(
            ctx,
            api=MagicMock(),
            request=ConnectRequest(),
        )


@pytest.mark.asyncio()
async def test_begin_connect_raises_when_no_device_flow_row() -> None:
    provider = DeviceFlowConnectProvider()
    ctx = Context(_make_config())
    ctx._control_db = _mock_control_db()
    with (
        patch(
            "jentic_one.control.repos.device_flow_credential_repo."
            "DeviceFlowCredentialRepository.get_by_credential",
            new_callable=AsyncMock,
            return_value=None,
        ),
        pytest.raises(NotConnectableError, match="grant_type=device_code"),
    ):
        await provider.begin_connect(
            ctx,
            api=MagicMock(),
            request=ConnectRequest(extra={"credential_id": "cred_1"}),
        )


@pytest.mark.asyncio()
async def test_begin_connect_seeds_transient_state_and_returns_challenge() -> None:
    provider = DeviceFlowConnectProvider()
    ctx = Context(_make_config())
    ctx._control_db = _mock_control_db()

    dfc = _FakeDFC(requested_scopes=["read"])
    device_result = BeginResult(
        device_code="dev-code-xyz",
        user_code="ABCD-1234",
        verification_uri="https://idp.example.com/device",
        verification_uri_complete="https://idp.example.com/device?user_code=ABCD-1234",
        expires_in=1800,
        interval=5,
    )

    seed_mock = AsyncMock()
    with (
        patch(
            "jentic_one.control.repos.device_flow_credential_repo."
            "DeviceFlowCredentialRepository.get_by_credential",
            new_callable=AsyncMock,
            return_value=dfc,
        ),
        patch(
            "jentic_one.control.repos.device_flow_credential_repo."
            "DeviceFlowCredentialRepository.set_transient_state",
            new=seed_mock,
        ),
        patch(
            "jentic_one.control.services.credentials.providers.device_flow.df.begin_device_flow",
            new_callable=AsyncMock,
            return_value=device_result,
        ),
    ):
        challenge = await provider.begin_connect(
            ctx,
            api=MagicMock(),
            request=ConnectRequest(extra={"credential_id": "cred_1"}),
        )

    assert isinstance(challenge, DeviceCodeChallenge)
    assert challenge.user_code == "ABCD-1234"
    assert challenge.verification_uri == "https://idp.example.com/device"
    assert challenge.poll_interval_seconds == 5
    # The transient device_code / expiry MUST be written before the
    # scanner picks it up — otherwise the first tick would exit early
    # (no encrypted_device_code) and the flow would stall silently.
    seed_mock.assert_awaited_once()
    assert seed_mock.await_args is not None
    kwargs = seed_mock.await_args.kwargs
    assert kwargs["user_code"] == "ABCD-1234"
    assert kwargs["poll_interval_seconds"] == 5
    # Encrypted, not plaintext — never store device_code in the clear.
    assert kwargs["encrypted_device_code"] != "dev-code-xyz"


@pytest.mark.asyncio()
async def test_refresh_raises_not_refreshable_without_device_flow_row() -> None:
    provider = DeviceFlowConnectProvider()
    ctx = Context(_make_config())
    ctx._control_db = _mock_control_db()
    token = OAuthTokenView(
        credential_id="cred_1",
        provider="device_flow",
        expires_at=datetime.now(UTC),
        decrypt=AsyncMock(return_value="rt"),
    )
    with (
        patch(
            "jentic_one.control.repos.device_flow_credential_repo."
            "DeviceFlowCredentialRepository.get_by_credential",
            new_callable=AsyncMock,
            return_value=None,
        ),
        pytest.raises(NotRefreshableError),
    ):
        await provider.refresh(ctx, token=token)


@pytest.mark.asyncio()
async def test_refresh_omits_client_secret() -> None:
    # THE public-client invariant: RFC 8628 device flow uses a public
    # OAuth client; the refresh payload must never carry a client_secret.
    # If this test breaks, the wire request is leaking a nonexistent
    # secret (best case: 400 from the IdP; worst case: broken auth).
    provider = DeviceFlowConnectProvider()
    ctx = Context(_make_config())
    ctx._control_db = _mock_control_db()

    dfc = _FakeDFC(client_id="public-client")
    token_response = {
        "access_token": "at_rotated",
        "refresh_token": "rt_rotated",
        "expires_in": 3600,
    }

    async def fake_decrypt() -> str:
        return "old-refresh-token"

    token_view = OAuthTokenView(
        credential_id="cred_1",
        provider="device_flow",
        expires_at=datetime.now(UTC),
        decrypt=fake_decrypt,
    )

    with (
        patch(
            "jentic_one.control.repos.device_flow_credential_repo."
            "DeviceFlowCredentialRepository.get_by_credential",
            new_callable=AsyncMock,
            return_value=dfc,
        ),
        patch("httpx.AsyncClient") as mock_client_cls,
    ):
        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.post = AsyncMock(return_value=httpx.Response(200, json=token_response))
        mock_client_cls.return_value = mock_client

        result = await provider.refresh(ctx, token=token_view)

        call = mock_client.post.await_args
        posted_payload: dict[str, str] = call.kwargs["data"]

    assert result.access_token == "at_rotated"
    assert result.refresh_token == "rt_rotated"
    assert posted_payload["grant_type"] == "refresh_token"
    assert posted_payload["client_id"] == "public-client"
    assert "client_secret" not in posted_payload


@pytest.mark.asyncio()
async def test_refresh_maps_invalid_grant_via_shared_base() -> None:
    # The shared ``OAuth2Provider._post_token`` maps invalid_grant → the
    # typed InvalidGrantError; the device-flow provider inherits it.
    # Callers upstream distinguish this from transient upstream faults,
    # so the mapping must survive the inheritance change.
    provider = DeviceFlowConnectProvider()
    ctx = Context(_make_config())
    ctx._control_db = _mock_control_db()

    dfc = _FakeDFC()

    async def fake_decrypt() -> str:
        return "revoked-refresh"

    token_view = OAuthTokenView(
        credential_id="cred_1",
        provider="device_flow",
        expires_at=datetime.now(UTC),
        decrypt=fake_decrypt,
    )

    with (
        patch(
            "jentic_one.control.repos.device_flow_credential_repo."
            "DeviceFlowCredentialRepository.get_by_credential",
            new_callable=AsyncMock,
            return_value=dfc,
        ),
        patch("httpx.AsyncClient") as mock_client_cls,
    ):
        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.post = AsyncMock(
            return_value=httpx.Response(400, text='{"error":"invalid_grant"}')
        )
        mock_client_cls.return_value = mock_client

        with pytest.raises(InvalidGrantError):
            await provider.refresh(ctx, token=token_view)


@pytest.mark.asyncio()
async def test_refresh_maps_other_failures_to_token_exchange_error() -> None:
    provider = DeviceFlowConnectProvider()
    ctx = Context(_make_config())
    ctx._control_db = _mock_control_db()

    dfc = _FakeDFC()

    async def fake_decrypt() -> str:
        return "some-refresh"

    token_view = OAuthTokenView(
        credential_id="cred_1",
        provider="device_flow",
        expires_at=datetime.now(UTC),
        decrypt=fake_decrypt,
    )

    with (
        patch(
            "jentic_one.control.repos.device_flow_credential_repo."
            "DeviceFlowCredentialRepository.get_by_credential",
            new_callable=AsyncMock,
            return_value=dfc,
        ),
        patch("httpx.AsyncClient") as mock_client_cls,
    ):
        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.post = AsyncMock(return_value=httpx.Response(503, text="upstream down"))
        mock_client_cls.return_value = mock_client

        with pytest.raises(TokenExchangeError) as exc_info:
            await provider.refresh(ctx, token=token_view)
    assert exc_info.value.status == 503
