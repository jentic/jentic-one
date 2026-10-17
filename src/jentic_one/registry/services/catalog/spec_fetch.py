"""Fetching an entry's spec through the catalog source that lists it.

Preview, import and the update-notify sweep all fetch this way: look the entry
up, build its source (``CatalogSourceSync.build_source``, so the pooled client,
auth and fetch limits apply) and call ``fetch_spec`` with the entry's
``spec_ref``. ``CatalogRefResolver`` adapts it for the import job, where a
``CatalogRefSource`` carries only the entry id.
"""

from __future__ import annotations

from collections.abc import Mapping

from jentic_one.registry.core.schema.catalog_entries import CatalogEntryRow
from jentic_one.registry.ingest.exc import IngestStageError
from jentic_one.registry.ingest.fetch import CatalogRefSource, CatalogSpec
from jentic_one.registry.repos.catalog_entry_repo import CatalogEntryRepository
from jentic_one.registry.services.catalog.snapshot import spec_url_of
from jentic_one.registry.services.catalog.sources.protocol import (
    CatalogSource,
    NotModified,
    SourceUnavailableError,
    SpecBody,
    SpecFetch,
    SpecGoneError,
)
from jentic_one.registry.services.catalog.sources.sync import CatalogSourceSync
from jentic_one.registry.services.errors import (
    CatalogEntryNotFoundError,
    CatalogSourceUnavailableError,
    CatalogSpecGoneError,
)
from jentic_one.shared.context import Context


class CatalogSpecFetcher:
    """Fetches entry specs through their sources; one source instance per slug."""

    def __init__(self, ctx: Context, *, sync: CatalogSourceSync | None = None) -> None:
        self._ctx = ctx
        self._sync = sync or CatalogSourceSync(ctx)
        self._built: dict[str, CatalogSource] = {}

    def _source(self, slug: str) -> CatalogSource | None:
        cfg = self._sync.sources.get(slug)
        if cfg is None or not cfg.enabled:
            return None
        source = self._built.get(slug)
        if source is None:
            source = self._sync.build_source(cfg)
            self._built[slug] = source
        return source

    async def live_entry(self, entry_id: str) -> CatalogEntryRow | None:
        """The live row ``entry_id`` of an enabled source, if any."""
        async with self._ctx.registry_db.session() as session:
            row = await CatalogEntryRepository.live_entry(session, entry_id)
        if row is None or self._source(row.source_slug) is None:
            return None
        return row

    async def fetch_row(self, row: CatalogEntryRow, *, validator: str | None = None) -> SpecFetch:
        """``fetch_spec`` for ``row``; the source's own errors propagate.

        A row without a ``spec_ref`` (a card-only entry) raises ``SpecGoneError``.
        """
        source = self._source(row.source_slug)
        if source is None:
            raise SourceUnavailableError(f"catalog source {row.source_slug!r} is not enabled")
        ref = row.spec_ref
        if not isinstance(ref, Mapping):
            raise SpecGoneError(f"catalog entry {row.entry_id!r} lists no spec")
        return await source.fetch_spec(ref, validator=validator)

    async def fetch_body(self, entry_id: str) -> tuple[CatalogEntryRow, SpecBody]:
        """The spec body of a live entry, with service errors for every failure.

        Raises ``CatalogEntryNotFoundError`` (no such live entry),
        ``CatalogSpecGoneError`` (the source says the spec is gone, or the entry
        lists none) or ``CatalogSourceUnavailableError`` (transient).
        """
        row = await self.live_entry(entry_id)
        if row is None:
            raise CatalogEntryNotFoundError(entry_id)
        try:
            result = await self.fetch_row(row)
        except SpecGoneError as exc:
            raise CatalogSpecGoneError(entry_id) from exc
        except SourceUnavailableError as exc:
            raise CatalogSourceUnavailableError(entry_id, retry_after_s=exc.retry_after_s) from exc
        if isinstance(result, NotModified):
            # No validator was sent, so a conforming source never answers this.
            raise CatalogSourceUnavailableError(entry_id)
        return row, result


class CatalogRefResolver:
    """The import job's ``CatalogSpecResolver``: fetches through the entry's source."""

    def __init__(self, ctx: Context) -> None:
        self._fetcher = CatalogSpecFetcher(ctx)

    async def resolve(self, source: CatalogRefSource) -> CatalogSpec:
        entry_id = source.catalog_entry_id
        try:
            row, body = await self._fetcher.fetch_body(entry_id)
        except CatalogEntryNotFoundError as exc:
            raise IngestStageError(
                f"catalog entry {entry_id!r} is no longer listed by an enabled source"
            ) from exc
        except CatalogSpecGoneError as exc:
            raise IngestStageError(str(exc)) from exc
        except CatalogSourceUnavailableError as exc:
            raise IngestStageError(str(exc)) from exc
        return CatalogSpec(
            content=body.content,
            digest=body.digest,
            media_type=body.media_type,
            source_url=spec_url_of(row.spec_ref),
        )
