"""Broker execute request/response models.

``ExecuteRequestContext`` is defined in ``shared/broker/schemas.py`` (it is part of
the :class:`~jentic_one.shared.broker.broker.Broker` seam contract) and re-exported
here so existing broker-internal imports keep working unchanged. The
``AsyncQueuedResponse*`` models are web-response bodies specific to the broker
surface and stay here.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

from jentic_one.shared.broker.schemas import ExecuteRequestContext

__all__ = [
    "AsyncQueuedResponse",
    "AsyncQueuedResponseLinks",
    "ExecuteRequestContext",
    "HeldExecutionResponse",
]


class AsyncQueuedResponseLinks(BaseModel):
    """HAL-style links for the async response."""

    self_link: str = Field(serialization_alias="self")


class AsyncQueuedResponse(BaseModel):
    """Response body for 202 async-queued executions."""

    job_id: str
    links: AsyncQueuedResponseLinks = Field(serialization_alias="_links")


class HeldExecutionResponse(AsyncQueuedResponse):
    """Response body for a 202 held-for-approval execution.

    ``state`` is always ``"held"`` — disambiguates from a plain async 202.
    ``approval_id`` is the ``execution_approvals`` row primary key so the agent
    can correlate a later ``get_execution_result`` call back to this hold.
    ``review_url`` is the admin review page URL; populated once the approval
    surface (phase 4) is wired; None until then.
    """

    state: Literal["held"] = "held"
    approval_id: str
    review_url: str | None = None
    # Structured guidance for the agent: instruction (human-readable step) and
    # parameters (job_id, approval_id, review_url) so the agent can relay the
    # approval context to the operator and poll for completion.
    agent_directive: dict[str, Any] | None = None
