"""Self-tests for the live, ingestable OpenAPI spec router.

These verify the document the smoke-test agent ingests is well-formed and that
its declared paths/security match the real harness routes and configurable base
URL — the contract Phase 2+ relies on.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from cryptography import x509
from fastapi import FastAPI
from httpx import AsyncClient

from tests.harness.smoke_upstream.__main__ import write_self_signed_cert
from tests.harness.smoke_upstream.routers.live_spec import (
    DEFAULT_PUBLIC_URL,
    DEFAULT_TLS_PUBLIC_URL,
    LIVE_SPEC_PATH,
    OAUTH_TOKEN_PATH,
    PUBLIC_URL_ENV,
    SECURITY_SCHEME_NAMES,
    TLS_PUBLIC_URL_ENV,
)


def _real_route_paths(app: FastAPI) -> set[str]:
    return set(app.openapi()["paths"])


async def test_live_spec_is_openapi_31(smoke_client: AsyncClient) -> None:
    response = await smoke_client.get(LIVE_SPEC_PATH)
    assert response.status_code == 200
    body = response.json()
    assert body["openapi"] == "3.1.0"
    assert body["info"]["x-vendor"] == "smoke-upstream"
    assert body["servers"][0]["url"] == DEFAULT_PUBLIC_URL


async def test_live_spec_declares_all_security_schemes(smoke_client: AsyncClient) -> None:
    body = (await smoke_client.get(LIVE_SPEC_PATH)).json()
    schemes = body["components"]["securitySchemes"]
    assert set(schemes) == set(SECURITY_SCHEME_NAMES)
    token_url = schemes["oauth2Auth"]["flows"]["clientCredentials"]["tokenUrl"]
    assert token_url == f"{DEFAULT_TLS_PUBLIC_URL}{OAUTH_TOKEN_PATH}"


async def test_auth_operations_declare_security(smoke_client: AsyncClient) -> None:
    paths = (await smoke_client.get(LIVE_SPEC_PATH)).json()["paths"]
    assert paths["/auth/bearer"]["get"]["security"] == [{"bearerAuth": []}]
    assert paths["/auth/basic"]["get"]["security"] == [{"basicAuth": []}]
    assert paths["/auth/api-key"]["get"]["security"] == [{"apiKeyAuth": []}]
    assert paths["/auth/oauth2"]["get"]["security"] == [{"oauth2Auth": []}]
    assert paths["/auth/complex"]["get"]["security"] == [{"apiKeyAuth": [], "appIdAuth": []}]


async def test_every_spec_path_is_a_real_route(
    smoke_app: FastAPI,
    smoke_client: AsyncClient,
) -> None:
    paths = (await smoke_client.get(LIVE_SPEC_PATH)).json()["paths"]
    real = _real_route_paths(smoke_app)
    missing = [path for path in paths if path not in real]
    assert not missing, f"spec declares paths with no matching route: {missing}"


async def test_public_url_override_is_reflected(
    smoke_client: AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    override = "http://jentic-smoke-upstream:8084"
    monkeypatch.setenv(PUBLIC_URL_ENV, override)
    body = (await smoke_client.get(LIVE_SPEC_PATH)).json()
    assert body["servers"][0]["url"] == override
    token_url = body["components"]["securitySchemes"]["oauth2Auth"]["flows"]["clientCredentials"][
        "tokenUrl"
    ]
    assert token_url == f"https://jentic-smoke-upstream:8443{OAUTH_TOKEN_PATH}"


async def test_tls_public_url_override_is_reflected(
    smoke_client: AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tls_override = "https://jentic-smoke-upstream:9443"
    monkeypatch.setenv(TLS_PUBLIC_URL_ENV, tls_override)
    body = (await smoke_client.get(LIVE_SPEC_PATH)).json()
    token_url = body["components"]["securitySchemes"]["oauth2Auth"]["flows"]["clientCredentials"][
        "tokenUrl"
    ]
    assert token_url == f"{tls_override}{OAUTH_TOKEN_PATH}"


def test_write_self_signed_cert_covers_incluster_and_local_hosts(tmp_path: Path) -> None:
    cert_path, key_path = write_self_signed_cert(tmp_path)
    assert cert_path.is_file()
    assert key_path.is_file()
    cert = x509.load_pem_x509_certificate(cert_path.read_bytes())
    san = cert.extensions.get_extension_for_class(x509.SubjectAlternativeName).value
    dns_names = set(san.get_values_for_type(x509.DNSName))
    assert {"localhost", "jentic-smoke-upstream"} <= dns_names


async def test_health_returns_ok(smoke_client: AsyncClient) -> None:
    response = await smoke_client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


async def test_oauth_token_stub_returns_bearer(smoke_client: AsyncClient) -> None:
    response = await smoke_client.post(OAUTH_TOKEN_PATH)
    assert response.status_code == 200
    body = response.json()
    assert body["token_type"] == "Bearer"
    assert body["access_token"]
