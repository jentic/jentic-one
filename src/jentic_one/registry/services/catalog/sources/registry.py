"""Kind registry for catalog sources.

Maps a ``kind`` string (``catalog.sources[].kind``) to the factory class that
builds it. Registering the same class twice is a no-op; registering a different
class under a taken ``kind`` raises, so an extension cannot silently shadow a
built-in kind. Extensions register their kinds from their own startup hook.
"""

from __future__ import annotations

from jentic_one.registry.services.catalog.sources.protocol import CatalogSourceFactory

_KINDS: dict[str, type[CatalogSourceFactory]] = {}


class UnknownCatalogSourceKindError(LookupError):
    """No source kind is registered under the requested name."""


def register_catalog_source_kind[T: type[CatalogSourceFactory]](cls: T) -> T:
    """Class decorator registering a source kind under ``cls.kind``."""
    kind = cls.kind
    existing = _KINDS.get(kind)
    if existing is not None and existing is not cls:
        msg = (
            f"Catalog source kind {kind!r} is already registered by "
            f"{existing.__module__}.{existing.__qualname__}; refusing to shadow it "
            f"with {cls.__module__}.{cls.__qualname__}"
        )
        raise ValueError(msg)
    _KINDS[kind] = cls
    return cls


def resolve_catalog_source_kind(kind: str) -> type[CatalogSourceFactory]:
    """The factory registered under ``kind``; raises ``UnknownCatalogSourceKindError``."""
    cls = _KINDS.get(kind)
    if cls is None:
        raise UnknownCatalogSourceKindError(
            f"unknown catalog source kind {kind!r}; registered kinds: {sorted(_KINDS)}"
        )
    return cls


def registered_catalog_source_kinds() -> frozenset[str]:
    """The kinds registered in this process."""
    return frozenset(_KINDS)


def unregister_catalog_source_kind(kind: str) -> None:
    """Drop ``kind`` from the registry (test helper; a no-op when absent)."""
    _KINDS.pop(kind, None)
