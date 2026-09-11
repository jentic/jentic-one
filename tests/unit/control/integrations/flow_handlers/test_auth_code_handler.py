"""Tests for AuthCodeFlowHandler.

The state JWT is the load-bearing contract between ``begin`` and the
public ``/credentials/oauth/callback`` route — its ``sid`` claim is
what routes a returning callback to ``ConnectSessionService`` rather
than the standalone-credential ConnectService path. If ``sid`` doesn't
round-trip, agent-initiated connects fail silently in the callback
router. ``complete_from_callback`` runs the RFC 6749 §4.1.3 token
exchange and is the second contract with the vendor, so its error
mapping matters too.
"""

from __future__ import annotations

import base64
import os
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, MagicMock, patch
from urllib.parse import parse_qs, urlparse

import httpx
import pytest
from pydantic import SecretStr

from jentic_one.control.services.credentials.state import decode_state
from jentic_one.control.services.integrations.flow_handlers.auth_code import (
    AuthCodeExchangeError,
    AuthCodeFlowHandler,
)
from jentic_one.control.services.integrations.flow_handlers.base import AuthCodeChallenge
from jentic_one.shared.config import (
    AppConfig,
    ConnectConfig,
    CredentialsConfig,
    DatabaseConfig,
    DatabasesConfig,
    DirectOAuth2ProviderConfig,
    EncryptionConfig,
    EncryptionKey,
    PipedreamProviderConfig,
    VendorAuthorizationCodeFlowConfig,
)
from jentic_one.shared.context import Context

_KEY_MATERIAL = base64.b64encode(os.urandom(32)).decode()
_STATE_SECRET = "test-state-secret"  # pragma: allowlist secret


def _make_context(*, with_direct_oauth2_provider: bool = True) -> Context:
    providers: dict[str, DirectOAuth2ProviderConfig | PipedreamProviderConfig] = {}
    if with_direct_oauth2_provider:
        providers["direct_oauth2"] = DirectOAuth2ProviderConfig(
            redirect_uri="https://app.example.com/credentials/oauth/callback",
        )
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
                state_secret=SecretStr(_STATE_SECRET),
                state_ttl_seconds=600,
            ),
            providers=providers,
        ),
    )
    ctx = Context(cfg)

    @asynccontextmanager
    async def _session():
        yield MagicMock()

    db = MagicMock()
    db.session = _session
    ctx._control_db = db
    return ctx


def _flow() -> VendorAuthorizationCodeFlowConfig:
    return VendorAuthorizationCodeFlowConfig(
        client_id="app-client",
        client_secret=SecretStr("app-secret"),
        authorize_url="https://idp.example.com/authorize",
        token_url="https://idp.example.com/token",
    )


def _row(**overrides) -> MagicMock:
    row = MagicMock()
    row.id = overrides.get("id", "sess_abc")
    row.credential_id = overrides.get("credential_id", "cred_1")
    # ``usr_`` prefix routes actor_type_from_id → USER; keeps the JWT
    # actor_type claim populated with a real ActorType value.
    row.initiator_actor_id = overrides.get("initiator_actor_id", "usr_alice")
    return row


@pytest.mark.asyncio()
async def test_begin_returns_challenge_with_state_and_scope() -> None:
    ctx = _make_context()
    handler = AuthCodeFlowHandler(ctx)
    row = _row(id="sess_xyz", credential_id="cred_9")
    result = await handler.begin(row, flow=_flow(), confirmed_scopes=["repo", "read:user"])

    assert isinstance(result, AuthCodeChallenge)
    parsed = urlparse(result.authorize_url)
    q = parse_qs(parsed.query)
    assert q["client_id"] == ["app-client"]
    assert q["redirect_uri"] == ["https://app.example.com/credentials/oauth/callback"]
    assert q["response_type"] == ["code"]
    assert q["scope"] == ["repo read:user"]
    assert "state" in q


@pytest.mark.asyncio()
async def test_begin_state_jwt_carries_sid_credential_and_actor() -> None:
    # The ``sid`` claim on the state JWT is what routes the callback
    # back to ConnectSessionService instead of the ConnectService path.
    # Losing it is a silent regression (the callback silently falls
    # through to the standalone-credential branch, then 404s).
    ctx = _make_context()
    handler = AuthCodeFlowHandler(ctx)
    row = _row(id="sess_xyz", credential_id="cred_9", initiator_actor_id="usr_alice")

    result = await handler.begin(row, flow=_flow(), confirmed_scopes=["scope1"])
    assert isinstance(result, AuthCodeChallenge)
    state_jwt = parse_qs(urlparse(result.authorize_url).query)["state"][0]
    decoded = decode_state(_STATE_SECRET, state_jwt)

    assert decoded.session_id == "sess_xyz"
    assert decoded.credential_id == "cred_9"
    assert decoded.actor_id == "usr_alice"
    assert decoded.actor_type == "user"


@pytest.mark.asyncio()
async def test_begin_omits_scope_when_no_confirmed_scopes() -> None:
    ctx = _make_context()
    handler = AuthCodeFlowHandler(ctx)
    result = await handler.begin(_row(), flow=_flow(), confirmed_scopes=[])
    assert isinstance(result, AuthCodeChallenge)
    q = parse_qs(urlparse(result.authorize_url).query)
    assert "scope" not in q


@pytest.mark.asyncio()
async def test_begin_raises_when_direct_oauth2_redirect_uri_unconfigured() -> None:
    # The auth-code handler shares the platform redirect_uri with
    # DirectOAuth2Provider — one URL whitelisted at the vendor for
    # both entry points. Failing loud if that config is missing beats
    # silently emitting an authorize URL with no redirect_uri.
    ctx = _make_context(with_direct_oauth2_provider=False)
    handler = AuthCodeFlowHandler(ctx)
    with pytest.raises(RuntimeError, match="redirect_uri must be configured"):
        await handler.begin(_row(), flow=_flow(), confirmed_scopes=[])


@pytest.mark.asyncio()
async def test_on_finalise_is_noop() -> None:
    # Auth-code has no per-session transient state — the OCC row IS
    # the credential's permanent auth descriptor. If ``on_finalise``
    # ever grows a mutating body, the credential's refresh path will
    # break the next time it runs. Pin the no-op.
    ctx = _make_context()
    handler = AuthCodeFlowHandler(ctx)
    session = MagicMock()
    await handler.on_finalise(session, credential_id="cred_1")
    session.assert_not_called()


@pytest.mark.asyncio()
async def test_complete_from_callback_exchanges_code_and_returns_tokens() -> None:
    ctx = _make_context()
    handler = AuthCodeFlowHandler(ctx)
    row = _row()
    occ = MagicMock()
    occ.client_id = "app-client"
    occ.encrypted_client_secret = ctx.encryption.encrypt("app-secret")
    occ.token_url = "https://idp.example.com/token"

    token_response = {
        "access_token": "at_ok",
        "refresh_token": "rt_ok",
        "expires_in": 3600,
        "scope": "repo read:user",
    }

    with (
        patch(
            "jentic_one.control.services.integrations.flow_handlers.auth_code."
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

        tokens = await handler.complete_from_callback(row, code="the-code")
        posted_payload = mock_client.post.await_args.kwargs["data"]

    assert tokens.access_token == "at_ok"
    assert tokens.refresh_token == "rt_ok"
    assert tokens.expires_in == 3600
    # Auth-code trusts the vendor's ``scope`` field (unlike device flow,
    # which reads it from the confirmed aux row) — RFC 6749 §5.1 says
    # the server SHOULD echo the granted set.
    assert tokens.granted_scopes == ["repo", "read:user"]
    # RFC 6749 §4.1.3 payload — confidential client sends client_secret.
    assert posted_payload["grant_type"] == "authorization_code"
    assert posted_payload["code"] == "the-code"
    assert posted_payload["client_id"] == "app-client"
    assert posted_payload["client_secret"] == "app-secret"  # pragma: allowlist secret


@pytest.mark.asyncio()
async def test_complete_from_callback_raises_when_no_client_credentials() -> None:
    ctx = _make_context()
    handler = AuthCodeFlowHandler(ctx)
    with (
        patch(
            "jentic_one.control.services.integrations.flow_handlers.auth_code."
            "OAuthClientCredentialRepository.get_by_credential",
            new_callable=AsyncMock,
            return_value=None,
        ),
        pytest.raises(AuthCodeExchangeError, match="no oauth_client_credentials"),
    ):
        await handler.complete_from_callback(_row(), code="c")


@pytest.mark.asyncio()
async def test_complete_from_callback_raises_on_non_200() -> None:
    ctx = _make_context()
    handler = AuthCodeFlowHandler(ctx)
    occ = MagicMock()
    occ.client_id = "app-client"
    occ.encrypted_client_secret = ctx.encryption.encrypt("app-secret")
    occ.token_url = "https://idp.example.com/token"
    with (
        patch(
            "jentic_one.control.services.integrations.flow_handlers.auth_code."
            "OAuthClientCredentialRepository.get_by_credential",
            new_callable=AsyncMock,
            return_value=occ,
        ),
        patch("httpx.AsyncClient") as mock_client_cls,
    ):
        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.post = AsyncMock(return_value=httpx.Response(400, text="bad_request"))
        mock_client_cls.return_value = mock_client
        with pytest.raises(AuthCodeExchangeError, match="HTTP 400"):
            await handler.complete_from_callback(_row(), code="c")


@pytest.mark.asyncio()
async def test_complete_from_callback_raises_when_no_access_token() -> None:
    ctx = _make_context()
    handler = AuthCodeFlowHandler(ctx)
    occ = MagicMock()
    occ.client_id = "app-client"
    occ.encrypted_client_secret = ctx.encryption.encrypt("app-secret")
    occ.token_url = "https://idp.example.com/token"
    with (
        patch(
            "jentic_one.control.services.integrations.flow_handlers.auth_code."
            "OAuthClientCredentialRepository.get_by_credential",
            new_callable=AsyncMock,
            return_value=occ,
        ),
        patch("httpx.AsyncClient") as mock_client_cls,
    ):
        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.post = AsyncMock(return_value=httpx.Response(200, json={"scope": "x"}))
        mock_client_cls.return_value = mock_client
        with pytest.raises(AuthCodeExchangeError, match="no access_token"):
            await handler.complete_from_callback(_row(), code="c")
