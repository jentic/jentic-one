"""Unit tests for the AWS credential chain (``credentials.py``).

All HTTP goes through ``httpx.MockTransport`` — no live sockets. Env vars are
managed per-test via monkeypatch so the chain's precedence is deterministic.
"""

from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest

from jentic_one.integrations.aws_marketplace.credentials import (
    CredentialProvider,
    CredentialResolutionError,
)

_AWS_ENV_VARS = (
    "AWS_ACCESS_KEY_ID",
    "AWS_SECRET_ACCESS_KEY",
    "AWS_SESSION_TOKEN",
    "AWS_CONTAINER_CREDENTIALS_RELATIVE_URI",
    "AWS_CONTAINER_CREDENTIALS_FULL_URI",
    "AWS_CONTAINER_AUTHORIZATION_TOKEN",
    "AWS_WEB_IDENTITY_TOKEN_FILE",
    "AWS_ROLE_ARN",
)


@pytest.fixture(autouse=True)
def _clean_aws_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Strip ambient AWS env so CI runner credentials never leak into tests."""
    for var in _AWS_ENV_VARS:
        monkeypatch.delenv(var, raising=False)


def _provider(
    handler: httpx.MockTransport | None = None, *, service: str = "aws-marketplace"
) -> CredentialProvider:
    transport = handler or httpx.MockTransport(lambda _req: httpx.Response(500))
    return CredentialProvider(
        httpx.AsyncClient(transport=transport), region="us-east-1", service=service
    )


@pytest.mark.asyncio
async def test_no_source_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    with pytest.raises(CredentialResolutionError):
        await _provider().resolve()


@pytest.mark.asyncio
async def test_static_env_wins(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "AKIASTATIC")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "secret")  # pragma: allowlist secret
    monkeypatch.setenv("AWS_SESSION_TOKEN", "token")
    # Also configure the container source — static env must take precedence.
    monkeypatch.setenv("AWS_CONTAINER_CREDENTIALS_RELATIVE_URI", "/v2/creds")

    material = await _provider().resolve()

    assert material.access_key_id == "AKIASTATIC"
    assert material.secret_access_key == "secret"  # pragma: allowlist secret
    assert material.session_token == "token"
    assert material.region == "us-east-1"
    assert material.service == "aws-marketplace"


@pytest.mark.asyncio
async def test_container_relative_uri(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AWS_CONTAINER_CREDENTIALS_RELATIVE_URI", "/v2/creds")
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(
            200,
            json={
                "AccessKeyId": "AKIATASK",
                "SecretAccessKey": "tasksecret",  # pragma: allowlist secret
                "Token": "tasktoken",
                "Expiration": "2099-01-01T00:00:00Z",
            },
        )

    material = await _provider(httpx.MockTransport(handler)).resolve()

    assert str(seen[0].url) == "http://169.254.170.2/v2/creds"
    assert material.access_key_id == "AKIATASK"
    assert material.session_token == "tasktoken"


@pytest.mark.asyncio
async def test_container_full_uri_sends_auth_token(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AWS_CONTAINER_CREDENTIALS_FULL_URI", "http://localhost:8901/creds")
    monkeypatch.setenv("AWS_CONTAINER_AUTHORIZATION_TOKEN", "Bearer local-token")
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(
            200, json={"AccessKeyId": "AKIAFULL", "SecretAccessKey": "s", "Token": "t"}
        )

    material = await _provider(httpx.MockTransport(handler)).resolve()

    assert str(seen[0].url) == "http://localhost:8901/creds"
    assert seen[0].headers["authorization"] == "Bearer local-token"
    assert material.access_key_id == "AKIAFULL"


@pytest.mark.asyncio
async def test_web_identity_sts_exchange(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    token_file = tmp_path / "oidc-token"
    token_file.write_text("oidc-jwt\n")
    monkeypatch.setenv("AWS_WEB_IDENTITY_TOKEN_FILE", str(token_file))
    monkeypatch.setenv("AWS_ROLE_ARN", "arn:aws:iam::123456789012:role/entitlement")
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(
            200,
            json={
                "AssumeRoleWithWebIdentityResponse": {
                    "AssumeRoleWithWebIdentityResult": {
                        "Credentials": {
                            "AccessKeyId": "ASIAIRSA",
                            "SecretAccessKey": "irsasecret",  # pragma: allowlist secret
                            "SessionToken": "irsatoken",
                            "Expiration": "2099-01-01T00:00:00Z",
                        }
                    }
                }
            },
        )

    material = await _provider(httpx.MockTransport(handler)).resolve()

    request = seen[0]
    assert request.url.host == "sts.us-east-1.amazonaws.com"
    body = request.content.decode()
    assert "Action=AssumeRoleWithWebIdentity" in body
    assert "WebIdentityToken=oidc-jwt" in body
    # The STS exchange is deliberately unsigned (the token is the proof).
    assert "authorization" not in request.headers
    assert material.access_key_id == "ASIAIRSA"
    assert material.session_token == "irsatoken"


@pytest.mark.asyncio
async def test_expired_temporaries_are_refetched(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AWS_CONTAINER_CREDENTIALS_RELATIVE_URI", "/v2/creds")
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(
            200,
            json={
                "AccessKeyId": f"AKIA{calls}",
                "SecretAccessKey": "s",
                "Token": "t",
                # Already inside the refresh skew — every resolve refetches.
                "Expiration": "2000-01-01T00:00:00Z",
            },
        )

    provider = _provider(httpx.MockTransport(handler))
    first = await provider.resolve()
    second = await provider.resolve()

    assert calls == 2
    assert (first.access_key_id, second.access_key_id) == ("AKIA1", "AKIA2")


@pytest.mark.asyncio
async def test_unexpired_temporaries_are_cached(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AWS_CONTAINER_CREDENTIALS_RELATIVE_URI", "/v2/creds")
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(
            200,
            json={
                "AccessKeyId": "AKIACACHED",
                "SecretAccessKey": "s",
                "Token": "t",
                "Expiration": "2099-01-01T00:00:00Z",
            },
        )

    provider = _provider(httpx.MockTransport(handler))
    await provider.resolve()
    await provider.resolve()

    assert calls == 1


@pytest.mark.asyncio
async def test_container_endpoint_failure_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AWS_CONTAINER_CREDENTIALS_RELATIVE_URI", "/v2/creds")

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, text="boom")

    with pytest.raises(CredentialResolutionError):
        await _provider(httpx.MockTransport(handler)).resolve()


@pytest.mark.asyncio
async def test_malformed_sts_response_raises(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    token_file = tmp_path / "oidc-token"
    token_file.write_text("oidc-jwt")
    monkeypatch.setenv("AWS_WEB_IDENTITY_TOKEN_FILE", str(token_file))
    monkeypatch.setenv("AWS_ROLE_ARN", "arn:aws:iam::123456789012:role/entitlement")

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=json.dumps({"unexpected": "shape"}))

    with pytest.raises(CredentialResolutionError):
        await _provider(httpx.MockTransport(handler)).resolve()
