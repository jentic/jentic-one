"""Unit tests for JOB_FAILED_PERMANENTLY event emission on dead-letter."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, patch

import pytest

from jentic_one.shared.config import WorkerConfig
from jentic_one.shared.jobs.handlers import JobHandlerRegistry, JobResultPayload
from jentic_one.shared.jobs.worker import WorkerLoop
from jentic_one.shared.models.events import EVENT_TYPE_SEVERITIES, EventType
from jentic_one.shared.models.jobs import JobKind, JobStatus


class _FailingHandler:
    async def execute(
        self,
        job_id: str,
        session: Any,
        *,
        payload: dict[str, Any] | None = None,
        created_by: str | None = None,
        actor_type: str | None = None,
    ) -> JobResultPayload:
        raise RuntimeError("permanent failure")


class _FakeJob:
    def __init__(self, *, job_id: str = "job_001", attempts: int = 5) -> None:
        self.id = job_id
        self.kind = JobKind.IMPORT.value
        self.attempts = attempts
        self.created_by = "usr_test"
        self.actor_type = "user"
        self.payload: dict[str, Any] = {}
        self.status = JobStatus.RUNNING
        self.error: str | None = None
        self.visible_at = None


class _FakeSession:
    def __init__(self, job: _FakeJob) -> None:
        self._job = job

    async def get(self, model: Any, job_id: str) -> _FakeJob | None:
        return self._job


class _FakeTransaction:
    def __init__(self, session: _FakeSession) -> None:
        self._session = session

    async def __aenter__(self) -> _FakeSession:
        return self._session

    async def __aexit__(self, *args: Any) -> None:
        pass


class _FakeDb:
    def __init__(self, job: _FakeJob) -> None:
        self._session = _FakeSession(job)

    def transaction(self) -> _FakeTransaction:
        return _FakeTransaction(self._session)


@pytest.mark.asyncio
async def test_dead_letter_emits_job_failed_permanently() -> None:
    """When a job is dead-lettered, JOB_FAILED_PERMANENTLY event type is emitted."""
    job = _FakeJob(attempts=5)
    db = _FakeDb(job)
    config = WorkerConfig(max_attempts=5)

    registry = JobHandlerRegistry()
    registry.register(JobKind.IMPORT, _FailingHandler())

    worker = WorkerLoop(
        db=db,  # type: ignore[arg-type]
        handler_registry=registry,
        worker_config=config,
    )

    with patch("jentic_one.shared.jobs.worker.emit_event", new_callable=AsyncMock) as mock_emit:
        await worker._terminal_job(
            job.id,
            JobStatus.DEAD_LETTER,
            "max retries exceeded",
            event_summary_prefix="Job dead-lettered",
        )

    mock_emit.assert_called_once()
    call_kwargs = mock_emit.call_args.kwargs
    assert call_kwargs["type"] == EventType.JOB_FAILED_PERMANENTLY
    assert call_kwargs["requires_action"] is True
    assert call_kwargs["severity"].value == "error"
    # Cross-check against the documented severity matrix (issue #907).
    assert call_kwargs["severity"] in EVENT_TYPE_SEVERITIES[EventType.JOB_FAILED_PERMANENTLY]


@pytest.mark.asyncio
async def test_failed_status_emits_import_failed() -> None:
    """When a job fails (non-retryable), IMPORT_FAILED event type is emitted."""
    job = _FakeJob(attempts=1)
    db = _FakeDb(job)

    registry = JobHandlerRegistry()
    worker = WorkerLoop(
        db=db,  # type: ignore[arg-type]
        handler_registry=registry,
    )

    with patch("jentic_one.shared.jobs.worker.emit_event", new_callable=AsyncMock) as mock_emit:
        await worker._terminal_job(
            job.id, JobStatus.FAILED, "no handler", event_summary_prefix="Import failed"
        )

    mock_emit.assert_called_once()
    call_kwargs = mock_emit.call_args.kwargs
    assert call_kwargs["type"] == EventType.IMPORT_FAILED
    # Cross-check against the documented severity matrix (issue #907).
    assert call_kwargs["severity"] in EVENT_TYPE_SEVERITIES[EventType.IMPORT_FAILED]
