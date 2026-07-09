"""Broker execute request/response models.

``ExecuteRequestContext`` is defined in ``shared/broker/schemas.py`` (it is part of
the :class:`~jentic_one.shared.broker.broker.Broker` seam contract) and re-exported
here so existing broker-internal imports keep working unchanged. The
``AsyncQueuedResponse*`` models are web-response bodies specific to the broker
surface and stay here.
"""

from __future__ import annotations

from pydantic import BaseModel, Field

from jentic_one.shared.broker.schemas import ExecuteRequestContext

__all__ = [
    "AsyncQueuedResponse",
    "AsyncQueuedResponseLinks",
    "ExecuteRequestContext",
]


class AsyncQueuedResponseLinks(BaseModel):
    """HAL-style links for the async response."""

    self_link: str = Field(serialization_alias="self")


class AsyncQueuedResponse(BaseModel):
    """Response body for 202 async-queued executions."""

    job_id: str
    links: AsyncQueuedResponseLinks = Field(serialization_alias="_links")
