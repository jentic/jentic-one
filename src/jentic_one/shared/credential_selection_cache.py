"""Short-TTL cache of a vendor's credential *headers* for broker resolution (#1714).

``CredentialResolver.resolve`` reads every credential row of the request's
vendor (``CredentialRepository.list_by_vendor``) and then picks one. This cache
holds that candidate list, keyed by vendor, as frozen :class:`CredentialHeader`
snapshots, so a warm resolve skips the query.

What it holds, and what it never holds:

- **Non-secret header fields only** — id, name, type, provider, API scope,
  server variables, ``active``, ``state``, owner, ``created_at`` and
  ``provider_account_ref``. Never secret material: no ciphertext, no
  plaintext, no token. The resolver reads the selected credential's material
  from the control DB on every call, so decryption and the
  ``credential.accessed`` audit event are never skipped.
- **No authorization.** The cached value is the same for every caller. The
  caller's binding boundary (``allowed_credential_ids``) and every other filter
  are applied to the cached headers on each call, so the key carries no actor
  identity and one actor's selection can never be served to another.

Staleness is bounded four ways:

- the TTL (``broker.credential_cache_ttl_s``, at most 60 s; ``0`` disables the
  cache);
- an in-process invalidation once a transaction that wrote a credential ends
  (on commit, rollback or close): a unit-of-work write to a watched ORM model
  is seen by :func:`watch_credential_writes`, and every bulk or raw-SQL writer
  of the ``credentials`` table calls :func:`mark_credentials_changed`
  (``tests/arch/test_credential_write_invalidation.py`` enforces it);
- the resolver's material read returns the selected credential's current
  header too, and re-resolves from the DB when it is gone, disabled or differs
  from the cached one;
- a selection that fails on cached headers (no match, unknown id or name,
  ambiguity) is re-run on a fresh DB read before it is returned, so a
  credential connected elsewhere is seen on the caller's retry.

What a write served by another process (the parts topology, or another worker)
can still miss for up to the TTL: a new or re-enabled credential that should
win over the cached winner (a more specific pin, or a sibling that should make
the call ambiguous), and any change to a ``NO_AUTH`` winner, which has no
material to read (a disable, a re-type, a ``server_variables`` edit). Each of
these selects a credential the caller is bound to right now; none injects a
secret that is gone or disabled.

The cache is per process. Concurrent misses on one vendor share a single
header read (single-flight), so a clear does not stampede the control DB. The
size bound counts headers, not vendors, so one vendor with many credentials is
charged for each of them.
"""

from __future__ import annotations

import asyncio
import time
import weakref
from collections import OrderedDict
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sqlalchemy import event
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Mapper, Session, SessionTransaction, object_session

from jentic_one.shared.metrics import get_meter

_meter = get_meter("broker")
_cache_hits = _meter.create_counter(
    "broker.credential_selection_cache.hits",
    description="Credential resolutions served from cached credential headers",
)
_cache_misses = _meter.create_counter(
    "broker.credential_selection_cache.misses",
    description="Credential header cache misses (no entry, or an expired one)",
)
_cache_stale = _meter.create_counter(
    "broker.credential_selection_cache.stale",
    description=(
        "Cached credential headers that were re-read from the DB before use "
        "(reason: winner_changed or negative_selection)"
    ),
)

STALE_WINNER_CHANGED = "winner_changed"
STALE_NEGATIVE_SELECTION = "negative_selection"


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
    state: str
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
            state=row.state,
            created_by=row.created_by,
            created_at=row.created_at,
            provider_account_ref=row.provider_account_ref,
        )


@dataclass(slots=True)
class _Entry:
    headers: tuple[CredentialHeader, ...]
    cached_at: float

    @property
    def cost(self) -> int:
        # An empty candidate list still occupies a slot.
        return max(1, len(self.headers))


class CredentialSelectionCache:
    """Per-process TTL-LRU of ``vendor -> tuple[CredentialHeader, ...]``.

    ``ttl_seconds <= 0`` disables it: :meth:`get` always misses and :meth:`put`
    stores nothing. ``max_headers`` bounds the total number of cached headers
    (an empty entry counts as one); least-recently-used vendors are evicted
    first, and a vendor with more headers than the bound is not cached.

    A fill races an invalidation safely: :meth:`begin_fill` returns the
    :attr:`generation` to capture before the DB query, and :meth:`put` drops the
    value when an invalidation landed in between. :meth:`begin_fill` /
    :meth:`end_fill` / :meth:`join_fill` give concurrent misses on one vendor a
    single in-flight read.
    """

    def __init__(self, *, ttl_seconds: float, max_headers: int) -> None:
        if max_headers < 1:
            raise ValueError("max_headers must be >= 1")
        self._ttl_seconds = ttl_seconds
        self._max_headers = max_headers
        self._entries: OrderedDict[str, _Entry] = OrderedDict()
        self._cost = 0
        self._generation = 0
        self._inflight: dict[str, asyncio.Future[tuple[CredentialHeader, ...] | None]] = {}
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
        """The vendor's cached headers, or ``None`` on a miss or an expired entry.

        Counts misses only: whether a returned entry serves the call is known
        to the caller, which reports it with :meth:`record_hit` or
        :meth:`record_stale`.
        """
        if not self.enabled:
            return None
        entry = self._entries.get(vendor)
        if entry is None or (time.monotonic() - entry.cached_at) >= self._ttl_seconds:
            _cache_misses.add(1)
            return None
        self._entries.move_to_end(vendor)
        return entry.headers

    @staticmethod
    def record_hit() -> None:
        """Count a resolution served from cached headers."""
        _cache_hits.add(1)

    @staticmethod
    def record_stale(reason: str) -> None:
        """Count cached headers that had to be re-read from the DB before use."""
        _cache_stale.add(1, {"reason": reason})

    def put(self, vendor: str, headers: tuple[CredentialHeader, ...], *, generation: int) -> None:
        """Store ``headers`` unless the cache was invalidated since ``generation``."""
        if not self.enabled or generation != self._generation:
            return
        self._drop(vendor)
        entry = _Entry(headers=headers, cached_at=time.monotonic())
        if entry.cost > self._max_headers:
            return
        self._entries[vendor] = entry
        self._cost += entry.cost
        while self._cost > self._max_headers:
            _, evicted = self._entries.popitem(last=False)
            self._cost -= evicted.cost

    def invalidate(self) -> None:
        """Drop every entry and fence off fills that read the DB before now.

        In-flight fills are released with no result, so their waiters read the
        DB themselves rather than use a read that predates the write.
        """
        self._generation += 1
        self._entries.clear()
        self._cost = 0
        inflight, self._inflight = self._inflight, {}
        for future in inflight.values():
            if not future.done():
                future.set_result(None)

    def begin_fill(
        self, vendor: str
    ) -> tuple[int, asyncio.Future[tuple[CredentialHeader, ...] | None] | None]:
        """Start a fill: ``(generation, token)``; pass both to :meth:`end_fill`.

        The token is ``None`` when another fill of ``vendor`` is already in
        flight (or the cache is disabled); the caller then reads without
        publishing its result to waiters.
        """
        if not self.enabled or vendor in self._inflight:
            return self._generation, None
        future: asyncio.Future[tuple[CredentialHeader, ...] | None] = (
            asyncio.get_running_loop().create_future()
        )
        self._inflight[vendor] = future
        return self._generation, future

    def end_fill(
        self,
        vendor: str,
        token: asyncio.Future[tuple[CredentialHeader, ...] | None] | None,
        headers: tuple[CredentialHeader, ...] | None,
        *,
        generation: int,
    ) -> None:
        """Finish a fill: store ``headers`` and hand them to the fill's waiters.

        ``headers=None`` (the read failed or was cancelled) stores nothing and
        releases the waiters with no result. Waiters only ever receive headers
        that :meth:`put` would also store, i.e. no invalidation landed since
        ``generation``.
        """
        fresh = headers is not None and generation == self._generation
        if headers is not None:
            self.put(vendor, headers, generation=generation)
        if token is None:
            return
        if self._inflight.get(vendor) is token:
            del self._inflight[vendor]
        if not token.done():
            token.set_result(headers if fresh else None)

    async def join_fill(self, vendor: str) -> tuple[CredentialHeader, ...] | None:
        """Wait for an in-flight fill of ``vendor``; ``None`` when there is none.

        Also ``None`` when the fill failed or an invalidation landed while it
        ran; the caller then reads the DB itself.
        """
        future = self._inflight.get(vendor)
        if future is None:
            return None
        # Shielded: a waiter's own cancellation must not cancel the fill's
        # shared result for the other waiters.
        return await asyncio.shield(future)

    def _drop(self, vendor: str) -> None:
        entry = self._entries.pop(vendor, None)
        if entry is not None:
            self._cost -= entry.cost


_live_caches: weakref.WeakSet[CredentialSelectionCache] = weakref.WeakSet()
_watched_models: set[type] = set()
# Sessions whose current transaction wrote a ``credentials`` row; consumed when
# the outermost transaction ends. A module-level set keeps the common case (no
# credential write) to one empty-set check per transaction end.
_changed_sessions: weakref.WeakSet[Session] = weakref.WeakSet()


def invalidate_all_credential_caches() -> None:
    """Invalidate every live :class:`CredentialSelectionCache` in this process."""
    for cache in list(_live_caches):
        cache.invalidate()


def mark_credentials_changed(session: AsyncSession | Session) -> None:
    """Invalidate every cache once ``session``'s outermost transaction ends.

    For writers the ORM write events do not see: ORM bulk statements and raw
    SQL (``text()``) against the ``credentials`` table. Call it at the write
    site, inside the transaction.
    """
    sync_session = session.sync_session if isinstance(session, AsyncSession) else session
    _changed_sessions.add(sync_session)


def watch_credential_writes(model: type) -> None:
    """Invalidate every cache once a transaction that wrote ``model`` ends.

    Covers unit-of-work writes (insert/update/delete of an instance). ORM bulk
    statements and raw SQL are not seen; their call sites call
    :func:`mark_credentials_changed`.
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
    _changed_sessions.add(session)


@event.listens_for(Session, "after_transaction_end")
def _invalidate_on_transaction_end(session: Session, transaction: SessionTransaction) -> None:
    # Only the outermost transaction ends the write. A SAVEPOINT (or a flush's
    # subtransaction) has a parent: its release is not yet visible to other
    # connections, and its rollback must not drop a write the outer
    # transaction made, so the mark stays until the outer transaction ends.
    # Rollback and close invalidate too: on a backend in DBAPI autocommit
    # (SQLite here) a flushed write persists without a commit, and a spurious
    # invalidation only costs one extra query.
    if not _changed_sessions or transaction.parent is not None:
        return
    if session in _changed_sessions:
        _changed_sessions.discard(session)
        invalidate_all_credential_caches()
