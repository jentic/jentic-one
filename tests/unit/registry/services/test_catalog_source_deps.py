"""Unit tests for the production ``SourceDeps`` HTTP client (``catalog/sources/deps.py``).

``GuardedSourceHttpClient`` is the only HTTP surface a catalog source connector
gets. These pin its contract: the catalog user agent on every request, source
auth headers only to the request URL's origin, ``404``/``410`` as
``SpecGoneError``, everything else as ``SourceUnavailableError`` (a ``429``
blocking the source for its ``Retry-After``), and conditional fetches mapped
to ``NotModified`` / ``SpecBody``.
"""

from __future__ import annotations

import hashlib
from collections.abc import Callable, Mapping
from datetime import UTC, datetime, timedelta
from email.utils import format_datetime

import httpx
import pytest

from jentic_one.registry.services.catalog.fetch import (
    CATALOG_USER_AGENT,
    MAX_RETRY_AFTER_SECONDS,
    parse_retry_after,
)
from jentic_one.registry.services.catalog.sources.deps import GuardedSourceHttpClient
from jentic_one.registry.services.catalog.sources.limits import ProcessSourceLimits
from jentic_one.registry.services.catalog.sources.protocol import (
    NotModified,
    SourceUnavailableError,
    SpecBody,
    SpecGoneError,
)
from jentic_one.shared.config import IngestConfig

_URL = "https://specs.example.com/openapi.json"


class _Auth:
    async def headers(self) -> Mapping[str, str]:
        return {"Authorization": "Bearer source-token"}


def _client(handler: Callable[[httpx.Request], httpx.Response]) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.MockTransport(handler), follow_redirects=False)


def _http(
    handler: Callable[[httpx.Request], httpx.Response],
    *,
    limits: ProcessSourceLimits | None = None,
) -> GuardedSourceHttpClient:
    client = _client(handler)
    return GuardedSourceHttpClient(
        "corp", IngestConfig(), limits=limits, auth=_Auth(), client=lambda: client
    )


# ── Retry-After ──────────────────────────────────────────────────────────────


def test_parse_retry_after_seconds_and_dates() -> None:
    now = datetime(2026, 1, 1, tzinfo=UTC)
    assert parse_retry_after("12") == 12.0
    assert parse_retry_after(format_datetime(now + timedelta(seconds=30)), now=now) == 30.0
    assert parse_retry_after("-5") == 0.0
    assert parse_retry_after("999999") == MAX_RETRY_AFTER_SECONDS
    for unusable in (None, "", "soon", "nan", "inf"):
        assert parse_retry_after(unusable) is None


# ── requests ─────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_sends_user_agent_and_auth_to_the_origin_only() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        if request.url.host == "specs.example.com":
            return httpx.Response(302, headers={"location": "https://cdn.example.org/x.json"})
        return httpx.Response(200, content=b"{}")

    await _http(handler).get_bytes(_URL)
    first, second = seen
    assert first.headers["user-agent"] == CATALOG_USER_AGENT
    assert first.headers["authorization"] == "Bearer source-token"
    assert second.headers["user-agent"] == CATALOG_USER_AGENT
    assert "authorization" not in second.headers  # a redirect off-origin drops the credential


@pytest.mark.asyncio
async def test_body_maps_to_spec_body_with_digest_and_validator() -> None:
    body = b'{"openapi": "3.1.0"}'

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=body, headers={"etag": '"v1"'})

    result = await _http(handler).get_bytes(_URL)
    assert isinstance(result, SpecBody)
    assert result.content == body
    assert result.digest == hashlib.sha256(body).hexdigest()
    assert result.validator == '"v1"'
    assert result.media_type == "application/json"


@pytest.mark.asyncio
async def test_yaml_body_is_labelled_yaml() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b"openapi: 3.1.0\n")

    result = await _http(handler).get_bytes(_URL)
    assert isinstance(result, SpecBody)
    assert result.media_type == "application/yaml"


@pytest.mark.asyncio
async def test_304_maps_to_not_modified() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["if-none-match"] == '"v1"'
        return httpx.Response(304, headers={"etag": '"v1"'})

    result = await _http(handler).get_bytes(_URL, validator='"v1"')
    assert result == NotModified(validator='"v1"')


@pytest.mark.parametrize("status", [404, 410])
@pytest.mark.asyncio
async def test_gone_statuses_map_to_spec_gone(status: int) -> None:
    with pytest.raises(SpecGoneError):
        await _http(lambda _r: httpx.Response(status)).get_bytes(_URL)


@pytest.mark.parametrize("status", [401, 403, 500, 502, 503])
@pytest.mark.asyncio
async def test_other_failures_map_to_source_unavailable(status: int) -> None:
    with pytest.raises(SourceUnavailableError):
        await _http(lambda _r: httpx.Response(status)).get_bytes(_URL)


@pytest.mark.asyncio
async def test_503_carries_its_retry_after() -> None:
    with pytest.raises(SourceUnavailableError) as raised:
        await _http(lambda _r: httpx.Response(503, headers={"retry-after": "9"})).get_bytes(_URL)
    assert raised.value.retry_after_s == 9.0


@pytest.mark.asyncio
async def test_429_blocks_the_source_for_its_retry_after() -> None:
    limits = ProcessSourceLimits("corp", concurrency=2, interactive_share=0.5)
    calls = 0

    def handler(_request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(429, headers={"retry-after": "30"})

    http = _http(handler, limits=limits)
    with pytest.raises(SourceUnavailableError) as raised:
        await http.get_json(_URL)
    assert raised.value.retry_after_s == 30.0
    # Blocked: the next request fails fast without reaching the source.
    with pytest.raises(SourceUnavailableError) as again:
        await http.get_bytes(_URL)
    assert calls == 1
    assert again.value.retry_after_s is not None
    assert 0 < again.value.retry_after_s <= 30.0


@pytest.mark.asyncio
async def test_transport_error_maps_to_source_unavailable() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused", request=request)

    with pytest.raises(SourceUnavailableError):
        await _http(handler).get_json(_URL)
