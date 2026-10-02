"""Integration tests for JobService against real PostgreSQL."""

from __future__ import annotations

import pytest
from sqlalchemy import delete

from jentic_one.admin.core.schema.jobs import Job
from jentic_one.admin.repos import JobRepository
from jentic_one.admin.services.errors import JobNotFoundError
from jentic_one.admin.services.job_service import JobService
from jentic_one.admin.services.schemas.jobs import JobFilter
from jentic_one.shared.auth.identity import Identity
from jentic_one.shared.context import Context
from jentic_one.shared.models import JobKind, JobStatus

pytestmark = pytest.mark.integration

_OWNER = Identity(sub="usr_test", email="test@local")


@pytest.fixture()
async def clean_jobs(integration_context: Context) -> None:
    async with integration_context.admin_db.session() as session:
        await session.execute(delete(Job))
        await session.commit()


async def test_list_returns_page(integration_context: Context, clean_jobs: None) -> None:
    ctx = integration_context
    async with ctx.admin_db.session() as session:
        for _ in range(3):
            await JobRepository.create(session, kind=JobKind.IMPORT, created_by="usr_test")
        await session.commit()

    service = JobService(ctx)
    page = await service.list_all(JobFilter(), identity=_OWNER, limit=50)
    assert len(page.data) == 3
    assert page.has_more is False


async def test_list_pagination(integration_context: Context, clean_jobs: None) -> None:
    ctx = integration_context
    for _ in range(5):
        async with ctx.admin_db.session() as session:
            await JobRepository.create(session, kind=JobKind.IMPORT, created_by="usr_test")
            await session.commit()

    service = JobService(ctx)
    page1 = await service.list_all(JobFilter(), identity=_OWNER, limit=2)
    assert len(page1.data) == 2
    assert page1.has_more is True

    page2 = await service.list_all(JobFilter(), identity=_OWNER, cursor=page1.next_cursor, limit=2)
    assert len(page2.data) == 2


async def test_list_with_status_filter(integration_context: Context, clean_jobs: None) -> None:
    ctx = integration_context
    async with ctx.admin_db.session() as session:
        await JobRepository.create(
            session, kind=JobKind.IMPORT, status=JobStatus.QUEUED, created_by="usr_test"
        )
        await JobRepository.create(
            session, kind=JobKind.IMPORT, status=JobStatus.COMPLETED, created_by="usr_test"
        )
        await session.commit()

    service = JobService(ctx)
    page = await service.list_all(JobFilter(status=["queued"]), identity=_OWNER, limit=50)
    assert len(page.data) == 1
    assert page.data[0].status == "queued"


async def test_get_by_id(integration_context: Context, clean_jobs: None) -> None:
    ctx = integration_context
    async with ctx.admin_db.session() as session:
        job = await JobRepository.create(session, kind=JobKind.EXECUTION, created_by="usr_test")
        await session.commit()
    job_id = job.id

    service = JobService(ctx)
    view = await service.get_by_id(job_id, identity=_OWNER)
    assert view.id == job_id
    assert view.kind == "execution"
    assert view.status == "queued"


async def test_get_by_id_not_found(integration_context: Context, clean_jobs: None) -> None:
    service = JobService(integration_context)
    with pytest.raises(JobNotFoundError):
        await service.get_by_id("job_nonexistent0000000000", identity=_OWNER)


async def test_cancel_queued_job(integration_context: Context, clean_jobs: None) -> None:
    ctx = integration_context
    async with ctx.admin_db.session() as session:
        job = await JobRepository.create(
            session, kind=JobKind.IMPORT, status=JobStatus.QUEUED, created_by="usr_test"
        )
        await session.commit()
    job_id = job.id

    service = JobService(ctx)
    result = await service.cancel(job_id, identity=_OWNER)
    assert result.status == "cancelled"


async def test_cancel_completed_job_is_idempotent(
    integration_context: Context, clean_jobs: None
) -> None:
    ctx = integration_context
    async with ctx.admin_db.session() as session:
        job = await JobRepository.create(
            session, kind=JobKind.IMPORT, status=JobStatus.COMPLETED, created_by="usr_test"
        )
        await session.commit()
    job_id = job.id

    service = JobService(ctx)
    result = await service.cancel(job_id, identity=_OWNER)
    assert result.status == "completed"


async def test_cancel_not_found_raises(integration_context: Context, clean_jobs: None) -> None:
    service = JobService(integration_context)
    with pytest.raises(JobNotFoundError):
        await service.cancel("job_nonexistent0000000000", identity=_OWNER)
