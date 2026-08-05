"""Integration tests for the credential connect flow — real DB, faked HTTP transports."""

from __future__ import annotations

from collections.abc import AsyncGenerator
from datetime import UTC, datetime
from unittest.mock import AsyncMock, patch

import httpx
import pytest
from sqlalchemy import delete

from jentic_one.control.core.schema.connect_nonces import ConnectNonce
from jentic_one.control.core.schema.credentials import Credential
from jentic_one.control.core.schema.customer_api_keys import CustomerAPIKey
from jentic_one.control.core.schema.oauth_client_credentials import OAuthClientCredential
from jentic_one.control.core.schema.oauth_tokens import OAuthToken
from jentic_one.control.repos import CredentialRepository, OAuthTokenRepository
from jentic_one.control.services.credentials.connect_service import ConnectFlowError, ConnectService
from jentic_one.control.services.credentials.providers.direct_oauth2 import DirectOAuth2Provider
from jentic_one.control.services.credentials.providers.pipedream import PipedreamProvider
from jentic_one.control.services.credentials.schemas.connect import ConnectCallback, ConnectRequest
from jentic_one.control.services.credentials.schemas.provision import OAuthTokenView
from jentic_one.shared.config import DirectOAuth2ProviderConfig, PipedreamProviderConfig
from jentic_one.shared.context import Context
from jentic_one.shared.db.session import DatabaseSession
from jentic_one.shared.models.credentials import CredentialType

pytestmark = pytest.mark.integration


@pytest.fixture()
async def clean_connect_tables(control_db: DatabaseSession) -> AsyncGenerator[None, None]:
    """Clean credential tables before and after each test."""
    async with control_db.session() as session:
        await session.execute(delete(ConnectNonce))
        await session.execute(delete(OAuthToken))
        await session.execute(delete(OAuthClientCredential))
        await session.execute(delete(CustomerAPIKey))
        await session.execute(delete(Credential))
        await session.commit()
    yield
    async with control_db.session() as session:
        await session.execute(delete(ConnectNonce))
        await session.execute(delete(OAuthToken))
        await session.execute(delete(OAuthClientCredential))
        await session.execute(delete(CustomerAPIKey))
        await session.execute(delete(Credential))
        await session.commit()


async def _create_oauth2_credential(ctx: Context, *, provider: str = "static") -> str:
    """Create a bare oauth2 credential and return its id."""
    async with ctx.control_db.transaction() as session:
        credential = await CredentialRepository.create(
            session,
            type=CredentialType.OAUTH2,
            name="Test OAuth2",
            api_vendor="test-vendor",
            api_name="test-api",
            api_version="v1",
            provider=provider,
            created_by="usr_test",
        )
        return credential.id


async def _attach_oauth_client(
    ctx: Context,
    credential_id: str,
    *,
    authorize_url: str = "https://idp.example.com/authorize",
) -> None:
    """Attach an OAuthClientCredential row for the given credential."""
    encrypted_secret = ctx.encryption.encrypt("test-client-secret")
    async with ctx.control_db.transaction() as session:
        occ = OAuthClientCredential(
            id=credential_id,
            client_id="client-123",
            encrypted_client_secret=encrypted_secret,
            token_url="https://idp.example.com/token",
            authorize_url=authorize_url,
            scope="read write",
        )
        session.add(occ)
        await session.flush()


def _patch_provider_registry(ctx: Context, name: str, provider: object) -> None:
    """Replace one provider in the context's registry for testing.

    Intercepts both the registry key (name) and the provider's internal name
    attribute, since ConnectService.begin resolves by registry key but
    ConnectService.complete resolves by state.provider (the provider.name).
    """
    ctx._providers = None
    original_get = ctx.providers.get
    provider_name = getattr(provider, "name", None)

    def patched_get(n: str) -> object:
        if n in (name, provider_name):
            return provider
        return original_get(n)

    ctx.providers.get = patched_get  # type: ignore[assignment]


# --- DirectOAuth2 connect flow tests ---


async def test_direct_oauth2_connect_and_callback_stores_tokens(
    integration_context: Context, clean_connect_tables: None
) -> None:
    ctx = integration_context
    credential_id = await _create_oauth2_credential(ctx, provider="my_oauth")

    provider = DirectOAuth2Provider(
        DirectOAuth2ProviderConfig(
            redirect_uri="https://app.example.com/credentials/oauth/callback",
            default_scopes=["read", "write"],
        )
    )
    _patch_provider_registry(ctx, "my_oauth", provider)

    await _attach_oauth_client(ctx, credential_id)

    svc = ConnectService(ctx)

    # Snapshot the credential's updated_at before completing the flow — the
    # SPA polls this field to detect connect completion for `direct_oauth2`,
    # so the connect path must bump it.
    async with ctx.control_db.session() as session:
        snapshot = await CredentialRepository.get_by_id(session, credential_id)
        assert snapshot is not None
        updated_at_before = snapshot.updated_at

    challenge = await svc.begin(credential_id, ConnectRequest(scopes=["read"]))
    assert "https://idp.example.com/authorize" in challenge.authorize_url
    assert challenge.state

    token_response = {
        "access_token": "at_fresh_123",
        "refresh_token": "rt_fresh_456",
        "expires_in": 3600,
        "scope": "read write",
    }

    with patch("httpx.AsyncClient") as mock_client_cls:
        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.post = AsyncMock(return_value=httpx.Response(200, json=token_response))
        mock_client_cls.return_value = mock_client

        result_id = await svc.complete(challenge.state, ConnectCallback(code="auth-code-xyz"))

    assert result_id == credential_id

    async with ctx.control_db.session() as session:
        token = await OAuthTokenRepository.get_by_credential(session, credential_id)
        assert token is not None
        assert token.encrypted_access_token != ""
        assert token.encrypted_refresh_token is not None
        assert token.expires_at is not None
        assert token.expires_at > datetime.now(UTC)

        credential = await CredentialRepository.get_by_id(session, credential_id)
        assert credential is not None
        assert credential.provider_account_ref is None
        # `updated_at` must bump on a successful connect — the SPA's polling
        # loop relies on it for direct_oauth2 (which returns no
        # `provider_account_ref`) to close the OAuth popup.
        assert credential.updated_at > updated_at_before


async def test_direct_oauth2_derived_redirect_uri_round_trips(
    integration_context: Context, clean_connect_tables: None
) -> None:
    """Issue #818: with no configured redirect_uri, a request-derived callback
    (e.g. a non-default port) flows from begin_connect into the token exchange."""
    ctx = integration_context
    credential_id = await _create_oauth2_credential(ctx, provider="my_oauth")

    # Provider configured WITHOUT a redirect_uri — it must derive per request.
    provider = DirectOAuth2Provider(DirectOAuth2ProviderConfig(default_scopes=["read", "write"]))
    _patch_provider_registry(ctx, "my_oauth", provider)
    await _attach_oauth_client(ctx, credential_id)

    svc = ConnectService(ctx)
    derived = "http://127.0.0.1:8020/credentials/oauth/callback"
    challenge = await svc.begin(
        credential_id, ConnectRequest(scopes=["read"]), redirect_uri=derived
    )
    # The non-default port made it into the authorize URL.
    assert "8020" in challenge.authorize_url

    with patch("httpx.AsyncClient") as mock_client_cls:
        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.post = AsyncMock(
            return_value=httpx.Response(200, json={"access_token": "at", "expires_in": 3600})
        )
        mock_client_cls.return_value = mock_client

        await svc.complete(challenge.state, ConnectCallback(code="auth-code-xyz"))

        # The token exchange replays the derived redirect_uri byte-identically.
        sent_data = mock_client.post.await_args.kwargs["data"]
        assert sent_data["redirect_uri"] == derived


async def test_direct_oauth2_replay_rejected(
    integration_context: Context, clean_connect_tables: None
) -> None:
    ctx = integration_context
    credential_id = await _create_oauth2_credential(ctx, provider="my_oauth")

    provider = DirectOAuth2Provider(
        DirectOAuth2ProviderConfig(
            redirect_uri="https://app.example.com/credentials/oauth/callback",
        )
    )
    _patch_provider_registry(ctx, "my_oauth", provider)

    await _attach_oauth_client(ctx, credential_id)

    svc = ConnectService(ctx)
    challenge = await svc.begin(credential_id, ConnectRequest())

    token_response = {
        "access_token": "at_1",
        "refresh_token": "rt_1",
        "expires_in": 3600,
    }

    with patch("httpx.AsyncClient") as mock_client_cls:
        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.post = AsyncMock(return_value=httpx.Response(200, json=token_response))
        mock_client_cls.return_value = mock_client

        await svc.complete(challenge.state, ConnectCallback(code="code-1"))

        with pytest.raises(ConnectFlowError, match="already used"):
            await svc.complete(challenge.state, ConnectCallback(code="code-2"))

    async with ctx.control_db.session() as session:
        token = await OAuthTokenRepository.get_by_credential(session, credential_id)
        assert token is not None
        decrypted = ctx.encryption.decrypt(token.encrypted_access_token)
        assert decrypted == "at_1"


# --- Pipedream connect flow tests ---


async def test_pipedream_connect_and_callback_stores_account_ref(
    integration_context: Context, clean_connect_tables: None
) -> None:
    ctx = integration_context
    credential_id = await _create_oauth2_credential(ctx, provider="my_pipedream")

    provider = PipedreamProvider(
        PipedreamProviderConfig(
            project_id="proj_test",
            client_id="pd-cid",
            client_secret="pd-csecret",  # type: ignore[arg-type]
        )
    )
    _patch_provider_registry(ctx, "my_pipedream", provider)

    svc = ConnectService(ctx)

    app_token_resp = {"access_token": "pd-app-token"}
    connect_token_resp = {
        "token": "ct_test",
        "connect_link_url": "https://pipedream.com/connect/ct_test",
    }

    with patch("httpx.AsyncClient") as mock_client_cls:
        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.post = AsyncMock(
            side_effect=[
                httpx.Response(200, json=app_token_resp),
                httpx.Response(200, json=connect_token_resp),
            ]
        )
        mock_client_cls.return_value = mock_client

        challenge = await svc.begin(credential_id, ConnectRequest())

    assert "pipedream.com" in challenge.authorize_url

    result_id = await svc.complete(challenge.state, ConnectCallback(account_id="acct_pd_789"))
    assert result_id == credential_id

    async with ctx.control_db.session() as session:
        credential = await CredentialRepository.get_by_id(session, credential_id)
        assert credential is not None
        assert credential.provider_account_ref == "acct_pd_789"

        token = await OAuthTokenRepository.get_by_credential(session, credential_id)
        assert token is None


async def test_pipedream_refresh_populates_access_token(
    integration_context: Context, clean_connect_tables: None
) -> None:
    ctx = integration_context
    credential_id = await _create_oauth2_credential(ctx, provider="my_pipedream")

    provider = PipedreamProvider(
        PipedreamProviderConfig(
            project_id="proj_test",
            client_id="pd-cid",
            client_secret="pd-csecret",  # type: ignore[arg-type]
        )
    )

    async with ctx.control_db.transaction() as session:
        credential = await CredentialRepository.get_by_id(session, credential_id)
        assert credential is not None
        credential.provider_account_ref = "acct_pd_789"
        await session.flush()

    app_token_resp = {"access_token": "pd-app-token"}
    account_token_resp = {
        "access_token": "vendor-live-at",
        "expires_in": 3600,
    }

    async def fake_decrypt() -> str:
        return ""

    token_view = OAuthTokenView(
        credential_id=credential_id,
        provider="my_pipedream",
        provider_account_ref="acct_pd_789",
        expires_at=None,
        decrypt=fake_decrypt,
    )

    with patch("httpx.AsyncClient") as mock_client_cls:
        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.post = AsyncMock(return_value=httpx.Response(200, json=app_token_resp))
        mock_client.get = AsyncMock(return_value=httpx.Response(200, json=account_token_resp))
        mock_client_cls.return_value = mock_client

        result = await provider.refresh(ctx, token=token_view)

    assert result.access_token == "vendor-live-at"
    assert result.expires_at is not None


async def test_pipedream_replay_rejected(
    integration_context: Context, clean_connect_tables: None
) -> None:
    ctx = integration_context
    credential_id = await _create_oauth2_credential(ctx, provider="my_pipedream")

    provider = PipedreamProvider(
        PipedreamProviderConfig(
            project_id="proj_test",
            client_id="pd-cid",
            client_secret="pd-csecret",  # type: ignore[arg-type]
        )
    )
    _patch_provider_registry(ctx, "my_pipedream", provider)

    svc = ConnectService(ctx)

    app_token_resp = {"access_token": "pd-app-token"}
    connect_token_resp = {
        "token": "ct_test",
        "connect_link_url": "https://pipedream.com/connect/ct_test",
    }

    with patch("httpx.AsyncClient") as mock_client_cls:
        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.post = AsyncMock(
            side_effect=[
                httpx.Response(200, json=app_token_resp),
                httpx.Response(200, json=connect_token_resp),
            ]
        )
        mock_client_cls.return_value = mock_client

        challenge = await svc.begin(credential_id, ConnectRequest())

    await svc.complete(challenge.state, ConnectCallback(account_id="acct_1"))

    with pytest.raises(ConnectFlowError, match="already used"):
        await svc.complete(challenge.state, ConnectCallback(account_id="acct_2"))

    async with ctx.control_db.session() as session:
        credential = await CredentialRepository.get_by_id(session, credential_id)
        assert credential is not None
        assert credential.provider_account_ref == "acct_1"


# --- Token-row lifecycle (repo-level) ---


async def test_reconnect_clears_revocation(
    integration_context: Context, clean_connect_tables: None
) -> None:
    """`update_tokens` (the re-connect/refresh write path) clears `revoked_at`.

    A revoked row that receives fresh tokens is live again — leaving the old
    revocation stamp would permanently pin the derived `connected` flag to
    False after a successful re-connect (#890 review board).
    """
    ctx = integration_context
    credential_id = await _create_oauth2_credential(ctx, provider="static")

    async with ctx.control_db.transaction() as session:
        await OAuthTokenRepository.create(
            session,
            credential_id=credential_id,
            encrypted_access_token="enc-old",
            created_by="usr_test",
        )
        await OAuthTokenRepository.mark_revoked(session, credential_id)

    async with ctx.control_db.transaction() as session:
        row = await OAuthTokenRepository.get_by_credential(session, credential_id)
        assert row is not None
        assert row.revoked_at is not None

        updated = await OAuthTokenRepository.update_tokens(
            session,
            credential_id,
            encrypted_access_token="enc-new",
        )
        assert updated is not None
        assert updated.revoked_at is None
