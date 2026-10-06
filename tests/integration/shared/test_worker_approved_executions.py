"""Integration tests: an approved held execution runs at most once.

The worker gives an approved job one attempt (no requeue on failure), refuses
to re-run one re-claimed after a mid-run crash, records a failed run as a
``failed`` job, keeps the result for the retention window, and links the
approval to the execution record the run wrote.
"""

from __future__ import annotations

from collections.abc import AsyncGenerator
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from sqlalchemy import delete, select

from jentic_one.admin.core.schema.execution_approvals import ExecutionApproval
from jentic_one.admin.core.schema.job_results import JobResult
from jentic_one.admin.core.schema.jobs import Job
from jentic_one.shared.config import WorkerConfig
from jentic_one.shared.db.session import DatabaseSession
from jentic_one.shared.jobs.handlers import JobHandlerRegistry, JobResultPayload
from jentic_one.shared.jobs.worker import WorkerLoop
from jentic_one.shared.models import JobKind, JobStatus

pytestmark = pytest.mark.integration

_RETENTION_S = 7200


class _RecordingHandler:
    """Returns a fixed execution outcome and counts its runs."""

    def __init__(self, status: str = "completed", *, boom: bool = False) -> None:
        self.status = status
        self.boom = boom
        self.calls = 0

    async def execute(
        self,
        job_id: str,
        session: Any,
        *,
        payload: dict[str, Any] | None = None,
        created_by: str | None = None,
        actor_type: str | None = None,
    ) -> JobResultPayload:
        self.calls += 1
        if self.boom:
            raise RuntimeError("upstream exploded")
        return JobResultPayload(
            body={"execution_id": f"exec_{job_id[-20:]}", "status": self.status, "http_status": 500}
        )


def _worker(admin_db: DatabaseSession, handler: Any) -> WorkerLoop:
    reg = JobHandlerRegistry()
    reg.register(JobKind.EXECUTION, handler)
    return WorkerLoop(
        admin_db,
        reg,
        worker_config=WorkerConfig(max_attempts=5),
        approved_result_retention_seconds=_RETENTION_S,
    )


@pytest.fixture()
async def clean_jobs(admin_db: DatabaseSession) -> AsyncGenerator[None, None]:
    async with admin_db.transaction() as session:
        await session.execute(delete(Job))
    yield
    async with admin_db.transaction() as session:
        await session.execute(delete(Job))


async def _job(admin_db: DatabaseSession, *, approved: bool, **fields: Any) -> str:
    fields.setdefault("status", JobStatus.QUEUED)
    async with admin_db.transaction() as session:
        job = Job(kind=JobKind.EXECUTION, created_by="agnt_worker", actor_type="agent", **fields)
        session.add(job)
        await session.flush()
        if approved:
            session.add(
                ExecutionApproval(
                    job_id=job.id,
                    agent_id="agnt_worker",
                    credential_id="cred_worker",
                    api_vendor="api.example.com",
                    api_name="main",
                    api_version="1",
                    method="POST",
                    path="/v1/x",
                    request_fingerprint=job.id.ljust(64, "0"),
                    state="approved",
                    expires_at=datetime.now(UTC) + timedelta(hours=1),
                )
            )
        return str(job.id)


async def _load(admin_db: DatabaseSession, job_id: str) -> tuple[Job, JobResult | None]:
    async with admin_db.session() as session:
        job = (await session.execute(select(Job).where(Job.id == job_id))).scalar_one()
        result = (
            await session.execute(select(JobResult).where(JobResult.job_id == job_id))
        ).scalar_one_or_none()
        return job, result


async def test_approved_job_failure_is_terminal_without_retry(
    admin_db: DatabaseSession, clean_jobs: None
) -> None:
    handler = _RecordingHandler(boom=True)
    job_id = await _job(admin_db, approved=True)
    worker = _worker(admin_db, handler)

    assert await worker._tick() is True
    assert await worker._tick() is False

    job, _ = await _load(admin_db, job_id)
    assert job.status == JobStatus.FAILED
    assert job.attempts == 1
    assert handler.calls == 1


async def test_unapproved_job_failure_still_requeues(
    admin_db: DatabaseSession, clean_jobs: None
) -> None:
    job_id = await _job(admin_db, approved=False)
    await _worker(admin_db, _RecordingHandler(boom=True))._tick()
    job, _ = await _load(admin_db, job_id)
    assert job.status == JobStatus.QUEUED


async def test_reclaimed_approved_job_is_failed_not_rerun(
    admin_db: DatabaseSession, clean_jobs: None
) -> None:
    """A RUNNING approved job past its visibility deadline (dead worker) fails as
    "resume failed" without dispatching the handler again."""
    handler = _RecordingHandler()
    past = datetime.now(UTC) - timedelta(seconds=1)
    job_id = await _job(
        admin_db, approved=True, status=JobStatus.RUNNING, visible_at=past, attempts=1
    )

    assert await _worker(admin_db, handler)._tick() is True

    job, result = await _load(admin_db, job_id)
    assert handler.calls == 0
    assert job.status == JobStatus.FAILED
    assert result is not None
    assert result.body["type"] == "approval_resume_failed"
    assert result.available_until is not None


async def test_reclaimed_unapproved_job_reruns(admin_db: DatabaseSession, clean_jobs: None) -> None:
    handler = _RecordingHandler()
    past = datetime.now(UTC) - timedelta(seconds=1)
    job_id = await _job(
        admin_db, approved=False, status=JobStatus.RUNNING, visible_at=past, attempts=1
    )
    await _worker(admin_db, handler)._tick()
    job, _ = await _load(admin_db, job_id)
    assert handler.calls == 1
    assert job.status == JobStatus.COMPLETED


async def test_approved_result_is_retained_and_linked(
    admin_db: DatabaseSession, clean_jobs: None
) -> None:
    job_id = await _job(admin_db, approved=True)
    before = datetime.now(UTC)
    await _worker(admin_db, _RecordingHandler())._tick()

    job, result = await _load(admin_db, job_id)
    assert job.status == JobStatus.COMPLETED
    assert result is not None
    assert result.available_until is not None
    window = result.available_until - before
    assert timedelta(seconds=_RETENTION_S - 60) < window < timedelta(seconds=_RETENTION_S + 60)
    async with admin_db.session() as session:
        approval = (
            await session.execute(
                select(ExecutionApproval).where(ExecutionApproval.job_id == job_id)
            )
        ).scalar_one()
    assert approval.execution_id == f"exec_{job_id[-20:]}"


async def test_failed_approved_run_is_a_failed_job_with_its_outcome(
    admin_db: DatabaseSession, clean_jobs: None
) -> None:
    job_id = await _job(admin_db, approved=True)
    await _worker(admin_db, _RecordingHandler(status="failed"))._tick()
    job, result = await _load(admin_db, job_id)
    assert job.status == JobStatus.FAILED
    assert result is not None
    assert result.body["status"] == "failed"


async def test_unapproved_result_keeps_default_retention(
    admin_db: DatabaseSession, clean_jobs: None
) -> None:
    job_id = await _job(admin_db, approved=False)
    await _worker(admin_db, _RecordingHandler())._tick()
    _, result = await _load(admin_db, job_id)
    assert result is not None
    assert result.available_until is None


async def test_held_job_is_never_claimed(admin_db: DatabaseSession, clean_jobs: None) -> None:
    handler = _RecordingHandler()
    job_id = await _job(admin_db, approved=False, status=JobStatus.HELD)
    assert await _worker(admin_db, handler)._tick() is False
    job, _ = await _load(admin_db, job_id)
    assert job.status == JobStatus.HELD
    assert handler.calls == 0
