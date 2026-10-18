"""Everything a catalog source connector is allowed to use for I/O.

A connector gets one ``SourceDeps`` when it is built and makes every outbound
call through ``deps.http``. The production client (:func:`build_source_deps`)
wraps the catalog's SSRF-guarded fetch helpers (``catalog/fetch.py``) with:

- one pooled ``httpx.AsyncClient`` per source per process, over the egress-pinned
  transport, closed when the context shuts down;
- the source's egress widening (``catalog.sources[].egress``) on top of
  ``ingest.egress``, for this source's client only;
- the source's auth headers, sent only when the request URL's origin is one the
  source's configuration names (``origins.credential_origins``: the kind's own
  configured URLs plus ``auth.origins``), never across a redirect to another
  host, and only when the connector asks for them. A URL a listing returns
  earns no credential, whatever the kind;
- ``CATALOG_USER_AGENT``;
- the source's fetch limits (``limits.py``): every request holds a slot on its
  lane, and a ``429`` blocks the source for its ``Retry-After``.

Failures are classified into the seam's two error types: ``404``/``410`` is
``SpecGoneError``, everything else ``SourceUnavailableError``.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Protocol

import httpx

from jentic_one.registry.services.catalog.fetch import (
    CATALOG_USER_AGENT,
    CatalogFetchError,
    fetch_bytes_conditional,
    fetch_json,
)
from jentic_one.registry.services.catalog.sources import metrics
from jentic_one.registry.services.catalog.sources.auth import (
    NoAuthResolver,
    SourceAuthResolver,
    build_source_auth,
)
from jentic_one.registry.services.catalog.sources.bucket import DbRateBucket
from jentic_one.registry.services.catalog.sources.limits import (
    FetchLane,
    MemoryRateBucket,
    ProcessSourceLimits,
    RateBucket,
)
from jentic_one.registry.services.catalog.sources.origins import (
    Origin,
    credential_origins,
    url_origin,
)
from jentic_one.registry.services.catalog.sources.protocol import (
    NotModified,
    SourceUnavailableError,
    SpecBody,
    SpecFetch,
    SpecGoneError,
)
from jentic_one.shared.config import CatalogSourceConfig, IngestConfig
from jentic_one.shared.context import Context
from jentic_one.shared.db.utils import utcnow
from jentic_one.shared.egress import build_pinned_transport

__all__ = [
    "CATALOG_USER_AGENT",
    "FetchLane",
    "GuardedSourceHttpClient",
    "SourceDeps",
    "SourceHttpClient",
    "SourceLimits",
    "UnboundedSourceLimits",
    "build_source_deps",
    "ensure_source_buckets",
    "source_ingest_config",
]

#: The lane a spec fetch takes when its connector names none. The update sweep
#: sets ``background`` so a bulk probe never drains the slots and token reserve
#: preview and import wait on.
spec_fetch_lane: ContextVar[FetchLane] = ContextVar(
    "catalog_spec_fetch_lane", default="interactive"
)

#: Upstream statuses that mean "this spec is permanently gone".
_GONE_STATUSES = frozenset({404, 410})


class SourceHttpClient(Protocol):
    """The only HTTP surface a connector may use.

    ``lane`` picks the fetch-limit lane: listings default to ``background``,
    spec fetches (preview, import, the update sweep) to ``interactive``.
    ``authenticate=False`` sends the request without the source's credentials
    (a connector passes it for a URL outside the origins it trusts with them).
    """

    async def get_json(
        self, url: str, *, lane: FetchLane = "background", authenticate: bool = True
    ) -> dict[str, Any]:
        """GET ``url`` and decode a JSON object."""
        ...

    async def get_bytes(
        self,
        url: str,
        *,
        validator: str | None = None,
        lane: FetchLane = "interactive",
        authenticate: bool = True,
    ) -> SpecFetch:
        """GET ``url``'s raw bytes, conditionally on ``validator`` (an ETag)."""
        ...


class SourceLimits(Protocol):
    """Per-source fetch limits."""

    def slot(self, lane: FetchLane) -> Any:
        """An async context manager holding one request's capacity on ``lane``."""
        ...

    async def block(self, seconds: float | None) -> float:
        """Block both lanes after a ``429``; returns how long."""
        ...


class UnboundedSourceLimits:
    """Limits that never wait (tests and fakes)."""

    @asynccontextmanager
    async def slot(self, lane: FetchLane) -> AsyncIterator[None]:
        del lane
        yield

    async def block(self, seconds: float | None) -> float:
        return seconds or 0.0


def _media_type(content: bytes) -> str:
    try:
        json.loads(content)
    except ValueError:
        return "application/yaml"
    return "application/json"


class GuardedSourceHttpClient:
    """``SourceHttpClient`` over the SSRF-guarded catalog fetch helpers."""

    def __init__(
        self,
        slug: str,
        ingest: IngestConfig,
        *,
        limits: SourceLimits | None = None,
        auth: SourceAuthResolver | None = None,
        client: Callable[[], httpx.AsyncClient | None] | None = None,
        user_agent: str = CATALOG_USER_AGENT,
        credential_origins: frozenset[Origin] = frozenset(),
    ) -> None:
        self._slug = slug
        self._ingest = ingest
        self._limits: SourceLimits = limits or UnboundedSourceLimits()
        self._auth: SourceAuthResolver = auth or NoAuthResolver()
        self._client = client or (lambda: None)
        self._user_agent = user_agent
        self._credential_origins = credential_origins

    async def _failed(self, exc: CatalogFetchError) -> Exception:
        if exc.status_code in _GONE_STATUSES:
            metrics.record_fetch(self._slug, metrics.FETCH_GONE)
            return SpecGoneError(str(exc))
        metrics.record_fetch(self._slug, metrics.FETCH_TRANSIENT)
        retry_after = exc.retry_after_s
        if exc.status_code == 429:
            retry_after = await self._limits.block(retry_after)
        return SourceUnavailableError(str(exc), retry_after_s=retry_after)

    async def _headers(self, url: str, authenticate: bool) -> dict[str, str]:
        """The auth headers for ``url``: none unless its origin is a configured one."""
        if not authenticate or url_origin(url) not in self._credential_origins:
            return {}
        return dict(await self._auth.headers())

    async def get_json(
        self, url: str, *, lane: FetchLane = "background", authenticate: bool = True
    ) -> dict[str, Any]:
        async with self._limits.slot(lane):
            headers = await self._headers(url, authenticate)
            try:
                doc = await fetch_json(
                    url,
                    config=self._ingest,
                    client=self._client(),
                    user_agent=self._user_agent,
                    headers=headers,
                )
            except CatalogFetchError as exc:
                raise await self._failed(exc) from exc
        metrics.record_fetch(self._slug, metrics.FETCH_BODY)
        return doc

    async def get_bytes(
        self,
        url: str,
        *,
        validator: str | None = None,
        lane: FetchLane = "interactive",
        authenticate: bool = True,
    ) -> SpecFetch:
        async with self._limits.slot(lane):
            headers = await self._headers(url, authenticate)
            try:
                result = await fetch_bytes_conditional(
                    url,
                    config=self._ingest,
                    etag=validator,
                    client=self._client(),
                    user_agent=self._user_agent,
                    headers=headers,
                )
            except CatalogFetchError as exc:
                raise await self._failed(exc) from exc
        if result.not_modified:
            metrics.record_fetch(self._slug, metrics.FETCH_NOT_MODIFIED)
            return NotModified(validator=result.etag or validator or "")
        metrics.record_fetch(self._slug, metrics.FETCH_BODY)
        content = result.content or b""
        return SpecBody(
            content=content,
            media_type=_media_type(content),
            validator=result.etag,
            digest=result.digest or hashlib.sha256(content).hexdigest(),
        )


@dataclass(frozen=True, slots=True)
class SourceDeps:
    """The I/O a connector is built with. Connectors never construct their own clients."""

    slug: str
    http: SourceHttpClient
    auth: SourceAuthResolver = field(default_factory=NoAuthResolver)
    limits: SourceLimits = field(default_factory=UnboundedSourceLimits)
    max_spec_bytes: int = 25 * 1024 * 1024
    user_agent: str = CATALOG_USER_AGENT
    clock: Callable[[], datetime] = utcnow


# ── per-process source runtime ────────────────────────────────────────────────


class _SourceRuntime:
    """The pooled client and limits one source shares across this process."""

    def __init__(self, ctx: Context, cfg: CatalogSourceConfig) -> None:
        ingest = source_ingest_config(ctx.config.ingest, cfg)
        self.ingest = ingest
        self._ingest = ingest
        self._concurrency = cfg.fetch_concurrency
        self.limits = ProcessSourceLimits(
            cfg.slug,
            concurrency=cfg.fetch_concurrency,
            interactive_share=cfg.interactive_share,
            bucket=_rate_bucket(ctx, cfg),
            max_wait_s=ingest.fetch_timeout_s,
        )
        self._client: httpx.AsyncClient | None = None
        self._loop: asyncio.AbstractEventLoop | None = None

    def client(self) -> httpx.AsyncClient:
        loop = asyncio.get_running_loop()
        if self._client is None or self._loop is not loop or self._client.is_closed:
            # A client is bound to the loop it first ran on; a new loop (tests,
            # a CLI command) gets its own, and the old one is closed on its own
            # loop when that loop is still running.
            self._discard_client()
            self._client = httpx.AsyncClient(
                timeout=self._ingest.fetch_timeout_s,
                follow_redirects=False,
                transport=build_pinned_transport(self._ingest.egress),
                limits=httpx.Limits(
                    max_connections=self._concurrency,
                    max_keepalive_connections=self._concurrency,
                ),
            )
            self._loop = loop
        return self._client

    def _discard_client(self) -> None:
        """Close the current client best effort, without awaiting it here."""
        client, loop = self._client, self._loop
        self._client = None
        self._loop = None
        if client is None or client.is_closed or loop is None or loop.is_closed():
            return
        if loop is _running_loop():
            task = loop.create_task(client.aclose())
            _PENDING_CLOSES.add(task)
            task.add_done_callback(_PENDING_CLOSES.discard)
        elif loop.is_running():
            asyncio.run_coroutine_threadsafe(client.aclose(), loop)

    async def close(self) -> None:
        if self._client is not None and self._loop is asyncio.get_running_loop():
            await self._client.aclose()
            self._client = None
            self._loop = None
        else:
            self._discard_client()


#: Closes scheduled by ``_discard_client``, kept referenced until they finish.
_PENDING_CLOSES: set[asyncio.Task[None]] = set()


def _running_loop() -> asyncio.AbstractEventLoop | None:
    try:
        return asyncio.get_running_loop()
    except RuntimeError:
        return None


def _rate_bucket(ctx: Context, cfg: CatalogSourceConfig) -> RateBucket | None:
    if cfg.requests_per_second is None:
        return None
    if ctx.registry_db.backend.dialect_name == "sqlite":
        return MemoryRateBucket(
            rate=cfg.requests_per_second,
            burst=cfg.burst,
            interactive_share=cfg.interactive_share,
        )
    return DbRateBucket(
        ctx,
        cfg.slug,
        rate=cfg.requests_per_second,
        burst=cfg.burst,
        interactive_share=cfg.interactive_share,
    )


_RuntimeKey = tuple[int, float | None, int, float, float, str]


def source_ingest_config(ingest: IngestConfig, cfg: CatalogSourceConfig) -> IngestConfig:
    """``ingest`` with ``cfg``'s egress widening applied (this source's requests only)."""
    widened = cfg.egress.widen(ingest.egress)
    if widened is ingest.egress:
        return ingest
    return ingest.model_copy(update={"egress": widened})


def _runtime_key(cfg: CatalogSourceConfig, ingest: IngestConfig) -> _RuntimeKey:
    """The settings a source's runtime is built from; a change rebuilds it."""
    return (
        cfg.fetch_concurrency,
        cfg.requests_per_second,
        cfg.burst,
        cfg.interactive_share,
        ingest.fetch_timeout_s,
        source_ingest_config(ingest, cfg).egress.model_dump_json(),
    )


class _SourceRuntimes:
    """One runtime per source slug, replaced when the source's limits change."""

    def __init__(self) -> None:
        self.by_slug: dict[str, tuple[_RuntimeKey, _SourceRuntime]] = {}

    async def close(self) -> None:
        for _key, runtime in self.by_slug.values():
            await runtime.close()
        self.by_slug.clear()


def _runtimes(ctx: Context) -> _SourceRuntimes:
    def _build() -> _SourceRuntimes:
        runtimes = _SourceRuntimes()
        ctx.on_shutdown(runtimes.close)
        return runtimes

    return ctx.resource("catalog.source_runtimes", _build)


def _runtime(ctx: Context, cfg: CatalogSourceConfig) -> _SourceRuntime:
    key = _runtime_key(cfg, ctx.config.ingest)
    runtimes = _runtimes(ctx)
    current = runtimes.by_slug.get(cfg.slug)
    if current is not None and current[0] == key:
        return current[1]
    if current is not None:
        current[1]._discard_client()
    runtime = _SourceRuntime(ctx, cfg)
    runtimes.by_slug[cfg.slug] = (key, runtime)
    return runtime


def build_source_deps(
    ctx: Context, cfg: CatalogSourceConfig, *, auth: SourceAuthResolver | None = None
) -> SourceDeps:
    """The production ``SourceDeps`` for one configured source.

    The pooled client and the limits are shared by every ``SourceDeps`` built
    for the same source in this process.
    """
    runtime = _runtime(ctx, cfg)
    resolver = auth or build_source_auth(cfg.auth)
    return SourceDeps(
        slug=cfg.slug,
        http=GuardedSourceHttpClient(
            cfg.slug,
            runtime.ingest,
            limits=runtime.limits,
            auth=resolver,
            client=runtime.client,
            credential_origins=credential_origins(cfg),
        ),
        auth=resolver,
        limits=runtime.limits,
        max_spec_bytes=ctx.config.ingest.max_spec_bytes,
    )


async def ensure_source_buckets(ctx: Context) -> None:
    """Create the rate-bucket row of every source with ``requests_per_second`` set.

    Run by the registry at startup, so a process whose database role may only
    update the bucket rows finds them in place.
    """
    if ctx.registry_db.backend.dialect_name == "sqlite":
        return
    for cfg in ctx.config.catalog.sources:
        if cfg.requests_per_second is None:
            continue
        bucket = _rate_bucket(ctx, cfg)
        if isinstance(bucket, DbRateBucket):
            await bucket.ensure()
