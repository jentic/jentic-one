"""Integration tests for the HeldExecution ORM model against a real database."""

from __future__ import annotations

from collections.abc import AsyncGenerator
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from sqlalchemy import delete, select
from sqlalchemy.exc import IntegrityError

from jentic_one.admin.core.schema.held_executions import HeldExecution
from jentic_one.admin.core.schema.jobs import Job
from jentic_one.shared.db.session import DatabaseSession
from jentic_one.shared.models import JobKind, JobStatus

pytestmark = pytest.mark.integration


@pytest.fixture()
async def clean_held_executions(admin_db: DatabaseSession) -> AsyncGenerator[None, None]:
    """Empty ``jobs`` (approvals cascade with their job) before and after each test."""
    async with admin_db.transaction() as session:
        await session.execute(delete(HeldExecution))
        await session.execute(delete(Job))
    yield
    async with admin_db.transaction() as session:
        await session.execute(delete(HeldExecution))
        await session.execute(delete(Job))


async def _held_job(admin_db: DatabaseSession) -> str:
    async with admin_db.transaction() as session:
        job = Job(kind=JobKind.EXECUTION, status=JobStatus.HELD, created_by="agnt_model")
        session.add(job)
        await session.flush()
        return str(job.id)


def _approval(job_id: str, **overrides: Any) -> HeldExecution:
    fields: dict[str, Any] = {
        "job_id": job_id,
        "agent_id": "agnt_model",
        "credential_id": "cred_model",
        "api_vendor": "api.example.com",
        "api_name": "main",
        "api_version": "1",
        "method": "POST",
        "path": "/v1/things",
        "request_fingerprint": "f" * 64,
        "expires_at": datetime.now(UTC) + timedelta(hours=1),
    }
    fields.update(overrides)
    return HeldExecution(**fields)


async def test_held_execution_round_trip(
    admin_db: DatabaseSession, clean_held_executions: None
) -> None:
    """A row inserts with a ksuid id and the pending default, and reads back intact."""
    job_id = await _held_job(admin_db)
    async with admin_db.transaction() as session:
        row = _approval(job_id, matched_rule_id="apr_1", trace_id="a" * 32)
        session.add(row)
        await session.flush()
        approval_id = row.id

    async with admin_db.session() as session:
        loaded = (
            await session.execute(select(HeldExecution).where(HeldExecution.id == approval_id))
        ).scalar_one()
    assert loaded.id.startswith("held_")
    assert loaded.state == "pending"
    assert loaded.job_id == job_id
    assert loaded.matched_rule_id == "apr_1"
    assert loaded.execution_id is None
    assert loaded.decided_at is None
    assert loaded.expires_at.tzinfo is not None
    assert loaded.created_at is not None


async def test_only_one_pending_row_per_fingerprint(
    admin_db: DatabaseSession, clean_held_executions: None
) -> None:
    """The partial unique index refuses a second pending row for one fingerprint
    but admits a new pending row once the first is settled."""
    first_job = await _held_job(admin_db)
    second_job = await _held_job(admin_db)
    async with admin_db.transaction() as session:
        session.add(_approval(first_job))

    with pytest.raises(IntegrityError):
        async with admin_db.session() as session:
            session.add(_approval(second_job))
            await session.flush()

    async with admin_db.transaction() as session:
        row = (
            await session.execute(select(HeldExecution).where(HeldExecution.job_id == first_job))
        ).scalar_one()
        row.state = "denied"
    async with admin_db.transaction() as session:
        session.add(_approval(second_job))
    async with admin_db.session() as session:
        states = sorted((await session.execute(select(HeldExecution.state))).scalars().all())
    assert states == ["denied", "pending"]


async def test_approval_is_deleted_with_its_job(
    admin_db: DatabaseSession, clean_held_executions: None
) -> None:
    """``job_id`` is a cascading foreign key to ``jobs.id``."""
    job_id = await _held_job(admin_db)
    async with admin_db.transaction() as session:
        session.add(_approval(job_id))
    async with admin_db.transaction() as session:
        await session.execute(delete(Job).where(Job.id == job_id))
    async with admin_db.session() as session:
        remaining = (await session.execute(select(HeldExecution))).scalars().all()
    assert remaining == []


async def test_job_id_must_reference_a_job(
    admin_db: DatabaseSession, clean_held_executions: None
) -> None:
    with pytest.raises(IntegrityError):
        async with admin_db.session() as session:
            session.add(_approval("job_missing"))
            await session.flush()
