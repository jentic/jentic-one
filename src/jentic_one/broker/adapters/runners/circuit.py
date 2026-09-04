"""``CircuitBreakerRunner`` — the per-upstream breaker as a runner decorator.

Part of the always-on, transport-agnostic envelope: the
breaker wraps *any* ``UpstreamRunner`` rather than living as an inline branch in
the handler, so the pipeline composes it without the edge knowing it is active.
It keys on the upstream **host** (parsed from the request URL), reads the latch
before dispatch, and records the outcome (``5xx`` or transport error ⇒ failure)
after.

``observation`` mode is the safe-rollout dry run: on an open circuit it
increments ``circuit_would_block_total`` and **still calls** the upstream instead
of raising — operators watch that counter to validate thresholds before flipping
to ``blocking``.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Literal
from urllib.parse import urlparse

from jentic_one.broker.adapters.runners.base import (
    RunnerRequest,
    RunnerResult,
    StreamingResult,
    StreamingUpstreamRunner,
    UpstreamRunner,
)
from jentic_one.broker.core.exceptions import BrokerError, CircuitOpenError
from jentic_one.shared.metrics import get_meter
from jentic_one.shared.resilience.circuit import CircuitBreaker

_meter = get_meter("broker")
_circuit_open_total = _meter.create_counter(
    "broker.circuit.open_total",
    description="Calls fast-failed because the upstream circuit was open (blocking mode).",
)
_circuit_would_block_total = _meter.create_counter(
    "broker.circuit.would_block_total",
    description="Calls that an open circuit WOULD have shed in observation mode (let through).",
)

# Breaker key for a URL with no parseable host. Such URLs are malformed for an
# upstream proxy and normally fail SSRF/validation before reaching the runner;
# the sentinel keeps any that slip through from sharing a real host's circuit
# (an empty key would let unrelated malformed targets trip — or ride — one
# upstream's latch).
_NO_HOST_KEY = "<no-host>"


def _host_of(url: str) -> str:
    """The breaker key — the upstream host (``netloc``), or a sentinel if none.

    Malformed/host-less URLs key on ``_NO_HOST_KEY`` so they never collide with a
    real upstream's circuit state.
    """
    return urlparse(url).netloc or _NO_HOST_KEY


class CircuitBreakerRunner:
    """Wraps an ``UpstreamRunner`` with a per-host circuit breaker."""

    def __init__(
        self,
        inner: UpstreamRunner,
        breaker: CircuitBreaker,
        *,
        enforcement_mode: Literal["blocking", "observation"] = "blocking",
    ) -> None:
        self._inner = inner
        self._breaker = breaker
        self._blocking = enforcement_mode == "blocking"

    async def run(self, request: RunnerRequest) -> RunnerResult:
        host = _host_of(request.url)
        decision = await self._breaker.allow(host)
        if not decision.allowed:
            if self._blocking:
                _circuit_open_total.add(1, {"upstream": host})
                raise CircuitOpenError(
                    detail="Upstream circuit open; the broker is fast-failing to let it recover.",
                    type="circuit_open",
                    headers={"Retry-After": str(decision.retry_after_s)},
                )
            # observation mode: count what we *would* have shed, then proceed.
            _circuit_would_block_total.add(1, {"upstream": host})

        try:
            result = await self._inner.run(request)
        except BrokerError:
            # A transport failure (timeout, connect error) the inner runner
            # mapped to a BrokerError counts against the upstream.
            await self._breaker.record(host, ok=False)
            raise
        await self._breaker.record(host, ok=result.status_code < 500)
        return result

    @asynccontextmanager
    async def stream(self, request: RunnerRequest) -> AsyncIterator[StreamingResult]:
        """Stream variant of :meth:`run`.

        Same breaker admission as the buffered path. Recording happens on
        **headers** (the status is known when the upstream stream opens), not on
        the full body: a 5xx status or a transport failure opening the stream
        counts as a failure; a mid-stream abort *after* a healthy header is not
        re-counted here (mid-stream failure accounting is a later increment).
        """
        host = _host_of(request.url)
        decision = await self._breaker.allow(host)
        if not decision.allowed:
            if self._blocking:
                _circuit_open_total.add(1, {"upstream": host})
                raise CircuitOpenError(
                    detail="Upstream circuit open; the broker is fast-failing to let it recover.",
                    type="circuit_open",
                    headers={"Retry-After": str(decision.retry_after_s)},
                )
            _circuit_would_block_total.add(1, {"upstream": host})

        if not isinstance(self._inner, StreamingUpstreamRunner):  # pragma: no cover - config guard
            raise BrokerError(
                detail="The wrapped runner does not support streaming.",
                type="streaming_unsupported",
            )
        recorded = False
        try:
            async with self._inner.stream(request) as result:
                recorded = True
                await self._breaker.record(host, ok=result.status_code < 500)
                yield result
        except BrokerError:
            if not recorded:
                await self._breaker.record(host, ok=False)
            raise


__all__ = ["CircuitBreakerRunner"]
