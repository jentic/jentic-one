"""The process-local catalog snapshot, source visibility and the per-request collapse.

``CatalogSnapshot`` holds the live ``catalog_entries`` rows of the enabled
sources as ``CatalogEntry`` values, loaded once per ``catalog_version`` (plus the
resolver's config inputs) and shared read-only by every request of the process.
Each load runs ``exclude_unowned``, so a non-curated entry whose stored id a
curated source owns is never served.

Visibility and the collapse are computed per request, never cached into the
snapshot's identity: ``allowed_sources`` turns the caller's effective
permissions and actor id into the set of sources they may read, and
``collapse`` keeps one entry per ``catalog_api_id`` among the visible ones,
choosing registered first, then the higher source ``precedence``, then the
lower ``entry_id``.
"""

from __future__ import annotations

import re
import weakref
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from jentic_one.registry.services.catalog.entries import (
    CatalogApiIdMapping,
    CatalogEntry,
    build_catalog_entry,
    curated_vendors_of,
    exclude_unowned,
)
from jentic_one.shared.auth.permission_catalog import (
    CATALOG_ORG_READ,
    CATALOG_RESTRICTED_READ,
    ORG_ADMIN,
)
from jentic_one.shared.config import CatalogSourceConfig

#: Collapsed views kept per snapshot (keyed by visibility and registered set).
_COLLAPSE_CACHE_SIZE = 32


def name_tokens(catalog_api_id: str) -> tuple[str, ...]:
    """The token scorer's tokens of a ``catalog_api_id``."""
    return tuple(re.findall(r"[a-z0-9]+", catalog_api_id.lower()))


def spec_url_of(spec_ref: Any) -> str | None:
    """The fetchable URL a ``spec_ref`` carries, if any (``git-manifest`` refs do)."""
    if isinstance(spec_ref, Mapping):
        url = spec_ref.get("url")
        if isinstance(url, str) and url:
            return url
    return None


@dataclass(frozen=True, slots=True)
class CollapsedView:
    """One caller's view of the snapshot: visible entries, collapsed unless asked not to."""

    #: The kept entries in ``entry_id`` order.
    entries: tuple[CatalogEntry, ...]
    #: Winner ``entry_id`` -> the visible entries collapsed into it, in ``entry_id`` order.
    alternates: Mapping[str, tuple[str, ...]]
    #: Every visible collapsed-out ``entry_id`` -> its winner.
    winner_of: Mapping[str, str]
    #: Visible ``catalog_api_id`` -> its winner's ``entry_id``.
    winner_by_api_id: Mapping[str, str]
    #: The ``entry_id``s of ``entries``.
    ids: frozenset[str]


@dataclass
class CatalogSnapshot:
    """The live entries of the enabled sources, read-only once built."""

    key: tuple[str, str]
    entries: tuple[CatalogEntry, ...]
    by_id: Mapping[str, CatalogEntry]
    spec_urls: Mapping[str, str]
    #: ``entry_id`` -> its id's scorer tokens, token set and space-joined tokens.
    tokens: Mapping[str, tuple[tuple[str, ...], frozenset[str], str]]
    precedence: Mapping[str, int]
    #: ``catalog_api_id`` -> entries sorted by ``(-precedence, entry_id)``.
    groups: Mapping[str, tuple[CatalogEntry, ...]]
    #: ``entry_id``s that share their ``catalog_api_id`` with another entry.
    shared_ids: frozenset[str]
    _collapsed: dict[tuple[Any, ...], CollapsedView] = field(default_factory=dict)

    def collapse(
        self,
        allowed: frozenset[str],
        registered: frozenset[str],
        *,
        include_alternates: bool = False,
    ) -> CollapsedView:
        """The caller's view; cached on the inputs that can change it."""
        reg_key = registered & self.shared_ids
        key = (allowed, reg_key, include_alternates)
        cached = self._collapsed.get(key)
        if cached is not None:
            return cached
        view = (
            _uncollapsed(self, allowed) if include_alternates else _collapse(self, allowed, reg_key)
        )
        if len(self._collapsed) >= _COLLAPSE_CACHE_SIZE:
            self._collapsed.pop(next(iter(self._collapsed)))
        self._collapsed[key] = view
        return view


def _uncollapsed(snapshot: CatalogSnapshot, allowed: frozenset[str]) -> CollapsedView:
    visible = tuple(e for e in snapshot.entries if e.source_slug in allowed)
    winners: dict[str, str] = {}
    for entry in visible:
        winners.setdefault(entry.catalog_api_id, entry.entry_id)
    return CollapsedView(
        entries=visible,
        alternates={},
        winner_of={},
        winner_by_api_id=winners,
        ids=frozenset(e.entry_id for e in visible),
    )


def _collapse(
    snapshot: CatalogSnapshot, allowed: frozenset[str], registered: frozenset[str]
) -> CollapsedView:
    kept: list[CatalogEntry] = []
    alternates: dict[str, tuple[str, ...]] = {}
    winner_of: dict[str, str] = {}
    winner_by_api_id: dict[str, str] = {}
    for api_id, group in snapshot.groups.items():
        if len(group) == 1:
            only = group[0]
            if only.source_slug in allowed:
                kept.append(only)
                winner_by_api_id[api_id] = only.entry_id
            continue
        visible = [e for e in group if e.source_slug in allowed]
        if not visible:
            continue
        winner = next((e for e in visible if e.entry_id in registered), visible[0])
        kept.append(winner)
        winner_by_api_id[api_id] = winner.entry_id
        others = sorted(e.entry_id for e in visible if e is not winner)
        if others:
            alternates[winner.entry_id] = tuple(others)
            for other in others:
                winner_of[other] = winner.entry_id
    kept.sort(key=lambda e: e.entry_id)
    return CollapsedView(
        entries=tuple(kept),
        alternates=alternates,
        winner_of=winner_of,
        winner_by_api_id=winner_by_api_id,
        ids=frozenset(e.entry_id for e in kept),
    )


def build_snapshot(
    key: tuple[str, str],
    rows: Iterable[Any],
    *,
    sources: Mapping[str, CatalogSourceConfig],
    mappings: Sequence[CatalogApiIdMapping],
) -> CatalogSnapshot:
    """Build a snapshot from live ``catalog_entries`` rows of enabled sources."""
    built: list[CatalogEntry] = []
    spec_refs: dict[str, Any] = {}
    for row in rows:
        entry = build_catalog_entry(
            source_slug=row.source_slug,
            external_id=row.external_id,
            catalog_api_id=row.catalog_api_id,
            hint=row.catalog_api_id_hint,
            title=row.title,
            version=row.version,
            tags=list(row.tags or []),
            human_url=row.human_url,
            has_spec=row.spec_ref is not None,
            content_hint=row.content_hint,
        )
        built.append(entry)
        spec_refs[entry.entry_id] = row.spec_ref
    curated = frozenset(s.slug for s in sources.values() if s.enabled and s.trust == "curated")
    curated_ids = {e.catalog_api_id for e in built if e.source_slug in curated}
    kept = exclude_unowned(
        built,
        curated_sources=curated,
        curated_vendors=curated_vendors_of(curated_ids),
        mappings=mappings,
    )
    kept.sort(key=lambda e: e.entry_id)
    precedence = {slug: cfg.precedence for slug, cfg in sources.items()}
    grouped: dict[str, list[CatalogEntry]] = {}
    for entry in kept:
        grouped.setdefault(entry.catalog_api_id, []).append(entry)
    groups = {
        api_id: tuple(
            sorted(members, key=lambda e: (-precedence.get(e.source_slug, 0), e.entry_id))
        )
        for api_id, members in grouped.items()
    }
    shared = frozenset(
        e.entry_id for members in groups.values() if len(members) > 1 for e in members
    )
    tokens: dict[str, tuple[tuple[str, ...], frozenset[str], str]] = {}
    by_api: dict[str, tuple[tuple[str, ...], frozenset[str], str]] = {}
    for entry in kept:
        tok = by_api.get(entry.catalog_api_id)
        if tok is None:
            names = name_tokens(entry.catalog_api_id)
            tok = (names, frozenset(names), " ".join(names))
            by_api[entry.catalog_api_id] = tok
        tokens[entry.entry_id] = tok
    spec_urls = {
        e.entry_id: url for e in kept if (url := spec_url_of(spec_refs.get(e.entry_id))) is not None
    }
    return CatalogSnapshot(
        key=key,
        entries=tuple(kept),
        by_id={e.entry_id: e for e in kept},
        spec_urls=spec_urls,
        tokens=tokens,
        precedence=precedence,
        groups=groups,
        shared_ids=shared,
    )


def allowed_sources(
    sources: Iterable[CatalogSourceConfig],
    *,
    permissions: frozenset[str],
    actor_id: str | None,
) -> frozenset[str]:
    """The enabled sources a caller may read.

    ``public`` sources are open to every catalog reader; ``org`` sources need
    ``catalog:org:read``; ``restricted`` sources need ``catalog:restricted:read``
    and a match in the source's ``readers`` ACL (one of its permissions, or the
    caller's actor id). An ``org:admin`` passes every ACL. ``permissions`` must
    be the caller's effective set; an empty set sees public sources only.
    """
    allowed: set[str] = set()
    admin = ORG_ADMIN in permissions
    for source in sources:
        if not source.enabled:
            continue
        if source.visibility == "public":
            allowed.add(source.slug)
        elif source.visibility == "org":
            if CATALOG_ORG_READ in permissions:
                allowed.add(source.slug)
        elif CATALOG_RESTRICTED_READ in permissions:
            readers = source.readers
            if admin or (
                readers is not None
                and (
                    bool(set(readers.permissions) & permissions)
                    or (actor_id is not None and actor_id in readers.actor_ids)
                )
            ):
                allowed.add(source.slug)
    return frozenset(allowed)


_SNAPSHOTS: weakref.WeakKeyDictionary[Any, CatalogSnapshot] = weakref.WeakKeyDictionary()


def cached_snapshot(owner: Any, key: tuple[str, str]) -> CatalogSnapshot | None:
    """The snapshot cached for ``owner`` (the registry database) under ``key``."""
    snapshot = _SNAPSHOTS.get(owner)
    return snapshot if snapshot is not None and snapshot.key == key else None


def store_snapshot(owner: Any, snapshot: CatalogSnapshot) -> None:
    _SNAPSHOTS[owner] = snapshot


def clear_snapshots() -> None:
    """Drop every cached snapshot (tests that rewrite catalog rows directly)."""
    _SNAPSHOTS.clear()
