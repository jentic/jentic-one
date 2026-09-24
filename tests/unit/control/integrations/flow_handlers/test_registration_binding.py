"""Unit tests for the shared-registration path through the flow handlers.

Pins the branching contract:

* ``prepare`` with ``SessionApp.registration_id`` non-None sets the credential
  FK + owner_user_id via the repository, and skips the legacy embedded write
  (``oauth_client_credentials`` for auth-code, aux app-config columns for
  device flow are still written because the aux row is required for
  transient state).
* ``prepare`` with ``SessionApp.registration_id`` None keeps the legacy
  embedded writes intact.
* ``complete_from_callback`` on auth-code dereferences the registration when
  ``credentials.oauth_app_registration_id`` is set, and refuses when the
  referenced registration is inactive.
* ``DirectOAuth2Provider._resolve_client_material`` prefers the registration
  when the FK is set, refuses inactive registrations, and falls back to
  ``oauth_client_credentials`` when the FK is null.

All tests are pure-unit — no DB, repositories are patched with in-memory
fakes so the branching is exercised in isolation.
"""

from __future__ import annotations

import base64
import os
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch
from urllib.parse import parse_qs, urlparse

import httpx
import pytest
from pydantic import SecretStr

from jentic_one.control.services.credentials.providers.direct_oauth2 import (
    DirectOAuth2Provider,
    InactiveRegistrationError,
)
from jentic_one.control.services.credentials.schemas.provision import OAuthTokenView
from jentic_one.control.services.integrations.flow_handlers.auth_code import (
    AuthCodeFlowHandler,
    RegistrationInactiveError,
)
from jentic_one.control.services.integrations.flow_handlers.base import AuthCodeBeginResult
from jentic_one.control.services.integrations.flow_handlers.device_authorization import (
    DeviceAuthorizationHandler,
)
from jentic_one.control.services.integrations.flow_handlers.session_app import SessionApp
from jentic_one.shared.config import (
    AppConfig,
    ConnectConfig,
    CredentialsConfig,
    DatabaseConfig,
    DatabasesConfig,
    DirectOAuth2ProviderConfig,
    EncryptionConfig,
    EncryptionKey,
)
from jentic_one.shared.context import Context

_KEY_MATERIAL = base64.b64encode(os.urandom(32)).decode()


def _make_context() -> Context:
    cfg = AppConfig(
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
                "direct_oauth2": DirectOAuth2ProviderConfig(
                    redirect_uri="https://app.example.com/credentials/oauth/callback",
                ),
            },
        ),
    )
    ctx = Context(cfg)

    @asynccontextmanager
    async def _session():
        yield MagicMock()

    @asynccontextmanager
    async def _tx():
        yield MagicMock()

    db = MagicMock()
    db.session = _session
    db.transaction = _tx
    ctx._control_db = db
    return ctx


def _auth_code_app_from_registration() -> SessionApp:
    def _secret() -> str:
        return "shared-secret"

    return SessionApp(
        flow_kind="authorization_code",
        client_id="shared-client",
        client_secret_provider=_secret,
        default_scopes=["repo"],
        registration_id="oar_123",
        authorize_url="https://idp.example.com/authorize",
        token_url="https://idp.example.com/token",
    )


def _auth_code_app_from_config() -> SessionApp:
    def _secret() -> str:
        return "embedded-secret"

    return SessionApp(
        flow_kind="authorization_code",
        client_id="embedded-client",
        client_secret_provider=_secret,
        default_scopes=[],
        registration_id=None,
        authorize_url="https://idp.example.com/authorize",
        token_url="https://idp.example.com/token",
    )


def _device_app_from_registration() -> SessionApp:
    return SessionApp(
        flow_kind="device_authorization",
        client_id="shared-device-client",
        client_secret_provider=None,
        default_scopes=[],
        registration_id="oar_dev_456",
        authorization_endpoint="https://idp.example.com/device",
        token_endpoint="https://idp.example.com/token",
    )


def _device_app_from_config() -> SessionApp:
    return SessionApp(
        flow_kind="device_authorization",
        client_id="embedded-device-client",
        client_secret_provider=None,
        default_scopes=[],
        registration_id=None,
        authorization_endpoint="https://idp.example.com/device",
        token_endpoint="https://idp.example.com/token",
    )


# ---------------------------------------------------------------------------
# Auth-code handler — prepare branching
# ---------------------------------------------------------------------------


@pytest.mark.asyncio()
async def test_auth_code_prepare_registration_path_sets_fk_and_owner() -> None:
    ctx = _make_context()
    handler = AuthCodeFlowHandler(ctx)
    session = MagicMock()

    with (
        patch(
            "jentic_one.control.services.integrations.flow_handlers.auth_code."
            "CredentialRepository.set_oauth_app_registration",
            new_callable=AsyncMock,
        ) as set_reg,
        patch(
            "jentic_one.control.services.integrations.flow_handlers.auth_code."
            "OAuthClientCredentialRepository.create",
            new_callable=AsyncMock,
        ) as create_occ,
    ):
        await handler.prepare(
            session,
            credential_id="cred_1",
            app=_auth_code_app_from_registration(),
            requested_scopes=["repo"],
            created_by="usr_alice",
            owner_user_id="usr_alice",
        )

    # FK stamped through the repository, ``owner_user_id`` threaded through.
    set_reg.assert_awaited_once()
    assert set_reg.await_args is not None
    call_kwargs = set_reg.await_args.kwargs
    assert call_kwargs["registration_id"] == "oar_123"
    assert call_kwargs["owner_user_id"] == "usr_alice"
    # Legacy embedded write is skipped — every mint through the shared
    # registration must resolve to the same client material at refresh time.
    create_occ.assert_not_awaited()


@pytest.mark.asyncio()
async def test_auth_code_prepare_config_path_writes_oauth_client_credentials() -> None:
    ctx = _make_context()
    handler = AuthCodeFlowHandler(ctx)
    session = MagicMock()

    with (
        patch(
            "jentic_one.control.services.integrations.flow_handlers.auth_code."
            "CredentialRepository.set_oauth_app_registration",
            new_callable=AsyncMock,
        ) as set_reg,
        patch(
            "jentic_one.control.services.integrations.flow_handlers.auth_code."
            "OAuthClientCredentialRepository.create",
            new_callable=AsyncMock,
        ) as create_occ,
    ):
        await handler.prepare(
            session,
            credential_id="cred_1",
            app=_auth_code_app_from_config(),
            requested_scopes=["read"],
            created_by="usr_alice",
        )

    set_reg.assert_not_awaited()
    create_occ.assert_awaited_once()
    assert create_occ.await_args is not None
    kwargs = create_occ.await_args.kwargs
    assert kwargs["client_id"] == "embedded-client"
    assert kwargs["token_url"] == "https://idp.example.com/token"
    assert kwargs["authorize_url"] == "https://idp.example.com/authorize"


# ---------------------------------------------------------------------------
# Auth-code handler — complete_from_callback branching
# ---------------------------------------------------------------------------


@pytest.mark.asyncio()
async def test_auth_code_complete_from_callback_uses_registration_when_fk_set() -> None:
    ctx = _make_context()
    handler = AuthCodeFlowHandler(ctx)

    credential = MagicMock()
    credential.oauth_app_registration_id = "oar_123"
    credential.owner_user_id = "usr_alice"

    ac_details = MagicMock()
    ac_details.token_url = "https://idp.example.com/token"
    ac_details.encrypted_client_secret = ctx.encryption.encrypt("shared-secret")

    registration = MagicMock()
    registration.id = "oar_123"
    registration.is_active = True
    registration.client_id = "shared-client"
    registration.authorization_code_details = ac_details

    row = MagicMock()
    row.credential_id = "cred_1"
    row.pkce_code_verifier = "verifier_abc"

    token_response = {"access_token": "at_ok", "expires_in": 3600, "scope": "repo"}

    with (
        patch(
            "jentic_one.control.services.integrations.flow_handlers.auth_code."
            "CredentialRepository.get_by_id",
            new_callable=AsyncMock,
            return_value=credential,
        ),
        patch(
            "jentic_one.control.services.integrations.flow_handlers.auth_code."
            "OAuthAppRegistrationRepository.get_by_id",
            new_callable=AsyncMock,
            return_value=registration,
        ),
        patch(
            "jentic_one.control.services.integrations.flow_handlers.auth_code."
            "OAuthClientCredentialRepository.get_by_credential",
            new_callable=AsyncMock,
        ) as occ_get,
        patch("httpx.AsyncClient") as mock_client_cls,
    ):
        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.post = AsyncMock(return_value=httpx.Response(200, json=token_response))
        mock_client_cls.return_value = mock_client

        tokens = await handler.complete_from_callback(row, code="the-code")
        posted = mock_client.post.await_args.kwargs["data"]

    assert tokens.access_token == "at_ok"
    # OCC never consulted on the registration path.
    occ_get.assert_not_awaited()
    # Client material came off the registration + PKCE verifier travelled through.
    assert posted["client_id"] == "shared-client"
    assert posted["client_secret"] == "shared-secret"  # pragma: allowlist secret
    assert posted["code_verifier"] == "verifier_abc"


@pytest.mark.asyncio()
async def test_auth_code_complete_from_callback_refuses_inactive_registration() -> None:
    ctx = _make_context()
    handler = AuthCodeFlowHandler(ctx)

    credential = MagicMock()
    credential.oauth_app_registration_id = "oar_disabled"

    ac_details = MagicMock()
    ac_details.token_url = "https://idp.example.com/token"
    ac_details.encrypted_client_secret = ctx.encryption.encrypt("shared-secret")

    registration = MagicMock()
    registration.id = "oar_disabled"
    registration.is_active = False
    registration.client_id = "shared-client"
    registration.authorization_code_details = ac_details

    row = MagicMock()
    row.credential_id = "cred_1"
    row.pkce_code_verifier = None

    with (
        patch(
            "jentic_one.control.services.integrations.flow_handlers.auth_code."
            "CredentialRepository.get_by_id",
            new_callable=AsyncMock,
            return_value=credential,
        ),
        patch(
            "jentic_one.control.services.integrations.flow_handlers.auth_code."
            "OAuthAppRegistrationRepository.get_by_id",
            new_callable=AsyncMock,
            return_value=registration,
        ),
        pytest.raises(RegistrationInactiveError, match="inactive"),
    ):
        await handler.complete_from_callback(row, code="the-code")


@pytest.mark.asyncio()
async def test_auth_code_begin_includes_pkce_challenge() -> None:
    """PKCE (RFC 7636) must ride every new auth-code flow.

    The vendor sees ``code_challenge_method=S256`` + a URL-safe base64 hash
    of the verifier; the verifier itself is persisted server-side (never in
    the state JWT, which transits the browser).
    """
    ctx = _make_context()
    handler = AuthCodeFlowHandler(ctx)
    row = MagicMock()
    row.id = "sess_xyz"
    row.credential_id = "cred_9"
    row.initiator_actor_id = "usr_alice"

    with patch(
        "jentic_one.control.services.integrations.flow_handlers.auth_code."
        "ConnectSessionRepository.update_fields",
        new_callable=AsyncMock,
    ) as persist:
        result = await handler.begin(row, app=_auth_code_app_from_config(), confirmed_scopes=[])

    assert isinstance(result, AuthCodeBeginResult)
    q = parse_qs(urlparse(result.authorize_url).query)
    assert q["code_challenge_method"] == ["S256"]
    assert q["code_challenge"] and q["code_challenge"][0]
    # Verifier is persisted, not thrown away.
    persist.assert_awaited_once()
    assert persist.await_args is not None
    assert persist.await_args.kwargs["pkce_code_verifier"]


# ---------------------------------------------------------------------------
# Device-flow handler — prepare branching
# ---------------------------------------------------------------------------


@pytest.mark.asyncio()
async def test_device_prepare_registration_path_sets_fk_and_writes_aux() -> None:
    """Device flow always needs the aux row (transient device_code state); the
    FK is set additionally on the shared-registration path."""
    ctx = _make_context()
    handler = DeviceAuthorizationHandler(ctx)
    session = MagicMock()

    with (
        patch(
            "jentic_one.control.services.integrations.flow_handlers.device_authorization."
            "DeviceAuthorizationCredentialRepository.create",
            new_callable=AsyncMock,
        ) as create_aux,
        patch(
            "jentic_one.control.services.integrations.flow_handlers.device_authorization."
            "CredentialRepository.set_oauth_app_registration",
            new_callable=AsyncMock,
        ) as set_reg,
    ):
        await handler.prepare(
            session,
            credential_id="cred_1",
            app=_device_app_from_registration(),
            requested_scopes=[],
            created_by="usr_alice",
            owner_user_id="usr_alice",
        )

    create_aux.assert_awaited_once()
    set_reg.assert_awaited_once()
    assert set_reg.await_args is not None
    assert set_reg.await_args.kwargs["registration_id"] == "oar_dev_456"
    assert set_reg.await_args.kwargs["owner_user_id"] == "usr_alice"


@pytest.mark.asyncio()
async def test_device_prepare_config_path_skips_registration_stamp() -> None:
    ctx = _make_context()
    handler = DeviceAuthorizationHandler(ctx)
    session = MagicMock()

    with (
        patch(
            "jentic_one.control.services.integrations.flow_handlers.device_authorization."
            "DeviceAuthorizationCredentialRepository.create",
            new_callable=AsyncMock,
        ) as create_aux,
        patch(
            "jentic_one.control.services.integrations.flow_handlers.device_authorization."
            "CredentialRepository.set_oauth_app_registration",
            new_callable=AsyncMock,
        ) as set_reg,
    ):
        await handler.prepare(
            session,
            credential_id="cred_1",
            app=_device_app_from_config(),
            requested_scopes=[],
            created_by="usr_alice",
        )

    create_aux.assert_awaited_once()
    set_reg.assert_not_awaited()


# ---------------------------------------------------------------------------
# DirectOAuth2Provider — registration-first refresh path
# ---------------------------------------------------------------------------


def _make_direct_provider() -> DirectOAuth2Provider:
    return DirectOAuth2Provider(
        DirectOAuth2ProviderConfig(
            redirect_uri="https://app.example.com/credentials/oauth/callback",
            default_scopes=["read", "write"],
            expiry_skew_seconds=60,
        )
    )


@pytest.mark.asyncio()
async def test_direct_oauth2_refresh_prefers_registration() -> None:
    ctx = _make_context()
    provider = _make_direct_provider()

    credential = MagicMock()
    credential.oauth_app_registration_id = "oar_123"

    ac_details = MagicMock()
    ac_details.token_url = "https://idp.example.com/token"
    ac_details.encrypted_client_secret = ctx.encryption.encrypt("shared-secret")

    registration = MagicMock()
    registration.id = "oar_123"
    registration.is_active = True
    registration.client_id = "shared-client"
    registration.authorization_code_details = ac_details

    async def _decrypt() -> str:
        return "old-refresh"

    token_view = OAuthTokenView(
        credential_id="cred_1",
        provider="direct_oauth2",
        expires_at=datetime.now(UTC),
        decrypt=_decrypt,
    )

    token_response = {"access_token": "at_new", "expires_in": 3600, "scope": "read"}

    with (
        patch(
            "jentic_one.control.services.credentials.providers.direct_oauth2."
            "CredentialRepository.get_by_id",
            new_callable=AsyncMock,
            return_value=credential,
        ),
        patch(
            "jentic_one.control.services.credentials.providers.direct_oauth2."
            "OAuthAppRegistrationRepository.get_by_id",
            new_callable=AsyncMock,
            return_value=registration,
        ),
        patch(
            "jentic_one.control.services.credentials.providers.direct_oauth2."
            "OAuthClientCredentialRepository.get_by_credential",
            new_callable=AsyncMock,
        ) as occ_get,
        patch("httpx.AsyncClient") as mock_client_cls,
    ):
        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.post = AsyncMock(return_value=httpx.Response(200, json=token_response))
        mock_client_cls.return_value = mock_client

        result = await provider.refresh(ctx, token=token_view)
        posted = mock_client.post.await_args.kwargs["data"]

    assert result.access_token == "at_new"
    occ_get.assert_not_awaited()
    assert posted["client_id"] == "shared-client"
    assert posted["client_secret"] == "shared-secret"  # pragma: allowlist secret


@pytest.mark.asyncio()
async def test_direct_oauth2_refresh_refuses_inactive_registration() -> None:
    ctx = _make_context()
    provider = _make_direct_provider()

    credential = MagicMock()
    credential.oauth_app_registration_id = "oar_disabled"

    ac_details = MagicMock()
    ac_details.encrypted_client_secret = ctx.encryption.encrypt("shared-secret")
    ac_details.token_url = "https://idp.example.com/token"

    registration = MagicMock()
    registration.id = "oar_disabled"
    registration.is_active = False
    registration.client_id = "shared-client"
    registration.authorization_code_details = ac_details

    async def _decrypt() -> str:
        return "old-refresh"

    token_view = OAuthTokenView(
        credential_id="cred_1",
        provider="direct_oauth2",
        expires_at=datetime.now(UTC),
        decrypt=_decrypt,
    )

    with (
        patch(
            "jentic_one.control.services.credentials.providers.direct_oauth2."
            "CredentialRepository.get_by_id",
            new_callable=AsyncMock,
            return_value=credential,
        ),
        patch(
            "jentic_one.control.services.credentials.providers.direct_oauth2."
            "OAuthAppRegistrationRepository.get_by_id",
            new_callable=AsyncMock,
            return_value=registration,
        ),
        pytest.raises(InactiveRegistrationError, match="inactive"),
    ):
        await provider.refresh(ctx, token=token_view)


@pytest.mark.asyncio()
async def test_direct_oauth2_refresh_falls_back_to_oauth_client_credentials() -> None:
    ctx = _make_context()
    provider = _make_direct_provider()

    credential = MagicMock()
    credential.oauth_app_registration_id = None

    occ = MagicMock()
    occ.client_id = "embedded-client"
    occ.token_url = "https://idp.example.com/token"
    occ.encrypted_client_secret = ctx.encryption.encrypt("embedded-secret")

    async def _decrypt() -> str:
        return "old-refresh"

    token_view = OAuthTokenView(
        credential_id="cred_1",
        provider="direct_oauth2",
        expires_at=datetime.now(UTC),
        decrypt=_decrypt,
    )

    token_response = {"access_token": "at_new", "expires_in": 3600, "scope": "read"}

    with (
        patch(
            "jentic_one.control.services.credentials.providers.direct_oauth2."
            "CredentialRepository.get_by_id",
            new_callable=AsyncMock,
            return_value=credential,
        ),
        patch(
            "jentic_one.control.services.credentials.providers.direct_oauth2."
            "OAuthClientCredentialRepository.get_by_credential",
            new_callable=AsyncMock,
            return_value=occ,
        ),
        patch("httpx.AsyncClient") as mock_client_cls,
    ):
        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.post = AsyncMock(return_value=httpx.Response(200, json=token_response))
        mock_client_cls.return_value = mock_client

        result = await provider.refresh(ctx, token=token_view)
        posted = mock_client.post.await_args.kwargs["data"]

    assert result.access_token == "at_new"
    assert posted["client_id"] == "embedded-client"
    assert posted["client_secret"] == "embedded-secret"  # pragma: allowlist secret
