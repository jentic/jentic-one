"""Default Broker: adapts BrokerExecutionPipeline to the Broker seam.

Holds no new behavior — it exists so the broker surface depends on the
:class:`~jentic_one.shared.broker.broker.Broker` protocol (not the concrete
pipeline), and so a downstream package can wrap or replace it (e.g. a wrapper
that delegates to a ``DefaultBroker`` for the standard path).
"""

from __future__ import annotations

from jentic_one.broker.services.execution.pipeline import BrokerExecutionPipeline
from jentic_one.shared.broker.execution import ExecutionContext, ExecutionOutcome, RunnerRequest


class DefaultBroker:
    """Thin adapter wrapping the existing execution pipeline (implements ``Broker``)."""

    def __init__(self, pipeline: BrokerExecutionPipeline) -> None:
        self._pipeline = pipeline

    async def execute(self, request: RunnerRequest, context: ExecutionContext) -> ExecutionOutcome:
        return await self._pipeline.execute(request, context)
