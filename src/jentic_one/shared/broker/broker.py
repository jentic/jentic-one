"""Neutral Broker seam: the behavioral interface the data plane calls.

Lives in ``shared/broker`` (the arch-neutral seam package) so the broker surface
and any downstream implementation depend on the Protocol, not the concrete
pipeline. The default implementation is ``DefaultBroker``
(``broker/default_broker.py``); a downstream package can inject its own
implementation via ``AppContainer`` without importing broker internals.

Two behavioral entry points, over the shared
:mod:`jentic_one.shared.broker.execution` / :mod:`jentic_one.shared.broker.schemas`
value objects:

* ``execute`` — the buffered path (mirrors ``BrokerExecutionPipeline.execute``);
  the sync router adapts the outcome to a ``Response`` and the async worker
  persists the record.
* ``execute_streaming`` — the sync streaming passthrough (mirrors what
  ``open_streaming_response`` needs); returns a ready ``Response`` streaming the
  upstream body. Routing streaming through the seam means an injected broker's
  controls are not bypassed on non-idempotent streaming traffic.

Signature drift between an implementation and this contract is caught by
``BaseBrokerComplianceTest`` (``jentic_one.testing``); Protocols do not check
method signatures themselves.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Protocol, runtime_checkable

from fastapi import Response

from jentic_one.shared.broker.execution import (
    ExecutionContext,
    ExecutionOutcome,
    RunnerRequest,
    StreamingOutcome,
    StreamingUpstreamRunner,
)
from jentic_one.shared.broker.schemas import ExecuteRequestContext


@runtime_checkable
class Broker(Protocol):
    """Executes a resolved upstream call end-to-end (transport + post-response).

    The sync router awaits ``execute`` (buffered) or ``execute_streaming``
    (passthrough) and returns the ``Response``; the async worker runs ``execute``
    and persists the record. Neither re-implements the steps. An implementation
    may wrap or replace the default (e.g. a wrapper delegating to a
    ``DefaultBroker`` for the standard path) as long as it honors this contract.

    An injected broker **owns its own transport and resilience** (circuit
    breaking, connection pooling, retries, bulkheads, timeouts): the built-in
    resilience stack is applied by the runner that wraps the *default* broker, and
    both callers use an injected ``Broker`` verbatim. To keep the built-in stack,
    wrap a ``DefaultBroker`` and delegate to it.
    """

    async def execute(
        self, request: RunnerRequest, context: ExecutionContext
    ) -> ExecutionOutcome: ...

    async def execute_streaming(
        self,
        runner: StreamingUpstreamRunner,
        request: RunnerRequest,
        ctx_req: ExecuteRequestContext,
        execution_id: str,
        *,
        transfer_deadline_s: float,
        background_callback: Callable[[StreamingOutcome], Awaitable[None]] | None = None,
    ) -> Response: ...
