"""Session reconnect grant replacement and refresh-token carry-over rules.

A reconnect replaces the credential's grant rather than merging omitted fields
into the previous grant. The previous refresh token is carried over only when
the new grant omitted one, the credential uses the same OAuth app registration,
and identity echo positively confirms the same vendor account.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, patch
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest
from pydantic import SecretStr

from jentic_one.broker.services.credentials.errors import RefreshInvalidGrantError
from jentic_one.broker.services.credentials.refresh import TokenRefresher
from jentic_one.broker.services.credentials.resolver import ResolvedCredential
from jentic_one.control.core.schema.oauth_client_credentials import OAuthClientCredential
from jentic_one.control.repos.connect_session_repo import ConnectSessionRepository
from jentic_one.control.repos.credential_repo import CredentialRepository
from jentic_one.control.repos.oauth_app_registration_repo import (
    OAuthAppRegistrationRepository,
)
from jentic_one.control.repos.oauth_token_repo import OAuthTokenRepository
from jentic_one.control.services.credentials.state import new_browser_binding
from jentic_one.control.services.integrations import identity_echo
from jentic_one.control.services.integrations.connect_session_service import (
    AuthCodeConfirmResult,
)
from jentic_one.control.services.integrations.flow_handlers.auth_code import (
    AuthCodeFlowHandler,
)
from jentic_one.control.services.integrations.flow_handlers.base import (
    AuthCodeBeginResult,
    SuccessTokens,
)
from jentic_one.control.services.integrations.flow_handlers.session_app import (
    SessionApp,
)
from jentic_one.shared.config import (
    VendorAuthConfig,
    VendorAuthorizationCodeFlowConfig,
    VendorIdentityProbeConfig,
    VendorScopeConfig,
)
from jentic_one.shared.context import Context
from jentic_one.shared.models.credentials import CredentialType, StoredCredentialType
from tests.integration.control.connect_sec.support import (
    AGENT_ID,
    NAME,
    OWNER,
    OWNER_ID,
    VENDOR,
    svc,
)

pytestmark = pytest.mark.integration

_BINDING = new_browser_binding()
_CATALOG_API_ID = f"{VENDOR}/{NAME}"


def _configure_vendor_with_identity_probe(ctx: Context) -> None:
    ctx.config.vendors.entries[VENDOR] = VendorAuthConfig(
        vendor=_CATALOG_API_ID,
        display_name="Vault Vendor",
        flows=[
            VendorAuthorizationCodeFlowConfig(
                client_id="config-vault-client",
                client_secret=SecretStr("config-vault-secret"),  # pragma: allowlist secret
                authorize_url="https://auth.vault.example/authorize",
                token_url="https://auth.vault.example/token",
            )
        ],
        scopes=[
            VendorScopeConfig(name="read", classification="read", default=True),
            VendorScopeConfig(name="write", classification="write", default=False),
        ],
        identity_probe=VendorIdentityProbeConfig(
            endpoint="https://api.vault.example/me",
            identity_field="username",
            display_template="{username}",
        ),
    )


async def _create_registration(ctx: Context, *, name: str = "Shared Vault App") -> str:
    async with ctx.control_db.transaction() as session:
        reg = await OAuthAppRegistrationRepository.create_authorization_code(
            session,
            name=name,
            api_vendor=VENDOR,
            catalog_api_id=_CATALOG_API_ID,
            display_name=name,
            client_id="shared-vault-client",
            encrypted_client_secret=ctx.encryption.encrypt("shared-vault-secret"),
            authorize_url="https://auth.vault.example/authorize",
            token_url="https://auth.vault.example/token",
            default_scopes=["read", "write"],
            created_by=OWNER_ID,
        )
        return reg.id


def _state_from_authorize_url(authorize_url: str) -> str:
    return parse_qs(urlsplit(authorize_url).query)["state"][0]


async def _initial_session_connect(
    ctx: Context,
    *,
    registration_id: str,
    account: str = "alice",
    access_token: str = "at-alice-1",
    refresh_token: str = "rt-alice-1",
    scope: str = "read write",
) -> str:
    service = svc(ctx)
    created = await service.create_session(
        vendor_key=VENDOR,
        agent_id=AGENT_ID,
        identity=OWNER,
        requested_scopes=["read", "write"],
        oauth_app_registration_id=registration_id,
    )
    async with ctx.control_db.session() as session:
        row = await ConnectSessionRepository.get_by_id(session, created.session_id)
        assert row is not None and row.current_revision_id is not None
        revision_id = row.current_revision_id
        credential_id = row.credential_id

    confirmed = await service.confirm(
        created.session_id,
        revision_id=revision_id,
        poll_token=created.poll_token,
        confirmed_scopes=["read", "write"],
        permission_rules=[],
        identity=OWNER,
        browser_binding=_BINDING.claim,
    )
    assert isinstance(confirmed, AuthCodeConfirmResult)

    token_resp = httpx.Response(
        200,
        json={
            "access_token": access_token,
            "refresh_token": refresh_token,
            "expires_in": 3600,
            "scope": scope,
        },
    )
    echo_res = identity_echo.IdentityEchoResult(display=account, raw={"username": account})
    with (
        patch(
            "jentic_one.shared.http.idp_client.build_always_pinned_transport",
            return_value=httpx.MockTransport(lambda _req: token_resp),
        ),
        patch.object(
            identity_echo,
            "echo_identity",
            new=AsyncMock(return_value=echo_res),
        ),
    ):
        status = await service.complete_from_callback(
            raw_state=_state_from_authorize_url(confirmed.authorize_url),
            code="code-initial",
            browser_binding=_BINDING.value,
        )
    assert status.status == "connected"
    assert status.connected_as == account
    return credential_id


async def _reconnect_session_for_credential(
    ctx: Context,
    *,
    credential_id: str,
    registration_id: str,
    account: str,
    token_payload: dict[str, object],
) -> None:
    """Run a session-mode auth-code reconnect against an existing ``credential_id``."""
    service = svc(ctx)
    handler = AuthCodeFlowHandler(ctx)
    async with ctx.control_db.transaction() as session:
        row = await ConnectSessionRepository.create(
            session,
            vendor=VENDOR,
            vendor_key=VENDOR,
            agent_id=AGENT_ID,
            initiator_actor_id=OWNER_ID,
            credential_id=credential_id,
            state="polling",
            resolved_flow=AuthCodeFlowHandler.kind,
            requested_scopes=["read"],
            poll_token_hash="reconnect-poll-token-hash",
            created_by=OWNER_ID,
        )

    app = SessionApp(
        flow_kind="authorization_code",
        client_id="shared-vault-client",
        client_secret_provider=lambda: "shared-vault-secret",
        default_scopes=["read", "write"],
        authorize_url="https://auth.vault.example/authorize",
        token_url="https://auth.vault.example/token",
        registration_id=registration_id,
    )
    began = await handler.begin(
        row,
        app=app,
        confirmed_scopes=["read"],
        browser_binding=_BINDING.claim,
    )
    assert isinstance(began, AuthCodeBeginResult)

    token_resp = httpx.Response(200, json=token_payload)
    echo_res = identity_echo.IdentityEchoResult(display=account, raw={"username": account})
    with (
        patch(
            "jentic_one.shared.http.idp_client.build_always_pinned_transport",
            return_value=httpx.MockTransport(lambda _req: token_resp),
        ),
        patch.object(
            identity_echo,
            "echo_identity",
            new=AsyncMock(return_value=echo_res),
        ),
    ):
        status = await service.complete_from_callback(
            raw_state=_state_from_authorize_url(began.authorize_url),
            code="code-reconnect",
            browser_binding=_BINDING.value,
        )
    assert status.status == "connected"
    assert status.connected_as == account


async def test_session_reconnect_same_account_keeps_refresh_token(
    env: Context,
) -> None:
    """When identity echo confirms the same account on the same registration,
    an omitted refresh_token carries over the previous refresh token while
    replacing access_token and scope."""
    _configure_vendor_with_identity_probe(env)
    reg_id = await _create_registration(env)
    credential_id = await _initial_session_connect(
        env,
        registration_id=reg_id,
        account="alice",
        access_token="at-alice-1",
        refresh_token="rt-alice-1",
        scope="read write",
    )

    await _reconnect_session_for_credential(
        env,
        credential_id=credential_id,
        registration_id=reg_id,
        account="alice",
        token_payload={
            "access_token": "at-alice-2",
            "expires_in": 3600,
        },
    )

    async with env.control_db.session() as session:
        token = await OAuthTokenRepository.get_by_credential(session, credential_id)
        assert token is not None
        assert env.encryption.decrypt(token.encrypted_access_token) == "at-alice-2"
        assert token.encrypted_refresh_token is not None
        assert env.encryption.decrypt(token.encrypted_refresh_token) == "rt-alice-1"
        assert token.scope is None
        assert token.app_registration_id == reg_id


async def test_session_reconnect_different_account_drops_refresh_token(
    env: Context,
) -> None:
    """When identity echo reports a different account, an omitted refresh_token
    must not carry over account A's refresh token, and the next broker refresh
    raises RefreshInvalidGrantError instead of minting for account A."""
    _configure_vendor_with_identity_probe(env)
    reg_id = await _create_registration(env)
    credential_id = await _initial_session_connect(
        env,
        registration_id=reg_id,
        account="alice",
        access_token="at-alice-1",
        refresh_token="rt-alice-1",
        scope="read write",
    )

    await _reconnect_session_for_credential(
        env,
        credential_id=credential_id,
        registration_id=reg_id,
        account="bob",
        token_payload={
            "access_token": "at-bob-1",
            "expires_in": 3600,
        },
    )

    expired_at = datetime.now(UTC) - timedelta(seconds=30)
    async with env.control_db.transaction() as session:
        token = await OAuthTokenRepository.get_by_credential(session, credential_id)
        assert token is not None
        assert env.encryption.decrypt(token.encrypted_access_token) == "at-bob-1"
        assert token.encrypted_refresh_token is None
        assert token.scope is None
        token.expires_at = expired_at
        encrypted_access = token.encrypted_access_token
        encrypted_refresh = token.encrypted_refresh_token

        credential = await CredentialRepository.get_by_id(session, credential_id)
        assert credential is not None
        assert credential.provider_account_ref == "bob"

    resolved = ResolvedCredential(
        credential_id=credential_id,
        name="Vault",
        wire_type=CredentialType.OAUTH2,
        stored_type=StoredCredentialType.OAUTH2_AUTHORIZATION_CODE,
        provider="direct_oauth2",
        encrypted_access_token=encrypted_access,
        encrypted_refresh_token=encrypted_refresh,
        token_expires_at=expired_at,
        provider_account_ref="bob",
    )

    refresh_requests: list[httpx.Request] = []

    def _unexpected_idp_call(request: httpx.Request) -> httpx.Response:
        refresh_requests.append(request)
        return httpx.Response(200, json={"access_token": "at-alice-minted", "expires_in": 3600})

    with (
        patch(
            "jentic_one.shared.http.idp_client.build_always_pinned_transport",
            return_value=httpx.MockTransport(_unexpected_idp_call),
        ),
        pytest.raises(RefreshInvalidGrantError),
    ):
        await TokenRefresher(env).ensure_fresh(resolved=resolved, caller=AGENT_ID)

    assert refresh_requests == []


async def test_migrating_off_a_registration_clears_app_registration_id(
    env: Context,
) -> None:
    """Reconnecting a credential after migrating it off a shared registration
    clears oauth_tokens.app_registration_id and drops the registration's refresh token."""
    _configure_vendor_with_identity_probe(env)
    reg_id = await _create_registration(env)
    credential_id = await _initial_session_connect(
        env,
        registration_id=reg_id,
        account="alice",
        access_token="at-reg-1",
        refresh_token="rt-reg-1",
        scope="read write",
    )

    async with env.control_db.transaction() as session:
        token = await OAuthTokenRepository.get_by_credential(session, credential_id)
        assert token is not None
        assert token.app_registration_id == reg_id

        credential = await CredentialRepository.get_by_id(session, credential_id)
        assert credential is not None
        credential.oauth_app_registration_id = None
        session.add(
            OAuthClientCredential(
                id=credential_id,
                token_url="https://auth.vault.example/token",
                client_id="embedded-client",
                encrypted_client_secret=env.encryption.encrypt("embedded-secret"),
                authorize_url="https://auth.vault.example/authorize",
                created_by=OWNER_ID,
            )
        )
        await session.flush()

    async with env.control_db.session() as session:
        credential = await CredentialRepository.get_by_id(session, credential_id)
        assert credential is not None

    await svc(env)._finalise_credential_connected(
        credential=credential,
        handler=AuthCodeFlowHandler(env),
        tokens=SuccessTokens(
            access_token="at-embedded-2",
            refresh_token=None,
            expires_in=3600,
            scope="read",
            token_url="https://auth.vault.example/token",
        ),
    )

    async with env.control_db.session() as session:
        token = await OAuthTokenRepository.get_by_credential(session, credential_id)
        assert token is not None
        assert env.encryption.decrypt(token.encrypted_access_token) == "at-embedded-2"
        assert token.app_registration_id is None
        assert token.encrypted_refresh_token is None
        assert token.scope == "read"
