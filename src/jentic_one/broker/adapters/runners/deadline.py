"""``DeadlineRunner`` — the overall request-deadline budget as a runner decorator.

Part of the always-on, transport-agnostic envelope: the
deadline wraps *any* ``UpstreamRunner`` rather than living as an inline branch in
the handler, so the pipeline composes it without the edge knowing it is active.

It is the **whole-call wall-clock budget**, distinct from the per-attempt
connect/read timeout the transport already enforces: a single attempt can read
within its `read_timeout_s` yet the *call* still blow its budget once the retry
loop replays attempts. The deadline sits **outermost**
in the envelope — outside the circuit breaker and (eventually) the retry loop —
so the budget bounds the entire envelope, not one hop. Exceeding it raises the
domain :class:`DeadlineExceededError` (``504``) carrying an agent directive, which
the central handler maps to ``problem+json``.

``deadline_s <= 0`` disables the budget (the call runs unbounded) so an operator
can opt out without removing the decorator from the chain.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import NoReturn

from jentic_one.broker.adapters.runners.base import (
    RunnerRequest,
    RunnerResult,
    StreamingResult,
    StreamingUpstreamRunner,
    UpstreamRunner,
)
from jentic_one.broker.core.exceptions import (
    AgentDirective,
    BrokerError,
    DeadlineExceededError,
    ErrorOrigin,
)
from jentic_one.shared.metrics import get_meter

_meter = get_meter("broker")
_deadline_exceeded_total = _meter.create_counter(
    "broker.deadline.exceeded_total",
    description="Calls aborted because the overall request deadline elapsed.",
)


def _deadline_directive(deadline_s: float) -> AgentDirective:
    """Directive for a blown deadline — wait, then retry the call.

    The broker ran out of its overall budget waiting on the upstream; the safe
    agent recovery is to back off briefly and retry (or pivot to async if the
    caller supports it), not to hammer the same slow path.
    """
    return AgentDirective(
        strategy="wait",
        parameters={"deadline_seconds": deadline_s},
        human_readable_instruction=(
            "The request exceeded the broker's overall deadline waiting on the "
            "upstream; wait briefly and retry, or issue the call asynchronously."
        ),
    )


class DeadlineRunner:
    """Wraps an ``UpstreamRunner`` with an overall wall-clock deadline budget."""

    def __init__(self, inner: UpstreamRunner, *, deadline_s: float) -> None:
        self._inner = inner
        self._deadline_s = deadline_s

    async def run(self, request: RunnerRequest) -> RunnerResult:
        if self._deadline_s <= 0:
            return await self._inner.run(request)
        try:
            async with asyncio.timeout(self._deadline_s):
                return await self._inner.run(request)
        except TimeoutError as exc:
            self._raise_deadline(exc)

    @asynccontextmanager
    async def stream(self, request: RunnerRequest) -> AsyncIterator[StreamingResult]:
        """Stream variant of :meth:`run`.

        The deadline guards **opening** the upstream stream (connect + first
        response). Once the :class:`StreamingResult` is yielded the body transfer
        is governed by the streaming guard's own whole-transfer deadline
        (``transfer_deadline_s``), not re-budgeted here — so a long but
        healthy download isn't aborted by the request-admission deadline.
        """
        if not isinstance(self._inner, StreamingUpstreamRunner):  # pragma: no cover - config guard
            raise BrokerError(
                detail="The wrapped runner does not support streaming.",
                type="streaming_unsupported",
            )
        if self._deadline_s <= 0:
            async with self._inner.stream(request) as result:
                yield result
            return
        try:
            async with asyncio.timeout(self._deadline_s):
                cm = self._inner.stream(request)
                result = await cm.__aenter__()
        except TimeoutError as exc:
            self._raise_deadline(exc)
        try:
            yield result
        finally:
            await cm.__aexit__(None, None, None)

    def _raise_deadline(self, exc: BaseException) -> NoReturn:
        _deadline_exceeded_total.add(1)
        raise DeadlineExceededError(
            detail=(f"The request exceeded the broker's {self._deadline_s:g}s overall deadline."),
            type="deadline_exceeded",
            origin=ErrorOrigin.UPSTREAM,
            directive=_deadline_directive(self._deadline_s),
        ) from exc


__all__ = ["DeadlineRunner"]
