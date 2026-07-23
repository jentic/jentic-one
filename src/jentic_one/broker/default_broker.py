"""Default Broker: adapts BrokerExecutionPipeline to the Broker seam.

Holds no new behavior — it exists so the broker surface depends on the
:class:`~jentic_one.shared.broker.broker.Broker` protocol (not the concrete
pipeline), and so a downstream package can wrap or replace it (e.g. a wrapper
that delegates to a ``DefaultBroker`` for the standard path).
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable

from fastapi import Response

from jentic_one.broker.services.execution.pipeline import BrokerExecutionPipeline
from jentic_one.broker.web.streaming import open_streaming_response
from jentic_one.shared.broker.execution import (
    ExecutionContext,
    ExecutionOutcome,
    RunnerRequest,
    StreamingOutcome,
    StreamingUpstreamRunner,
)
from jentic_one.shared.broker.schemas import ExecuteRequestContext


class DefaultBroker:
    """Thin adapter wrapping the existing execution pipeline (implements ``Broker``)."""

    def __init__(self, pipeline: BrokerExecutionPipeline) -> None:
        self._pipeline = pipeline

    async def execute(self, request: RunnerRequest, context: ExecutionContext) -> ExecutionOutcome:
        return await self._pipeline.execute(request, context)

    async def execute_streaming(
        self,
        runner: StreamingUpstreamRunner,
        request: RunnerRequest,
        ctx_req: ExecuteRequestContext,
        execution_id: str,
        *,
        transfer_deadline_s: float,
        background_callback: Callable[[StreamingOutcome], Awaitable[None]] | None = None,
    ) -> Response:
        return await open_streaming_response(
            runner,
            request,
            ctx_req,
            execution_id,
            transfer_deadline_s=transfer_deadline_s,
            background_callback=background_callback,
        )
