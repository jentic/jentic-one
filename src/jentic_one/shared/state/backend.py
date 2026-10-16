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
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Protocol, runtime_checkable

import structlog

_logger = structlog.get_logger(__name__)

# Default bounds for :class:`MemoryStateBackend`. A sweep walks every entry, so
# it runs at most once per interval (on a write), not per call.
DEFAULT_MEMORY_MAX_ENTRIES = 100_000
DEFAULT_MEMORY_SWEEP_INTERVAL_S = 30.0


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
    """A stored value with its monotonic-clock expiry deadline."""

    value: bytes
    expires_at: float


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


class MemoryStateBackend:
    """In-process implementation of all three state roles.

    Backed by plain dicts with monotonic-clock TTL eviction. Scope is
    per-process, which is fine behind autoscaling for most installs; the Redis
    backend exists for cross-instance coordination.

    A ``clock`` callable (defaulting to :func:`time.monotonic`) can be injected
    to make TTL and token-bucket refill behaviour deterministic in tests.

    Memory stays bounded for keys that are written once and never read again
    (JWT-bearer ``jti`` replay records, idempotency claims):

    * an expired entry is dropped when it is read, and every write at least
      ``sweep_interval_s`` after the last sweep drops every expired entry and
      every token bucket that has refilled to full;
    * a write that would take the key/value store past ``max_entries`` sweeps
      first and, if every remaining entry is still live, evicts the entries
      closest to expiry (a tenth of the bound at a time). That trades the last seconds of those
      records' TTL for a hard memory ceiling, and logs a warning — a process
      that reaches it needs a larger bound or the redis backend.
    """

    def __init__(
        self,
        *,
        clock: Callable[[], float] = time.monotonic,
        max_entries: int = DEFAULT_MEMORY_MAX_ENTRIES,
        sweep_interval_s: float = DEFAULT_MEMORY_SWEEP_INTERVAL_S,
    ) -> None:
        if max_entries < 1:
            raise ValueError(f"max_entries must be positive, got {max_entries}")
        self._clock = clock
        self._max_entries = max_entries
        self._sweep_interval_s = sweep_interval_s
        self._kv: dict[str, _Entry] = {}
        self._buckets: dict[str, _Bucket] = {}
        self._next_sweep_at = clock() + sweep_interval_s

    def _live_entry(self, key: str) -> _Entry | None:
        """Return the entry for *key* if present and not expired, else evict it."""
        entry = self._kv.get(key)
        if entry is None:
            return None
        if entry.expires_at <= self._clock():
            del self._kv[key]
            return None
        return entry

    def _sweep(self, now: float) -> None:
        """Drop every expired entry and every bucket that has refilled to full."""
        self._kv = {k: e for k, e in self._kv.items() if e.expires_at > now}
        self._buckets = {k: b for k, b in self._buckets.items() if b.full_at > now}
        self._next_sweep_at = now + self._sweep_interval_s

    def _maybe_sweep(self, now: float) -> None:
        if now >= self._next_sweep_at:
            self._sweep(now)

    def _store(self, key: str, entry: _Entry, now: float) -> None:
        """Write *entry*, sweeping on schedule and keeping the store within ``max_entries``."""
        self._maybe_sweep(now)
        if key not in self._kv and len(self._kv) >= self._max_entries:
            self._sweep(now)
            overflow = len(self._kv) - self._max_entries + 1
            if overflow > 0:
                # Free a tenth of the bound at once so a sustained flood pays
                # the O(n) selection once per batch, not on every write.
                overflow = max(overflow, self._max_entries // 10)
                soonest = heapq.nsmallest(
                    overflow, self._kv.items(), key=lambda item: item[1].expires_at
                )
                for evicted, _ in soonest:
                    del self._kv[evicted]
                _logger.warning(
                    "memory_state_backend_capacity_evicted",
                    evicted=overflow,
                    max_entries=self._max_entries,
                    detail=(
                        "the in-process state backend is full of live entries; the "
                        "ones closest to expiry were dropped early. Use the redis "
                        "backend for this load."
                    ),
                )
        self._kv[key] = entry

    async def get(self, key: str) -> bytes | None:
        entry = self._live_entry(key)
        return entry.value if entry is not None else None

    async def set(self, key: str, value: bytes, *, ttl_s: float) -> None:
        now = self._clock()
        self._store(key, _Entry(value=value, expires_at=now + ttl_s), now)

    async def set_if_absent(self, key: str, value: bytes, *, ttl_s: float) -> bool:
        if self._live_entry(key) is not None:
            return False
        now = self._clock()
        self._store(key, _Entry(value=value, expires_at=now + ttl_s), now)
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
        self._store(key, _Entry(value=str(new_value).encode(), expires_at=expires_at), now)
        return new_value

    async def token_bucket(
        self, key: str, *, rate: float, burst: int, cost: int = 1
    ) -> RateLimitDecision:
        now = self._clock()
        self._maybe_sweep(now)
        bucket = self._buckets.get(key)
        if bucket is None:
            bucket = _Bucket(tokens=float(burst), updated_at=now, full_at=now)
            self._buckets[key] = bucket

        # Refill by elapsed time * rate, capped at burst.
        elapsed = max(0.0, now - bucket.updated_at)
        bucket.tokens = min(float(burst), bucket.tokens + elapsed * rate)
        bucket.updated_at = now

        if bucket.tokens >= cost:
            bucket.tokens -= cost
            bucket.full_at = _full_at(bucket, rate=rate, burst=burst)
            return RateLimitDecision(
                allowed=True,
                remaining=int(bucket.tokens),
                retry_after_s=0.0,
                limit=burst,
            )

        bucket.full_at = _full_at(bucket, rate=rate, burst=burst)
        deficit = cost - bucket.tokens
        retry_after = deficit / rate if rate > 0 else float("inf")
        return RateLimitDecision(
            allowed=False,
            remaining=int(bucket.tokens),
            retry_after_s=retry_after,
            limit=burst,
        )

    async def aclose(self) -> None:
        """No-op: the memory backend holds no external resources."""
