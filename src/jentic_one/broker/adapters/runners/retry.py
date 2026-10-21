"""``RetryRunner`` — idempotency-aware upstream retry as a runner decorator.

Part of the composable execution envelope: retry is a
**decorator** around the breaker-wrapped transport, not an inline branch in the
handler. It sits *outside* the :class:`CircuitBreakerRunner` (every attempt flows
through the breaker, so a tripped breaker fast-fails the next attempt instead of
burning the retry budget) and *inside* the :class:`DeadlineRunner` (the overall
wall-clock budget bounds the whole loop, including the backoff sleeps).

**Retryability is gated by the failure *phase*, not just the method:**

- A connect-phase failure (``BrokerError.pre_send`` — no request bytes hit the
  wire) is safe to retry for **any** method, including ``POST``: the
  upstream cannot have acted.
- A post-send failure (read timeout, connection drop after send, or a mirrored
  ``429``/``5xx`` status) is retried **only** for idempotent HTTP methods —
  ``Idempotency-Key`` is broker-consumed for caller-level replay and stripped
  from outbound headers (``BROKER_CONSUMED_HEADERS``), so the upstream has no
  way to deduplicate a re-sent ``POST`` or ``PATCH`` (#1799).

So the gate is ``pre_send OR idempotent_method``. Backoff
is exponential with full jitter; an upstream ``Retry-After`` (or
``X-RateLimit-Reset``) on a ``429``/``503`` is honored, normalized to integer
seconds, and capped at the remaining deadline budget.

Streaming requests are **not** retried: the body has already begun transferring
when failures surface, so the stream is passed straight through to the inner
runner (its own per-attempt timeout / breaker still apply).
"""

from __future__ import annotations

import asyncio
import random
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from jentic_one.broker.adapters.runners.base import (
    RunnerRequest,
    RunnerResult,
    StreamingResult,
    StreamingUpstreamRunner,
    UpstreamRunner,
)
from jentic_one.broker.core.exceptions import BrokerError
from jentic_one.broker.core.retry_after import parse_retry_after_seconds
from jentic_one.shared.metrics import get_meter

_meter = get_meter("broker")
_retry_attempts_total = _meter.create_counter(
    "broker.retry.attempts_total",
    description="Upstream attempts retried after a transient failure.",
)
_retry_exhausted_total = _meter.create_counter(
    "broker.retry.exhausted_total",
    description="Calls that exhausted the retry budget (attempts or deadline).",
)

# Methods whose HTTP semantics make a retry safe even after the request reached
# the upstream (RFC 7231 idempotent set). POST and PATCH are excluded: the
# broker consumes Idempotency-Key for caller-level replay and strips it from
# forwarded headers, so the upstream cannot deduplicate a post-send retry
# (#1799); they retry only on pre-send failures.
_IDEMPOTENT_METHODS: frozenset[str] = frozenset(
    {"GET", "HEAD", "PUT", "DELETE", "OPTIONS", "TRACE"}
)


def _is_idempotent(request: RunnerRequest) -> bool:
    """Whether a *post-send* retry is safe for this request."""
    return request.method.upper() in _IDEMPOTENT_METHODS


class RetryRunner:
    """Wraps an ``UpstreamRunner`` with idempotency-aware, deadline-bounded retries."""

    def __init__(
        self,
        inner: UpstreamRunner,
        *,
        max_attempts: int = 3,
        base_backoff_s: float = 0.2,
        max_backoff_s: float = 5.0,
        retry_statuses: frozenset[int] | None = None,
        deadline_s: float = 0.0,
    ) -> None:
        self._inner = inner
        # At least one attempt; the loop counts the initial try as attempt 1.
        self._max_attempts = max(1, max_attempts)
        self._base_backoff_s = max(0.0, base_backoff_s)
        self._max_backoff_s = max(0.0, max_backoff_s)
        self._retry_statuses = retry_statuses or frozenset({429, 502, 503, 504})
        # The overall budget (mirrors the outer DeadlineRunner) so a backoff sleep
        # never overshoots the deadline; <= 0 means "bounded only by the outer
        # decorator" and backoff is capped at max_backoff_s.
        self._deadline_s = deadline_s

    async def run(self, request: RunnerRequest) -> RunnerResult:
        deadline = (time.monotonic() + self._deadline_s) if self._deadline_s > 0 else None
        idempotent = _is_idempotent(request)
        attempt = 0
        while True:
            attempt += 1
            try:
                result = await self._inner.run(request)
            except BrokerError as exc:
                if not self._should_retry_error(exc, idempotent=idempotent):
                    raise
                delay = self._backoff(attempt, hint=None, deadline=deadline)
                if not self._has_budget(attempt, delay, deadline):
                    _retry_exhausted_total.add(1)
                    raise
                await self._sleep(delay, attempt)
                continue

            if not self._should_retry_status(result.status_code, idempotent=idempotent):
                return result
            hint = parse_retry_after_seconds(result.headers, cap_s=self._remaining(deadline))
            delay = self._backoff(attempt, hint=hint, deadline=deadline)
            if not self._has_budget(attempt, delay, deadline):
                _retry_exhausted_total.add(1)
                return result
            await self._sleep(delay, attempt)

    @asynccontextmanager
    async def stream(self, request: RunnerRequest) -> AsyncIterator[StreamingResult]:
        """Pass streaming requests straight through — a half-sent body can't be retried."""
        if not isinstance(self._inner, StreamingUpstreamRunner):  # pragma: no cover - config guard
            raise BrokerError(
                detail="The wrapped runner does not support streaming.",
                type="streaming_unsupported",
            )
        async with self._inner.stream(request) as result:
            yield result

    def _should_retry_error(self, exc: BrokerError, *, idempotent: bool) -> bool:
        """A connect-phase error retries for any method; post-send only if idempotent."""
        return bool(exc.pre_send) or idempotent

    def _should_retry_status(self, status_code: int, *, idempotent: bool) -> bool:
        """A transient upstream status retries only when a post-send retry is safe."""
        return status_code in self._retry_statuses and idempotent

    def _backoff(self, attempt: int, *, hint: int | None, deadline: float | None) -> float:
        """Backoff seconds for the *next* sleep: honor an upstream hint, else jittered exp."""
        if hint is not None:
            base = float(hint)
        else:
            exp = self._base_backoff_s * (2 ** (attempt - 1))
            capped = min(exp, self._max_backoff_s)
            base = random.uniform(0, capped)  # full jitter
        remaining = self._remaining(deadline)
        if remaining is not None:
            base = min(base, remaining)
        return max(0.0, base)

    def _has_budget(self, attempt: int, delay: float, deadline: float | None) -> bool:
        """Whether another attempt fits within the attempt count and the deadline."""
        if attempt >= self._max_attempts:
            return False
        remaining = self._remaining(deadline)
        return not (remaining is not None and delay >= remaining)

    def _remaining(self, deadline: float | None) -> float | None:
        if deadline is None:
            return None
        return max(0.0, deadline - time.monotonic())

    async def _sleep(self, delay: float, attempt: int) -> None:
        _retry_attempts_total.add(1)
        if delay > 0:
            await asyncio.sleep(delay)


__all__ = ["RetryRunner"]
