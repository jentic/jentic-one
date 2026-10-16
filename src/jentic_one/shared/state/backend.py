"""Role-segregated shared-state protocols and the in-process memory backend.

The broker (and other surfaces later) need a small set of cross-instance state
operations: read-mostly caches, a shared token-bucket rate limiter, atomic
counters for circuit state, an atomic claim primitive for idempotency, and a
one-round-trip windowed counter for the repeated-failure gate.

Rather than a single fat interface that every consumer depends on, the surface
is split into **narrow, role-specific** protocols (ISP): a rate limiter
should not see ``set_if_absent`` and a cache should not see the Lua
``token_bucket``. The concrete backends (:class:`MemoryStateBackend` here,
``RedisStateBackend`` in :mod:`jentic_one.shared.state.redis`) implement every
role; the factory builds a single instance per process. Consumers, however,
type their dependency on the roles they need.
"""

from __future__ import annotations

import time
from collections.abc import Callable, Sequence
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
class WindowCounterStore(Protocol):
    """Windowed-counter role: several increments and reads in one atomic step.

    One call (one round trip on Redis) increments every key in *incr_keys* by one
    — setting ``ttl_s`` only when it creates a counter, like
    :meth:`AtomicStore.incr_with_ttl` — then reads every key in *read_keys*. It
    returns the new counts and the read values in the order given.
    """

    async def incr_and_read(
        self, incr_keys: Sequence[str], read_keys: Sequence[str], *, ttl_s: float
    ) -> tuple[list[int], list[bytes | None]]: ...


@runtime_checkable
class SharedStateBackend(KeyValueStore, RateLimitStore, AtomicStore, WindowCounterStore, Protocol):
    """Convenience union of all the roles.

    Used **only** at the composition root, where the factory builds one concrete
    backend that fulfils every role. No consumer should depend on this union —
    inject the narrow role (``KeyValueStore`` / ``RateLimitStore`` /
    ``AtomicStore``) instead.
    """


class StateCapacityError(RuntimeError):
    """A bounded :class:`MemoryStateBackend` has no room for a new live key."""


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
    """In-process implementation of every shared-state role.

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

    ``max_entries`` optionally bounds the key-value/counter map (token buckets
    are not counted). Creating a new key on a full map first sweeps expired
    entries; if the map is still full the write raises
    :class:`StateCapacityError` rather than evicting a live entry, so a caller
    whose correctness depends on a counter never silently loses one — it sees
    the error and falls back. ``None`` (the default) leaves the map unbounded.
    """

    def __init__(
        self,
        *,
        clock: Callable[[], float] = time.monotonic,
        max_entries: int | None = None,
    ) -> None:
        if max_entries is not None and max_entries < 1:
            raise ValueError(f"max_entries must be positive, got {max_entries}")
        self._clock = clock
        self._max_entries = max_entries
        # Lower bound on the earliest deadline in the map: until the clock passes
        # it a sweep cannot free anything, so a full map rejects new keys in O(1)
        # instead of rescanning every entry on every write.
        self._next_sweep_at = float("inf")
        self._kv: dict[str, _Entry] = {}
        self._buckets: dict[str, _Bucket] = {}

    def _reserve(self, key: str) -> None:
        """Ensure there is room to create *key*, or raise :class:`StateCapacityError`."""
        if self._max_entries is None or key in self._kv or len(self._kv) < self._max_entries:
            return
        now = self._clock()
        if now >= self._next_sweep_at:
            for stale in [k for k, e in self._kv.items() if e.expires_at <= now]:
                del self._kv[stale]
            self._next_sweep_at = min(
                (e.expires_at for e in self._kv.values()), default=float("inf")
            )
        if len(self._kv) >= self._max_entries:
            raise StateCapacityError(
                f"memory state backend is full ({self._max_entries} live entries)"
            )

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
        self._reserve(key)
        self._store(key, value, self._clock() + ttl_s)

    async def set_if_absent(self, key: str, value: bytes, *, ttl_s: float) -> bool:
        if self._live_entry(key) is not None:
            return False
        self._reserve(key)
        self._store(key, value, self._clock() + ttl_s)
        return True

    def _store(self, key: str, value: bytes, expires_at: float) -> None:
        self._kv[key] = _Entry(value=value, expires_at=expires_at)
        self._next_sweep_at = min(self._next_sweep_at, expires_at)

    async def incr_with_ttl(self, key: str, *, ttl_s: float, amount: int = 1) -> int:
        # The fresh-window TTL reset below (and the redis backend's value == amount
        # heuristic) assumes a strictly positive increment; a zero/negative amount
        # would let a counter silently re-arm or go backwards, so reject it.
        if amount <= 0:
            raise ValueError(f"incr_with_ttl amount must be positive, got {amount}")
        return self._incr(key, ttl_s=ttl_s, amount=amount)

    async def incr_and_read(
        self, incr_keys: Sequence[str], read_keys: Sequence[str], *, ttl_s: float
    ) -> tuple[list[int], list[bytes | None]]:
        # No await between the steps, so the batch is atomic on the event loop.
        counts = [self._incr(key, ttl_s=ttl_s, amount=1) for key in incr_keys]
        reads = [
            entry.value if (entry := self._live_entry(key)) is not None else None
            for key in read_keys
        ]
        return counts, reads

    def _incr(self, key: str, *, ttl_s: float, amount: int) -> int:
        now = self._clock()
        entry = self._live_entry(key)
        if entry is None:
            self._reserve(key)
        current = int(entry.value) if entry is not None else 0
        new_value = current + amount
        # A fresh window resets the TTL; an existing counter keeps its deadline
        # so a sliding burst can't indefinitely extend the window.
        expires_at = entry.expires_at if entry is not None else now + ttl_s
        self._store(key, str(new_value).encode(), expires_at)
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
