"""Self-tests for the auth-validation router."""

from __future__ import annotations

import base64

import pytest
from httpx import AsyncClient

from tests.harness.smoke_upstream.routers.auth import (
    HEADER_API_KEY,
    HEADER_APP_ID,
    HEADER_AUTHORIZATION,
    AuthScheme,
)

_BASIC_CREDENTIAL = base64.b64encode(b"user:pass").decode("ascii")


@pytest.mark.parametrize(
    ("path", "scheme"),
    [
        ("/auth/bearer", AuthScheme.BEARER),
        ("/auth/basic", AuthScheme.BASIC),
        ("/auth/api-key", AuthScheme.API_KEY),
        ("/auth/oauth2", AuthScheme.OAUTH2),
    ],
)
async def test_auth_unauthorized_without_credential(
    smoke_client: AsyncClient,
    path: str,
    scheme: AuthScheme,
) -> None:
    response = await smoke_client.get(path)
    assert response.status_code == 401
    body = response.json()
    assert body["authenticated"] is False
    assert body["scheme"] == scheme.value


@pytest.mark.parametrize(
    ("path", "scheme", "header_name", "header_value"),
    [
        ("/auth/bearer", AuthScheme.BEARER, HEADER_AUTHORIZATION, "Bearer abc123"),
        ("/auth/basic", AuthScheme.BASIC, HEADER_AUTHORIZATION, f"Basic {_BASIC_CREDENTIAL}"),
        ("/auth/api-key", AuthScheme.API_KEY, HEADER_API_KEY, "secret-api-key"),
        ("/auth/oauth2", AuthScheme.OAUTH2, HEADER_AUTHORIZATION, "Bearer oauth-token"),
    ],
)
async def test_auth_authorized_with_credential(
    smoke_client: AsyncClient,
    path: str,
    scheme: AuthScheme,
    header_name: str,
    header_value: str,
) -> None:
    response = await smoke_client.get(path, headers={header_name: header_value})
    assert response.status_code == 200
    body = response.json()
    assert body["authenticated"] is True
    assert body["scheme"] == scheme.value


@pytest.mark.parametrize(
    "headers",
    [
        {},
        {HEADER_API_KEY: "key-only"},  # pragma: allowlist secret
        {HEADER_APP_ID: "app-only"},
    ],
)
async def test_auth_complex_unauthorized_without_both(
    smoke_client: AsyncClient,
    headers: dict[str, str],
) -> None:
    response = await smoke_client.get("/auth/complex", headers=headers)
    assert response.status_code == 401
    assert response.json()["scheme"] == AuthScheme.COMPLEX.value


async def test_auth_complex_authorized_with_both(smoke_client: AsyncClient) -> None:
    headers = {
        HEADER_API_KEY: "secret-api-key",  # pragma: allowlist secret
        HEADER_APP_ID: "app-123",
    }
    response = await smoke_client.get("/auth/complex", headers=headers)
    assert response.status_code == 200
    body = response.json()
    assert body["authenticated"] is True
    assert body["scheme"] == AuthScheme.COMPLEX.value
