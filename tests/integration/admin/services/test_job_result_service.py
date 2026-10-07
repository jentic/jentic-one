"""Integration tests for JobResultService against real PostgreSQL."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import delete

from jentic_one.admin.core.schema.job_results import JobResult
from jentic_one.admin.core.schema.jobs import Job
from jentic_one.admin.repos import JobRepository, JobResultRepository
from jentic_one.admin.services.errors import (
    JobNotCompletedError,
    JobNotFoundError,
    JobResultExpiredError,
)
from jentic_one.admin.services.job_result_service import JobResultService
from jentic_one.shared.auth.identity import Identity
from jentic_one.shared.context import Context
from jentic_one.shared.models import JobKind, JobStatus

pytestmark = pytest.mark.integration

_OWNER = Identity(sub="usr_test", email="test@local")


@pytest.fixture()
async def clean_jobs(integration_context: Context) -> None:
    async with integration_context.admin_db.session() as session:
        await session.execute(delete(JobResult))
        await session.execute(delete(Job))
        await session.commit()


async def test_get_result(integration_context: Context, clean_jobs: None) -> None:
    ctx = integration_context
    async with ctx.admin_db.session() as session:
        job = await JobRepository.create(
            session, kind=JobKind.IMPORT, status=JobStatus.COMPLETED, created_by="usr_test"
        )
        await JobResultRepository.create(
            session,
            job_id=job.id,
            kind="import",
            body={"records_imported": 42},
            available_until=datetime.now(UTC) + timedelta(days=7),
            created_by="usr_test",
        )
        await session.commit()
    job_id = job.id

    service = JobResultService(ctx)
    view = await service.get(job_id, identity=_OWNER)
    assert view.kind == "import"
    assert view.body == {"records_imported": 42}
    assert view.job_id == job_id


async def test_get_job_not_found(integration_context: Context, clean_jobs: None) -> None:
    service = JobResultService(integration_context)
    with pytest.raises(JobNotFoundError):
        await service.get("job_nonexistent0000000000", identity=_OWNER)


async def test_get_job_not_completed(integration_context: Context, clean_jobs: None) -> None:
    ctx = integration_context
    async with ctx.admin_db.session() as session:
        job = await JobRepository.create(
            session, kind=JobKind.IMPORT, status=JobStatus.RUNNING, created_by="usr_test"
        )
        await session.commit()
    job_id = job.id

    service = JobResultService(ctx)
    with pytest.raises(JobNotCompletedError):
        await service.get(job_id, identity=_OWNER)


async def test_get_result_expired(integration_context: Context, clean_jobs: None) -> None:
    ctx = integration_context
    async with ctx.admin_db.session() as session:
        job = await JobRepository.create(
            session, kind=JobKind.IMPORT, status=JobStatus.COMPLETED, created_by="usr_test"
        )
        await JobResultRepository.create(
            session,
            job_id=job.id,
            kind="import",
            body={},
            available_until=datetime.now(UTC) - timedelta(days=1),
            created_by="usr_test",
        )
        await session.commit()
    job_id = job.id

    service = JobResultService(ctx)
    with pytest.raises(JobResultExpiredError):
        await service.get(job_id, identity=_OWNER)


async def test_get_result_missing(integration_context: Context, clean_jobs: None) -> None:
    ctx = integration_context
    async with ctx.admin_db.session() as session:
        job = await JobRepository.create(
            session, kind=JobKind.IMPORT, status=JobStatus.COMPLETED, created_by="usr_test"
        )
        await session.commit()
    job_id = job.id

    service = JobResultService(ctx)
    with pytest.raises(JobResultExpiredError):
        await service.get(job_id, identity=_OWNER)
