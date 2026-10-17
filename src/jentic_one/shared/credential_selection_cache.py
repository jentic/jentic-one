"""Short-TTL cache of a vendor's credential *headers* for broker resolution (#1714).

``CredentialResolver.resolve`` reads every credential row of the request's
vendor (``CredentialRepository.list_by_vendor``) and then picks one. This cache
holds that candidate list, keyed by vendor, as frozen :class:`CredentialHeader`
snapshots, so a warm resolve skips the query.

What it holds, and what it never holds:

- **Non-secret header fields only** — id, name, type, provider, API scope,
  server variables, ``active``, owner. Never secret material: no ciphertext, no
  plaintext, no token. The resolver reads the selected credential's material
  from the control DB on every call, so decryption and the
  ``credential.accessed`` audit event are never skipped.
- **No authorization.** The cached value is the same for every caller. The
  caller's binding boundary (``allowed_credential_ids``) and every other filter
  are applied to the cached headers on each call, so the key carries no actor
  identity and one actor's selection can never be served to another.

Staleness is bounded three ways:

- the TTL (``broker.credential_cache_ttl_s``; ``0`` disables the cache);
- an in-process invalidation on every committed write to a watched ORM model
  (see :func:`watch_credential_writes`): a create, edit, disable or delete
  served by this process drops every entry as its transaction ends;
- the resolver's material read returns the selected credential's current
  header too, and re-resolves from the DB when it is gone, disabled or differs
  from the cached one. A ``NO_AUTH`` winner has no material to read, so for it
  only the TTL and the in-process invalidation apply.

The cache is per process. A write served by another process (the parts
topology, or another worker) reaches this one after the TTL at worst.
"""

from __future__ import annotations

import time
import weakref
from collections import OrderedDict
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sqlalchemy import event
from sqlalchemy.orm import Mapper, ORMExecuteState, Session, SessionTransaction, object_session

from jentic_one.shared.metrics import get_meter

_meter = get_meter("broker")
_cache_hits = _meter.create_counter(
    "broker.credential_selection_cache.hits", description="Credential header cache hits"
)
_cache_misses = _meter.create_counter(
    "broker.credential_selection_cache.misses", description="Credential header cache misses"
)

# Vendors come from registry discovery, so the key space is bounded by the
# catalog; the cap only guards memory against a pathological spray.
DEFAULT_MAX_ENTRIES = 4096

# ``Session.info`` flag set when a flush or bulk statement writes a watched
# model; consumed when the session's transaction ends.
_CHANGED_KEY = "jentic_one.credential_headers_changed"


@dataclass(frozen=True, slots=True)
class CredentialHeader:
    """Non-secret snapshot of one ``credentials`` row — never secret material."""

    id: str
    name: str
    type: str
    provider: str
    api_vendor: str
    api_name: str | None
    api_version: str | None
    server_variables: Mapping[str, str] | None
    active: bool
    created_by: str | None
    created_at: datetime
    provider_account_ref: str | None

    @classmethod
    def from_row(cls, row: Any) -> CredentialHeader:
        """Snapshot the header columns of a ``Credential`` row."""
        return cls(
            id=row.id,
            name=row.name,
            type=row.type,
            provider=row.provider,
            api_vendor=row.api_vendor,
            api_name=row.api_name,
            api_version=row.api_version,
            # A private copy: the row's dict must not alias the cached value.
            server_variables=dict(row.server_variables) if row.server_variables else None,
            active=row.active,
            created_by=row.created_by,
            created_at=row.created_at,
            provider_account_ref=row.provider_account_ref,
        )


@dataclass(slots=True)
class _Entry:
    headers: tuple[CredentialHeader, ...]
    cached_at: float


class CredentialSelectionCache:
    """Per-process TTL-LRU of ``vendor -> tuple[CredentialHeader, ...]``.

    ``ttl_seconds <= 0`` disables it: :meth:`get` always misses and :meth:`put`
    stores nothing. A fill races an invalidation safely: read :attr:`generation`
    before the DB query and pass it to :meth:`put`, which drops the value when
    an invalidation landed in between.
    """

    def __init__(self, *, ttl_seconds: float, max_entries: int = DEFAULT_MAX_ENTRIES) -> None:
        if max_entries < 1:
            raise ValueError("max_entries must be >= 1")
        self._ttl_seconds = ttl_seconds
        self._max_entries = max_entries
        self._entries: OrderedDict[str, _Entry] = OrderedDict()
        self._generation = 0
        _live_caches.add(self)

    @property
    def enabled(self) -> bool:
        """Whether the cache stores anything (``ttl_seconds > 0``)."""
        return self._ttl_seconds > 0

    @property
    def generation(self) -> int:
        """Bumped on every invalidation; capture it before a fill's DB read."""
        return self._generation

    def get(self, vendor: str) -> tuple[CredentialHeader, ...] | None:
        """The vendor's cached headers, or ``None`` on a miss or an expired entry."""
        if not self.enabled:
            return None
        entry = self._entries.get(vendor)
        if entry is None or (time.monotonic() - entry.cached_at) >= self._ttl_seconds:
            _cache_misses.add(1)
            return None
        self._entries.move_to_end(vendor)
        _cache_hits.add(1)
        return entry.headers

    def put(self, vendor: str, headers: tuple[CredentialHeader, ...], *, generation: int) -> None:
        """Store ``headers`` unless the cache was invalidated since ``generation``."""
        if not self.enabled or generation != self._generation:
            return
        self._entries[vendor] = _Entry(headers=headers, cached_at=time.monotonic())
        self._entries.move_to_end(vendor)
        while len(self._entries) > self._max_entries:
            self._entries.popitem(last=False)

    def discard(self, vendor: str) -> None:
        """Drop one vendor's entry (the resolver found it stale)."""
        self._entries.pop(vendor, None)

    def invalidate(self) -> None:
        """Drop every entry and fence off fills that read the DB before now."""
        self._generation += 1
        self._entries.clear()


_live_caches: weakref.WeakSet[CredentialSelectionCache] = weakref.WeakSet()
_watched_models: set[type] = set()


def invalidate_all_credential_caches() -> None:
    """Invalidate every live :class:`CredentialSelectionCache` in this process."""
    for cache in list(_live_caches):
        cache.invalidate()


def watch_credential_writes(model: type) -> None:
    """Invalidate every cache once a transaction that wrote ``model`` ends.

    Covers unit-of-work writes (insert/update/delete of an instance) and ORM
    bulk ``insert``/``update``/``delete`` statements. Raw SQL text is not
    seen; the TTL bounds it.
    """
    if model in _watched_models:
        return
    _watched_models.add(model)
    for name in ("after_insert", "after_update", "after_delete"):
        event.listen(model, name, _mark_instance_write)


def _mark_instance_write(_mapper: Mapper[Any], _connection: Any, target: Any) -> None:
    session = object_session(target)
    if session is None:
        invalidate_all_credential_caches()
        return
    session.info[_CHANGED_KEY] = True


@event.listens_for(Session, "do_orm_execute")
def _mark_bulk_write(state: ORMExecuteState) -> None:
    if not (state.is_insert or state.is_update or state.is_delete):
        return
    if any(m.class_ in _watched_models for m in state.all_mappers):
        state.session.info[_CHANGED_KEY] = True


@event.listens_for(Session, "after_transaction_end")
def _invalidate_on_transaction_end(session: Session, transaction: SessionTransaction) -> None:
    # Only the outermost transaction ends the write: a SAVEPOINT release is not
    # yet visible to other connections. Rollback and close invalidate too: on a
    # backend in DBAPI autocommit (SQLite here) a flushed write persists without
    # a commit, and a spurious invalidation only costs one extra query.
    if transaction.parent is not None:
        return
    if session.info.pop(_CHANGED_KEY, False):
        invalidate_all_credential_caches()
