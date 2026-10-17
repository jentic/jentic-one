"""Catalog sources: the ``CatalogSource`` seam, built-in kinds and per-source sync.

Importing this package registers the built-in ``git-manifest`` kind.
"""

from jentic_one.registry.services.catalog.sources import git_manifest as _git_manifest
from jentic_one.registry.services.catalog.sources.protocol import (
    CatalogSource,
    CatalogSourceFactory,
    EntryChange,
    JsonScalar,
    ListStream,
    ListSummary,
    NotModified,
    Removed,
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
    resolve_catalog_source_kind,
)

del _git_manifest

__all__ = [
    "CatalogSource",
    "CatalogSourceFactory",
    "EntryChange",
    "JsonScalar",
    "ListStream",
    "ListSummary",
    "NotModified",
    "Removed",
    "SourceCapabilities",
    "SourceEntry",
    "SourceHealth",
    "SourceUnavailableError",
    "SpecBody",
    "SpecFetch",
    "SpecGoneError",
    "Upserted",
    "register_catalog_source_kind",
    "resolve_catalog_source_kind",
]
