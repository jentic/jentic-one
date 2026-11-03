"""Integration tests for the credential connect flow — real DB, faked HTTP transports."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncGenerator, Awaitable
from datetime import UTC, datetime
from unittest.mock import AsyncMock, patch

import httpx
import pytest
from sqlalchemy import delete, select, text

from jentic_one.admin.repos.agent_credential_binding_repo import AgentCredentialBindingRepository
from jentic_one.control.core.schema.connect_nonces import ConnectNonce
from jentic_one.control.core.schema.credentials import Credential
from jentic_one.control.core.schema.customer_api_keys import CustomerAPIKey
from jentic_one.control.core.schema.oauth_client_credentials import OAuthClientCredential
from jentic_one.control.core.schema.oauth_tokens import OAuthToken
from jentic_one.control.repos import CredentialRepository, OAuthTokenRepository
from jentic_one.control.repos.effects_repo import EffectsRepository
from jentic_one.control.repos.prerequisite_repo import PrerequisiteRepository
from jentic_one.control.services.credentials.connect_service import ConnectFlowError, ConnectService
from jentic_one.control.services.credentials.errors import CredentialNotFoundError
from jentic_one.control.services.credentials.providers.direct_oauth2 import DirectOAuth2Provider
from jentic_one.control.services.credentials.providers.pipedream import PipedreamProvider
from jentic_one.control.services.credentials.schemas.connect import (
    AuthCodeChallenge,
    ConnectCallback,
    ConnectRequest,
)
from jentic_one.control.services.credentials.schemas.credentials import (
    CredentialUpdate,
    OAuth2Redacted,
)
from jentic_one.control.services.credentials.schemas.provision import OAuthTokenView
from jentic_one.control.services.credentials.service import CredentialService
from jentic_one.control.services.credentials.state import decode_state, new_browser_binding
from jentic_one.control.services.integrations.connect_session_service import (
    ConnectSessionService,
)
from jentic_one.control.services.integrations.errors import TokenEndpointMovedError
from jentic_one.control.services.integrations.flow_handlers.auth_code import AuthCodeFlowHandler
from jentic_one.control.services.integrations.flow_handlers.base import SuccessTokens
from jentic_one.shared.auth.identity import Identity
from jentic_one.shared.config import (
    DirectOAuth2ProviderConfig,
    EgressConfig,
    PipedreamProviderConfig,
)
from jentic_one.shared.context import Context
from jentic_one.shared.db.locks import advisory_lock_key
from jentic_one.shared.db.session import DatabaseSession
from jentic_one.shared.models import ActorType
from jentic_one.shared.models.credentials import CredentialType, StoredCredentialType

pytestmark = pytest.mark.integration

# The browser that starts each flow here; its cookie value accompanies the callback.
_BINDING = new_browser_binding()

_OWNER = Identity(sub="usr_test", permissions=["credentials:write"])
_OTHER_USER = Identity(sub="usr_someone_else", permissions=["credentials:write"])
_ADMIN = Identity(sub="usr_admin", permissions=["org:admin"])
_DELEGATED_AGENT = Identity(
    sub="agnt_delegated",
    actor_type=ActorType.AGENT,
    parent_actor_id=_OWNER.sub,
    permissions=["credentials:write", "owner:credentials:read"],
)
_AGENT_WITHOUT_DELEGATION = Identity(
    sub="agnt_undelegated",
    actor_type=ActorType.AGENT,
    parent_actor_id=_OWNER.sub,
    permissions=["credentials:write"],
)
_AGENT_OF_OTHER_OWNER = Identity(
    sub="agnt_other_owner",
    actor_type=ActorType.AGENT,
    parent_actor_id=_OTHER_USER.sub,
    permissions=["credentials:write", "owner:credentials:read"],
)


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
            created_by=_OWNER.sub,
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

    challenge = await svc.begin(
        credential_id,
        ConnectRequest(scopes=["read"]),
        identity=_OWNER,
        browser_binding=_BINDING.claim,
    )
    assert isinstance(challenge, AuthCodeChallenge)
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

        result_id = await svc.complete(
            challenge.state, ConnectCallback(code="auth-code-xyz"), browser_binding=_BINDING.value
        )

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
        credential_id,
        ConnectRequest(scopes=["read"]),
        identity=_OWNER,
        redirect_uri=derived,
        browser_binding=_BINDING.claim,
    )
    assert isinstance(challenge, AuthCodeChallenge)
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

        await svc.complete(
            challenge.state, ConnectCallback(code="auth-code-xyz"), browser_binding=_BINDING.value
        )

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
    challenge = await svc.begin(
        credential_id, ConnectRequest(), identity=_OWNER, browser_binding=_BINDING.claim
    )
    assert isinstance(challenge, AuthCodeChallenge)

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

        await svc.complete(
            challenge.state, ConnectCallback(code="code-1"), browser_binding=_BINDING.value
        )

        with pytest.raises(ConnectFlowError, match="already used"):
            await svc.complete(
                challenge.state, ConnectCallback(code="code-2"), browser_binding=_BINDING.value
            )

    async with ctx.control_db.session() as session:
        token = await OAuthTokenRepository.get_by_credential(session, credential_id)
        assert token is not None
        decrypted = ctx.encryption.decrypt(token.encrypted_access_token)
        assert decrypted == "at_1"


async def test_connect_refuses_a_credential_the_caller_does_not_own(
    integration_context: Context, clean_connect_tables: None
) -> None:
    """Connecting writes tokens onto the credential, so a non-owner gets 404.

    Otherwise a second user could attach their own vendor account to someone
    else's credential and every agent bound to it would act as that account.
    """
    ctx = integration_context
    credential_id = await _create_oauth2_credential(ctx, provider="my_oauth")
    provider = DirectOAuth2Provider(
        DirectOAuth2ProviderConfig(
            redirect_uri="https://app.example.com/credentials/oauth/callback"
        )
    )
    _patch_provider_registry(ctx, "my_oauth", provider)
    await _attach_oauth_client(ctx, credential_id)

    with pytest.raises(CredentialNotFoundError):
        await ConnectService(ctx).begin(credential_id, ConnectRequest(), identity=_OTHER_USER)

    async with ctx.control_db.session() as session:
        assert await OAuthTokenRepository.get_by_credential(session, credential_id) is None


async def test_org_admin_may_connect_any_credential(
    integration_context: Context, clean_connect_tables: None
) -> None:
    ctx = integration_context
    credential_id = await _create_oauth2_credential(ctx, provider="my_oauth")
    provider = DirectOAuth2Provider(
        DirectOAuth2ProviderConfig(
            redirect_uri="https://app.example.com/credentials/oauth/callback"
        )
    )
    _patch_provider_registry(ctx, "my_oauth", provider)
    await _attach_oauth_client(ctx, credential_id)

    challenge = await ConnectService(ctx).begin(credential_id, ConnectRequest(), identity=_ADMIN)
    assert isinstance(challenge, AuthCodeChallenge)


async def _connectable_credential(ctx: Context) -> str:
    """Create an owner-held direct-OAuth2 credential ready for ``begin``."""
    credential_id = await _create_oauth2_credential(ctx, provider="my_oauth")
    provider = DirectOAuth2Provider(
        DirectOAuth2ProviderConfig(
            redirect_uri="https://app.example.com/credentials/oauth/callback"
        )
    )
    _patch_provider_registry(ctx, "my_oauth", provider)
    await _attach_oauth_client(ctx, credential_id)
    return credential_id


async def test_owner_delegated_agent_may_connect_its_owners_credential(
    integration_context: Context, clean_connect_tables: None
) -> None:
    ctx = integration_context
    credential_id = await _connectable_credential(ctx)

    challenge = await ConnectService(ctx).begin(
        credential_id, ConnectRequest(), identity=_DELEGATED_AGENT
    )
    assert isinstance(challenge, AuthCodeChallenge)


@pytest.mark.parametrize(
    "identity",
    [_AGENT_WITHOUT_DELEGATION, _AGENT_OF_OTHER_OWNER],
    ids=["missing-owner-scope", "different-parent"],
)
async def test_agent_without_owner_delegation_cannot_connect(
    integration_context: Context, clean_connect_tables: None, identity: Identity
) -> None:
    ctx = integration_context
    credential_id = await _connectable_credential(ctx)

    with pytest.raises(CredentialNotFoundError):
        await ConnectService(ctx).begin(credential_id, ConnectRequest(), identity=identity)


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

        challenge = await svc.begin(
            credential_id, ConnectRequest(), identity=_OWNER, browser_binding=_BINDING.claim
        )

    assert isinstance(challenge, AuthCodeChallenge)
    assert "pipedream.com" in challenge.authorize_url

    result_id = await svc.complete(
        challenge.state, ConnectCallback(account_id="acct_pd_789"), browser_binding=_BINDING.value
    )
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

        challenge = await svc.begin(
            credential_id, ConnectRequest(), identity=_OWNER, browser_binding=_BINDING.claim
        )

    assert isinstance(challenge, AuthCodeChallenge)
    await svc.complete(
        challenge.state, ConnectCallback(account_id="acct_1"), browser_binding=_BINDING.value
    )

    with pytest.raises(ConnectFlowError, match="already used"):
        await svc.complete(
            challenge.state, ConnectCallback(account_id="acct_2"), browser_binding=_BINDING.value
        )

    async with ctx.control_db.session() as session:
        credential = await CredentialRepository.get_by_id(session, credential_id)
        assert credential is not None
        assert credential.provider_account_ref == "acct_1"


# --- Token-row lifecycle (repo-level) ---


_MOVED_TOKEN_URL = "https://login.other.example.org/token"


class _RecordingOAuth2(DirectOAuth2Provider):
    """Real direct-OAuth2 connect logic over a fake token endpoint."""

    def __init__(self) -> None:
        super().__init__(
            DirectOAuth2ProviderConfig(
                redirect_uri="https://app.example.com/credentials/oauth/callback"
            )
        )
        self.posted_to: list[str] = []
        # When set, the code exchange pauses after the vendor answered and
        # before the connect opens its token-write transaction.
        self.hold_after_exchange: asyncio.Event | None = None
        self.exchanged = asyncio.Event()

    async def _post_token(
        self, token_url: str, payload: dict[str, str], *, egress: EgressConfig | None
    ) -> dict[str, str]:
        self.posted_to.append(token_url)
        if self.hold_after_exchange is not None:
            self.exchanged.set()
            await self.hold_after_exchange.wait()
        return {"access_token": "at-reconnected", "refresh_token": "rt-reconnected"}


async def _connected(ctx: Context) -> str:
    """An auth-code credential with a live grant at the original token_url."""
    async with ctx.control_db.transaction() as session:
        credential = await CredentialRepository.create(
            session,
            type=StoredCredentialType.OAUTH2_AUTHORIZATION_CODE,
            name="Moved OAuth2",
            api_vendor="test-vendor",
            api_name="test-api",
            api_version="v1",
            provider="my_oauth",
            created_by=_OWNER.sub,
        )
        credential_id = credential.id
    await _attach_oauth_client(ctx, credential_id)
    async with ctx.control_db.transaction() as session:
        await OAuthTokenRepository.create(
            session,
            credential_id=credential_id,
            encrypted_access_token=ctx.encryption.encrypt("at-old"),
            encrypted_refresh_token=ctx.encryption.encrypt("rt-old"),
            created_by=_OWNER.sub,
        )
    return credential_id


async def _move_token_url(ctx: Context, credential_id: str) -> None:
    """The cross-origin token_url PATCH (re-entering the client secret)."""
    moved = await CredentialService(ctx).update(
        credential_id,
        CredentialUpdate(
            type=CredentialType.OAUTH2,
            token_url=_MOVED_TOKEN_URL,
            client_secret="moved-secret",  # pragma: allowlist secret
        ),
        identity=_OWNER,
    )
    assert isinstance(moved.details, OAuth2Redacted)
    assert moved.details.connected is False


async def _connected_then_moved(ctx: Context) -> str:
    """An auth-code credential with a live grant, then moved cross-origin."""
    credential_id = await _connected(ctx)
    await _move_token_url(ctx, credential_id)
    return credential_id


async def _assert_reconnected(ctx: Context, credential_id: str) -> None:
    view = await CredentialService(ctx).get(credential_id, identity=_OWNER)
    assert isinstance(view.details, OAuth2Redacted)
    assert view.details.connected is True
    async with ctx.control_db.session() as session:
        token = await OAuthTokenRepository.get_by_credential(session, credential_id)
    assert token is not None
    assert ctx.encryption.decrypt(token.encrypted_access_token) == "at-reconnected"


async def test_connect_complete_reconnects_after_token_url_move(
    integration_context: Context, clean_connect_tables: None
) -> None:
    """Pins #1425: after a cross-origin token_url move drops the grant, the
    credential-page connect creates a fresh token row against the new
    endpoint and the credential reads as connected again."""
    ctx = integration_context
    provider = _RecordingOAuth2()
    _patch_provider_registry(ctx, "my_oauth", provider)
    credential_id = await _connected_then_moved(ctx)

    svc = ConnectService(ctx)
    challenge = await svc.begin(
        credential_id, ConnectRequest(), identity=_OWNER, browser_binding=_BINDING.claim
    )
    assert isinstance(challenge, AuthCodeChallenge)
    await svc.complete(
        challenge.state, ConnectCallback(code="code-after-move"), browser_binding=_BINDING.value
    )

    assert provider.posted_to == [_MOVED_TOKEN_URL]
    await _assert_reconnected(ctx, credential_id)


async def test_connect_session_finalise_reconnects_after_token_url_move(
    integration_context: Context, clean_connect_tables: None
) -> None:
    """Pins #1425: the connect-session finalise write likewise creates a
    fresh token row for a credential whose grant was dropped."""
    ctx = integration_context
    _patch_provider_registry(ctx, "my_oauth", _RecordingOAuth2())
    credential_id = await _connected_then_moved(ctx)
    async with ctx.control_db.session() as session:
        credential = await CredentialRepository.get_by_id(session, credential_id)
    assert credential is not None

    await ConnectSessionService(ctx)._finalise_credential_connected(
        credential,
        AuthCodeFlowHandler(ctx),
        SuccessTokens(
            access_token="at-reconnected",
            refresh_token="rt-reconnected",
            expires_in=None,
            scope=None,
        ),
    )

    await _assert_reconnected(ctx, credential_id)


async def test_inflight_connect_discards_grant_minted_before_token_url_move(
    integration_context: Context, clean_connect_tables: None
) -> None:
    """Pins #1425: a credential-page connect whose code exchange at the old
    token endpoint is in flight when a cross-origin token_url edit commits
    must not store that grant beside the new token_url (the next refresh
    would present its refresh token to the new origin). The connect fails
    and the credential is left needing a reconnect."""
    ctx = integration_context
    provider = _RecordingOAuth2()
    _patch_provider_registry(ctx, "my_oauth", provider)
    credential_id = await _connected(ctx)
    svc = ConnectService(ctx)
    challenge = await svc.begin(
        credential_id, ConnectRequest(), identity=_OWNER, browser_binding=_BINDING.claim
    )
    assert isinstance(challenge, AuthCodeChallenge)
    release = asyncio.Event()
    provider.hold_after_exchange = release

    async with asyncio.TaskGroup() as tg:
        complete = tg.create_task(
            _raises(
                svc.complete(
                    challenge.state,
                    ConnectCallback(code="code-in-flight"),
                    browser_binding=_BINDING.value,
                )
            )
        )
        await asyncio.wait_for(provider.exchanged.wait(), timeout=5)
        await _move_token_url(ctx, credential_id)
        release.set()

    assert isinstance(complete.result(), ConnectFlowError)
    assert provider.posted_to == ["https://idp.example.com/token"]
    async with ctx.control_db.session() as session:
        assert await OAuthTokenRepository.get_by_credential(session, credential_id) is None
    view = await CredentialService(ctx).get(credential_id, identity=_OWNER)
    assert isinstance(view.details, OAuth2Redacted)
    assert view.details.connected is False


async def test_credential_mode_finalise_discards_grant_minted_before_token_url_move(
    integration_context: Context, clean_connect_tables: None
) -> None:
    """Pins #1425: the shared finalise write (here through the credential-mode
    entry, which has no session to fail) re-checks the token endpoint under
    the refresh lock and discards a grant exchanged at an origin the
    credential has since moved off. The session path, which turns this into
    a ``token_url_moved`` failure, is covered in
    ``test_connect_session_api_targets.py``."""
    ctx = integration_context
    _patch_provider_registry(ctx, "my_oauth", _RecordingOAuth2())
    credential_id = await _connected_then_moved(ctx)
    async with ctx.control_db.session() as session:
        credential = await CredentialRepository.get_by_id(session, credential_id)
    assert credential is not None

    with pytest.raises(TokenEndpointMovedError):
        await ConnectSessionService(ctx)._finalise_credential_connected(
            credential,
            AuthCodeFlowHandler(ctx),
            SuccessTokens(
                access_token="at-stale",
                refresh_token="rt-stale",
                expires_in=None,
                scope=None,
                token_url="https://idp.example.com/token",
            ),
        )

    async with ctx.control_db.session() as session:
        assert await OAuthTokenRepository.get_by_credential(session, credential_id) is None


async def _raises(awaitable: Awaitable[object]) -> BaseException | None:
    """Await *awaitable* and return the exception it raised (None if none)."""
    try:
        await awaitable
    except Exception as exc:
        return exc
    return None


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


async def test_state_without_browser_binding_is_refused_and_not_consumed(
    integration_context: Context, clean_connect_tables: None
) -> None:
    """A state signed without the ``bnd`` claim (e.g. in flight across the deploy
    that introduced it) is refused rather than completed unbound, and its nonce
    is left unconsumed. The SPA's popup lands on ``status=error`` and the user
    reconnects; nothing about the credential changes."""
    ctx = integration_context
    credential_id = await _create_oauth2_credential(ctx, provider="my_oauth")
    provider = DirectOAuth2Provider(
        DirectOAuth2ProviderConfig(
            redirect_uri="https://app.example.com/credentials/oauth/callback"
        )
    )
    _patch_provider_registry(ctx, "my_oauth", provider)
    await _attach_oauth_client(ctx, credential_id)

    svc = ConnectService(ctx)
    challenge = await svc.begin(credential_id, ConnectRequest(), identity=_OWNER)
    assert isinstance(challenge, AuthCodeChallenge)
    secret = ctx.config.credentials.connect.state_secret.get_secret_value()
    assert decode_state(secret, challenge.state).browser_binding_hash is None

    with patch("httpx.AsyncClient") as mock_client_cls:
        with pytest.raises(ConnectFlowError, match="starting browser"):
            await svc.complete(
                challenge.state, ConnectCallback(code="c"), browser_binding=_BINDING.value
            )
        mock_client_cls.assert_not_called()

    async with ctx.control_db.session() as session:
        assert await OAuthTokenRepository.get_by_credential(session, credential_id) is None
        consumed = (await session.execute(select(ConnectNonce))).scalars().all()
    assert consumed == []


# --- Binding writers wait on the credential's bindings lock ---

_LOCK_FIRST_AGENT = "agnt_lock_first"
_LOCK_SECOND_AGENT = "agnt_lock_second"


@pytest.fixture()
async def lock_agents(integration_context: Context) -> AsyncGenerator[None, None]:
    """Two approved agents registered by ``_OWNER``; their bindings are removed after."""
    ctx = integration_context
    await _delete_lock_agents(ctx)
    async with ctx.admin_db.transaction() as session:
        for agent_id in (_LOCK_FIRST_AGENT, _LOCK_SECOND_AGENT):
            await session.execute(
                text(
                    "INSERT INTO agents (id, name, registered_by, status, created_by) "
                    "VALUES (:id, :id, :owner, 'approved', :owner)"
                ),
                {"id": agent_id, "owner": _OWNER.sub},
            )
    yield
    await _delete_lock_agents(ctx)


async def _delete_lock_agents(ctx: Context) -> None:
    ids = {"a": _LOCK_FIRST_AGENT, "b": _LOCK_SECOND_AGENT}
    async with ctx.admin_db.transaction() as session:
        await session.execute(
            text("DELETE FROM agent_credential_bindings WHERE agent_id IN (:a, :b)"), ids
        )
        await session.execute(text("DELETE FROM agents WHERE id IN (:a, :b)"), ids)


def _require_postgres(ctx: Context) -> None:
    if ctx.admin_db.engine.dialect.name != "postgresql":
        pytest.skip(
            "the bindings lock is a Postgres advisory lock; on SQLite the admin "
            "BEGIN IMMEDIATE serialises binding writers"
        )


async def _parked_on_bindings_lock(ctx: Context, credential_id: str) -> None:
    """Return once a session waits on ``credential_id``'s bindings lock.

    Only that credential's key in the admin database counts. pg_locks splits
    a bigint advisory key into ``classid`` (high 32 bits) and ``objid`` (low
    32 bits) with ``objsubid = 1``.
    """
    key = advisory_lock_key(f"credentials.bindings.{credential_id}")
    while True:
        async with ctx.admin_db.session() as session:
            waiting = (
                await session.execute(
                    text(
                        "SELECT count(*) FROM pg_locks"
                        " WHERE locktype = 'advisory' AND NOT granted"
                        " AND database = (SELECT oid FROM pg_database"
                        " WHERE datname = current_database())"
                        " AND objsubid = 1 AND classid = :hi AND objid = :lo"
                    ),
                    {"hi": (key >> 32) & 0xFFFFFFFF, "lo": key & 0xFFFFFFFF},
                )
            ).scalar_one()
        if waiting:
            return
        await asyncio.sleep(0.01)


async def _bind_second_agent(ctx: Context, credential_id: str, binder: str) -> None:
    async with ctx.admin_db.transaction() as session:
        if binder == "admin_repository":
            await AgentCredentialBindingRepository.bind(
                session,
                agent_id=_LOCK_SECOND_AGENT,
                credential_id=credential_id,
                created_by=_OWNER.sub,
            )
        else:
            await EffectsRepository.bind_agent_to_credential(
                session,
                agent_id=_LOCK_SECOND_AGENT,
                credential_id=credential_id,
                rule_set_id=None,
                created_by=_OWNER.sub,
            )


async def _bound_agents(ctx: Context, credential_id: str) -> list[str]:
    async with ctx.admin_db.session() as session:
        rows = await PrerequisiteRepository.list_agents_for_credential(
            session, credential_id=credential_id
        )
    return sorted(row.agent_id for row in rows)


@pytest.mark.parametrize("binder", ["admin_repository", "connect_effects"])
async def test_binding_waits_while_the_bindings_lock_is_held(
    integration_context: Context,
    clean_connect_tables: None,
    lock_agents: None,
    binder: str,
) -> None:
    """Both binding writers park on the credential's bindings lock and
    commit only after the transaction holding it ends."""
    ctx = integration_context
    _require_postgres(ctx)
    credential_id = await _connected(ctx)

    async with asyncio.TaskGroup() as tg, ctx.admin_db.transaction() as held:
        await PrerequisiteRepository.lock_bindings_for_credential(held, credential_id=credential_id)
        bind = tg.create_task(_bind_second_agent(ctx, credential_id, binder))
        await asyncio.wait_for(_parked_on_bindings_lock(ctx, credential_id), timeout=5)
        assert not bind.done()
        assert await _bound_agents(ctx, credential_id) == []

    assert await _bound_agents(ctx, credential_id) == [_LOCK_SECOND_AGENT]
