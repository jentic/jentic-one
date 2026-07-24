"""Integration tests for the unified async execution path (§11 RN-0.3).

Proves the worker → ``ExecutionHandler`` → injected ``UpstreamExecutor`` path
runs end to end against the real admin DB: the job is claimed, the handler
delegates to the executor (the broker pipeline seam in production), credentials
are applied to the outbound request, and a ``JobResult`` is persisted. Per the
no-DB-mocking rule the DB is real; the executor is the legitimate injected seam
(the broker swaps in ``PipelineExecutor`` over the shared composed runner).
"""

from __future__ import annotations

import base64
from collections.abc import AsyncGenerator
from typing import Any

import pytest
from sqlalchemy import delete, select

from jentic_one.admin.core.schema.job_results import JobResult
from jentic_one.admin.core.schema.jobs import Job
from jentic_one.shared.config import WorkerConfig
from jentic_one.shared.db.session import DatabaseSession
from jentic_one.shared.jobs.execution_handler import ExecutionHandler
from jentic_one.shared.jobs.handlers import JobHandlerRegistry
from jentic_one.shared.jobs.protocols import (
    InjectedAuth,
    UpstreamExecRequest,
    UpstreamExecResult,
)
from jentic_one.shared.jobs.worker import WorkerLoop
from jentic_one.shared.models import JobKind, JobStatus

pytestmark = pytest.mark.integration


class _RecordingExecutor:
    """Stands in for the broker PipelineExecutor; records the dispatched request."""

    def __init__(self, result: UpstreamExecResult) -> None:
        self._result = result
        self.last_request: UpstreamExecRequest | None = None

    async def execute(self, request: UpstreamExecRequest, *, session: Any) -> UpstreamExecResult:
        self.last_request = request
        return self._result


class _StaticInjector:
    def __init__(self, injection: InjectedAuth) -> None:
        self._injection = injection

    async def inject(
        self,
        *,
        api_vendor: str,
        api_name: str,
        api_version: str,
        identity: Any,
        credential_name: str | None = None,
        trace_id: str | None = None,
    ) -> InjectedAuth:
        return self._injection


def _registry(handler: Any) -> JobHandlerRegistry:
    reg = JobHandlerRegistry()
    reg.register(JobKind.EXECUTION, handler)
    return reg


@pytest.fixture()
async def clean_jobs(admin_db: DatabaseSession) -> AsyncGenerator[None, None]:
    async with admin_db.session() as session:
        await session.execute(delete(JobResult))
        await session.execute(delete(Job))
        await session.commit()
    yield
    async with admin_db.session() as session:
        await session.execute(delete(JobResult))
        await session.execute(delete(Job))
        await session.commit()


async def _insert_execution_job(admin_db: DatabaseSession, payload: dict[str, Any]) -> str:
    job = Job(
        kind=JobKind.EXECUTION,
        status=JobStatus.QUEUED,
        created_by="actor-1",
        payload=payload,
    )
    async with admin_db.session() as session:
        session.add(job)
        await session.commit()
        return job.id


def _payload() -> dict[str, Any]:
    return {
        "execution_id": "exec_int_1",
        "upstream_url": "https://api.example.com/v1/widgets",
        "method": "GET",
        "trace_id": "unknown",
        "api_vendor": "example",
        "api_name": "api",
        "api_version": "1.0.0",
    }


async def test_async_job_dispatches_through_executor(
    admin_db: DatabaseSession, clean_jobs: None
) -> None:
    """The worker runs an execution job through the injected executor and persists a result."""
    executor = _RecordingExecutor(
        UpstreamExecResult(status_code=200, body=b"hello", content_type="text/plain", duration_ms=7)
    )
    injector = _StaticInjector(
        InjectedAuth(
            headers={"Authorization": "Bearer tok"},
            query_params={"api_key": "qsecret"},  # pragma: allowlist secret
            cookies={"sid": "csecret"},
        )
    )
    handler = ExecutionHandler(
        executor=executor,
        credential_injector=injector,  # pragma: allowlist secret
    )
    job_id = await _insert_execution_job(admin_db, _payload())

    worker = WorkerLoop(admin_db, _registry(handler), worker_config=WorkerConfig())
    processed = await worker._tick()

    assert processed is True

    # Credentials were applied to the outbound request (headers + query + cookie).
    req = executor.last_request
    assert req is not None
    assert req.headers["Authorization"] == "Bearer tok"
    assert "api_key=qsecret" in req.url
    assert req.headers["Cookie"] == "sid=csecret"
    # The execution id from the 202 is threaded through for the executions row.
    assert req.metadata["execution_id"] == "exec_int_1"

    # The job completed and the upstream body was persisted as the job result.
    async with admin_db.session() as session:
        job = (await session.execute(select(Job).where(Job.id == job_id))).scalar_one()
        assert job.status == JobStatus.COMPLETED
        result = (
            await session.execute(select(JobResult).where(JobResult.job_id == job_id))
        ).scalar_one()
    assert result.body["http_status"] == 200
    assert base64.b64decode(result.body["body_b64"]) == b"hello"


async def test_async_job_records_failed_on_pipeline_error(
    admin_db: DatabaseSession, clean_jobs: None
) -> None:
    """A pipeline error completes the job with a failed result (pre-E4.1 behaviour)."""

    class _Raising:
        async def execute(
            self, request: UpstreamExecRequest, *, session: Any
        ) -> UpstreamExecResult:
            raise RuntimeError("upstream circuit open")

    handler = ExecutionHandler(executor=_Raising())
    job_id = await _insert_execution_job(admin_db, _payload())

    worker = WorkerLoop(admin_db, _registry(handler), worker_config=WorkerConfig())
    await worker._tick()

    async with admin_db.session() as session:
        job = (await session.execute(select(Job).where(Job.id == job_id))).scalar_one()
        result = (
            await session.execute(select(JobResult).where(JobResult.job_id == job_id))
        ).scalar_one()
    # The handler swallowed the error → the job is COMPLETED with a failed result
    # body (it did not raise into the worker's retry/DLQ path).
    assert job.status == JobStatus.COMPLETED
    assert result.body["status"] == "failed"
    assert result.body["http_status"] is None
