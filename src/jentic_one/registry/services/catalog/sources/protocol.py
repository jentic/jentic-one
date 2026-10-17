"""The ``CatalogSource`` read seam: how the catalog lists entries and fetches specs.

A connector (a source *kind*) lists the entries a backend offers and fetches one
entry's spec on demand. The catalog never talks to a backend any other way, and
a connector never builds its own HTTP client: every outbound call goes through
the ``SourceDeps`` it was built with.

This interface is provisional: it may still change until a second production
connector (beyond ``git-manifest``) passes the ``SourceContract`` kit in
``jentic_one.testing.catalog_source``.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Mapping
from dataclasses import dataclass
from datetime import datetime
from enum import Flag, auto
from typing import TYPE_CHECKING, ClassVar, Literal, Protocol, runtime_checkable

from pydantic import BaseModel

if TYPE_CHECKING:
    from jentic_one.registry.services.catalog.sources.deps import SourceDeps
    from jentic_one.shared.config import CatalogSourceConfig

JsonScalar = str | int | float | bool | None


class SourceCapabilities(Flag):
    """Optional behaviours a source kind declares."""

    NONE = 0
    #: ``list_entries`` honours ``since`` and may return a partial (delta) listing.
    DELTA_LIST = auto()
    #: ``fetch_spec`` honours ``validator`` and may answer ``NotModified``.
    CONDITIONAL_FETCH = auto()
    #: The backend can signal that something changed.
    PUSH_NOTIFY = auto()


@dataclass(frozen=True, slots=True)
class SourceEntry:
    """One entry as a connector lists it (raw: the sync apply sanitises it)."""

    #: Stable within the source across syncs; may contain ``/``.
    external_id: str
    #: The connector's proposed ``catalog_api_id``; the resolver has the last word.
    catalog_api_id_hint: str | None
    title: str | None
    version: str | None
    #: Opaque, connector-owned locator passed back to ``fetch_spec``; ``None`` for a
    #: card-only entry with no fetchable spec.
    spec_ref: Mapping[str, JsonScalar] | None
    #: A human-facing page for the entry.
    human_url: str | None
    tags: tuple[str, ...] = ()
    #: A cheap change token from the listing, when the backend has one.
    content_hint: str | None = None


@dataclass(frozen=True, slots=True)
class Upserted:
    """The listing contains (or changed) this entry."""

    entry: SourceEntry


@dataclass(frozen=True, slots=True)
class Removed:
    """A delta listing reports this entry gone."""

    external_id: str


EntryChange = Upserted | Removed


@dataclass(frozen=True, slots=True)
class ListSummary:
    """What a listing reports once it has been fully iterated."""

    #: Opaque; persisted per source and passed back as ``since``.
    watermark: str
    #: Must equal the stream's ``is_full``.
    is_full: bool


class ListStream(Protocol):
    """One listing.

    ``is_full`` is declared before iteration, so the sync knows up front whether
    entries absent from the listing will be tombstoned; the watermark is only
    known after the iterator is exhausted.
    """

    is_full: bool

    def __aiter__(self) -> AsyncIterator[EntryChange]: ...

    async def summary(self) -> ListSummary:
        """Valid only once the iterator is exhausted; raises ``RuntimeError`` otherwise."""
        ...


@dataclass(frozen=True, slots=True)
class SpecBody:
    """A fetched spec."""

    content: bytes
    media_type: str
    validator: str | None
    #: SHA-256 hex of ``content``.
    digest: str


@dataclass(frozen=True, slots=True)
class NotModified:
    """The spec is unchanged since ``validator``."""

    validator: str


SpecFetch = SpecBody | NotModified


class SourceUnavailableError(Exception):
    """Transient: throttled, 5xx, timeout, or an auth refresh failed. Retry later.

    ``retry_after_s`` is how long the backend asked callers to wait, when it said.
    """

    def __init__(self, message: str = "", *, retry_after_s: float | None = None) -> None:
        super().__init__(message)
        self.retry_after_s = retry_after_s


class SpecGoneError(Exception):
    """Permanent: the backend says the spec no longer exists (404, 410, deleted)."""


@dataclass(frozen=True, slots=True)
class SourceHealth:
    """A source's reachability as of ``checked_at``."""

    ok: bool
    reason: Literal["auth_failed", "unreachable", "throttled", "misconfigured"] | None
    checked_at: datetime


@runtime_checkable
class CatalogSource(Protocol):
    """A read-only catalog backend. Nothing here mutates the backend."""

    kind: ClassVar[str]
    capabilities: ClassVar[SourceCapabilities]
    #: Validates the source's ``settings`` block at startup.
    config_model: ClassVar[type[BaseModel]]

    def list_entries(self, *, since: str | None) -> ListStream:
        """Start one listing. Never called on a request path."""
        ...

    async def fetch_spec(
        self, ref: Mapping[str, JsonScalar], *, validator: str | None = None
    ) -> SpecFetch:
        """Fetch one entry's spec by its ``spec_ref``."""
        ...

    async def health(self) -> SourceHealth:
        """Probe the backend."""
        ...


class CatalogSourceFactory(Protocol):
    """Builds a configured source. A source kind class is its own factory."""

    kind: ClassVar[str]
    capabilities: ClassVar[SourceCapabilities]
    config_model: ClassVar[type[BaseModel]]

    @classmethod
    def build(cls, cfg: CatalogSourceConfig, *, deps: SourceDeps) -> CatalogSource: ...
