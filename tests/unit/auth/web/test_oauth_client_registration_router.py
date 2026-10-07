"""Unit tests for the anonymous OAuth-client DCR router (POST /oauth-clients)."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from jentic_one.auth.services.errors import AuthServiceError, InvalidClientMetadataError
from jentic_one.auth.services.oauth_dcr_service import DcrRegisterResult
from jentic_one.auth.web.errors import service_error_handler
from jentic_one.auth.web.routers import oauth_client_registration
from jentic_one.shared.config import AuthConfig, OAuthRateLimitConfig, ServerConfig

_BODY = {
    "client_name": "Cursor",
    "redirect_uris": ["http://localhost:33418/callback"],
    "token_endpoint_auth_method": "none",
    "software_id": "com.cursor.ide",
}


def _result(*, created: bool, software_id: str | None = "com.cursor.ide") -> DcrRegisterResult:
    return DcrRegisterResult(
        client_id="oc_abc123",
        client_name="Cursor",
        redirect_uris=["http://localhost:33418/callback"],
        grant_types=["authorization_code", "refresh_token"],
        scope="apis:read capabilities:execute",
        software_id=software_id,
        software_version=None,
        application_type=None,
        client_id_issued_at=1_700_000_000,
        created=created,
    )


def _make_client(
    *, oauth_enabled: bool = True, rate_limit: OAuthRateLimitConfig | None = None
) -> TestClient:
    app = FastAPI()
    app.include_router(oauth_client_registration.router)
    app.add_exception_handler(AuthServiceError, service_error_handler)

    mock_ctx = MagicMock()
    mock_ctx.config.auth = AuthConfig(
        canonical_base_url="https://auth.example.com",
        oauth_rate_limit=rate_limit or OAuthRateLimitConfig(),
    )
    server = ServerConfig()
    server.mcp.oauth.enabled = oauth_enabled
    mock_ctx.config.server = server
    app.state.ctx = mock_ctx
    return TestClient(app)


def test_disabled_returns_plain_404() -> None:
    """server.mcp.oauth.enabled=false → indistinguishable from not-shipped."""
    client = _make_client(oauth_enabled=False)
    resp = client.post("/oauth-clients", json=_BODY)
    assert resp.status_code == 404
    # Exactly the framework's route-not-found body, not a problem-details doc.
    assert resp.json() == {"detail": "Not Found"}


@patch("jentic_one.auth.web.routers.oauth_client_registration.OAuthDcrService")
def test_register_returns_201_with_rfc7591_shape(mock_svc_cls: MagicMock) -> None:
    mock_svc_cls.return_value.register = AsyncMock(return_value=_result(created=True))
    client = _make_client()

    resp = client.post("/oauth-clients", json=_BODY)

    assert resp.status_code == 201
    data = resp.json()
    assert data["client_id"] == "oc_abc123"
    assert data["client_id_issued_at"] == 1_700_000_000
    assert data["token_endpoint_auth_method"] == "none"
    assert data["grant_types"] == ["authorization_code", "refresh_token"]
    assert data["response_types"] == ["code"]
    # Registered optional metadata is echoed back (exclude_none must not
    # drop members that ARE set).
    assert data["software_id"] == "com.cursor.ide"
    # No secret and no RFC 7592 management surface (D12).
    assert "client_secret" not in data
    assert "registration_access_token" not in data


@patch("jentic_one.auth.web.routers.oauth_client_registration.OAuthDcrService")
@pytest.mark.parametrize(("created", "expected_status"), [(True, 201), (False, 200)])
def test_unset_optional_metadata_is_omitted_not_null(
    mock_svc_cls: MagicMock, created: bool, expected_status: int
) -> None:
    """RFC 7591 §3.2.1: optional metadata the client did not register is
    OMITTED from the response body — never serialized as JSON ``null``.
    Strict clients (Cursor's MCP SDK zod schema: software_id /
    software_version must be string-or-absent) reject a null, drop their
    stored client info, and loop re-registering. Pinned on the raw JSON
    body for both the 201-create and 200 D8-dedupe arms."""
    mock_svc_cls.return_value.register = AsyncMock(
        return_value=_result(created=created, software_id=None)
    )
    client = _make_client()

    body = {
        "client_name": "Cursor",
        "redirect_uris": ["http://localhost:33418/callback"],
        "token_endpoint_auth_method": "none",
    }
    resp = client.post("/oauth-clients", json=body)

    assert resp.status_code == expected_status
    data = resp.json()
    assert "software_id" not in data
    assert "software_version" not in data
    assert "application_type" not in data
    # Belt-and-braces: the raw body carries no null members at all.
    assert b"null" not in resp.content


@patch("jentic_one.auth.web.routers.oauth_client_registration.OAuthDcrService")
def test_dedupe_hit_returns_200_with_existing_client_id(mock_svc_cls: MagicMock) -> None:
    mock_svc_cls.return_value.register = AsyncMock(return_value=_result(created=False))
    client = _make_client()

    resp = client.post("/oauth-clients", json=_BODY)

    assert resp.status_code == 200
    assert resp.json()["client_id"] == "oc_abc123"


@patch("jentic_one.auth.web.routers.oauth_client_registration.OAuthDcrService")
def test_invalid_metadata_maps_to_rfc7591_400(mock_svc_cls: MagicMock) -> None:
    """Service-level rejections carry the RFC 7591 §3.2.2 top-level `error`."""
    mock_svc_cls.return_value.register = AsyncMock(
        side_effect=InvalidClientMetadataError("token_endpoint_auth_method must be 'none'")
    )
    client = _make_client()

    resp = client.post(
        "/oauth-clients", json={**_BODY, "token_endpoint_auth_method": "client_secret_basic"}
    )

    assert resp.status_code == 400
    data = resp.json()
    assert data["error"] == "invalid_client_metadata"
    assert "token_endpoint_auth_method" in data["error_description"]


def test_missing_required_metadata_is_rfc7591_400() -> None:
    """Schema-level rejections are reshaped from FastAPI's 422 into the
    RFC 7591 400 invalid_client_metadata (F3)."""
    client = _make_client()
    resp = client.post("/oauth-clients", json={"client_name": "no-redirects"})
    assert resp.status_code == 400
    data = resp.json()
    assert data["error"] == "invalid_client_metadata"
    assert "redirect_uris" in data["error_description"]


def test_malformed_json_is_rfc7591_400() -> None:
    client = _make_client()
    resp = client.post(
        "/oauth-clients", content=b"{not json", headers={"Content-Type": "application/json"}
    )
    assert resp.status_code == 400
    assert resp.json()["error"] == "invalid_client_metadata"


def test_oversized_body_is_413() -> None:
    """Declared-length bodies beyond the raw cap are refused before parsing."""
    client = _make_client()
    resp = client.post(
        "/oauth-clients",
        content=b'{"client_name": "' + b"a" * (65 * 1024) + b'"}',
        headers={"Content-Type": "application/json"},
    )
    assert resp.status_code == 413
    assert resp.json()["error"] == "invalid_client_metadata"


@patch("jentic_one.auth.web.routers.oauth_client_registration.OAuthDcrService")
def test_registration_rate_limit_enforced_per_ip(mock_svc_cls: MagicMock) -> None:
    """Burst exhaustion → 429 with Retry-After (registration_rpm/burst knobs)."""
    mock_svc_cls.return_value.register = AsyncMock(return_value=_result(created=True))
    client = _make_client(rate_limit=OAuthRateLimitConfig(registration_rpm=1, registration_burst=1))

    first = client.post("/oauth-clients", json=_BODY)
    second = client.post("/oauth-clients", json=_BODY)

    assert first.status_code == 201
    assert second.status_code == 429
    assert int(second.headers["Retry-After"]) >= 1
    assert second.json()["type"] == "rate_limit_exceeded"


@patch("jentic_one.auth.web.routers.oauth_client_registration.OAuthDcrService")
def test_disabled_is_404_even_over_quota(mock_svc_cls: MagicMock) -> None:
    """F2: the enabled gate runs before the rate-limit dependency, so a
    disabled door never answers 429 — every probe gets the same 404 a build
    without the route would return (no quota is spent either)."""
    mock_svc_cls.return_value.register = AsyncMock(return_value=_result(created=True))
    client = _make_client(
        oauth_enabled=False,
        rate_limit=OAuthRateLimitConfig(registration_rpm=1, registration_burst=1),
    )

    for _ in range(3):
        resp = client.post("/oauth-clients", json=_BODY)
        assert resp.status_code == 404
        assert resp.json() == {"detail": "Not Found"}


def test_disabled_is_404_for_malformed_body() -> None:
    """F2: body validation must not reveal a disabled endpoint (a 422/400
    where a missing route would 404 is a feature-presence oracle)."""
    client = _make_client(oauth_enabled=False)
    missing_fields = client.post("/oauth-clients", json={"client_name": "no-redirects"})
    malformed = client.post(
        "/oauth-clients", content=b"{not json", headers={"Content-Type": "application/json"}
    )
    assert missing_fields.status_code == 404
    assert missing_fields.json() == {"detail": "Not Found"}
    assert malformed.status_code == 404
    assert malformed.json() == {"detail": "Not Found"}


@pytest.mark.parametrize("scope", ["a" * 65, " ".join(f"s{i}" for i in range(101))])
def test_oversized_scope_rejected_at_schema(scope: str) -> None:
    client = _make_client()
    resp = client.post("/oauth-clients", json={**_BODY, "scope": scope})
    assert resp.status_code == 400
    assert resp.json()["error"] == "invalid_client_metadata"
