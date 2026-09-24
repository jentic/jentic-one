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

import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Protocol, runtime_checkable


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
    """Token-bucket state: current token count and last-refill timestamp."""

    tokens: float
    updated_at: float


class MemoryStateBackend:
    """In-process implementation of all three state roles.

    Backed by plain dicts with monotonic-clock TTL eviction. Scope is
    per-process, which is fine behind autoscaling for most installs; the Redis
    backend exists for cross-instance coordination.

    A ``clock`` callable (defaulting to :func:`time.monotonic`) can be injected
    to make TTL and token-bucket refill behaviour deterministic in tests.

    Eviction is **lazy**: expired entries are dropped on access (see
    :meth:`_live_entry`), so keys that are written once and never read again hold
    memory until process exit. For the per-process, autoscaled scope this targets
    that is acceptable; a periodic background sweep is a possible follow-up if a
    workload accumulates many write-only short-TTL keys.
    """

    def __init__(self, *, clock: Callable[[], float] = time.monotonic) -> None:
        self._clock = clock
        self._kv: dict[str, _Entry] = {}
        self._buckets: dict[str, _Bucket] = {}

    def _live_entry(self, key: str) -> _Entry | None:
        """Return the entry for *key* if present and not expired, else evict it."""
        entry = self._kv.get(key)
        if entry is None:
            return None
        if entry.expires_at <= self._clock():
            del self._kv[key]
            return None
        return entry

    async def get(self, key: str) -> bytes | None:
        entry = self._live_entry(key)
        return entry.value if entry is not None else None

    async def set(self, key: str, value: bytes, *, ttl_s: float) -> None:
        self._kv[key] = _Entry(value=value, expires_at=self._clock() + ttl_s)

    async def set_if_absent(self, key: str, value: bytes, *, ttl_s: float) -> bool:
        if self._live_entry(key) is not None:
            return False
        self._kv[key] = _Entry(value=value, expires_at=self._clock() + ttl_s)
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
        self._kv[key] = _Entry(value=str(new_value).encode(), expires_at=expires_at)
        return new_value

    async def token_bucket(
        self, key: str, *, rate: float, burst: int, cost: int = 1
    ) -> RateLimitDecision:
        now = self._clock()
        bucket = self._buckets.get(key)
        if bucket is None:
            bucket = _Bucket(tokens=float(burst), updated_at=now)
            self._buckets[key] = bucket

        # Refill by elapsed time * rate, capped at burst.
        elapsed = max(0.0, now - bucket.updated_at)
        bucket.tokens = min(float(burst), bucket.tokens + elapsed * rate)
        bucket.updated_at = now

        if bucket.tokens >= cost:
            bucket.tokens -= cost
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

    async def aclose(self) -> None:
        """No-op: the memory backend holds no external resources."""
