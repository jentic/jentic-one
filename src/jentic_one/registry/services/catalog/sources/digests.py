"""Content-addressed versions of the catalog.

- Per source, ``content_digest`` covers every served field of the live rows and
  ``listing_digest`` only their ``(entry_id, catalog_api_id)`` pairs.
- ``catalog_version`` (12 hex) changes when any enabled source's live content or
  configuration changes; it keys process-local snapshots.
- ``listing_version`` (12 hex) covers only the sources visible to one caller, and
  only what can shift a listing (entries, their ids, and each source's
  visibility, precedence and enabled flag), so a change to a source the caller
  cannot see never changes it.

A sync that finds nothing new leaves every digest unchanged.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from typing import Any, Protocol

from jentic_one.shared.config import CatalogSourceConfig

_VERSION_LENGTH = 12


class EntryDigestRow(Protocol):
    """The stored fields a digest covers."""

    entry_id: str
    catalog_api_id: str
    title: str | None
    version: str | None
    tags: Any
    human_url: str | None
    content_hint: str | None
    spec_ref: Any


def _dumps(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), default=str).encode()


def content_digest(rows: Iterable[EntryDigestRow]) -> str:
    """SHA-256 over the sorted live rows' served fields."""
    digest = hashlib.sha256()
    for row in sorted(rows, key=lambda r: r.entry_id):
        digest.update(
            _dumps(
                [
                    row.entry_id,
                    row.catalog_api_id,
                    row.title,
                    row.version,
                    list(row.tags or []),
                    row.human_url,
                    row.content_hint,
                    row.spec_ref,
                ]
            )
        )
        digest.update(b"\n")
    return digest.hexdigest()


def listing_digest(rows: Iterable[EntryDigestRow]) -> str:
    """SHA-256 over the sorted live rows' ``(entry_id, catalog_api_id)``."""
    digest = hashlib.sha256()
    for row in sorted(rows, key=lambda r: r.entry_id):
        digest.update(_dumps([row.entry_id, row.catalog_api_id]))
        digest.update(b"\n")
    return digest.hexdigest()


def config_digest(cfg: CatalogSourceConfig) -> str:
    """SHA-256 of one source's configuration (names of secret env vars, never secrets)."""
    return hashlib.sha256(_dumps(cfg.model_dump(mode="json"))).hexdigest()


@dataclass(frozen=True, slots=True)
class SourceVersionInputs:
    """The per-source state the two catalog versions are computed from."""

    slug: str
    enabled: bool
    visibility: str
    precedence: int
    content_digest: str | None
    listing_digest: str | None
    config_digest: str


def catalog_version(sources: Sequence[SourceVersionInputs]) -> str:
    """12 hex over ``(slug, content_digest, config_digest)`` of every enabled source."""
    parts = sorted((s.slug, s.content_digest or "", s.config_digest) for s in sources if s.enabled)
    return hashlib.sha256(_dumps(parts)).hexdigest()[:_VERSION_LENGTH]


def listing_version(
    sources: Sequence[SourceVersionInputs], *, visible: frozenset[str] | None
) -> str:
    """12 hex over ``(slug, listing_digest, visibility, precedence, enabled)`` of visible sources.

    ``visible=None`` means every source is visible.
    """
    parts = sorted(
        (s.slug, s.listing_digest or "", s.visibility, s.precedence, s.enabled)
        for s in sources
        if visible is None or s.slug in visible
    )
    return hashlib.sha256(_dumps(parts)).hexdigest()[:_VERSION_LENGTH]
