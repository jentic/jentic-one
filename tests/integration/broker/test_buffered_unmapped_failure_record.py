"""Integration test: a buffered call's unmapped failure writes one FAILED record.

Pins the buffered half of the one-vocabulary record contract: an exception the
broker does not map (the caller gets a 500) records ``server_error:
<ExceptionClass>`` with no upstream status and the elapsed ``duration_ms``,
plus one ``execution.failed`` event. The write runs on the app's
background-task registry against the real admin DB, so the exception reaches
the caller before the record lands.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncGenerator
from typing import Any
from unittest.mock import patch

import pytest
from sqlalchemy import delete, select
from starlette.applications import Starlette
from starlette.requests import Request

from jentic_one.admin.core.schema.events import Event
from jentic_one.admin.core.schema.execution_records import ExecutionRecord
from jentic_one.broker.adapters.runners.base import RunnerRequest, RunnerResult, UpstreamRunner
from jentic_one.broker.core.schemas import ExecuteRequestContext
from jentic_one.broker.services.execution.service import default_broker, run_execution
from jentic_one.broker.web.routers import execute as execute_router
from jentic_one.shared.auth.identity import Identity
from jentic_one.shared.context import Context
from jentic_one.shared.models import ActorType, ExecutionStatus
from jentic_one.shared.models.events import EventType
from jentic_one.shared.schemas import OperationInfo
from jentic_one.shared.web.background_tasks import install_background_tasks

pytestmark = pytest.mark.integration

_EXECUTION_ID = "exec_bufferedunmappedinteg01"
_TRACE_ID = "5bf92f3577b34da6a3ce929d0e0e4736"
_DELAY_S = 0.03
_IDENTITY = Identity(
    sub="agnt_buffered_unmapped_test",
    actor_type=ActorType.AGENT,
    permissions=["execute"],
    active=True,
)


class _UnmappedError(RuntimeError):
    """An exception no broker handler maps."""


class _UnmappedRunner(UpstreamRunner):
    async def run(self, request: RunnerRequest) -> RunnerResult:
        await asyncio.sleep(_DELAY_S)
        raise _UnmappedError("unmapped")


@pytest.fixture()
async def cleanup(integration_context: Context) -> AsyncGenerator[None, None]:
    yield
    async with integration_context.admin_db.transaction() as session:
        await session.execute(delete(Event).where(Event.execution_id == _EXECUTION_ID))
        await session.execute(delete(ExecutionRecord).where(ExecutionRecord.id == _EXECUTION_ID))


async def test_unmapped_failure_lands_one_failed_record_after_the_raise(
    integration_context: Context, cleanup: None
) -> None:
    app = Starlette()
    registry = install_background_tasks(app)
    request = Request({"type": "http", "app": app})
    ctx_req = ExecuteRequestContext(
        upstream_url="https://api.example.com/v1/things",
        method="GET",
        trace_id=_TRACE_ID,
        operation=OperationInfo(id="getThing", path="/v1/things", method="GET"),
    )
    release = asyncio.Event()
    real_persist = execute_router._persist_unmapped_failure

    async def _gated_persist(*args: Any, **kwargs: Any) -> None:
        await release.wait()
        await real_persist(*args, **kwargs)

    with patch.object(execute_router, "_persist_unmapped_failure", _gated_persist):
        with pytest.raises(_UnmappedError):
            await run_execution(
                ctx_req,
                body=None,
                headers=None,
                admin_db=integration_context.admin_db,
                broker=default_broker(_UnmappedRunner()),
                execution_id=_EXECUTION_ID,
                actor_id=_IDENTITY.sub,
                actor_type=_IDENTITY.actor_type.value,
                on_unmapped_failure=execute_router._unmapped_failure_recorder(
                    request, integration_context, ctx_req, _IDENTITY
                ),
            )
        # The exception reached the caller while the write is still pending.
        assert registry.pending == 1
        release.set()
        await registry.drain(5)

    async with integration_context.admin_db.session() as session:
        records = list(
            (
                await session.execute(
                    select(ExecutionRecord).where(ExecutionRecord.id == _EXECUTION_ID)
                )
            ).scalars()
        )
        events = list(
            (await session.execute(select(Event).where(Event.execution_id == _EXECUTION_ID)))
            .scalars()
            .all()
        )
    (record,) = records
    assert record.status == ExecutionStatus.FAILED
    assert record.http_status is None
    assert record.error == "server_error: _UnmappedError"
    assert record.duration_ms is not None
    assert record.duration_ms >= int(_DELAY_S * 1000) // 2
    assert record.trace_id == _TRACE_ID
    assert [e.type for e in events] == [EventType.EXECUTION_FAILED]
