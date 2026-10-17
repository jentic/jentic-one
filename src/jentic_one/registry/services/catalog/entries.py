"""Catalog entries and their cross-source identity (``catalog_api_id``).

``CatalogEntry`` is the catalog's view of one source entry. Its ``entry_id``
(``{source_slug}/{external_id}``) keys it; its ``catalog_api_id`` names the API
across sources and is what the control plane (credentials, bindings) keys on.

``CatalogApiIdResolver`` turns a connector's hint into the stored
``catalog_api_id``:

- normalisation is exactly "strip surrounding whitespace, then the
  ``domain[/sub]`` grammar"; values are never case-folded or slugified, because
  credential scopes derive from the raw value;
- a ``curated`` source owns the ids it lists, checked by **vendor**: a hint from
  any other source whose slugified vendor equals a curated vendor is namespaced
  to ``{source_slug}~{hint}`` (so no existing vendor-wide credential covers it),
  unless a ``catalog.catalog_api_id_mappings`` row allows that exact entry;
- a missing or invalid hint (``~`` is reserved for the resolver, so a hint
  containing it is invalid), or a namespaced form whose slug still equals a
  curated vendor's or id's slug, becomes
  ``{source_slug}~{slugify_api_field(external_id)}``.

``exclude_unowned`` is the read-side fail-safe: it drops any non-curated entry
whose stored id is curated-owned and not allowed by a current mapping row, so a
stale stored id is never served under a name it no longer owns.
"""

from __future__ import annotations

import hashlib
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field

import structlog

from jentic_one.registry.services.catalog.sources.metrics import (
    record_mapping_unmatched,
    record_namespaced,
)
from jentic_one.shared.catalog.api_id import NAMESPACE_SEPARATOR, normalise_catalog_api_id
from jentic_one.shared.config import CatalogApiIdMappingConfig, CatalogSourceConfig
from jentic_one.shared.models.api_identity import slugify_api_field
from jentic_one.shared.vendor_domain import vendor_from_api_id

logger = structlog.get_logger(__name__)


@dataclass(frozen=True, slots=True)
class CatalogEntry:
    """One catalog entry as the catalog serves it."""

    #: ``{source_slug}/{external_id}``: the key for identity, membership and order.
    entry_id: str
    source_slug: str
    external_id: str
    #: Verbatim (trim + grammar only); may be ``{slug}~{hint}`` when namespaced.
    catalog_api_id: str
    #: Identity vendor passed to ingest (credentials match its slug): the id's
    #: vendor, or the whole id when namespaced.
    vendor: str | None
    #: For UI grouping only: the hint's vendor. Never read by the control plane.
    display_vendor: str | None
    title: str | None
    version: str | None
    tags: tuple[str, ...] = ()
    human_url: str | None = None
    #: ``False`` for a card-only entry (no ``spec_ref``).
    has_spec: bool = True
    content_hint: str | None = None


@dataclass(frozen=True, slots=True)
class CatalogApiIdMapping:
    """Lets one non-curated entry claim a curated ``catalog_api_id``."""

    source: str
    external_id: str
    catalog_api_id: str

    @classmethod
    def from_config(cls, row: CatalogApiIdMappingConfig) -> CatalogApiIdMapping:
        return cls(
            source=row.source, external_id=row.external_id, catalog_api_id=row.catalog_api_id
        )


def make_entry_id(source_slug: str, external_id: str) -> str:
    """The ``entry_id`` of ``external_id`` in ``source_slug``."""
    return f"{source_slug}/{external_id}"


def is_namespaced(catalog_api_id: str) -> bool:
    """Whether ``catalog_api_id`` is a ``{slug}~…`` namespaced id."""
    return NAMESPACE_SEPARATOR in catalog_api_id


def identity_vendor(catalog_api_id: str) -> str | None:
    """The identity vendor of a stored id (the whole id when namespaced)."""
    if is_namespaced(catalog_api_id):
        return catalog_api_id
    return vendor_from_api_id(catalog_api_id)


def build_catalog_entry(
    *,
    source_slug: str,
    external_id: str,
    catalog_api_id: str,
    hint: str | None,
    title: str | None,
    version: str | None,
    tags: Sequence[str] = (),
    human_url: str | None = None,
    has_spec: bool = True,
    content_hint: str | None = None,
) -> CatalogEntry:
    """A ``CatalogEntry`` with its vendors derived from the stored id and the hint."""
    normalised_hint = normalise_catalog_api_id(hint)
    return CatalogEntry(
        entry_id=make_entry_id(source_slug, external_id),
        source_slug=source_slug,
        external_id=external_id,
        catalog_api_id=catalog_api_id,
        vendor=identity_vendor(catalog_api_id),
        display_vendor=vendor_from_api_id(normalised_hint) if normalised_hint else None,
        title=title,
        version=version,
        tags=tuple(tags),
        human_url=human_url,
        has_spec=has_spec,
        content_hint=content_hint,
    )


def _vendor_slug(catalog_api_id: str) -> str:
    return slugify_api_field(vendor_from_api_id(catalog_api_id) or "")


def curated_vendors_of(curated_owned: Iterable[str]) -> frozenset[str]:
    """The slugified vendors of a set of curated-owned ids."""
    return frozenset(v for v in (_vendor_slug(i) for i in curated_owned) if v)


def _fallback_id(source_slug: str, external_id: str) -> str:
    slug = slugify_api_field(external_id)
    if not slug:
        slug = hashlib.sha256(external_id.encode()).hexdigest()[:16]
    return f"{source_slug}{NAMESPACE_SEPARATOR}{slug}"


@dataclass(frozen=True, slots=True)
class _MatchedMappings:
    allowed: Mapping[tuple[str, str], str]
    unmatched: tuple[CatalogApiIdMapping, ...] = field(default=())


def _match_mappings(
    mappings: Sequence[CatalogApiIdMapping], curated_owned: frozenset[str]
) -> _MatchedMappings:
    by_lower: dict[str, str] = {}
    for owned in sorted(curated_owned):
        by_lower.setdefault(owned.lower(), owned)
    allowed: dict[tuple[str, str], str] = {}
    unmatched: list[CatalogApiIdMapping] = []
    for row in mappings:
        normalised = normalise_catalog_api_id(row.catalog_api_id)
        curated = by_lower.get(normalised.lower()) if normalised else None
        if curated is None:
            unmatched.append(row)
            continue
        allowed[(row.source, row.external_id)] = curated
    return _MatchedMappings(allowed=allowed, unmatched=tuple(unmatched))


class CatalogApiIdResolver:
    """Resolves connector hints to stored ``catalog_api_id`` values."""

    def __init__(
        self,
        *,
        sources: Mapping[str, CatalogSourceConfig],
        mappings: Sequence[CatalogApiIdMapping],
        curated_owned: frozenset[str],
    ) -> None:
        self._sources = sources
        self._curated_owned = curated_owned
        self._curated_vendors = curated_vendors_of(curated_owned)
        self._curated_id_slugs = frozenset(slugify_api_field(i) for i in curated_owned)
        matched = _match_mappings(mappings, curated_owned)
        self._allowed = matched.allowed
        self.unmatched_mappings = matched.unmatched

    def report_unmatched(self) -> None:
        """Count and log every mapping row that matches no curated id (ignored rows)."""
        for row in self.unmatched_mappings:
            record_mapping_unmatched(row.source)
            logger.warning(
                "catalog_mapping_unmatched",
                source=row.source,
                external_id=row.external_id,
                catalog_api_id=row.catalog_api_id,
            )

    def _is_curated(self, source_slug: str) -> bool:
        cfg = self._sources.get(source_slug)
        return cfg is not None and cfg.enabled and cfg.trust == "curated"

    def is_curated_owned(self, catalog_api_id: str) -> bool:
        """Whether ``catalog_api_id`` claims a curated vendor."""
        return _vendor_slug(catalog_api_id) in self._curated_vendors

    def _collides(self, namespaced: str) -> bool:
        # A namespaced id's identity vendor is the whole id, so its vendor slug
        # and its id slug are the same form.
        return slugify_api_field(namespaced) in (self._curated_vendors | self._curated_id_slugs)

    def mapped(self, source_slug: str, external_id: str) -> str | None:
        """The curated id a mapping row allows for this entry, if any."""
        return self._allowed.get((source_slug, external_id))

    def resolve(self, *, source_slug: str, external_id: str, hint: str | None) -> str:
        """The ``catalog_api_id`` to store for one entry."""
        mapped = self.mapped(source_slug, external_id)
        if mapped is not None:
            return mapped
        normalised = normalise_catalog_api_id(hint)
        if normalised is None:
            return _fallback_id(source_slug, external_id)
        if self._is_curated(source_slug) or not self.is_curated_owned(normalised):
            return normalised
        namespaced = f"{source_slug}{NAMESPACE_SEPARATOR}{normalised}"
        if self._collides(namespaced):
            return _fallback_id(source_slug, external_id)
        record_namespaced(source_slug)
        return namespaced


def exclude_unowned(
    entries: Sequence[CatalogEntry],
    *,
    curated_sources: frozenset[str],
    curated_vendors: frozenset[str],
    mappings: Sequence[CatalogApiIdMapping],
) -> list[CatalogEntry]:
    """Drop non-curated entries whose stored id is curated-owned and not mapped.

    ``curated_sources`` names the sources whose entries are never dropped;
    ``curated_vendors`` is the slugified vendor set they own (see
    ``curated_vendors_of``). A mapping row allows an entry only when it names
    that exact ``(source, external_id)`` and its id equals the stored one
    (case-insensitively).
    """
    allowed = {
        (row.source, row.external_id): row.catalog_api_id.strip().lower() for row in mappings
    }
    kept: list[CatalogEntry] = []
    for entry in entries:
        if entry.source_slug in curated_sources:
            kept.append(entry)
            continue
        if _vendor_slug(entry.catalog_api_id) not in curated_vendors:
            kept.append(entry)
            continue
        if allowed.get((entry.source_slug, entry.external_id)) == entry.catalog_api_id.lower():
            kept.append(entry)
    return kept


def resolver_inputs_digest(
    sources: Sequence[CatalogSourceConfig], mappings: Sequence[CatalogApiIdMapping]
) -> str:
    """SHA-256 over the resolver's config inputs: mapping rows and each source's trust/enabled."""
    digest = hashlib.sha256()
    for source in sorted(sources, key=lambda s: s.slug):
        digest.update(f"s\0{source.slug}\0{source.trust}\0{int(source.enabled)}\n".encode())
    for row in sorted(mappings, key=lambda r: (r.source, r.external_id, r.catalog_api_id)):
        digest.update(f"m\0{row.source}\0{row.external_id}\0{row.catalog_api_id}\n".encode())
    return digest.hexdigest()
