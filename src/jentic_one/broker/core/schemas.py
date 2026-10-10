"""Broker execute request/response models.

``ExecuteRequestContext`` is defined in ``shared/broker/schemas.py`` (it is part of
the :class:`~jentic_one.shared.broker.broker.Broker` seam contract) and re-exported
here so existing broker-internal imports keep working unchanged. The
``AsyncQueuedResponse*`` models are web-response bodies specific to the broker
surface and stay here.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

from jentic_one.shared.broker.schemas import ExecuteRequestContext

__all__ = [
    "AsyncQueuedResponse",
    "AsyncQueuedResponseLinks",
    "ExecuteRequestContext",
    "HeldApprovalResponse",
    "HeldExecutionLinks",
    "HeldExecutionResponse",
]


class AsyncQueuedResponseLinks(BaseModel):
    """HAL-style links for the async response."""

    self_link: str = Field(serialization_alias="self")


class AsyncQueuedResponse(BaseModel):
    """Response body for 202 async-queued executions."""

    job_id: str
    links: AsyncQueuedResponseLinks = Field(serialization_alias="_links")


class HeldApprovalResponse(BaseModel):
    """The approval a held execution waits on."""

    id: str = Field(description="Approval id (`exap_…`).")
    review_url: str = Field(
        description="Review page a signed-in reviewer opens to approve or deny the call."
    )
    expires_at: datetime = Field(
        description="When the approval expires undecided and the job fails."
    )


class HeldExecutionLinks(AsyncQueuedResponseLinks):
    """Links on a held execution: the job to poll and the route that abandons it."""

    withdraw: str = Field(
        description="`POST` here to withdraw the approval and cancel the held job."
    )


class HeldExecutionResponse(AsyncQueuedResponse):
    """202 body for an execution held for human approval.

    Extends the async-queued shape, so a client reading only ``job_id`` and
    ``_links.self`` polls it exactly like any other async execution.
    """

    links: HeldExecutionLinks = Field(serialization_alias="_links")
    status: Literal["held"] = "held"
    approval: HeldApprovalResponse
    agent_directive: str
