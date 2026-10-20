"""Everything a catalog source connector is allowed to use for I/O.

A connector gets one ``SourceDeps`` when it is built and makes every outbound
call through ``deps.http``. The default client is a thin wrapper over the
catalog's SSRF-guarded fetch helpers (``catalog/fetch.py``): it shares their
redirect, size and egress guards and classifies failures into the seam's two
error types. Every request carries ``CATALOG_USER_AGENT`` (the fetch helpers
set it) and no auth headers: startup refuses a source whose ``auth.type`` is not
``none``. ``limits`` is the per-source concurrency gate; the default never
blocks.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Literal, Protocol

from jentic_one.registry.services.catalog.fetch import (
    CATALOG_USER_AGENT,
    CatalogFetchError,
    fetch_bytes_conditional,
    fetch_json,
)
from jentic_one.registry.services.catalog.sources.auth import NoAuthResolver, SourceAuthResolver
from jentic_one.registry.services.catalog.sources.protocol import (
    NotModified,
    SourceUnavailableError,
    SpecBody,
    SpecFetch,
    SpecGoneError,
)
from jentic_one.shared.config import IngestConfig
from jentic_one.shared.db.utils import utcnow

#: Upstream statuses that mean "this spec is permanently gone".
_GONE_STATUSES = frozenset({404, 410})

FetchLane = Literal["interactive", "background"]


class SourceHttpClient(Protocol):
    """The only HTTP surface a connector may use."""

    async def get_json(self, url: str) -> dict[str, Any]:
        """GET ``url`` and decode a JSON object."""
        ...

    async def get_bytes(self, url: str, *, validator: str | None = None) -> SpecFetch:
        """GET ``url``'s raw bytes, conditionally on ``validator`` (an ETag)."""
        ...


class SourceLimits(Protocol):
    """Per-source fetch limits; ``slot`` holds one unit of capacity."""

    def slot(self, lane: FetchLane) -> Any: ...


class UnboundedSourceLimits:
    """Limits that never block."""

    @asynccontextmanager
    async def slot(self, lane: FetchLane) -> AsyncIterator[None]:
        del lane
        yield


def _classify(exc: CatalogFetchError) -> Exception:
    if exc.status_code in _GONE_STATUSES:
        return SpecGoneError(str(exc))
    return SourceUnavailableError(str(exc))


class GuardedSourceHttpClient:
    """``SourceHttpClient`` over the SSRF-guarded catalog fetch helpers.

    A 404/410 answer becomes ``SpecGoneError``; every other failure (5xx, 429,
    timeout, guard rejection, unparsable body) becomes ``SourceUnavailableError``.
    """

    def __init__(self, ingest: IngestConfig) -> None:
        self._ingest = ingest

    async def get_json(self, url: str) -> dict[str, Any]:
        try:
            return await fetch_json(url, config=self._ingest)
        except CatalogFetchError as exc:
            raise _classify(exc) from exc

    async def get_bytes(self, url: str, *, validator: str | None = None) -> SpecFetch:
        try:
            result = await fetch_bytes_conditional(url, config=self._ingest, etag=validator)
        except CatalogFetchError as exc:
            raise _classify(exc) from exc
        if result.not_modified:
            return NotModified(validator=result.etag or validator or "")
        content = result.content or b""
        return SpecBody(
            content=content,
            media_type=_media_type(content),
            validator=result.etag,
            digest=result.digest or hashlib.sha256(content).hexdigest(),
        )


def _media_type(content: bytes) -> str:
    try:
        json.loads(content)
    except ValueError:
        return "application/yaml"
    return "application/json"


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


def build_source_deps(
    slug: str, *, ingest: IngestConfig, auth: SourceAuthResolver | None = None
) -> SourceDeps:
    """The production ``SourceDeps`` for one source."""
    return SourceDeps(
        slug=slug,
        http=GuardedSourceHttpClient(ingest),
        auth=auth or NoAuthResolver(),
        max_spec_bytes=ingest.max_spec_bytes,
    )
