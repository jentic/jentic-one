"""Unit tests for JOB_FAILED_PERMANENTLY event emission on dead-letter."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, patch

import pytest

from jentic_one.admin.core.schema.jobs import Job
from jentic_one.shared.config import WorkerConfig
from jentic_one.shared.db.session import DatabaseSession
from jentic_one.shared.jobs.handlers import JobHandlerRegistry, JobResultPayload
from jentic_one.shared.jobs.worker import WorkerLoop
from jentic_one.shared.models.events import EVENT_TYPE_SEVERITIES, EventType
from jentic_one.shared.models.jobs import JobKind, JobStatus


class _FailingHandler:
    async def execute(
        self,
        job_id: str,
        db: Any,
        *,
        payload: dict[str, Any] | None = None,
        created_by: str | None = None,
        actor_type: str | None = None,
    ) -> JobResultPayload:
        raise RuntimeError("permanent failure")


async def _claimed_import_job(db: DatabaseSession, worker: WorkerLoop, *, attempts: int) -> Any:
    """Insert a queued import job and claim it with ``worker``."""
    async with db.transaction() as session:
        session.add(
            Job(
                kind=JobKind.IMPORT,
                status=JobStatus.QUEUED,
                attempts=attempts,
                actor_type="user",
                created_by="usr_test",
            )
        )
    claimed = await worker._claim_next()
    assert claimed is not None
    return claimed


@pytest.mark.asyncio
async def test_dead_letter_emits_job_failed_permanently(jobs_db: DatabaseSession) -> None:
    """When a job is dead-lettered, JOB_FAILED_PERMANENTLY event type is emitted."""
    registry = JobHandlerRegistry()
    registry.register(JobKind.IMPORT, _FailingHandler())
    worker = WorkerLoop(
        db=jobs_db,
        handler_registry=registry,
        worker_config=WorkerConfig(max_attempts=5),
    )
    job = await _claimed_import_job(jobs_db, worker, attempts=4)

    with patch("jentic_one.shared.jobs.worker.emit_event", new_callable=AsyncMock) as mock_emit:
        finalised = await worker._terminal_job(
            job,
            JobStatus.DEAD_LETTER,
            "max retries exceeded",
            event_summary_prefix="Job dead-lettered",
        )

    assert finalised is True
    mock_emit.assert_called_once()
    call_kwargs = mock_emit.call_args.kwargs
    assert call_kwargs["type"] == EventType.JOB_FAILED_PERMANENTLY
    assert call_kwargs["requires_action"] is True
    assert call_kwargs["severity"].value == "error"
    assert call_kwargs["actor_id"] == "usr_test"
    # Cross-check against the documented severity matrix (issue #907).
    assert call_kwargs["severity"] in EVENT_TYPE_SEVERITIES[EventType.JOB_FAILED_PERMANENTLY]


@pytest.mark.asyncio
async def test_failed_status_emits_import_failed(jobs_db: DatabaseSession) -> None:
    """When a job fails (non-retryable), IMPORT_FAILED event type is emitted."""
    registry = JobHandlerRegistry()
    registry.register(JobKind.IMPORT, _FailingHandler())
    worker = WorkerLoop(db=jobs_db, handler_registry=registry)
    job = await _claimed_import_job(jobs_db, worker, attempts=0)

    with patch("jentic_one.shared.jobs.worker.emit_event", new_callable=AsyncMock) as mock_emit:
        await worker._terminal_job(
            job, JobStatus.FAILED, "no handler", event_summary_prefix="Import failed"
        )

    mock_emit.assert_called_once()
    call_kwargs = mock_emit.call_args.kwargs
    assert call_kwargs["type"] == EventType.IMPORT_FAILED
    # Cross-check against the documented severity matrix (issue #907).
    assert call_kwargs["severity"] in EVENT_TYPE_SEVERITIES[EventType.IMPORT_FAILED]
