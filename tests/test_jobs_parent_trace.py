"""GET /jobs and GET /jobs/{id} expose `parent_trace_id` mirrored from the
owning trace.

Powers the Job drawer's "part of workflow X" line for child broker jobs (the
async wait=0 hop spawned inside an arazzo workflow). Without the correlated
subquery in the jobs router the UI would have to fetch the trace separately
just to render that line.
"""

import sqlite3
import uuid

import pytest
from src.db import DB_PATH
from src.routers.traces import write_trace


_FIXTURE_PARENT_TRACE = "exec_parent_trace_xyz"


@pytest.fixture
def cleanup_parent_trace_rows():
    """Strip seeded rows after each test."""
    yield
    with sqlite3.connect(DB_PATH) as cx:
        cx.execute("DELETE FROM jobs WHERE id LIKE 'job_pt_%'")
        cx.execute("DELETE FROM executions WHERE id LIKE 'exec_pt_%'")
        cx.execute("DELETE FROM executions WHERE id = ?", (_FIXTURE_PARENT_TRACE,))
        cx.commit()


@pytest.mark.asyncio
async def test_get_job_returns_parent_trace_id_when_owning_trace_is_a_child(
    admin_client,
    cleanup_parent_trace_rows,  # noqa: ARG001
):
    """Job whose trace has parent_trace_id set surfaces it on /jobs/{id}."""
    job_id = f"job_pt_{uuid.uuid4().hex[:8]}"
    child_trace_id = f"exec_pt_{uuid.uuid4().hex[:8]}"

    # The trace this job produced is itself a child broker hop of a parent
    # workflow trace.
    await write_trace(
        trace_id=child_trace_id,
        toolkit_id="default",
        operation_id="GET/api.example.com/anything",
        workflow_id=None,
        spec_path=None,
        status="success",
        http_status=200,
        duration_ms=42,
        error=None,
        job_id=job_id,
        parent_trace_id=_FIXTURE_PARENT_TRACE,
    )

    with sqlite3.connect(DB_PATH) as cx:
        cx.execute(
            """INSERT INTO jobs (id, kind, slug_or_id, toolkit_id, status,
                                 trace_id, created_at)
               VALUES (?, 'broker', 'GET /api.example.com/anything', 'default',
                       'complete', ?, strftime('%s','now'))""",
            (job_id, child_trace_id),
        )
        cx.commit()

    detail = admin_client.get(f"/jobs/{job_id}").json()
    assert detail["job_id"] == job_id
    assert detail["trace_id"] == child_trace_id
    assert detail["parent_trace_id"] == _FIXTURE_PARENT_TRACE


@pytest.mark.asyncio
async def test_get_job_omits_parent_trace_id_for_top_level_job(
    admin_client,
    cleanup_parent_trace_rows,  # noqa: ARG001
):
    """Job whose trace is top-level (no parent) reports None.

    Pydantic's `JobOut` defaults the field so it's always present in the
    serialised payload — what matters is that the value is null, not that
    the key is absent.
    """
    job_id = f"job_pt_{uuid.uuid4().hex[:8]}"
    top_trace_id = f"exec_pt_{uuid.uuid4().hex[:8]}"

    await write_trace(
        trace_id=top_trace_id,
        toolkit_id="default",
        operation_id="GET/api.example.com/zen",
        workflow_id=None,
        spec_path=None,
        status="success",
        http_status=200,
        duration_ms=15,
        error=None,
        job_id=job_id,
    )

    with sqlite3.connect(DB_PATH) as cx:
        cx.execute(
            """INSERT INTO jobs (id, kind, slug_or_id, toolkit_id, status,
                                 trace_id, created_at)
               VALUES (?, 'broker', 'GET /api.example.com/zen', 'default',
                       'complete', ?, strftime('%s','now'))""",
            (job_id, top_trace_id),
        )
        cx.commit()

    detail = admin_client.get(f"/jobs/{job_id}").json()
    assert detail["trace_id"] == top_trace_id
    assert detail.get("parent_trace_id") is None


@pytest.mark.asyncio
async def test_list_jobs_includes_parent_trace_id(
    admin_client,
    cleanup_parent_trace_rows,  # noqa: ARG001
):
    """The list endpoint surfaces parent_trace_id alongside per-row details."""
    job_id = f"job_pt_{uuid.uuid4().hex[:8]}"
    child_trace_id = f"exec_pt_{uuid.uuid4().hex[:8]}"

    await write_trace(
        trace_id=child_trace_id,
        toolkit_id="default",
        operation_id="GET/api.example.com/anything",
        workflow_id=None,
        spec_path=None,
        status="success",
        http_status=200,
        duration_ms=42,
        error=None,
        job_id=job_id,
        parent_trace_id=_FIXTURE_PARENT_TRACE,
    )

    with sqlite3.connect(DB_PATH) as cx:
        cx.execute(
            """INSERT INTO jobs (id, kind, slug_or_id, toolkit_id, status,
                                 trace_id, created_at)
               VALUES (?, 'broker', 'GET /api.example.com/anything', 'default',
                       'complete', ?, strftime('%s','now'))""",
            (job_id, child_trace_id),
        )
        cx.commit()

    resp = admin_client.get("/jobs?limit=100")
    assert resp.status_code == 200
    rows = {row["job_id"]: row for row in resp.json()["data"]}
    assert rows[job_id]["parent_trace_id"] == _FIXTURE_PARENT_TRACE
