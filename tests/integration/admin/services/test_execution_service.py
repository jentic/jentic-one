"""Integration tests for ExecutionService against real PostgreSQL."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import delete

from jentic_one.admin.core.schema.execution_records import ExecutionRecord
from jentic_one.admin.repos import ExecutionRecordRepository
from jentic_one.admin.services.errors import ExecutionNotFoundError
from jentic_one.admin.services.execution_service import ExecutionService
from jentic_one.admin.services.schemas.executions import ExecutionFilter
from jentic_one.shared.auth.identity import Identity
from jentic_one.shared.context import Context

pytestmark = pytest.mark.integration

# Unrestricted reader; per-caller scoping lives in test_execution_scoping.py.
_ADMIN = Identity(sub="usr_exec_admin", permissions=["org:admin"])


@pytest.fixture()
async def clean_executions(integration_context: Context) -> None:
    async with integration_context.admin_db.session() as session:
        await session.execute(delete(ExecutionRecord))
        await session.commit()


async def test_list_returns_page(integration_context: Context, clean_executions: None) -> None:
    ctx = integration_context
    async with ctx.admin_db.session() as session:
        for i in range(3):
            await ExecutionRecordRepository.create(
                session,
                toolkit_id="tk_test",
                trace_id=f"trace_{i:03d}",
                started_at=datetime.now(UTC),
                status="success",
                duration_ms=100 + i,
                created_by="usr_test",
                actor_id="usr_test",
                actor_type="user",
            )
        await session.commit()

    service = ExecutionService(ctx)
    page = await service.list_all(ExecutionFilter(), identity=_ADMIN, limit=50)
    assert len(page.data) == 3
    assert page.has_more is False


async def test_list_pagination(integration_context: Context, clean_executions: None) -> None:
    ctx = integration_context
    now = datetime.now(UTC)
    async with ctx.admin_db.session() as session:
        for i in range(5):
            await ExecutionRecordRepository.create(
                session,
                toolkit_id="tk_test",
                trace_id=f"trace_{i:03d}",
                started_at=now - timedelta(seconds=5 - i),
                status="success",
                created_by="usr_test",
                actor_id="usr_test",
                actor_type="user",
            )
        await session.commit()

    service = ExecutionService(ctx)
    page1 = await service.list_all(ExecutionFilter(), identity=_ADMIN, limit=2)
    assert len(page1.data) == 2
    assert page1.has_more is True

    page2 = await service.list_all(
        ExecutionFilter(), identity=_ADMIN, cursor=page1.next_cursor, limit=2
    )
    assert len(page2.data) == 2


async def test_list_with_toolkit_filter(
    integration_context: Context, clean_executions: None
) -> None:
    ctx = integration_context
    async with ctx.admin_db.session() as session:
        await ExecutionRecordRepository.create(
            session,
            toolkit_id="tk_alpha",
            trace_id="trace_a",
            started_at=datetime.now(UTC),
            status="success",
            created_by="usr_test",
            actor_id="usr_test",
            actor_type="user",
        )
        await ExecutionRecordRepository.create(
            session,
            toolkit_id="tk_beta",
            trace_id="trace_b",
            started_at=datetime.now(UTC),
            status="success",
            created_by="usr_test",
            actor_id="usr_test",
            actor_type="user",
        )
        await session.commit()

    service = ExecutionService(ctx)
    page = await service.list_all(ExecutionFilter(toolkit_id="tk_alpha"), identity=_ADMIN, limit=50)
    assert len(page.data) == 1
    assert page.data[0].toolkit_id == "tk_alpha"


async def test_get_by_id(integration_context: Context, clean_executions: None) -> None:
    ctx = integration_context
    async with ctx.admin_db.session() as session:
        record = await ExecutionRecordRepository.create(
            session,
            toolkit_id="tk_test",
            trace_id="trace_xyz",
            started_at=datetime.now(UTC),
            status="success",
            duration_ms=250,
            api_vendor="acme",
            api_name="widget",
            api_version="v2",
            http_status=200,
            created_by="usr_test",
            actor_id="usr_test",
            actor_type="user",
        )
        await session.commit()
    record_id = record.id

    service = ExecutionService(ctx)
    view = await service.get_by_id(record_id, identity=_ADMIN)
    assert view.id == record_id
    assert view.toolkit_id == "tk_test"
    assert view.api is not None
    assert view.api.vendor == "acme"
    assert view.api.name == "widget"
    assert view.duration_ms == 250


async def test_get_by_id_not_found(integration_context: Context, clean_executions: None) -> None:
    service = ExecutionService(integration_context)
    with pytest.raises(ExecutionNotFoundError):
        await service.get_by_id("exec_nonexistent0000000000", identity=_ADMIN)
