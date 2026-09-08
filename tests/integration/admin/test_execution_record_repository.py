"""Integration tests for ExecutionRecordRepository against real PostgreSQL."""

from __future__ import annotations

from collections.abc import AsyncGenerator
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import delete

from jentic_one.admin.core.schema.execution_records import ExecutionRecord
from jentic_one.admin.repos import ExecutionRecordRepository
from jentic_one.shared.db.session import DatabaseSession

pytestmark = pytest.mark.integration


@pytest.fixture()
async def clean_execution_records(admin_db: DatabaseSession) -> AsyncGenerator[None, None]:
    async with admin_db.session() as session:
        await session.execute(delete(ExecutionRecord))
        await session.commit()
    yield
    async with admin_db.session() as session:
        await session.execute(delete(ExecutionRecord))
        await session.commit()


async def test_create_generates_ksuid(
    admin_db: DatabaseSession, clean_execution_records: None
) -> None:
    async with admin_db.session() as session:
        record = await ExecutionRecordRepository.create(
            session,
            toolkit_id="tk_test000000000000000000",
            trace_id="abcdef1234567890abcdef12",
            started_at=datetime.now(UTC),
            status="completed",
            created_by="usr_test",
            actor_id="usr_test",
            actor_type="user",
        )
        await session.commit()
        assert record.id.startswith("exec_")
        assert len(record.id) == 29


async def test_create_and_get_by_id(
    admin_db: DatabaseSession, clean_execution_records: None
) -> None:
    now = datetime.now(UTC)
    async with admin_db.session() as session:
        record = await ExecutionRecordRepository.create(
            session,
            toolkit_id="tk_test000000000000000000",
            trace_id="abcdef1234567890abcdef12",
            started_at=now,
            status="completed",
            duration_ms=1500,
            operation_id="listUsers",
            api_vendor="github",
            api_name="rest",
            api_version="v3",
            pinned_revisions={"rev": 1},
            http_status=200,
            created_by="usr_test",
            actor_id="usr_test",
            actor_type="user",
        )
        await session.commit()
        record_id = record.id

    async with admin_db.session() as session:
        loaded = await ExecutionRecordRepository.get_by_id(session, record_id)
        assert loaded is not None
        assert loaded.toolkit_id == "tk_test000000000000000000"
        assert loaded.status == "completed"
        assert loaded.duration_ms == 1500
        assert loaded.operation_id == "listUsers"
        assert loaded.api_vendor == "github"
        assert loaded.http_status == 200
        assert loaded.pinned_revisions == {"rev": 1}


async def test_create_persists_credential_attribution(
    admin_db: DatabaseSession, clean_execution_records: None
) -> None:
    """#740: credential id/name populate on the execution record."""
    now = datetime.now(UTC)
    async with admin_db.session() as session:
        record = await ExecutionRecordRepository.create(
            session,
            toolkit_id="tk_test000000000000000000",
            trace_id="abcdef1234567890abcdef12",
            started_at=now,
            status="completed",
            created_by="usr_test",
            actor_id="usr_test",
            actor_type="user",
            credential_id="cred_abc123",
            credential_name="stripe-live",
        )
        await session.commit()
        record_id = record.id

    async with admin_db.session() as session:
        loaded = await ExecutionRecordRepository.get_by_id(session, record_id)
        assert loaded is not None
        assert loaded.credential_id == "cred_abc123"
        assert loaded.credential_name == "stripe-live"


async def test_create_leaves_credential_attribution_null_when_absent(
    admin_db: DatabaseSession, clean_execution_records: None
) -> None:
    """No credential path attempted ⇒ both columns NULL (unambiguous)."""
    async with admin_db.session() as session:
        record = await ExecutionRecordRepository.create(
            session,
            toolkit_id="tk_test000000000000000000",
            trace_id="abcdef1234567890abcdef12",
            started_at=datetime.now(UTC),
            status="completed",
            created_by="usr_test",
            actor_id="usr_test",
            actor_type="user",
        )
        await session.commit()
        record_id = record.id

    async with admin_db.session() as session:
        loaded = await ExecutionRecordRepository.get_by_id(session, record_id)
        assert loaded is not None
        assert loaded.credential_id is None
        assert loaded.credential_name is None


async def test_list_all_with_filters(
    admin_db: DatabaseSession, clean_execution_records: None
) -> None:
    base_time = datetime.now(UTC) - timedelta(hours=2)
    async with admin_db.session() as session:
        await ExecutionRecordRepository.create(
            session,
            toolkit_id="tk_a000000000000000000000",
            trace_id="trace1__________________________",
            started_at=base_time,
            status="completed",
            created_by="usr_test",
            actor_id="usr_test",
            actor_type="user",
        )
        await ExecutionRecordRepository.create(
            session,
            toolkit_id="tk_b000000000000000000000",
            trace_id="trace2__________________________",
            started_at=base_time + timedelta(hours=1),
            status="failed",
            created_by="usr_test",
            actor_id="usr_test",
            actor_type="user",
        )
        await ExecutionRecordRepository.create(
            session,
            toolkit_id="tk_a000000000000000000000",
            trace_id="trace3__________________________",
            started_at=base_time + timedelta(hours=2),
            status="completed",
            created_by="usr_test",
            actor_id="usr_test",
            actor_type="user",
        )
        await session.commit()

    async with admin_db.session() as session:
        by_toolkit = await ExecutionRecordRepository.list_all(
            session, toolkit_id="tk_a000000000000000000000"
        )
        assert len(by_toolkit) == 2

        by_status = await ExecutionRecordRepository.list_all(session, status=["failed"])
        assert len(by_status) == 1

        from_result = await ExecutionRecordRepository.list_all(
            session, from_=base_time + timedelta(minutes=30)
        )
        assert len(from_result) == 2
