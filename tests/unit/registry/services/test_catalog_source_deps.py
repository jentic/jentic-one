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
from collections.abc import Awaitable, Callable, Mapping
from datetime import UTC, datetime, timedelta
from email.utils import format_datetime
from types import SimpleNamespace
from typing import Any

import httpx
import pytest

from jentic_one.registry.services.catalog.fetch import (
    CATALOG_USER_AGENT,
    MAX_RETRY_AFTER_SECONDS,
    parse_retry_after,
)
from jentic_one.registry.services.catalog.sources import deps as deps_module
from jentic_one.registry.services.catalog.sources.deps import (
    GuardedSourceHttpClient,
    build_source_deps,
    spec_fetch_lane,
)
from jentic_one.registry.services.catalog.sources.limits import ProcessSourceLimits
from jentic_one.registry.services.catalog.sources.protocol import (
    NotModified,
    SourceUnavailableError,
    SpecBody,
    SpecGoneError,
)
from jentic_one.shared.config import CatalogSourceConfig, IngestConfig

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


# ── build_source_deps: one pooled runtime per source ─────────────────────────


class _Backend:
    dialect_name = "sqlite"


class _Db:
    backend = _Backend()


class _Ctx:
    """The slice of ``Context`` that ``build_source_deps`` uses."""

    def __init__(self) -> None:
        self.config = SimpleNamespace(ingest=IngestConfig())
        self.registry_db = _Db()
        self._resources: dict[str, object] = {}
        self.shutdown: list[Callable[[], Awaitable[None]]] = []

    def resource(self, key: str, factory: Callable[[], object]) -> object:
        if key not in self._resources:
            self._resources[key] = factory()
        return self._resources[key]

    def on_shutdown(self, callback: Callable[[], Awaitable[None]]) -> None:
        self.shutdown.append(callback)


def _source(**kw: object) -> CatalogSourceConfig:
    return CatalogSourceConfig.model_validate(
        {"slug": "corp", "kind": "static", "settings": {}, **kw}
    )


@pytest.fixture()
def mock_transport(monkeypatch: pytest.MonkeyPatch) -> list[httpx.Request]:
    """Route the pooled client through a mock transport; returns the requests seen."""
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(429, headers={"retry-after": "20"})

    monkeypatch.setattr(
        deps_module, "build_pinned_transport", lambda _egress: httpx.MockTransport(handler)
    )
    return seen


@pytest.mark.asyncio
async def test_same_source_shares_one_pooled_client_and_limits(
    mock_transport: list[httpx.Request],
) -> None:
    ctx = _Ctx()
    first = build_source_deps(ctx, _source())  # type: ignore[arg-type]
    second = build_source_deps(ctx, _source())  # type: ignore[arg-type]
    assert first.limits is second.limits
    runtime = deps_module._runtimes(ctx).by_slug["corp"][1]  # type: ignore[arg-type]
    assert runtime.client() is runtime.client()
    # A change to the source's limits replaces its runtime (and its client).
    third = build_source_deps(ctx, _source(fetch_concurrency=2))  # type: ignore[arg-type]
    assert third.limits is not first.limits
    for callback in ctx.shutdown:
        await callback()


@pytest.mark.asyncio
async def test_429_through_source_deps_blocks_both_lanes(
    mock_transport: list[httpx.Request],
) -> None:
    """With a rate bucket, a 429 on a background listing blocks interactive spec fetches."""
    ctx = _Ctx()
    deps = build_source_deps(ctx, _source(requests_per_second=50.0, burst=4))  # type: ignore[arg-type]
    with pytest.raises(SourceUnavailableError) as raised:
        await deps.http.get_json(_URL)
    assert raised.value.retry_after_s == 20.0
    with pytest.raises(SourceUnavailableError) as again:
        await deps.http.get_bytes(_URL)
    assert len(mock_transport) == 1  # the interactive fetch never reached the source
    assert again.value.retry_after_s is not None
    assert 0 < again.value.retry_after_s <= 20.0
    for callback in ctx.shutdown:
        await callback()


class _LaneRecorder(ProcessSourceLimits):
    """Process limits that remember the lane of every slot taken."""

    def __init__(self) -> None:
        super().__init__("corp", concurrency=2, interactive_share=0.5)
        self.lanes: list[str] = []

    def slot(self, lane: Any) -> Any:
        self.lanes.append(lane)
        return super().slot(lane)


@pytest.mark.asyncio
async def test_spec_fetch_lane_defaults_to_interactive_and_follows_the_caller() -> None:
    """Preview/import fetch on ``interactive``; the update sweep sets ``background``."""
    limits = _LaneRecorder()
    http = _http(lambda _req: httpx.Response(200, content=b"{}"), limits=limits)
    await http.get_bytes(_URL)
    token = spec_fetch_lane.set("background")
    try:
        await http.get_bytes(_URL)
        await http.get_bytes(_URL, lane="interactive")
    finally:
        spec_fetch_lane.reset(token)
    assert limits.lanes == ["interactive", "background", "interactive"]
