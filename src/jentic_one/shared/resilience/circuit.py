"""Per-upstream circuit breaker — a policy layer over an ``AtomicStore``.

The breaker keeps two atomic counters per ``(host, window)`` — total calls and
failures — and an "open" latch keyed per host. When a window accumulates enough
calls and the failure ratio crosses the threshold, the next ``record`` opens the
latch with the cooldown as its TTL, so the latch self-heals (Redis/​memory expiry
is the half-open transition — the first call after expiry probes the upstream).

State lives entirely in the ``AtomicStore`` (memory ⇒ per-instance, Redis ⇒
cluster-wide). This class is dependency-free policy: ``allow`` reads the latch,
``record`` updates counters and trips the latch.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol, runtime_checkable

import structlog

from jentic_one.shared.state.backend import StateBackendCapacityError

_logger = structlog.get_logger(__name__)

_OPEN_PREFIX = "cb:open:"
_TOTAL_PREFIX = "cb:total:"
_FAIL_PREFIX = "cb:fail:"
_OPEN_VALUE = b"1"


@runtime_checkable
class CircuitStateStore(Protocol):
    """The two state roles the breaker needs: read the latch + atomic counters.

    Both concrete backends (``MemoryStateBackend`` / ``RedisStateBackend``)
    satisfy this; it is the intersection of ``KeyValueStore.get`` and the
    ``AtomicStore`` writes, so the breaker doesn't depend on the full union.
    """

    async def get(self, key: str) -> bytes | None: ...

    async def incr_with_ttl(self, key: str, *, ttl_s: float, amount: int = 1) -> int: ...

    async def set_if_absent(self, key: str, value: bytes, *, ttl_s: float) -> bool: ...


class CircuitState(StrEnum):
    """The two states a caller observes (half-open is the latch-expiry probe)."""

    CLOSED = "closed"
    OPEN = "open"


@dataclass(frozen=True, slots=True)
class CircuitDecision:
    """An ``allow`` verdict. ``retry_after_s`` is populated only when open."""

    state: CircuitState
    allowed: bool
    retry_after_s: int


class CircuitBreaker:
    """Rolling-window failure-ratio breaker keyed per upstream host."""

    def __init__(
        self,
        store: CircuitStateStore,
        *,
        failure_ratio: float,
        min_calls: int,
        window_s: int,
        cooldown_s: int,
    ) -> None:
        self._store = store
        self._failure_ratio = failure_ratio
        self._min_calls = min_calls
        self._window_s = window_s
        self._cooldown_s = cooldown_s

    async def allow(self, host: str) -> CircuitDecision:
        """Return whether a call to ``host`` may proceed (latch closed)."""
        open_latch = await self._store.get(f"{_OPEN_PREFIX}{host}")
        if open_latch is not None:
            return CircuitDecision(
                state=CircuitState.OPEN, allowed=False, retry_after_s=self._cooldown_s
            )
        return CircuitDecision(state=CircuitState.CLOSED, allowed=True, retry_after_s=0)

    async def record(self, host: str, *, ok: bool) -> None:
        """Record a call outcome; trip the latch if the window crosses threshold.

        Both counters share the window TTL: a fresh window resets the deadline,
        an in-window increment keeps it, so the ratio is always over the same
        rolling window. The latch is set with ``set_if_absent`` so concurrent
        trippers don't extend an already-open circuit's cooldown.

        A counter or latch write the store refuses (its ``cb`` namespace is
        full) skips this outcome and is logged. It runs after the upstream call
        has already completed, so failing the request would discard a real
        response. The breaker is an availability guard, not a correctness one:
        while the namespace is full it may trip late or not at all.
        """
        try:
            await self._record(host, ok=ok)
        except StateBackendCapacityError:
            _logger.warning("circuit_breaker_record_skipped", host=host, reason="store_full")

    async def _record(self, host: str, *, ok: bool) -> None:
        total = await self._store.incr_with_ttl(f"{_TOTAL_PREFIX}{host}", ttl_s=self._window_s)
        failures = 0
        if not ok:
            failures = await self._store.incr_with_ttl(
                f"{_FAIL_PREFIX}{host}", ttl_s=self._window_s
            )
        if ok or total < self._min_calls:
            return
        if failures / total >= self._failure_ratio:
            try:
                await self._store.set_if_absent(
                    f"{_OPEN_PREFIX}{host}", _OPEN_VALUE, ttl_s=self._cooldown_s
                )
            except StateBackendCapacityError:
                # The window crossed the threshold but the circuit cannot open:
                # calls to this host keep flowing until the namespace frees up.
                _logger.warning(
                    "circuit_breaker_record_skipped", host=host, reason="open_latch_refused"
                )


__all__ = ["CircuitBreaker", "CircuitDecision", "CircuitState"]
