"""Regression tests for the broker's outbound transport invariants."""

from __future__ import annotations

import ipaddress

import httpx
import pytest

import jentic_one.broker.adapters.http_client as http_client_module
from jentic_one.broker.adapters.http_client import build_client
from jentic_one.broker.adapters.runners.base import RunnerRequest
from jentic_one.broker.adapters.runners.http import HttpRunner
from jentic_one.broker.core.proxy_headers import forward_headers
from jentic_one.shared import egress as egress_module
from jentic_one.shared.config import EgressConfig, UpstreamClientConfig
from jentic_one.shared.egress import DnsPinningTransport

_REDIRECT_LOCATION = "http://169.254.169.254/latest/meta-data/"
_UPSTREAM_URL = "https://api.example.com/resource"


@pytest.mark.asyncio
async def test_shared_client_never_follows_redirects(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen: list[httpx.Request] = []

    def redirect(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(302, headers={"Location": _REDIRECT_LOCATION})

    transport = httpx.MockTransport(redirect)
    monkeypatch.setattr(
        http_client_module,
        "DnsPinningTransport",
        lambda _inner, _egress: transport,
    )
    async with build_client(UpstreamClientConfig(), EgressConfig()) as client:
        assert client.follow_redirects is False
        result = await HttpRunner(client).run(RunnerRequest("GET", _UPSTREAM_URL))

    assert result.status_code == 302
    assert result.headers["location"] == _REDIRECT_LOCATION
    assert [str(request.url) for request in seen] == [_UPSTREAM_URL]


@pytest.mark.asyncio
async def test_streaming_runner_never_follows_redirects(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen: list[httpx.Request] = []

    def redirect(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(302, headers={"Location": _REDIRECT_LOCATION})

    transport = httpx.MockTransport(redirect)
    monkeypatch.setattr(
        http_client_module,
        "DnsPinningTransport",
        lambda _inner, _egress: transport,
    )
    async with build_client(UpstreamClientConfig(), EgressConfig()) as client:
        assert client.follow_redirects is False
        async with HttpRunner(client).stream(RunnerRequest("GET", _UPSTREAM_URL)) as result:
            assert result.status_code == 302
            assert result.headers["location"] == _REDIRECT_LOCATION
            assert [chunk async for chunk in result.aiter] == []

    assert [str(request.url) for request in seen] == [_UPSTREAM_URL]


@pytest.mark.asyncio
async def test_agent_host_header_never_reaches_transport(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    filtered = forward_headers({"Host": "evil.example", "host": "x"}, {})
    assert "host" not in {name.lower() for name in filtered}

    async def resolve_public_host(
        _host: str,
    ) -> tuple[ipaddress.IPv4Address, ...]:
        return (ipaddress.IPv4Address("93.184.216.34"),)

    monkeypatch.setattr(egress_module, "resolve_host", resolve_public_host)
    seen: list[httpx.Request] = []

    def capture(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(204)

    transport = DnsPinningTransport(httpx.MockTransport(capture), EgressConfig())
    async with httpx.AsyncClient(transport=transport, follow_redirects=False) as client:
        response = await client.get(_UPSTREAM_URL, headers=filtered)

    assert response.status_code == 204
    assert len(seen) == 1
    request = seen[0]
    assert request.url.host == "93.184.216.34"
    assert request.headers["Host"] == "api.example.com"
    assert request.extensions["sni_hostname"] == "api.example.com"


@pytest.mark.asyncio
async def test_env_proxy_ignored_when_pinning_enabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("HTTPS_PROXY", "http://10.0.0.1:3128")

    async with build_client(UpstreamClientConfig(), EgressConfig()) as client:
        assert isinstance(client._transport, DnsPinningTransport)
        assert not client._mounts
