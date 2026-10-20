"""Role-segregated shared-state protocols and the in-process memory backend.

The broker (and other surfaces later) need a small set of cross-instance state
operations: read-mostly caches, a shared token-bucket rate limiter, atomic
counters for circuit state, and an atomic claim primitive for idempotency.

Rather than a single fat interface that every consumer depends on, the surface
is split into three **narrow, role-specific** protocols (ISP): a rate limiter
should not see ``set_if_absent`` and a cache should not see the Lua
``token_bucket``. The concrete backends (:class:`MemoryStateBackend` here,
``RedisStateBackend`` in :mod:`jentic_one.shared.state.redis`) implement all
three roles; the factory builds a single instance per process. Consumers,
however, type their dependency on the one role they need.
"""

from __future__ import annotations

import heapq
import math
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import Protocol, runtime_checkable

import structlog

_logger = structlog.get_logger(__name__)

# Default per-namespace bounds for :class:`MemoryStateBackend`'s key/value store.
DEFAULT_MEMORY_MAX_ENTRIES = 100_000
DEFAULT_MEMORY_MAX_BYTES = 16 * 1024 * 1024
# Default bound on the budgeted bytes of all namespaces of one backend together.
DEFAULT_MEMORY_MAX_TOTAL_BYTES = 192 * 1024 * 1024
DEFAULT_MEMORY_SWEEP_INTERVAL_S = 30.0
# Per-entry cost on top of the key and value bytes: the dict slot, the key
# ``str`` and value ``bytes`` object headers, the ``_Entry``, and its items in
# the namespace and owner expiry indexes, including superseded items up to the
# compaction threshold. Measured with ``tracemalloc`` on CPython 3.12 by
# filling ``idem``/``idem-body`` through ``SharedStateIdempotencyStore`` (20
# callers, 7 response headers, a claim then a record per request) and taking
# (traced bytes - key and value bytes) / entries: about 680-730 bytes per
# record and about 430 per body entry.
# ``test_the_byte_budget_tracks_real_memory`` in
# ``tests/unit/broker/test_idempotency_store.py`` keeps the budget within 15%
# of real memory. Allocator fragmentation outside ``tracemalloc``'s view is
# not included, so leave container headroom over the budgets.
ENTRY_OVERHEAD_BYTES = 750
# Bounds of the ``Retry-After`` hint a capacity refusal carries.
_MIN_RETRY_AFTER_S = 1
_MAX_RETRY_AFTER_S = 3600
# An expiry index starts compacting once its superseded items outnumber its
# live entries by this much, so overwrites cannot grow it without bound.
_INDEX_SLACK = 64
# Most items one write moves while an expiry index compacts, so compaction is
# spread over many writes and no single write pays an O(entries) rebuild.
COMPACTION_STEP = 64
# The scope name a refusal by the backend-wide byte cap reports.
TOTAL_SCOPE = "total"


class StateBackendCapacityError(Exception):
    """A write was refused because its key namespace (or its owner's share) is full.

    Raised by :class:`MemoryStateBackend` instead of evicting a live entry: an
    evicted idempotency claim or replay marker would make the next
    ``set_if_absent`` for that key succeed, so the guard would fail open.
    Callers must refuse the request that needed the write (for example with a
    503), never treat it as a fresh claim.

    Attributes:
        namespace: The full namespace.
        retry_after_s: Seconds until the earliest live entry in the refusing
            namespace (or owner share, or the whole backend) expires, clamped
            to [1, 3600].
        owner: The owner whose share is spent, or ``None`` when the whole
            namespace (or the backend) is full.
        total: Whether the backend-wide byte cap refused the write.
    """

    def __init__(
        self,
        namespace: str,
        *,
        retry_after_s: int,
        owner: str | None = None,
        total: bool = False,
    ) -> None:
        if total:
            scope = f"total byte budget (writing namespace {namespace!r})"
        else:
            scope = f"namespace {namespace!r}" + (f" share of owner {owner!r}" if owner else "")
        super().__init__(f"shared-state {scope} is full")
        self.namespace = namespace
        self.retry_after_s = retry_after_s
        self.owner = owner
        self.total = total


@dataclass(frozen=True, slots=True)
class NamespaceBudget:
    """Entry-count and approximate byte budget for one key namespace.

    ``max_owner_share`` (a fraction in ``(0, 1]``), when set, also caps what one
    owner may hold: the owner of a key is its second ``:``-separated segment
    (``idem:<caller>:<key>`` → ``<caller>``), so one caller filling its share is
    refused without locking every other caller out of the namespace.
    """

    max_entries: int
    max_bytes: int
    max_owner_share: float | None = None


def key_namespace(key: str) -> str:
    """The namespace a key is budgeted under: its prefix up to the first ``:``."""
    return key.split(":", 1)[0] if ":" in key else ""


def key_owner(key: str) -> str:
    """The owner a key is shared under: its second ``:``-separated segment."""
    parts = key.split(":", 2)
    return parts[1] if len(parts) > 1 else ""


@dataclass(frozen=True, slots=True)
class RateLimitDecision:
    """Outcome of a single token-bucket evaluation.

    Attributes:
        allowed: Whether the request was permitted (enough tokens for ``cost``).
        remaining: Whole tokens left in the bucket after this evaluation.
        retry_after_s: Seconds the caller should wait before the bucket will
            hold enough tokens for the requested ``cost``. ``0.0`` when allowed.
        limit: The bucket capacity (``burst``) — useful for ``X-RateLimit-Limit``
            style response headers.
    """

    allowed: bool
    remaining: int
    retry_after_s: float
    limit: int


@runtime_checkable
class KeyValueStore(Protocol):
    """Read-mostly cache role: opaque bytes with a TTL."""

    async def get(self, key: str) -> bytes | None: ...

    async def set(self, key: str, value: bytes, *, ttl_s: float) -> None: ...


@runtime_checkable
class RateLimitStore(Protocol):
    """Rate-limiter role: a single atomic token-bucket evaluation."""

    async def token_bucket(
        self, key: str, *, rate: float, burst: int, cost: int = 1
    ) -> RateLimitDecision: ...


@runtime_checkable
class AtomicStore(Protocol):
    """Atomic-counter + claim role: circuit counters and idempotency."""

    async def incr_with_ttl(self, key: str, *, ttl_s: float, amount: int = 1) -> int: ...

    async def set_if_absent(self, key: str, value: bytes, *, ttl_s: float) -> bool: ...

    async def delete_if_equals(self, key: str, expected: bytes) -> bool:
        """Atomically delete *key* only while it still holds *expected*.

        The release half of a ``set_if_absent`` claim: a holder whose claim
        expired and was re-claimed by someone else holds a different value, so
        it can never delete the newer claim. Returns whether a key was deleted.
        """
        ...

    async def aclose(self) -> None: ...


@runtime_checkable
class SharedStateBackend(KeyValueStore, RateLimitStore, AtomicStore, Protocol):
    """Convenience union of all three roles.

    Used **only** at the composition root, where the factory builds one concrete
    backend that fulfils every role. No consumer should depend on this union —
    inject the narrow role (``KeyValueStore`` / ``RateLimitStore`` /
    ``AtomicStore``) instead.
    """


@dataclass(slots=True)
class _Entry:
    """A stored value with its expiry deadline, budgeted size and index sequence."""

    value: bytes
    expires_at: float
    size: int
    seq: int


@dataclass(slots=True)
class _Usage:
    """Live accounting for one namespace (or one owner's share of it).

    ``index`` is a min-heap of ``(expires_at, seq, key)``. An item is current
    only while ``seq`` matches the stored entry; overwrites and deletes leave
    superseded items behind, which are skipped when they reach the top.

    Once superseded items outnumber the live entries, ``index`` becomes
    ``draining`` and a fresh ``index`` starts. Every later index write moves at
    most :data:`COMPACTION_STEP` items from ``draining`` (keeping only current
    ones) until it is empty, so compaction costs at most that many heap pushes
    per write rather than one O(n) rebuild. Both heaps are consulted for the
    earliest deadline while ``draining`` is non-empty.
    """

    entries: int = 0
    bytes: int = 0
    index: list[tuple[float, int, str]] = field(default_factory=list)
    draining: list[tuple[float, int, str]] = field(default_factory=list)


@dataclass(frozen=True, slots=True)
class _Refusal:
    """The scope refusing a write: ``namespace``, ``owner_share`` or :data:`TOTAL_SCOPE`."""

    scope: str
    usage: _Usage | None
    owner: str | None = None


@dataclass(slots=True)
class _Bucket:
    """Token-bucket state: current token count, last-refill time and refill-to-full time.

    ``full_at`` is when the bucket will have refilled to ``burst``; from then on
    it is indistinguishable from a fresh bucket, so a sweep may drop it.
    """

    tokens: float
    updated_at: float
    full_at: float


def _full_at(bucket: _Bucket, *, rate: float, burst: int) -> float:
    """When *bucket* will have refilled to ``burst`` at ``rate`` tokens per second."""
    missing = float(burst) - bucket.tokens
    if missing <= 0:
        return bucket.updated_at
    return bucket.updated_at + missing / rate if rate > 0 else float("inf")


def _clamp_retry_after(seconds: float) -> int:
    return max(_MIN_RETRY_AFTER_S, min(_MAX_RETRY_AFTER_S, math.ceil(seconds)))


class MemoryStateBackend:
    """In-process implementation of all three state roles.

    Backed by plain dicts with monotonic-clock TTL expiry. Scope is
    per-process, which is fine behind autoscaling for most installs; the Redis
    backend exists for cross-instance coordination.

    A ``clock`` callable (defaulting to :func:`time.monotonic`) can be injected
    to make TTL and token-bucket refill behaviour deterministic in tests.

    The key/value store is bounded per key namespace (:func:`key_namespace`,
    the key's prefix up to its first ``:``, e.g. ``idem``, ``login-csrf``):

    * each namespace keeps its entries in expiry order, so dropping expired
      entries costs O(expired), never a scan of the store. An expired entry is
      dropped when it is read, every write at least ``sweep_interval_s`` after
      the last sweep drops the expired entries of every namespace (and every
      token bucket that has refilled to full), and a write to a full
      namespace drops that namespace's expired entries first;
    * each namespace has an entry-count budget and an approximate byte budget
      (key + value + :data:`ENTRY_OVERHEAD_BYTES`), and optionally a per-owner
      share of both (:class:`NamespaceBudget`); all namespaces together also
      have a backend-wide byte cap (``max_total_bytes``). A write that would
      still take its namespace, its owner or the backend past a budget is
      refused with :class:`StateBackendCapacityError`, whose
      ``retry_after_s`` is when the earliest live entry there expires. A live
      entry is never evicted, so a claim or replay marker cannot be dropped
      early and re-claimed.

    An overwrite is charged only its size difference, so rewriting a key with
    a value no larger than the current one always fits.

    Budgets are per namespace, so filling one key kind (for example
    unauthenticated login CSRF nonces) refuses only writes of that kind and
    never starves idempotency claims or other key kinds, as long as
    the sum of the namespace budgets in use stays under ``max_total_bytes``.
    The key/value store's memory is bounded by the smaller of that sum and
    ``max_total_bytes``. Token buckets are not budgeted: each is a
    fixed-size record kept only until it has refilled, so they are bounded by
    request rate times refill time.
    """

    def __init__(
        self,
        *,
        clock: Callable[[], float] = time.monotonic,
        max_entries: int = DEFAULT_MEMORY_MAX_ENTRIES,
        max_bytes: int = DEFAULT_MEMORY_MAX_BYTES,
        max_total_bytes: int = DEFAULT_MEMORY_MAX_TOTAL_BYTES,
        namespace_budgets: Mapping[str, NamespaceBudget] | None = None,
        sweep_interval_s: float = DEFAULT_MEMORY_SWEEP_INTERVAL_S,
    ) -> None:
        default = NamespaceBudget(max_entries=max_entries, max_bytes=max_bytes)
        budgets = dict(namespace_budgets or {})
        if max_total_bytes < 1:
            raise ValueError(f"max_total_bytes must be positive, got {max_total_bytes}")
        for name, budget in {"": default, **budgets}.items():
            if budget.max_entries < 1:
                raise ValueError(
                    f"max_entries must be positive, got {budget.max_entries} for {name!r}"
                )
            if budget.max_bytes < 1:
                raise ValueError(f"max_bytes must be positive, got {budget.max_bytes} for {name!r}")
            share = budget.max_owner_share
            if share is not None and not 0 < share <= 1:
                raise ValueError(f"max_owner_share must be in (0, 1], got {share} for {name!r}")
        self._clock = clock
        self._default_budget = default
        self._budgets = budgets
        self._max_total_bytes = max_total_bytes
        self._total_bytes = 0
        self._sweep_interval_s = sweep_interval_s
        self._kv: dict[str, _Entry] = {}
        self._usage: dict[str, _Usage] = {}
        self._owner_usage: dict[tuple[str, str], _Usage] = {}
        self._seq = 0
        self._buckets: dict[str, _Bucket] = {}
        self._bucket_index: list[tuple[float, str]] = []
        self._next_sweep_at = clock() + sweep_interval_s
        self._refusal_logged_at: dict[tuple[str, str], float] = {}

    def _budget(self, namespace: str) -> NamespaceBudget:
        return self._budgets.get(namespace, self._default_budget)

    def _owner_key(self, key: str, namespace: str) -> tuple[str, str] | None:
        """The ``(namespace, owner)`` share *key* counts against, if its namespace has one."""
        if self._budget(namespace).max_owner_share is None:
            return None
        return (namespace, key_owner(key))

    def _is_current(self, item: tuple[float, int, str]) -> bool:
        entry = self._kv.get(item[2])
        return entry is not None and entry.seq == item[1]

    def _index(self, usage: _Usage, entry: _Entry, key: str) -> None:
        heapq.heappush(usage.index, (entry.expires_at, entry.seq, key))
        if usage.draining:
            self._compact_step(usage)
        elif len(usage.index) > 2 * usage.entries + _INDEX_SLACK:
            usage.draining, usage.index = usage.index, []
            self._compact_step(usage)

    def _compact_step(self, usage: _Usage) -> None:
        """Move up to :data:`COMPACTION_STEP` items from ``draining`` to ``index``.

        Popping from the end of a heap's list keeps it a valid heap, so
        ``draining`` still answers earliest-deadline queries mid-compaction.
        A draining heap of ``d`` items empties within ``d / COMPACTION_STEP``
        index writes, well before the fresh index can reach the threshold
        again.
        """
        draining, index = usage.draining, usage.index
        for _ in range(min(COMPACTION_STEP, len(draining))):
            item = draining.pop()
            if self._is_current(item):
                heapq.heappush(index, item)

    def _delete(self, key: str) -> None:
        entry = self._kv.pop(key)
        self._total_bytes -= entry.size
        namespace = key_namespace(key)
        usage = self._usage[namespace]
        usage.entries -= 1
        usage.bytes -= entry.size
        owner_key = self._owner_key(key, namespace)
        if owner_key is not None:
            owner = self._owner_usage[owner_key]
            owner.entries -= 1
            owner.bytes -= entry.size
            if owner.entries == 0:
                del self._owner_usage[owner_key]

    def _live_entry(self, key: str) -> _Entry | None:
        """Return the entry for *key* if present and not expired, else drop it."""
        entry = self._kv.get(key)
        if entry is None:
            return None
        if entry.expires_at <= self._clock():
            self._delete(key)
            return None
        return entry

    def _earliest_live(self, usage: _Usage, now: float) -> float | None:
        """Drop *usage*'s expired entries; return its earliest live deadline.

        Pops only superseded and expired index items, so the cost is
        O(expired), not O(entries).
        """
        earliest: float | None = None
        for index in (usage.index, usage.draining):
            while index:
                item = index[0]
                if not self._is_current(item):
                    heapq.heappop(index)
                elif item[0] <= now:
                    heapq.heappop(index)
                    self._delete(item[2])
                else:
                    earliest = item[0] if earliest is None else min(earliest, item[0])
                    break
        return earliest

    def _earliest_live_anywhere(self, now: float) -> float | None:
        """Drop every namespace's expired entries; return the earliest live deadline.

        O(namespaces + expired): what the backend-wide cap waits on.
        """
        deadlines = [self._earliest_live(usage, now) for usage in list(self._usage.values())]
        live = [d for d in deadlines if d is not None]
        return min(live) if live else None

    def _sweep(self, now: float) -> None:
        """Drop expired entries of every namespace and every refilled bucket."""
        self._earliest_live_anywhere(now)
        index = self._bucket_index
        while index and index[0][0] <= now:
            full_at, key = heapq.heappop(index)
            bucket = self._buckets.get(key)
            if bucket is not None and bucket.full_at == full_at:
                del self._buckets[key]
        self._next_sweep_at = now + self._sweep_interval_s

    def _maybe_sweep(self, now: float) -> None:
        if now >= self._next_sweep_at:
            self._sweep(now)

    @staticmethod
    def _fits(
        usage: _Usage | None, existing: _Entry | None, size: int, max_entries: int, max_bytes: int
    ) -> bool:
        entries = (usage.entries if usage else 0) + (0 if existing is not None else 1)
        used = (usage.bytes if usage else 0) - (existing.size if existing is not None else 0)
        return entries <= max_entries and used + size <= max_bytes

    def _refusing_scope(self, key: str, namespace: str, size: int) -> _Refusal | None:
        """``None`` when the write fits; else the scope that refuses it."""
        budget = self._budget(namespace)
        existing = self._kv.get(key)
        usage = self._usage.get(namespace)
        if not self._fits(usage, existing, size, budget.max_entries, budget.max_bytes):
            return _Refusal("namespace", usage)
        owner_key = self._owner_key(key, namespace)
        if owner_key is not None and budget.max_owner_share is not None:
            share = budget.max_owner_share
            owner = self._owner_usage.get(owner_key)
            if not self._fits(
                owner,
                existing,
                size,
                max(1, int(budget.max_entries * share)),
                max(1, int(budget.max_bytes * share)),
            ):
                return _Refusal("owner_share", owner, owner_key[1])
        # Charged like a namespace: an overwrite no larger than the entry fits.
        total = self._total_bytes - (existing.size if existing is not None else 0)
        if total + size > self._max_total_bytes:
            return _Refusal(TOTAL_SCOPE, None)
        return None

    def _reclaim(self, refusal: _Refusal, now: float) -> float | None:
        """Drop the refusing scope's expired entries; return its earliest live deadline."""
        if refusal.scope == TOTAL_SCOPE:
            return self._earliest_live_anywhere(now)
        if refusal.usage is None:
            return None
        return self._earliest_live(refusal.usage, now)

    def _refuse(
        self, namespace: str, refusal: _Refusal, earliest: float | None, now: float
    ) -> StateBackendCapacityError:
        retry_after_s = _clamp_retry_after(
            earliest - now if earliest is not None else _MAX_RETRY_AFTER_S
        )
        # One warning per namespace (and scope) per sweep interval: a flood of
        # refused writes must not become a flood of log lines.
        log_key = (namespace, refusal.scope)
        last = self._refusal_logged_at.get(log_key)
        if last is None or now - last >= self._sweep_interval_s:
            self._refusal_logged_at[log_key] = now
            budget = self._budget(namespace)
            usage = self._usage.get(namespace, _Usage())
            _logger.warning(
                "memory_state_backend_capacity_refused",
                namespace=namespace,
                scope=refusal.scope,
                entries=usage.entries,
                approx_bytes=usage.bytes,
                max_entries=budget.max_entries,
                max_bytes=budget.max_bytes,
                max_owner_share=budget.max_owner_share,
                total_approx_bytes=self._total_bytes,
                max_total_bytes=self._max_total_bytes,
                retry_after_s=retry_after_s,
                detail=(
                    "the in-process state backend namespace (or one owner's share "
                    "of it, or the backend as a whole) is full of live entries, so "
                    "new writes to it are refused (callers answer 503) until entries "
                    "expire. Raise broker.resilience.backend.memory budgets (and the "
                    "container memory limit with them), shorten the TTL of what it "
                    "stores, or use the redis backend."
                ),
            )
        return StateBackendCapacityError(
            namespace,
            retry_after_s=retry_after_s,
            owner=refusal.owner,
            total=refusal.scope == TOTAL_SCOPE,
        )

    def _store(self, key: str, value: bytes, expires_at: float, now: float) -> None:
        """Write *value* under *key*, or raise when a budget it counts against is spent.

        A write that does not fit first drops the expired entries of the
        scope refusing it (its namespace, its owner share, or for the
        backend-wide cap every namespace), in O(expired). Nothing live is
        ever evicted.

        Raises:
            StateBackendCapacityError: The namespace, the owner's share or the
                backend-wide byte cap is still full.
        """
        self._maybe_sweep(now)
        namespace = key_namespace(key)
        size = len(key) + len(value) + ENTRY_OVERHEAD_BYTES
        refusal = self._refusing_scope(key, namespace, size)
        if refusal is not None:
            self._reclaim(refusal, now)
            refusal = self._refusing_scope(key, namespace, size)
            if refusal is not None:
                raise self._refuse(namespace, refusal, self._reclaim(refusal, now), now)
        existing = self._kv.get(key)
        if existing is not None and existing.expires_at == expires_at:
            # Same deadline (a counter increment): update in place, so the
            # expiry index gains no superseded item.
            self._resize(key, namespace, existing, size)
            existing.value = value
            return
        if existing is not None:
            self._delete(key)
        self._seq += 1
        entry = _Entry(value=value, expires_at=expires_at, size=size, seq=self._seq)
        self._kv[key] = entry
        self._total_bytes += size
        scopes = [self._usage.setdefault(namespace, _Usage())]
        owner_key = self._owner_key(key, namespace)
        if owner_key is not None:
            scopes.append(self._owner_usage.setdefault(owner_key, _Usage()))
        for usage in scopes:
            usage.entries += 1
            usage.bytes += size
            self._index(usage, entry, key)

    def _resize(self, key: str, namespace: str, entry: _Entry, size: int) -> None:
        delta = size - entry.size
        entry.size = size
        self._total_bytes += delta
        self._usage[namespace].bytes += delta
        owner_key = self._owner_key(key, namespace)
        if owner_key is not None:
            self._owner_usage[owner_key].bytes += delta

    async def get(self, key: str) -> bytes | None:
        entry = self._live_entry(key)
        return entry.value if entry is not None else None

    async def set(self, key: str, value: bytes, *, ttl_s: float) -> None:
        now = self._clock()
        self._store(key, value, now + ttl_s, now)

    async def set_if_absent(self, key: str, value: bytes, *, ttl_s: float) -> bool:
        if self._live_entry(key) is not None:
            return False
        now = self._clock()
        self._store(key, value, now + ttl_s, now)
        return True

    async def delete_if_equals(self, key: str, expected: bytes) -> bool:
        # No await between the read and the delete, so the compare-and-delete
        # is atomic on the single-threaded event loop.
        entry = self._live_entry(key)
        if entry is None or entry.value != expected:
            return False
        self._delete(key)
        return True

    async def incr_with_ttl(self, key: str, *, ttl_s: float, amount: int = 1) -> int:
        # The fresh-window TTL reset below (and the redis backend's value == amount
        # heuristic) assumes a strictly positive increment; a zero/negative amount
        # would let a counter silently re-arm or go backwards, so reject it.
        if amount <= 0:
            raise ValueError(f"incr_with_ttl amount must be positive, got {amount}")
        now = self._clock()
        entry = self._live_entry(key)
        current = int(entry.value) if entry is not None else 0
        new_value = current + amount
        # A fresh window resets the TTL; an existing counter keeps its deadline
        # so a sliding burst can't indefinitely extend the window.
        expires_at = entry.expires_at if entry is not None else now + ttl_s
        self._store(key, str(new_value).encode(), expires_at, now)
        return new_value

    async def token_bucket(
        self, key: str, *, rate: float, burst: int, cost: int = 1
    ) -> RateLimitDecision:
        now = self._clock()
        self._maybe_sweep(now)
        bucket = self._buckets.get(key)
        if bucket is None:
            # Starts unindexed; ``_reindex_bucket`` below indexes it.
            bucket = _Bucket(tokens=float(burst), updated_at=now, full_at=float("-inf"))
            self._buckets[key] = bucket

        # Refill by elapsed time * rate, capped at burst.
        elapsed = max(0.0, now - bucket.updated_at)
        bucket.tokens = min(float(burst), bucket.tokens + elapsed * rate)
        bucket.updated_at = now

        allowed = bucket.tokens >= cost
        if allowed:
            bucket.tokens -= cost
        self._reindex_bucket(key, bucket, _full_at(bucket, rate=rate, burst=burst))
        if allowed:
            return RateLimitDecision(
                allowed=True,
                remaining=int(bucket.tokens),
                retry_after_s=0.0,
                limit=burst,
            )

        deficit = cost - bucket.tokens
        retry_after = deficit / rate if rate > 0 else float("inf")
        return RateLimitDecision(
            allowed=False,
            remaining=int(bucket.tokens),
            retry_after_s=retry_after,
            limit=burst,
        )

    def _reindex_bucket(self, key: str, bucket: _Bucket, full_at: float) -> None:
        if full_at == bucket.full_at:
            return
        bucket.full_at = full_at
        heapq.heappush(self._bucket_index, (full_at, key))
        if len(self._bucket_index) > 2 * len(self._buckets) + _INDEX_SLACK:
            # Amortised like the key/value index: drop superseded items.
            self._bucket_index = [(b.full_at, k) for k, b in self._buckets.items()]
            heapq.heapify(self._bucket_index)

    async def aclose(self) -> None:
        """No-op: the memory backend holds no external resources."""
