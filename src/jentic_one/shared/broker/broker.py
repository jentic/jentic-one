"""Neutral Broker seam: the single behavioral interface the data plane calls.

Lives in ``shared/broker`` (the arch-neutral seam package) so the broker surface
and any downstream implementation depend on the Protocol, not the concrete
pipeline. The default implementation is ``DefaultBroker``
(``broker/default_broker.py``); a downstream package can inject its own
implementation via ``AppContainer`` without importing broker internals.

The signature mirrors ``BrokerExecutionPipeline.execute`` exactly, over the shared
:mod:`jentic_one.shared.broker.execution` value objects. Signature drift between an
implementation and this contract is caught by ``BaseBrokerComplianceTest``
(``jentic_one.testing``); Protocols do not check method signatures themselves.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from jentic_one.shared.broker.execution import ExecutionContext, ExecutionOutcome, RunnerRequest


@runtime_checkable
class Broker(Protocol):
    """Executes a resolved upstream call end-to-end (transport + post-response).

    One behavioral entry point — the sync router awaits it and adapts the outcome
    to a FastAPI ``Response``; the async worker runs it and persists the record.
    Neither re-implements the steps. An implementation may wrap or replace the
    default (e.g. a wrapper delegating to a ``DefaultBroker`` for the standard
    path) as long as it honors this contract.

    An injected broker **owns its own transport and resilience** (circuit
    breaking, connection pooling, retries, bulkheads, timeouts): the built-in
    resilience stack is applied by the runner that wraps the *default* broker, and
    both callers use an injected ``Broker`` verbatim. To keep the built-in stack,
    wrap a ``DefaultBroker`` and delegate to it.
    """

    async def execute(
        self, request: RunnerRequest, context: ExecutionContext
    ) -> ExecutionOutcome: ...
