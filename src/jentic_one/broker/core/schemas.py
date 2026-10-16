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
    "HeldExecutionAcceptedLinks",
    "HeldExecutionAcceptedResponse",
]


class AsyncQueuedResponseLinks(BaseModel):
    """HAL-style links for the async response."""

    self_link: str = Field(serialization_alias="self")


class AsyncQueuedResponse(BaseModel):
    """Response body for 202 async-queued executions."""

    status: Literal["queued"] = "queued"
    job_id: str
    links: AsyncQueuedResponseLinks = Field(serialization_alias="_links")


class HeldExecutionAcceptedLinks(BaseModel):
    """Links on a held execution: where it stands, its eventual result, and withdrawal."""

    self_link: str = Field(
        serialization_alias="self",
        description="The held execution: its decision and where the call stands.",
    )
    result: str = Field(description="The call's outcome once it has finished.")
    withdraw: str = Field(description="`POST` here to withdraw the call before it is decided.")


class HeldExecutionAcceptedResponse(BaseModel):
    """202 body for a call held for human approval.

    The call has not run. A reviewer approves or denies it on ``review_url``;
    the agent follows ``_links.self`` to see where it stands and
    ``_links.result`` for the outcome.
    """

    status: Literal["held"] = "held"
    held_execution_id: str = Field(description="Held execution id (`held_…`).")
    review_url: str = Field(
        description="Review page a signed-in reviewer opens to approve or deny the call."
    )
    expires_at: datetime = Field(description="When the call expires undecided and fails.")
    agent_directive: str
    links: HeldExecutionAcceptedLinks = Field(serialization_alias="_links")
