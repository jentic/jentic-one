"""Unit tests for the license clients (``client.py``).

Request assertions (signed headers, X-Amz-Target, body shape) and the verdict
mapping table for both pricing variants, via ``httpx.MockTransport``.
"""

from __future__ import annotations

import json

import httpx
import pytest

from jentic_one.integrations.aws_marketplace.client import (
    LicenseManagerClient,
    LicenseVerdict,
    MeteringLicenseClient,
    build_license_client,
)
from jentic_one.shared.config import EntitlementConfig


def _config(**overrides: object) -> EntitlementConfig:
    values: dict[str, object] = {
        "enabled": True,
        "product_code": "prod-abc123",
        "region": "us-east-1",
        # Transport-level tests use the simpler metering variant; contract
        # tests override explicitly (the *config* default is "contract").
        "pricing_model": "usage",
    }
    values.update(overrides)
    return EntitlementConfig.model_validate(values)


def _client_pair(
    handler: httpx.MockTransport, config: EntitlementConfig, monkeypatch: pytest.MonkeyPatch
) -> tuple[MeteringLicenseClient | LicenseManagerClient, httpx.AsyncClient]:
    """Build the configured client variant with static env credentials."""
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "AKIATEST")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "secret")
    monkeypatch.delenv("AWS_SESSION_TOKEN", raising=False)
    http = httpx.AsyncClient(transport=handler)
    client = build_license_client(config, http)
    assert isinstance(client, MeteringLicenseClient | LicenseManagerClient)
    return client, http


def _capture(
    status: int, body: dict[str, object] | None = None
) -> tuple[httpx.MockTransport, list[httpx.Request]]:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(status, json=body if body is not None else {})

    return httpx.MockTransport(handler), seen


@pytest.mark.asyncio
async def test_register_usage_request_shape(monkeypatch: pytest.MonkeyPatch) -> None:
    transport, seen = _capture(200)
    client, _ = _client_pair(transport, _config(), monkeypatch)

    verdict = await client.check()

    assert verdict is LicenseVerdict.ENTITLED
    request = seen[0]
    assert request.url.host == "metering.marketplace.us-east-1.amazonaws.com"
    assert request.headers["x-amz-target"] == "AWSMPMeteringService.RegisterUsage"
    assert request.headers["content-type"] == "application/x-amz-json-1.1"
    # SigV4 headers from the shared signer.
    assert request.headers["authorization"].startswith("AWS4-HMAC-SHA256 ")
    assert "x-amz-date" in request.headers
    assert "x-amz-content-sha256" in request.headers
    body = json.loads(request.content)
    assert body["ProductCode"] == "prod-abc123"
    assert body["PublicKeyVersion"] == 1
    assert body["Nonce"]


@pytest.mark.asyncio
async def test_checkout_license_request_shape(monkeypatch: pytest.MonkeyPatch) -> None:
    transport, seen = _capture(200)
    config = _config(
        pricing_model="contract",
        license_sku="prod-id-1",
        license_dimensions=["users", "executions"],
    )
    client, _ = _client_pair(transport, config, monkeypatch)

    verdict = await client.check()

    assert verdict is LicenseVerdict.ENTITLED
    request = seen[0]
    assert request.url.host == "license-manager.us-east-1.amazonaws.com"
    assert request.headers["x-amz-target"] == "AWSLicenseManager.CheckoutLicense"
    body = json.loads(request.content)
    assert body["CheckoutType"] == "PROVISIONAL"
    assert body["ProductSKU"] == "prod-id-1"
    assert body["Entitlements"] == [
        {"Name": "users", "Unit": "None"},
        {"Name": "executions", "Unit": "None"},
    ]
    assert body["ClientToken"]


@pytest.mark.asyncio
async def test_endpoint_override_wins(monkeypatch: pytest.MonkeyPatch) -> None:
    transport, seen = _capture(200)
    config = _config(endpoint="http://stub.local:9999/")
    client, _ = _client_pair(transport, config, monkeypatch)

    await client.check()

    assert str(seen[0].url) == "http://stub.local:9999/"


@pytest.mark.parametrize(
    ("pricing_model", "error_type", "expected"),
    [
        ("usage", "CustomerNotEntitledException", LicenseVerdict.NOT_ENTITLED),
        ("usage", "PlatformNotSupportedException", LicenseVerdict.NOT_ENTITLED),
        ("usage", "ThrottlingException", LicenseVerdict.UNKNOWN),
        ("contract", "NoEntitlementsAllowedException", LicenseVerdict.NOT_ENTITLED),
        ("contract", "EntitlementNotAllowedException", LicenseVerdict.NOT_ENTITLED),
        ("contract", "ResourceNotFoundException", LicenseVerdict.NOT_ENTITLED),
        ("contract", "ValidationException", LicenseVerdict.UNKNOWN),
    ],
)
@pytest.mark.asyncio
async def test_error_type_verdict_mapping(
    monkeypatch: pytest.MonkeyPatch,
    pricing_model: str,
    error_type: str,
    expected: LicenseVerdict,
) -> None:
    transport, _ = _capture(
        400, {"__type": f"com.amazonaws.services#{error_type}", "message": "no"}
    )
    overrides: dict[str, object] = {"pricing_model": pricing_model}
    if pricing_model == "contract":
        overrides["license_sku"] = "prod-id-1"
    client, _ = _client_pair(transport, _config(**overrides), monkeypatch)

    assert await client.check() is expected


@pytest.mark.asyncio
async def test_error_type_header_fallback(monkeypatch: pytest.MonkeyPatch) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            400,
            content=b"",
            headers={"x-amzn-errortype": "CustomerNotEntitledException:http://internal"},
        )

    client, _ = _client_pair(httpx.MockTransport(handler), _config(), monkeypatch)

    assert await client.check() is LicenseVerdict.NOT_ENTITLED


@pytest.mark.asyncio
async def test_server_error_is_unknown(monkeypatch: pytest.MonkeyPatch) -> None:
    transport, _ = _capture(500, {"__type": "InternalServiceErrorException"})
    client, _ = _client_pair(transport, _config(), monkeypatch)

    assert await client.check() is LicenseVerdict.UNKNOWN


@pytest.mark.asyncio
async def test_network_failure_is_unknown(monkeypatch: pytest.MonkeyPatch) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("unreachable", request=request)

    client, _ = _client_pair(httpx.MockTransport(handler), _config(), monkeypatch)

    assert await client.check() is LicenseVerdict.UNKNOWN


@pytest.mark.asyncio
async def test_missing_credentials_is_unknown(monkeypatch: pytest.MonkeyPatch) -> None:
    for var in (
        "AWS_ACCESS_KEY_ID",
        "AWS_SECRET_ACCESS_KEY",
        "AWS_CONTAINER_CREDENTIALS_RELATIVE_URI",
        "AWS_CONTAINER_CREDENTIALS_FULL_URI",
        "AWS_WEB_IDENTITY_TOKEN_FILE",
        "AWS_ROLE_ARN",
    ):
        monkeypatch.delenv(var, raising=False)
    transport, seen = _capture(200)
    http = httpx.AsyncClient(transport=transport)
    client = build_license_client(_config(), http)

    assert await client.check() is LicenseVerdict.UNKNOWN
    assert seen == []  # never sent an unsigned request


def test_build_selects_variant_and_service() -> None:
    http = httpx.AsyncClient(transport=httpx.MockTransport(lambda _r: httpx.Response(200)))

    usage = build_license_client(_config(), http)
    contract = build_license_client(
        _config(pricing_model="contract", license_sku="prod-id-1"), http
    )

    assert isinstance(usage, MeteringLicenseClient)
    assert isinstance(contract, LicenseManagerClient)


@pytest.mark.asyncio
async def test_session_token_signed_when_present(monkeypatch: pytest.MonkeyPatch) -> None:
    transport, seen = _capture(200)
    client, _ = _client_pair(transport, _config(), monkeypatch)
    # Credentials resolve lazily at check() time, so this takes effect.
    monkeypatch.setenv("AWS_SESSION_TOKEN", "temp-token")

    await client.check()

    assert seen[0].headers["x-amz-security-token"] == "temp-token"
