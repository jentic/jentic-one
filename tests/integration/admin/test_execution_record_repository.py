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
            operation_path="/v1/users",
            operation_method="GET",
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
        assert loaded.operation_path == "/v1/users"
        assert loaded.operation_method == "GET"
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
        by_trace = await ExecutionRecordRepository.list_all(
            session, trace_id="trace1__________________________"
        )
        assert len(by_trace) == 1

        by_status = await ExecutionRecordRepository.list_all(session, status=["failed"])
        assert len(by_status) == 1

        from_result = await ExecutionRecordRepository.list_all(
            session, from_=base_time + timedelta(minutes=30)
        )
        assert len(from_result) == 2


async def _record(
    admin_db: DatabaseSession,
    *,
    actor_id: str,
    credential_id: str | None,
    started_at: datetime,
    api_vendor: str | None = "slack.com",
    api_name: str | None = "default",
    api_version: str = "v1",
    status: str = "completed",
) -> None:
    async with admin_db.session() as session:
        await ExecutionRecordRepository.create(
            session,
            toolkit_id="tk_test000000000000000000",
            trace_id="abcdef1234567890abcdef12",
            started_at=started_at,
            status=status,
            api_vendor=api_vendor,
            api_name=api_name,
            api_version=api_version,
            created_by=actor_id,
            actor_id=actor_id,
            actor_type="agent",
            credential_id=credential_id,
        )
        await session.commit()


async def test_last_used_by_api_takes_latest_per_credential_and_api(
    admin_db: DatabaseSession, clean_execution_records: None
) -> None:
    base = datetime(2026, 3, 1, tzinfo=UTC)
    agent = "agnt_lastused_1"
    await _record(admin_db, actor_id=agent, credential_id="cred_a", started_at=base)
    await _record(
        admin_db,
        actor_id=agent,
        credential_id="cred_a",
        started_at=base + timedelta(days=2),
        status="failed",  # A failed call is still use.
    )
    await _record(
        admin_db,
        actor_id=agent,
        credential_id="cred_a",
        started_at=base + timedelta(days=1),
        api_name="admin",
    )
    await _record(admin_db, actor_id=agent, credential_id="cred_b", started_at=base)
    await _record(
        admin_db, actor_id=agent, credential_id="cred_a", started_at=base, api_version="v2"
    )
    # Not counted: another agent, a credential not asked about, no credential.
    await _record(
        admin_db, actor_id="agnt_other", credential_id="cred_a", started_at=base + timedelta(9)
    )
    await _record(
        admin_db, actor_id=agent, credential_id="cred_c", started_at=base + timedelta(days=9)
    )
    await _record(admin_db, actor_id=agent, credential_id=None, started_at=base + timedelta(9))

    async with admin_db.session() as session:
        rows = await ExecutionRecordRepository.last_used_by_api(
            session, actor_id=agent, credential_ids=["cred_a", "cred_b"]
        )

    got = {(r.credential_id, r.api_vendor, r.api_name, r.api_version): r.last_used_at for r in rows}
    assert got == {
        ("cred_a", "slack.com", "default", "v1"): base + timedelta(days=2),
        ("cred_a", "slack.com", "default", "v2"): base,
        ("cred_a", "slack.com", "admin", "v1"): base + timedelta(days=1),
        ("cred_b", "slack.com", "default", "v1"): base,
    }


async def test_last_used_by_api_empty_input(
    admin_db: DatabaseSession, clean_execution_records: None
) -> None:
    async with admin_db.session() as session:
        assert (
            await ExecutionRecordRepository.last_used_by_api(
                session, actor_id="agnt_x", credential_ids=[]
            )
            == []
        )
