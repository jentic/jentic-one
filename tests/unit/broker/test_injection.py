"""Unit tests for broker credential injection logic."""

from __future__ import annotations

import base64
from unittest.mock import MagicMock

import pytest

from jentic_one.broker.core.injection import InjectionResult, inject_auth
from jentic_one.broker.services.credentials.resolver import ResolvedCredential
from jentic_one.shared.models.credentials import (
    CredentialLocation,
    CredentialType,
    StoredCredentialType,
)


def _make_ctx(decrypt_map: dict[str, str]) -> MagicMock:
    ctx = MagicMock()
    ctx.encryption.decrypt.side_effect = lambda blob: decrypt_map[blob]
    return ctx


def test_inject_bearer_token() -> None:
    resolved = ResolvedCredential(
        credential_id="cred_1",
        name="cred1",
        wire_type=CredentialType.BEARER_TOKEN,
        stored_type=StoredCredentialType.STATIC_BEARER_TOKEN,
        provider="static",
        encrypted_secret="enc:my-token",
    )
    ctx = _make_ctx({"enc:my-token": "sk-live-secret"})

    result = inject_auth(resolved, ctx=ctx)

    assert result.headers == {"Authorization": "Bearer sk-live-secret"}
    assert result.query_params == {}


def test_inject_api_key_header() -> None:
    resolved = ResolvedCredential(
        credential_id="cred_2",
        name="cred2",
        wire_type=CredentialType.API_KEY,
        stored_type=StoredCredentialType.API_KEY,
        provider="static",
        encrypted_secret="enc:api-key",
        location=CredentialLocation.HEADER,
        field_name="X-Api-Key",
    )
    ctx = _make_ctx({"enc:api-key": "key-12345"})

    result = inject_auth(resolved, ctx=ctx)

    assert result.headers == {"X-Api-Key": "key-12345"}
    assert result.query_params == {}


def test_inject_api_key_query() -> None:
    resolved = ResolvedCredential(
        credential_id="cred_3",
        name="cred3",
        wire_type=CredentialType.API_KEY,
        stored_type=StoredCredentialType.API_KEY,
        provider="static",
        encrypted_secret="enc:qkey",
        location=CredentialLocation.QUERY,
        field_name="api_key",
    )
    ctx = _make_ctx({"enc:qkey": "query-secret"})

    result = inject_auth(resolved, ctx=ctx)

    assert result.headers == {}
    assert result.query_params == {"api_key": "query-secret"}
    assert result.cookies == {}


def test_inject_api_key_cookie() -> None:
    resolved = ResolvedCredential(
        credential_id="cred_3c",
        name="cred3c",
        wire_type=CredentialType.API_KEY,
        stored_type=StoredCredentialType.API_KEY,
        provider="static",
        encrypted_secret="enc:ckey",  # pragma: allowlist secret
        location=CredentialLocation.COOKIE,
        field_name="session",
    )
    ctx = _make_ctx({"enc:ckey": "cookie-secret"})

    result = inject_auth(resolved, ctx=ctx)

    assert result.headers == {}
    assert result.query_params == {}
    assert result.cookies == {"session": "cookie-secret"}


def test_inject_basic_auth() -> None:
    resolved = ResolvedCredential(
        credential_id="cred_4",
        name="cred4",
        wire_type=CredentialType.BASIC,
        stored_type=StoredCredentialType.BASIC_AUTH,
        provider="static",
        username="admin",
        encrypted_password="enc:pw",
    )
    ctx = _make_ctx({"enc:pw": "secret123"})

    result = inject_auth(resolved, ctx=ctx)

    expected = base64.b64encode(b"admin:secret123").decode()
    assert result.headers == {"Authorization": f"Basic {expected}"}
    assert result.query_params == {}


def test_inject_oauth2_with_access_token() -> None:
    resolved = ResolvedCredential(
        credential_id="cred_5",
        name="cred5",
        wire_type=CredentialType.OAUTH2,
        stored_type=StoredCredentialType.OAUTH2_CLIENT_CREDENTIALS,
        provider="direct_oauth2",
        encrypted_access_token="enc:at",
    )
    ctx = MagicMock()

    result = inject_auth(resolved, ctx=ctx, access_token="access-token-live")

    assert result.headers == {"Authorization": "Bearer access-token-live"}
    assert result.query_params == {}


def test_inject_oauth2_raises_without_access_token() -> None:
    resolved = ResolvedCredential(
        credential_id="cred_6",
        name="cred6",
        wire_type=CredentialType.OAUTH2,
        stored_type=StoredCredentialType.OAUTH2_CLIENT_CREDENTIALS,
        provider="direct_oauth2",
        encrypted_access_token=None,
    )
    ctx = MagicMock()

    with pytest.raises(ValueError, match="pre-validated access_token"):
        inject_auth(resolved, ctx=ctx, access_token=None)


def test_injection_result_dataclass() -> None:
    result = InjectionResult(headers={"A": "B"}, query_params={"c": "d"}, cookies={"e": "f"})
    assert result.headers == {"A": "B"}
    assert result.query_params == {"c": "d"}
    assert result.cookies == {"e": "f"}


def test_inject_no_auth_injects_nothing() -> None:
    # A no-auth credential carries no secret — injection is a no-op (#603).
    resolved = ResolvedCredential(
        credential_id="cred_noauth",
        name="noauth",
        wire_type=CredentialType.NO_AUTH,
        stored_type=StoredCredentialType.NO_AUTH,
        provider="static",
    )
    result = inject_auth(resolved, ctx=MagicMock())
    assert result.headers == {}
    assert result.query_params == {}
    assert result.cookies == {}


def test_inject_unknown_wire_type_raises() -> None:
    # An unknown wire type must fail loudly, not silently inject nothing — a quiet
    # empty injection would send an unauthenticated request (B4 / Ren review).
    resolved = MagicMock()
    resolved.wire_type = "totally-unknown-type"
    with pytest.raises(ValueError, match="Unsupported credential wire_type"):
        inject_auth(resolved, ctx=MagicMock())
