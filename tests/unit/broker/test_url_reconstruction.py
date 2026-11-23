"""Unit tests for upstream URL reconstruction from the raw ASGI scope."""

from __future__ import annotations

import ipaddress
from unittest.mock import MagicMock

import pytest
from fastapi import FastAPI, Request, Response
from fastapi.testclient import TestClient

from jentic_one.broker.adapters.runners.base import RunnerRequest, RunnerResult
from jentic_one.broker.adapters.runners.registry import RunnerRegistry
from jentic_one.broker.core.headers import JenticHeader
from jentic_one.broker.core.proxy_headers import reconstruct_upstream_url
from jentic_one.broker.web.deps import HttpRunnerDep
from jentic_one.broker.web.errors import install_broker_error_handlers
from jentic_one.broker.web.routers.execute import _handle
from jentic_one.shared.auth.identity import Identity
from jentic_one.shared.config import BrokerConfig, EgressConfig


def _scope(raw_path: str, query_string: str = "") -> dict[str, object]:
    return {
        "raw_path": raw_path.encode("latin-1"),
        "query_string": query_string.encode("latin-1"),
    }


def test_bare_host_gets_https_scheme() -> None:
    url = reconstruct_upstream_url(_scope("/api.stripe.com/v1/charges"))
    assert url == "https://api.stripe.com/v1/charges"


def test_explicit_scheme_round_trips() -> None:
    url = reconstruct_upstream_url(_scope("/https://x/y"))
    assert url == "https://x/y"


def test_http_scheme_preserved() -> None:
    url = reconstruct_upstream_url(_scope("/http://example.com/p"))
    assert url == "http://example.com/p"


def test_query_string_reattached() -> None:
    url = reconstruct_upstream_url(_scope("/https://x/y", "a=1&b=2"))
    assert url == "https://x/y?a=1&b=2"


def test_encoded_slash_survives() -> None:
    # A single path segment containing a literal slash must NOT be collapsed.
    url = reconstruct_upstream_url(_scope("/https://api.github.com/repos/jentic%2Fcore"))
    assert url == "https://api.github.com/repos/jentic%2Fcore"
    assert "%2F" in url


def test_encoded_question_mark_survives_in_path() -> None:
    url = reconstruct_upstream_url(_scope("/https://x/a%3Fb"))
    assert "%3F" in url


@pytest.mark.parametrize(
    ("raw_path", "query", "expected"),
    [
        ("/HTTPS://x/y", "", "https://x/y"),
        ("/Https://x/y", "", "https://x/y"),
        ("/HTTP://example.com/p", "", "http://example.com/p"),
        ("/Http://example.com/p", "", "http://example.com/p"),
        ("/HTTPS:/x/y", "", "https://x/y"),
        ("/Https:/x/y", "", "https://x/y"),
        ("/HTTP:/example.com/p", "", "http://example.com/p"),
        ("/Http:/example.com/p", "", "http://example.com/p"),
        (
            "/HTTPS://Api.Example.COM/MixedCase/Path",
            "Key=Value",
            "https://Api.Example.COM/MixedCase/Path?Key=Value",
        ),
        (
            "/Https:/Api.Example.COM/MixedCase/Path",
            "Key=Value",
            "https://Api.Example.COM/MixedCase/Path?Key=Value",
        ),
    ],
)
def test_mixed_case_http_schemes_normalised_without_changing_destination(
    raw_path: str, query: str, expected: str
) -> None:
    """Pins #1897: scheme matching is case-insensitive; host/path/query case stays intact."""
    assert reconstruct_upstream_url(_scope(raw_path, query)) == expected


@pytest.mark.parametrize(
    ("raw_path", "expected"),
    [
        ("/localhost:8080/foo", "https://localhost:8080/foo"),
        ("/example.com:8443/v1/items", "https://example.com:8443/v1/items"),
        ("/[::1]:8080/foo", "https://[::1]:8080/foo"),
        ("/api.example.com/v1/ftp://nested", "https://api.example.com/v1/ftp://nested"),
        ("/api.example.com/v1:/colon-segment", "https://api.example.com/v1:/colon-segment"),
    ],
)
def test_schemeless_host_port_defaults_to_https(raw_path: str, expected: str) -> None:
    """Pins #1897: host:port/path without a scheme still defaults to https://."""
    assert reconstruct_upstream_url(_scope(raw_path)) == expected


@pytest.mark.parametrize(
    ("raw_path", "expected"),
    [
        ("/ftp://files.example.com/x", "ftp://files.example.com/x"),
        ("/FTP://Files.Example.COM/x", "ftp://Files.Example.COM/x"),
        ("/ftp:/files.example.com/x", "ftp://files.example.com/x"),
        ("/ws://echo.example.com/socket", "ws://echo.example.com/socket"),
        ("/wss:/echo.example.com/socket", "wss://echo.example.com/socket"),
        ("/git+https://github.com/org/repo", "git+https://github.com/org/repo"),
        ("/file:///etc/passwd", "file:///etc/passwd"),
    ],
)
def test_non_http_schemes_are_preserved_not_prefixed_with_https(
    raw_path: str, expected: str
) -> None:
    """Pins #1897: explicit non-http(s) schemes are never rewritten into https://<scheme>:..."""
    assert reconstruct_upstream_url(_scope(raw_path)) == expected


class _NoopRunner:
    async def run(self, request: RunnerRequest) -> RunnerResult:
        return RunnerResult(status_code=200, body=b"", headers={}, content_type=None, duration_ms=1)


def _proxy_client(*, use_runner_registry: bool) -> tuple[TestClient, list[str]]:
    seen_urls: list[str] = []

    class _RecordingResolver:
        async def resolve_operation(
            self, *, method: str, url: str, revision_id: object = None
        ) -> None:
            seen_urls.append(url)
            return None

    ctx = MagicMock()
    ctx.config.broker = BrokerConfig(egress=EgressConfig())

    app = FastAPI()
    install_broker_error_handlers(app)
    app.state.broker_registry_resolver = _RecordingResolver()

    if use_runner_registry:
        registry = RunnerRegistry()
        registry.register(["http", "https"], _NoopRunner(), required=True)
        app.state.broker_runner_registry = registry

        @app.api_route("/{upstream_url:path}", methods=["GET"])
        async def proxy_with_registry(
            upstream_url: str, request: Request, runner: HttpRunnerDep
        ) -> Response:
            return await _handle(
                request,
                request.method,
                ctx,
                Identity(sub="agnt_test", permissions=[]),
                MagicMock(),
                MagicMock(),
                runner,
                None,
            )
    else:

        @app.api_route("/{upstream_url:path}", methods=["GET"])
        async def proxy_direct(upstream_url: str, request: Request) -> Response:
            return await _handle(
                request,
                request.method,
                ctx,
                Identity(sub="agnt_test", permissions=[]),
                MagicMock(),
                MagicMock(),
                _NoopRunner(),
                None,
            )

    return TestClient(app, raise_server_exceptions=False), seen_urls


@pytest.mark.parametrize(
    ("path", "expected_url"),
    [
        ("/HTTPS://example.com/v1/Items?Page=2", "https://example.com/v1/Items?Page=2"),
        ("/Https:/example.com/v1/Items", "https://example.com/v1/Items"),
        ("/HTTP:/example.com/v1/Items", "http://example.com/v1/Items"),
    ],
)
def test_proxy_route_accepts_mixed_case_http_schemes(
    monkeypatch: pytest.MonkeyPatch, path: str, expected_url: str
) -> None:
    """Pins #1897: mixed-case HTTP(S) proxy paths select the HTTP runner and reach discovery."""

    async def _public_dns(_hostname: str) -> tuple[object, ...]:
        return (ipaddress.ip_address("93.184.216.34"),)

    monkeypatch.setattr("jentic_one.shared.url_validation.resolve_host", _public_dns)
    client, seen_urls = _proxy_client(use_runner_registry=True)

    resp = client.get(path)

    assert resp.status_code == 404
    assert resp.json()["type"] == "operation_not_found"
    assert seen_urls == [expected_url]


@pytest.mark.parametrize(
    "path",
    [
        "/ftp://files.example.com/x",
        "/FTP:/files.example.com/x",
        "/ws://echo.example.com/socket",
        "/file:///etc/passwd",
    ],
)
def test_proxy_route_rejects_non_http_schemes_at_runner_and_egress(path: str) -> None:
    """Pins #1897: non-HTTP(S) schemes fail as unsupported scheme rather than https://<scheme>."""
    registry_client, registry_seen = _proxy_client(use_runner_registry=True)
    resp_reg = registry_client.get(path)
    assert resp_reg.status_code == 501
    assert resp_reg.headers[JenticHeader.ERROR_ORIGIN.value] == "broker"
    assert resp_reg.json()["type"] == "runner_scheme_unsupported"
    assert registry_seen == []

    direct_client, direct_seen = _proxy_client(use_runner_registry=False)
    resp_direct = direct_client.get(path)
    assert resp_direct.status_code == 400
    assert resp_direct.headers[JenticHeader.ERROR_ORIGIN.value] == "broker"
    assert resp_direct.json()["type"] == "invalid_upstream_url"
    assert "unsupported scheme" in resp_direct.json()["detail"]
    assert direct_seen == []
