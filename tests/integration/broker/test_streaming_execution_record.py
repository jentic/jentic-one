"""Integration test: streaming execution produces a queryable admin.execution_records row.

Exercises the persistence callback end-to-end against a real database — after
a streaming execution the record is available in admin.execution_records with
correct execution_id, status, http_status, and duration_ms > 0.
"""

from __future__ import annotations

from collections.abc import AsyncGenerator
from datetime import UTC, datetime

import pytest
from sqlalchemy import delete, select

from jentic_one.admin.core.schema.execution_records import ExecutionRecord
from jentic_one.broker.core.schemas import ExecuteRequestContext
from jentic_one.broker.services.execution.service import persist_streaming_execution
from jentic_one.shared.db.session import DatabaseSession
from jentic_one.shared.models import ExecutionStatus
from jentic_one.shared.schemas import OperationInfo

pytestmark = pytest.mark.integration


@pytest.fixture(autouse=True)
async def clean_records(admin_db: DatabaseSession) -> AsyncGenerator[None, None]:
    """Remove this module's fixed-id records, before and after.

    The tests insert deterministic ids, so a leftover row from a previous
    session against the persistent Docker fixture would fail the insert with
    a duplicate key.
    """

    async def _cleanup() -> None:
        async with admin_db.session() as session:
            await session.execute(
                delete(ExecutionRecord).where(ExecutionRecord.id.like("integ-stream-exec-%"))
            )
            await session.commit()

    await _cleanup()
    yield
    await _cleanup()


@pytest.fixture()
def ctx_req() -> ExecuteRequestContext:
    return ExecuteRequestContext(
        upstream_url="https://api.example.com/data",
        method="GET",
        trace_id="trace-integ-001",
        toolkit_id="tk-integ-1",
        operation=OperationInfo(id="getData", path="/data", method="GET"),
        api_vendor="example",
        api_name="data-api",
        api_version="v1",
    )


@pytest.mark.asyncio
async def test_streaming_execution_persists_completed_record(
    admin_db: DatabaseSession,
    ctx_req: ExecuteRequestContext,
) -> None:
    """A successful streaming execution produces a COMPLETED row."""
    execution_id = "integ-stream-exec-001"
    started_at = datetime.now(UTC)

    async with admin_db.transaction() as session:
        await persist_streaming_execution(
            session,
            execution_id=execution_id,
            started_at=started_at,
            status=ExecutionStatus.COMPLETED,
            http_status=200,
            duration_ms=42,
            error=None,
            ctx_req=ctx_req,
            actor_id="agent-integ-1",
            actor_type="agent",
        )

    async with admin_db.session() as session:
        stmt = select(ExecutionRecord).where(ExecutionRecord.id == execution_id)
        result = await session.execute(stmt)
        record = result.scalar_one()

    assert record.id == execution_id
    assert record.status == ExecutionStatus.COMPLETED
    assert record.http_status == 200
    assert record.duration_ms == 42
    assert record.toolkit_id == "tk-integ-1"
    assert record.operation_id == "getData"
    assert record.operation_path == "/data"
    assert record.operation_method == "GET"
    assert record.actor_id == "agent-integ-1"
    assert record.actor_type == "agent"
    assert record.error is None


@pytest.mark.asyncio
async def test_streaming_execution_persists_failed_record(
    admin_db: DatabaseSession,
    ctx_req: ExecuteRequestContext,
) -> None:
    """A failed streaming execution (upstream error) produces a FAILED row."""
    execution_id = "integ-stream-exec-002"
    started_at = datetime.now(UTC)

    async with admin_db.transaction() as session:
        await persist_streaming_execution(
            session,
            execution_id=execution_id,
            started_at=started_at,
            status=ExecutionStatus.FAILED,
            http_status=502,
            duration_ms=15,
            error="upstream_error: ConnectionError",
            ctx_req=ctx_req,
            actor_id="agent-integ-1",
            actor_type="agent",
        )

    async with admin_db.session() as session:
        stmt = select(ExecutionRecord).where(ExecutionRecord.id == execution_id)
        result = await session.execute(stmt)
        record = result.scalar_one()

    assert record.id == execution_id
    assert record.status == ExecutionStatus.FAILED
    assert record.http_status == 502
    assert record.duration_ms == 15
    assert record.error == "upstream_error: ConnectionError"


@pytest.mark.asyncio
async def test_streaming_execution_without_operation_persists_null_trio(
    admin_db: DatabaseSession,
    ctx_req: ExecuteRequestContext,
) -> None:
    """No resolved operation → all three operation_* columns are NULL."""
    execution_id = "integ-stream-exec-003"
    ctx_req = ctx_req.model_copy(update={"operation": None})

    async with admin_db.transaction() as session:
        await persist_streaming_execution(
            session,
            execution_id=execution_id,
            started_at=datetime.now(UTC),
            status=ExecutionStatus.COMPLETED,
            http_status=200,
            duration_ms=5,
            error=None,
            ctx_req=ctx_req,
            actor_id="agent-integ-1",
            actor_type="agent",
        )

    async with admin_db.session() as session:
        record = (
            await session.execute(select(ExecutionRecord).where(ExecutionRecord.id == execution_id))
        ).scalar_one()

    assert record.operation_id is None
    assert record.operation_path is None
    assert record.operation_method is None


@pytest.mark.asyncio
async def test_oversized_operation_path_is_truncated_not_fatal(
    admin_db: DatabaseSession,
    ctx_req: ExecuteRequestContext,
) -> None:
    """A path template longer than the 512-char column truncates at the write
    seam instead of failing the flush — the registry source column is unbounded
    Text, so without the guard a successful execution would lose its record."""
    execution_id = "integ-stream-exec-004"
    long_name = "/v1/" + "x" * 600
    ctx_req = ctx_req.model_copy(
        update={"operation": OperationInfo(id="getData", path=long_name, method="GET")}
    )

    async with admin_db.transaction() as session:
        await persist_streaming_execution(
            session,
            execution_id=execution_id,
            started_at=datetime.now(UTC),
            status=ExecutionStatus.COMPLETED,
            http_status=200,
            duration_ms=5,
            error=None,
            ctx_req=ctx_req,
            actor_id="agent-integ-1",
            actor_type="agent",
        )

    async with admin_db.session() as session:
        record = (
            await session.execute(select(ExecutionRecord).where(ExecutionRecord.id == execution_id))
        ).scalar_one()

    assert record.operation_path == long_name[:512]
    assert record.operation_method == "GET"
