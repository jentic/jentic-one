"""Tests for DirectOAuth2Provider behaviour."""

from __future__ import annotations

import base64
import json
import os
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch
from urllib.parse import parse_qs, urlparse

import httpx
import pytest
from pydantic import SecretStr

from jentic_one.control.services.credentials.providers.base import (
    NotConnectableError,
    ProviderError,
)
from jentic_one.control.services.credentials.providers.direct_oauth2 import (
    DirectOAuth2Provider,
    InvalidGrantError,
    TokenExchangeError,
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
from jentic_one.control.services.credentials.state import decode_state
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
                "my_oauth": DirectOAuth2ProviderConfig(
                    redirect_uri="https://app.example.com/credentials/oauth/callback",
                    default_scopes=["read", "write"],
                    expiry_skew_seconds=60,
                ),
            },
        ),
    )


def _make_provider() -> DirectOAuth2Provider:
    cfg = DirectOAuth2ProviderConfig(
        redirect_uri="https://app.example.com/credentials/oauth/callback",
        default_scopes=["read", "write"],
        expiry_skew_seconds=60,
    )
    return DirectOAuth2Provider(cfg)


@asynccontextmanager
async def _fake_session():
    """No-op async context manager standing in for ctx.control_db.session()."""
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


def test_name_is_direct_oauth2() -> None:
    provider = _make_provider()
    assert provider.name == "direct_oauth2"


@pytest.mark.asyncio()
async def test_complete_connect_exchanges_code() -> None:
    provider = _make_provider()
    ctx = Context(_make_config())
    ctx._control_db = _mock_control_db()

    encrypted_secret = ctx.encryption.encrypt("my-client-secret")

    class FakeOCC:
        token_url = "https://idp.example.com/token"
        client_id = "client-abc"
        encrypted_client_secret = encrypted_secret
        authorize_url = "https://idp.example.com/authorize"
        scope = "read write"

    state = ConnectState(
        credential_id="cred_123",
        provider="direct_oauth2",
        actor_id="user_1",
        issued_at=datetime.now(UTC),
        nonce="test-nonce",
    )
    callback = ConnectCallback(code="auth-code-xyz")

    token_response = {
        "access_token": "at_fresh",
        "refresh_token": "rt_fresh",
        "expires_in": 3600,
        "scope": "read write",
    }

    with (
        patch(
            "jentic_one.control.repos.OAuthClientCredentialRepository.get_by_credential",
            new_callable=AsyncMock,
            return_value=FakeOCC(),
        ),
        patch("httpx.AsyncClient") as mock_client_cls,
    ):
        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.post = AsyncMock(return_value=httpx.Response(200, json=token_response))
        mock_client_cls.return_value = mock_client

        result = await provider.complete_connect(ctx, state=state, callback=callback)

    assert result.access_token == "at_fresh"
    assert result.refresh_token == "rt_fresh"
    assert result.scope == "read write"
    assert result.expires_at is not None
    assert result.provider_account_ref is None


@pytest.mark.asyncio()
async def test_complete_connect_raises_on_error_callback() -> None:
    provider = _make_provider()
    ctx = Context(_make_config())

    state = ConnectState(
        credential_id="cred_123",
        provider="direct_oauth2",
        actor_id=None,
        issued_at=datetime.now(UTC),
        nonce="test-nonce",
    )
    callback = ConnectCallback(error="access_denied")

    with pytest.raises(ProviderError, match="Authorization denied"):
        await provider.complete_connect(ctx, state=state, callback=callback)


@pytest.mark.asyncio()
async def test_complete_connect_raises_on_missing_code() -> None:
    provider = _make_provider()
    ctx = Context(_make_config())

    state = ConnectState(
        credential_id="cred_123",
        provider="direct_oauth2",
        actor_id=None,
        issued_at=datetime.now(UTC),
        nonce="test-nonce",
    )
    callback = ConnectCallback()

    with pytest.raises(ProviderError, match="No authorization code"):
        await provider.complete_connect(ctx, state=state, callback=callback)


@pytest.mark.asyncio()
async def test_refresh_exchanges_refresh_token() -> None:
    provider = _make_provider()
    ctx = Context(_make_config())
    ctx._control_db = _mock_control_db()

    encrypted_secret = ctx.encryption.encrypt("my-client-secret")

    class FakeOCC:
        token_url = "https://idp.example.com/token"
        client_id = "client-abc"
        encrypted_client_secret = encrypted_secret

    token_response = {
        "access_token": "at_rotated",
        "refresh_token": "rt_rotated",
        "expires_in": 7200,
        "scope": "read write",
    }

    async def fake_decrypt() -> str:
        return "old-refresh-token"

    token_view = OAuthTokenView(
        credential_id="cred_123",
        provider="direct_oauth2",
        expires_at=datetime.now(UTC),
        decrypt=fake_decrypt,
    )

    with (
        patch(
            "jentic_one.control.repos.OAuthClientCredentialRepository.get_by_credential",
            new_callable=AsyncMock,
            return_value=FakeOCC(),
        ),
        patch("httpx.AsyncClient") as mock_client_cls,
    ):
        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.post = AsyncMock(return_value=httpx.Response(200, json=token_response))
        mock_client_cls.return_value = mock_client

        result = await provider.refresh(ctx, token=token_view)

    assert result.access_token == "at_rotated"
    assert result.refresh_token == "rt_rotated"
    assert result.expires_at is not None


@pytest.mark.asyncio()
async def test_refresh_raises_invalid_grant() -> None:
    provider = _make_provider()
    ctx = Context(_make_config())
    ctx._control_db = _mock_control_db()

    encrypted_secret = ctx.encryption.encrypt("my-client-secret")

    class FakeOCC:
        token_url = "https://idp.example.com/token"
        client_id = "client-abc"
        encrypted_client_secret = encrypted_secret

    async def fake_decrypt() -> str:
        return "revoked-refresh-token"

    token_view = OAuthTokenView(
        credential_id="cred_123",
        provider="direct_oauth2",
        expires_at=datetime.now(UTC),
        decrypt=fake_decrypt,
    )

    error_body = json.dumps({"error": "invalid_grant", "error_description": "Token revoked"})

    with (
        patch(
            "jentic_one.control.repos.OAuthClientCredentialRepository.get_by_credential",
            new_callable=AsyncMock,
            return_value=FakeOCC(),
        ),
        patch("httpx.AsyncClient") as mock_client_cls,
    ):
        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.post = AsyncMock(return_value=httpx.Response(400, text=error_body))
        mock_client_cls.return_value = mock_client

        with pytest.raises(InvalidGrantError):
            await provider.refresh(ctx, token=token_view)


@pytest.mark.asyncio()
async def test_refresh_raises_token_exchange_error_on_other_failure() -> None:
    provider = _make_provider()
    ctx = Context(_make_config())
    ctx._control_db = _mock_control_db()

    encrypted_secret = ctx.encryption.encrypt("my-client-secret")

    class FakeOCC:
        token_url = "https://idp.example.com/token"
        client_id = "client-abc"
        encrypted_client_secret = encrypted_secret

    async def fake_decrypt() -> str:
        return "some-refresh-token"

    token_view = OAuthTokenView(
        credential_id="cred_123",
        provider="direct_oauth2",
        expires_at=datetime.now(UTC),
        decrypt=fake_decrypt,
    )

    with (
        patch(
            "jentic_one.control.repos.OAuthClientCredentialRepository.get_by_credential",
            new_callable=AsyncMock,
            return_value=FakeOCC(),
        ),
        patch("httpx.AsyncClient") as mock_client_cls,
    ):
        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.post = AsyncMock(return_value=httpx.Response(500, text="Internal Server Error"))
        mock_client_cls.return_value = mock_client

        with pytest.raises(TokenExchangeError) as exc_info:
            await provider.refresh(ctx, token=token_view)
        assert exc_info.value.status == 500


@pytest.mark.asyncio()
async def test_post_token_raises_on_non_json_response() -> None:
    provider = _make_provider()
    ctx = Context(_make_config())
    ctx._control_db = _mock_control_db()

    encrypted_secret = ctx.encryption.encrypt("my-client-secret")

    class FakeOCC:
        token_url = "https://idp.example.com/token"
        client_id = "client-abc"
        encrypted_client_secret = encrypted_secret
        authorize_url = "https://idp.example.com/authorize"
        scope = "read"

    state = ConnectState(
        credential_id="cred_123",
        provider="direct_oauth2",
        actor_id=None,
        issued_at=datetime.now(UTC),
        nonce="test-nonce",
    )
    callback = ConnectCallback(code="auth-code-xyz")

    with (
        patch(
            "jentic_one.control.repos.OAuthClientCredentialRepository.get_by_credential",
            new_callable=AsyncMock,
            return_value=FakeOCC(),
        ),
        patch("httpx.AsyncClient") as mock_client_cls,
    ):
        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.post = AsyncMock(return_value=httpx.Response(200, text="<html>Not JSON</html>"))
        mock_client_cls.return_value = mock_client

        with pytest.raises(TokenExchangeError) as exc_info:
            await provider.complete_connect(ctx, state=state, callback=callback)
        assert exc_info.value.status == 200


def _begin_connect_request() -> ConnectRequest:
    return ConnectRequest(
        scopes=["read", "write"],
        extra={"credential_id": "cred_123", "actor_id": "user_1", "actor_type": "user"},
    )


def _begin_connect_api_ref() -> APIReference:
    return APIReference(vendor="example", name="example-api", version="v1")


class _FakeOCCForBegin:
    client_id = "client-abc"
    authorize_url = "https://idp.example.com/authorize"
    scope = "read"


def _parse_authorize_url_params(url: str) -> dict[str, list[str]]:
    return parse_qs(urlparse(url).query)


@pytest.mark.asyncio()
async def test_begin_connect_appends_default_authorize_extra_params() -> None:
    provider = _make_provider()
    ctx = Context(_make_config())
    ctx._control_db = _mock_control_db()

    with (
        patch(
            "jentic_one.control.repos.CredentialRepository.get_by_id",
            new_callable=AsyncMock,
            return_value=MagicMock(),
        ),
        patch(
            "jentic_one.control.repos.OAuthClientCredentialRepository.get_by_credential",
            new_callable=AsyncMock,
            return_value=_FakeOCCForBegin(),
        ),
    ):
        challenge = await provider.begin_connect(
            ctx, api=_begin_connect_api_ref(), request=_begin_connect_request()
        )

    params = _parse_authorize_url_params(challenge.authorize_url)
    assert params["prompt"] == ["consent"]
    assert params["access_type"] == ["offline"]
    assert params["response_type"] == ["code"]
    assert params["client_id"] == ["client-abc"]
    assert params["redirect_uri"] == ["https://app.example.com/credentials/oauth/callback"]
    assert params["scope"] == ["read write"]
    assert "state" in params


@pytest.mark.asyncio()
async def test_begin_connect_respects_custom_authorize_extra_params() -> None:
    cfg = DirectOAuth2ProviderConfig(
        redirect_uri="https://app.example.com/credentials/oauth/callback",
        default_scopes=["read"],
        authorize_extra_params={"prompt": "select_account"},
    )
    provider = DirectOAuth2Provider(cfg)
    ctx = Context(_make_config())
    ctx._control_db = _mock_control_db()

    with (
        patch(
            "jentic_one.control.repos.CredentialRepository.get_by_id",
            new_callable=AsyncMock,
            return_value=MagicMock(),
        ),
        patch(
            "jentic_one.control.repos.OAuthClientCredentialRepository.get_by_credential",
            new_callable=AsyncMock,
            return_value=_FakeOCCForBegin(),
        ),
    ):
        challenge = await provider.begin_connect(
            ctx, api=_begin_connect_api_ref(), request=_begin_connect_request()
        )

    params = _parse_authorize_url_params(challenge.authorize_url)
    assert params["prompt"] == ["select_account"]
    assert "access_type" not in params


@pytest.mark.asyncio()
async def test_begin_connect_extra_params_can_override_required_oauth_params() -> None:
    """`authorize_extra_params` is applied last and wins over every standard param.

    Contract (Option B, configurer's peril): an operator can override
    *any* parameter — including the OAuth-required ones (response_type,
    client_id, redirect_uri, state, scope) — from config. This is the
    only knob general enough to accommodate non-standard IdPs (e.g. a
    different ``response_type``, an alternative scope claim, etc.).

    Misconfiguration consequences are on the operator. The config field
    is operator-only (not user-supplied), so the threat model is
    "operator misconfigures their own deployment", not "user tampers
    with the OAuth flow". In particular: overriding ``state`` with a
    static value breaks the signed/time-limited CSRF binding the
    callback relies on — don't do it.
    """
    cfg = DirectOAuth2ProviderConfig(
        redirect_uri="https://app.example.com/credentials/oauth/callback",
        authorize_extra_params={
            "response_type": "token",
            "client_id": "spoofed",
            "redirect_uri": "https://attacker.example.com/steal",
            "state": "spoofed-state",
            "scope": "spoofed-scope",
            "prompt": "consent",
        },
    )
    provider = DirectOAuth2Provider(cfg)
    ctx = Context(_make_config())
    ctx._control_db = _mock_control_db()

    with (
        patch(
            "jentic_one.control.repos.CredentialRepository.get_by_id",
            new_callable=AsyncMock,
            return_value=MagicMock(),
        ),
        patch(
            "jentic_one.control.repos.OAuthClientCredentialRepository.get_by_credential",
            new_callable=AsyncMock,
            return_value=_FakeOCCForBegin(),
        ),
    ):
        challenge = await provider.begin_connect(
            ctx,
            api=_begin_connect_api_ref(),
            request=ConnectRequest(
                scopes=["read", "write"],
                extra={"credential_id": "cred_123"},
            ),
        )

    params = _parse_authorize_url_params(challenge.authorize_url)
    # Every config-supplied value wins over the standard default.
    assert params["response_type"] == ["token"]
    assert params["client_id"] == ["spoofed"]
    assert params["redirect_uri"] == ["https://attacker.example.com/steal"]
    assert params["scope"] == ["spoofed-scope"]
    assert params["state"] == ["spoofed-state"]
    assert params["prompt"] == ["consent"]


@pytest.mark.asyncio()
async def test_begin_connect_with_empty_extra_params_emits_only_required_params() -> None:
    cfg = DirectOAuth2ProviderConfig(
        redirect_uri="https://app.example.com/credentials/oauth/callback",
        default_scopes=["read"],
        authorize_extra_params={},
    )
    provider = DirectOAuth2Provider(cfg)
    ctx = Context(_make_config())
    ctx._control_db = _mock_control_db()

    with (
        patch(
            "jentic_one.control.repos.CredentialRepository.get_by_id",
            new_callable=AsyncMock,
            return_value=MagicMock(),
        ),
        patch(
            "jentic_one.control.repos.OAuthClientCredentialRepository.get_by_credential",
            new_callable=AsyncMock,
            return_value=_FakeOCCForBegin(),
        ),
    ):
        challenge = await provider.begin_connect(
            ctx, api=_begin_connect_api_ref(), request=_begin_connect_request()
        )

    params = _parse_authorize_url_params(challenge.authorize_url)
    assert "prompt" not in params
    assert "access_type" not in params
    assert set(params.keys()) == {"response_type", "client_id", "redirect_uri", "state", "scope"}


@pytest.mark.asyncio()
async def test_begin_connect_raises_when_authorize_url_missing() -> None:
    provider = _make_provider()
    ctx = Context(_make_config())
    ctx._control_db = _mock_control_db()

    class _NoAuthorizeURL:
        client_id = "client-abc"
        authorize_url = ""
        scope = None

    with (
        patch(
            "jentic_one.control.repos.CredentialRepository.get_by_id",
            new_callable=AsyncMock,
            return_value=MagicMock(),
        ),
        patch(
            "jentic_one.control.repos.OAuthClientCredentialRepository.get_by_credential",
            new_callable=AsyncMock,
            return_value=_NoAuthorizeURL(),
        ),
        pytest.raises(NotConnectableError),
    ):
        await provider.begin_connect(
            ctx, api=_begin_connect_api_ref(), request=_begin_connect_request()
        )


@pytest.mark.asyncio()
async def test_begin_connect_raises_without_credential_id() -> None:
    provider = _make_provider()
    ctx = Context(_make_config())

    with pytest.raises(ProviderError, match="credential_id required"):
        await provider.begin_connect(
            ctx,
            api=_begin_connect_api_ref(),
            request=ConnectRequest(scopes=["read"], extra={}),
        )


def _provider_without_redirect() -> DirectOAuth2Provider:
    return DirectOAuth2Provider(
        DirectOAuth2ProviderConfig(default_scopes=["read", "write"], expiry_skew_seconds=60)
    )


@pytest.mark.asyncio()
async def test_begin_connect_uses_request_redirect_uri_when_unconfigured() -> None:
    """With no configured redirect_uri, the request-derived one is used and
    embedded in the signed state (so the token exchange can replay it)."""
    provider = _provider_without_redirect()
    ctx = Context(_make_config())
    ctx._control_db = _mock_control_db()
    request = ConnectRequest(
        scopes=["read", "write"],
        extra={"credential_id": "cred_123", "actor_id": "user_1", "actor_type": "user"},
        redirect_uri="http://127.0.0.1:8020/credentials/oauth/callback",
    )

    with (
        patch(
            "jentic_one.control.repos.CredentialRepository.get_by_id",
            new_callable=AsyncMock,
            return_value=MagicMock(),
        ),
        patch(
            "jentic_one.control.repos.OAuthClientCredentialRepository.get_by_credential",
            new_callable=AsyncMock,
            return_value=_FakeOCCForBegin(),
        ),
    ):
        challenge = await provider.begin_connect(ctx, api=_begin_connect_api_ref(), request=request)

    params = _parse_authorize_url_params(challenge.authorize_url)
    assert params["redirect_uri"] == ["http://127.0.0.1:8020/credentials/oauth/callback"]
    # The state carries the same value for the token exchange.
    state_secret = ctx.config.credentials.connect.state_secret.get_secret_value()
    decoded = decode_state(state_secret, params["state"][0])
    assert decoded.redirect_uri == "http://127.0.0.1:8020/credentials/oauth/callback"


@pytest.mark.asyncio()
async def test_begin_connect_configured_redirect_uri_wins_over_request() -> None:
    provider = _make_provider()  # configured https://app.example.com/...
    ctx = Context(_make_config())
    ctx._control_db = _mock_control_db()
    request = ConnectRequest(
        scopes=["read", "write"],
        extra={"credential_id": "cred_123", "actor_id": "user_1"},
        redirect_uri="http://127.0.0.1:8020/credentials/oauth/callback",
    )

    with (
        patch(
            "jentic_one.control.repos.CredentialRepository.get_by_id",
            new_callable=AsyncMock,
            return_value=MagicMock(),
        ),
        patch(
            "jentic_one.control.repos.OAuthClientCredentialRepository.get_by_credential",
            new_callable=AsyncMock,
            return_value=_FakeOCCForBegin(),
        ),
    ):
        challenge = await provider.begin_connect(ctx, api=_begin_connect_api_ref(), request=request)

    params = _parse_authorize_url_params(challenge.authorize_url)
    assert params["redirect_uri"] == ["https://app.example.com/credentials/oauth/callback"]


@pytest.mark.asyncio()
async def test_begin_connect_raises_when_no_redirect_uri_available() -> None:
    provider = _provider_without_redirect()
    ctx = Context(_make_config())
    ctx._control_db = _mock_control_db()
    request = ConnectRequest(
        scopes=["read"],
        extra={"credential_id": "cred_123"},
        redirect_uri=None,
    )

    with (
        patch(
            "jentic_one.control.repos.CredentialRepository.get_by_id",
            new_callable=AsyncMock,
            return_value=MagicMock(),
        ),
        patch(
            "jentic_one.control.repos.OAuthClientCredentialRepository.get_by_credential",
            new_callable=AsyncMock,
            return_value=_FakeOCCForBegin(),
        ),
        pytest.raises(ProviderError, match="No redirect_uri available"),
    ):
        await provider.begin_connect(ctx, api=_begin_connect_api_ref(), request=request)


@pytest.mark.asyncio()
async def test_complete_connect_replays_state_redirect_uri_in_exchange() -> None:
    """The token exchange must send the exact redirect_uri from the state
    (RFC 6749 §4.1.3), not the provider's configured value."""
    provider = _make_provider()  # configured https://app.example.com/...
    ctx = Context(_make_config())
    ctx._control_db = _mock_control_db()
    encrypted_secret = ctx.encryption.encrypt("my-client-secret")

    class FakeOCC:
        token_url = "https://idp.example.com/token"
        client_id = "client-abc"
        encrypted_client_secret = encrypted_secret

    state = ConnectState(
        credential_id="cred_123",
        provider="direct_oauth2",
        actor_id="user_1",
        issued_at=datetime.now(UTC),
        nonce="test-nonce",
        redirect_uri="http://127.0.0.1:8020/credentials/oauth/callback",
    )
    callback = ConnectCallback(code="auth-code-xyz")

    with (
        patch(
            "jentic_one.control.repos.OAuthClientCredentialRepository.get_by_credential",
            new_callable=AsyncMock,
            return_value=FakeOCC(),
        ),
        patch("httpx.AsyncClient") as mock_client_cls,
    ):
        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.post = AsyncMock(
            return_value=httpx.Response(200, json={"access_token": "at", "expires_in": 3600})
        )
        mock_client_cls.return_value = mock_client

        await provider.complete_connect(ctx, state=state, callback=callback)

        sent_data = mock_client.post.await_args.kwargs["data"]
        assert sent_data["redirect_uri"] == "http://127.0.0.1:8020/credentials/oauth/callback"
