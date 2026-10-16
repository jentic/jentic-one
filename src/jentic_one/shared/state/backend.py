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
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Protocol, runtime_checkable

import structlog

_logger = structlog.get_logger(__name__)

# Default per-namespace bounds for :class:`MemoryStateBackend`'s key/value
# store. A sweep walks every entry, so it runs at most once per interval on a
# write, plus at most once per ``_FULL_SWEEP_MIN_INTERVAL_S`` when a namespace
# is full.
DEFAULT_MEMORY_MAX_ENTRIES = 100_000
DEFAULT_MEMORY_MAX_BYTES = 64 * 1024 * 1024
DEFAULT_MEMORY_SWEEP_INTERVAL_S = 30.0
_FULL_SWEEP_MIN_INTERVAL_S = 1.0
# Approximate per-entry cost on top of the key and value bytes: the dict slot,
# the key ``str`` and value ``bytes`` object headers and the ``_Entry``.
_ENTRY_OVERHEAD_BYTES = 200


class StateBackendCapacityError(Exception):
    """A write was refused because its key namespace is full of live entries.

    Raised by :class:`MemoryStateBackend` instead of evicting a live entry: an
    evicted idempotency claim or replay marker would make the next
    ``set_if_absent`` for that key succeed, so the guard would fail open.
    Callers must refuse the request that needed the write (for example with a
    503), never treat it as a fresh claim.
    """

    def __init__(self, namespace: str, *, retry_after_s: int) -> None:
        super().__init__(f"shared-state namespace {namespace!r} is full")
        self.namespace = namespace
        self.retry_after_s = retry_after_s


@dataclass(frozen=True, slots=True)
class NamespaceBudget:
    """Entry-count and approximate byte budget for one key namespace."""

    max_entries: int
    max_bytes: int


def key_namespace(key: str) -> str:
    """The namespace a key is budgeted under: its prefix up to the first ``:``."""
    return key.split(":", 1)[0] if ":" in key else ""


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
    """A stored value with its monotonic-clock expiry deadline and budgeted size."""

    value: bytes
    expires_at: float
    size: int


@dataclass(slots=True)
class _Usage:
    """Live accounting for one key namespace."""

    entries: int = 0
    bytes: int = 0


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

    The key/value store is bounded per key namespace (:func:`key_namespace`,
    the key's prefix up to its first ``:``, e.g. ``idem``, ``login-csrf``):

    * an expired entry is dropped when it is read, and every write at least
      ``sweep_interval_s`` after the last sweep drops every expired entry and
      every token bucket that has refilled to full;
    * each namespace has an entry-count budget and an approximate byte budget
      (key + value + a fixed per-entry overhead). A write that would take its
      namespace past either budget sweeps expired entries first and, if the
      namespace is still full, is refused with
      :class:`StateBackendCapacityError`. A live entry is never evicted, so a
      claim or replay marker cannot be dropped early and re-claimed.

    Budgets are per namespace, so filling one key kind (for example
    unauthenticated login CSRF nonces) refuses only writes of that kind and
    never starves idempotency claims or ``jti`` replay records. The key/value
    store's memory is therefore bounded by roughly the sum of the byte budgets
    of the namespaces in use (sizes are approximate: key + value + a fixed
    per-entry overhead). Token buckets are not budgeted: each is a fixed-size
    record kept only until it has refilled, so they are bounded by request
    rate times refill time.
    """

    def __init__(
        self,
        *,
        clock: Callable[[], float] = time.monotonic,
        max_entries: int = DEFAULT_MEMORY_MAX_ENTRIES,
        max_bytes: int = DEFAULT_MEMORY_MAX_BYTES,
        namespace_budgets: Mapping[str, NamespaceBudget] | None = None,
        sweep_interval_s: float = DEFAULT_MEMORY_SWEEP_INTERVAL_S,
    ) -> None:
        default = NamespaceBudget(max_entries=max_entries, max_bytes=max_bytes)
        budgets = dict(namespace_budgets or {})
        for name, budget in {"": default, **budgets}.items():
            if budget.max_entries < 1:
                raise ValueError(
                    f"max_entries must be positive, got {budget.max_entries} for {name!r}"
                )
            if budget.max_bytes < 1:
                raise ValueError(f"max_bytes must be positive, got {budget.max_bytes} for {name!r}")
        self._clock = clock
        self._default_budget = default
        self._budgets = budgets
        self._sweep_interval_s = sweep_interval_s
        self._retry_after_s = max(1, int(sweep_interval_s + 0.999))
        self._kv: dict[str, _Entry] = {}
        self._usage: dict[str, _Usage] = {}
        self._buckets: dict[str, _Bucket] = {}
        self._next_sweep_at = clock() + sweep_interval_s
        self._last_full_sweep_at = float("-inf")
        self._refusal_logged_at: dict[str, float] = {}

    def _budget(self, namespace: str) -> NamespaceBudget:
        return self._budgets.get(namespace, self._default_budget)

    def _delete(self, key: str) -> None:
        entry = self._kv.pop(key)
        usage = self._usage[key_namespace(key)]
        usage.entries -= 1
        usage.bytes -= entry.size

    def _live_entry(self, key: str) -> _Entry | None:
        """Return the entry for *key* if present and not expired, else drop it."""
        entry = self._kv.get(key)
        if entry is None:
            return None
        if entry.expires_at <= self._clock():
            self._delete(key)
            return None
        return entry

    def _sweep(self, now: float) -> None:
        """Drop every expired entry and every bucket that has refilled to full."""
        self._kv = {k: e for k, e in self._kv.items() if e.expires_at > now}
        usage: dict[str, _Usage] = {}
        for key, entry in self._kv.items():
            ns_usage = usage.setdefault(key_namespace(key), _Usage())
            ns_usage.entries += 1
            ns_usage.bytes += entry.size
        self._usage = usage
        self._buckets = {k: b for k, b in self._buckets.items() if b.full_at > now}
        self._next_sweep_at = now + self._sweep_interval_s

    def _maybe_sweep(self, now: float) -> None:
        if now >= self._next_sweep_at:
            self._sweep(now)

    def _fits(self, key: str, namespace: str, size: int) -> bool:
        budget = self._budget(namespace)
        usage = self._usage.get(namespace, _Usage())
        existing = self._kv.get(key)
        entries = usage.entries + (0 if existing is not None else 1)
        used_bytes = usage.bytes - (existing.size if existing is not None else 0) + size
        return entries <= budget.max_entries and used_bytes <= budget.max_bytes

    def _refuse(self, namespace: str, now: float) -> StateBackendCapacityError:
        # One warning per namespace per sweep interval: a flood of refused
        # writes must not become a flood of log lines.
        last = self._refusal_logged_at.get(namespace)
        if last is None or now - last >= self._sweep_interval_s:
            self._refusal_logged_at[namespace] = now
            budget = self._budget(namespace)
            usage = self._usage.get(namespace, _Usage())
            _logger.warning(
                "memory_state_backend_capacity_refused",
                namespace=namespace,
                entries=usage.entries,
                approx_bytes=usage.bytes,
                max_entries=budget.max_entries,
                max_bytes=budget.max_bytes,
                detail=(
                    "the in-process state backend namespace is full of live "
                    "entries, so new writes to it are refused (callers answer "
                    "503) until entries expire. Raise "
                    "broker.resilience.backend.memory budgets for this namespace, "
                    "shorten the TTL of what it stores, or use the redis backend."
                ),
            )
        return StateBackendCapacityError(namespace, retry_after_s=self._retry_after_s)

    def _store(self, key: str, value: bytes, expires_at: float, now: float) -> None:
        """Write *value* under *key*, or raise when its namespace budget is spent.

        A namespace that is full sweeps expired entries first (at most once
        per ``_FULL_SWEEP_MIN_INTERVAL_S``, so a flood of refused writes does
        not pay a full scan each). Nothing live is ever evicted.

        Raises:
            StateBackendCapacityError: The namespace is still full after the sweep.
        """
        self._maybe_sweep(now)
        namespace = key_namespace(key)
        size = len(key) + len(value) + _ENTRY_OVERHEAD_BYTES
        if not self._fits(key, namespace, size):
            if now - self._last_full_sweep_at >= _FULL_SWEEP_MIN_INTERVAL_S:
                self._last_full_sweep_at = now
                self._sweep(now)
            if not self._fits(key, namespace, size):
                raise self._refuse(namespace, now)
        if key in self._kv:
            self._delete(key)
        self._kv[key] = _Entry(value=value, expires_at=expires_at, size=size)
        usage = self._usage.setdefault(namespace, _Usage())
        usage.entries += 1
        usage.bytes += size

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
