"""The ``git-manifest`` source kind: a curated ``apis.json`` index (public-apis).

The listing fetches the index through ``SourceDeps.http`` and parses it with the
catalog's ``parse_apis_json``, so every entry's ``external_id`` and
``catalog_api_id_hint`` is exactly the ``api_id`` the snapshot catalog serves,
its ``spec_ref`` is ``{"url": <spec url>}`` and its ``human_url`` is the GitHub
tree page. The listing is always full; the kind has no delta listing.
"""

from __future__ import annotations

import hashlib
from collections.abc import Mapping, Sequence
from typing import Any, ClassVar

from pydantic import BaseModel, ConfigDict

from jentic_one.registry.services.catalog import manifest_builder as mb
from jentic_one.registry.services.catalog.sources.deps import SourceDeps
from jentic_one.registry.services.catalog.sources.protocol import (
    EntryChange,
    JsonScalar,
    ListStream,
    SourceCapabilities,
    SourceEntry,
    SourceHealth,
    SourceUnavailableError,
    SpecFetch,
    SpecGoneError,
    Upserted,
)
from jentic_one.registry.services.catalog.sources.registry import register_catalog_source_kind
from jentic_one.registry.services.catalog.sources.streams import FullListStream
from jentic_one.shared.config import CatalogSourceConfig


class GitManifestSettings(BaseModel):
    """``settings`` of a ``git-manifest`` source."""

    model_config = ConfigDict(extra="forbid")

    #: The ``apis.json`` index URL.
    url: str


def manifest_entry_to_source_entry(entry: mb.ManifestEntry) -> SourceEntry:
    """The ``SourceEntry`` a parsed manifest entry lists as."""
    return SourceEntry(
        external_id=entry.api_id,
        catalog_api_id_hint=entry.api_id,
        title=None,
        version=None,
        spec_ref={"url": entry.spec_url} if entry.spec_url else None,
        human_url=entry.github_url or None,
    )


def manifest_changes(doc: dict[str, Any]) -> list[EntryChange]:
    """Every entry of an ``apis.json`` document, as listing changes."""
    return [Upserted(manifest_entry_to_source_entry(e)) for e in mb.parse_apis_json(doc)]


def changes_watermark(changes: Sequence[EntryChange]) -> str:
    """A content digest of a full listing (the kind keeps no delta cursor)."""
    digest = hashlib.sha256()
    for change in changes:
        if isinstance(change, Upserted):
            digest.update(change.entry.external_id.encode())
            digest.update(b"\0")
    return digest.hexdigest()


def manifest_stream(doc: dict[str, Any]) -> FullListStream:
    """A full listing over an already fetched ``apis.json`` document."""
    changes = manifest_changes(doc)

    async def _load() -> list[EntryChange]:
        return changes

    return FullListStream(_load, watermark=changes_watermark)


@register_catalog_source_kind
class GitManifestSource:
    """Lists a curated ``apis.json`` index; fetches specs by URL."""

    kind: ClassVar[str] = "git-manifest"
    capabilities: ClassVar[SourceCapabilities] = SourceCapabilities.CONDITIONAL_FETCH
    config_model: ClassVar[type[BaseModel]] = GitManifestSettings

    def __init__(self, settings: GitManifestSettings, *, deps: SourceDeps) -> None:
        self._settings = settings
        self._deps = deps

    @classmethod
    def build(cls, cfg: CatalogSourceConfig, *, deps: SourceDeps) -> GitManifestSource:
        return cls(GitManifestSettings.model_validate(cfg.settings), deps=deps)

    def list_entries(self, *, since: str | None) -> ListStream:
        del since  # full listings only

        async def _load() -> list[EntryChange]:
            doc = await self._deps.http.get_json(self._settings.url)
            return manifest_changes(doc)

        return FullListStream(_load, watermark=changes_watermark)

    async def fetch_spec(
        self, ref: Mapping[str, JsonScalar], *, validator: str | None = None
    ) -> SpecFetch:
        url = ref.get("url")
        if not isinstance(url, str) or not url:
            raise SpecGoneError("git-manifest spec_ref carries no url")
        return await self._deps.http.get_bytes(url, validator=validator)

    async def health(self) -> SourceHealth:
        try:
            await self._deps.http.get_json(self._settings.url)
        except SourceUnavailableError:
            return SourceHealth(ok=False, reason="unreachable", checked_at=self._deps.clock())
        except SpecGoneError:
            return SourceHealth(ok=False, reason="misconfigured", checked_at=self._deps.clock())
        return SourceHealth(ok=True, reason=None, checked_at=self._deps.clock())
