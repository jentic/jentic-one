"""Contract kit and test doubles for catalog source kinds.

- ``SourceContract``: a base class whose ``test_*`` methods prove a
  ``CatalogSource`` kind honours the seam: ``is_full`` is declared before
  iteration, ``summary()`` is only valid after exhaustion, ``NotModified`` is
  honoured for a kind that declares conditional fetch, ``SpecGoneError`` and
  ``SourceUnavailableError`` reach the caller unchanged, and no HTTP client is
  built outside ``SourceDeps``. Subclass it as ``Test<YourKind>`` in your suite.
- ``FakeSourceHttp``: an in-memory ``SourceHttpClient`` the kit hands the source.
- The test-only ``static`` kind, which lists entries from its own ``settings``.
  It is registered only by an explicit ``register_static_source_kind()`` call,
  never at import and never by production wiring.
- ``synthetic_catalog(n, sources)``: deterministic ``static`` source configs.

The ``CatalogSource`` interface, and therefore this kit, is provisional and may
still change.
"""

from __future__ import annotations

import hashlib
import inspect
import json
from collections.abc import Callable, Iterator, Mapping, Sequence
from contextlib import contextmanager
from typing import Any, ClassVar
from unittest import mock

import httpx
from pydantic import BaseModel, ConfigDict, Field

from jentic_one.registry.services.catalog.sources.deps import FetchLane, SourceDeps
from jentic_one.registry.services.catalog.sources.protocol import (
    CatalogSource,
    EntryChange,
    JsonScalar,
    ListStream,
    NotModified,
    SourceCapabilities,
    SourceEntry,
    SourceHealth,
    SourceUnavailableError,
    SpecBody,
    SpecFetch,
    SpecGoneError,
    Upserted,
)
from jentic_one.registry.services.catalog.sources.registry import (
    register_catalog_source_kind,
    registered_catalog_source_kinds,
)
from jentic_one.registry.services.catalog.sources.streams import FullListStream
from jentic_one.shared.config import CatalogSourceConfig

STATIC_SOURCE_KIND = "static"


def _digest(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


class FakeSourceHttp:
    """In-memory ``SourceHttpClient``: canned JSON and byte responses per URL.

    ``get_bytes`` answers ``NotModified`` when the caller's validator equals the
    content's SHA-256. Set ``fail_with`` to make every request raise it. Each
    requested URL is appended to ``requests``.
    """

    def __init__(
        self,
        *,
        json_responses: Mapping[str, dict[str, Any]] | None = None,
        bytes_responses: Mapping[str, bytes] | None = None,
    ) -> None:
        self.json_responses: dict[str, dict[str, Any]] = dict(json_responses or {})
        self.bytes_responses: dict[str, bytes] = dict(bytes_responses or {})
        self.fail_with: Exception | None = None
        self.requests: list[str] = []

    def _check(self, url: str) -> None:
        self.requests.append(url)
        if self.fail_with is not None:
            raise self.fail_with

    async def get_json(self, url: str, *, lane: FetchLane = "background") -> dict[str, Any]:
        del lane
        self._check(url)
        if url not in self.json_responses:
            raise SpecGoneError(f"no canned JSON for {url}")
        return self.json_responses[url]

    async def get_bytes(
        self, url: str, *, validator: str | None = None, lane: FetchLane | None = None
    ) -> SpecFetch:
        del lane
        self._check(url)
        if url not in self.bytes_responses:
            raise SpecGoneError(f"no canned bytes for {url}")
        content = self.bytes_responses[url]
        digest = _digest(content)
        if validator is not None and validator == digest:
            return NotModified(validator=validator)
        return SpecBody(
            content=content, media_type="application/json", validator=digest, digest=digest
        )


def fake_source_deps(slug: str, http: FakeSourceHttp | None = None) -> SourceDeps:
    """``SourceDeps`` for a test, backed by ``http`` (an empty ``FakeSourceHttp`` by default)."""
    return SourceDeps(slug=slug, http=http or FakeSourceHttp())


# ── the test-only ``static`` kind ─────────────────────────────────────────────


class StaticEntry(BaseModel):
    """One entry of a ``static`` source; ``spec`` is the inline spec body."""

    model_config = ConfigDict(extra="forbid")

    external_id: str
    catalog_api_id_hint: str | None = None
    title: str | None = None
    version: str | None = None
    human_url: str | None = None
    tags: list[str] = Field(default_factory=list)
    content_hint: str | None = None
    spec: str | None = None


class StaticSettings(BaseModel):
    """``settings`` of a ``static`` source."""

    model_config = ConfigDict(extra="forbid")

    entries: list[StaticEntry] = Field(default_factory=list)


class StaticSource:
    """Lists the entries in its ``settings``; serves each inline ``spec``. Test-only."""

    kind: ClassVar[str] = STATIC_SOURCE_KIND
    capabilities: ClassVar[SourceCapabilities] = SourceCapabilities.CONDITIONAL_FETCH
    config_model: ClassVar[type[BaseModel]] = StaticSettings

    def __init__(self, settings: StaticSettings, *, deps: SourceDeps) -> None:
        self._settings = settings
        self._deps = deps
        self._by_id = {e.external_id: e for e in settings.entries}

    @classmethod
    def build(cls, cfg: CatalogSourceConfig, *, deps: SourceDeps) -> StaticSource:
        return cls(StaticSettings.model_validate(cfg.settings), deps=deps)

    def list_entries(self, *, since: str | None) -> ListStream:
        del since
        entries = list(self._settings.entries)

        async def _load() -> list[EntryChange]:
            return [
                Upserted(
                    SourceEntry(
                        external_id=e.external_id,
                        catalog_api_id_hint=e.catalog_api_id_hint,
                        title=e.title,
                        version=e.version,
                        spec_ref={"external_id": e.external_id} if e.spec is not None else None,
                        human_url=e.human_url,
                        tags=tuple(e.tags),
                        content_hint=e.content_hint,
                    )
                )
                for e in entries
            ]

        def _watermark(changes: Sequence[EntryChange]) -> str:
            return hashlib.sha256(
                "\n".join(c.entry.external_id for c in changes if isinstance(c, Upserted)).encode()
            ).hexdigest()

        return FullListStream(_load, watermark=_watermark)

    async def fetch_spec(
        self, ref: Mapping[str, JsonScalar], *, validator: str | None = None
    ) -> SpecFetch:
        entry = self._by_id.get(str(ref.get("external_id")))
        if entry is None or entry.spec is None:
            raise SpecGoneError(f"static entry {ref.get('external_id')!r} has no spec")
        content = entry.spec.encode()
        digest = _digest(content)
        if validator is not None and validator == digest:
            return NotModified(validator=validator)
        return SpecBody(
            content=content, media_type="application/json", validator=digest, digest=digest
        )

    async def health(self) -> SourceHealth:
        return SourceHealth(ok=True, reason=None, checked_at=self._deps.clock())


def register_static_source_kind() -> None:
    """Register the test-only ``static`` kind (idempotent)."""
    if STATIC_SOURCE_KIND in registered_catalog_source_kinds():
        return
    register_catalog_source_kind(StaticSource)


def static_source_config(
    slug: str, entries: Sequence[StaticEntry | Mapping[str, Any]], **fields: Any
) -> CatalogSourceConfig:
    """A ``static`` ``CatalogSourceConfig``; ``fields`` sets trust, precedence, etc."""
    rows = [e.model_dump() if isinstance(e, StaticEntry) else dict(e) for e in entries]
    return CatalogSourceConfig(
        slug=slug, kind=STATIC_SOURCE_KIND, settings={"entries": rows}, **fields
    )


def synthetic_catalog(n: int, sources: Sequence[str]) -> list[CatalogSourceConfig]:
    """``n`` deterministic entries spread round-robin over ``static`` sources named ``sources``.

    Entry ``i`` is ``api-{i}`` with hint ``api{i}.example.com`` and a tiny inline
    spec, so the same arguments always yield the same catalog.
    """
    if not sources:
        raise ValueError("synthetic_catalog needs at least one source slug")
    buckets: dict[str, list[StaticEntry]] = {slug: [] for slug in sources}
    for i in range(n):
        slug = sources[i % len(sources)]
        buckets[slug].append(
            StaticEntry(
                external_id=f"api-{i}",
                catalog_api_id_hint=f"api{i}.example.com",
                title=f"Synthetic API {i}",
                version="1.0.0",
                human_url=f"https://example.com/apis/{i}",
                tags=["synthetic"],
                content_hint=f"v{i}",
                spec=json.dumps(
                    {
                        "openapi": "3.1.0",
                        "info": {"title": f"Synthetic API {i}", "version": "1.0.0"},
                        "paths": {},
                    }
                ),
            )
        )
    return [static_source_config(slug, entries) for slug, entries in buckets.items()]


# ── the contract kit ──────────────────────────────────────────────────────────


@contextmanager
def _no_client_construction() -> Iterator[None]:
    def _refuse(*args: Any, **kwargs: Any) -> None:
        raise AssertionError("a catalog source built its own HTTP client; use SourceDeps.http")

    with (
        mock.patch.object(httpx.AsyncClient, "__init__", _refuse),
        mock.patch.object(httpx.Client, "__init__", _refuse),
    ):
        yield


class SourceContract:
    """Contract tests for a ``CatalogSource`` kind.

    Subclass as ``Test<YourKind>`` and implement ``build_source``; override
    ``seed_http`` to load ``FakeSourceHttp`` with the responses your source
    requests. The listing must yield at least one entry with a ``spec_ref``.
    """

    #: The slug the source under test is built with.
    slug: ClassVar[str] = "contract"

    def build_source(self, deps: SourceDeps) -> CatalogSource:
        raise NotImplementedError("SourceContract subclasses implement build_source")

    def seed_http(self, http: FakeSourceHttp) -> None:
        """Load the canned responses the source needs (none by default)."""

    def _make(self) -> tuple[CatalogSource, FakeSourceHttp]:
        http = FakeSourceHttp()
        self.seed_http(http)
        return self.build_source(fake_source_deps(self.slug, http)), http

    async def _drain(self, stream: ListStream) -> list[EntryChange]:
        return [change async for change in stream]

    async def _spec_ref(self, source: CatalogSource) -> Mapping[str, JsonScalar]:
        for change in await self._drain(source.list_entries(since=None)):
            if isinstance(change, Upserted) and change.entry.spec_ref is not None:
                return change.entry.spec_ref
        raise AssertionError("the listing yielded no entry with a spec_ref")

    def test_is_a_catalog_source(self) -> None:
        source, _ = self._make()
        assert isinstance(source, CatalogSource)
        assert isinstance(type(source).kind, str) and type(source).kind
        assert isinstance(type(source).capabilities, SourceCapabilities)
        assert issubclass(type(source).config_model, BaseModel)
        assert inspect.iscoroutinefunction(type(source).fetch_spec)
        assert inspect.iscoroutinefunction(type(source).health)

    async def test_is_full_declared_before_iteration(self) -> None:
        source, _ = self._make()
        stream = source.list_entries(since=None)
        assert isinstance(stream.is_full, bool)
        declared = stream.is_full
        await self._drain(stream)
        assert stream.is_full == declared

    async def test_summary_only_after_exhaustion(self) -> None:
        source, _ = self._make()
        stream = source.list_entries(since=None)
        try:
            await stream.summary()
        except RuntimeError:
            pass
        else:
            raise AssertionError("summary() must raise RuntimeError before the stream is drained")
        await self._drain(stream)
        summary = await stream.summary()
        assert isinstance(summary.watermark, str)
        assert summary.is_full == stream.is_full

    async def test_listing_yields_entry_changes(self) -> None:
        source, _ = self._make()
        changes = await self._drain(source.list_entries(since=None))
        assert changes, "the contract listing must yield at least one change"
        for change in changes:
            if isinstance(change, Upserted):
                assert isinstance(change.entry.external_id, str) and change.entry.external_id

    async def test_not_modified_honoured(self) -> None:
        source, _ = self._make()
        if SourceCapabilities.CONDITIONAL_FETCH not in type(source).capabilities:
            return
        ref = await self._spec_ref(source)
        first = await source.fetch_spec(ref)
        assert isinstance(first, SpecBody)
        assert first.digest == _digest(first.content)
        assert first.validator is not None
        again = await source.fetch_spec(ref, validator=first.validator)
        assert isinstance(again, NotModified)

    async def test_spec_gone_reaches_the_caller(self) -> None:
        source, http = self._make()
        ref = await self._spec_ref(source)
        http.fail_with = SpecGoneError("gone")
        try:
            await source.fetch_spec(ref)
        except SpecGoneError:
            return
        if not http.requests:
            return  # serves specs without HTTP; nothing to classify
        raise AssertionError("fetch_spec swallowed or reclassified SpecGoneError")

    async def test_unavailable_reaches_the_caller(self) -> None:
        source, http = self._make()
        ref = await self._spec_ref(source)
        before = len(http.requests)
        http.fail_with = SourceUnavailableError("throttled")
        try:
            await source.fetch_spec(ref)
        except SourceUnavailableError:
            return
        if len(http.requests) == before:
            return  # serves specs without HTTP; nothing to classify
        raise AssertionError("fetch_spec swallowed or reclassified SourceUnavailableError")

    async def test_no_client_built_outside_deps(self) -> None:
        source, _ = self._make()
        with _no_client_construction():
            ref = await self._spec_ref(source)
            await source.fetch_spec(ref)
            await source.health()


#: Every contract check, by name, for runners that cannot use class collection.
CONTRACT_CHECKS: tuple[str, ...] = tuple(
    name for name in vars(SourceContract) if name.startswith("test_")
)


def contract_checks(contract: SourceContract) -> list[tuple[str, Callable[[], Any]]]:
    """``(name, bound check)`` pairs of ``contract``, for parametrised plain-function tests."""
    return [(name, getattr(contract, name)) for name in CONTRACT_CHECKS]


__all__ = [
    "CONTRACT_CHECKS",
    "STATIC_SOURCE_KIND",
    "FakeSourceHttp",
    "SourceContract",
    "StaticEntry",
    "StaticSettings",
    "StaticSource",
    "contract_checks",
    "fake_source_deps",
    "register_static_source_kind",
    "static_source_config",
    "synthetic_catalog",
]
