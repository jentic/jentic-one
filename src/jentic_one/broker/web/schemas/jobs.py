"""Broker job response schemas — wire-compatible with the control-plane job representation."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field

from jentic_one.shared.web import openapi_examples as ex


class JobLinksResponse(BaseModel):
    """Hypermedia links for a broker-polled job."""

    self_link: str = Field(
        serialization_alias="self",
        description="URL of this job on the Broker.",
        examples=[f"https://broker.your-instance.example/jobs/{ex.JOB_ID}"],
    )
    result: str | None = Field(
        default=None,
        description="URL of the job result when an unexpired result is stored; null otherwise.",
        examples=[f"https://broker.your-instance.example/jobs/{ex.JOB_ID}/result"],
    )
    execution: str | None = Field(
        default=None,
        description="URL of the related execution record on the control plane; null if none.",
        examples=[f"https://control.your-instance.example/executions/{ex.EXECUTION_ID}"],
    )


class JobResponse(BaseModel):
    """Job representation returned by ``GET /jobs/{job_id}`` on the Broker."""

    job_id: str = Field(description="Unique ID of the job.", examples=[ex.JOB_ID])
    kind: str = Field(
        description="Kind of job (`execution` for Broker-enqueued jobs).",
        examples=["execution"],
    )
    status: str = Field(
        description=(
            "Lifecycle state: `queued`, `running`, `completed`, `failed`, `cancelled` "
            "or `dead_letter`."
        ),
        examples=["completed"],
    )
    execution_id: str | None = Field(
        default=None,
        description="ID of the execution record linked to the job, if any.",
        examples=[ex.EXECUTION_ID],
    )
    error: str | None = Field(
        default=None,
        description="Error message recorded for the job; null if none.",
        examples=["Upstream returned HTTP 502 Bad Gateway"],
    )
    created_at: datetime = Field(
        description="When the job was created (RFC 3339).", examples=["2026-01-15T09:30:00Z"]
    )
    updated_at: datetime | None = Field(
        default=None,
        description="When the job was last updated (RFC 3339); null if never updated.",
        examples=["2026-01-15T09:30:05Z"],
    )
    actor_id: str | None = Field(
        default=None,
        description="Subject that enqueued the job.",
        examples=[ex.AGENT_ID],
    )
    actor_type: str = Field(
        description="Kind of actor that enqueued the job (`agent` or `user`).",
        examples=["agent"],
    )
    links: JobLinksResponse = Field(serialization_alias="_links")
